"""VOICEVOX ENGINE を使って日本語のナレーションを合成する。

Windows 標準の音声合成でも文章は読み上げられるが、声の作り方が古く、
数分以上聞き続けるナレーションには向かない。VOICEVOX はニューラル音声合成で、
イントネーションと声質が自然なため、eラーニング動画のナレーションに使える。

    * 手元で動く(通信も、外部サービスへの送信も、従量課金も無い)
    * 話す速さ・高さ・抑揚を数値で指定できる
    * 24000Hz 固定ではなく、出力の標本化周波数を指定できる

VOICEVOX ENGINE はローカルの HTTP サーバとして動く。既に起動していれば
それに接続し、起動していなければ `run.exe` を探して起動する(自分で起動した
場合だけ、終了時に止める)。

    GET  /version                               動いているかの確認
    GET  /speakers                              話者とスタイルの一覧
    POST /accent_phrases?text=...&speaker=ID    文字列を読み(アクセント句)にする
    POST /mora_data?speaker=ID                  アクセント句の長さと音程を決める
    POST /synthesis?speaker=ID                  下書きから WAV を作る

読みと音程を分けて作るのは、スライド 1 枚分を「続きとして」読み上げさせるため。
VOICEVOX は渡された文字列を 1 つの発話として扱い、その書き出しの音程を高く取る。
文や箇条書きの項目ごとに別々の合成を頼むと、項目が変わるたびに書き出しの音程が
現れ、そこだけ音程と抑揚が跳ね上がって聞こえる。

そこで区切りごとに `/accent_phrases` で読みを作り、それを 1 枚分つないでから
`/mora_data` で音程を決める。音程は前後のアクセント句を見て決まるため、
つないでから頼むとスライド 1 枚が地続きの読み方になる。区切りに置く間は、
音程が決まったあとでアクセント句の `pause_mora`(無音の拍)として入れる。
こうすると間は保ったまま、間の前後がひと続きの発話になる。

合成に渡した下書きは作業ディレクトリに残すので、失敗したときは同じ内容を
そのまま再送して確認できる。

利用条件: VOICEVOX で作った音声を公開する場合、キャラクター名の表示
(例: `VOICEVOX:ずんだもん`)が必要。使った音声に対応する表記は
`narration.json` の `credit` に入れている。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .speech import (
    AudioFormat,
    SpeechFailure,
    SpeechJob,
    SpeechNotAvailableError,
    SpeechPiece,
    SynthesisError,
    SynthesisReport,
    Voice,
)

ENGINE_NAME = "voicevox"
DEFAULT_URL = "http://127.0.0.1:50021"
#: 動画の音声トラックに合わせる。VOICEVOX は任意の周波数で出力できる。
DEFAULT_SAMPLE_RATE = 48000
#: 起動を待つ時間(秒)。初回は音声モデルの読み込みで時間がかかる。
DEFAULT_STARTUP_TIMEOUT = 180.0

#: ナレーション向きの声を上から順に選ぶ。読み上げが目的の落ち着いた声を優先する。
#: どれも入っていない場合は、最初の会話用スタイルを使う。
PREFERRED_STYLES = (
    "No.7/アナウンス",
    "No.7/読み聞かせ",
    "玄野武宏/ノーマル",
    "九州そら/ノーマル",
    "冥鳴ひまり/ノーマル",
)

_ENGINE_CANDIDATES = (
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "VOICEVOX", "vv-engine", "run.exe"
    ),
    os.path.join(os.environ.get("ProgramFiles", ""), "VOICEVOX", "vv-engine", "run.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VOICEVOX Engine", "run.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""), "VOICEVOX ENGINE", "run.exe"),
)

_INSTALL_HINT = (
    "VOICEVOX(https://voicevox.hiroshiba.jp/)を入れるか、既に起動している場合は "
    "--voicevox-url で場所を指定してください。Windows 標準の音声合成を使う場合は "
    "--engine sapi または --engine onecore を指定できます。"
)


@dataclass(frozen=True)
class Style:
    """話者 1 人のスタイル 1 つ。これが音声の選択単位になる。"""

    speaker: str
    style: str
    id: int
    kind: str = "talk"
    uuid: str = ""

    @property
    def name(self) -> str:
        return f"{self.speaker}/{self.style}"

    @property
    def credit(self) -> str:
        """公開時に表示する必要のあるクレジット表記。"""
        return f"VOICEVOX:{self.speaker}"

    def to_voice(self) -> Voice:
        return Voice(
            name=self.name,
            language="ja-JP",
            engine=ENGINE_NAME,
            note=f"id={self.id}",
        )


# ---------------------------------------------------------------------------
# エンジンの場所
# ---------------------------------------------------------------------------


def candidate_paths(explicit: Optional[str] = None) -> List[str]:
    if explicit:
        return [explicit]
    paths = [os.environ.get("VOICEVOX_ENGINE_PATH", ""), *_ENGINE_CANDIDATES]
    return [p for p in paths if p and os.path.dirname(p)]


def find_engine_exe(explicit: Optional[str] = None) -> Optional[str]:
    """VOICEVOX ENGINE の `run.exe` を探す。"""
    for candidate in candidate_paths(explicit):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class HttpFailure(Exception):
    """1 回の HTTP 呼び出しの失敗。合成の 1 件分の失敗として扱う。"""

    def __init__(self, url: str, message: str, status: Optional[int] = None, body: str = "") -> None:
        self.url = url
        self.status = status
        self.body = body
        detail = f"HTTP {status}: " if status else ""
        if body.strip():
            message = f"{message}\n    応答: {_shorten(body)}"
        super().__init__(f"{detail}{message}")


def _shorten(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _request(url: str, data: Optional[bytes] = None, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 応答が読めなくても本来の失敗を伝えたい
            pass
        raise HttpFailure(url, exc.reason or "要求が拒否されました", exc.code, body) from exc
    except urllib.error.URLError as exc:
        raise HttpFailure(url, f"VOICEVOX ENGINE に接続できません: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpFailure(url, f"応答がありません(待ち時間 {timeout:g} 秒)") from exc
    except OSError as exc:
        raise HttpFailure(url, f"通信に失敗しました: {exc}") from exc


# ---------------------------------------------------------------------------
# エンジン
# ---------------------------------------------------------------------------


class VoicevoxEngine:
    """VOICEVOX ENGINE を音声合成エンジンとして使う。"""

    name = ENGINE_NAME

    def __init__(
        self,
        url: Optional[str] = None,
        exe: Optional[str] = None,
        autostart: bool = True,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        on_startup: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.base_url = (url or os.environ.get("VOICEVOX_URL") or DEFAULT_URL).rstrip("/")
        self.exe = exe
        self.autostart = autostart
        self.startup_timeout = startup_timeout
        self.on_startup = on_startup
        self._process: Optional[subprocess.Popen] = None
        self._styles: Optional[List[Style]] = None
        self._version = ""
        self._ready = False

    # -- 起動 -----------------------------------------------------------
    def ensure_ready(self) -> str:
        """接続できる状態にする。起動していなければ起動する。"""
        if self._ready:
            return self._version
        version = self._probe()
        if version is None:
            version = self._start()
        self._version = version
        self._ready = True
        return version

    def _probe(self, timeout: float = 3.0) -> Optional[str]:
        try:
            raw = _request(f"{self.base_url}/version", timeout=timeout)
        except HttpFailure:
            return None
        try:
            return str(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace").strip()

    def _start(self) -> str:
        if not self.autostart:
            raise SpeechNotAvailableError(
                f"VOICEVOX ENGINE に接続できません: {self.base_url}\n"
                "VOICEVOX を起動してから実行するか、--voicevox-url で場所を指定してください。"
            )
        exe = find_engine_exe(self.exe)
        if not exe:
            tried = "\n".join(f"  {p}" for p in candidate_paths(self.exe))
            raise SpeechNotAvailableError(
                f"VOICEVOX ENGINE が見つかりません。{_INSTALL_HINT}\n"
                f"接続を試した場所: {self.base_url}\n探した場所:\n{tried}"
            )

        host, port = _split_url(self.base_url)
        command = [exe, "--host", host, "--port", str(port)]
        if self.on_startup:
            self.on_startup(f"VOICEVOX ENGINE を起動しています: {exe}")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=os.path.dirname(exe),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise SynthesisError(f"VOICEVOX ENGINE を起動できませんでした: {exc}", command) from exc

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                stderr = _read_stderr(self._process)
                raise SynthesisError(
                    "VOICEVOX ENGINE がすぐに終了しました。",
                    command,
                    returncode=self._process.returncode,
                    stderr=stderr,
                )
            version = self._probe()
            if version is not None:
                return version
            time.sleep(1.0)

        self.close()
        raise SynthesisError(
            f"VOICEVOX ENGINE が {self.startup_timeout:g} 秒以内に起動しませんでした。"
            "先に VOICEVOX を起動しておくか、--voicevox-startup-timeout を延ばしてください。",
            command,
        )

    def close(self) -> None:
        """自分で起動した場合だけ止める(利用者が開いている VOICEVOX は触らない)。"""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    def __enter__(self) -> "VoicevoxEngine":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- 情報 -----------------------------------------------------------
    def default_format(self, sample_rate: Optional[int] = None) -> AudioFormat:
        return AudioFormat(sample_rate or DEFAULT_SAMPLE_RATE)

    def honors_sample_rate(self) -> bool:
        return True

    def version(self) -> str:
        return self._version

    def list_styles(self, refresh: bool = False) -> List[Style]:
        if self._styles is not None and not refresh:
            return self._styles
        self.ensure_ready()
        url = f"{self.base_url}/speakers"
        try:
            raw = _request(url, timeout=30)
            data = json.loads(raw.decode("utf-8"))
        except HttpFailure as exc:
            raise SynthesisError("話者の一覧を取得できませんでした。", ["GET", url]) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SynthesisError(
                f"話者の一覧を読み取れませんでした: {exc}", ["GET", url]
            ) from exc

        styles = [
            Style(
                speaker=str(speaker.get("name", "")),
                style=str(style.get("name", "")),
                id=int(style.get("id")),
                kind=str(style.get("type") or "talk"),
                uuid=str(speaker.get("speaker_uuid", "")),
            )
            for speaker in data
            if isinstance(speaker, dict)
            for style in speaker.get("styles", [])
            if isinstance(style, dict) and style.get("id") is not None
        ]
        if not styles:
            raise SynthesisError("VOICEVOX に使える音声がありません。", ["GET", url])
        self._styles = styles
        return styles

    def list_voices(self, timeout: float = 30, refresh: bool = False) -> List[Voice]:
        return [style.to_voice() for style in self.list_styles(refresh=refresh)]

    def pick_style(self, name: Optional[str] = None) -> Style:
        """使う音声を決める。

        名前は「話者/スタイル」で指定する。スタイルを省くとその話者の最初の
        会話用スタイルを使う。指定が無ければナレーション向きの声を順に探す。
        """
        styles = self.list_styles()
        if name:
            wanted = name.strip()
            for style in styles:
                if style.name == wanted:
                    return style
            speaker_matches = [s for s in styles if s.speaker == wanted and s.kind == "talk"]
            if speaker_matches:
                return speaker_matches[0]
            if wanted.isdigit():
                for style in styles:
                    if style.id == int(wanted):
                        return style
            raise SpeechNotAvailableError(
                f"音声が見つかりません: {name}\n"
                "「話者/スタイル」の形で指定してください(--list-voices で一覧を表示できます)。"
            )

        talk = [s for s in styles if s.kind == "talk"] or styles
        by_name = {s.name: s for s in talk}
        for preferred in PREFERRED_STYLES:
            if preferred in by_name:
                return by_name[preferred]
        return talk[0]

    def pick_voice(self, name: Optional[str] = None, language: str = "ja") -> Voice:
        return self.pick_style(name).to_voice()

    def credit_for(self, voice_name: str) -> str:
        for style in self.list_styles():
            if style.name == voice_name:
                return style.credit
        return ""

    # -- 合成 -----------------------------------------------------------
    def synthesize(
        self,
        jobs: Sequence[SpeechJob],
        workdir: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        volume: int = 100,
        sample_rate: Optional[int] = None,
        timeout: float = 600,
        on_done: Optional[Callable[[int], None]] = None,
        pitch: float = 0.0,
        intonation: float = 1.0,
    ) -> SynthesisReport:
        """まとめて合成する。1 件の失敗で全体を止めず、失敗した件だけを返す。"""
        report = SynthesisReport(voice=voice or "")
        if not jobs:
            return report

        self.ensure_ready()
        style = self.pick_style(voice)
        report.voice = style.name
        report.command = _curl_command(self.base_url, style.id)

        os.makedirs(workdir, exist_ok=True)
        self._initialize_speaker(style.id)

        deadline = time.monotonic() + timeout
        for job in jobs:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report.failures.append(
                    SpeechFailure(
                        job.index,
                        f"合成が {timeout:g} 秒以内に終わりませんでした"
                        "(--timeout を延ばしてください)",
                    )
                )
                continue
            try:
                self._synthesize_one(
                    job, workdir, style, speed, volume, sample_rate, pitch, intonation, remaining
                )
            except HttpFailure as exc:
                report.failures.append(SpeechFailure(job.index, str(exc)))
                continue
            except OSError as exc:
                report.failures.append(SpeechFailure(job.index, f"音声を保存できません: {exc}"))
                continue
            if on_done:
                on_done(job.index)
        return report

    def _initialize_speaker(self, style_id: int) -> None:
        """音声モデルを先に読み込ませる。最初の 1 件だけ遅くなるのを避ける。"""
        try:
            _request(f"{self.base_url}/initialize_speaker?speaker={style_id}", data=b"", timeout=180)
        except HttpFailure:
            pass  # 読み込みは合成時にも行われるため、失敗しても続ける

    def _synthesize_one(
        self,
        job: SpeechJob,
        workdir: str,
        style: Style,
        speed: float,
        volume: int,
        sample_rate: Optional[int],
        pitch: float,
        intonation: float,
        timeout: float,
    ) -> None:
        query = {
            "accent_phrases": self._read_continuously(job.pieces, style.id, speed, timeout),
            "speedScale": float(speed),
            "pitchScale": float(pitch),
            "intonationScale": float(intonation),
            "volumeScale": max(0.0, min(1.0, volume / 100.0)),
            # 前後の余白は音声を組み立てるときに入れ直すため、ここでは付けない。
            "prePhonemeLength": 0.0,
            "postPhonemeLength": 0.0,
            "outputSamplingRate": int(sample_rate or DEFAULT_SAMPLE_RATE),
            "outputStereo": False,
        }
        # 失敗したときに同じ内容を送り直せるよう、下書きを残す。
        query_path = os.path.join(workdir, f"query_{job.index:04d}.json")
        with open(query_path, "w", encoding="utf-8") as f:
            json.dump(query, f, ensure_ascii=False, indent=2)

        payload = json.dumps(query, ensure_ascii=False).encode("utf-8")
        wav = _request(f"{self.base_url}/synthesis?speaker={style.id}", payload, timeout)
        if not wav:
            raise HttpFailure(f"{self.base_url}/synthesis", "音声が空でした")
        with open(job.out_path, "wb") as f:
            f.write(wav)

    def _read_continuously(
        self, pieces: Sequence[SpeechPiece], style_id: int, speed: float, timeout: float
    ) -> List[dict]:
        """1 枚分の区切りを、ひと続きに読み上げるアクセント句の並びにする。

        区切りごとに読みを作ってからつなぎ、音程は 1 枚分まとめて決める。
        こうしないと、区切りの先頭が毎回「文章の書き出し」の音程になる。
        """
        groups = [self._accent_phrases(piece.text, style_id, timeout) for piece in pieces]
        combined = [phrase for group in groups for phrase in group]
        if not combined:
            raise HttpFailure(
                f"{self.base_url}/accent_phrases",
                f"読み上げられる文字がありませんでした: {''.join(p.text for p in pieces)!r}",
            )

        combined = self._mora_data(combined, style_id, timeout)
        _place_pauses(combined, [len(group) for group in groups], [p.pause_after for p in pieces], speed)
        return combined

    def _accent_phrases(self, text: str, style_id: int, timeout: float) -> List[dict]:
        query = urllib.parse.urlencode({"text": text, "speaker": style_id})
        url = f"{self.base_url}/accent_phrases?{query}"
        data = _json_response(url, _request(url, data=b"", timeout=timeout), "読み")
        if not isinstance(data, list):
            raise HttpFailure(url, "読みの形式が想定と違います")
        return data

    def _mora_data(self, phrases: List[dict], style_id: int, timeout: float) -> List[dict]:
        """つないだアクセント句の長さと音程を、前後を見て決め直す。"""
        url = f"{self.base_url}/mora_data?speaker={style_id}"
        payload = json.dumps(phrases, ensure_ascii=False).encode("utf-8")
        data = _json_response(url, _request(url, payload, timeout), "音程")
        if not isinstance(data, list) or len(data) != len(phrases):
            raise HttpFailure(url, "音程の形式が想定と違います")
        return data


def _json_response(url: str, raw: bytes, what: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpFailure(url, f"{what}を取得できません: {exc}") from exc


def _place_pauses(
    phrases: List[dict], lengths: Sequence[int], pauses: Sequence[float], speed: float
) -> None:
    """区切りの終わりに、指定した長さの無音を置く(その場で書き換える)。

    間はアクセント句の `pause_mora`(無音の拍)として入れる。音程を決めたあとに
    入れるのは、間の長さを読み方に影響させないため(間を置くこと自体は、
    ひと続きの読み上げの中の息継ぎとして扱われる)。

    最後の区切りの後ろには置かない。スライドを読み終えたあとの無音は音声を
    組み立てる側(`audio.py`)が付ける。

    VOICEVOX は合成時にすべての長さを speedScale で割るため、指定した速さでも
    間が変わらないよう、あらかじめ掛けておく。
    """
    end = 0
    boundary: Optional[int] = None  # 間を置ける最後の位置(直前の区切りの終わり)
    pending = 0.0
    for length, pause in zip(lengths, pauses):
        if length:
            # 次の読みが始まるまでに溜まった間を、直前の区切りの終わりに置く。
            if boundary is not None:
                phrases[boundary]["pause_mora"] = (
                    _pause_mora(pending * max(speed, 0.0)) if pending > 0 else None
                )
            end += length
            boundary = end - 1
            pending = 0.0
        # 読みが 1 つも作られなかった区切り(記号だけなど)の間は、次に持ち越す。
        pending += max(0.0, pause)


def _pause_mora(seconds: float) -> dict:
    return {
        "text": "、",
        "consonant": None,
        "consonant_length": None,
        "vowel": "pau",
        "vowel_length": float(seconds),
        "pitch": 0.0,
    }


def _split_url(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 50021


def _read_stderr(process: subprocess.Popen) -> str:
    if process.stderr is None:
        return ""
    try:
        from .proc import decode_output

        return decode_output(process.stderr.read())
    except OSError:
        return ""


def _curl_command(base_url: str, style_id: int) -> List[str]:
    """失敗したときに手元で同じ要求を出すためのコマンド。"""
    return [
        "curl",
        "-X",
        "POST",
        f"{base_url}/synthesis?speaker={style_id}",
        "-H",
        "Content-Type: application/json",
        "--data-binary",
        "@query_0001.json",
        "-o",
        "out.wav",
    ]


def is_installed(exe: Optional[str] = None) -> bool:
    return find_engine_exe(exe) is not None
