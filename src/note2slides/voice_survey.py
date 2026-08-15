"""使える声を、音にする前に測って一覧にする。

VOICEVOX と VOICEVOX Nemo を合わせると 130 以上のスタイルがある。全部を
聴き比べるのは現実的ではないので、まず同じ文を読ませて、長時間のeラーニングで
効いてくる 3 つを測り、候補を絞る材料にする。

    高さ   基本周波数(Hz)。高いほど耳に付きやすく、長時間では疲れやすい
    抑揚   基本周波数の振れ幅(半音)。大きいほど演技寄りで、内容より声に注意が向く
    速さ   話す速さ(モーラ/秒)。同じ倍率でも話者によって元の速さが違う

合成はしない。読み(アクセント句)を作って長さと音程を決めるところまでは
合成と同じ手順なので、ここで測った値は実際に聞こえる音声のものと一致する。
1 話者あたり 1 秒もかからないため、全話者を測っても数分で終わる。

測るのは「どれが良いか」を決めるためではなく、**人気や知名度ではない基準で
候補を絞る** ため。最終的にどれを標準にするかは、聴き比べた人が決める。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from . import tts as tts_mod
from . import voicevox as voicevox_mod
from .speech import SpeechError
from .voicevox import Edition, Style, VoicevoxEngine

INDEX_NAME = "survey.json"
REPORT_NAME = "survey.md"

#: 測るのに使う文。eラーニングの説明文らしい長さと言い回しにそろえてある。
#: 話者どうしを比べるので、いつも同じ文で測る(文を変えると値が動く)。
DEFAULT_TEXT = (
    "情報セキュリティは、専門の担当者だけが気にすればよいものではありません。"
    "業務で扱う情報の多くは、日々の作業の中で受け取り、加工し、誰かに渡しています。"
    "まず、守る対象と、その重要度を決めます。"
)

#: 半音への換算(音程は自然対数の Hz で返ってくる)。
_SEMITONES_PER_LOG = 12.0 / math.log(2.0)
#: これより低い音程は声帯が鳴っていない(ささやき声)とみなす。
#: ささやき声は音の高さを持たないので、平均に混ぜると値が壊れる。
_MIN_PITCH_HZ = 50.0

#: 読み上げのために用意されたスタイル。話者の既定のスタイルでなくても残す。
READING_STYLES = ("アナウンス", "読み聞かせ")


@dataclass(frozen=True)
class Screen:
    """聴き比べる候補を絞るときの条件。

    長時間のeラーニングで問題になりやすいところ(高すぎる声・大きすぎる抑揚・
    極端な速さ)を落とすためのもの。良し悪しを決めるものではなく、
    **人気や知名度ではない基準で数を減らす** ために使う。

    感情や演技のスタイル(あまあま・怒り・ささやき など)も落とす。説明を読み上げる
    用途では選ばないうえ、同じ話者から何種類も残ると聞く時間だけが増えるため、
    話者ごとに既定のスタイル(一覧の先頭)と読み上げ用のスタイルだけを残す。
    """

    pitch_hz: tuple = (100.0, 260.0)  # 高いほど耳に付き、低すぎると聞き取りにくい
    pitch_spread: tuple = (0.0, 3.0)  # 抑揚の振れ幅(半音)
    rate: tuple = (6.5, 9.0)  # 話す速さ(モーラ/秒)。倍率で直せる範囲に収める

    def passes(self, item: "Measurement") -> bool:
        if not item.ok or not item.voiced:
            return False
        if not (item.default_style or item.style in READING_STYLES):
            return False
        return all(
            low <= value <= high
            for value, (low, high) in (
                (item.pitch_hz, self.pitch_hz),
                (item.pitch_spread, self.pitch_spread),
                (item.rate, self.rate),
            )
        )

    def describe(self) -> List[str]:
        return [
            f"* 高さ {self.pitch_hz[0]:g}〜{self.pitch_hz[1]:g} Hz",
            f"* 抑揚 {self.pitch_spread[1]:g} 半音以下",
            f"* 速さ {self.rate[0]:g}〜{self.rate[1]:g} モーラ/秒",
            "* 話者ごとに既定のスタイル("
            + " / ".join(READING_STYLES)
            + " は別に残す。感情や演技のスタイルは外す)",
        ]


#: 標準のナレーションを選ぶときに使っている条件。
DEFAULT_SCREEN = Screen()


@dataclass(frozen=True)
class Measurement:
    """スタイル 1 つ分の測定結果。"""

    engine: str
    voice: str
    id: int
    credit: str
    pitch_hz: float = 0.0  # 基本周波数(相乗平均)
    pitch_spread: float = 0.0  # 基本周波数の振れ幅(半音、標準偏差)
    rate: float = 0.0  # 話す速さ(モーラ/秒。間は含めない)
    moras: int = 0
    voiced: int = 0  # 音程を持つモーラの数(ささやき声は 0 になる)
    seconds: float = 0.0
    #: その話者の既定のスタイル(一覧の先頭)かどうか。
    default_style: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def style(self) -> str:
        return self.voice.split("/")[-1]

    def to_dict(self) -> dict:
        data = {"engine": self.engine, "voice": self.voice, "id": self.id, "credit": self.credit}
        if self.error:
            data["error"] = self.error
            return data
        data.update(
            {
                "pitch_hz": round(self.pitch_hz, 1),
                "pitch_spread": round(self.pitch_spread, 2),
                "rate": round(self.rate, 2),
                "moras": self.moras,
                "voiced": self.voiced,
                "seconds": round(self.seconds, 3),
                "default_style": self.default_style,
            }
        )
        return data


def measure(phrases: Sequence[dict]) -> tuple:
    """読み(アクセント句)から、高さ・振れ幅・速さを出す。

    音程は自然対数の Hz で入っている。高さは相乗平均(対数のまま平均して戻す)、
    振れ幅は対数の標準偏差を半音に直したものにする。どちらも音の高さの感じ方に
    合わせるため、Hz ではなく対数で扱う。

    無声のモーラ(声帯が鳴っていないもの)は音の高さを持たないので数に入れない。
    ささやき声のスタイルは全体が無声になり、高さも振れ幅も出ない。
    速さは間(`pause_mora`)を除いたモーラ数 ÷ 秒で出す。間の長さはこちらで
    決め直すため、話者の速さの比較には入れない。
    """
    pitches: List[float] = []
    seconds = 0.0
    moras = 0
    for phrase in phrases:
        for mora in phrase.get("moras", []):
            moras += 1
            seconds += float(mora.get("consonant_length") or 0.0)
            seconds += float(mora.get("vowel_length") or 0.0)
            pitch = float(mora.get("pitch") or 0.0)
            if pitch > math.log(_MIN_PITCH_HZ):
                pitches.append(pitch)
    rate = moras / seconds if seconds > 0 else 0.0
    if not pitches:
        return 0.0, 0.0, rate, moras, 0, seconds

    mean = sum(pitches) / len(pitches)
    variance = sum((p - mean) ** 2 for p in pitches) / len(pitches)
    return (
        math.exp(mean),
        math.sqrt(variance) * _SEMITONES_PER_LOG,
        rate,
        moras,
        len(pitches),
        seconds,
    )


def measure_styles(
    engine: VoicevoxEngine,
    text: str = DEFAULT_TEXT,
    styles: Optional[Sequence[Style]] = None,
    timeout: float = 60.0,
    on_measured: Optional[Callable[[Measurement], None]] = None,
) -> List[Measurement]:
    """1 つのエンジンの全スタイルを測る。

    1 つ失敗しても残りは測る(1 話者のために一覧が作れないほうが困る)。
    """
    if styles is None:
        styles = [s for s in engine.list_styles() if s.kind == "talk"]
    # 話者の既定のスタイル(一覧の先頭)は、感情や演技を付けていない読み方になる。
    seen_speakers = set()
    defaults = set()
    for style in styles:
        if style.speaker not in seen_speakers:
            seen_speakers.add(style.speaker)
            defaults.add(style.name)

    results: List[Measurement] = []
    for style in styles:
        try:
            phrases = engine.read(text, style, timeout=timeout)
        except SpeechError as exc:
            result = Measurement(
                engine.name, style.name, style.id, style.credit, error=str(exc)
            )
        else:
            pitch_hz, spread, rate, moras, voiced, seconds = measure(phrases)
            result = Measurement(
                engine.name,
                style.name,
                style.id,
                style.credit,
                pitch_hz,
                spread,
                rate,
                moras,
                voiced,
                seconds,
                default_style=style.name in defaults,
            )
        results.append(result)
        if on_measured:
            on_measured(result)
    return results


def survey_editions(
    editions: Sequence[Edition] = (voicevox_mod.VOICEVOX, voicevox_mod.NEMO),
    text: str = DEFAULT_TEXT,
    voicevox_options: Optional[dict] = None,
    timeout: float = 60.0,
    on_engine: Optional[Callable[[Edition, str], None]] = None,
    on_measured: Optional[Callable[[Measurement], None]] = None,
) -> List[Measurement]:
    """使えるエンジンをすべて測る。使えないエンジンは飛ばす(あるものだけで一覧を作る)。"""
    results: List[Measurement] = []
    for edition in editions:
        engine = VoicevoxEngine(
            edition=edition, **tts_mod.voicevox_engine_options(edition, voicevox_options)
        )
        try:
            version = engine.ensure_ready()
            if on_engine:
                on_engine(edition, version)
            results.extend(measure_styles(engine, text, timeout=timeout, on_measured=on_measured))
        except SpeechError as exc:
            if on_engine:
                on_engine(edition, f"使えません: {_first_line(exc)}")
        finally:
            engine.close()
    return results


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def write_survey(
    outdir: str,
    results: Sequence[Measurement],
    text: str = DEFAULT_TEXT,
    marked: Sequence[str] = (),
    screen: Screen = DEFAULT_SCREEN,
) -> tuple:
    """測定結果を JSON と Markdown で書き出す。"""
    os.makedirs(outdir, exist_ok=True)
    index_path = os.path.join(outdir, INDEX_NAME)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "text": text,
                "screen": {
                    "pitch_hz": list(screen.pitch_hz),
                    "pitch_spread": list(screen.pitch_spread),
                    "rate": list(screen.rate),
                    "reading_styles": list(READING_STYLES),
                },
                "voices": [
                    dict(r.to_dict(), passes=screen.passes(r), candidate=r.voice in marked)
                    for r in results
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")
    report_path = os.path.join(outdir, REPORT_NAME)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(_report_lines(results, text, marked, screen)))
    return os.path.abspath(index_path), os.path.abspath(report_path)


def _report_lines(
    results: Sequence[Measurement], text: str, marked: Sequence[str], screen: Screen
) -> List[str]:
    ok = [r for r in results if r.ok]
    shortlist = [r for r in ok if screen.passes(r)]
    lines = [
        "# 使える声の一覧(測定)",
        "",
        f"{len(ok)} 種類の声を、同じ文で読ませて測った結果です。音声は作っていません。",
        "",
        f"> {text}",
        "",
        "| 項目 | 意味 | 長時間のeラーニングでの見方 |",
        "| --- | --- | --- |",
        "| 高さ | 基本周波数(Hz) | 高いほど耳に付きやすく、聞き続けると疲れやすい |",
        "| 抑揚 | 高さの振れ幅(半音) | 大きいほど演技寄りで、内容より声に注意が向く |",
        "| 速さ | モーラ/秒(間を除く) | 同じ倍率でも話者ごとに元の速さが違う |",
        "",
        "この値だけで良し悪しは決まりません(声質・語尾・息づかい・言い切り方は測れません)。",
        "**聴き比べる候補を、人気や知名度ではない基準で絞る** ために使います。",
        "",
        "## 絞り込み",
        "",
        f"{len(ok)} 種類のうち、次の条件に入るのは {len(shortlist)} 種類です。",
        "",
        *screen.describe(),
        "",
        "| | 話者/スタイル | エンジン | 高さ (Hz) | 抑揚 (半音) | 速さ (モーラ/秒) |",
        "| --- | --- | --- | --: | --: | --: |",
    ]
    for row in sorted(shortlist, key=lambda r: r.pitch_hz):
        mark = "*" if row.voice in marked else ""
        lines.append(
            f"| {mark} | {row.voice} | {row.engine} | {row.pitch_hz:.0f} | "
            f"{row.pitch_spread:.2f} | {row.rate:.2f} |"
        )
    lines.append("")
    if marked:
        lines += [
            "`*` は聴き比べの候補にした声です。ここからどれを選んだかと、"
            "その理由は `compare.md` に書いてあります。",
            "",
        ]

    lines += ["## 測定した全部の声", ""]
    for engine in sorted({r.engine for r in results}):
        rows = [r for r in ok if r.engine == engine]
        voiced = [r for r in rows if r.voiced]
        lines += [
            f"### {engine}({len(rows)} 種類)",
            "",
            "| | 話者/スタイル | 高さ (Hz) | 抑揚 (半音) | 速さ (モーラ/秒) |",
            "| --- | --- | --: | --: | --: |",
        ]
        for row in sorted(voiced, key=lambda r: r.pitch_hz):
            mark = "*" if row.voice in marked else ("+" if screen.passes(row) else "")
            lines.append(
                f"| {mark} | {row.voice} | {row.pitch_hz:.0f} | "
                f"{row.pitch_spread:.2f} | {row.rate:.2f} |"
            )
        lines.append("")
        whispers = [r for r in rows if not r.voiced]
        if whispers:
            lines += [
                "ささやき声のスタイルは声帯が鳴らないため、高さと抑揚を測れません"
                "(いずれも説明の読み上げには使いません)。",
                "",
                "* " + " / ".join(sorted(r.voice for r in whispers)),
                "",
            ]

    failed = [r for r in results if not r.ok]
    if failed:
        lines += ["## 測れなかった声", ""]
        for row in failed:
            lines += [f"* {row.engine} / {row.voice}: {row.error.splitlines()[0]}"]
        lines.append("")
    return lines
