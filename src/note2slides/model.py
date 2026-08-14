"""記事とスライドの中間表現。

Markdown の解析結果(ブロック)と、スライド構成結果(スライド)をこのモジュールの
データ構造で表現する。解析・構成・描画の各段階はこの表現だけを介して連携する。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# インライン
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """書式が一様なテキスト片。text は原文の文字列をそのまま保持する。"""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: Optional[str] = None

    def with_text(self, text: str) -> "Run":
        return replace(self, text=text)


def runs_to_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def plain_run(text: str) -> List[Run]:
    return [Run(text)]


# ---------------------------------------------------------------------------
# ブロック(Markdown 由来)
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    level: int
    runs: List[Run]


@dataclass
class Paragraph:
    runs: List[Run]
    quote: bool = False


@dataclass
class ListItem:
    level: int
    runs: List[Run]
    ordered: bool = False
    number: Optional[str] = None
    quote: bool = False


@dataclass
class ListBlock:
    items: List[ListItem]


@dataclass
class CodeBlock:
    text: str
    lang: str = ""


@dataclass
class Table:
    header: List[str]
    rows: List[List[str]]


@dataclass
class Image:
    src: str
    alt: str = ""


@dataclass
class SlideBreak:
    """`---` による明示的なスライド区切り。"""


Block = object  # 上記いずれかのブロック


@dataclass
class Article:
    """記事全体。meta は front matter の内容。"""

    blocks: List[Block] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    source_path: Optional[str] = None


# ---------------------------------------------------------------------------
# スライド(描画用)
# ---------------------------------------------------------------------------

KIND_TITLE = "title"
KIND_SECTION = "section"
KIND_BULLETS = "bullets"
KIND_CODE = "code"
KIND_TABLE = "table"
KIND_IMAGE = "image"

BULLET = "bullet"
NUMBER = "number"
QUOTE = "quote"


@dataclass
class Bullet:
    """本文スライドの 1 行。level は 0 起点のインデント段数。"""

    runs: List[Run]
    level: int = 0
    kind: str = BULLET
    number: Optional[str] = None

    @property
    def text(self) -> str:
        return runs_to_text(self.runs)


@dataclass
class Slide:
    kind: str
    title: str = ""
    subtitle: str = ""
    bullets: List[Bullet] = field(default_factory=list)
    code: str = ""
    code_lang: str = ""
    table_header: List[str] = field(default_factory=list)
    table_rows: List[List[str]] = field(default_factory=list)
    image_path: Optional[str] = None
    image_alt: str = ""
    notes: str = ""
    #: 直前のスライドから続いている表・コード(1 枚に収まらず分けたもの)。
    #: ナレーションで「表の続きです」と案内するために使う。
    continued: bool = False

    def to_dict(self) -> dict:
        """--dump-plan 用。スライド構成の確認・差分比較に使う。"""
        data = {"kind": self.kind, "title": self.title}
        if self.subtitle:
            data["subtitle"] = self.subtitle
        if self.bullets:
            data["bullets"] = [
                {"level": b.level, "kind": b.kind, "text": b.text} for b in self.bullets
            ]
        if self.code:
            data["code"] = self.code
            data["code_lang"] = self.code_lang
        if self.table_header or self.table_rows:
            data["table"] = {"header": self.table_header, "rows": self.table_rows}
        if self.image_path:
            data["image"] = {"path": self.image_path, "alt": self.image_alt}
        if self.continued:
            data["continued"] = True
        if self.notes:
            data["notes"] = self.notes
        return data


# ---------------------------------------------------------------------------
# 図形の名前(資料に残す目印)
# ---------------------------------------------------------------------------
#
# 表とコードは、画面には出ているが文字としては読み上げられない。ナレーション側
# (narration.py)が「何が映っているのか」を判断できるよう、描画側(renderer.py)は
# 図形の名前に種類を残す。名前は `種類[-continued][:言語]` の形にする。
#
#     code            コード
#     code:bash       bash のコード
#     table-continued 前のスライドから続いている表

SHAPE_CODE = "code"
SHAPE_TABLE = "table"
_SHAPE_CONTINUED = "-continued"


def shape_name(kind: str, continued: bool = False, language: str = "") -> str:
    """描画側が図形に付ける名前を組み立てる。"""
    name = kind + (_SHAPE_CONTINUED if continued else "")
    language = (language or "").strip()
    return f"{name}:{language}" if language else name


def parse_shape_name(name: Optional[str]) -> Tuple[str, bool, str]:
    """図形の名前から「種類・続きかどうか・言語」を読み取る。"""
    base, _, language = (name or "").strip().lower().partition(":")
    continued = base.endswith(_SHAPE_CONTINUED)
    if continued:
        base = base[: -len(_SHAPE_CONTINUED)]
    return base, continued, language


@dataclass
class Deck:
    slides: List[Slide] = field(default_factory=list)
    title: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "slides": [s.to_dict() for s in self.slides],
            "warnings": self.warnings,
        }
