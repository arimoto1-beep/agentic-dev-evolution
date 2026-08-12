"""Windows の音声合成を PowerShell 経由で呼び出す。

PowerPoint と同じく、追加のサービスや課金を前提にしないため、Windows に最初から
入っている音声合成をそのまま使う。呼び出しは同梱の `speech.ps1` に任せ、
Python 側は「何を読み上げて、どこに WAV を書くか」だけを渡す。

エンジンは 2 つある。どちらも Windows の標準機能で、オフラインで動く。

    onecore  Windows.Media.SpeechSynthesis(Ayumi / Haruka / Ichiro / Sayaka など)
    sapi     System.Speech(Haruka Desktop など。出力形式を指定できる)

1 回の呼び出しで全スライド分をまとめて合成する。PowerShell の起動は 1 回で済み、
1 件ごとに成否が返るので、失敗したスライドだけを特定できる。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .proc import CommandError, decode_output, format_command

#: 利用できるエンジン。auto はこの順で試す。
ENGINES = ("onecore", "sapi")
ENGINE_AUTO = "auto"

#: OneCore は出力形式を指定できず、この形式で返ってくる。
ONECORE_SAMPLE_RATE = 16000
#: SAPI は形式を指定できる。動画の音声トラックに合わせて 48kHz を既定にする。
SAPI_SAMPLE_RATE = 48000

_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speech.ps1")

_POWERSHELL_CANDIDATES = (
    os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    ),
    "powershell.exe",
    "powershell",
)

_INSTALL_HINT = (
    "Windows PowerShell(powershell.exe)が必要です。"
    "場所を --powershell または環境変数 POWERSHELL_PATH で指定できます。"
)


class SpeechError(RuntimeError):
    """音声合成に関する失敗。"""


class SpeechNotAvailableError(SpeechError):
    """音声合成そのものが使えない場合(PowerShell やエンジン、音声が無い)。"""


class SynthesisError(CommandError, SpeechError):
    """合成の実行が失敗した場合。実行したコマンドと出力を保持する。"""

    def __init__(
        self,
        reason: str,
        command: List[str],
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            reason,
            command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            hint="PowerShell は何も出力していません。",
        )


@dataclass(frozen=True)
class Voice:
    name: str
    language: str = ""
    gender: str = ""
    engine: str = ""

    def speaks(self, language: str) -> bool:
        """指定した言語(`ja` や `ja-JP`)の音声かどうか。"""
        if not language:
            return True
        return self.language.lower().startswith(language.lower().split("-")[0])

    def describe(self) -> str:
        parts = [self.name]
        if self.language:
            parts.append(self.language)
        if self.gender:
            parts.append(self.gender)
        return " / ".join(parts)


@dataclass(frozen=True)
class AudioFormat:
    """WAV の形式。動画側で音声をつなぐときに揃っている必要がある。"""

    sample_rate: int
    channels: int = 1
    sample_width: int = 2  # バイト数(2 = 16bit)

    def describe(self) -> str:
        return f"{self.sample_rate}Hz / {self.sample_width * 8}bit / {'モノラル' if self.channels == 1 else f'{self.channels}ch'}"

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width": self.sample_width,
        }


@dataclass
class SpeechJob:
    """1 件の読み上げ。index は原稿(=スライド)の番号。"""

    index: int
    text: str
    out_path: str


@dataclass
class SpeechFailure:
    index: int
    message: str


@dataclass
class SynthesisReport:
    voice: str = ""
    failures: List[SpeechFailure] = field(default_factory=list)
    command: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# PowerShell の場所
# ---------------------------------------------------------------------------


def candidate_paths(explicit: Optional[str] = None) -> List[str]:
    if explicit:
        return [explicit]
    paths = [os.environ.get("POWERSHELL_PATH", ""), *_POWERSHELL_CANDIDATES]
    return [p for p in paths if p]


def find_powershell(explicit: Optional[str] = None) -> Optional[str]:
    for candidate in candidate_paths(explicit):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        found = shutil.which(candidate)
        if found:
            return found
    return None


def require_powershell(explicit: Optional[str] = None) -> str:
    powershell = find_powershell(explicit)
    if not powershell:
        tried = "\n".join(f"  {p}" for p in candidate_paths(explicit))
        raise SpeechNotAvailableError(
            f"PowerShell が見つかりません。{_INSTALL_HINT}\n探した場所:\n{tried}"
        )
    return powershell


# ---------------------------------------------------------------------------
# エンジン
# ---------------------------------------------------------------------------


class SpeechEngine:
    """`speech.ps1` を通して Windows の音声合成を使う。"""

    def __init__(self, name: str, powershell: Optional[str] = None) -> None:
        if name not in ENGINES:
            known = " / ".join(ENGINES)
            raise SpeechNotAvailableError(f"未知のエンジンです: {name}(利用できるのは {known})")
        self.name = name
        self.powershell = require_powershell(powershell)
        self._voices: Optional[List[Voice]] = None

    # -- 情報 -----------------------------------------------------------
    def default_format(self, sample_rate: Optional[int] = None) -> AudioFormat:
        """このエンジンが書き出す WAV の形式。

        実際の形式は書き出した WAV から読み直すが、1 件も合成しなかった場合
        (すべて無音のとき)はこの値を使う。
        """
        if self.name == "onecore":
            return AudioFormat(ONECORE_SAMPLE_RATE)
        return AudioFormat(sample_rate or SAPI_SAMPLE_RATE)

    def honors_sample_rate(self) -> bool:
        """出力の標本化周波数を指定できるか。"""
        return self.name == "sapi"

    def list_voices(self, timeout: float = 120, refresh: bool = False) -> List[Voice]:
        # 一覧の取得にも PowerShell の起動が要るため、一度読んだら覚えておく。
        if self._voices is not None and not refresh:
            return self._voices
        command = self._command("-ListVoices", self.name)
        completed = self._run(command, timeout)
        voices = [
            Voice(
                name=str(item.get("name", "")),
                language=str(item.get("language", "")),
                gender=str(item.get("gender", "")),
                engine=self.name,
            )
            for item in _parse_lines(decode_output(completed.stdout))
            if item.get("kind") == "voice" and item.get("name")
        ]
        if not voices:
            raise SynthesisError(
                f"{self.name} で使える音声が見つかりませんでした。",
                command,
                returncode=completed.returncode,
                stdout=decode_output(completed.stdout),
                stderr=decode_output(completed.stderr),
            )
        self._voices = voices
        return voices

    def pick_voice(self, name: Optional[str] = None, language: str = "ja") -> Voice:
        """使う音声を決める。名前の指定が無ければ、指定言語の最初の音声を選ぶ。"""
        voices = self.list_voices()
        if name:
            for voice in voices:
                if voice.name == name:
                    return voice
            available = "\n".join(f"  {v.describe()}" for v in voices)
            raise SpeechNotAvailableError(
                f"音声が見つかりません: {name}(エンジン: {self.name})\n"
                f"使える音声:\n{available}"
            )
        for voice in voices:
            if voice.speaks(language):
                return voice
        available = "\n".join(f"  {v.describe()}" for v in voices)
        raise SpeechNotAvailableError(
            f"{language} の音声が {self.name} に入っていません。\n"
            "Windows の設定 > 時刻と言語 > 言語と地域 から日本語の音声を追加するか、"
            "--voice で使う音声を指定してください。\n"
            f"使える音声:\n{available}"
        )

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
    ) -> SynthesisReport:
        """まとめて合成する。失敗した件は report.failures に入れて返す。

        1 件の失敗で全体を止めない。どのスライドが失敗したかを一度に見せた方が
        原因を追いやすいため。
        """
        report = SynthesisReport(voice=voice or "")
        if not jobs:
            return report

        os.makedirs(workdir, exist_ok=True)
        job_path = self._write_job(jobs, workdir, voice, speed, volume, sample_rate)
        command = self._command("-JobFile", job_path)
        report.command = command

        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SynthesisError(
                f"音声合成が {timeout:g} 秒以内に終わりませんでした。"
                "--timeout を延ばすか、原稿を短くしてください。",
                command,
                stdout=decode_output(exc.stdout),
                stderr=decode_output(exc.stderr),
            ) from exc
        except OSError as exc:
            raise SynthesisError(f"PowerShell を起動できませんでした: {exc}", command) from exc

        report.stdout = decode_output(completed.stdout)
        report.stderr = decode_output(completed.stderr)
        results = _parse_lines(report.stdout)

        for item in results:
            if item.get("kind") == "engine" and item.get("voice"):
                report.voice = str(item["voice"])

        done = {
            int(item["index"]): item
            for item in results
            if item.get("kind") == "done" and isinstance(item.get("index"), int)
        }
        if not done:
            raise SynthesisError(
                "音声合成が 1 件も実行されませんでした。",
                command,
                returncode=completed.returncode,
                stdout=report.stdout,
                stderr=report.stderr,
            )

        for job in jobs:
            result = done.get(job.index)
            failure = _check_result(job, result)
            if failure:
                report.failures.append(failure)
            elif on_done:
                on_done(job.index)
        return report

    # -- 内部 -----------------------------------------------------------
    def _command(self, *args: str) -> List[str]:
        return [
            self.powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _SCRIPT_PATH,
            *args,
        ]

    def _write_job(
        self,
        jobs: Sequence[SpeechJob],
        workdir: str,
        voice: Optional[str],
        speed: float,
        volume: int,
        sample_rate: Optional[int],
    ) -> str:
        """読み上げる文章と出力先を JSON にまとめる。

        文章は 1 件ずつ UTF-8 のテキストにして渡す。コマンドライン引数に日本語を
        載せると、環境の文字コードによっては壊れるため。
        """
        items = []
        for job in jobs:
            text_path = os.path.join(workdir, f"text_{job.index:04d}.txt")
            with open(text_path, "w", encoding="utf-8") as f:
                f.write(job.text)
            items.append(
                {
                    "index": job.index,
                    "text_file": os.path.abspath(text_path),
                    "out_file": os.path.abspath(job.out_path),
                }
            )

        data = {
            "engine": self.name,
            "voice": voice or "",
            "speed": speed,
            "rate": sapi_rate(speed),
            "volume": max(0, min(100, int(volume))),
            "sample_rate": sample_rate or SAPI_SAMPLE_RATE,
            "items": items,
        }
        path = os.path.join(workdir, "job.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return os.path.abspath(path)

    def _run(self, command: List[str], timeout: float) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SynthesisError(
                f"PowerShell が {timeout:g} 秒以内に終了しませんでした。",
                command,
                stdout=decode_output(exc.stdout),
                stderr=decode_output(exc.stderr),
            ) from exc
        except OSError as exc:
            raise SynthesisError(f"PowerShell を起動できませんでした: {exc}", command) from exc


def _check_result(job: SpeechJob, result: Optional[dict]) -> Optional[SpeechFailure]:
    """1 件の結果を確かめる。PowerShell が成功と言っても出力の有無まで見る。"""
    if result is None:
        return SpeechFailure(job.index, "結果が返ってきませんでした(合成が中断した可能性があります)")
    if not result.get("ok"):
        return SpeechFailure(job.index, str(result.get("error") or "原因不明の失敗"))
    if not os.path.isfile(job.out_path):
        return SpeechFailure(job.index, f"音声ファイルが作られませんでした: {job.out_path}")
    if os.path.getsize(job.out_path) == 0:
        return SpeechFailure(job.index, f"音声ファイルが空です: {job.out_path}")
    return None


def _parse_lines(stdout: str) -> List[dict]:
    """1 行 1 件の JSON を読む。JSON でない行(警告など)は読み飛ばす。"""
    items = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def sapi_rate(speed: float) -> int:
    """速度の倍率を SAPI の Rate(-10〜10 の整数)に直す。

    SAPI の Rate は 1 増えるごとにおよそ 1.4 倍の速さになるため、対数で換算する。
    """
    if speed <= 0:
        raise SpeechError(f"読み上げ速度は 0 より大きくしてください: {speed}")
    return max(-10, min(10, round(math.log(speed, 1.4))))


def select_engine(
    name: str = ENGINE_AUTO,
    powershell: Optional[str] = None,
    language: str = "ja",
) -> SpeechEngine:
    """使うエンジンを決める。auto なら、指定言語の音声があるものを順に探す。"""
    if name != ENGINE_AUTO:
        return SpeechEngine(name, powershell)

    problems = []
    for candidate in ENGINES:
        try:
            engine = SpeechEngine(candidate, powershell)
            engine.pick_voice(language=language)
            return engine
        except SpeechNotAvailableError as exc:
            problems.append(f"[{candidate}] {exc}")
        except SpeechError as exc:
            problems.append(f"[{candidate}] {exc}")
    detail = "\n".join(problems)
    raise SpeechNotAvailableError(
        f"{language} の音声合成が使えるエンジンが見つかりませんでした。\n{detail}"
    )


def available_engines(powershell: Optional[str] = None) -> List[str]:
    """PowerShell が使える環境かどうかだけを見て、候補を返す。"""
    if not find_powershell(powershell):
        return []
    return list(ENGINES)


def script_path() -> str:
    """呼び出しに使う PowerShell スクリプトの場所(失敗時の再現用)。"""
    return _SCRIPT_PATH


def describe_command(command: Sequence[str]) -> str:
    return format_command(list(command))
