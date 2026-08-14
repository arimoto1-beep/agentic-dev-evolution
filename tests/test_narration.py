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
    """「（続き）」は画面上の目印。読み上げると「かっこ つづき」になってしまう。"""
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_CODE, title="変換を自動化する（続き）", code="echo hello")],
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "変換を自動化する"
    assert segment.title == "変換を自動化する（続き）"  # 一覧では元のタイトルを示す


def test_code_is_not_read_aloud(tmp_path):
    # コードをそのまま読み上げても聞き取れないため、タイトルだけを読む。
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_CODE, title="コード例", code="print('hello')\nexit(0)")],
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "コード例"
    assert "print" not in segment.text


def test_table_is_not_read_aloud(tmp_path):
    path = make_pptx(
        tmp_path,
        [Slide(kind=KIND_TABLE, title="対応表", table_header=["工程"], table_rows=[["画像化"]])],
    )

    segment = extract_script(path).segments[0]

    assert segment.text == "対応表"


def test_slide_without_text_becomes_silent(tmp_path):
    path = make_pptx(tmp_path, [Slide(kind=KIND_BULLETS, title="")])

    script = extract_script(path)

    assert script.segments[0].source == narration.SOURCE_NONE
    assert script.segments[0].is_empty
    assert any("無音" in w for w in script.warnings)


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
