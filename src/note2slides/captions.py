"""ナレーションから字幕(.srt / .vtt)と章立て(概要欄用)を作る。

    動画の時間割り + ナレーションの読み上げ単位 --> 字幕 / 章立て

字幕は音声を聞き取り直して作るのではない。何を読み上げたか(`reading.py` が
決めた読み上げ単位)と、それが何秒目にあるか(`video.py` の時間割り)は
すでに分かっているので、その 2 つから組み立てる。聞き取りを挟まないぶん、
字幕の文字は読み上げた文章と必ず一致する。

    build/audio/narration.json   1 枚分の読み上げ単位と間・前後の無音
    動画の時間割り(Segment)     1 枚が何秒目から始まるか

1 枚の音声は「前の無音 + 読み上げ + 後ろの無音」で、読み上げの中に単位ごとの
間が入っている(`audio.py`)。どこからどこまでが 1 つの単位かは、音声を測らずに
次のように割り当てる。

    読み上げに使われた時間 = 音声の長さ - 前後の無音 - 単位の間の合計
    その時間を、各単位の「読み上げるのにかかる長さの目安」で按分する

按分に使う目安は拍(モーラ)数の見積もりなので、実際の合成結果とは少しずれる。
ただしスライドごとに実測の長さへ合わせ直すため、ずれが後ろへ積み上がることは
なく、1 枚の中に収まる(`estimate_moras`)。

字幕は 1 枚 1 つではなく、文(読み上げ単位)ごとに出す。1 枚分をまとめて出すと
数十秒ぶんの文章が画面に出たままになり、いま聞いているところが分からない。
長い文はさらに読点で分けて、1 つの字幕を 2 行までに収める(`CaptionStyle`)。

章立ては、スライドの見出しが変わるところを章の切れ目とみなして作る。YouTube の
概要欄にそのまま貼れる形(`0:00 見出し`)で書き出す。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .speech import SpeechPiece

#: 字幕を作らない。
FORMAT_NONE = "none"
FORMAT_SRT = "srt"
FORMAT_VTT = "vtt"
FORMAT_BOTH = "both"
#: 字幕の形式に指定できる値。
FORMATS = (FORMAT_SRT, FORMAT_VTT, FORMAT_BOTH, FORMAT_NONE)

SRT_SUFFIX = ".srt"
VTT_SUFFIX = ".vtt"
CHAPTERS_SUFFIX = "_chapters.txt"

#: YouTube が章として扱う最短の長さ(秒)。これより短い章は前の章にまとめる。
MIN_CHAPTER_SECONDS = 10.0
#: YouTube が章立てを表示する最少の数。
MIN_CHAPTERS = 3


class CaptionError(RuntimeError):
    """字幕の組み立てに失敗した場合。"""


@dataclass
class CaptionStyle:
    """字幕の見せ方。既定は日本語のナレーションを 2 行までで出す。"""

    line_chars: int = 24  # 1 行に入れる文字数の上限
    max_lines: int = 2  # 1 つの字幕に使う行数の上限
    min_chars: int = 8  # 長い文を分けるとき、これ未満の切れ端は作らない
    min_duration: float = 0.6  # 1 つの字幕を出しておく最短の長さ(秒)

    @property
    def max_chars(self) -> int:
        """1 つの字幕に入る文字数の上限。"""
        return self.line_chars * self.max_lines

    def validate(self) -> None:
        if self.line_chars < 4:
            raise CaptionError(f"字幕の 1 行の文字数は 4 以上にしてください: {self.line_chars}")
        if self.max_lines < 1:
            raise CaptionError(f"字幕の行数は 1 以上にしてください: {self.max_lines}")
        if self.min_chars < 1:
            raise CaptionError("字幕の最小の文字数は 1 以上にしてください")
        if self.min_duration < 0:
            raise CaptionError("字幕の最短の長さは 0 以上にしてください")


@dataclass
class SlideCaption:
    """1 枚のスライドから字幕を作るのに要る材料。

    `duration` は音声ファイルの長さで、前後の無音(`lead_silence` /
    `tail_silence`)を含む。`pieces` はその間にある読み上げ単位と、単位の
    あとに置いた間。
    """

    index: int
    start: float = 0.0  # 動画の中でこのスライドが始まる時刻(秒)
    duration: float = 0.0  # 音声ファイルの長さ(秒)
    lead_silence: float = 0.0
    tail_silence: float = 0.0
    pieces: List[SpeechPiece] = field(default_factory=list)
    title: str = ""

    @property
    def has_speech(self) -> bool:
        return any(piece.text.strip() for piece in self.pieces)


@dataclass(frozen=True)
class Cue:
    """字幕 1 つ。`text` は改行を含むことがある(2 行に分けた場合)。"""

    index: int  # 字幕の通し番号(1 起点)
    slide: int  # もとにしたスライド番号
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "slide": self.slide,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


@dataclass(frozen=True)
class Chapter:
    """章 1 つ。`start` は動画の中での開始時刻(秒)。"""

    start: float
    title: str
    slide: int = 0

    def to_dict(self) -> dict:
        return {"start": round(self.start, 3), "at": chapter_time(self.start), "title": self.title}


# ---------------------------------------------------------------------------
# 読み上げにかかる長さの見積もり
# ---------------------------------------------------------------------------

# 読み上げにかかる長さの見積もりに使う重み。かな 1 文字(1 拍)を 1 とした
# 相対値で、1 枚の中で時間を按分するためのものなので、絶対値ではなく単位
# どうしの比が合っていればよい。
#
#   かな       1 文字 1 拍(「っ」「ん」「ー」も 1 拍として数える)
#   小書き     直前の文字と合わせて 1 拍なので数えない(「しゃ」= 1 拍)
#   漢字       音読み・訓読みの平均でおよそ 2 拍
#   数字       桁と単位の読みが付くため、かなより長い
#   英字       語として読まれるため 1 文字あたりはかなより短い
#   読点       文字としては音にならないが、合成エンジンはここで間を取る
#   句点       文末の音が伸びる
#   その他     音にならないので数えない
#
# 読点と句点の重みは、実際の合成結果(VOICEVOX が返す拍の長さ)に最小二乗で
# 当てはめて決めた。読点を 0 として扱うと、読点の多い文だけ見積もりが短く
# 出て、字幕が 1 秒近く早く変わる。
_SMALL_KANA = "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
_WEIGHT_KANA = 1.0
_WEIGHT_KANJI = 1.7
_WEIGHT_DIGIT = 2.2
_WEIGHT_LATIN = 0.9
_WEIGHT_COMMA = 6.5
_WEIGHT_PERIOD = 0.8

_KANA = re.compile(r"[ぁ-んァ-ヴーｰ]")
_KANJI = re.compile(r"[一-鿿㐀-䶿々〆〇]")
_LATIN = re.compile(r"[A-Za-z]")
_HIRAGANA = re.compile(r"[ぁ-ん]")
_COMMA = "、,，"
_PERIOD = "。.．!?！？"


def estimate_moras(text: str) -> float:
    """読み上げるのにかかる長さの目安(かな 1 文字を 1 とした相対値)。

    合成してみるまで正確な長さは分からないが、1 枚の中で読み上げ時間を
    按分するには単位どうしの比が分かればよい。文字の種類ごとに目安を
    足し合わせる。
    """
    total = 0.0
    for char in text:
        if char in _SMALL_KANA:
            continue
        if _KANA.match(char):
            total += _WEIGHT_KANA
        elif _KANJI.match(char):
            total += _WEIGHT_KANJI
        elif char.isdigit():
            total += _WEIGHT_DIGIT
        elif _LATIN.match(char):
            total += _WEIGHT_LATIN
        elif char in _COMMA:
            total += _WEIGHT_COMMA
        elif char in _PERIOD:
            total += _WEIGHT_PERIOD
    return total


# ---------------------------------------------------------------------------
# 字幕を組み立てる
# ---------------------------------------------------------------------------

# 分けてよい位置(この文字の直後で切る)。読点を優先し、無ければ空白で切る。
_BREAK_AFTER = "、,。．.!?！？」』)）"
_SPACE = re.compile(r"[ 　]")


def build_cues(slides: Sequence[SlideCaption], style: Optional[CaptionStyle] = None) -> List[Cue]:
    """スライドごとの材料から、動画全体の字幕を組み立てる。

    1 枚の中では字幕を切れ目なく並べる(前の字幕は次が出るまで残す)。文と文の
    間で字幕が消えると、間のたびに画面が点滅して読みにくいため。
    """
    style = style or CaptionStyle()
    style.validate()

    cues: List[Cue] = []
    for slide in sorted(slides, key=lambda s: s.index):
        for start, end, text in _slide_spans(slide, style):
            cues.append(
                Cue(index=len(cues) + 1, slide=slide.index, start=start, end=end, text=text)
            )
    return cues


def _slide_spans(slide: SlideCaption, style: CaptionStyle) -> List[Tuple[float, float, str]]:
    """1 枚分の字幕を(開始・終了・文字)の並びにする。"""
    if not slide.has_speech:
        return []

    pieces = [piece for piece in slide.pieces if piece.text.strip()]
    pauses = sum(max(0.0, piece.pause_after) for piece in pieces[:-1])
    speech = slide.duration - slide.lead_silence - slide.tail_silence
    spoken = speech - pauses
    if spoken <= 0:
        # 前後の無音と間だけで音声の長さを使い切っている(長さの情報が
        # 食い違っている)。按分できないので、間も読み上げに含めて割り当てる。
        spoken = max(0.0, speech)
        pauses = 0.0

    weights = [estimate_moras(piece.text) or 1.0 for piece in pieces]
    total = sum(weights)

    spans: List[Tuple[float, float, str]] = []
    at = slide.start + slide.lead_silence
    for position, (piece, weight) in enumerate(zip(pieces, weights)):
        length = spoken * weight / total
        last = position == len(pieces) - 1
        pause = 0.0 if last or not pauses else max(0.0, piece.pause_after)
        # 単位の中をさらに分けるときも、同じ見積もりで時間を割り当てる。
        for text, share in _split_text(piece.text, style):
            end = at + length * share
            spans.append((at, end, text))
            at = end
        # 次の単位までの間は、直前の字幕を出したままにする(点滅を避ける)。
        if pause:
            start, end, text = spans[-1]
            spans[-1] = (start, end + pause, text)
            at = end + pause

    return _merge_short_cues(spans, style)


def _merge_short_cues(
    spans: List[Tuple[float, float, str]], style: CaptionStyle
) -> List[Tuple[float, float, str]]:
    """短すぎる字幕を前の字幕にまとめる(読む前に消えるのを防ぐ)。"""
    if style.min_duration <= 0:
        return spans

    merged: List[Tuple[float, float, str]] = []
    for start, end, text in spans:
        if end - start < style.min_duration and merged:
            previous_start, _, previous_text = merged[-1]
            joined = previous_text.replace("\n", "") + text.replace("\n", "")
            if len(joined) <= style.max_chars:
                merged[-1] = (previous_start, end, _wrap(joined, style))
                continue
        merged.append((start, end, text))
    return merged


def _split_text(text: str, style: CaptionStyle) -> List[Tuple[str, float]]:
    """1 つの読み上げ単位を、画面に入る大きさに分ける。

    返すのは(表示する文字, その単位の中で占める時間の割合)の並び。
    """
    chunks = _split_chunks(text.strip(), style)
    weights = [estimate_moras(chunk) or 1.0 for chunk in chunks]
    total = sum(weights)
    return [(_wrap(chunk, style), weight / total) for chunk, weight in zip(chunks, weights)]


def _split_chunks(text: str, style: CaptionStyle) -> List[str]:
    """画面に入る文字数まで、区切りのよいところで分ける。"""
    if len(text) <= style.max_chars:
        return [text]

    chunks: List[str] = []
    rest = text
    while len(rest) > style.max_chars:
        cut = _break_point(rest, style.max_chars, style.min_chars)
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        # 最後が短すぎる切れ端になるなら前にまとめる(数文字だけが表示される
        # 時間ができるのを避ける)。入りきらない場合は 2 つに分け直す。
        if len(rest) < style.min_chars and chunks:
            joined = chunks[-1] + rest
            if len(joined) <= style.max_chars:
                chunks[-1] = joined
                return chunks
            cut = _break_point(
                joined, style.max_chars, len(joined) - style.max_chars, -(-len(joined) // 2)
            )
            chunks[-1] = joined[:cut].strip()
            rest = joined[cut:].lstrip()
        chunks.append(rest)
    return chunks


def _break_point(text: str, limit: int, minimum: int, fallback: Optional[int] = None) -> int:
    """`minimum` より後ろ・`limit` 文字までの中で切ってよい位置。

    返すのは「その手前までを 1 つにする」位置。区切りが見つからなければ
    `fallback`(既定は `limit`)で切る。
    """
    minimum = min(max(0, minimum), limit - 1)
    # 句読点や閉じ括弧の直後で切る。ちょうど上限の次にある場合も拾って、その
    # 1 文字だけは手前にぶら下げる(行頭に句読点を置かないため)。
    for position in range(min(limit + 1, len(text)), minimum, -1):
        if text[position - 1] in _BREAK_AFTER:
            return position
    for position in range(min(limit, len(text)) - 1, minimum, -1):
        if _SPACE.match(text[position]):
            return position
    # 区切りが無いので長さで切る。ここでも行頭の句読点は避ける。
    cut = limit if fallback is None else min(max(fallback, minimum + 1), limit)
    cut = _avoid_okurigana(text, cut, minimum + 1, limit)
    if cut < len(text) and text[cut] in _BREAK_AFTER:
        return cut + 1
    return cut


def _avoid_okurigana(text: str, cut: int, lowest: int, highest: int) -> int:
    """漢字と送り仮名の間で切らないよう、近い位置へずらす。

    「読み上げる」を「読み上/げる」で折り返すと、読み手はいったん別の語として
    読んでしまう。動かせる範囲に良い位置が無ければ、元の位置のままにする。
    """
    if not _splits_okurigana(text, cut):
        return cut
    for distance in range(1, 4):
        for candidate in (cut - distance, cut + distance):
            if lowest <= candidate <= highest and not _splits_okurigana(text, candidate):
                return candidate
    return cut


def _splits_okurigana(text: str, cut: int) -> bool:
    if not 0 < cut < len(text):
        return False
    return bool(_KANJI.match(text[cut - 1])) and bool(_HIRAGANA.match(text[cut]))


def _wrap(text: str, style: CaptionStyle) -> str:
    """1 つの字幕を行に分ける。どの行も `line_chars` を超えないようにする。"""
    text = text.strip()
    if len(text) <= style.line_chars or style.max_lines < 2:
        return text

    lines: List[str] = []
    rest = text
    while len(rest) > style.line_chars and len(lines) < style.max_lines - 1:
        # 残りの行に収まる範囲でだけ区切りを探す。ここを見ないと、手前に
        # 読点があるときに 1 行目が数文字・2 行目が上限超えになってしまう。
        remaining = style.max_lines - len(lines)
        lower = len(rest) - style.line_chars * (remaining - 1)
        # 区切りが無いときは、残りの行に均等に分ける(1 行が数文字だけになる
        # のを避ける)。
        cut = _break_point(rest, style.line_chars, lower, -(-len(rest) // remaining))
        lines.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        lines.append(rest)
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def srt_time(seconds: float) -> str:
    """SubRip の時刻(`00:00:01,234`)。"""
    return _clock(seconds, ",")


def vtt_time(seconds: float) -> str:
    """WebVTT の時刻(`00:00:01.234`)。"""
    return _clock(seconds, ".")


def _clock(seconds: float, separator: str) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}{separator}{milliseconds:03d}"


def format_srt(cues: Sequence[Cue]) -> str:
    blocks = [
        f"{number}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.text}\n"
        for number, cue in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def format_vtt(cues: Sequence[Cue]) -> str:
    blocks = ["WEBVTT\n"]
    blocks += [f"{vtt_time(cue.start)} --> {vtt_time(cue.end)}\n{cue.text}\n" for cue in cues]
    return "\n".join(blocks)


def write_captions(cues: Sequence[Cue], path: str, fmt: str = FORMAT_SRT) -> str:
    """字幕ファイルを書き出す。形式は拡張子ではなく `fmt` で決める。"""
    text = format_vtt(cues) if fmt == FORMAT_VTT else format_srt(cues)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # 改行は LF にそろえる(YouTube もプレイヤーも LF を読む)。
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# 章立て
# ---------------------------------------------------------------------------


def chapter_time(seconds: float) -> str:
    """YouTube の概要欄に書く時刻(`0:00` / `1:02:03`)。"""
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_chapters(
    slides: Sequence[SlideCaption],
    total_duration: float,
    min_duration: float = MIN_CHAPTER_SECONDS,
) -> List[Chapter]:
    """見出しが変わるところを章の切れ目にする。

    YouTube は「最初の章が 0:00 から」「1 つの章が 10 秒以上」「3 つ以上ある」を
    満たしたときだけ章立てとして扱う。前の 2 つはここで満たす(短い章は前の章に
    まとめる)。数が足りない場合はそのまま返し、呼び出し側に判断させる。
    """
    chapters: List[Chapter] = []
    for slide in sorted(slides, key=lambda s: s.index):
        title = slide.title.strip()
        if not title:
            continue  # 見出しの無いスライドは前の章の続きとして扱う
        if chapters and chapters[-1].title == title:
            continue
        chapters.append(Chapter(start=slide.start, title=title, slide=slide.index))

    if not chapters:
        return []
    if chapters[0].start > 0:
        chapters[0] = replace(chapters[0], start=0.0)
    return _merge_short_chapters(chapters, total_duration, min_duration)


def _merge_short_chapters(
    chapters: List[Chapter], total_duration: float, min_duration: float
) -> List[Chapter]:
    """短すぎる章を前の章にまとめる(YouTube が受け付けないため)。"""
    if min_duration <= 0:
        return chapters

    kept: List[Chapter] = []
    for position, chapter in enumerate(chapters):
        end = chapters[position + 1].start if position + 1 < len(chapters) else total_duration
        if kept and end - chapter.start < min_duration:
            continue  # 前の章に含める(見出しは前のものを残す)
        kept.append(chapter)

    # 最後の章が短いときは前の章に含める(先頭を消すと 0:00 が失われるため、
    # 残りが 1 つになったらそこで止める)。
    while len(kept) > 1 and total_duration - kept[-1].start < min_duration:
        kept.pop()
    return kept


def format_chapters(chapters: Sequence[Chapter]) -> str:
    """概要欄にそのまま貼れる形にする。"""
    return "".join(f"{chapter_time(c.start)} {c.title}\n" for c in chapters)


def write_chapters(chapters: Sequence[Chapter], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(format_chapters(chapters))
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# ナレーションの一覧(narration.json)から材料を読む
# ---------------------------------------------------------------------------

MANIFEST_NAME = "narration.json"


def load_captions(audio_dir: Optional[str]) -> Tuple[Dict[int, SlideCaption], List[str]]:
    """`narration.json` から、字幕にする材料を読む。

    返すのは(スライド番号 -> 材料, 伝えたいこと)。音声の一覧が無い場合(手で
    並べた素材など)は、何も言わずに空を返す。一覧はあるのに読み上げ単位が
    入っていない場合(古い形式で書き出したもの)は、書き出し直せば字幕を
    作れることを伝える。
    """
    path = os.path.join(audio_dir, MANIFEST_NAME) if audio_dir else ""
    if not path or not os.path.isfile(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}, [f"音声の一覧を読めなかったため、字幕を作っていません: {path}"]
    if not isinstance(data, dict):
        return {}, []

    captions: Dict[int, SlideCaption] = {}
    spoken = 0
    with_pieces = 0
    for entry in data.get("clips", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
            continue
        if str(entry.get("reading") or entry.get("text") or "").strip():
            spoken += 1
        pieces = _pieces_of(entry.get("pieces"))
        with_pieces += bool(pieces)
        # 読み上げる文章が無いスライドも入れる。字幕にはならないが、見出しは
        # 章の切れ目になりうる(図だけを見せる画面など)。
        captions[entry["index"]] = SlideCaption(
            index=entry["index"],
            duration=_number(entry.get("duration")),
            lead_silence=_number(entry.get("lead_silence")),
            tail_silence=_number(entry.get("tail_silence")),
            pieces=pieces,
            title=str(entry.get("title") or ""),
        )

    if spoken and not with_pieces:
        return {}, [
            f"音声の一覧に読み上げ単位が入っていないため、字幕を作っていません: {path}\n"
            "note2slides-audio で音声を書き出し直すと、字幕も作れるようになります。"
        ]
    return captions, []


def _pieces_of(raw: object) -> List[SpeechPiece]:
    if not isinstance(raw, list):
        return []
    pieces: List[SpeechPiece] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "")
        if text.strip():
            pieces.append(SpeechPiece(text=text, pause_after=_number(entry.get("pause_after"))))
    return pieces


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
