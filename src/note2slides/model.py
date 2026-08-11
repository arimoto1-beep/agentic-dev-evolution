"""記事とスライドの中間表現。

Markdown の解析結果(ブロック)と、スライド構成結果(スライド)をこのモジュールの
データ構造で表現する。解析・構成・描画の各段階はこの表現だけを介して連携する。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional

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
        if self.notes:
            data["notes"] = self.notes
        return data


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
