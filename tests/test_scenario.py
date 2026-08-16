"""教材シナリオの読み取りと、スライド構成への変換。

確認したいのは「書いたとおりになること」で、記事入力(test_planner.py)と違い、
構成を推測しないことが要点になる。
"""

from __future__ import annotations

import pytest

from note2slides import narration, scenario
from note2slides.model import (
    KIND_BULLETS,
    KIND_CODE,
    KIND_CONTENT,
    KIND_IMAGE,
    KIND_SECTION,
    KIND_TABLE,
    KIND_TITLE,
    PLAIN,
)
from note2slides.scenario import ScenarioError, build_deck, parse_scenario
from note2slides.style import Style

HEAD = "---\ntype: scenario\ntitle: サンプル\n---\n\n"

#: 資料に貼れる最小の PNG(1x1)。図のあるスライドを実際に描画するため。
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def scenario_of(body: str, source_path: str = "lesson.md"):
    return parse_scenario(HEAD + body, source_path=source_path)


def deck_of(body: str, source_path: str = "lesson.md"):
    return build_deck(scenario_of(body, source_path))


class TestWhatIsAScenario:
    def test_a_front_matter_declaration_marks_a_scenario(self, tmp_path):
        path = tmp_path / "lesson.md"
        path.write_text(HEAD + "## 見出し\n\n### 画面\n本文\n", encoding="utf-8")

        assert scenario.is_scenario_file(str(path))

    def test_an_article_is_not_a_scenario(self, tmp_path):
        path = tmp_path / "article.md"
        path.write_text("# 記事\n\n本文です。\n", encoding="utf-8")

        assert not scenario.is_scenario_file(str(path))

    def test_reading_an_article_as_a_scenario_says_what_is_missing(self):
        with pytest.raises(ScenarioError) as error:
            parse_scenario("# 記事\n\n本文です。\n", source_path="article.md")

        assert "type: scenario" in str(error.value)


class TestSlides:
    def test_each_heading_becomes_one_slide(self):
        deck = deck_of("## 一枚目\n\n### 画面\nA\n\n## 二枚目\n\n### 画面\nB\n")

        assert [s.title for s in deck.slides] == ["一枚目", "二枚目"]

    def test_the_screen_and_the_narration_are_kept_apart(self):
        deck = deck_of("## 見出し\n\n### 画面\n画面の文字\n\n### ナレーション\n読み上げる文章。\n")

        slide = deck.slides[0]
        assert [b.text for b in slide.bullets] == ["画面の文字"]
        assert slide.notes == "読み上げる文章。"

    def test_a_long_screen_is_not_split_into_two_slides(self):
        """記事入力と違い、収まらなくても分けない(分け方はシナリオが決める)。"""
        body = "## 見出し\n\n### 画面\n" + "".join(f"- {i} 番目の項目です\n" for i in range(30))

        deck = deck_of(body)

        assert len(deck.slides) == 1
        assert len(deck.slides[0].bullets) == 30
        assert any("収まらない" in w for w in deck.warnings)

    def test_a_paragraph_is_one_line_without_a_bullet_mark(self):
        deck = deck_of("## 見出し\n\n### 画面\n一つ目の段落。\n\n二つ目の段落。\n")

        assert [(b.kind, b.text) for b in deck.slides[0].bullets] == [
            (PLAIN, "一つ目の段落。"),
            (PLAIN, "二つ目の段落。"),
        ]

    def test_a_list_keeps_its_levels_and_numbers(self):
        deck = deck_of("## 見出し\n\n### 画面\n1. 一つ目\n2. 二つ目\n   - 中身\n")

        bullets = deck.slides[0].bullets
        assert [b.text for b in bullets] == ["一つ目", "二つ目", "中身"]
        assert [b.level for b in bullets] == [0, 0, 1]
        assert bullets[0].number == "1."

    def test_a_table_becomes_a_table_slide(self):
        deck = deck_of("## 見出し\n\n### 画面\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")

        slide = deck.slides[0]
        assert slide.kind == KIND_TABLE
        assert slide.table_header == ["A", "B"]
        assert slide.table_rows == [["1", "2"]]

    def test_a_code_block_becomes_a_code_slide(self):
        deck = deck_of("## 見出し\n\n### 画面\n```bash\nls -l\n```\n")

        slide = deck.slides[0]
        assert slide.kind == KIND_CODE
        assert slide.code == "ls -l"
        assert slide.code_lang == "bash"

    def test_headings_inside_a_code_block_are_screen_text(self):
        """画面に出すコードの中の `##` は、次のスライドの見出しではない。"""
        deck = deck_of("## 見出し\n\n### 画面\n```markdown\n## 例\n### 画面\n```\n")

        assert len(deck.slides) == 1
        assert deck.slides[0].code == "## 例\n### 画面"


class TestLayout:
    def test_a_cover_uses_the_screen_as_its_subtitle(self):
        deck = deck_of("## 題名\n\n### 設定\n- レイアウト: 表紙\n\n### 画面\n副題です\n")

        slide = deck.slides[0]
        assert slide.kind == KIND_TITLE
        assert slide.subtitle == "副題です"

    def test_a_section_shows_only_its_heading(self):
        deck = deck_of("## 章の名前\n\n### 設定\n- レイアウト: 章扉\n")

        assert deck.slides[0].kind == KIND_SECTION

    def test_a_section_with_screen_content_is_refused(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 章\n\n### 設定\n- レイアウト: 章扉\n\n### 画面\n本文\n")

        assert "章扉" in str(error.value)

    def test_an_unknown_layout_lists_the_ones_that_exist(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\n\n### 設定\n- レイアウト: 全画面\n")

        assert "全画面" in str(error.value)
        assert "章扉" in str(error.value)

    def test_english_names_can_be_used(self):
        deck = deck_of("## Title\n\n### settings\n- layout: cover\n\n### screen\nsub\n\n### narration\nHello.\n")

        assert deck.slides[0].kind == KIND_TITLE
        assert deck.slides[0].notes == "Hello."


class TestImages:
    def test_an_image_becomes_an_image_slide_with_its_caption(self, tmp_path):
        (tmp_path / "figure.png").write_bytes(b"")
        body = "## 図の説明\n\n### 画面\n![流れの図](figure.png)\n\n### ナレーション\n図をご覧ください。\n"

        deck = deck_of(body, source_path=str(tmp_path / "lesson.md"))

        slide = deck.slides[0]
        assert slide.kind == KIND_IMAGE
        assert slide.image_path == str(tmp_path / "figure.png")
        assert slide.image_alt == "流れの図"
        assert slide.notes == "図をご覧ください。"

    def test_a_missing_image_stops_with_the_path_it_looked_for(self, tmp_path):
        body = "## 図\n\n### 画面\n![図](none.png)\n"

        with pytest.raises(ScenarioError) as error:
            deck_of(body, source_path=str(tmp_path / "lesson.md"))

        assert "none.png" in str(error.value)


class TestOneScreenWithSeveralThings:
    """1 つの画面に、文章と図・表・コードを一緒に置けること。

    ナレーションは画面ごとに 1 つなので、中身が増えても「この画面ではこう説明
    する」という対応は変わらない。
    """

    def test_text_and_a_figure_live_on_one_screen(self, tmp_path):
        (tmp_path / "figure.png").write_bytes(PNG)
        body = (
            "## 図の画面\n\n### 画面\n図の見方を説明します。\n\n"
            "![流れの図](figure.png)\n\n"
            "### ナレーション\n図の左から順に見ていきます。\n"
        )

        slide = deck_of(body, source_path=str(tmp_path / "lesson.md")).slides[0]

        assert slide.kind == KIND_CONTENT
        assert [p.kind for p in slide.parts] == [KIND_BULLETS, KIND_IMAGE]
        assert [b.text for b in slide.parts[0].bullets] == ["図の見方を説明します。"]
        assert slide.parts[1].image_path == str(tmp_path / "figure.png")
        assert slide.notes == "図の左から順に見ていきます。"

    def test_the_order_on_the_screen_is_the_order_in_the_scenario(self, tmp_path):
        (tmp_path / "figure.png").write_bytes(PNG)
        body = "## 図\n\n### 画面\n![図](figure.png)\n\n図の下に置く補足です。\n"

        slide = deck_of(body, source_path=str(tmp_path / "lesson.md")).slides[0]

        assert [p.kind for p in slide.parts] == [KIND_IMAGE, KIND_BULLETS]

    def test_a_table_can_have_a_note_under_it(self):
        body = (
            "## 表の画面\n\n### 画面\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "表の見出しは左の列です。\n"
        )

        slide = deck_of(body).slides[0]

        assert [p.kind for p in slide.parts] == [KIND_TABLE, KIND_BULLETS]
        assert slide.parts[0].table_header == ["A", "B"]

    def test_code_can_have_an_explanation_next_to_it(self):
        body = "## コード\n\n### 画面\n- 実行するコマンドです\n\n```bash\nls -l\n```\n"

        slide = deck_of(body).slides[0]

        assert [p.kind for p in slide.parts] == [KIND_BULLETS, KIND_CODE]
        assert slide.parts[1].code == "ls -l"

    def test_two_figures_can_be_compared_on_one_screen(self, tmp_path):
        (tmp_path / "a.png").write_bytes(PNG)
        (tmp_path / "b.png").write_bytes(PNG)
        body = "## 見比べる\n\n### 画面\n![前](a.png)\n\n![後](b.png)\n"

        slide = deck_of(body, source_path=str(tmp_path / "lesson.md")).slides[0]

        assert [p.kind for p in slide.parts] == [KIND_IMAGE, KIND_IMAGE]

    def test_one_thing_on_a_screen_stays_a_plain_slide(self):
        """中身が 1 つの画面は、これまでどおりの 1 種類のスライドのまま。"""
        deck = deck_of("## 見出し\n\n### 画面\n| A |\n| --- |\n| 1 |\n")

        assert deck.slides[0].kind == KIND_TABLE
        assert deck.slides[0].parts == []

    def test_too_much_on_one_screen_warns_instead_of_splitting(self, tmp_path):
        (tmp_path / "figure.png").write_bytes(PNG)
        body = (
            "## 詰め込みすぎ\n\n### 画面\n"
            + "".join(f"- {i} 番目の項目です\n" for i in range(25))
            + "\n![図](figure.png)\n"
        )

        deck = deck_of(body, source_path=str(tmp_path / "lesson.md"))

        assert len(deck.slides) == 1
        assert any("収まらない" in w for w in deck.warnings)

    def test_a_figure_next_to_a_little_text_still_fits(self, tmp_path):
        (tmp_path / "figure.png").write_bytes(PNG)
        body = "## 図と文\n\n### 画面\n短い説明です。\n\n![図](figure.png)\n"

        deck = deck_of(body, source_path=str(tmp_path / "lesson.md"))

        assert deck.warnings == []


class TestNarration:
    def test_paragraphs_become_lines_and_wrapped_lines_join(self):
        body = "## 見出し\n\n### ナレーション\n一つ目の文です。\n続きの行です。\n\n次の段落です。\n"

        assert deck_of(body).slides[0].notes == "一つ目の文です。 続きの行です。\n次の段落です。"

    def test_a_screen_without_narration_is_silent_not_read_aloud(self):
        """画面の文字を勝手に読み上げないこと(書いた人が読まないと決めている)。"""
        deck = deck_of("## 見出し\n\n### 画面\n画面の文字\n")

        body, directives = narration.split_directives(deck.slides[0].notes)
        assert body == ""
        assert directives.narration == narration.NARRATION_NONE

    def test_the_time_to_look_at_the_screen_is_carried_in_the_notes(self):
        deck = deck_of("## 見出し\n\n### 設定\n- 見る時間: 2.5秒\n\n### ナレーション\n図です。\n")

        body, directives = narration.split_directives(deck.slides[0].notes)
        assert body == "図です。"
        assert directives.hold == 2.5

    def test_a_table_in_the_narration_is_refused(self):
        body = "## 見出し\n\n### ナレーション\n| A |\n| --- |\n| 1 |\n"

        with pytest.raises(ScenarioError) as error:
            deck_of(body)

        assert "ナレーション" in str(error.value)


class TestMistakes:
    def test_text_outside_a_section_says_where_to_put_it(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\nいきなりの本文\n")

        assert "### 画面" in str(error.value)

    def test_an_unknown_section_lists_the_ones_that_exist(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\n\n### せりふ\n本文\n")

        assert "せりふ" in str(error.value)
        assert "### ナレーション" in str(error.value)

    def test_a_repeated_section_is_refused(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\n\n### 画面\nA\n\n### 画面\nB\n")

        assert "重複" in str(error.value)

    def test_an_unknown_setting_lists_the_ones_that_exist(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\n\n### 設定\n- 背景色: 青\n")

        assert "背景色" in str(error.value)
        assert "見る時間" in str(error.value)

    def test_a_time_that_is_not_a_number_is_refused(self):
        with pytest.raises(ScenarioError) as error:
            deck_of("## 見出し\n\n### 設定\n- 見る時間: ゆっくり\n")

        assert "秒数" in str(error.value)

    def test_a_scenario_without_slides_is_refused(self):
        with pytest.raises(ScenarioError) as error:
            parse_scenario(HEAD, source_path="lesson.md")

        assert "スライドが 1 枚もありません" in str(error.value)

    def test_the_message_points_at_the_line_and_the_slide(self):
        body = "## 一枚目\n\n### 画面\nA\n\n## 二枚目\n\n### 設定\n- 見る時間: あとで\n"

        with pytest.raises(ScenarioError) as error:
            deck_of(body)

        # front matter 4 行のあとに本文が続く。行番号は元のファイルのものになる。
        assert "lesson.md の 14 行目" in str(error.value)


class TestThroughThePresentation:
    """資料を経由しても、画面とナレーションの対応が変わらないこと。"""

    BODY = (
        "## 図の画面\n\n"
        "### 設定\n- 見る時間: 2\n\n"
        "### 画面\n![流れの図](figure.png)\n\n"
        "### ナレーション\n図の左から順に見ていきます。\n\n"
        "## 読み上げない画面\n\n"
        "### 画面\n- 画面の項目\n\n"
        "## 図と文の画面\n\n"
        "### 画面\n図の見方です。\n\n![流れの図](figure.png)\n\n"
        "### ナレーション\nこの画面では、図と文をあわせて見ていきます。\n"
    )

    def script_of(self, tmp_path):
        from note2slides.narration import extract_script
        from note2slides.renderer import render_deck

        (tmp_path / "figure.png").write_bytes(PNG)
        deck = deck_of(self.BODY, source_path=str(tmp_path / "lesson.md"))
        path = str(tmp_path / "lesson.pptx")
        render_deck(deck, path)
        return extract_script(path)

    def test_the_narration_is_what_the_scenario_says(self, tmp_path):
        segment = self.script_of(tmp_path).segments[0]

        assert segment.source == narration.SOURCE_NOTES
        assert segment.text == "図の左から順に見ていきます。"
        assert segment.hold == 2.0

    def test_a_screen_without_narration_is_not_described_instead(self, tmp_path):
        """ノートが無い資料なら画面の案内文になるところを、無音のままにする。"""
        segment = self.script_of(tmp_path).segments[1]

        assert segment.source == narration.SOURCE_NONE
        assert segment.text == ""

    def test_a_screen_with_several_things_still_has_one_narration(self, tmp_path):
        """中身が増えても、ナレーションは画面ごとに 1 つのまま。"""
        script = self.script_of(tmp_path)

        assert script.count == 3
        segment = script.segments[2]
        assert segment.source == narration.SOURCE_NOTES
        assert segment.text == "この画面では、図と文をあわせて見ていきます。"


class TestDeck:
    def test_the_title_comes_from_the_front_matter(self):
        assert deck_of("## 見出し\n\n### 画面\nA\n").title == "サンプル"

    def test_without_a_front_matter_title_the_cover_is_used(self):
        text = "---\ntype: scenario\n---\n\n## 表紙の題\n\n### 設定\n- レイアウト: 表紙\n"

        assert build_deck(parse_scenario(text, source_path="lesson.md")).title == "表紙の題"

    def test_the_screen_fits_check_uses_the_same_measure_as_the_planner(self):
        deck = deck_of("## 見出し\n\n### 画面\n- 短い項目\n")

        assert deck.warnings == []
        assert Style().body_height_pt > 0
