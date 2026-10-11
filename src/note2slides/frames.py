"""動画のコマを 1 枚ずつ作って、ffmpeg へ流し込む。

これまでの動画は「スライド 1 枚 = 止まった絵 1 枚」で、ffmpeg に絵と枚数を
渡せば済んだ(`video._write_filter_script`)。1 枚の中で画面を変える
(段を足す・絵を動かす・前の画面から溶かす)には、コマごとに違う絵が要る。
ここでは、どのコマに何を出すかを先に決め(`plan_frames`)、そのとおりに
コマを作って流す(`write_frames`)。

    スライドの時間割り + 場面(`staging.Shot`) --> コマの並び --> ffmpeg

止まっているあいだは同じコマをそのまま流し直すので、作る手間がかかるのは
変わり目のコマだけになる。長さはコマ数で数えるので、音声とのずれは出ない
(`video.build_timeline` が決めたコマ数ちょうどを流す)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .staging import Shot


@dataclass(frozen=True)
class Change:
    """あるコマから、画面を別の絵へ変える。"""

    frame: int  # そのスライドの中で何コマ目か
    image: str
    fade: int = 0  # 何コマかけて溶かすか(0 なら切り替える)
    motion: Tuple[str, ...] = ()  # 溶かす代わりに流す連番


@dataclass
class SlideFrames:
    index: int
    frames: int
    changes: List[Change] = field(default_factory=list)


def plan_frames(
    slides: Sequence[Tuple[int, float, Sequence[Shot]]],
    fps: int,
    step_fade: float = 0.3,
    slide_fade: float = 0.2,
) -> List[SlideFrames]:
    """スライドごとに、何コマ目で何へ変えるかを決める。

    `slides` は(スライド番号, 映す長さ(秒), 場面の並び)。場面の変わり目が
    前の変わり目より手前になったり、スライドの長さからはみ出したりしないように
    そろえる(はみ出した変わり目は、次のスライドに食い込む前に捨てる)。
    """
    plans: List[SlideFrames] = []
    for position, (index, duration, shots) in enumerate(slides):
        total = max(1, int(round(duration * fps)))
        plan = SlideFrames(index=index, frames=total)
        busy_until = 0
        for number, shot in enumerate(shots):
            if number == 0:
                fade = int(round(slide_fade * fps)) if position > 0 else 0
                plan.changes.append(Change(0, shot.image, min(fade, max(0, total - 1))))
                busy_until = plan.changes[-1].fade
                continue
            frame = max(int(round(shot.at * fps)), busy_until)
            if frame >= total:
                break
            length = len(shot.motion) if shot.motion else int(round(step_fade * fps))
            plan.changes.append(Change(frame, shot.image, 0 if shot.motion else length, shot.motion))
            busy_until = frame + length
        plans.append(plan)
    return plans


def count_changes(plans: Sequence[SlideFrames]) -> int:
    """スライドの中で画面が変わる回数(スライドの切り替わりは数えない)。"""
    return sum(max(0, len(plan.changes) - 1) for plan in plans)


def write_frames(
    stream,
    plans: Sequence[SlideFrames],
    size: Tuple[int, int],
    background: Tuple[int, int, int] = (255, 255, 255),
    on_frame: Optional[Callable[[int], None]] = None,
) -> int:
    """決めたとおりのコマを、RGB の生データとして `stream` に書く。書いた枚数を返す。"""
    loader = _Loader(size, background)
    written = 0
    last: Optional[np.ndarray] = None
    for plan in plans:
        pending = list(plan.changes)
        current: Optional[np.ndarray] = last
        current_bytes: Optional[bytes] = None
        active: Optional[Tuple[str, int, object, np.ndarray]] = None
        for frame in range(plan.frames):
            while pending and pending[0].frame <= frame:
                change = pending.pop(0)
                target = loader.load(change.image)
                if change.motion:
                    active = ("motion", frame, change.motion, target)
                elif change.fade > 0 and last is not None:
                    active = ("fade", frame, (last, change.fade), target)
                else:
                    active, current, current_bytes = None, target, None

            if active is not None:
                kind, start, payload, target = active
                step = frame - start
                if kind == "motion" and step < len(payload):
                    shown = loader.load(payload[step], keep=False)
                elif kind == "fade" and step < payload[1]:
                    shown = _blend(payload[0], target, _ease((step + 1) / (payload[1] + 1)))
                else:
                    active, current, current_bytes = None, target, None
                    shown = None
                if shown is not None:
                    stream.write(shown.tobytes())
                    written += 1
                    if on_frame:
                        on_frame(written)
                    continue

            if current is None:  # 念のため。最初のスライドには必ず最初の絵がある
                current = loader.blank()
            if current_bytes is None:
                current_bytes = current.tobytes()
            stream.write(current_bytes)
            last = current
            written += 1
            if on_frame:
                on_frame(written)
        # 溶かしている途中でスライドが終わっても、次は行き先の絵から続ける。
        if active is not None:
            last = active[3]
    return written


def _ease(t: float) -> float:
    """ゆっくり始まり、ゆっくり終わる(一定の速さで溶かすと、機械的に見える)。"""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _blend(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    weight = int(round(t * 256))
    mixed = (a.astype(np.uint16) * (256 - weight) + b.astype(np.uint16) * weight) >> 8
    return mixed.astype(np.uint8)


class _Loader:
    """絵を、動画の大きさの画素の並びとして読む(同じ絵は読み直さない)。"""

    def __init__(self, size: Tuple[int, int], background: Tuple[int, int, int]) -> None:
        self.size = size
        self.background = background
        self._cache: Dict[str, np.ndarray] = {}

    def blank(self) -> np.ndarray:
        width, height = self.size
        return np.full((height, width, 3), self.background, dtype=np.uint8)

    def load(self, path: str, keep: bool = True) -> np.ndarray:
        if path in self._cache:
            return self._cache[path]
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        if image.size != self.size:
            image = _fit(image, self.size, self.background)
        pixels = np.asarray(image, dtype=np.uint8)
        if keep:
            if len(self._cache) > 12:  # 1 枚 6MB。直前の数枚があれば足りる
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = pixels
        return pixels


def _fit(image: Image.Image, size: Tuple[int, int], background: Tuple[int, int, int]) -> Image.Image:
    """引き伸ばさずに収め、足りない分は余白にする(止まった絵の経路と同じ扱い)。"""
    width, height = size
    ratio = min(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, int(round(image.width * ratio))), max(1, int(round(image.height * ratio)))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGB", size, background)
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas
