"""動く部品の絵を、コマごとに描き直す下地。

`save_steps` の `transition` は層を出し入れするだけで、部品そのものは動かせない。ピストンが
上下する、流れが進む、といった動きは、値を少しずつ変えて絵を描き直す(`motion` に渡す)。
画面全体を毎回描くと遅く、1 コマの絵をメモリに溜めると足りなくなる。

ここが受け持つのは、題材によらないところだけ:

* `Rig` 画面の一部だけを描き直して、背景の絵に貼る(文字や舞台は 1 度描けば済む)
* `still` ある値のときの 1 枚
* `run` 値を動かす連番(1 枚ずつ作るので、長い動きでもメモリに溜まらない)。`save_steps` の
  `motion` と `loop` に、そのまま渡せる

何をどう描くか(エンジンか、流れか)は題材の側に書く(`Rig` に渡す関数)。

    r = [Rig((100, 200, 800, 600), lambda sk, deg: engine(sk, deg))]
    base = sk.image(show=["題"])
    sk.save_steps(path, [
        dict(image=still(base, r, 180), motion=run(base, r, 0, 180, 5.0), fit=True),
    ], opening=dict(image=still(base, r, 0)))
"""

from __future__ import annotations

from typing import Callable, Iterator, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image

from .sketch import Sketch

Value = Union[float, Sequence[float]]
Color = Tuple[int, int, int]

#: 絵の地として、貼らずに透かす色の許し幅(3 色の差の合計)。
_TRANSPARENT = 6


class Rig:
    """画面の一部だけを描き直す枠。

    `box` は(左, 上, 幅, 高さ)。`draw(sk, value)` は画面と同じ座標で描く。枠の地の色 `ground` は
    貼らずに透かすので、枠の下にある文字は消えない(背景と同じ色を渡す)。
    """

    def __init__(
        self,
        box: Tuple[int, int, int, int],
        draw: Callable[[Sketch, float], None],
        ground: Color = (255, 255, 255),
    ) -> None:
        self.box = box
        self.draw = draw
        self.ground = ground

    def paste(self, frame: np.ndarray, value: float) -> None:
        x, y, width, height = self.box
        sk = Sketch(width, height, background=self.ground)
        with sk.at(-x, -y):
            self.draw(sk, value)
        pixels = np.asarray(sk.image(), dtype=np.uint8)
        difference = np.abs(pixels.astype(np.int16) - np.array(self.ground, dtype=np.int16))
        mask = difference.sum(axis=2) > _TRANSPARENT
        frame[y : y + height, x : x + width][mask] = pixels[mask]


def _each(value: Value, count: int) -> List[float]:
    return list(value) if isinstance(value, (list, tuple)) else [float(value)] * count


def still(base: Image.Image, rigs: Sequence[Rig], values: Value) -> Image.Image:
    """`base` の上に、それぞれの枠を `values` の値で描いた 1 枚。"""
    frame = np.array(base, dtype=np.uint8)
    for rig, value in zip(rigs, _each(values, len(rigs))):
        rig.paste(frame, value)
    return Image.fromarray(frame)


def run(
    base: Image.Image,
    rigs: Sequence[Rig],
    start: Value,
    end: Value,
    seconds: float,
    fps: int = 30,
    shape: Optional[Callable[[float], float]] = None,
    closed: bool = False,
) -> Iterator[Image.Image]:
    """値を `start` から `end` まで動かす連番。

    `shape` は進み方(`sketch.ease` など。省くと一定の速さ)。`closed=True` は繰り返す動き用で、
    最後の 1 枚を `end` の手前で止める(`start` と同じ絵が 2 回続いて、継ぎ目で止まるのを防ぐ)。
    """
    count = max(1, int(round(seconds * fps)))
    first, last = _each(start, len(rigs)), _each(end, len(rigs))
    for index in range(1, count + 1):
        t = (index - 1) / count if closed else index / count
        if shape:
            t = shape(t)
        yield still(base, rigs, [a + (b - a) * t for a, b in zip(first, last)])
