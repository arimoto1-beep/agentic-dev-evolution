"""タイトルの折り返し位置。

見た目の良し悪しは数値では決まらないため、ここでは「守るべき決まり」
(禁則・幅・行数)と、「機械的な折り返しより自然になっていること」を確認する。
"""

import pytest

from note2slides import metrics
from note2slides.text_wrap import fit_lines, wrap_text

#: 表紙の条件(幅 11.33in / 40pt)を em にしたもの。
TITLE_EM = 11.33 * 72 / 40


def widths(lines):
    return [metrics.text_width_em(line) for line in lines]


def test_short_text_stays_on_one_line():
    assert wrap_text("教材シナリオから動画を作る", TITLE_EM) == ["教材シナリオから動画を作る"]


def test_every_line_fits_in_the_width():
    text = "生成AIを前提とした開発環境をゼロから作り直すための実践ガイド"
    lines = wrap_text(text, TITLE_EM)
    assert len(lines) == 2
    assert max(widths(lines)) <= TITLE_EM


def test_wrapped_lines_keep_the_original_text():
    text = "note記事から、eラーニング用の動画を自動で作る方法"
    assert "".join(wrap_text(text, TITLE_EM)) == text


def test_katakana_word_is_not_split():
    # 機械的な折り返しでは「プレゼンテー / ション」で切れる幅にする。
    lines = wrap_text("Pythonでプレゼンテーション資料を自動生成する", TITLE_EM)
    assert len(lines) == 2
    assert "プレゼンテーション" in lines[0]


def test_english_word_is_not_split():
    lines = wrap_text("Getting started with note2slides for e-learning videos", 32)
    assert all(not line.startswith(" ") for line in lines)
    for line in lines:
        for word in line.split():
            assert word in "Getting started with note2slides for e-learning videos"


@pytest.mark.parametrize("text", [
    "図をゆっくり見せたいときは、ここで秒数を指定します。設定に書いてください",
    "「まず動くものを作る」という進め方について(その2)",
    "開発環境そのものを改善しながらソフトウェアを作るという実験について、その進め方をまとめました",
])
def test_line_start_and_end_rules(text):
    lines = wrap_text(text, 14)
    assert len(lines) > 1
    for line in lines[1:]:
        assert line[0] not in "、。,.)）」』】!?！？ー・"
    for line in lines[:-1]:
        assert line[-1] not in "(（「『【"


def test_lines_are_reasonably_balanced():
    lines = wrap_text("開発環境そのものを改善しながらソフトウェアを作るという実験", TITLE_EM)
    assert len(lines) == 2
    shorter, longer = sorted(widths(lines))
    assert shorter >= longer * 0.5


def test_existing_line_breaks_are_kept():
    assert wrap_text("前半\n後半", TITLE_EM) == ["前半", "後半"]


def test_falls_back_when_no_natural_break_exists():
    # 切ってよい場所が無い文字列でも、幅で折り返して返す(諦めて 1 行にしない)。
    lines = wrap_text("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 6)
    assert len(lines) > 1
    assert "".join(lines) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def test_max_lines_is_respected():
    text = "開発環境そのものを改善しながらソフトウェアを作るという実験について、その進め方と観察結果をまとめました"
    assert len(wrap_text(text, TITLE_EM, max_lines=2)) == 2


def test_fit_lines_uses_font_size_and_width():
    text = "生成AIを前提とした開発環境をゼロから作り直すための実践ガイド"
    assert fit_lines(text, 40, 11.33 * 72) == wrap_text(text, TITLE_EM)
    # 小さい文字なら 1 行に収まる。
    assert len(fit_lines(text, 14, 11.33 * 72)) == 1


def test_empty_text_is_safe():
    assert wrap_text("", TITLE_EM) == [""]
    assert wrap_text("   ", TITLE_EM) == [""]
