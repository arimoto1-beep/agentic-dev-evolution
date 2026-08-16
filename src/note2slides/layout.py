"""1 枚の画面の中で、それぞれの中身が占める場所を決める。

教材シナリオでは、1 つの画面に文章と図、表と補足の文のように、種類の違う
中身を並べることがある。ここでは中身を **書かれた順に縦へ並べ**、それぞれが
使える高さを決める。左右に分けたり順番を入れ替えたりはしない(シナリオに
書いた順が、そのまま画面の上から下になる)。

描画(`renderer`)と、収まるかどうかの警告(`scenario`)が同じ見積りを使えるよう、
高さの計算はこのモジュールに集める。見積りは厳密な描画結果ではなく、あふれを
避けるための目安である(`metrics` と同じ考え方)。

場所が足りない場合は、まず **図が譲る**。図は縮めても縦横比のまま小さく映る
だけだが、文章・表・コードは縮めると読めなくなるためである。図を縮めても
入りきらない場合だけ「収まらない」(`Layout.overflow` が 1 を超える)とし、
呼び出し側が警告する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import metrics
from .model import KIND_CODE, KIND_IMAGE, KIND_TABLE, Bullet, Content
from .style import Style, inches_to_pt

#: 表の 1 行の高さと、上下の余白(inch)。
TABLE_ROW_HEIGHT = 0.45
TABLE_PADDING = 0.2

#: コードの枠の上下余白と、最小の高さ(inch)。
CODE_PADDING = 0.4
CODE_MIN_HEIGHT = 0.8

#: 図が使える場所が無くなっても、これだけは残す(画面の高さに対する割合)。
#: 図が点のように潰れるくらいなら、文章側があふれて警告になるほうがよい。
MIN_IMAGE_SHARE = 0.25

#: 図の縦横比を読み取れなかった場合に使う比(高さ / 幅)。
FALLBACK_IMAGE_RATIO = 0.75


@dataclass(frozen=True)
class Box:
    """スライド上の矩形(inch)。"""

    left: float
    top: float
    width: float
    height: float

    @property
    def width_pt(self) -> float:
        return inches_to_pt(self.width)

    @property
    def height_pt(self) -> float:
        return inches_to_pt(self.height)


@dataclass
class Placed:
    content: Content
    box: Box


@dataclass
class Layout:
    """並べ終えた結果。`overflow` は必要な高さ / 使える高さ。"""

    parts: List[Placed] = field(default_factory=list)
    overflow: float = 0.0

    @property
    def fits(self) -> bool:
        return self.overflow <= 1.0


def body_box(style: Style) -> Box:
    """タイトルの下、本文に使える範囲。"""
    return Box(style.body_left, style.body_top, style.body_width, style.body_height)


# ---------------------------------------------------------------------------
# 高さの見積り
# ---------------------------------------------------------------------------


def bullet_height(
    bullet: Bullet, style: Style, first: bool = True, width_pt: Optional[float] = None
) -> float:
    """本文 1 行が占める高さ(pt)の見積り。"""
    size = style.body_size(bullet.level)
    avail = (style.body_width_pt if width_pt is None else width_pt) - style.bullet_indent_pt(
        bullet.level
    )
    lines = metrics.line_count(bullet.text, size, avail)
    height = lines * style.line_height_pt(size)
    if not first:
        height += style.space_before_pt
    return height


def bullets_height(
    bullets: List[Bullet], style: Style, width_pt: Optional[float] = None
) -> float:
    """本文全体が占める高さ(pt)の見積り。`Style.body_height_pt` と比べて使う。"""
    return sum(
        bullet_height(b, style, first=i == 0, width_pt=width_pt) for i, b in enumerate(bullets)
    )


def content_height(content: Content, style: Style, width: float) -> float:
    """中身が本来必要とする高さ(inch)。幅は使える横幅(inch)。"""
    if content.kind == KIND_IMAGE:
        return image_height(content, width)
    if content.kind == KIND_TABLE:
        rows = (1 if content.table_header else 0) + len(content.table_rows)
        return TABLE_ROW_HEIGHT * rows + TABLE_PADDING if rows else 0.0
    if content.kind == KIND_CODE:
        lines = content.code.split("\n")
        height = len(lines) * style.line_height_pt(style.code_size, style.code_line_spacing) / 72.0
        return max(CODE_MIN_HEIGHT, height + CODE_PADDING)
    return bullets_height(content.bullets, style, inches_to_pt(width)) / 72.0


def image_height(content: Content, width: float) -> float:
    """幅いっぱいに置いた図の高さ(inch)。説明を出す場合はそのぶんも足す。"""
    return width * image_ratio(content.image_path) + (
        caption_height() if content.image_alt else 0.0
    )


def caption_height() -> float:
    """図の下に出す説明(キャプション)の高さ(inch)。"""
    return 0.45


def image_ratio(path: Optional[str]) -> float:
    """図の縦横比(高さ / 幅)。読み取れない場合は目安の比を返す。"""
    if not path:
        return FALLBACK_IMAGE_RATIO
    from pptx.exc import PythonPptxError
    from pptx.parts.image import Image as PptxImage

    try:
        pixels_wide, pixels_high = PptxImage.from_file(path).size
    except (PythonPptxError, OSError, ValueError):
        # 壊れた画像・未対応の形式でも、並べ方の見積りはここで止めない
        # (描画のときに、あらためて同じファイルを読んで知らせる)。
        return FALLBACK_IMAGE_RATIO
    if not pixels_wide or not pixels_high:
        return FALLBACK_IMAGE_RATIO
    return pixels_high / pixels_wide


# ---------------------------------------------------------------------------
# 並べる
# ---------------------------------------------------------------------------


def fit(contents: List[Content], style: Style, box: Box) -> Layout:
    """中身を上から順に縦へ並べ、それぞれの置き場所を決める。"""
    if not contents:
        return Layout()

    gaps = style.part_gap * (len(contents) - 1)
    avail = max(0.0, box.height - gaps)
    natural = [content_height(c, style, box.width) for c in contents]
    flexible = [c.kind == KIND_IMAGE for c in contents]

    fixed_total = sum(h for h, f in zip(natural, flexible) if not f)
    flex_total = sum(h for h, f in zip(natural, flexible) if f)

    # 図に残す場所。足りなければ縮めるが、最低限は残す。
    flex_used = min(flex_total, max(avail - fixed_total, avail * MIN_IMAGE_SHARE))
    heights = list(natural)
    if flex_total > flex_used:
        scale = flex_used / flex_total
        heights = [h * scale if f else h for h, f in zip(heights, flexible)]

    # 収まるかどうかは、縮められない中身(文章・表・コード)だけで判断する。
    # 図が最低限の場所を取るために全体が少し縮む分は、警告するほどではない。
    overflow = fixed_total / avail if avail > 0 else float("inf")
    needed = fixed_total + flex_used
    if needed > avail:
        # 入りきらない。小さく表示して収める(警告は呼び出し側が出す)。
        heights = [h * (avail / needed) for h in heights]
    elif heights:
        # 余った場所は最後の中身に渡す。文章の高さは折り返しの見積りなので、
        # 見積りより実際が大きくても収まるようにしておく(下に何も無いので、
        # 広げても他の中身の位置は動かない。図・表・コードは自分の大きさで
        # 描かれるため、広い場所を渡しても大きくはならない)。
        heights[-1] += avail - needed

    placed: List[Placed] = []
    top = box.top
    for content, height in zip(contents, heights):
        placed.append(Placed(content, Box(box.left, top, box.width, height)))
        top += height + style.part_gap
    return Layout(parts=placed, overflow=overflow)
