"""資料からナレーション原稿を取り出す処理を確認する。

外部ツールは使わず、renderer で作った .pptx をそのまま読む。
"""

import json

import pytest
from pptx import Presentation

from note2slides import narration
from note2slides.model import (
    KIND_BULLETS,
    KIND_CODE,
    KIND_IMAGE,
    KIND_SECTION,
    KIND_TABLE,
    KIND_TITLE,
    Bullet,
    Deck,
    Run,
    Slide,
)
from note2slides.narration import (
    NarrationError,
    NarrationScript,
    NarrationSegment,
    extract_script,
    read_script,
)
from note2slides.renderer import render_deck


def make_pptx(tmp_path, slides, name="deck.pptx"):
    path = str(tmp_path / name)
    render_deck(Deck(slides=slides, title="タイトル"), path)
    return path


def bullets(*texts, **kwargs):
    return Slide(
        kind=KIND_BULLETS,
        bullets=[Bullet(runs=[Run(t)]) for t in texts],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 読み上げ元の選び方
# ---------------------------------------------------------------------------


def test_notes_are_used_as_the_script(tmp_path):
    path = make_pptx(
        tmp_path,
        [bullets("要点です。", title="見出し", notes="元の本文です。ここを読み上げます。")],
    )

    script = extract_script(path)

    assert script.count == 1
    segment = script.segments[0]
    assert segment.index == 1
    assert segment.title == "見出し"
    assert segment.source == narration.SOURCE_NOTES
    assert segment.text == "元の本文です。ここを読み上げます。"


def test_body_is_used_when_there_are_no_notes(tmp_path):
    path = make_pptx(tmp_path, [bullets("1 つ目の項目", "2 つ目の項目", title="見出し")])

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_BODY
    # 画面に出ている文字だけを、出ている順に読む(補足は足さない)。
    assert segment.text == "見出し\n1 つ目の項目\n2 つ目の項目"


def test_title_slide_reads_title_and_subtitle(tmp_path):
    path = make_pptx(
        tmp_path, [Slide(kind=KIND_TITLE, title="講座のタイトル", subtitle="副題です")]
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "講座のタイトル\n副題です"


def test_section_slide_reads_its_title(tmp_path):
    path = make_pptx(tmp_path, [Slide(kind=KIND_SECTION, title="第 1 章")])

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_TITLE
    assert segment.text == "第 1 章"


def test_continuation_marker_is_not_read_aloud(tmp_path):
    """「（続き）」は画面上の目印。読み上げると「かっこ つづき」になってしまう。

    見出しは前のスライドで読み上げているので、続きのスライドでは繰り返さない。
    """
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_BULLETS, title="変換を自動化する（続き）", bullets=[Bullet([Run("要点")])])],
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "要点"
    assert "続き" not in segment.text
    assert segment.title == "変換を自動化する（続き）"  # 一覧では元のタイトルを示す


def test_title_is_read_when_nothing_else_is_on_the_slide(tmp_path):
    path = make_pptx(tmp_path, [Slide(kind=KIND_SECTION, title="変換を自動化する（続き）")])

    segment = extract_script(path).segments[0]

    assert segment.text == "変換を自動化する"


# ---------------------------------------------------------------------------
# 画面に出ているものの案内
# ---------------------------------------------------------------------------


def test_code_slide_points_at_the_screen(tmp_path):
    """コードは 1 文字ずつ読んでも聞き取れない。何が出ているかを伝えて画面へ導く。"""
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_CODE, title="コード例", code="print('hello')\nexit(0)", code_lang="python")],
    )

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_SCREEN
    assert segment.text == "コード例\n画面のコードをご覧ください。\nパイソンのコードを2行示しています。"
    assert "print" not in segment.text
    # 画面のコードを読む時間を取る(そのぶんスライドが長く映る)。
    assert segment.hold > 0


def test_shell_code_is_called_a_command(tmp_path):
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_CODE, title="実行する", code="note2slides article.md", code_lang="bash")],
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "実行する\n画面のコマンドをご覧ください。"


def test_japanese_comments_in_code_are_read(tmp_path):
    """コメントは書き手が読み手に向けて書いた文なので、そのまま読み上げる。"""
    path = make_pptx(
        tmp_path,
        [
            Slide(
                kind=KIND_CODE,
                title="",
                code="#!/bin/sh\n# 記事から資料を作る\nnote2slides article.md",
                code_lang="bash",
            )
        ],
    )

    segment = extract_script(path).segments[0]

    assert "記事から資料を作る" in segment.text
    assert "bin/sh" not in segment.text  # 日本語を含まないコメントは読み上げない


def test_table_is_read_row_by_row(tmp_path):
    """表を映すだけでは、視聴者は何を読み取ればよいのか分からない。"""
    path = make_pptx(
        tmp_path,
        [
            Slide(
                kind=KIND_TABLE,
                title="対応表",
                table_header=["工程", "入力", "出力"],
                table_rows=[["画像化", "資料", "スライド画像"]],
            )
        ],
    )

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_SCREEN
    assert segment.text == (
        "対応表\n"
        "画面の表をご覧ください。\n"
        "列は左から、工程、入力、出力です。\n"
        "画像化の行は、入力が資料、出力がスライド画像です。"
    )


def test_split_table_says_it_continues(tmp_path):
    path = make_pptx(
        tmp_path,
        [
            Slide(
                kind=KIND_TABLE,
                title="対応表（続き）",
                table_header=["工程", "入力"],
                table_rows=[["画像化", "資料"]],
                continued=True,
            )
        ],
    )

    segment = extract_script(path).segments[0]

    assert segment.text.startswith("表の続きです。")


def test_image_slide_points_at_the_screen_and_reads_its_caption(tmp_path):
    from PIL import Image as PILImage

    png = tmp_path / "figure.png"
    PILImage.new("RGB", (320, 180), (230, 230, 230)).save(png)
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_IMAGE, title="構成図", image_path=str(png), image_alt="全体の流れ")],
    )

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_SCREEN
    assert segment.text == "構成図\n画面の図をご覧ください。\n全体の流れ"
    assert segment.hold > 0


def test_notes_take_priority_over_the_screen_guidance(tmp_path):
    """人が書いたノートがあれば、それをそのまま読む(案内文で上書きしない)。"""
    path = make_pptx(
        tmp_path,
        [
            Slide(
                kind=KIND_TABLE,
                title="対応表",
                table_header=["工程"],
                table_rows=[["画像化"]],
                notes="工程ごとの対応をまとめました。",
            )
        ],
    )

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_NOTES
    assert segment.text == "工程ごとの対応をまとめました。"


def test_slide_without_text_becomes_silent(tmp_path):
    path = make_pptx(tmp_path, [Slide(kind=KIND_BULLETS, title="")])

    script = extract_script(path)

    assert script.segments[0].source == narration.SOURCE_NONE
    assert script.segments[0].is_empty
    assert any("無音" in w for w in script.warnings)


# ---------------------------------------------------------------------------
# ノートの指示行(読み上げないこと・画面を見せる時間)
# ---------------------------------------------------------------------------


def test_notes_can_ask_for_time_to_look_at_the_screen(tmp_path):
    notes = narration.compose_notes("図の説明です。", hold=2.5)
    path = make_pptx(tmp_path, [bullets("画面の文字", title="見出し", notes=notes)])

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_NOTES
    assert segment.text == "図の説明です。"  # 指示行は読み上げに入らない
    assert segment.hold == 2.5


def test_a_slide_marked_as_not_narrated_stays_silent(tmp_path):
    """読まないと決めた画面で、画面の文字を代わりに読み上げないこと。"""
    path = make_pptx(
        tmp_path,
        [bullets("画面の文字", title="見出し", notes=narration.compose_notes("", hold=4))],
    )

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_NONE
    assert segment.is_empty
    assert segment.hold == 4


def test_a_note_without_a_directive_line_is_read_as_written(tmp_path):
    path = make_pptx(tmp_path, [bullets("画面", title="見出し", notes="ふつうのノートです。")])

    segment = extract_script(path).segments[0]

    assert segment.source == narration.SOURCE_NOTES
    assert segment.hold == 0


def test_composing_notes_only_adds_a_line_when_needed():
    assert narration.compose_notes("読み上げる文章。") == "読み上げる文章。"
    assert narration.compose_notes("読み上げる文章。", hold=3) == (
        "読み上げる文章。\n[note2slides] hold=3"
    )
    assert narration.compose_notes("") == "[note2slides] narration=none"


def test_an_unknown_directive_says_what_can_be_written(tmp_path):
    path = make_pptx(tmp_path, [bullets("画面", notes="文章。\n[note2slides] speed=2")])

    with pytest.raises(NarrationError) as error:
        extract_script(path)

    assert "speed=2" in str(error.value)
    assert "hold" in str(error.value)


def test_a_directive_narration_can_only_say_none(tmp_path):
    """読み上げる文章は本文に書くもので、指示行には書けない。"""
    path = make_pptx(tmp_path, [bullets("画面", notes="[note2slides] narration=あとで")])

    with pytest.raises(NarrationError) as error:
        extract_script(path)

    assert "narration" in str(error.value)


def test_a_directive_hold_must_be_a_number(tmp_path):
    path = make_pptx(tmp_path, [bullets("画面", notes="文章。\n[note2slides] hold=ゆっくり")])

    with pytest.raises(NarrationError) as error:
        extract_script(path)

    assert "hold" in str(error.value)


# ---------------------------------------------------------------------------
# スライドとの対応
# ---------------------------------------------------------------------------


def test_index_follows_slide_order(tmp_path):
    path = make_pptx(
        tmp_path,
        [
            Slide(kind=KIND_TITLE, title="表紙"),
            bullets("2 枚目", title="二"),
            bullets("3 枚目", title="三"),
        ],
    )

    script = extract_script(path)

    assert [s.index for s in script.segments] == [1, 2, 3]
    assert "2 枚目" in script.segments[1].text
    assert "3 枚目" in script.segments[2].text


def test_hidden_slides_are_skipped(tmp_path):
    # 非表示スライドはスライド画像にも出ないため、番号がずれないよう原稿からも外す。
    path = make_pptx(tmp_path, [bullets("表示"), bullets("非表示"), bullets("表示 2")])
    presentation = Presentation(path)
    presentation.slides[1].element.set("show", "0")
    presentation.save(path)

    script = extract_script(path)

    assert script.count == 2
    assert [s.index for s in script.segments] == [1, 2]
    assert "非表示" not in script.segments[1].text
    assert any("非表示" in w for w in script.warnings)


# ---------------------------------------------------------------------------
# 原稿ファイル
# ---------------------------------------------------------------------------


def test_script_round_trip(tmp_path):
    path = make_pptx(tmp_path, [bullets("要点", title="見出し", notes="本文です。")])
    script = extract_script(path)
    out = tmp_path / "script.json"

    script.write(str(out))
    loaded = read_script(str(out))

    assert [s.to_dict() for s in loaded.segments] == [s.to_dict() for s in script.segments]
    assert json.loads(out.read_text(encoding="utf-8"))["count"] == 1


def test_edited_script_can_be_used_as_input(tmp_path):
    out = tmp_path / "script.json"
    out.write_text(
        json.dumps(
            {"segments": [{"index": 1, "text": "読みを直した文章"}]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )

    script = extract_script(str(out))

    assert script.segments[0].text == "読みを直した文章"


def test_hold_survives_the_round_trip(tmp_path):
    """画面を読む時間も原稿に残す(手で直せるようにする)。"""
    path = make_pptx(tmp_path, [Slide(kind=KIND_CODE, title="例", code="ls", code_lang="bash")])
    out = tmp_path / "script.json"

    extract_script(path).write(str(out))
    loaded = read_script(str(out))

    assert loaded.segments[0].hold > 0
    assert json.loads(out.read_text(encoding="utf-8"))["segments"][0]["hold"] > 0


def test_hold_must_be_a_positive_number(tmp_path):
    out = tmp_path / "script.json"
    out.write_text(json.dumps({"segments": [{"index": 1, "text": "あ", "hold": -1}]}), "utf-8")

    with pytest.raises(NarrationError) as excinfo:
        read_script(str(out))

    assert "hold" in str(excinfo.value)


def test_script_index_must_be_continuous(tmp_path):
    out = tmp_path / "script.json"
    out.write_text(
        json.dumps({"segments": [{"index": 1, "text": "あ"}, {"index": 3, "text": "い"}]}),
        encoding="utf-8",
    )

    with pytest.raises(NarrationError) as excinfo:
        read_script(str(out))

    assert "連番" in str(excinfo.value)


def test_broken_script_reports_where(tmp_path):
    out = tmp_path / "script.json"
    out.write_text("{壊れている", encoding="utf-8")

    with pytest.raises(NarrationError) as excinfo:
        read_script(str(out))

    assert "JSON" in str(excinfo.value)


def test_empty_script_is_rejected(tmp_path):
    out = tmp_path / "script.json"
    out.write_text(json.dumps({"segments": []}), encoding="utf-8")

    with pytest.raises(NarrationError):
        read_script(str(out))


def test_failure_says_which_slide_it_was(tmp_path, monkeypatch):
    """どの 1 枚で止まったか分からないと、資料のどこを直せばよいか調べようがない。"""
    path = make_pptx(tmp_path, [bullets("一"), bullets("二", title="壊れた見出し")])

    def explode(slide, number):
        if number == 2:
            raise ValueError("読めない図形")
        return NarrationSegment(number)

    monkeypatch.setattr(narration, "_segment_of", explode)

    with pytest.raises(NarrationError) as excinfo:
        extract_script(path)

    message = str(excinfo.value)
    assert "スライド 2" in message
    assert "壊れた見出し" in message
    assert "読めない図形" in message


def test_unsupported_input(tmp_path):
    source = tmp_path / "article.md"
    source.write_text("# 記事", encoding="utf-8")

    with pytest.raises(NarrationError) as excinfo:
        extract_script(str(source))

    assert "未対応" in str(excinfo.value)


def test_missing_input(tmp_path):
    with pytest.raises(NarrationError) as excinfo:
        extract_script(str(tmp_path / "none.pptx"))

    assert "見つかりません" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 文字の整え方
# ---------------------------------------------------------------------------


def test_clean_only_touches_whitespace():
    assert narration._clean("  前後の空白  ") == "前後の空白"
    assert narration._clean("1 行目\n\n2 行目") == "1 行目\n2 行目"
    assert narration._clean("行内\v改行") == "行内\n改行"
    # 文字そのものは書き換えない。
    assert narration._clean("記号（）や 30% はそのまま") == "記号（）や 30% はそのまま"


def test_segment_from_dict_requires_text_string():
    with pytest.raises(NarrationError):
        NarrationSegment.from_dict({"index": 1, "text": 12}, 1)


def test_script_to_dict_keeps_order():
    script = NarrationScript(
        segments=[NarrationSegment(1, "あ"), NarrationSegment(2, "い")], source="deck.pptx"
    )

    data = script.to_dict()

    assert data["source"] == "deck.pptx"
    assert [s["index"] for s in data["segments"]] == [1, 2]
