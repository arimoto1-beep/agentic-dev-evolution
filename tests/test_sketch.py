"""絵を描くための下地(`sketch`)。

絵の中身(何を描くか)は題材の側にあるので、ここでは確かめない。確かめるのは、
どの題材でも崩れると困るところだけ:

* 画用紙が、資料の本文領域と同じ縦横比であること(図だけの画面に縮めずに収まる)
* ずらす・傾けるが、書いたとおりの場所に描くこと(ずれた絵は、正しく見える間違いになる)
* 日本語の文字が出ること(豆腐になっても、絵としては描けてしまう)
"""

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
