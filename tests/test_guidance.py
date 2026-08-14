"""画面に出ているものの案内文を確認する。

ここで確かめたいのは 2 つ。視聴者が画面のどこを見ればよいか分かること、
そして画面に書いていないことを足していないこと。
"""

from note2slides import guidance


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------


def test_table_reads_columns_then_rows():
    text = guidance.describe_table(
        ["工程", "入力", "出力"],
        [["資料生成", "Markdown 記事", "プレゼンテーション"]],
    )

    assert text == (
        "画面の表をご覧ください。\n"
        "列は左から、工程、入力、出力です。\n"
        "資料生成の行は、入力がMarkdown 記事、出力がプレゼンテーションです。"
    )


def test_table_rows_are_read_in_order():
    text = guidance.describe_table(["工程", "出力"], [["画像化", "画像"], ["動画生成", "動画"]])

    assert text.splitlines()[2:] == [
        "画像化の行は、出力が画像です。",
        "動画生成の行は、出力が動画です。",
    ]


def test_table_without_a_header_is_read_by_row_number():
    text = guidance.describe_table([], [["あ", "い"], ["う", "え"]])

    assert text.splitlines() == [
        "画面の表をご覧ください。",
        "1行目は、あ、いです。",
        "2行目は、う、えです。",
    ]


def test_single_column_table_is_read_as_a_list():
    text = guidance.describe_table(["項目"], [["あ"], ["い"]])

    assert text.splitlines()[-1] == "あ、いが並んでいます。"


def test_empty_cells_are_skipped():
    text = guidance.describe_table(["工程", "入力", "出力"], [["画像化", "", "画像"]])

    assert text.splitlines()[-1] == "画像化の行は、出力が画像です。"


def test_row_without_a_label_falls_back_to_its_number():
    text = guidance.describe_table(["工程", "出力"], [["", "画像"]])

    assert text.splitlines()[-1] == "1行目は、出力が画像です。"


def test_cell_line_breaks_do_not_break_the_sentence():
    text = guidance.describe_table(["工程", "出力"], [["画像化", "スライド\n画像"]])

    assert text.splitlines()[-1] == "画像化の行は、出力がスライド 画像です。"


def test_continued_table_says_so():
    text = guidance.describe_table(["工程"], [["画像化"]], continued=True)

    assert text.startswith("表の続きです。")


def test_empty_table_has_nothing_to_say():
    assert guidance.describe_table([], []) == ""


# ---------------------------------------------------------------------------
# コード
# ---------------------------------------------------------------------------


def test_code_points_at_the_screen_with_its_language():
    text = guidance.describe_code("x = 1\ny = 2", "python")

    assert text == "画面のコードをご覧ください。\nパイソンのコードを2行示しています。"


def test_shell_code_is_called_a_command():
    assert guidance.describe_code("ls", "bash") == "画面のコマンドをご覧ください。"


def test_unknown_language_is_not_spoken():
    """合成エンジンが読めない綴りをそのまま渡すと、意味の取れない音になる。"""
    text = guidance.describe_code("a\nb", "brainfuck")

    assert text == "画面のコードをご覧ください。\nコードを2行示しています。"


def test_code_itself_is_not_read():
    text = guidance.describe_code("print('hello')", "python")

    assert "print" not in text


def test_japanese_comments_are_read():
    text = guidance.describe_code("# 記事から資料を作る\nnote2slides a.md", "bash")

    assert text.splitlines()[-1] == "記事から資料を作る"


def test_comments_without_japanese_are_not_read():
    text = guidance.describe_code("#!/bin/sh\n// TODO: fix later\nls", "bash")

    assert "bin" not in text and "TODO" not in text


def test_continued_code_says_so():
    assert guidance.describe_code("ls", "bash", continued=True).startswith("コマンドの続きです。")


def test_empty_code_has_nothing_to_say():
    assert guidance.describe_code("\n  \n") == ""


def test_hold_grows_with_the_number_of_lines():
    short = guidance.hold_for_code("ls")
    long = guidance.hold_for_code("\n".join("line" for _ in range(20)))

    assert 0 < short < long <= guidance.CODE_HOLD_MAX
    assert guidance.hold_for_code("") == 0.0


# ---------------------------------------------------------------------------
# 図
# ---------------------------------------------------------------------------


def test_image_points_at_the_screen():
    assert guidance.describe_image() == "画面の図をご覧ください。"
    assert guidance.hold_for_image() > 0
