"""図解(流れ・枠)。

教材の図は、これまで ASCII アートをコードブロックに書くしかなかった。文字を
等幅で並べる書き方は「日本語と罫線が同じ幅で描かれる」ことを前提にしていて、
資料 -> PDF -> 画像と変換する間にどこかで崩れる。しかも崩れたことは、画像を
目で見るまで分からない。

そこで、箱と矢印を **図形として** 置く。ここで確かめたいのは次の 3 つ。

* 書いた項目が、書いた順にそのまま図になること(構成を推測しない)
* 図の大きさの見積り(layout)と、実際に描く大きさ(renderer)が一致すること
* ナレーションが、図解 1 つにつき案内文を 1 度だけ作ること
"""

from __future__ import annotations

import pytest
from pptx import Presentation

from note2slides import guidance, layout, narration
from note2slides.model import (
    DIAGRAM_FLOW,
    DIAGRAM_FRAME,
    KIND_DIAGRAM,
    SHAPE_DIAGRAM,
    SHAPE_DIAGRAM_ITEM,
    Content,
    Deck,
    Slide,
    diagram_shape_of,
    parse_shape_name,
)
from note2slides.renderer import render_deck
from note2slides.scenario import ScenarioError, build_deck, parse_scenario
from note2slides.style import Style

HEAD = "---\ntype: scenario\ntitle: サンプル\n---\n\n"


def deck_of(body: str):
    return build_deck(parse_scenario(HEAD + body, source_path="lesson.md"))


def screen(block: str) -> str:
    return "## 見出し\n\n### 画面\n\n" + block + "\n\n### ナレーション\n\n説明です。\n"


FLOW = "```流れ\n受け取る\n処理する\n返す\n```"
FRAME = "```枠\n指示\n会話\n質問\n```"


class TestWhatCountsAsADiagram:
    """どう書いたら図解になるか。"""

    @pytest.mark.parametrize(
        "lang, shape",
        [
            ("流れ", DIAGRAM_FLOW),
            ("flow", DIAGRAM_FLOW),
            ("枠", DIAGRAM_FRAME),
            ("frame", DIAGRAM_FRAME),
            ("FLOW", DIAGRAM_FLOW),
        ],
    )
    def test_japanese_and_english_names_both_work(self, lang, shape):
        assert diagram_shape_of(lang) == shape

    @pytest.mark.parametrize("lang", ["", "text", "python", "bash", "図"])
    def test_other_code_blocks_are_still_code(self, lang):
        """図解の名前でないコードブロックは、これまでどおりコードのまま。

        ここが崩れると、既存の資料のコードが図に化ける。
        """
        assert diagram_shape_of(lang) is None

    def test_a_text_block_stays_code_in_a_scenario(self):
        deck = deck_of(screen("```text\nA\nB\n```"))
        assert deck.slides[0].kind != KIND_DIAGRAM


class TestWhatIsWrittenIsWhatIsDrawn:
    """シナリオが正本であること(構成を推測しない)。"""

    def test_each_line_becomes_one_item_in_order(self):
        deck = deck_of(screen(FLOW))
        slide = deck.slides[0]
        assert slide.kind == KIND_DIAGRAM
        assert slide.diagram_shape == DIAGRAM_FLOW
        assert slide.diagram_items == ["受け取る", "処理する", "返す"]

    def test_blank_lines_and_indentation_do_not_add_items(self):
        deck = deck_of(screen("```流れ\n  受け取る  \n\n   返す\n\n```"))
        assert deck.slides[0].diagram_items == ["受け取る", "返す"]

    def test_a_frame_keeps_its_own_shape(self):
        deck = deck_of(screen(FRAME))
        assert deck.slides[0].diagram_shape == DIAGRAM_FRAME

    def test_an_empty_diagram_stops_with_the_place(self):
        """教材では、図が抜けたまま動画にしても直しようがない(図の指定と同じ扱い)。"""
        with pytest.raises(ScenarioError) as err:
            deck_of(screen("```流れ\n\n```"))
        assert "lesson.md" in str(err.value)
        assert "図の中身が空です" in str(err.value)

    def test_too_many_items_warns_but_does_not_split(self):
        """分けるかどうかを決めるのは書いた人。勝手に次の画面へ送らない。"""
        deck = deck_of(screen("```流れ\n" + "\n".join(f"手順{i}" for i in range(8)) + "\n```"))
        assert len(deck.slides) == 1
        assert len(deck.slides[0].diagram_items) == 8
        assert any("図の項目が 8 個" in w for w in deck.warnings)

    def test_a_diagram_can_sit_beside_text_on_one_screen(self):
        deck = deck_of(screen("説明の文です。\n\n" + FLOW))
        slide = deck.slides[0]
        assert [part.kind for part in slide.parts][-1] == KIND_DIAGRAM

    def test_the_plan_dump_shows_the_diagram(self):
        """--dump-plan で、図の中身を目で確かめられること。"""
        data = deck_of(screen(FLOW)).to_dict()
        assert data["slides"][0]["diagram"] == {
            "shape": DIAGRAM_FLOW,
            "items": ["受け取る", "処理する", "返す"],
        }


class TestArticleInputToo:
    """記事(教材シナリオでない Markdown)でも同じ書き方が使えること。"""

    def test_a_flow_block_in_an_article_becomes_a_diagram(self):
        from note2slides.markdown_reader import parse_article
        from note2slides.planner import plan_deck

        deck = plan_deck(parse_article("# 題\n\n## 見出し\n\n" + FLOW + "\n"))
        diagrams = [s for s in deck.slides if s.kind == KIND_DIAGRAM]
        assert len(diagrams) == 1
        assert diagrams[0].diagram_items == ["受け取る", "処理する", "返す"]
        assert diagrams[0].title == "見出し"


class TestTheEstimateMatchesTheDrawing:
    """見積り(layout)と実際(renderer)がずれると、図が下の中身に重なる。"""

    @pytest.mark.parametrize("shape", [DIAGRAM_FLOW, DIAGRAM_FRAME])
    @pytest.mark.parametrize("count", [1, 3, 6])
    def test_the_drawn_height_stays_inside_the_estimate(self, shape, count, tmp_path):
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=shape,
            diagram_items=[f"項目{i}" for i in range(count)],
        )
        estimate = layout.diagram_height(content, style)
        assert estimate > 0

        path = str(tmp_path / "d.pptx")
        render_deck(Deck(slides=[content.as_slide(title="見出し")], title="題"), path, style)
        shapes = _diagram_shapes(path)
        outer = [s for s in shapes if _kind(s) == SHAPE_DIAGRAM]
        assert len(outer) == 1
        drawn = outer[0].height / 914400  # EMU -> inch
        assert drawn == pytest.approx(estimate, abs=0.02)

    def test_a_diagram_is_shrunk_rather_than_overflowing(self, tmp_path):
        """場所が足りないときは小さく描く(下の中身に重ねない)。"""
        style = Style()
        items = [f"項目{i}" for i in range(6)]
        content = Content(kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=items)
        path = str(tmp_path / "d.pptx")
        slide = Slide(
            kind="content",
            title="見出し",
            parts=[Content(kind="bullets", bullets=[]), content],
        )
        # 本文の場所を図と文章で分け合うので、図には全部は渡らない。
        render_deck(Deck(slides=[slide], title="題"), path, style)
        outer = [s for s in _diagram_shapes(path) if _kind(s) == SHAPE_DIAGRAM][0]
        bottom = (outer.top + outer.height) / 914400
        assert bottom <= style.body_top + style.body_height + 0.05

    def test_every_item_is_drawn(self, tmp_path):
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["A", "B", "C"]
        )
        path = str(tmp_path / "d.pptx")
        render_deck(Deck(slides=[content.as_slide(title="見出し")], title="題"), path, style)
        texts = [
            s.text_frame.text
            for s in _diagram_shapes(path)
            if _kind(s) == SHAPE_DIAGRAM_ITEM and s.has_text_frame and s.text_frame.text
        ]
        assert texts == ["A", "B", "C"]


class TestTheNarrationGuidesToTheDiagram:
    """図解は画面に出ているが、文字としては読み上げられない(表・コードと同じ)。"""

    def test_a_flow_is_read_in_the_order_on_screen(self):
        text = guidance.describe_diagram(DIAGRAM_FLOW, ["文章", "トークン", "回答"])
        assert text == "画面の図をご覧ください。上から順に、文章、トークン、回答と進みます。"

    def test_a_frame_says_what_is_inside(self):
        text = guidance.describe_diagram(DIAGRAM_FRAME, ["指示", "会話"])
        assert text == "画面の図をご覧ください。枠の中には、指示、会話が入っています。"

    def test_nothing_is_added_beyond_what_is_on_screen(self):
        """画面に無い語を足さないこと(表・コードと同じ約束)。"""
        text = guidance.describe_diagram(DIAGRAM_FLOW, ["文章", "トークン"])
        for word in ("文章", "トークン"):
            assert word in text
        assert "つまり" not in text and "重要" not in text

    def test_the_guidance_is_made_once_per_diagram(self, tmp_path):
        """図解は複数の図形でできている。案内文が項目の数だけ出ては困る。"""
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["A", "B", "C"]
        )
        path = str(tmp_path / "d.pptx")
        render_deck(Deck(slides=[content.as_slide(title="見出し")], title="題"), path, Style())
        lines = narration.extract_script(path).segments
        assert len(lines) == 1
        assert lines[0].text.count("画面の図をご覧ください") == 1
        assert "上から順に、A、B、Cと進みます" in lines[0].text

    def test_the_items_are_not_read_twice(self, tmp_path):
        """案内文で読んだ項目を、本文としてもう一度読まないこと。

        図解は文字を持つ図形の集まりなので、本文を集める側から外しておかないと
        「上から順に、受け取る、返すと進みます。受け取る。返す。」になる。
        """
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["受け取る", "返す"]
        )
        path = str(tmp_path / "d.pptx")
        render_deck(Deck(slides=[content.as_slide(title="見出し")], title="題"), path, Style())
        text = narration.extract_script(path).segments[0].text
        assert text.count("受け取る") == 1
        assert text.count("返す") == 1

    def test_two_diagrams_on_one_screen_do_not_mix(self, tmp_path):
        flow = Content(kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["A", "B"])
        frame = Content(kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FRAME, diagram_items=["X", "Y"])
        path = str(tmp_path / "d.pptx")
        render_deck(
            Deck(slides=[Slide(kind="content", title="見出し", parts=[flow, frame])], title="題"),
            path,
            Style(),
        )
        text = narration.extract_script(path).segments[0].text
        assert "上から順に、A、Bと進みます" in text
        assert "枠の中には、X、Yが入っています" in text
        assert "A、B、X、Y" not in text

    def test_a_written_narration_is_used_instead(self, tmp_path):
        """教材シナリオでナレーションを書いた画面では、案内文を組み立てない。"""
        deck = build_deck(parse_scenario(HEAD + screen(FLOW), source_path="lesson.md"))
        path = str(tmp_path / "d.pptx")
        render_deck(deck, path, Style())
        assert narration.extract_script(path).segments[0].text == "説明です。"

    def test_time_is_left_to_look_at_the_diagram(self):
        assert guidance.hold_for_diagram(["A"]) > 0
        assert guidance.hold_for_diagram(["A", "B", "C"]) > guidance.hold_for_diagram(["A"])
        assert guidance.hold_for_diagram([f"項目{i}" for i in range(20)]) <= 3.0


def _diagram_shapes(path: str):
    prs = Presentation(path)
    return list(prs.slides[0].shapes)


def _kind(shape) -> str:
    kind, _, _ = parse_shape_name(getattr(shape, "name", ""))
    return kind
