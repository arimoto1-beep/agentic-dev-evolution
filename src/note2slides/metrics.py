"""テキスト量の見積り。

スライドに何行入るかを判断するために、文字幅を em 単位で概算する。
厳密なレンダリング結果ではなく、あふれを避けるための目安として使う。
"""

from __future__ import annotations

import math
import unicodedata

# 全角として扱う East Asian Width。A(ambiguous)は日本語記事では全角相当が多い。
_WIDE = ("W", "F", "A")


def char_width(ch: str) -> float:
    if ch == "\t":
        return 2.0
    return 1.0 if unicodedata.east_asian_width(ch) in _WIDE else 0.5


def text_width_em(text: str) -> float:
    """テキストの表示幅を em 単位で概算する。"""
    return sum(char_width(ch) for ch in text)


def line_count(text: str, font_pt: float, avail_pt: float) -> int:
    """折り返しを考慮した行数を概算する。"""
    if avail_pt <= 0 or font_pt <= 0:
        return 1
    per_line = avail_pt / font_pt  # 1 行に入る em 数
    lines = 0
    for raw_line in text.split("\n"):
        width = text_width_em(raw_line)
        lines += max(1, math.ceil(width / per_line)) if width else 1
    return max(1, lines)
