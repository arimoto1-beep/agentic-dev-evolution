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
* 層(`layer`)。描いたものに名前を付けておき、あとから **出す層・目立たせる層を
  選んで** 同じ絵を何通りにも書き出す。動画で、話に合わせて絵を 1 つずつ足したり、
  いま話している部品だけを濃く残したりするのに使う(`save_step`。`staging.py`)
* 動き(`save_motion`)。少しずつ変えた絵を連番で書き出す
* 段の並び(`save_steps`)。「何段目に、どの層を出し、どれを濃く残すか」を並べて渡すと、
  段ごとの絵と、**前の段から移るときの動き** をまとめて書き出す。新しく出る層は
  少し下から上がりながら現れ、濃さが変わる層はその場で変わる。画面全体を溶かすと
  「どこが変わったのか」を目で探すことになるが、変わるものだけが動けば、目はそこへ行く

画面いっぱいの場面(`レイアウト: 全画面`)を描くときは、`Sketch(*VIDEO_SIZE)` で
動画のコマと同じ大きさの画用紙を使う。

座標は左上が原点、右が +x、下が +y(単位は書き出す画像の画素)。
"""

from __future__ import annotations

import glob
import math
import os
import shutil
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .staging import motion_dir, step_path
from .style import Style

Point = Tuple[float, float]
Color = Tuple[int, int, int]

#: 資料の本文領域(11.53 x 5.05 inch)と同じ縦横比。図だけの画面なら、縮めずに収まる。
DEFAULT_SIZE = (2400, 1050)

#: 動画のコマと同じ大きさ。画面いっぱいの場面(`レイアウト: 全画面`)を描くときに使う。
VIDEO_SIZE = (1920, 1080)

#: 段が変わるときの動きの、既定の長さ(秒)と、新しく出る層が上がってくる距離(画素)。
#: 短く、小さくしてある。目が「いま出たもの」へ向けば足り、動きそのものを見せたいわけではない。
ENTER_SECONDS = 0.4
ENTER_RISE = 26

#: 何倍の大きさで描いてから縮めるか。
SUPERSAMPLE = 2

#: 目立たせない層を、どれだけの濃さで残すか(0 = 消す、1 = そのまま)。
#: 消さないのは、いま話していない部品も「そこにある」ことは見えていてほしいため。
FADED = 0.22

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
        # 層。使い始めるまでは空で、絵は上の 1 枚にそのまま描く(これまでと同じ)。
        # 使い始めたら、描いた順に(名前, 透明な地の絵)を積む。名前の無い層は、
        # 層の外で描いたもの(いつも出す)。
        self._ground = self._image
        self._layers: List[Tuple[str, Image.Image]] = []
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

    # -- 層 ---------------------------------------------------------------
    @contextmanager
    def layer(self, name: str) -> Iterator[None]:
        """この中で描くものを、`name` という層にまとめる。

        層は描いた順に重なる。同じ名前でもう一度開くと、同じ層に描き足す
        (重なりの順は、最初に開いたときのまま)。層の外で描いたものは、
        どの書き出しでもそのまま出る。
        """
        if not name:
            raise SketchError("層には名前を付けてください。")
        outer = self._image
        outer_named = any(image is outer and existing for existing, image in self._layers)
        self._drop_empty_top()
        self._target(self._named(name))
        try:
            yield
        finally:
            if outer_named:
                self._target(outer)  # 層の中で開いた層。外側の層へ戻る
            else:
                # 層を閉じたあとに描くものは、この層より上に重なる。
                self._layers.append(("", self._blank()))
                self._target(self._layers[-1][1])

    def layers(self) -> List[str]:
        """付けた層の名前(描いた順)。"""
        return list(dict.fromkeys(name for name, _ in self._layers if name))

    def _blank(self) -> Image.Image:
        return Image.new("RGBA", self._ground.size, (0, 0, 0, 0))

    def _named(self, name: str) -> Image.Image:
        for existing, image in self._layers:
            if existing == name:
                return image
        self._layers.append((name, self._blank()))
        return self._layers[-1][1]

    def _target(self, image: Image.Image) -> None:
        self._image = image
        self._draw = ImageDraw.Draw(image)

    def _drop_empty_top(self) -> None:
        """何も描かれなかった名前なしの層を片付ける(層ごとに 1 枚ぶんの記憶を使う)。"""
        if self._layers and not self._layers[-1][0] and self._layers[-1][1] is self._image:
            if self._layers[-1][1].getbbox() is None:
                self._layers.pop()

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
        halo=True,
    ) -> None:
        """文字。`anchor` は Pillow と同じ(左右 l/m/r と上下 t/m/b)。改行できる。
        `halo` は文字の縁の縁取り(線や塗りの上に重なっても読めるように)。True なら白、
        色を渡すとその色(白くない地の上に描くとき、地の色を渡す)。"""
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
                stroke_fill=WHITE if halo is True else (halo or WHITE),
            )

    def text_width(self, text: str, size: float = 44, bold: bool = False) -> float:
        """その文字を書いたときの幅(並べて置くものの位置を決めるのに使う)。"""
        return max(self._font(size, bold).getlength(line) for line in text.splitlines()) / self._k

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
    def image(
        self,
        show: Optional[Sequence[str]] = None,
        focus: Optional[Sequence[str]] = None,
        faded: float = FADED,
    ) -> Image.Image:
        """縮めたあとの絵(書き出す大きさ)。

        `show` に層の名前を並べると、その層だけを出す(省くと全部)。
        `focus` に並べた層はそのまま、出しているほかの層は薄くする。
        層の外で描いたものは、どちらを指定してもそのまま出る。
        """
        if not self._layers:
            return self._shrink(self._image)
        return self._shrink(self._stack(self._layers, self.levels(show, focus, faded)))

    def levels(
        self,
        show: Optional[Sequence[str]] = None,
        focus: Optional[Sequence[str]] = None,
        faded: float = FADED,
    ) -> Dict[str, float]:
        """層ごとの濃さ(0 = 出さない、1 = そのまま)。`image` と同じ選び方。"""
        known = self.layers()
        for name in list(show or []) + list(focus or []):
            if name not in known:
                raise SketchError(
                    f"層がありません: {name}(あるのは {' / '.join(known)})"
                )
        levels: Dict[str, float] = {}
        for name in known:
            if show is not None and name not in show:
                levels[name] = 0.0
            elif focus is not None and name not in focus:
                levels[name] = faded
            else:
                levels[name] = 1.0
        return levels

    def _stack(
        self,
        layers: Sequence[Tuple[str, Image.Image]],
        levels: Mapping[str, float],
        shifts: Optional[Mapping[str, Point]] = None,
        ground: Optional[Image.Image] = None,
    ) -> Image.Image:
        """層を、指定した濃さとずれで重ねる(描いた大きさのまま)。"""
        canvas = (ground if ground is not None else self._ground).convert("RGBA")
        for name, layer in layers:
            level = levels.get(name, 1.0) if name else 1.0
            if level <= 0.0:
                continue
            if level < 1.0:
                alpha = layer.getchannel("A").point(lambda value, k=level: int(value * k))
                layer = layer.copy()
                layer.putalpha(alpha)
            dx, dy = (shifts or {}).get(name, (0.0, 0.0)) if name else (0.0, 0.0)
            _composite(canvas, layer, int(round(dx * self._k)), int(round(dy * self._k)))
        return canvas

    def _shrink(self, canvas: Image.Image) -> Image.Image:
        # ちょうど整数分の 1 に縮める。画素をまとめて平均するだけなので速い
        # (動きの連番では、1 コマごとにここを通る)。
        return canvas.convert("RGB").reduce(self._k)

    def transition(
        self,
        before: Mapping[str, object],
        after: Mapping[str, object],
        seconds: float = ENTER_SECONDS,
        rise: float = ENTER_RISE,
        fps: int = 30,
    ) -> List[Image.Image]:
        """`before` の姿から `after` の姿へ移る動き(1 コマに 1 枚。最後の 1 枚が `after`)。

        `before` `after` は `image` に渡す引数(`show` `focus` `faded`)の辞書。
        新しく出る層は、`rise` 画素だけ下から上がりながら現れる。消える層・濃さが
        変わる層は、その場で変わる。どれも、ゆっくり動き出してゆっくり止まる。
        """
        start = self.levels(before.get("show"), before.get("focus"), before.get("faded", FADED))
        end = self.levels(after.get("show"), after.get("focus"), after.get("faded", FADED))
        moving = {name for name in end if abs(start[name] - end[name]) > 1e-6}
        runs = self._runs(moving, end)
        count = max(1, int(round(seconds * fps)))
        frames: List[Image.Image] = []
        for index in range(1, count + 1):
            t = ease(index / count)
            levels = {name: start[name] + (end[name] - start[name]) * t for name in moving}
            shifts = {
                name: (0.0, rise * (1.0 - t)) for name in moving if start[name] <= 0.0 < end[name]
            }
            frames.append(self._shrink(self._stack(runs, levels, shifts)))
        return frames

    def _runs(
        self, moving: Iterable[str], levels: Mapping[str, float]
    ) -> List[Tuple[str, Image.Image]]:
        """動かない層を、続いているぶんだけ 1 枚にまとめる(コマごとに重ね直す枚数を減らす)。"""
        moving = set(moving)
        runs: List[Tuple[str, Image.Image]] = []
        pending: List[Tuple[str, Image.Image]] = []

        def flush() -> None:
            if pending:
                runs.append(("", self._stack(pending, levels, ground=self._blank())))
                pending.clear()

        for name, layer in self._layers:
            if name in moving:
                flush()
                runs.append((name, layer))
            else:
                pending.append((name, layer))
        flush()
        return runs

    def save_steps(
        self,
        path: str,
        steps: Sequence[Mapping[str, object]],
        opening: Optional[Mapping[str, object]] = None,
        seconds: float = ENTER_SECONDS,
        rise: float = ENTER_RISE,
        fps: int = 30,
    ) -> List[str]:
        """段の並びを、絵と動きにして書き出す(`図.png` `図@1.png` … と `図@2/000.png` …)。

        `steps` の 1 つが 1 段。`image` に渡す引数(`show` `focus` `faded`)の辞書で、
        ほかに次のものを書ける。

            seconds   この段へ移る動きの長さ(秒)。0 なら動きを付けない
            rise      新しく出る層が上がってくる距離(画素)
            motion    この段へ移る動きを、自分で用意した絵の並びにする
                      (部品そのものを動かすとき。最後の 1 枚は、この段の姿にする)
            image     この段の姿を、自分で用意した絵にする(別の絵へ切り替えるとき)

        `opening` を渡すと、最初の合図までの姿(`図@0.png`)も書き出し、1 段目にも
        そこからの動きを付ける。`図.png`(資料に載る姿)は最後の段と同じ絵になる。
        """
        clear_steps(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        written: List[str] = []
        previous: Optional[Mapping[str, object]] = None
        if opening is not None:
            target = step_path(path, 0)
            self._still(opening).save(target, optimize=True)
            written.append(target)
            previous = opening
        last: Optional[Image.Image] = None
        for number, step in enumerate(steps, start=1):
            last = self._still(step)
            target = step_path(path, number)
            last.save(target, optimize=True)
            written.append(target)
            frames = step.get("motion")
            if frames is None and previous is not None and "image" not in previous:
                length = float(step.get("seconds", seconds))
                if length > 0 and "image" not in step:
                    frames = self.transition(
                        previous, step, length, float(step.get("rise", rise)), fps
                    )
            if frames:
                save_motion(path, number, frames)
            previous = step
        if last is not None:
            last.save(path, optimize=True)
        return written

    def _still(self, step: Mapping[str, object]) -> Image.Image:
        if step.get("image") is not None:
            return step["image"]  # type: ignore[return-value]
        return self.image(
            show=step.get("show"),  # type: ignore[arg-type]
            focus=step.get("focus"),  # type: ignore[arg-type]
            faded=float(step.get("faded", FADED)),
        )

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.image().save(path, optimize=True)
        return path

    def save_step(
        self,
        path: str,
        step: int,
        show: Optional[Sequence[str]] = None,
        focus: Optional[Sequence[str]] = None,
    ) -> str:
        """絵 `path` の `step` 段目として書き出す(`図@2.png`)。

        動画は、原稿の合図〔2〕のところでこの絵に変える(`staging.py`)。
        段は 1 から続き番号で置く(途中が抜けると、そこから先は使われない)。
        """
        target = step_path(path, step)
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        self.image(show=show, focus=focus).save(target, optimize=True)
        return target


def _composite(canvas: Image.Image, layer: Image.Image, dx: int, dy: int) -> None:
    """`layer` を (dx, dy) だけずらして `canvas` に重ねる(はみ出した分は捨てる)。"""
    if dx == 0 and dy == 0:
        canvas.alpha_composite(layer)
        return
    width, height = canvas.size
    left, top = max(0, -dx), max(0, -dy)
    right, bottom = min(width, width - dx), min(height, height - dy)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(layer, dest=(max(0, dx), max(0, dy)), source=(left, top, right, bottom))


def clear_steps(path: str) -> None:
    """絵 `path` の段と動きを消す(描き直す前に呼ぶ。段を減らしたときに古い段が残らない)。"""
    stem, suffix = os.path.splitext(path)
    for found in glob.glob(glob.escape(stem) + "@*"):
        tail = found[len(stem) + 1 :]
        if os.path.isdir(found) and tail.isdigit():
            shutil.rmtree(found)
        elif found.endswith(suffix) and tail[: -len(suffix)].isdigit():
            os.remove(found)


def save_motion(path: str, step: int, frames: Iterable[Image.Image]) -> List[str]:
    """絵 `path` の `step` 段目に入るときの動きを、連番で書き出す(`図@2/000.png` …)。

    動画は 1 枚を 1 コマとして流し、流し終えたら `図@2.png` で止まる。最後の 1 枚を
    `図@2.png` と同じ絵にしておくと、止まるときに跳ねない。
    """
    directory = motion_dir(path, step)
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    written: List[str] = []
    for position, frame in enumerate(frames):
        target = os.path.join(directory, f"{position:03d}.png")
        frame.save(target, compress_level=1)
        written.append(target)
    return written


def ease(t: float) -> float:
    """ゆっくり動き出して、ゆっくり止まる(0 から 1)。動きの連番を作るときに使う。"""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


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
