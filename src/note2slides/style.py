"""スライドの寸法・書式設定。

16:9 (13.333in x 7.5in) を前提とした値を持つ。ここを変えるだけでレイアウトを
調整できるよう、位置やフォントサイズはすべてこのデータクラスに集約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

# 16:9 のスライドサイズ(EMU)。914400 EMU = 1 inch。
SLIDE_WIDTH_EMU = 12192000  # 13.333 in
SLIDE_HEIGHT_EMU = 6858000  # 7.5 in

EMU_PER_INCH = 914400
PT_PER_INCH = 72.0


def inches_to_pt(inches: float) -> float:
    return inches * PT_PER_INCH


@dataclass
class Style:
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

    # 行間・間隔
    line_spacing: float = 1.22
    space_before_pt: float = 9.0
    code_line_spacing: float = 1.15
    # 実際の行の高さはフォントサイズより大きい(日本語フォントでおよそ 1.2 倍)。
    # 何行入るかの見積りに使う。
    font_line_factor: float = 1.2

    # 箇条書きのインデント (inch / 段)
    indent_per_level: float = 0.42

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
