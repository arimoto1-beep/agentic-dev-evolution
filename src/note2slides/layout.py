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
from .model import (
    DIAGRAM_BOUNDARY,
    DIAGRAM_FRAME,
    DIAGRAM_LANES,
    DIAGRAM_STEPS,
    KIND_CODE,
    KIND_DIAGRAM,
    KIND_IMAGE,
    KIND_TABLE,
    Bullet,
    Content,
    boundary_parts,
    lane_parts,
    step_parts,
)
from .style import Style, inches_to_pt

#: 表の 1 行の高さと、上下の余白(inch)。
TABLE_ROW_HEIGHT = 0.45
TABLE_PADDING = 0.2

#: コードの枠の上下余白と、最小の高さ(inch)。
CODE_PADDING = 0.4
CODE_MIN_HEIGHT = 0.8

#: 図が使える場所が無くなっても、これだけは残す(画面の高さに対する割合)。
#: 図が点のように潰れるくらいなら、文章側があふれて警告になるほうがよい。
MIN_FIGURE_SHARE = 0.25

#: 大きくできる中身が無いとき、余った場所のうち何割を上に空けるか。
#: 0.5 で、中身のかたまりが本文の範囲の縦中央に来る(下には後述の 1 行ぶんを残す)。
CENTER_SHARE = 0.5

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
    if content.kind == KIND_DIAGRAM:
        return diagram_height(content, style, width)
    return bullets_height(content.bullets, style, inches_to_pt(width)) / 72.0


@dataclass(frozen=True)
class DiagramGeometry:
    """図解の並べ方と大きさ。

    見積り(`layout`)と実際の描画(`renderer`)がずれると、図が下の中身に
    重なったり、場所が余ったまま小さく描かれたりする。しかもその食い違いは
    画像を目で見るまで分からない(gen27 / gen28)。そのため **並べ方も大きさも
    ここだけで決め**、両方が同じ答えを使う。
    """

    horizontal: bool  # 流れを左から右へ並べるか(縦に積むか)
    item_width: float  # 項目の箱 1 つの幅(inch)
    item_height: float  # 項目の箱 1 つの高さ(inch)
    gap: float  # 項目と項目の間(inch。流れなら矢印の置き場所)
    padding: float  # 枠図の外枠と中身の間(inch。流れ図では 0)
    width: float  # 図解全体の幅(inch)
    height: float  # 図解全体の高さ(inch)
    font_pt: float  # 項目の文字の大きさ(pt)
    scale: float  # 基準の大きさに対する倍率
    band: float = 0.0  # 境界図で、線をまたぐものを置く帯の高さ(inch)
    header: float = 0.0  # レーン図で、レーン名の帯の高さ(inch)
    gutter: float = 0.0  # レーン図で、左右のレーンのあいだ(inch)
    ret_band: float = 0.0  # レーン図で、戻りの矢印と札を置く帯の幅(inch。無ければ 0)
    ret_left: bool = True  # 戻りの帯を左に置くか(戻り先のレーンの側に置く)
    offset: float = 0.0  # 階段図で、1 段ごとに右へずらす幅(inch)
    badge: float = 0.0  # 階段図で、段の名前の札の幅(inch)
    reach_band: float = 0.0  # 階段図で、到達点を置く帯の幅(inch。無ければ 0)


def _max_diagram_scale(style: Style) -> float:
    """図解を大きくしてよい上限(倍率)。

    文字がスライドの見出しより大きくならないところで止める。図の中の語が、
    その画面の題より目立つことにならないようにするため。
    """
    return max(1.0, style.slide_title_size / style.diagram_size)


def diagram_geometry(
    content: Content,
    style: Style,
    width: float,
    height: Optional[float] = None,
) -> DiagramGeometry:
    """図解の並べ方と大きさを決める。

    * **流れは、横に並べて収まるなら横に並べる。** 「A から B へ」は左から右へ
      読むほうが自然で、縦に積むと画面の左右が大きく空く。収まらない長さの
      ときだけ縦に積む(項目の文字を折り返してまで横には並べない)。
    * **渡された場所に合わせて拡大・縮小する。** これまでは中身の文字の長さ
      だけで大きさが決まり、場所が余っていても小さいままだった。図解は絵なので、
      `![](図.png)` と同じく、渡された場所を使う。

    `height` を渡さない場合は「本来必要な大きさ」(拡大も縮小もしない)を返す。
    `layout` が場所を配るときに使うのがこちら。
    """
    items = diagram_items(content)
    count = len(items)
    if count == 0:
        return DiagramGeometry(False, 0, 0, 0, 0, 0, 0, style.diagram_size, 1.0)

    frame = content.diagram_shape == DIAGRAM_FRAME
    item_h = style.diagram_item_height
    pad = style.diagram_frame_padding if frame else 0.0

    if content.diagram_shape == DIAGRAM_BOUNDARY:
        return _boundary_geometry(content, style, width, height)
    if content.diagram_shape == DIAGRAM_LANES:
        return _lanes_geometry(content, style, width, height)
    if content.diagram_shape == DIAGRAM_STEPS:
        return _steps_geometry(content, style, width, height)

    horizontal = False
    if not frame and count > 1:
        # 横に並べる場合、箱の幅は下限を置かない。下限は「1 つの箱が画面の
        # 真ん中にぽつんと細く立つ」のを避けるためのもので、横一列なら
        # 並び全体が広いので細くても間が抜けない。
        row_item = _item_width(items, style, style.diagram_size, minimum=0.0)
        row_width = count * row_item + (count - 1) * style.diagram_arrow_width
        if row_width <= width:
            horizontal = True
            item_w = row_item
            natural_w, natural_h = row_width, item_h
            gap = style.diagram_arrow_width

    if not horizontal:
        item_w = _item_width(items, style, style.diagram_size)
        gap = style.diagram_item_gap if frame else style.diagram_arrow_height
        natural_w = item_w + 2 * pad
        natural_h = count * item_h + (count - 1) * gap + 2 * pad

    scale = 1.0
    if height is not None and natural_w > 0 and natural_h > 0:
        scale = min(width / natural_w, height / natural_h, _max_diagram_scale(style))
    return DiagramGeometry(
        horizontal=horizontal,
        item_width=item_w * scale,
        item_height=item_h * scale,
        gap=gap * scale,
        padding=pad * scale,
        width=natural_w * scale,
        height=natural_h * scale,
        font_pt=style.diagram_size * scale,
        scale=scale,
    )


def _boundary_geometry(
    content: Content, style: Style, width: float, height: Optional[float]
) -> DiagramGeometry:
    """境界図の並べ方と大きさ。

    上の箱、線をまたぐものの帯、下の箱を縦に積む。横に並べる形は無い
    (線を挟んで上下に置くこと自体が図の意味なので、向きは選べない)。

    幅は、箱に入る文字と、帯に並ぶ札の両方から決める。札は線の上に横並びで
    載るので、札が長いと図全体を広げないと重なる。
    """
    parts = boundary_parts(content.diagram_items)
    upper = [t for t in parts.upper if t.strip()]
    lower = [t for t in parts.lower if t.strip()]
    boxes = upper + lower
    gaps = max(0, len(upper) - 1) + max(0, len(lower) - 1)
    item_h = style.diagram_item_height
    gap = style.diagram_item_gap
    band = style.diagram_boundary_band

    item_w = _item_width(boxes, style, style.diagram_size)
    crossings = len(parts.crossings)
    if crossings:
        # 札 1 つに要る幅(矢印 + 文字 + 左右の余白)を、並ぶ数だけ横に取る。
        widest = max((metrics.text_width_em(c.label) for c in parts.crossings), default=0.0)
        label_w = widest * style.diagram_size * style.diagram_crossing_ratio / 72.0
        need = crossings * (style.diagram_crossing_arrow + label_w + style.diagram_item_gap * 3)
        item_w = min(style.diagram_max_width, max(item_w, need))

    # 線は箱より左右へはみ出す。図全体の幅にはその分を含める。
    natural_w = item_w + 2 * style.diagram_boundary_overhang
    natural_h = len(boxes) * item_h + gaps * gap + band

    scale = 1.0
    if height is not None and natural_w > 0 and natural_h > 0:
        scale = min(width / natural_w, height / natural_h, _max_diagram_scale(style))
    return DiagramGeometry(
        horizontal=False,
        item_width=item_w * scale,
        item_height=item_h * scale,
        gap=gap * scale,
        padding=0.0,
        width=natural_w * scale,
        height=natural_h * scale,
        font_pt=style.diagram_size * scale,
        scale=scale,
        band=band * scale,
    )


def _lanes_geometry(
    content: Content, style: Style, width: float, height: Optional[float]
) -> DiagramGeometry:
    """レーン図の並べ方と大きさ。

    左右に 2 列、上から下へ 1 手順 1 行。担当が変わるところで矢印が縦線を
    またぐので、**行は必ず 1 手順ずつ** 使う(同じレーンの手順をまとめて
    1 行に詰めると、またぐ位置が決まらない)。

    幅は「戻りの帯 + 列 + 列のあいだ + 列」。戻りが無ければ帯は取らない。
    """
    parts = lane_parts(content.diagram_items)
    steps = parts.steps
    if not steps:
        return DiagramGeometry(False, 0, 0, 0, 0, 0, 0, style.diagram_size, 1.0)

    item_h = style.diagram_item_height
    gap = style.diagram_lane_gap
    header = style.diagram_lane_header
    gutter = style.diagram_lane_gutter
    # 列の幅は、手順の文字とレーン名の両方が入る幅。レーン名は帯に出るので、
    # 名前のほうが長ければそちらで決まる。
    item_w = _item_width([s.text for s in steps] + parts.lanes, style, style.diagram_size)
    ret_band = _return_band(parts, style)

    natural_w = ret_band + item_w * 2 + gutter
    natural_h = header + len(steps) * item_h + max(0, len(steps) - 1) * gap

    scale = 1.0
    if height is not None and natural_w > 0 and natural_h > 0:
        scale = min(width / natural_w, height / natural_h, _max_diagram_scale(style))
    return DiagramGeometry(
        horizontal=False,
        item_width=item_w * scale,
        item_height=item_h * scale,
        gap=gap * scale,
        padding=0.0,
        width=natural_w * scale,
        height=natural_h * scale,
        font_pt=style.diagram_size * scale,
        scale=scale,
        header=header * scale,
        gutter=gutter * scale,
        ret_band=ret_band * scale,
        ret_left=_return_goes_left(parts),
    )


def _steps_geometry(
    content: Content, style: Style, width: float, height: Optional[float]
) -> DiagramGeometry:
    """階段図の並べ方と大きさ。

    段は上から下へ 1 段ずつ、**右へずらしながら** 積む。ずらす幅が
    「深くなっていくこと」そのものなので、向きは選べない(縦に積むだけの
    形は流れ図と同じもので、深さが見えない)。

    幅は「段の札 + 段の文字 + ずらした分 + 到達点の帯」。到達点が無ければ
    帯は取らず、段だけで場所いっぱいに描く。
    """
    parts = step_parts(content.diagram_items)
    levels = parts.levels
    if not levels:
        return DiagramGeometry(False, 0, 0, 0, 0, 0, 0, style.diagram_size, 1.0)

    item_h = style.diagram_item_height
    gap = style.diagram_step_gap
    offset = style.diagram_step_offset
    badge = _badge_width([lv.name for lv in levels], style)
    # 段の箱は「札 + 中身」。中身の幅は、いちばん長い段の文字で決まる。
    item_w = badge + _item_width([lv.text for lv in levels], style, style.diagram_size)
    reach_band = _reach_band(parts, style)

    stair_w = item_w + offset * max(0, len(levels) - 1)
    reach_gap = style.diagram_step_reach_gap if parts.reaches else 0.0
    natural_w = stair_w + reach_gap + reach_band
    natural_h = len(levels) * item_h + max(0, len(levels) - 1) * gap

    scale = 1.0
    if height is not None and natural_w > 0 and natural_h > 0:
        scale = min(width / natural_w, height / natural_h, _max_diagram_scale(style))
    return DiagramGeometry(
        horizontal=False,
        item_width=item_w * scale,
        item_height=item_h * scale,
        gap=gap * scale,
        padding=0.0,
        width=natural_w * scale,
        height=natural_h * scale,
        font_pt=style.diagram_size * scale,
        scale=scale,
        offset=offset * scale,
        badge=badge * scale,
        reach_band=reach_band * scale,
    )


def _badge_width(names: List[str], style: Style) -> float:
    """階段図の、段の名前の札の幅(inch)。長い名前が入るときだけ広げる。"""
    widest = max((metrics.text_width_em(n) for n in names), default=0.0)
    need = widest * style.diagram_size / 72.0 + style.diagram_item_padding
    return max(style.diagram_step_badge, need)


def _reach_band(parts, style: Style) -> float:
    """階段図で、到達点の札と矢印を置く帯の幅(inch)。到達点が無ければ 0。

    レーン図の戻りの札と同じ考え方で、およそ 2 行に収まる幅を取る。
    """
    if not parts.reaches:
        return 0.0
    widest = max((metrics.text_width_em(r.label) for r in parts.reaches), default=0.0)
    line = widest / 2 * LABEL_SLACK * style.diagram_size * style.diagram_crossing_ratio / 72.0
    band = line / style.diagram_step_reach_ratio
    return min(style.diagram_step_reach_max, max(style.diagram_step_reach_min, band))


#: 戻りの札を 2 行に収めるために、幅の見積りへ足す余裕。
LABEL_SLACK = 1.15


def _return_band(parts, style: Style) -> float:
    """戻りの札と矢印を置く帯の幅(inch)。戻りが無ければ 0。

    札がおよそ 2 行に収まる幅を取る。狭いと語の途中で改行が入り
    (「決め / られない」)、広いと左が空いたまま図全体が縮む。
    """
    if not parts.returns:
        return 0.0
    widest = max((metrics.text_width_em(r.label) for r in parts.returns), default=0.0)
    # 半分ちょうどに合わせると、実際の字幅がこの見積りより少しでも広い場合に
    # 3 行目へこぼれ、最後の 1 文字だけが残る。少し余らせて 2 行に収める。
    line = widest / 2 * LABEL_SLACK * style.diagram_size * style.diagram_crossing_ratio / 72.0
    band = line / style.diagram_lane_label_ratio
    return min(style.diagram_lane_return_max, max(style.diagram_lane_return_min, band))


def _return_goes_left(parts) -> bool:
    """戻りの帯を左に置くか。**戻り先のレーンの側** に置く。

    戻り先を挟んで反対側に帯があると、矢印が図全体を横切って手順の箱に
    重なる。帯を戻り先の外側に出せば、箱の上を通らずに済む。
    """
    if not parts.returns or len(parts.lanes) < 2:
        return True
    first = parts.steps[0].lane if parts.steps else ""
    for ret in parts.returns:
        if 0 <= ret.after < len(parts.steps):
            source = parts.steps[ret.after].lane
            target = parts.lanes[1] if source == parts.lanes[0] else parts.lanes[0]
            return target == parts.lanes[0]
    return first == parts.lanes[0]


def diagram_items(content: Content) -> List[str]:
    """図解の項目のうち、実際に箱として描かれるもの(空行は数えない)。

    境界図では、線をまたぐものは箱ではなく矢印と札になるので、ここには入らない。
    レーン図の戻りも同じ(矢印と札で、箱ではない)。
    """
    items = [text for text in content.diagram_items if text.strip()]
    if content.diagram_shape == DIAGRAM_BOUNDARY:
        parts = boundary_parts(items)
        return parts.upper + parts.lower
    if content.diagram_shape == DIAGRAM_LANES:
        return [step.text for step in lane_parts(items).steps]
    if content.diagram_shape == DIAGRAM_STEPS:
        return [level.text for level in step_parts(items).levels]
    return items


def _item_width(
    items: List[str], style: Style, font_pt: float, minimum: Optional[float] = None
) -> float:
    """項目の箱の幅(inch)。いちばん長い項目に合わせ、全部を同じ幅にする。"""
    widest = max((metrics.text_width_em(t) for t in items), default=0.0)
    width = widest * font_pt / 72.0 + 2 * style.diagram_item_padding
    low = style.diagram_min_width if minimum is None else minimum
    return min(style.diagram_max_width, max(low, width))


def diagram_height(content: Content, style: Style, width: float) -> float:
    """図解が本来必要とする高さ(inch)。描画側と同じ数え方をする。"""
    return diagram_geometry(content, style, width).height


def usable_height(content: Content, style: Style, width: float) -> float:
    """その中身が **実際に使える** 高さの上限(inch)。

    高さを渡せば渡すだけ大きくなる中身は無い。図解は幅と
    `_max_diagram_scale` で頭打ちになり(横並びの流れは特に早く止まる)、
    図は幅いっぱいまでしか大きくならない。上限を超えて渡した高さは、
    **図と次の中身のあいだの空白** になって現れる。
    """
    if content.kind == KIND_DIAGRAM:
        geometry = diagram_geometry(content, style, width)
        if geometry.height <= 0:
            return 0.0
        # 高さを無制限に渡したときの倍率(幅と最大倍率だけで決まる)。
        natural_w, natural_h = geometry.width, geometry.height
        return natural_h * min(width / natural_w if natural_w else 1.0, _max_diagram_scale(style))
    if content.kind == KIND_IMAGE:
        return image_height(content, width)
    return float("inf")


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


def _extra_line_height(content: Content, style: Style) -> float:
    """その中身の「もう 1 行ぶん」の高さ(inch)。

    文章だけが折り返しの見積りに頼っている。表・コード・図は書かれた大きさで
    描かれるので、余裕を渡しても使い道が無い(0 を返す)。
    """
    if content.kind in (KIND_IMAGE, KIND_TABLE, KIND_CODE, KIND_DIAGRAM):
        return 0.0
    levels = [b.level for b in content.bullets]
    if not levels:
        return 0.0
    return style.line_height_pt(style.body_size(max(levels))) / 72.0


def fit(contents: List[Content], style: Style, box: Box) -> Layout:
    """中身を上から順に縦へ並べ、それぞれの置き場所を決める。"""
    if not contents:
        return Layout()

    gaps = style.part_gap * (len(contents) - 1)
    avail = max(0.0, box.height - gaps)
    natural = [content_height(c, style, box.width) for c in contents]
    # 譲れるのは図(`![](図.png)`)と図解(流れ・枠)。どちらも縦横比のまま
    # 小さく描けるので、renderer 側も box に合わせて縮める。文章・表・コードは
    # 縮めると読めなくなるため、譲らない。
    flexible = [c.kind in (KIND_IMAGE, KIND_DIAGRAM) for c in contents]

    fixed_total = sum(h for h, f in zip(natural, flexible) if not f)
    flex_total = sum(h for h, f in zip(natural, flexible) if f)

    # 図に残す場所。足りなければ縮めるが、最低限は残す。
    flex_used = min(flex_total, max(avail - fixed_total, avail * MIN_FIGURE_SHARE))
    heights = list(natural)
    if flex_total > flex_used:
        scale = flex_used / flex_total
        heights = [h * scale if f else h for h, f in zip(heights, flexible)]

    # 収まるかどうかは、縮められない中身(文章・表・コード)だけで判断する。
    # 図が最低限の場所を取るために全体が少し縮む分は、警告するほどではない。
    overflow = fixed_total / avail if avail > 0 else float("inf")
    needed = fixed_total + flex_used
    # 中身のかたまりを上から何 inch 下げて置くか(下記の「余りの渡し先」で決める)。
    lead = 0.0
    if needed > avail:
        # 入りきらない。小さく表示して収める(警告は呼び出し側が出す)。
        heights = [h * (avail / needed) for h in heights]
    elif heights:
        extra = avail - needed
        growable = [i for i, f in enumerate(flexible) if f]
        if growable:
            # 余った場所は、まず **大きく描ける中身**(図・図解)に渡す。図は絵で、
            # 場所が広ければそのぶん大きく描ける。小さく描いても伝わる内容は
            # 増えないので、場所が余っているのに小さいままなのは、ただの損。
            #
            # ただし全部は渡さない。最後が文章なら、折り返しが 1 行増えても
            # 収まるだけは残す。文章の高さは見積りなので、余裕を全部取り上げると
            # 実際が 1 行多かったときに文章が下端まで来て、ページ番号の帯と
            # 同じ高さに並ぶ(gen28 が直したのがこの見え方)。
            reserve = min(extra, _extra_line_height(contents[-1], style))
            share = extra - reserve
            # 渡しても使えない分は渡さない。使えない高さを渡すと、そのぶんが
            # 図と次の中身のあいだの空白になる(横並びの流れで目立つ)。
            for index in growable:
                room = max(0.0, usable_height(contents[index], style, box.width) - heights[index])
                take = min(room, share / len(growable))
                heights[index] += take
                share -= take
            heights[-1] += reserve
            # それでも余ったら、大きくできる中身が無かったときと同じに扱う。
            slack = max(0.0, share - _extra_line_height(contents[-1], style))
            lead = slack * CENTER_SHARE
        else:
            # 図が無ければ最後の中身に渡す。文章の高さは折り返しの見積りなので、
            # 見積りより実際が大きくても収まるようにしておく(下に何も無いので、
            # 広げても他の中身の位置は動かない。表・コードは自分の大きさで
            # 描かれるため、広い場所を渡しても大きくはならない)。
            heights[-1] += extra
            # そのうえで、かたまり全体を下げて縦中央に置く。文章・表・コードは
            # 広い箱をもらっても大きくならないので、上に貼り付いたままだと
            # **画面の下半分が丸ごと空く**。2 行しか書いていない画面が、
            # 見ている側には「作りかけ」に見える(動画では 20 秒それを見る)。
            #
            # 文字の大きさは変えない。書いたとおりの大きさで出すのは前提
            # (gen28)なので、動かすのは置く場所だけにする。
            #
            # 下げる前に 1 行ぶんを取り分ける。文章の高さは見積りなので、
            # 余りを全部使って中央に寄せると、実際が 1 行多かったときに
            # 下端まで来てページ番号の帯と並ぶ(gen28 が直した見え方)。
            slack = max(0.0, extra - _extra_line_height(contents[-1], style))
            lead = slack * CENTER_SHARE

    placed: List[Placed] = []
    top = box.top + lead
    for content, height in zip(contents, heights):
        placed.append(Placed(content, Box(box.left, top, box.width, height)))
        top += height + style.part_gap
    return Layout(parts=placed, overflow=overflow)
