"""スライド画像を並べた一覧(コンタクトシート)。

ここが引き受けるのは **見比べやすさだけ** で、崩れているかどうかの判定はしない
(何が正しい絵かを決められないため)。確認するのは、並べ方と番号の対応、
そして「縮めるだけで切り取らない」ことの 3 つ。
"""

from __future__ import annotations

import pytest

from note2slides.contact_sheet import (
    DEFAULT_COLUMNS,
    DEFAULT_ROWS,
    ContactSheetError,
    build_contact_sheets,
)

Image = pytest.importorskip("PIL.Image", reason="Pillow が必要")


def slides(tmp_path, count: int, size=(1920, 1080)):
    """連番のスライド画像を作る。1 枚ずつ違う色にして、並び順を見分けられるようにする。"""
    paths = []
    for i in range(count):
        path = tmp_path / f"slide_{i + 1:03d}.png"
        Image.new("RGB", size, (10 * i % 256, 40, 90)).save(path)
        paths.append(str(path))
    return paths


def test_slides_are_split_into_sheets_of_twelve(tmp_path):
    sheets = build_contact_sheets(slides(tmp_path, 44), str(tmp_path / "out"))

    assert [len(s.slides) for s in sheets] == [12, 12, 12, 8]
    assert DEFAULT_COLUMNS * DEFAULT_ROWS == 12


def test_every_slide_appears_exactly_once_in_order(tmp_path):
    sheets = build_contact_sheets(slides(tmp_path, 44), str(tmp_path / "out"))

    numbered = [n for sheet in sheets for n in sheet.slides]

    assert numbered == list(range(1, 45))


def test_the_numbers_shown_are_the_slide_numbers(tmp_path):
    """番号は 1 から数え直さない。見つけたものを指させるのが目的。"""
    sheets = build_contact_sheets(
        slides(tmp_path, 3), str(tmp_path / "out"), numbers=[7, 8, 9]
    )

    assert sheets[0].slides == [7, 8, 9]


def test_the_aspect_ratio_is_kept(tmp_path):
    """縮めるだけで切り取らない。はみ出しを探す一覧で切り取ると、探すものが消える。"""
    sheets = build_contact_sheets(slides(tmp_path, 1), str(tmp_path / "out"))

    with Image.open(sheets[0].path) as sheet:
        width, height = sheet.size
    # 4 列 3 行ぶんの枠が、16:9 の升目として確保されている。
    assert width > height
    assert (sheets[0].width, sheets[0].height) == (width, height)


def test_a_sheet_is_written_for_every_chunk(tmp_path):
    sheets = build_contact_sheets(slides(tmp_path, 13), str(tmp_path / "out"))

    assert len(sheets) == 2
    for sheet in sheets:
        with Image.open(sheet.path) as image:
            assert image.size == (sheet.width, sheet.height)


def test_the_grid_can_be_changed(tmp_path):
    sheets = build_contact_sheets(
        slides(tmp_path, 6), str(tmp_path / "out"), columns=2, rows=2
    )

    assert [len(s.slides) for s in sheets] == [4, 2]


def test_nothing_to_show_is_an_error_not_an_empty_sheet(tmp_path):
    with pytest.raises(ContactSheetError):
        build_contact_sheets([], str(tmp_path / "out"))


def test_a_wrong_number_of_labels_is_an_error(tmp_path):
    """番号がずれた一覧は、無いより悪い(違う画面を指してしまう)。"""
    with pytest.raises(ContactSheetError):
        build_contact_sheets(slides(tmp_path, 3), str(tmp_path / "out"), numbers=[1, 2])
