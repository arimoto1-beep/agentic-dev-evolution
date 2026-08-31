"""スライドの寸法・書式設定。

16:9 (13.333in x 7.5in) を前提とした値を持つ。ここを変えるだけでレイアウトを
調整できるよう、位置やフォントサイズはすべてこのデータクラスに集約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

RGB = Tuple[int, int, int]

# 16:9 のスライドサイズ(EMU)。914400 EMU = 1 inch。
SLIDE_WIDTH_EMU = 12192000  # 13.333 in
SLIDE_HEIGHT_EMU = 6858000  # 7.5 in

EMU_PER_INCH = 914400
PT_PER_INCH = 72.0


def inches_to_pt(inches: float) -> float:
    return inches * PT_PER_INCH



@dataclass(frozen=True)
class Theme:
    """スライド全体の見た目(背景と、装飾に使う色)。

    文字の大きさや位置は `Style` が持ち、ここには「地の見え方」だけを置く。
    見た目を変えても、どこに何が入るかは変わらないようにするためである。

    画面の役割ごとに地の色を変え、動画を見たときに切り替わりが分かるようにする。

        表紙   濃い地に白い文字(動画の最初の 1 枚。区切りとして最も強い)
        章扉   うすい地に濃い文字(章の変わり目)
        本文   白い地(読むための画面。いちばん明るい)
    """

    name: str = "light"
    #: 本文スライドの地の色。
    background: RGB = (0xFF, 0xFF, 0xFF)
    #: 表紙の地・文字。
    cover_background: RGB = (0x14, 0x22, 0x3A)
    cover_title: RGB = (0xFF, 0xFF, 0xFF)
    cover_subtitle: RGB = (0xB3, 0xC1, 0xD6)
    cover_accent: RGB = (0x4C, 0x9A, 0xE0)
    #: 章扉の地・文字。
    section_background: RGB = (0xEE, 0xF3, 0xF9)
    section_title: RGB = (0x14, 0x22, 0x3A)
    #: 見出しの下に引く、うすい罫線(短い濃い罫線と組みで使う)。
    rule: RGB = (0xDA, 0xE0, 0xE8)
    #: 見出しの下の、短い濃い罫線の長さ(inch)。0 なら、うすい罫線を引かずに
    #: 見出しの幅いっぱいの濃い罫線にする(従来の見た目)。
    accent_rule_width: float = 1.5
    #: 下端に出す、資料名とページ番号。
    footer: bool = True
    footer_color: RGB = (0x93, 0x9D, 0xAB)
    footer_size: float = 11.0
    footer_top: float = 6.82
    #: 表紙・章扉の地を塗るか。False なら本文と同じ地になる(従来の見た目)。
    filled_cover: bool = False
    #: 表紙の題の下端(inch)。罫線と副題はこの下に続く。地を塗る見た目では
    #: 下端に帯が入るぶん、かたまりを少し上に置く。
    cover_title_bottom: float = 3.55


#: 名前で選べる見た目。`plain` は Run 022 までの見た目(白地・装飾なし)。
THEMES: Dict[str, Theme] = {
    "light": Theme(name="light", filled_cover=True),
    "plain": Theme(
        name="plain",
        cover_background=(0xFF, 0xFF, 0xFF),
        cover_title=(0x1F, 0x24, 0x2E),
        cover_subtitle=(0x6B, 0x72, 0x80),
        cover_accent=(0x1B, 0x6F, 0xB8),
        cover_title_bottom=3.8,
        section_background=(0xFF, 0xFF, 0xFF),
        rule=(0xFF, 0xFF, 0xFF),
        accent_rule_width=0.0,
        footer=False,
        filled_cover=False,
    ),
}

DEFAULT_THEME = "light"


def theme_names() -> Tuple[str, ...]:
    return tuple(THEMES)


def get_theme(name: Optional[str]) -> Theme:
    """名前から見た目を選ぶ。知らない名前は既定に落とさず、その場で知らせる。"""
    if not name:
        return THEMES[DEFAULT_THEME]
    try:
        return THEMES[name]
    except KeyError:
        known = " / ".join(theme_names())
        raise ValueError(f"知らない見た目です: {name}(選べるのは {known})") from None


@dataclass
class Style:
    #: 全体の見た目(地の色と装飾)。
    theme: Theme = field(default_factory=lambda: THEMES[DEFAULT_THEME])

    # フォント(欧文 / 日本語 / 等幅)
    font_latin: str = "Arial"
    font_ea: str = "Meiryo"
    font_mono: str = "Consolas"

    # 色 (RGB)
    color_title: Tuple[int, int, int] = (0x1F, 0x24, 0x2E)
    color_body: Tuple[int, int, int] = (0x2B, 0x31, 0x3B)
    color_accent: Tuple[int, int, int] = (0x1B, 0x6F, 0xB8)
    color_muted: Tuple[int, int, int] = (0x6B, 0x72, 0x80)
    color_code_bg: Tuple[int, int, int] = (0xF4, 0xF5, 0xF7)

    # 本文領域 (inch)
    body_left: float = 0.9
    body_top: float = 1.75
    body_width: float = 11.53
    body_height: float = 5.05

    # タイトル領域 (inch)
    title_left: float = 0.9
    title_top: float = 0.55
    title_width: float = 11.53
    title_height: float = 0.85
    title_rule_top: float = 1.45
    title_rule_height: float = 0.045

    # フォントサイズ (pt)
    deck_title_size: float = 40
    deck_subtitle_size: float = 20
    section_title_size: float = 34
    slide_title_size: float = 28
    body_sizes: Dict[int, float] = field(
        default_factory=lambda: {0: 20, 1: 17, 2: 15, 3: 14}
    )
    code_size: float = 14
    table_size: float = 14
    caption_size: float = 13
    diagram_size: float = 18

    # 行間・間隔
    line_spacing: float = 1.22
    space_before_pt: float = 9.0
    code_line_spacing: float = 1.15
    # 実際の行の高さはフォントサイズより大きい(日本語フォントでおよそ 1.2 倍)。
    # 何行入るかの見積りに使う。
    font_line_factor: float = 1.2

    # 箇条書きのインデント (inch / 段)
    indent_per_level: float = 0.42

    # 1 枚に複数の中身(文章と図など)を並べるときの、上下の間隔 (inch)
    part_gap: float = 0.24

    # 図解 (inch)
    #: 項目 1 つの高さ。
    diagram_item_height: float = 0.56
    #: 流れ図で、項目と項目の間(矢印を置く場所)。縦に積む場合。
    diagram_arrow_height: float = 0.30
    #: 流れ図で、項目と項目の間(矢印を置く場所)。横に並べる場合。
    #: 縦より広く取る。横並びでは箱が隣り合うため、間が詰まると 1 つの帯に見える。
    diagram_arrow_width: float = 0.44
    #: 枠図で、項目と項目の間。
    diagram_item_gap: float = 0.12
    #: 枠図の外枠と、中の項目との間。
    diagram_frame_padding: float = 0.26
    #: 項目の箱の、文字の左右に取る余白(この 2 倍が箱の幅に足される)。
    diagram_item_padding: float = 0.42
    #: 項目の箱の幅の下限と上限。短い語ばかりでも細くなりすぎず、長い文でも
    #: 画面いっぱいには広げない(図は中央に置くので、幅が揃っていた方が読める)。
    diagram_min_width: float = 3.4
    diagram_max_width: float = 7.6
    #: 境界図で、線をまたぐものを置く帯の高さ。線はこの帯の中央に引き、
    #: 矢印は帯いっぱいに立てて線を突き抜けさせる(またいでいることが分かる)。
    #: 札は線の上下どちらかの半分に入るので、帯は札 1 行の倍を見込む。
    diagram_boundary_band: float = 1.04
    #: 境界図で、線が箱より左右へはみ出す長さ。箱の幅ぴったりで止めると
    #: 「箱と箱をつなぐ線」に見えて、越える・越えないの話に見えない。
    diagram_boundary_overhang: float = 0.36
    #: 境界図の線そのものの太さ。項目の箱の輪郭より太くする(この線が図の主役で、
    #: 箱の輪郭と同じ太さだと、ただの区切りに見える)。
    diagram_boundary_rule: float = 0.055
    #: 境界図で、またぐものの矢印の幅。
    diagram_crossing_arrow: float = 0.26
    #: 境界図で、またぐものの札の文字の大きさ(項目の文字に対する割合)。
    #: 線をまたぐものは図の主役ではないので、箱の中の語より小さくする。
    diagram_crossing_ratio: float = 0.78

    def body_size(self, level: int) -> float:
        sizes = self.body_sizes
        if level in sizes:
            return sizes[level]
        return sizes[max(sizes)]

    @property
    def body_width_pt(self) -> float:
        return inches_to_pt(self.body_width)

    @property
    def body_height_pt(self) -> float:
        return inches_to_pt(self.body_height)

    def bullet_indent_pt(self, level: int) -> float:
        """箇条書き記号ぶんを含む左インデント(pt)。"""
        return inches_to_pt(self.indent_per_level * (level + 1))

    def line_height_pt(self, font_pt: float, spacing: float | None = None) -> float:
        """1 行が占める高さ(pt)の見積り。"""
        return font_pt * (self.line_spacing if spacing is None else spacing) * self.font_line_factor
