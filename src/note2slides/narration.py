"""プレゼンテーション資料からナレーション原稿を取り出す。

原稿は「スライド 1 枚 = セリフ 1 本」で、番号はスライド番号(1 起点)と一致する。
音声ファイルの連番もこの番号に合わせるため、スライドと音声の対応は番号だけで
追える。

文章は資料に書かれている文字をそのまま使う。要約・言い換え・補足の生成は
行わない(資料自体が記事本文をそのまま並べたものなので、原稿も記事の文言の
ままになる)。読み上げ元は次の優先順で選び、どれを使ったかを `source` に残す。

    notes  発表者ノート(note2slides が入れた元の本文)
    body   スライド上の本文(ノートが無い場合)
    title  スライドのタイトルだけ(本文が無い場合)
    none   読み上げる文字が無い(無音として扱う)

原稿は JSON として書き出し・読み込みできる。読みを直したい場合は書き出した
JSON を編集して、それを入力にして合成する。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List

#: 読み上げ元の種別。
SOURCE_NOTES = "notes"
SOURCE_BODY = "body"
SOURCE_TITLE = "title"
SOURCE_NONE = "none"

#: 読み上げ対象から外す図形の名前(接頭辞で判定する)。
#: renderer がコード用の図形に付ける名前。コードをそのまま読み上げても
#: 聞き取れないため、ノートが無ければ無音にする。
SKIP_SHAPE_PREFIXES = ("code",)

#: 1 枚に収まらないスライドのタイトルに planner が付ける目印。画面を見れば
#: 続きだと分かるので、読み上げからは外す(「かっこ つづき」と読まれてしまう)。
CONTINUATION_SUFFIX = "（続き）"

SCRIPT_SUFFIXES = (".json",)
PRESENTATION_SUFFIXES = (".pptx",)


class NarrationError(RuntimeError):
    """原稿の取り出し・読み込みに失敗した場合。"""


@dataclass
class NarrationSegment:
    """1 枚のスライドに対応するセリフ。index はスライド番号(1 起点)。"""

    index: int
    text: str = ""
    title: str = ""
    source: str = SOURCE_NONE

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "source": self.source,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict, fallback_index: int) -> "NarrationSegment":
        if not isinstance(data, dict):
            raise NarrationError(f"セリフの形式が正しくありません: {data!r}")
        index = data.get("index", fallback_index)
        if not isinstance(index, int) or index < 1:
            raise NarrationError(f"index は 1 以上の整数にしてください: {index!r}")
        text = data.get("text", "")
        if not isinstance(text, str):
            raise NarrationError(f"{index} 番のセリフの text が文字列ではありません")
        return cls(
            index=index,
            text=text,
            title=str(data.get("title", "")),
            # 手で書いた原稿にも対応するため、source は無ければ推定する。
            source=str(data.get("source") or (SOURCE_NOTES if text.strip() else SOURCE_NONE)),
        )


@dataclass
class NarrationScript:
    segments: List[NarrationSegment] = field(default_factory=list)
    source: str = ""  # 取り出し元の資料
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.segments)

    def to_dict(self) -> dict:
        return {
            "source": os.path.basename(self.source) if self.source else "",
            "count": self.count,
            "segments": [s.to_dict() for s in self.segments],
            "warnings": self.warnings,
        }

    def write(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")
        return os.path.abspath(path)


def read_script(path: str) -> NarrationScript:
    """書き出した原稿(JSON)を読み込む。手で直したものも入力にできる。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise NarrationError(f"原稿を開けませんでした: {path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise NarrationError(
            f"原稿が JSON として読めませんでした: {path}\n{exc.lineno} 行 {exc.colno} 文字目: {exc.msg}"
        ) from exc

    if isinstance(data, list):  # segments だけを書いた形も許す
        data = {"segments": data}
    if not isinstance(data, dict):
        raise NarrationError(f"原稿の形式が正しくありません: {path}")

    raw = data.get("segments")
    if not isinstance(raw, list) or not raw:
        raise NarrationError(f"原稿にセリフ(segments)がありません: {path}")

    segments = [NarrationSegment.from_dict(item, i) for i, item in enumerate(raw, start=1)]
    script = NarrationScript(
        segments=segments,
        source=str(data.get("source") or path),
        warnings=[str(w) for w in data.get("warnings", []) if isinstance(w, str)],
    )
    _check_indexes(script, path)
    return script


def _check_indexes(script: NarrationScript, path: str) -> None:
    """番号の重複と欠落を弾く。スライドとの対応が崩れたまま合成しないため。"""
    indexes = [s.index for s in script.segments]
    if sorted(indexes) != list(range(1, len(indexes) + 1)):
        raise NarrationError(
            f"原稿の index が 1 からの連番になっていません: {path}\n"
            f"  見つかった index: {sorted(indexes)}\n"
            "スライド番号と音声の番号を合わせるため、1 起点の通し番号にしてください。"
        )
    script.segments.sort(key=lambda s: s.index)


def extract_script(source: str) -> NarrationScript:
    """資料(.pptx)からナレーション原稿を取り出す。

    非表示スライドは画像化でも出力されないため、原稿からも外して警告する。
    """
    suffix = os.path.splitext(source)[1].lower()
    if suffix in SCRIPT_SUFFIXES:
        return read_script(source)
    if suffix not in PRESENTATION_SUFFIXES:
        known = " / ".join(PRESENTATION_SUFFIXES + SCRIPT_SUFFIXES)
        raise NarrationError(f"未対応の入力形式です: {suffix or source}(扱えるのは {known})")
    if not os.path.isfile(source):
        raise NarrationError(f"入力ファイルが見つかりません: {source}")

    from pptx import Presentation
    from pptx.exc import PythonPptxError

    try:
        presentation = Presentation(source)
        slides = list(presentation.slides)
    except (PythonPptxError, ValueError, KeyError, OSError) as exc:
        raise NarrationError(
            f"資料として読み取れませんでした: {source}\n{type(exc).__name__}: {exc}"
        ) from exc

    script = NarrationScript(source=os.path.abspath(source))
    hidden = 0
    number = 0
    for slide in slides:
        if slide.element.get("show") == "0":
            hidden += 1
            continue
        number += 1
        script.segments.append(_segment_of(slide, number))

    if not script.segments:
        raise NarrationError(f"スライドが 1 枚もありませんでした: {source}")
    if hidden:
        script.warnings.append(
            f"非表示のスライド {hidden} 枚は原稿から外しました(スライド画像にも出力されません)。"
        )
    silent = [s.index for s in script.segments if s.is_empty]
    if silent:
        script.warnings.append(
            "読み上げる文字が無いスライドがあります(無音になります): "
            + ", ".join(str(i) for i in silent)
        )
    return script


def _segment_of(slide, number: int) -> NarrationSegment:
    """1 枚分のセリフを決める。ノートが無い場合は画面に出ている文字を読む。"""
    title = _clean(_title_text(slide))
    spoken_title = _spoken_title(title)
    notes = _clean(_notes_text(slide))
    if notes:
        return NarrationSegment(number, text=notes, title=title, source=SOURCE_NOTES)

    # 表紙や章扉はタイトルだけが本文なので、タイトルも読み上げの対象にする。
    body = _clean("\n".join(_body_texts(slide)))
    if body:
        text = f"{spoken_title}\n{body}" if spoken_title else body
        return NarrationSegment(number, text=text, title=title, source=SOURCE_BODY)
    if spoken_title:
        return NarrationSegment(number, text=spoken_title, title=title, source=SOURCE_TITLE)
    return NarrationSegment(number, text="", title=title, source=SOURCE_NONE)


def _spoken_title(title: str) -> str:
    """読み上げ用のタイトル。レイアウト上の目印は落とす。"""
    if title.endswith(CONTINUATION_SUFFIX):
        return title[: -len(CONTINUATION_SUFFIX)].strip()
    return title


def _title_text(slide) -> str:
    try:
        title = slide.shapes.title
    except (AttributeError, ValueError):
        return ""
    return title.text if title is not None else ""


def _notes_text(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text if frame is not None else ""


def _body_texts(slide) -> List[str]:
    """タイトル以外の本文テキストを、スライド上の並び順で集める。

    表・画像は文字として読み上げられないため対象外。コード用の図形も、
    そのまま読み上げても聞き取れないので外す。
    """
    title = slide.shapes.title
    texts = []
    for shape in slide.shapes:
        if title is not None and shape.element is title.element:
            continue
        if _is_skipped(shape) or not getattr(shape, "has_text_frame", False):
            continue
        text = shape.text_frame.text
        if text.strip():
            texts.append(text)
    return texts


def _is_skipped(shape) -> bool:
    name = (getattr(shape, "name", "") or "").lower()
    return name.startswith(SKIP_SHAPE_PREFIXES)


def _clean(text: str) -> str:
    """読み上げ用に整える。文字の削除・追加はせず、空白と改行だけを整理する。

    * 縦タブ(pptx の行内改行)を改行にする
    * 行頭・行末の空白を落とし、空行を詰める
    """
    if not text:
        return ""
    normalized = text.replace("\v", "\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)
