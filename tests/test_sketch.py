"""絵を描くための下地(`sketch`)。

絵の中身(何を描くか)は題材の側にあるので、ここでは確かめない。確かめるのは、
どの題材でも崩れると困るところだけ:

* 画用紙が、資料の本文領域と同じ縦横比であること(図だけの画面に縮めずに収まる)
* ずらす・傾けるが、書いたとおりの場所に描くこと(ずれた絵は、正しく見える間違いになる)
* 日本語の文字が出ること(豆腐になっても、絵としては描けてしまう)
"""

import os

import pytest
from PIL import Image

from note2slides import sketch
from note2slides.sketch import Sketch, SketchError
from note2slides.style import Style

RED = (200, 0, 0)


def _has_japanese_font():
    try:
        sketch.font_path()
        return True
    except SketchError:
        return False


requires_font = pytest.mark.skipif(not _has_japanese_font(), reason="日本語の書体が見つかりません")


def ink(image, box, color=None):
    """`box`(左, 上, 右, 下)の中で、地の白ではない画素の数。色を指定すればその色だけ。"""
    region = image.crop(box).convert("RGB")
    counts = region.getcolors(region.width * region.height)
    if color is None:
        return sum(n for n, p in counts if p != (255, 255, 255))
    return sum(n for n, p in counts if p == color)


def test_default_canvas_matches_the_body_area_of_a_slide():
    style = Style()
    sk = Sketch()
    assert sk.width / sk.height == pytest.approx(style.body_width / style.body_height, rel=0.01)
    assert sk.image().size == (sk.width, sk.height)


def test_shapes_are_drawn_where_they_are_written():
    sk = Sketch(400, 200)
    sk.rect(100, 50, 80, 40, fill=RED)
    image = sk.image()
    assert ink(image, (105, 55, 175, 85), RED) == 70 * 30
    assert ink(image, (0, 0, 95, 200)) == 0
    assert ink(image, (185, 0, 400, 200)) == 0


def test_at_moves_and_scales_but_keeps_what_was_drawn():
    sk = Sketch(400, 200)
    with sk.at(200, 100, scale=0.5):
        sk.rect(0, 0, 80, 40, fill=RED)  # (200, 100) から 40 x 20 になる
    image = sk.image()
    assert ink(image, (203, 103, 237, 117), RED) == 34 * 14
    assert ink(image, (0, 0, 197, 200)) == 0
    assert ink(image, (243, 0, 400, 200)) == 0


def test_at_tilts_clockwise_around_its_origin():
    sk = Sketch(400, 400)
    with sk.at(200, 200, angle=90):
        sk.rect(50, -10, 100, 20, fill=RED)  # 右へ伸びる棒が、下へ伸びる棒になる
    image = sk.image()
    assert ink(image, (195, 255, 205, 345), RED) == 10 * 90
    assert ink(image, (215, 0, 400, 400)) == 0


def test_nested_at_applies_the_outer_frame_last():
    sk = Sketch(400, 200)
    with sk.at(100, 0):
        with sk.at(0, 100, scale=0.5):
            sk.rect(0, 0, 80, 40, fill=RED)
    assert ink(sk.image(), (103, 103, 137, 117), RED) == 34 * 14


def test_dashed_line_leaves_gaps():
    solid, dashed = Sketch(400, 100), Sketch(400, 100)
    solid.line([(20, 50), (380, 50)], RED, 6)
    dashed.line([(20, 50), (380, 50)], RED, 6, dash=(20, 20))
    row = (0, 49, 400, 51)
    assert 0 < ink(dashed.image(), row, RED) < ink(solid.image(), row, RED) * 0.7


def test_arrow_has_a_head_wider_than_its_shaft():
    sk = Sketch(400, 200)
    sk.arrow((50, 100), (350, 100), color=RED, width=6, head=40)
    image = sk.image()
    assert ink(image, (150, 80, 160, 120)) < ink(image, (318, 80, 328, 120))


@requires_font
def test_japanese_text_is_drawn_and_not_as_identical_boxes():
    """書体に字が無いと、どの字も同じ四角(豆腐)になる。違う字が違う形で出ることを見る。"""

    def glyph(ch):
        sk = Sketch(120, 120)
        sk.text((60, 60), ch, size=80, halo=False)
        return sk.image()

    a, b = glyph("輪"), glyph("軸")
    assert ink(a, (0, 0, 120, 120)) > 200
    assert a.tobytes() != b.tobytes()


@requires_font
def test_label_draws_a_leader_line_to_its_target():
    sk = Sketch(400, 200)
    sk.label((300, 50), "名前", (60, 160), color=RED, size=30)
    image = sk.image()
    assert ink(image, (50, 150, 70, 170)) > 100  # 指している先の点
    assert ink(image, (150, 90, 210, 130)) > 0  # 途中の線


def test_missing_font_is_reported_with_the_places_searched(monkeypatch):
    monkeypatch.setattr(sketch, "_FONT_CANDIDATES", {False: ["/no/such/font.ttc"], True: ["/no/such/font.ttc"]})
    with pytest.raises(SketchError) as error:
        sketch.font_path()
    assert "/no/such/font.ttc" in str(error.value)


def test_save_writes_a_png_of_the_stated_size(tmp_path):
    sk = Sketch(300, 150)
    sk.circle((150, 75), 40, fill=RED)
    path = sk.save(str(tmp_path / "out" / "figure.png"))
    with Image.open(path) as image:
        assert image.size == (300, 150)


# ---------------------------------------------------------------------------
# 層・段・動き(同じ絵を、話に合わせて何通りにも書き出す)
# ---------------------------------------------------------------------------

BLUE = (0, 0, 200)


def layered():
    """層の外に赤、層 `a` に青、層 `b` に緑を、重ならない場所に描いた絵。"""
    sk = Sketch(300, 100)
    sk.rect(10, 10, 60, 60, fill=RED)
    with sk.layer("a"):
        sk.rect(110, 10, 60, 60, fill=BLUE)
    with sk.layer("b"):
        sk.rect(210, 10, 60, 60, fill=(0, 150, 0))
    return sk


def test_layers_do_not_change_the_finished_picture():
    plain = Sketch(300, 100)
    plain.rect(10, 10, 60, 60, fill=RED)
    plain.rect(110, 10, 60, 60, fill=BLUE)
    plain.rect(210, 10, 60, 60, fill=(0, 150, 0))
    assert layered().image().tobytes() == plain.image().tobytes()


def test_show_picks_layers_and_keeps_what_was_drawn_outside_layers():
    image = layered().image(show=["a"])
    assert ink(image, (15, 15, 65, 65), RED) == 50 * 50  # 層の外は、いつも出る
    assert ink(image, (115, 15, 165, 65), BLUE) == 50 * 50
    assert ink(image, (200, 0, 300, 100)) == 0  # 選ばなかった層は出ない


def test_focus_fades_the_other_layers_but_does_not_remove_them():
    image = layered().image(focus=["a"])
    assert ink(image, (115, 15, 165, 65), BLUE) == 50 * 50
    faded = image.getpixel((240, 40))
    assert faded != (255, 255, 255) and faded != (0, 150, 0)
    assert faded[0] > 150  # 地の白に寄っている


def test_drawing_after_a_layer_is_on_top_of_it():
    sk = Sketch(100, 100)
    with sk.layer("under"):
        sk.rect(10, 10, 80, 80, fill=BLUE)
    sk.rect(30, 30, 40, 40, fill=RED)
    assert sk.image().getpixel((50, 50)) == RED
    assert sk.image(show=[]).getpixel((50, 50)) == RED  # 層を消しても、外で描いたものは残る


def test_reopening_a_layer_adds_to_the_same_layer():
    sk = Sketch(200, 100)
    with sk.layer("a"):
        sk.rect(10, 10, 40, 40, fill=BLUE)
    with sk.layer("b"):
        sk.rect(60, 10, 40, 40, fill=RED)
    with sk.layer("a"):
        sk.rect(110, 10, 40, 40, fill=BLUE)
    assert sk.layers() == ["a", "b"]
    image = sk.image(show=["a"])
    assert ink(image, (0, 0, 200, 100), BLUE) > 2 * 30 * 30
    assert ink(image, (0, 0, 200, 100), RED) == 0


def test_unknown_layer_name_is_reported_with_the_known_names():
    with pytest.raises(SketchError) as error:
        layered().image(show=["c"])
    assert "c" in str(error.value) and "a / b" in str(error.value)


def test_save_step_and_motion_use_the_names_the_video_looks_for(tmp_path):
    from note2slides import staging

    sk = layered()
    path = sk.save(str(tmp_path / "figure.png"))
    sk.save_step(path, 1, show=["a"])
    sk.save_step(path, 2, show=["a", "b"])
    sketch.save_motion(path, 2, [sk.image(show=["a"]), sk.image()])

    assert staging.figure_steps(path) == [str(tmp_path / "figure@1.png"), str(tmp_path / "figure@2.png")]
    assert sorted(p.name for p in (tmp_path / "figure@2").iterdir()) == ["000.png", "001.png"]

    sketch.clear_steps(path)  # 描き直す前に消す。段を減らしても、古い段が残らない
    assert staging.figure_steps(path) == []
    assert not (tmp_path / "figure@2").exists()
    assert (tmp_path / "figure.png").exists()


def test_ease_starts_and_ends_gently():
    assert sketch.ease(0) == 0 and sketch.ease(1) == 1
    assert sketch.ease(0.5) == pytest.approx(0.5)
    assert sketch.ease(0.1) < 0.1 and sketch.ease(0.9) > 0.9


# ---------------------------------------------------------------------------
# 段の並びと、段のあいだの動き(gen37)
# ---------------------------------------------------------------------------


def test_video_canvas_is_the_size_of_a_video_frame():
    assert sketch.VIDEO_SIZE == (1920, 1080)
    assert Sketch(*sketch.VIDEO_SIZE).image().size == (1920, 1080)


def test_levels_say_how_strongly_each_layer_is_drawn():
    sk = layered()
    assert sk.levels() == {"a": 1.0, "b": 1.0}
    assert sk.levels(show=["a"]) == {"a": 1.0, "b": 0.0}
    assert sk.levels(focus=["a"], faded=0.3) == {"a": 1.0, "b": 0.3}


def test_transition_ends_exactly_on_the_next_step():
    sk = layered()
    frames = sk.transition(dict(show=["a"]), dict(show=["a", "b"]), seconds=0.2, fps=30)
    assert len(frames) == 6
    assert frames[-1].tobytes() == sk.image(show=["a", "b"]).tobytes()  # 止まるときに跳ねない


def test_a_new_layer_rises_into_place_while_it_appears():
    sk = layered()
    frames = sk.transition(dict(show=["a"]), dict(show=["a", "b"]), seconds=0.4, rise=20, fps=30)
    early = frames[1]
    # まだ薄く、少し下にある(本来の場所の下の縁より下に、色が出ている)。
    assert ink(early, (210, 72, 270, 92)) > 0
    assert early.getpixel((240, 50)) != (0, 150, 0)
    # ほかの層と、層の外に描いたものは動かない。
    assert ink(early, (115, 15, 165, 65), BLUE) == 50 * 50
    assert ink(early, (15, 15, 65, 65), RED) == 50 * 50


def test_a_layer_that_only_changes_strength_stays_where_it_is():
    sk = layered()
    frames = sk.transition(dict(focus=["a"]), dict(focus=["b"]), seconds=0.2, rise=20, fps=30)
    for frame in frames:
        assert ink(frame, (100, 72, 180, 100)) == 0  # 下へずれていない
        assert ink(frame, (200, 72, 280, 100)) == 0


def test_save_steps_writes_the_pictures_and_the_motions_the_video_looks_for(tmp_path):
    from note2slides import staging

    sk = layered()
    path = str(tmp_path / "scene.png")
    sk.save_steps(path, [dict(show=["a"]), dict(show=["a", "b"])], opening=dict(show=[]), seconds=0.2)

    assert staging.figure_steps(path) == [str(tmp_path / "scene@1.png"), str(tmp_path / "scene@2.png")]
    with Image.open(staging.step_path(path, 0)) as opening:
        assert ink(opening, (100, 0, 300, 100)) == 0  # 層は 1 つも出ていない
    for step in (1, 2):  # 1 段目にも、最初の姿からの動きが付く
        assert len(os.listdir(staging.motion_dir(path, step))) == 6
    with Image.open(path) as final, Image.open(staging.step_path(path, 2)) as last:
        assert final.tobytes() == last.tobytes()  # 資料に載るのは、最後の段の姿


def test_save_steps_without_an_opening_gives_the_first_step_no_motion(tmp_path):
    from note2slides import staging

    sk = layered()
    path = str(tmp_path / "scene.png")
    sk.save_steps(path, [dict(show=["a"]), dict(show=["a", "b"], seconds=0)], seconds=0.2)
    assert not os.path.exists(staging.step_path(path, 0))
    assert not os.path.isdir(staging.motion_dir(path, 1))
    assert not os.path.isdir(staging.motion_dir(path, 2))  # seconds=0 は動きを付けない


def test_save_steps_accepts_pictures_and_motions_prepared_by_the_caller(tmp_path):
    from note2slides import staging

    sk = layered()
    path = str(tmp_path / "scene.png")
    other = Image.new("RGB", (300, 100), (1, 2, 3))
    sk.save_steps(path, [dict(show=["a"]), dict(image=other, motion=[other, other, other]), dict(show=["a", "b"])])
    with Image.open(staging.step_path(path, 2)) as image:
        assert image.getpixel((5, 5)) == (1, 2, 3)
    assert len(os.listdir(staging.motion_dir(path, 2))) == 3
    # 自分で用意した絵からは、層の動きを作れない(動画の側が溶かす)。
    assert not os.path.isdir(staging.motion_dir(path, 3))


def test_save_steps_removes_steps_left_from_a_previous_run(tmp_path):
    from note2slides import staging

    sk = layered()
    path = str(tmp_path / "scene.png")
    sk.save_steps(path, [dict(show=["a"]), dict(show=["a", "b"]), dict()], opening=dict(show=[]))
    sk.save_steps(path, [dict(show=["a"])])
    assert staging.figure_steps(path) == [str(tmp_path / "scene@1.png")]
    assert not os.path.exists(staging.step_path(path, 0))


@requires_font
def test_halo_can_take_the_colour_of_the_ground():
    dark = (20, 30, 50)
    sk = Sketch(200, 80, background=dark)
    sk.text((100, 40), "字", size=40, color=(255, 255, 255), halo=dark)
    colours = {pixel for _, pixel in sk.image().getcolors(200 * 80)}
    # 縁取りが地の色なので、白い字と地のあいだに別の色の輪ができない。
    assert all(min(c) >= 20 for c in colours)
    plain = Sketch(200, 80, background=dark)
    plain.text((100, 40), "字", size=40, color=(255, 0, 0), halo=True)
    assert (255, 255, 255) in {pixel for _, pixel in plain.image().getcolors(200 * 80)}
