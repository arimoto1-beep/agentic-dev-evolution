"""LibreOffice を実際に使って、記事 -> .pptx -> スライド画像を通しで確認する。

soffice が無い環境ではスキップする。実行に数十秒かかるため slow マークを付ける。
"""

import json
import os

import pytest
from PIL import Image

from note2slides import convert_file, export_slide_images
from note2slides.slide_images import ImageOptions
from note2slides.soffice import convert_to_pdf, find_soffice

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLE = os.path.join(_ROOT, "samples", "sample_article.md")

soffice = find_soffice()
requires_soffice = pytest.mark.skipif(
    soffice is None, reason="LibreOffice(soffice)が見つかりません"
)


@pytest.mark.slow
@requires_soffice
def test_sample_opens_in_libreoffice(tmp_path):
    pptx_path = str(tmp_path / "sample.pptx")
    deck = convert_file(_SAMPLE, pptx_path)

    pdf_path = convert_to_pdf(pptx_path, str(tmp_path), soffice=soffice)

    assert os.path.getsize(pdf_path) > 0
    with open(pdf_path, "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert len(deck.slides) > 1


@pytest.mark.slow
@requires_soffice
def test_sample_becomes_one_image_per_slide(tmp_path):
    pptx_path = str(tmp_path / "sample.pptx")
    deck = convert_file(_SAMPLE, pptx_path)
    outdir = tmp_path / "images"

    result = export_slide_images(
        pptx_path, str(outdir), ImageOptions(width=1280), soffice_path=soffice
    )

    # スライドの枚数と画像の枚数が一致し、順番も保たれる。
    assert result.count == len(deck.slides)
    assert result.warnings == []
    assert [i.filename for i in result.images] == [
        f"slide_{n:03d}.png" for n in range(1, len(deck.slides) + 1)
    ]
    for image in result.images:
        with Image.open(image.path) as opened:
            assert opened.size == (1280, 720)
            assert opened.mode == "RGB"
            # 白紙のまま出ていないこと(何かが描かれていること)を確認する。
            assert len(opened.convert("L").getcolors(maxcolors=256) or []) > 1

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["count"] == len(deck.slides)
    assert manifest["source"] == "sample.pptx"
