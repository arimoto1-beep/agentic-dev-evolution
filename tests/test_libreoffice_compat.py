"""LibreOffice で実際に開けることを確認する。

soffice が無い環境ではスキップする。実行に数秒かかるため slow マークを付ける。
"""

import importlib.util
import os
import sys

import pytest

from note2slides import convert_file

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLE = os.path.join(_ROOT, "samples", "sample_article.md")


def _load_preview_module():
    path = os.path.join(_ROOT, "tools", "preview_pptx.py")
    spec = importlib.util.spec_from_file_location("preview_pptx", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["preview_pptx"] = module
    spec.loader.exec_module(module)
    return module


preview = _load_preview_module()
soffice = preview.find_soffice()


@pytest.mark.slow
@pytest.mark.skipif(soffice is None, reason="LibreOffice(soffice)が見つかりません")
def test_sample_opens_in_libreoffice(tmp_path):
    pptx_path = str(tmp_path / "sample.pptx")
    deck = convert_file(_SAMPLE, pptx_path)

    pdf_path = preview.pptx_to_pdf(soffice, pptx_path, str(tmp_path))

    assert os.path.getsize(pdf_path) > 0
    with open(pdf_path, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"
    assert len(deck.slides) > 1
