"""コマの並び(`frames`)。1 枚の中で画面を変える動画の、時間の数え方を確かめる。

確かめたいのは見た目ではなく、**音声とずれないこと**:

* 流すコマの数が、スライドに割り当てた長さちょうどであること
* 画面の変わり目が、決めたコマで起きること
* 止まっているあいだは、同じコマが流れること
"""

import io

import numpy as np
from PIL import Image

from note2slides.frames import Change, SlideFrames, count_changes, plan_frames, write_frames
from note2slides.staging import Shot

SIZE = (32, 18)
RED, GREEN, BLUE = (200, 0, 0), (0, 200, 0), (0, 0, 200)


def picture(tmp_path, name, color):
    path = str(tmp_path / f"{name}.png")
    Image.new("RGB", SIZE, color).save(path)
    return path


def colors(data, count):
    """流したコマごとの、真ん中の画素の色。"""
    frames = np.frombuffer(data, dtype=np.uint8).reshape(count, SIZE[1], SIZE[0], 3)
    return [tuple(int(v) for v in frame[SIZE[1] // 2, SIZE[0] // 2]) for frame in frames]


def test_changes_are_placed_on_the_frame_of_their_time():
    shots = [Shot(0.0, "a.png"), Shot(1.0, "b.png", "1"), Shot(2.5, "c.png", "2")]
    (plan,) = plan_frames([(1, 4.0, shots)], fps=10, step_fade=0.3)
    assert plan.frames == 40
    assert [(c.frame, c.image, c.fade) for c in plan.changes] == [
        (0, "a.png", 0),
        (10, "b.png", 3),
        (25, "c.png", 3),
    ]


def test_a_change_waits_until_the_previous_one_has_finished():
    shots = [Shot(0.0, "a.png"), Shot(1.0, "b.png"), Shot(1.1, "c.png")]
    (plan,) = plan_frames([(1, 4.0, shots)], fps=10, step_fade=0.5)
    assert [c.frame for c in plan.changes] == [0, 10, 15]


def test_a_change_after_the_end_of_the_slide_is_dropped():
    shots = [Shot(0.0, "a.png"), Shot(5.0, "b.png")]
    (plan,) = plan_frames([(1, 2.0, shots)], fps=10)
    assert [c.image for c in plan.changes] == ["a.png"]


def test_slides_after_the_first_fade_in_from_the_previous_slide():
    plans = plan_frames(
        [(1, 1.0, [Shot(0.0, "a.png")]), (2, 1.0, [Shot(0.0, "b.png")])], fps=10, slide_fade=0.2
    )
    assert plans[0].changes[0].fade == 0  # 動画の最初は、いきなり出す
    assert plans[1].changes[0].fade == 2
    assert count_changes(plans) == 0  # スライドの切り替わりは、変化に数えない


def test_written_frames_match_the_planned_length(tmp_path):
    a, b = picture(tmp_path, "a", RED), picture(tmp_path, "b", BLUE)
    plans = [
        SlideFrames(1, 6, [Change(0, a), Change(3, b, fade=2)]),
        SlideFrames(2, 4, [Change(0, a, fade=1)]),
    ]
    stream = io.BytesIO()
    written = write_frames(stream, plans, SIZE)
    assert written == 10
    assert len(stream.getvalue()) == 10 * SIZE[0] * SIZE[1] * 3


def test_a_fade_passes_through_colors_in_between_and_then_holds(tmp_path):
    a, b = picture(tmp_path, "a", RED), picture(tmp_path, "b", BLUE)
    stream = io.BytesIO()
    write_frames(stream, [SlideFrames(1, 8, [Change(0, a), Change(2, b, fade=3)])], SIZE)
    seen = colors(stream.getvalue(), 8)
    assert seen[:2] == [RED, RED]
    assert seen[5:] == [BLUE, BLUE, BLUE]
    reds = [color[0] for color in seen[2:5]]
    assert reds == sorted(reds, reverse=True) and RED[0] > reds[0] and reds[-1] > BLUE[0]


def test_motion_frames_are_played_one_per_frame_and_then_settle(tmp_path):
    a, b = picture(tmp_path, "a", RED), picture(tmp_path, "b", BLUE)
    moving = (picture(tmp_path, "m0", GREEN), picture(tmp_path, "m1", (0, 100, 0)))
    stream = io.BytesIO()
    write_frames(stream, [SlideFrames(1, 6, [Change(0, a), Change(2, b, motion=moving)])], SIZE)
    assert colors(stream.getvalue(), 6) == [RED, RED, GREEN, (0, 100, 0), BLUE, BLUE]


def test_images_of_another_size_are_fitted_without_stretching(tmp_path):
    path = str(tmp_path / "tall.png")
    Image.new("RGB", (18, 18), RED).save(path)  # 正方形の絵を、横長の画面へ
    stream = io.BytesIO()
    write_frames(stream, [SlideFrames(1, 1, [Change(0, path)])], SIZE, background=(255, 255, 255))
    frame = np.frombuffer(stream.getvalue(), dtype=np.uint8).reshape(SIZE[1], SIZE[0], 3)
    assert tuple(frame[9, 16]) == RED
    assert tuple(frame[9, 1]) == (255, 255, 255)  # 左右は余白
