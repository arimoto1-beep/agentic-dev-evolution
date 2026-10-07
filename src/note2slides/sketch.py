"""絵を描くための下地(模式図を PNG として書き出す)。

図解(流れ・枠・境界・レーン・階段・配置)は、どれも **名前の付いた箱** を並べる。
箱で言えるのは順番・包含・位置までで、**ものの形** と **動き** は言えない。
「車輪が車輪に見える」「棒が傾く」「片方だけ持ち上がる」を見せたいときは、
箱を並べ直すのではなく絵を描き、教材シナリオから `![説明](図.png)` で置く。

ここにあるのは、絵の中身ではなく下地だけ。何を描くかは題材ごとに違うので、
描く手順は題材の側(シナリオの隣)にスクリプトとして置く。ここが受け持つのは、
どの題材でも同じになるところ:

* 資料の本文領域と同じ縦横比の画用紙(`Sketch()` の既定)
* 大きく描いて縮める(斜めの線や丸がぎざぎざにならない)
* 資料と同じ書体・同じ色(`style.Style` から取る)
* ずらす・傾ける(`at`)。同じ部品を、別の位置・別の傾きでもう一度描ける
* 引き出し線の付いた名前(`label`)。絵の中のものに、その場で名前を付ける

座標は左上が原点、右が +x、下が +y(単位は書き出す画像の画素)。
"""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .style import Style

Point = Tuple[float, float]
Color = Tuple[int, int, int]

#: 資料の本文領域(11.53 x 5.05 inch)と同じ縦横比。図だけの画面なら、縮めずに収まる。
DEFAULT_SIZE = (2400, 1050)

#: 何倍の大きさで描いてから縮めるか。
SUPERSAMPLE = 2

_STYLE = Style()
INK: Color = _STYLE.color_body
MUTED: Color = _STYLE.color_muted
ACCENT: Color = _STYLE.color_accent
WHITE: Color = (255, 255, 255)

# 資料の日本語書体(Meiryo)を先に探す。無い環境では、日本語が出る書体へ落とす。
_FONT_CANDIDATES = {
    False: [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    True: [
        "C:/Windows/Fonts/meiryob.ttc",
        "C:/Windows/Fonts/YuGothB.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ],
}


class SketchError(Exception):
    """絵を描けない(日本語の書体が見つからない、など)。"""


def font_path(bold: bool = False) -> str:
    for candidate in _FONT_CANDIDATES[bold]:
        if os.path.isfile(candidate):
            return candidate
    raise SketchError(
        "絵の中に文字を書くための日本語の書体が見つかりません。\n"
        "  探した場所: " + " / ".join(_FONT_CANDIDATES[bold])
    )


class Sketch:
    """1 枚の絵。描き終えたら `save` で PNG にする。"""

    def __init__(
        self,
        width: int = DEFAULT_SIZE[0],
        height: int = DEFAULT_SIZE[1],
        background: Color = WHITE,
    ) -> None:
        self.width = width
        self.height = height
        self._k = SUPERSAMPLE
        self._image = Image.new("RGB", (width * self._k, height * self._k), background)
        self._draw = ImageDraw.Draw(self._image)
        # いま効いている「ずらす・傾ける」。外側から順に積む。
        self._frames: List[Tuple[float, float, float, float]] = []
        self._fonts: dict = {}

    # -- ずらす・傾ける ---------------------------------------------------
    @contextmanager
    def at(
        self, dx: float = 0.0, dy: float = 0.0, angle: float = 0.0, scale: float = 1.0
    ) -> Iterator[None]:
        """この中で描くものを、(0, 0) を中心に `scale` 倍して `angle` 度だけ
        時計回りに傾け、(dx, dy) へずらす。文字の大きさと線の太さは変えない
        (縮めた絵でも名前が読めるように)。"""
        self._frames.append((dx, dy, math.radians(angle), scale))
        try:
            yield
        finally:
            self._frames.pop()

    def _map(self, point: Point) -> Point:
        x, y = point
        for dx, dy, rad, scale in reversed(self._frames):
            x, y = x * scale, y * scale
            x, y = (
                x * math.cos(rad) - y * math.sin(rad) + dx,
                x * math.sin(rad) + y * math.cos(rad) + dy,
            )
        return x * self._k, y * self._k

    def _px(self, points: Sequence[Point]) -> List[Point]:
        return [self._map(p) for p in points]

    def _w(self, width: float) -> int:
        return max(1, int(round(width * self._k)))

    # -- 線 ---------------------------------------------------------------
    def line(
        self,
        points: Sequence[Point],
        color: Color = INK,
        width: float = 6,
        dash: Optional[Tuple[float, float]] = None,
    ) -> None:
        """折れ線。角と端は丸める。`dash=(線, 空き)` で破線。"""
        mapped = self._px(points)
        if dash:
            for piece in _dashes(mapped, dash[0] * self._k, dash[1] * self._k):
                self._stroke(piece, color, width)
        else:
            self._stroke(mapped, color, width)

    def _stroke(self, mapped: Sequence[Point], color: Color, width: float) -> None:
        w = self._w(width)
        self._draw.line(list(mapped), fill=color, width=w, joint="curve")
        r = w / 2
        for x, y in (mapped[0], mapped[-1]):
            self._draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def arrow(
        self,
        start: Point,
        end: Point,
        color: Color = ACCENT,
        width: float = 10,
        head: float = 34,
        both: bool = False,
    ) -> None:
        """矢印。`both=True` で両側に矢じりを付ける。"""
        length = math.dist(start, end)
        if length == 0:
            return
        ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        inset = head * 0.8
        a = (start[0] + ux * inset, start[1] + uy * inset) if both else start
        b = (end[0] - ux * inset, end[1] - uy * inset)
        self.line([a, b], color, width)
        self._head(end, (ux, uy), color, head)
        if both:
            self._head(start, (-ux, -uy), color, head)

    def _head(self, tip: Point, direction: Point, color: Color, size: float) -> None:
        ux, uy = direction
        back = (tip[0] - ux * size, tip[1] - uy * size)
        half = size * 0.55
        self.polygon(
            [tip, (back[0] - uy * half, back[1] + ux * half), (back[0] + uy * half, back[1] - ux * half)],
            fill=color,
        )

    def arc_arrow(
        self,
        center: Point,
        radius: float,
        start: float,
        end: float,
        color: Color = ACCENT,
        width: float = 8,
        head: float = 28,
    ) -> None:
        """円弧の矢印(回る・傾く)。角度は度、右向きが 0、時計回りが正。"""
        steps = max(8, int(abs(end - start) / 4))
        points = [
            (
                center[0] + radius * math.cos(math.radians(start + (end - start) * i / steps)),
                center[1] + radius * math.sin(math.radians(start + (end - start) * i / steps)),
            )
            for i in range(steps + 1)
        ]
        self.line(points[:-1], color, width)
        sign = 1 if end >= start else -1
        rad = math.radians(end)
        self._head(points[-1], (-math.sin(rad) * sign, math.cos(rad) * sign), color, head)

    # -- 面 ---------------------------------------------------------------
    def polygon(
        self,
        points: Sequence[Point],
        fill: Optional[Color] = None,
        outline: Optional[Color] = None,
        width: float = 5,
    ) -> None:
        mapped = self._px(points)
        if fill is not None:
            self._draw.polygon(mapped, fill=fill)
        if outline is not None:
            self._stroke(mapped + [mapped[0]], outline, width)

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: Optional[Color] = None,
        outline: Optional[Color] = None,
        width: float = 5,
        radius: float = 0,
        dash: Optional[Tuple[float, float]] = None,
    ) -> None:
        """四角。`radius` で角を丸める。傾けた中でも形が崩れない。"""
        points = _rounded(x, y, w, h, radius)
        if dash and outline is not None:
            self.polygon(points, fill=fill)
            self.line(points + [points[0]], outline, width, dash=dash)
        else:
            self.polygon(points, fill=fill, outline=outline, width=width)

    def circle(
        self,
        center: Point,
        radius: float,
        fill: Optional[Color] = None,
        outline: Optional[Color] = None,
        width: float = 5,
    ) -> None:
        points = [
            (center[0] + radius * math.cos(math.radians(a)), center[1] + radius * math.sin(math.radians(a)))
            for a in range(0, 360, 6)
        ]
        self.polygon(points, fill=fill, outline=outline, width=width)

    def hatch(
        self, x: float, y: float, w: float, h: float, color: Color = MUTED, gap: float = 26, width: float = 3
    ) -> None:
        """四角の中に斜めの線を引く(「ここは動かない側」を言うときの塗り)。"""
        offset = -h
        while offset < w:
            x0, y0, x1, y1 = x + offset, y + h, x + offset + h, y
            if x0 < x:
                x0, y0 = x, y + h - (x - (x + offset))
            if x1 > x + w:
                x1, y1 = x + w, y + (x + offset + h - (x + w))
            self.line([(x0, y0), (x1, y1)], color, width)
            offset += gap

    def spring(
        self, start: Point, end: Point, color: Color = MUTED, width: float = 6, turns: int = 5, swing: float = 26
    ) -> None:
        """バネ(ジグザグ)。"""
        length = math.dist(start, end)
        ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        lead = length * 0.12
        points = [start, (start[0] + ux * lead, start[1] + uy * lead)]
        body = length - lead * 2
        for i in range(turns * 2):
            along = lead + body * (i + 0.5) / (turns * 2)
            side = swing if i % 2 == 0 else -swing
            points.append((start[0] + ux * along - uy * side, start[1] + uy * along + ux * side))
        points += [(end[0] - ux * lead, end[1] - uy * lead), end]
        self.line(points, color, width)

    # -- 文字 -------------------------------------------------------------
    def _font(self, size: float, bold: bool) -> ImageFont.FreeTypeFont:
        key = (int(round(size * self._k)), bold)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(font_path(bold), key[0])
        return self._fonts[key]

    def text(
        self,
        at: Point,
        text: str,
        size: float = 44,
        color: Color = INK,
        bold: bool = False,
        anchor: str = "mm",
        halo: bool = True,
    ) -> None:
        """文字。`anchor` は Pillow と同じ(左右 l/m/r と上下 t/m/b)。改行できる。
        `halo` は文字の縁の白い縁取り(線や塗りの上に重なっても読めるように)。"""
        x, y = self._map(at)
        font = self._font(size, bold)
        lines = text.split("\n")
        pitch = size * self._k * 1.32
        top = y - {"t": 0.0, "m": 0.5, "b": 1.0}[anchor[1]] * pitch * (len(lines) - 1)
        for i, line in enumerate(lines):
            self._draw.text(
                (x, top + pitch * i),
                line,
                font=font,
                fill=color,
                anchor=anchor[0] + anchor[1].replace("t", "a").replace("b", "d"),
                stroke_width=self._w(5) if halo else 0,
                stroke_fill=WHITE,
            )

    def label(
        self,
        at: Point,
        text: str,
        target: Point,
        size: float = 44,
        color: Color = INK,
        bold: bool = False,
        anchor: str = "mm",
        line_from: Optional[Point] = None,
    ) -> None:
        """名前と、それが指すものまでの引き出し線。線は先に描く(文字が上になる)。
        線の出どころは `line_from`(省くと文字の位置)。"""
        self.line([line_from or at, target], color, 3)
        self.circle(target, 9, fill=color)
        self.text(at, text, size=size, color=color, bold=bold, anchor=anchor)

    # -- 書き出す ---------------------------------------------------------
    def image(self) -> Image.Image:
        """縮めたあとの絵(書き出す大きさ)。"""
        return self._image.resize((self.width, self.height), Image.LANCZOS)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.image().save(path, optimize=True)
        return path


def _rounded(x: float, y: float, w: float, h: float, radius: float) -> List[Point]:
    r = max(0.0, min(radius, w / 2, h / 2))
    if r == 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    points: List[Point] = []
    corners = [(x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)]
    for cx, cy, start in corners:
        for step in range(0, 91, 10):
            rad = math.radians(start + step)
            points.append((cx + r * math.cos(rad), cy + r * math.sin(rad)))
    return points


def _dashes(points: Sequence[Point], on: float, off: float) -> List[List[Point]]:
    """折れ線を、長さ `on` の線と長さ `off` の空きに切り分ける。"""
    pieces: List[List[Point]] = []
    current: List[Point] = [points[0]]
    drawing, left = True, on
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        pos = 0.0
        while seg - pos > left:
            pos += left
            cut = (a[0] + (b[0] - a[0]) * pos / seg, a[1] + (b[1] - a[1]) * pos / seg)
            if drawing:
                current.append(cut)
                pieces.append(current)
            current = [cut]
            drawing = not drawing
            left = on if drawing else off
        left -= seg - pos
        if drawing:
            current.append(b)
        else:
            current = [b]
    if drawing and len(current) > 1:
        pieces.append(current)
    return pieces
