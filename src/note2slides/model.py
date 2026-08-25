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
#: 1 枚に複数の中身を縦に並べた画面(文章と図、表と補足文など)。中身は
#: `Slide.parts` に書かれた順で入る。教材シナリオだけが作る(記事入力は
#: 1 枚 = 1 つの中身のまま)。
KIND_CONTENT = "content"
#: 図解(流れ・枠)。ASCII アートではなく図形として描く。教材の図は
#: 「順に進む」か「何かの中に入っている」かのどちらかで書けることが多いので、
#: その 2 つだけを持つ。中身は `Content.diagram_items` に書かれた順で入る。
KIND_DIAGRAM = "diagram"
#: 動画の外側で使う 1 枚絵(YouTube のサムネイルなど)。資料には入れず、
#: `thumbnail.py` が単独の .pptx を作るときだけ使う。表紙より文字を大きくし、
#: 小さく表示されても題が読めるようにする。
KIND_THUMBNAIL = "thumbnail"

#: 上から下へ、矢印でつなぐ図(工程・流れ)。
DIAGRAM_FLOW = "flow"
#: 枠の中に並べる図(何が含まれているか)。
DIAGRAM_FRAME = "frame"
DIAGRAM_SHAPES = (DIAGRAM_FLOW, DIAGRAM_FRAME)
#: コードブロックの言語名として書く、図解の指定。日本語と英語のどちらでも書ける。
DIAGRAM_LANGS = {
    "流れ": DIAGRAM_FLOW,
    "flow": DIAGRAM_FLOW,
    "枠": DIAGRAM_FRAME,
    "frame": DIAGRAM_FRAME,
}


def diagram_shape_of(lang: str) -> Optional[str]:
    """コードブロックの言語名が図解の指定なら、その形を返す。"""
    return DIAGRAM_LANGS.get((lang or "").strip().lower())

BULLET = "bullet"
NUMBER = "number"
QUOTE = "quote"
#: 記号を付けずに置く行。教材シナリオで、箇条書きではなく説明の文として
#: 書かれた段落に使う(記事入力では、段落も文ごとの箇条書きになる)。
PLAIN = "plain"


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
class Content:
    """画面に出す 1 つの中身。kind で、どのフィールドを使うかが決まる。

    1 枚に 1 つだけ置く場合は `Slide` がそのまま中身になり、複数を並べる場合は
    `Slide.parts` に並ぶ(`Slide` はこの `Content` に見出しとナレーションを
    足したもの)。描画側は中身の種類ごとに描くだけなので、1 枚に 1 つでも
    複数でも同じ処理で扱える。
    """

    kind: str
    bullets: List[Bullet] = field(default_factory=list)
    code: str = ""
    code_lang: str = ""
    table_header: List[str] = field(default_factory=list)
    table_rows: List[List[str]] = field(default_factory=list)
    image_path: Optional[str] = None
    image_alt: str = ""
    diagram_shape: str = ""
    diagram_items: List[str] = field(default_factory=list)

    def content_dict(self) -> dict:
        """中身のフィールドだけを辞書にする(種類・見出しは含めない)。"""
        data: dict = {}
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
        if self.diagram_items:
            data["diagram"] = {"shape": self.diagram_shape, "items": list(self.diagram_items)}
        return data

    def to_dict(self) -> dict:
        return {"kind": self.kind, **self.content_dict()}

    def as_slide(self, title: str = "", notes: str = "") -> "Slide":
        """この中身だけを置いた 1 枚にする。"""
        return Slide(
            kind=self.kind,
            title=title,
            bullets=self.bullets,
            code=self.code,
            code_lang=self.code_lang,
            table_header=self.table_header,
            table_rows=self.table_rows,
            image_path=self.image_path,
            image_alt=self.image_alt,
            diagram_shape=self.diagram_shape,
            diagram_items=list(self.diagram_items),
            notes=notes,
        )


@dataclass
class Slide(Content):
    title: str = ""
    subtitle: str = ""
    #: 左上に小さく出す短い文字(教材名・回数など)。サムネイルだけが使う。
    label: str = ""
    notes: str = ""
    #: 1 枚に並べる中身(`KIND_CONTENT` のときだけ入る)。書かれた順に縦へ並ぶ。
    parts: List[Content] = field(default_factory=list)
    #: 直前のスライドから続いている表・コード(1 枚に収まらず分けたもの)。
    #: ナレーションで「表の続きです」と案内するために使う。
    continued: bool = False

    def to_dict(self) -> dict:
        """--dump-plan 用。スライド構成の確認・差分比較に使う。"""
        data = {"kind": self.kind, "title": self.title}
        if self.subtitle:
            data["subtitle"] = self.subtitle
        if self.label:
            data["label"] = self.label
        data.update(self.content_dict())
        if self.parts:
            data["parts"] = [part.to_dict() for part in self.parts]
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
#     footer          資料名・ページ番号(読み上げない飾り)

SHAPE_CODE = "code"
SHAPE_TABLE = "table"
#: 図解の外枠。図解は複数の図形でできているので、案内文を二重に出さないよう、
#: この名前を付けるのは外枠の 1 つだけにする(中の図形は SHAPE_DIAGRAM_ITEM)。
SHAPE_DIAGRAM = "diagram"
SHAPE_DIAGRAM_ITEM = "diagram-item"
#: 見た目のために置く文字(資料名・ページ番号)。画面には出るが、内容ではない
#: ので読み上げない。ナレーション側(narration.py)がこの名前で除外する。
SHAPE_FOOTER = "footer"
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
