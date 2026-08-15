"""ナレーションの声の候補を、同じ原稿で聴き比べられる形にする。

どの声を標準にするかは、聞いた人にしか決められない。合成できることと、
30 分の教材を最後まで聞けることは別なので、ここでは「決める」のではなく
「決めるための材料」を作る。

    原稿(.md / .pptx / script.json)
        --> 候補ごとに既存のナレーション生成(audio.export_narration)
        --> 候補ごとの通し音声(continuous.wav)  ... 続けて聞いたときの印象
        --> 候補を並べた頭出し音声(preview.wav) ... 第一印象と直接比較
        --> compare.json / compare.md           ... 設定と選定理由

比較の条件は候補の間でそろえる。原稿・間の取り方・音量の目標・出力形式は
すべて同じものを使い、候補ごとに変えるのは声と読み上げ設定だけにする。
音量は候補ごとに(その候補の全スライドをまとめて)目標へそろえるので、
声の大きさの差が印象に混ざらない。

候補は「人気のある話者」ではなく、長時間のeラーニングで問題になりにくい
条件で選ぶ。数が多すぎて聞けないので、まず `voice_survey` で全部の声を測って
絞り、そこから選ぶ。判断の根拠は `Candidate.reason` に文章で持たせ、成果物の
`compare.md` にそのまま書き出す(あとから選定の理由を確認できるようにする)。
入れなかった声の理由も `NOT_CHOSEN` に残す。

候補は使うエンジンを指名できる(VOICEVOX と VOICEVOX Nemo は別のエンジンで、
公開時のクレジット表記も違う)。エンジンは名前ごとに 1 つだけ作って使い回す。

ここでは標準の音声を変更しない。`voicevox.py` の `preferred_styles` は
人が聴き比べて決めたあとで書き換える。Gen8 の聴き比べでは `nemo-male3`
(VOICEVOX Nemo の「男声3/ノーマル」)が選ばれ、それが現在の標準になっている。
標準を見直したくなったときに、同じ材料をもう一度作れるようにここを残す。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence

from . import tts as tts_mod
from . import waveform as wave_mod
from .audio import AudioExportError, AudioOptions, NarrationResult, export_narration
from .narration import NarrationError
from .speech import SpeechError

#: 候補ごとの通し音声。1 枚ずつの WAV をスライド順につないだもの。
CONTINUOUS_NAME = "continuous.wav"
#: 候補の頭出しを並べた音声。第一印象を直接比べるために使う。
PREVIEW_NAME = "preview.wav"
INDEX_NAME = "compare.json"
REPORT_NAME = "compare.md"
SOURCE_NAME = "source.pptx"

#: 頭出しに入れる長さ(秒)。文の途中で切らないよう、スライド単位で足していく。
DEFAULT_PREVIEW_SECONDS = 30.0
#: 頭出しで候補と候補の間に置く無音(秒)。切り替わりが分かる長さにする。
PREVIEW_GAP = 1.5

_ID = re.compile(r"^[0-9a-z][0-9a-z-]*$")


class VoiceCompareError(RuntimeError):
    """聴き比べ用の音声を作れなかった場合。"""


class OutputExistsError(VoiceCompareError):
    """出力先に前回の結果が残っている場合(--force で上書きできる)。"""


# ---------------------------------------------------------------------------
# 候補
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """聴き比べる候補 1 つ。

    声(話者/スタイル)と読み上げ設定の組で 1 候補になる。同じ話者でも設定が
    違えば別の候補として扱う(声を変えずに設定だけで良くなるなら、そのほうが
    キャラクターの表示や利用条件が変わらない)。

    `reason` は「なぜこの候補を聴き比べに入れたか」。聞いた人が、印象と
    選定の意図を突き合わせられるように、成果物へそのまま書き出す。
    """

    id: str  # 出力ディレクトリ名になる識別子(英小文字・数字・ハイフン)
    voice: str  # 「話者/スタイル」
    reason: str  # この候補を入れた理由
    role: str = ""  # 比較の中での位置づけ(基準 / 別スタイル / 別話者 など)
    engine: str = ""  # 合成に使うエンジン(空なら基準のエンジン)
    speed: float = 1.0
    pitch: float = 0.0
    intonation: float = 1.0
    #: None なら基準の設定をそのまま使う(間だけを変えた候補を作れるようにする)。
    sentence_pause: Optional[float] = None
    line_pause: Optional[float] = None

    @property
    def speaker(self) -> str:
        return self.voice.split("/", 1)[0]

    @property
    def style(self) -> str:
        return self.voice.split("/", 1)[1] if "/" in self.voice else ""

    def options(self, base: AudioOptions) -> AudioOptions:
        """基準の出力条件に、この候補の声と読み上げ設定を重ねる。"""
        reading = replace(base.reading)
        if self.sentence_pause is not None:
            reading.sentence_pause = self.sentence_pause
        if self.line_pause is not None:
            reading.line_pause = self.line_pause
        return replace(
            base,
            engine=self.engine or base.engine,
            voice=self.voice,
            speed=self.speed,
            pitch=self.pitch,
            intonation=self.intonation,
            reading=reading,
        )

    def describe_settings(self, base: AudioOptions) -> str:
        """基準と違うところだけを並べる(何を変えた候補かが分かるようにする)。"""
        changed = []
        if self.speed != base.speed:
            changed.append(f"速さ {self.speed:g}")
        if self.pitch != base.pitch:
            changed.append(f"高さ {self.pitch:+g}")
        if self.intonation != base.intonation:
            changed.append(f"抑揚 {self.intonation:g}")
        if self.sentence_pause is not None and self.sentence_pause != base.reading.sentence_pause:
            changed.append(f"文の間 {self.sentence_pause:g}秒")
        if self.line_pause is not None and self.line_pause != base.reading.line_pause:
            changed.append(f"行の間 {self.line_pause:g}秒")
        return " / ".join(changed) if changed else "既定のまま"

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "voice": self.voice,
            "speaker": self.speaker,
            "style": self.style,
            "role": self.role,
            "engine": self.engine,
            "reason": self.reason,
            "speed": self.speed,
            "pitch": self.pitch,
            "intonation": self.intonation,
        }
        if self.sentence_pause is not None:
            data["sentence_pause"] = self.sentence_pause
        if self.line_pause is not None:
            data["line_pause"] = self.line_pause
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Candidate":
        if not isinstance(data, dict):
            raise VoiceCompareError(f"候補は辞書で書いてください: {data!r}")
        missing = [key for key in ("id", "voice") if not str(data.get(key, "")).strip()]
        if missing:
            raise VoiceCompareError(
                f"候補に {' と '.join(missing)} がありません: {json.dumps(data, ensure_ascii=False)}"
            )
        try:
            return cls(
                id=str(data["id"]).strip(),
                voice=str(data["voice"]).strip(),
                reason=str(data.get("reason", "")),
                role=str(data.get("role", "")),
                engine=str(data.get("engine", "")),
                speed=float(data.get("speed", 1.0)),
                pitch=float(data.get("pitch", 0.0)),
                intonation=float(data.get("intonation", 1.0)),
                sentence_pause=_optional_float(data.get("sentence_pause")),
                line_pause=_optional_float(data.get("line_pause")),
            )
        except (TypeError, ValueError) as exc:
            raise VoiceCompareError(
                f"候補の値を読み取れません({exc}): {json.dumps(data, ensure_ascii=False)}"
            ) from exc


def _optional_float(value) -> Optional[float]:
    return None if value is None else float(value)


#: 聴き比べる候補。長時間のeラーニングで問題になりにくい条件で選ぶ。
#:
#: 選び方は 2 段階。まず `--survey` で使える声(VOICEVOX 127 種類 + VOICEVOX Nemo
#: 9 種類)を同じ文で測り、`voice_survey.DEFAULT_SCREEN` の条件に入る声だけを残す。
#: この時点で 136 種類が 20 種類になる。人気や知名度は使わない。
#: 次に、残ったものから次の観点で選ぶ。
#:
#:   1. 30 分聞いても疲れないか(高さが低め・抑揚が過剰でない)
#:   2. 声ではなく内容に注意が向くか(キャラクター性・演技の強さが弱い)
#:   3. 視聴者を選ばないか(特定の層にだけ強く好かれる声になっていないか)
#:   4. 比較として意味があるか(基準との違いが 1 つに絞れているか)
#:
#: 4 のために、候補は「基準と何を変えたものか」が 1 つずつ違うようにしてある。
#: 同じ役割の候補を 2 つ入れても、聞く時間が倍になるだけで判断は増えない。
#:
#: VOICEVOX Nemo からは 2 つだけ入れた。Nemo の声は人格を持たず、公開時の表示も
#: 話者名を含まない「VOICEVOX Nemo」で済む。キャラクターの声とは前提が違うので、
#: 女性の声域から 1 つ・男性の声域から 1 つを入れ、残り 7 つは入れていない
#: (測定値が近く、比較の判断が増えないため)。
CANDIDATES: Sequence[Candidate] = (
    Candidate(
        id="no7-announce",
        voice="No.7/アナウンス",
        role="Gen7 までの標準(比較の基準)",
        reason=(
            "Gen7 まで標準にしていた声。Gen7 までの改善はこの声で確認しているため、"
            "他の候補が良いかどうかはこれと比べて判断する。"
            "No.7 は特定の人格を演じる設定を持たない話者で、"
            "「アナウンス」は原稿を読むために用意されたスタイル。"
            "語尾を伸ばさず、文末を下げて言い切るので、説明の区切りが分かりやすい。"
            "測定値は 207Hz / 抑揚 2.70 半音 / 8.48 モーラ秒で、どれも中庸に位置する。"
        ),
    ),
    Candidate(
        id="no7-calm",
        voice="No.7/アナウンス",
        role="Gen7 標準の設定違い(落ち着かせた版)",
        speed=0.95,
        intonation=0.9,
        sentence_pause=0.45,
        line_pause=0.75,
        reason=(
            "声を変えずに、読み上げ設定だけで長時間向きにできるかを見る候補。"
            "アナウンス調は 1 文ごとの抑揚がはっきりしていて、短い動画では聞き取りやすい反面、"
            "何十分も続くと報道番組のような緊張感が残り、聞き手が休むところを失いやすい。"
            "そこで速さを 5% 落とし、抑揚を 1 割弱め、文と行の間を広げた。"
            "考えながら聞く教材では、間そのものが理解の時間になる。"
            "弱めすぎると平坦で眠くなるため、変更は控えめにしている。"
            "これが基準より良ければ、話者を変えずに標準を更新できる"
            "(公開時のクレジット表示も変わらない)。"
        ),
    ),
    Candidate(
        id="nemo-female1",
        voice="女声1/ノーマル",
        engine="voicevox-nemo",
        role="人格を持たない声(基準と同じ声域)",
        reason=(
            "VOICEVOX Nemo の声。キャラクターとして作られておらず、名前も「女声1」で、"
            "聞き手が思い浮かべる人物像や作品が無い。公開時の表示も"
            "「VOICEVOX Nemo」だけで済み、動画にキャラクター名が出ない。"
            "教材では、話し手の印象が内容より先に立たないほうがよい。"
            "225Hz / 抑揚 2.55 半音 / 8.35 モーラ秒と、基準(207Hz / 2.70 / 8.48)に"
            "最も近い測定値を持つ声を Nemo の女性の声域から選んだ。"
            "声域と読み方をそろえてあるので、"
            "「キャラクターの声かどうか」の違いだけを聞き比べられる。"
        ),
    ),
    Candidate(
        id="nemo-male3",
        voice="男声3/ノーマル",
        engine="voicevox-nemo",
        role="現在の標準(人格を持たない声・低い男性の声域)",
        reason=(
            "同じく VOICEVOX Nemo から、男性の声域で最も低く、その中で抑揚も小さい声"
            "(128Hz / 抑揚 2.90 半音 / 8.68 モーラ秒)。"
            "基本周波数が低いほど耳に刺さりにくく、音量を上げずに聞き取れるため、"
            "長時間の再生では有利になりやすい。"
            "同じ Nemo の男声1(150Hz)は 10.03 モーラ秒と速すぎ、"
            "男声2 は抑揚が 3.76 半音と大きいので入れていない。"
            "女性の声だけで比べると、聞き手によって受け取り方が偏る可能性が残る。"
            "Gen8 で全候補を聞いたうえで、この声を標準のナレーションに決めた。"
        ),
    ),
    Candidate(
        id="mitama-normal",
        voice="暁記ミタマ/ノーマル",
        role="別話者(基準より低く、抑揚の小さい女性)",
        reason=(
            "191Hz / 抑揚 1.85 半音 / 7.10 モーラ秒。基準より低く、抑揚は 3 割小さい。"
            "基準と同じ「キャラクターの声」の枠の中で、"
            "抑揚を落とすと長時間で楽になるのか、それとも平坦で眠くなるのかを見る。"
            "前回入れていた冥鳴ひまり(254Hz / 1.88)は、抑揚の小ささが同じで"
            "高さだけが基準より高く、この候補と役割が重なるため今回は外した。"
        ),
    ),
    Candidate(
        id="rito-normal",
        voice="離途/ノーマル",
        role="別話者(最も低く、抑揚の小さい男性)",
        reason=(
            "109Hz / 抑揚 1.76 半音 / 8.05 モーラ秒。"
            "絞り込みに残った 20 種類の中で最も低く、抑揚も最も小さい部類にある。"
            "低く平坦な読み方が長時間で楽なのか、抑揚が足りず聞き流してしまうのかは、"
            "測定値では決められないので実際に聞いて判断したい。"
            "前回入れていた青山龍星/ノーマルは、測ると抑揚が 3.77 半音で"
            "「淡々と読む」という前回の見立てより大きく、玄野武宏/ノーマルも 3.04 半音と、"
            "どちらも基準(2.70 半音)を超える。低い男性の声という役割は同じなので、"
            "同じ役割で抑揚のより小さいこの声に置き換えた。"
        ),
    ),
)

#: 絞り込みには残ったが候補に入れなかった声と、その理由。
#:
#: 「なぜ入れたか」と同じくらい「なぜ入れなかったか」が残っていないと、
#: 次に見直すときに同じ検討をやり直すことになる。聞いてみたい声があれば
#: `compare.json` に足して `--candidates` で渡せる。
NOT_CHOSEN: Sequence[tuple] = (
    (
        "No.7/読み聞かせ",
        "前回入れていた、基準と同じ話者の別スタイル。5.90 モーラ秒と他より 3 割遅く、"
        "同じ原稿でも聞く時間が変わるため、比較の条件がそろわない。"
        "同じ話者の設定違いは no7-calm で見られる。",
    ),
    (
        "冥鳴ひまり/ノーマル",
        "前回の候補。254Hz / 1.88 半音。抑揚の小ささは mitama-normal と同じ役割で、"
        "高さだけが基準より高い。役割が重なるので今回は外した。",
    ),
    (
        "青山龍星/ノーマル・玄野武宏/ノーマル",
        "前回の男性の候補。測ると抑揚が 3.77 / 3.04 半音で基準(2.70)を超える。"
        "低い男性の声という役割は rito-normal と nemo-male3 が引き継ぐ。",
    ),
    (
        "VOICEVOX Nemo の残り 7 声",
        "女声2〜6 は女声1 と声域も抑揚も近く、聞いて分かる差にならない。"
        "男声1 は 10.03 モーラ秒と速く、男声2 は抑揚 3.76 半音と大きい。",
    ),
    (
        "里石ユカ/つぼみ・栗田まろん/ノーマル・雀松朱司/ノーマルほか",
        "絞り込みに残った他の声。今回の 6 つで"
        "「高さ」「抑揚」「人格の有無」「読み上げ設定」の違いは一通り聞けるため、"
        "まずはこの 6 つを聞いて方向を決める。",
    ),
)


def load_candidates(path: str) -> List[Candidate]:
    """候補の一覧を JSON から読む。

    組み込みの候補以外を試したいときに使う。形は `compare.json` の
    `candidates` と同じなので、生成された結果をそのまま直して読み込める。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise VoiceCompareError(f"候補の一覧を読み取れませんでした: {path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise VoiceCompareError(
            f"候補の一覧が JSON として読めません: {path}\n{exc.lineno} 行目: {exc.msg}"
        ) from exc

    if isinstance(data, dict):
        data = data.get("candidates", [])
    if not isinstance(data, list) or not data:
        raise VoiceCompareError(
            f"候補が 1 つもありません: {path}\n"
            '[{"id": "no7-announce", "voice": "No.7/アナウンス", "reason": "..."}] '
            "の形で書いてください。"
        )
    return [Candidate.from_dict(item) for item in data]


def _validate(candidates: Sequence[Candidate]) -> None:
    if not candidates:
        raise VoiceCompareError("聴き比べる候補がありません。")
    seen: Dict[str, int] = {}
    for candidate in candidates:
        # id は出力ディレクトリ名になるので、環境によらず作れる文字に限る。
        if not _ID.match(candidate.id):
            raise VoiceCompareError(
                f"候補の id は英小文字・数字・ハイフンで付けてください: {candidate.id!r}"
            )
        if candidate.id in seen:
            raise VoiceCompareError(f"候補の id が重複しています: {candidate.id}")
        seen[candidate.id] = 1
        if candidate.speed <= 0:
            raise VoiceCompareError(
                f"読み上げ速度は 0 より大きくしてください: {candidate.id} の {candidate.speed}"
            )


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass
class CandidateResult:
    """候補 1 つ分の結果。失敗しても他の候補を続けるため、失敗も結果として持つ。"""

    candidate: Candidate
    outdir: str
    narration: Optional[NarrationResult] = None
    continuous_path: str = ""
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def voice(self) -> str:
        return self.narration.voice if self.narration else self.candidate.voice

    @property
    def credit(self) -> str:
        return self.narration.credit if self.narration else ""


@dataclass(frozen=True)
class PreviewMark:
    """頭出し音声の中で、どの候補が何秒目から始まるか。"""

    id: str
    start: float
    duration: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "at": _timestamp(self.start),
            "duration": round(self.duration, 3),
        }


@dataclass
class CompareResult:
    source: str = ""
    outdir: str = ""
    engine: str = ""
    options: Optional[AudioOptions] = None
    results: List[CandidateResult] = field(default_factory=list)
    preview_path: str = ""
    preview_marks: List[PreviewMark] = field(default_factory=list)
    index_path: str = ""
    report_path: str = ""

    @property
    def succeeded(self) -> List[CandidateResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> List[CandidateResult]:
        return [r for r in self.results if not r.ok]


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def prepare_source(source: str, workdir: str) -> str:
    """Markdown を渡された場合だけ、先に資料(.pptx)にする。

    候補ごとに作り直すと原稿がずれる可能性があるため、1 回だけ作って
    すべての候補で同じファイルを読む。
    """
    if not source.lower().endswith(".md"):
        return source
    from .markdown_reader import parse_article_file
    from .planner import plan_deck
    from .renderer import render_deck

    os.makedirs(workdir, exist_ok=True)
    output = os.path.join(workdir, SOURCE_NAME)
    render_deck(plan_deck(parse_article_file(source)), output)
    return output


def compare_voices(
    source: str,
    outdir: str,
    candidates: Optional[Sequence[Candidate]] = None,
    options: Optional[AudioOptions] = None,
    engine=None,
    powershell: Optional[str] = None,
    voicevox_options: Optional[dict] = None,
    force: bool = False,
    keep_work: bool = False,
    timeout: float = 600.0,
    preview_seconds: float = DEFAULT_PREVIEW_SECONDS,
    on_candidate: Optional[Callable[[int, int, Candidate], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> CompareResult:
    """候補ごとに同じ原稿を読み上げさせ、聴き比べる材料を書き出す。"""
    candidates = list(candidates if candidates is not None else CANDIDATES)
    _validate(candidates)
    base = options or AudioOptions()
    base.validate()

    os.makedirs(outdir, exist_ok=True)
    index_path = os.path.join(outdir, INDEX_NAME)
    if os.path.exists(index_path) and not force:
        raise OutputExistsError(
            f"出力先に前回の結果があります: {index_path}\n"
            "上書きする場合は --force を付けてください。"
        )

    result = CompareResult(outdir=os.path.abspath(outdir), options=base)
    try:
        result.source = os.path.abspath(prepare_source(source, outdir))
    except (OSError, ValueError) as exc:
        raise VoiceCompareError(f"入力を資料に変換できませんでした: {source}\n{exc}") from exc

    engines = _Engines(base, powershell, voicevox_options, engine)
    try:
        _require_voices(engines, candidates, base.language)
        result.engine = " / ".join(engines.names)
        for position, candidate in enumerate(candidates, start=1):
            if on_candidate:
                on_candidate(position, len(candidates), candidate)
            result.results.append(
                _run_candidate(
                    candidate,
                    result.source,
                    outdir,
                    base,
                    engines.of(candidate),
                    keep_work,
                    timeout,
                    on_progress,
                )
            )
    finally:
        engines.close()

    if preview_seconds > 0 and result.succeeded:
        result.preview_path = os.path.join(outdir, PREVIEW_NAME)
        result.preview_marks = _write_preview(
            result.succeeded, result.preview_path, base.sample_rate, preview_seconds
        )
    result.index_path = _write_index(outdir, result, preview_seconds)
    result.report_path = _write_report(outdir, result, preview_seconds)
    return result


class _Engines:
    """候補が使うエンジンを、名前ごとに 1 つだけ作って使い回す。

    候補は別々のエンジンの声を指せる(VOICEVOX と VOICEVOX Nemo など)。
    候補ごとに作り直すと、そのたびにエンジンの起動と音声モデルの読み込みが
    起きるため、名前ごとに 1 つ作って最後にまとめて閉じる。
    """

    def __init__(
        self,
        base: AudioOptions,
        powershell: Optional[str],
        voicevox_options: Optional[dict],
        engine=None,
    ) -> None:
        self._base = base
        self._powershell = powershell
        self._voicevox_options = voicevox_options
        # 呼び出し側が用意したエンジンは、基準のエンジンとして使い、閉じない。
        self._given = engine
        self._engines: Dict[str, object] = {}
        self._owned: List[object] = []

    def of(self, candidate: Candidate):
        name = candidate.engine or self._base.engine
        if name not in self._engines:
            if self._given is not None and not candidate.engine:
                self._engines[name] = self._given
            else:
                engine = tts_mod.select_engine(
                    name, self._powershell, self._base.language, self._voicevox_options
                )
                self._engines[name] = engine
                self._owned.append(engine)
        return self._engines[name]

    @property
    def names(self) -> List[str]:
        seen = []
        for engine in self._engines.values():
            if engine.name not in seen:
                seen.append(engine.name)
        return seen

    def close(self) -> None:
        for engine in self._owned:
            engine.close()
        self._owned.clear()


def _require_voices(engines: "_Engines", candidates: Sequence[Candidate], language: str) -> None:
    """合成を始める前に、候補の声がすべて使えるかを確かめる。

    候補が 6 つあると全部で 20 分以上かかる。名前の間違いは最初に分かるほうがよい。
    エンジンの用意もここで済ませるので、起動できないエンジンも先に分かる。
    """
    unknown = []
    for candidate in candidates:
        # エンジンそのものが使えない場合は、候補の書き間違いではないので
        # ここで止めず、そのまま呼び出し側へ返す(終了コードが変わる)。
        engine = engines.of(candidate)
        try:
            engine.pick_voice(candidate.voice, language)
        except SpeechError as exc:
            where = f"({candidate.engine})" if candidate.engine else ""
            unknown.append(
                f"  {candidate.id}: {candidate.voice}{where}\n" + _indent(str(exc), "    ")
            )
    if unknown:
        raise VoiceCompareError(
            "次の候補の声が使えません。\n"
            + "\n".join(unknown)
            + "\n--list-voices(note2slides-audio)で使える音声を確認してください。"
        )


def _run_candidate(
    candidate: Candidate,
    source: str,
    outdir: str,
    base: AudioOptions,
    engine,
    keep_work: bool,
    timeout: float,
    on_progress: Optional[Callable[[int], None]],
) -> CandidateResult:
    """1 候補分を合成する。失敗しても他の候補を止めない(比較は残りで続けられる)。"""
    candidate_dir = os.path.join(outdir, candidate.id)
    result = CandidateResult(candidate=candidate, outdir=os.path.abspath(candidate_dir))
    try:
        narration = export_narration(
            source,
            candidate_dir,
            options=candidate.options(base),
            engine=engine,  # 候補ごとに起動し直さないよう、同じエンジンを使い回す
            force=True,  # 出力先の確認は聴き比べ全体で 1 回だけ行っている
            keep_work=keep_work,
            timeout=timeout,
            on_progress=on_progress,
        )
    except (AudioExportError, NarrationError, SpeechError) as exc:
        result.error = str(exc)
        return result

    result.narration = narration
    try:
        result.continuous_path = os.path.join(candidate_dir, CONTINUOUS_NAME)
        result.duration = _write_continuous(narration, result.continuous_path, base.sample_rate)
    except wave_mod.WaveformError as exc:
        result.error = f"通し音声を作れませんでした: {exc}"
    return result


def _write_continuous(narration: NarrationResult, path: str, sample_rate: int) -> float:
    """スライドごとの WAV を順につないで、続けて聞ける 1 本にする。

    動画にしたときと同じ並び・同じ間で聞けるようにするため、間や無音を
    足し引きせず、書き出されたものをそのままつなぐ。
    """
    with wave_mod.WavWriter(path, sample_rate) as writer:
        for clip in narration.clips:
            writer.append(wave_mod.read_wav(clip.path))
    return writer.duration


def _write_preview(
    results: Sequence[CandidateResult], path: str, sample_rate: int, seconds: float
) -> List[PreviewMark]:
    """候補の頭出しを 1 本に並べる。

    続けて聞いた印象は候補ごとの通し音声で判断できるが、声そのものの違いは
    直後に聞き比べたほうが分かる。文の途中で切れると比べにくいので、
    指定の長さを超えるまでスライド単位で足していく。
    """
    marks: List[PreviewMark] = []
    with wave_mod.WavWriter(path, sample_rate) as writer:
        for result in results:
            if writer.frames:
                writer.append(wave_mod.silence(PREVIEW_GAP, sample_rate))
            start = writer.duration
            for clip in result.narration.clips if result.narration else []:
                if writer.duration - start >= seconds:
                    break
                writer.append(wave_mod.read_wav(clip.path))
            marks.append(PreviewMark(result.candidate.id, start, writer.duration - start))
    return marks


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def _write_index(outdir: str, result: CompareResult, preview_seconds: float) -> str:
    """機械が読む一覧。候補の定義をそのまま含むので、直して再実行できる。"""
    base = result.options or AudioOptions()
    data = {
        "source": os.path.basename(result.source),
        "engine": result.engine,
        "base": {
            "sample_rate": base.sample_rate,
            "loudness": base.loudness,
            "peak_ceiling": base.peak_ceiling,
            "silence": base.silent_duration,
            "reading": base.reading.to_dict(),
        },
        "preview": {
            "file": PREVIEW_NAME if result.preview_path else None,
            "seconds": preview_seconds,
            "marks": [mark.to_dict() for mark in result.preview_marks],
        },
        "candidates": [_candidate_entry(item, base) for item in result.results],
    }
    path = os.path.join(outdir, INDEX_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return os.path.abspath(path)


def _candidate_entry(result: CandidateResult, base: AudioOptions) -> dict:
    entry = dict(result.candidate.to_dict())
    entry["dir"] = result.candidate.id
    entry["ok"] = result.ok
    if result.error:
        entry["error"] = result.error
        return entry
    narration = result.narration
    entry.update(
        {
            "engine": narration.engine if narration else result.candidate.engine,
            "voice": result.voice,
            "credit": result.credit,
            "continuous": f"{result.candidate.id}/{CONTINUOUS_NAME}",
            "manifest": f"{result.candidate.id}/narration.json",
            "duration": round(result.duration, 3),
            "slides": narration.count if narration else 0,
            "reading": result.candidate.options(base).reading.to_dict(),
            "loudness": narration.loudness.to_dict() if narration and narration.loudness else None,
            "warnings": list(narration.warnings) if narration else [],
        }
    )
    return entry


def _write_report(outdir: str, result: CompareResult, preview_seconds: float) -> str:
    """人が読む比較表。聞く順番と、それぞれを候補にした理由を残す。"""
    base = result.options or AudioOptions()
    lines: List[str] = [
        "# ナレーション音声の聴き比べ",
        "",
        f"原稿: `{os.path.basename(result.source)}` / エンジン: {result.engine}",
        "",
        f"現在の標準は {_default_narration_label()} です。"
        "このコマンドは標準を変更しないので、変える場合は最後の手順に従ってください。",
        "",
        "## 聞く順番",
        "",
    ]
    if result.preview_marks:
        lines += [
            f"1. `{PREVIEW_NAME}` — 各候補の冒頭 {preview_seconds:g} 秒を並べたもの。"
            "声の違いはここで分かります。",
            f"2. 気になった候補の `<候補>/{CONTINUOUS_NAME}` — "
            f"全 {_slides_of(result)} 枚を通して聞くもの。"
            "第一印象では分からない、聞き続けたときの疲れやすさを見ます。",
            "",
            "`preview.wav` の頭出し位置です。",
            "",
            "| 時刻 | 候補 |",
            "| --- | --- |",
        ]
        by_id = {r.candidate.id: r for r in result.results}
        for mark in result.preview_marks:
            candidate = by_id[mark.id].candidate
            lines.append(f"| {_timestamp(mark.start)} | {mark.id}（{candidate.voice}） |")
    else:
        lines.append(f"各候補の `<候補>/{CONTINUOUS_NAME}` を続けて聞いてください。")
    lines += [
        "",
        "## 候補",
        "",
        "候補は、使える声を全部測って絞り込んだ中から選んでいます"
        "(`--survey` を付けると、測定値の一覧 `survey.md` を作れます)。",
        "",
        "| 候補 | 話者/スタイル | エンジン | 位置づけ | 設定 | 長さ |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.results:
        candidate = item.candidate
        length = _hms(item.duration) if item.ok else "生成できず"
        engine = (item.narration.engine if item.narration else "") or candidate.engine or "-"
        lines.append(
            f"| {candidate.id} | {candidate.voice} | {engine} | {candidate.role or '-'} | "
            f"{candidate.describe_settings(base)} | {length} |"
        )

    if base.loudness is None:
        loudness_line = "* 音量: 調整なし(合成したままの音量)"
    else:
        loudness_line = (
            f"* 音量: 候補ごとに全スライドをまとめて {base.loudness:g} LUFS へそろえ、"
            f"ピークを {base.peak_ceiling:g} dBFS で抑える"
        )
    lines += [
        "",
        "比較の条件は候補の間でそろえています。",
        "",
        f"* 原稿・スライドの区切り・読み方の整形: すべて同じ(`{os.path.basename(result.source)}`)",
        f"* 間: 文 {base.reading.sentence_pause:g} 秒 / 行 {base.reading.line_pause:g} 秒 / "
        f"前 {base.reading.lead_silence:g} 秒 / 後 {base.reading.tail_silence:g} 秒"
        "(設定を変えた候補は上の表に記載)",
        loudness_line,
        f"* 形式: WAV {wave_mod.describe_format(base.sample_rate)}",
        "",
        "音量をそろえているので、声の大きさの差は印象に混ざりません。",
        "",
    ]
    lines += _limiting_lines(result)
    lines += ["## それぞれを候補にした理由", ""]
    for item in result.results:
        candidate = item.candidate
        lines += [
            f"### {candidate.id} — {candidate.voice}",
            "",
            f"位置づけ: {candidate.role or '-'} / 設定: {candidate.describe_settings(base)}",
            "",
            candidate.reason,
            "",
        ]
        if item.ok:
            lines += [
                f"* 通し音声: `{candidate.id}/{CONTINUOUS_NAME}`（{_hms(item.duration)}）",
                f"* 設定と読み上げ内容: `{candidate.id}/narration.json`",
            ]
            if item.credit:
                lines.append(f"* 公開時の表示: {item.credit}")
            for warning in item.narration.warnings if item.narration else []:
                lines.append(f"* 注意: {warning}")
            lines.append("")

    if NOT_CHOSEN:
        lines += [
            "## 候補に入れなかった声",
            "",
            "聞く時間が増えるだけで判断が増えない候補は入れていません。"
            "気になるものがあれば `compare.json` の `candidates` に足して、"
            "`--candidates` で渡せば同じ条件で作れます。",
            "",
        ]
        for voice, reason in NOT_CHOSEN:
            lines += [f"* **{voice}** — {reason}"]
        lines.append("")

    failed = result.failed
    if failed:
        lines += ["## 生成できなかった候補", ""]
        for item in failed:
            lines += [
                f"### {item.candidate.id} — {item.candidate.voice}",
                "",
                "```",
                item.error.strip(),
                "```",
                "",
            ]

    lines += [
        "## 決めたあとにすること",
        "",
        f"現在の標準は {_default_narration_label()} です。",
        "別の候補を標準にする場合は、`src/note2slides/voicevox.py` にある",
        "そのエンジン(`VOICEVOX` / `NEMO`)の `preferred_styles` の先頭を、",
        "選んだ話者/スタイルに書き換えます。エンジンをまたいで変える場合は、",
        "同じファイルの `EDITIONS` の並び順(auto がどちらを先に試すか)も",
        "入れ替えてください。",
        "読み上げ設定も変えた候補を選んだ場合は、`audio_cli.py` の既定値",
        "(`--speed` / `--intonation` / `--sentence-pause` / `--line-pause`)も",
        "あわせて変更してください。既定を変えずに 1 回だけ試すなら、`--voice` で指定できます。",
        "",
        "```bash",
        ".venv/Scripts/python.exe -m note2slides.video_cli build/sample.pptx -o build/sample.mp4 \\",
        '    --engine voicevox-nemo --voice "選んだ話者/スタイル"',
        "```",
        "",
    ]
    path = os.path.join(outdir, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return os.path.abspath(path)


def _limiting_lines(result: CompareResult) -> List[str]:
    """ピークを抑えた量を候補ごとに出す。

    合成音声は平均に対してピークだけが飛び出すため、目標の音量まで持ち上げると
    飛び出した分を抑えることになる(`audio.py`)。抑える量は声によって違い、
    大きいほど音の質が変わる。処理は全候補で同じでも、**結果として受ける影響は
    同じではない** ので、聞く前に分かるようにしておく。
    """
    rows = [
        (item.candidate.id, item.narration.loudness)
        for item in result.succeeded
        if item.narration and item.narration.loudness
    ]
    if not rows:
        return []
    lines = [
        "ただし、ピークを抑えた量は声によって違います。抑える量が大きい候補は、"
        "そのぶん音の質も変わっています(声そのものの違いとは別に効きます)。",
        "",
        "| 候補 | 合成時の音量 | 持ち上げた量 | ピークを抑えた量 |",
        "| --- | --: | --: | --: |",
    ]
    for candidate_id, loudness in rows:
        lines.append(
            f"| {candidate_id} | {loudness.measured_lufs:.1f} LUFS | "
            f"{loudness.gain_db:+.1f} dB | {loudness.limit_db:.1f} dB |"
        )
    lines.append("")
    return lines


def _slides_of(result: CompareResult) -> int:
    for item in result.succeeded:
        if item.narration:
            return item.narration.count
    return 0


def _default_narration_label() -> str:
    """いま標準になっている声。合成に使われる設定から引くので、書き換え忘れが起きない。"""
    engine, voice = tts_mod.default_narration()
    return f"`{engine}` の「{voice}」" if voice else f"`{engine}`"


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}分{total % 60:02d}秒"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
