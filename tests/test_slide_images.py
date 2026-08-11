"""スライド画像の書き出しを確認する。

LibreOffice を使わずに済むよう、PDF を直接組み立てて入力にする。
LibreOffice を経由する経路は tests/test_libreoffice_compat.py で確認する。
"""

import json
import os

import pypdfium2
import pytest
from PIL import Image

from note2slides import slide_images
from note2slides.slide_images import (
    ImageOptions,
    OutputExistsError,
    SlideImageError,
    export_slide_images,
    ffmpeg_pattern,
    render_pdf_to_images,
)


def make_pdf(path, pages=3, size=(960, 540)):
    """指定枚数のページを持つ PDF を作る(既定は 16:9 のスライド相当)。"""
    document = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        document.new_page(*size)
    document.save(str(path))
    document.close()
    return str(path)


class StubBitmap:
    def __init__(self, image):
        self._image = image

    def to_pil(self):
        return self._image


class StubPage:
    """_render_page の縮尺・余白の計算だけを確かめるための偽ページ。"""

    def __init__(self, width, height, color=(10, 20, 30)):
        self._width = width
        self._height = height
        self._color = color

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def render(self, scale=1):
        size = (round(self._width * scale), round(self._height * scale))
        return StubBitmap(Image.new("RGB", size, self._color))


# ---------------------------------------------------------------------------
# 出力ファイル
# ---------------------------------------------------------------------------


def test_pages_become_numbered_images(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=3)
    outdir = tmp_path / "out"

    images = render_pdf_to_images(pdf, str(outdir))

    assert [os.path.basename(i.path) for i in images] == [
        "slide_001.png",
        "slide_002.png",
        "slide_003.png",
    ]
    # 辞書順に並べてもスライドの順番と一致する。
    assert sorted(os.listdir(outdir)) == [i.filename for i in images]
    assert [i.index for i in images] == [1, 2, 3]


def test_images_are_full_hd_rgb_by_default(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)

    images = render_pdf_to_images(pdf, str(tmp_path / "out"))

    with Image.open(images[0].path) as image:
        assert image.size == (1920, 1080)
        # 動画側でそのまま扱えるよう、アルファは持たせない。
        assert image.mode == "RGB"
        assert image.format == "PNG"


def test_all_images_have_the_same_size(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=2, size=(960, 540))
    options = ImageOptions(width=1280)

    images = render_pdf_to_images(pdf, str(tmp_path / "out"), options)

    assert {(i.width, i.height) for i in images} == {(1280, 720)}
    for image in images:
        with Image.open(image.path) as opened:
            assert opened.size == (1280, 720)


def test_non_16_9_slides_are_padded_not_stretched():
    # 4:3 のページを 16:9 に収めると、左右に余白が付く。
    page = StubPage(720, 540, color=(10, 20, 30))

    image = slide_images._render_page(page, (1920, 1080), (255, 255, 255))

    assert image.size == (1920, 1080)
    assert image.getpixel((0, 540)) == (255, 255, 255)  # 左端は余白
    assert image.getpixel((960, 540)) == (10, 20, 30)  # 中央は元の内容
    assert image.getpixel((1919, 540)) == (255, 255, 255)  # 右端も余白
    # 縦は目一杯まで使う(見た目を必要以上に縮めない)。
    assert image.getpixel((960, 0)) == (10, 20, 30)


def test_16_9_slides_fill_the_frame():
    page = StubPage(960, 540, color=(10, 20, 30))

    image = slide_images._render_page(page, (1920, 1080), (255, 255, 255))

    assert image.size == (1920, 1080)
    assert image.getpixel((0, 0)) == (10, 20, 30)
    assert image.getpixel((1919, 1079)) == (10, 20, 30)


def test_jpeg_output(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)
    options = ImageOptions(fmt="jpg", width=640, height=360)

    images = render_pdf_to_images(pdf, str(tmp_path / "out"), options)

    assert images[0].path.endswith(".jpg")
    with Image.open(images[0].path) as image:
        assert image.format == "JPEG"
        assert image.size == (640, 360)


def test_prefix_and_digits_are_configurable(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)
    options = ImageOptions(prefix="frame", digits=5)

    images = render_pdf_to_images(pdf, str(tmp_path / "out"), options)

    assert images[0].filename == "frame00001.png"
    assert ffmpeg_pattern(options) == "frame%05d.png"


# ---------------------------------------------------------------------------
# 指定の検証
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        ImageOptions(fmt="bmp"),
        ImageOptions(width=1921),  # 奇数は動画化で扱えない
        ImageOptions(width=1920, height=1081),
        ImageOptions(width=8),
        ImageOptions(digits=0),
    ],
)
def test_invalid_options_are_rejected(options):
    with pytest.raises(SlideImageError):
        options.validate()


def test_missing_input(tmp_path):
    with pytest.raises(SlideImageError, match="見つかりません"):
        export_slide_images(str(tmp_path / "none.pptx"), str(tmp_path / "out"))


def test_unsupported_input(tmp_path):
    source = tmp_path / "article.md"
    source.write_text("# hello", encoding="utf-8")

    with pytest.raises(SlideImageError, match="未対応の入力形式"):
        export_slide_images(str(source), str(tmp_path / "out"))


def test_broken_pptx_is_rejected_before_conversion(tmp_path):
    # LibreOffice は壊れた .pptx をテキストとして読み込み、それらしい 1 枚を
    # 出してしまうことがある。気付けないまま動画になるのを避ける。
    source = tmp_path / "broken.pptx"
    source.write_text("これは pptx ではありません", encoding="utf-8")

    with pytest.raises(SlideImageError, match="資料として読み取れませんでした"):
        export_slide_images(str(source), str(tmp_path / "out"))

    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.png"))


def test_broken_pdf_reports_the_file(tmp_path):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not a pdf")

    with pytest.raises(SlideImageError, match="PDF を開けませんでした"):
        export_slide_images(str(source), str(tmp_path / "out"))


# ---------------------------------------------------------------------------
# 出力先の扱いと一覧ファイル
# ---------------------------------------------------------------------------


def test_manifest_lists_slides_in_order(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=3)
    outdir = tmp_path / "out"

    result = export_slide_images(pdf, str(outdir), ImageOptions(width=640, height=360))

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["count"] == 3
    assert manifest["width"] == 640 and manifest["height"] == 360
    assert manifest["format"] == "png"
    assert [s["index"] for s in manifest["slides"]] == [1, 2, 3]
    assert [s["file"] for s in manifest["slides"]] == [
        "slide_001.png",
        "slide_002.png",
        "slide_003.png",
    ]
    # 出力先ごと移動できるよう、パスは相対で書く。
    assert not os.path.isabs(manifest["slides"][0]["file"])


def test_existing_images_need_force(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)
    outdir = str(tmp_path / "out")
    export_slide_images(pdf, outdir)

    with pytest.raises(OutputExistsError, match="--force"):
        export_slide_images(pdf, outdir)

    result = export_slide_images(pdf, outdir, force=True)
    assert result.count == 1


def test_force_removes_leftover_images(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=3)
    outdir = tmp_path / "out"
    export_slide_images(pdf, str(outdir))

    short = make_pdf(tmp_path / "short.pdf", pages=1)
    result = export_slide_images(short, str(outdir), force=True)

    # 前回の 2 枚目以降が残っていると、動画側が古いスライドを拾ってしまう。
    assert sorted(p.name for p in outdir.glob("*.png")) == ["slide_001.png"]
    assert any("削除しました" in w for w in result.warnings)


def test_changing_format_clears_the_previous_images(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=2)
    outdir = tmp_path / "out"
    export_slide_images(pdf, str(outdir), ImageOptions(width=320))

    export_slide_images(pdf, str(outdir), ImageOptions(width=320, fmt="jpg"), force=True)

    assert sorted(p.name for p in outdir.glob("slide_*")) == ["slide_001.jpg", "slide_002.jpg"]


def test_other_files_are_left_alone(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)
    outdir = tmp_path / "out"
    outdir.mkdir()
    keep = outdir / "narration.txt"
    keep.write_text("原稿", encoding="utf-8")

    export_slide_images(pdf, str(outdir), force=True)

    assert keep.exists()


def test_progress_is_reported_per_slide(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=2)
    seen = []

    export_slide_images(pdf, str(tmp_path / "out"), on_progress=seen.append)

    assert [i.index for i in seen] == [1, 2]
