from note2slides.markdown_reader import parse_article
from note2slides.model import KIND_BULLETS, KIND_CODE, KIND_SECTION, KIND_TABLE, KIND_TITLE, NUMBER, QUOTE, Run
from note2slides.planner import PlannerOptions, plan_deck, split_sentences


def _plan(text, **kwargs):
    return plan_deck(parse_article(text), options=PlannerOptions(**kwargs))


def _kinds(deck):
    return [s.kind for s in deck.slides]


def test_title_slide_uses_leading_heading():
    deck = _plan("# 記事タイトル\n\n## 節\n\n本文。\n")
    assert deck.title == "記事タイトル"
    assert deck.slides[0].kind == KIND_TITLE
    assert deck.slides[0].title == "記事タイトル"
    # 表紙と同じ見出しは本文側に重複させない
    assert KIND_SECTION not in _kinds(deck)


def test_front_matter_title_wins_and_duplicate_heading_removed():
    deck = _plan("---\ntitle: 記事タイトル\n---\n\n# 記事タイトル\n\n本文。\n")
    assert deck.title == "記事タイトル"
    assert _kinds(deck) == [KIND_TITLE, KIND_BULLETS]


def test_differing_leading_heading_is_kept_as_section():
    deck = _plan("---\ntitle: 別のタイトル\n---\n\n# 記事の見出し\n\n本文。\n")
    assert deck.title == "別のタイトル"
    assert _kinds(deck) == [KIND_TITLE, KIND_SECTION, KIND_BULLETS]
    assert deck.slides[1].title == "記事の見出し"


def test_subtitle_from_front_matter():
    deck = _plan("---\ntitle: T\nsubtitle: S\n---\n\n本文。\n")
    assert deck.slides[0].subtitle == "S"


def test_heading_starts_new_slide():
    deck = _plan("## A\n\nあ。\n\n## B\n\nい。\n", title_slide=False)
    assert [s.title for s in deck.slides] == ["A", "B"]


def test_paragraph_is_split_into_sentences():
    deck = _plan("## A\n\n一つ目です。二つ目です。\n", title_slide=False)
    assert [b.text for b in deck.slides[0].bullets] == ["一つ目です。", "二つ目です。"]


def test_sentence_split_can_be_disabled():
    deck = _plan("## A\n\n一つ目です。二つ目です。\n", title_slide=False, split_sentences=False)
    assert [b.text for b in deck.slides[0].bullets] == ["一つ目です。二つ目です。"]


def test_bullet_kinds():
    deck = _plan("## A\n\n1. 番号\n\n> 引用。\n", title_slide=False)
    kinds = [b.kind for b in deck.slides[0].bullets]
    assert NUMBER in kinds and QUOTE in kinds


def test_slide_break_forces_new_slide():
    deck = _plan("## A\n\nあ。\n\n---\n\nい。\n", title_slide=False)
    assert len(deck.slides) == 2
    assert deck.slides[1].title.endswith("（続き）")


def test_long_section_is_paginated():
    body = "".join(f"これは{i}番目の説明文です。" for i in range(40))
    deck = _plan(f"## 長い節\n\n{body}\n", title_slide=False)
    assert len(deck.slides) > 1
    assert deck.slides[0].title == "長い節"
    assert all(s.title.startswith("長い節") for s in deck.slides)
    assert all(s.title.endswith("（続き）") for s in deck.slides[1:])
    # 分割しても本文は落とさない
    joined = "".join(b.text for s in deck.slides for b in s.bullets)
    assert joined == body


def test_code_block_becomes_own_slide():
    deck = _plan("## A\n\n```py\nx = 1\n```\n", title_slide=False)
    assert _kinds(deck) == [KIND_CODE]
    assert deck.slides[0].code == "x = 1"


def test_long_code_is_chunked():
    code = "\n".join(f"line{i}" for i in range(40))
    deck = _plan(f"## A\n\n```\n{code}\n```\n", title_slide=False, max_code_lines=10)
    assert len(deck.slides) == 4
    assert "\n".join(s.code for s in deck.slides) == code


def test_table_slide_and_row_chunking():
    rows = "\n".join(f"| {i} | {i} |" for i in range(25))
    deck = _plan(f"## A\n\n| a | b |\n| --- | --- |\n{rows}\n", title_slide=False, max_table_rows=10)
    assert _kinds(deck) == [KIND_TABLE] * 3
    assert all(s.table_header == ["a", "b"] for s in deck.slides)
    assert sum(len(s.table_rows) for s in deck.slides) == 25


def test_missing_image_becomes_note_and_warning():
    deck = _plan("## A\n\n![図の説明](missing.png)\n", title_slide=False)
    assert deck.warnings
    assert deck.slides[0].bullets[0].text == "[画像] 図の説明"


def test_notes_hold_original_text():
    deck = _plan("## A\n\n一つ目です。二つ目です。\n", title_slide=False)
    assert deck.slides[0].notes == "一つ目です。二つ目です。"


def test_notes_can_be_disabled():
    deck = _plan("## A\n\n本文。\n", title_slide=False, include_notes=False)
    assert deck.slides[0].notes == ""


def test_notes_only_cover_the_bullets_on_that_slide():
    """ナレーションは、そのスライドに出ている内容と対応している必要がある。

    まとめて 1 枚目のノートに入れると、まだ画面に出ていない文を読み上げてしまう。
    """
    body = "".join(f"これは{i}番目の説明文です。" for i in range(40))
    deck = _plan(f"## 長い節\n\n{body}\n", title_slide=False)

    assert len(deck.slides) > 1
    for slide in deck.slides:
        shown = "".join(b.text for b in slide.bullets)
        assert slide.notes.replace(" ", "") == shown
    assert "".join(s.notes.replace(" ", "") for s in deck.slides) == body


def test_notes_of_a_list_keep_one_item_per_line():
    deck = _plan("## A\n\n- 一つ目\n- 二つ目\n", title_slide=False)

    assert deck.slides[0].notes == "一つ目\n二つ目"


def test_split_table_and_code_are_marked_as_continued():
    """「続き」かどうかは、見出しの重なりではなく分割そのもので決める。"""
    rows = "\n".join(f"| {i} | {i} |" for i in range(15))
    deck = _plan(f"## A\n\n| a | b |\n| --- | --- |\n{rows}\n", title_slide=False, max_table_rows=10)

    assert [s.continued for s in deck.slides] == [False, True]


def test_a_table_after_a_paragraph_is_not_continued():
    deck = _plan("## A\n\n本文。\n\n| a |\n| --- |\n| 1 |\n", title_slide=False)

    assert _kinds(deck) == [KIND_BULLETS, KIND_TABLE]
    # 見出しが同じで「（続き）」が付いていても、表そのものは続きではない。
    assert deck.slides[1].title.endswith("（続き）")
    assert deck.slides[1].continued is False


def test_deck_title_option_overrides():
    deck = _plan("# 元タイトル\n\n本文。\n", deck_title="指定タイトル")
    assert deck.slides[0].title == "指定タイトル"
    assert deck.slides[1].kind == KIND_SECTION  # 見出しは残る


class TestSplitSentences:
    def test_japanese_period(self):
        assert [_t(s) for s in split_sentences([Run("あ。い。")])] == ["あ。", "い。"]

    def test_closing_bracket_is_attached(self):
        assert [_t(s) for s in split_sentences([Run("「あ。」い。")])] == ["「あ。」", "い。"]

    def test_decimal_point_is_not_a_boundary(self):
        assert [_t(s) for s in split_sentences([Run("値は 3.5 です。")])] == ["値は 3.5 です。"]

    def test_english_sentences(self):
        assert [_t(s) for s in split_sentences([Run("One. Two.")])] == ["One.", "Two."]

    def test_styles_are_preserved_across_split(self):
        runs = [Run("太字です。", bold=True), Run("普通です。")]
        result = split_sentences(runs)
        assert len(result) == 2
        assert result[0][0].bold is True
        assert result[1][0].bold is False

    def test_split_inside_a_styled_run(self):
        result = split_sentences([Run("一つ目。二つ目。", bold=True)])
        assert [_t(s) for s in result] == ["一つ目。", "二つ目。"]
        assert all(r.bold for s in result for r in s)

    def test_text_is_never_altered(self):
        source = "あいう。かきく！さしす？たちつ。"
        assert "".join(_t(s) for s in split_sentences([Run(source)])) == source


def _t(runs):
    return "".join(r.text for r in runs)
