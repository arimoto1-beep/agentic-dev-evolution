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
    DIAGRAM_BOUNDARY,
    DIAGRAM_FLOW,
    DIAGRAM_FRAME,
    KIND_DIAGRAM,
    SHAPE_DIAGRAM,
    SHAPE_DIAGRAM_ITEM,
    Bullet,
    Content,
    Run,
    Deck,
    Slide,
    boundary_parts,
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
    def test_the_drawing_stays_inside_the_place_it_was_given(self, shape, count, tmp_path):
        """layout が配った場所から、描いた図がはみ出さないこと。

        図解は場所が余っていれば大きく描くので、「見積りと同じ大きさ」では
        なくなった。守るべきなのは **配られた場所に収まること** で、
        ここがずれると図が下の中身に重なる(それは画像を見るまで分からない)。
        """
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=shape,
            diagram_items=[f"項目{i}" for i in range(count)],
        )
        body = layout.body_box(style)
        assert layout.diagram_height(content, style, body.width) > 0

        path = str(tmp_path / "d.pptx")
        render_deck(Deck(slides=[content.as_slide(title="見出し")], title="題"), path, style)
        outer = [s for s in _diagram_shapes(path) if _kind(s) == SHAPE_DIAGRAM]
        assert len(outer) == 1
        left = outer[0].left / 914400
        top = outer[0].top / 914400
        width = outer[0].width / 914400
        height = outer[0].height / 914400
        assert left >= body.left - 0.02
        assert top >= body.top - 0.02
        assert left + width <= body.left + body.width + 0.02
        assert top + height <= body.top + body.height + 0.02

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
        assert "左から順に、A、B、Cと進みます" in lines[0].text  # 3 項目は横に並ぶ

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
        assert "左から順に、A、Bと進みます" in text  # 短い流れは横に並ぶ
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


class TestTheDiagramUsesThePlaceItIsGiven:
    """図解は絵なので、渡された場所を使う。

    gen28 までは、大きさが **中身の文字の長さだけ** で決まっていた。短い語の
    流れ図は箱の幅が下限(`diagram_min_width`)に張り付き、画面の左右が大きく
    空いたまま小さく描かれる。44 枚を通して見ると、どの図解も同じ細い列に
    見えるのはこれが理由だった。

    ここで確かめるのは 2 つ。
    * 短い流れは **左から右へ** 並ぶこと(「A から B へ」の自然な読み順)
    * 場所が余っていれば **大きく** 描くこと(ただし見出しより大きくはしない)
    """

    def _outer_and_items(self, path):
        shapes = _diagram_shapes(path)
        outer = [s for s in shapes if _kind(s) == SHAPE_DIAGRAM][0]
        items = [
            s
            for s in shapes
            if _kind(s) == SHAPE_DIAGRAM_ITEM and s.has_text_frame and s.text_frame.text
        ]
        return outer, items

    def _render(self, tmp_path, content, style=None, parts=None):
        style = style or Style()
        path = str(tmp_path / "d.pptx")
        slide = (
            Slide(kind="content", title="見出し", parts=parts)
            if parts
            else content.as_slide(title="見出し")
        )
        render_deck(Deck(slides=[slide], title="題"), path, style)
        return path

    def test_a_short_flow_is_laid_out_left_to_right(self, tmp_path):
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["AI", "MCP", "AWS"]
        )
        _, items = self._outer_and_items(self._render(tmp_path, content))

        assert [i.text_frame.text for i in items] == ["AI", "MCP", "AWS"]
        # 左から右へ。上下は同じ位置に並ぶ。
        assert items[0].left < items[1].left < items[2].left
        assert items[0].top == items[1].top == items[2].top

    def test_a_long_flow_stays_stacked(self, tmp_path):
        """横に並べるのは、そのままの文字で収まるときだけ。

        収まらない長さの項目を無理に横へ並べると、箱の中で文字が折り返され、
        1 つ 1 つが読めなくなる。それなら縦に積むほうがよい。
        """
        items = [
            "試験データを切り替える",
            "Step Functionsを実行する",
            "結果を確認する",
            "ログを確認する",
            "試験結果を判定する",
        ]
        content = Content(kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=items)
        _, drawn = self._outer_and_items(self._render(tmp_path, content))

        assert [i.text_frame.text for i in drawn] == items
        assert drawn[0].top < drawn[1].top
        assert drawn[0].left == drawn[1].left

    def test_a_frame_is_never_laid_out_left_to_right(self, tmp_path):
        """枠図は「中に何が入っているか」の図で、順番の図ではない。"""
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FRAME, diagram_items=["指示", "会話"]
        )
        _, items = self._outer_and_items(self._render(tmp_path, content))

        assert items[0].top < items[1].top

    def test_the_narration_follows_how_it_was_actually_drawn(self, tmp_path):
        """画面が左から右なら「左から順に」と言うこと。

        画面と言っていることが食い違うと、聞いている側だけが混乱する。
        向きを決めるのは layout なので、ナレーションはその結果を見る。
        """
        across = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["AI", "MCP", "AWS"]
        )
        down = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=DIAGRAM_FLOW,
            diagram_items=[
                "試験データを切り替える",
                "Step Functionsを実行する",
                "結果を確認する",
                "ログを確認する",
                "試験結果を判定する",
            ],
        )
        tmp_path.joinpath("a").mkdir()
        tmp_path.joinpath("b").mkdir()
        a = narration.extract_script(self._render(tmp_path / "a", across)).segments[0].text
        b = narration.extract_script(self._render(tmp_path / "b", down)).segments[0].text

        assert "左から順に" in a and "上から順に" not in a
        assert "上から順に" in b and "左から順に" not in b

    def test_a_diagram_alone_on_a_screen_is_drawn_larger(self, tmp_path):
        """1 枚を図解だけに使うなら、その場所を使って大きく描く。"""
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=DIAGRAM_FLOW,
            diagram_items=["機械判定", "AIレビュー", "最終判定"],
        )
        natural = layout.diagram_geometry(content, style, layout.body_box(style).width)
        outer, _ = self._outer_and_items(self._render(tmp_path, content, style))

        assert outer.height / 914400 > natural.height * 1.2

    def test_the_diagram_text_never_gets_bigger_than_the_slide_title(self, tmp_path):
        """図の中の語が、その画面の題より目立つことにならないようにする。"""
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["A", "B"]
        )
        body = layout.body_box(style)
        geometry = layout.diagram_geometry(content, style, body.width, body.height)

        assert geometry.font_pt <= style.slide_title_size + 0.01
        assert geometry.font_pt > style.diagram_size  # それでも大きくはなっている

    def test_a_diagram_sharing_the_screen_does_not_take_the_whole_place(self, tmp_path):
        """文章と並ぶ場合は、渡された高さの中に収まる(下の中身を押し出さない)。"""
        style = Style()
        content = Content(
            kind=KIND_DIAGRAM, diagram_shape=DIAGRAM_FLOW, diagram_items=["AI", "MCP", "AWS"]
        )
        text = Content(kind="bullets", bullets=[Bullet(runs=[Run("説明の文です。" * 10)])])
        path = self._render(tmp_path, content, style, parts=[content, text])
        outer, _ = self._outer_and_items(path)

        placed = layout.fit([content, text], style, layout.body_box(style))
        box = placed.parts[0].box
        assert outer.height / 914400 <= box.height + 0.02

    def test_the_leftover_place_goes_to_the_diagram(self, tmp_path):
        """図と文章が並ぶ画面で、余った場所は図に渡る。

        gen28 までは最後の中身(この場合は文章)に渡していた。文章は広い箱を
        もらっても大きくならないので、**余りはどこにも使われず**、図は小さい
        ままだった。
        """
        style = Style()
        diagram = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=DIAGRAM_FLOW,
            diagram_items=["ローカルへ証跡保存", "機械的な期待値チェック", "AIによる解析"],
        )
        text = Content(kind="bullets", bullets=[Bullet(runs=[Run("一行の説明です。")])])
        body = layout.body_box(style)

        placed = layout.fit([diagram, text], style, body)

        natural = layout.diagram_geometry(diagram, style, body.width).height
        assert placed.parts[0].box.height > natural * 1.2

    def test_the_last_text_keeps_room_for_one_more_line(self, tmp_path):
        """全部は渡さない。文章の折り返しが 1 行増えても収まるだけは残す。

        余りを全部図に渡すと、文章がちょうど下端まで来て、ページ番号の帯と
        同じ高さに並ぶ(gen28 が直したのがこの見え方)。
        """
        style = Style()
        diagram = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=DIAGRAM_FLOW,
            diagram_items=["ローカルへ証跡保存", "機械的な期待値チェック", "AIによる解析"],
        )
        text = Content(kind="bullets", bullets=[Bullet(runs=[Run("一行の説明です。")])])
        body = layout.body_box(style)

        placed = layout.fit([diagram, text], style, body)

        estimate = layout.content_height(text, style, body.width)
        one_line = style.line_height_pt(style.body_size(0)) / 72.0
        assert placed.parts[1].box.height >= estimate + one_line - 0.01


BOUNDARY = (
    "```境界\n"
    "人間が、何を作るかを決める\n"
    "↓ 決まった仕様\n"
    "↑ AIだけで決められないこと\n"
    "AIが、どう実現するかを作る\n"
    "```"
)


class TestTheBoundaryDiagram:
    """境界図。1 本の線で上下に分け、線をまたぐものを矢印で示す。

    流れ・枠で書けるのは「順に進む」「中に入っている」の 2 つで、
    **役割の分かれ目** は書けなかった。表に「誰が / 何を受け持つか」と
    書くことはできるが、表には線が無い。「線を引く」と言っている画面に
    線が無いと、言葉と絵が食い違う。

    ここで確かめたいのは 4 つ。

    * `↓` / `↑` の行が線の位置になること(区切りの記号を別に決めない)
    * 線が図の主役として描かれること(箱より広く、箱に隠されない)
    * 矢印が線を **突き抜ける** こと(触れているだけでは、またいで見えない)
    * 案内文が、上下と向きを言い分けること
    """

    @pytest.mark.parametrize("lang", ["境界", "boundary"])
    def test_a_boundary_block_becomes_a_boundary_diagram(self, lang):
        assert diagram_shape_of(lang) == DIAGRAM_BOUNDARY

    def test_the_arrow_lines_decide_where_the_line_is(self):
        parts = boundary_parts(["人間", "↓ 仕様", "↑ 決められないこと", "AI"])
        assert parts.upper == ["人間"]
        assert parts.lower == ["AI"]
        assert [(c.down, c.label) for c in parts.crossings] == [
            (True, "仕様"),
            (False, "決められないこと"),
        ]

    def test_the_crossings_are_not_drawn_as_boxes(self):
        """またぐものは矢印と札になる。箱として数えると場所の見積りがずれる。"""
        content = Content(
            kind=KIND_DIAGRAM,
            diagram_shape=DIAGRAM_BOUNDARY,
            diagram_items=["人間", "↓ 仕様", "AI"],
        )
        assert layout.diagram_items(content) == ["人間", "AI"]

    @pytest.mark.parametrize(
        "block, message",
        [
            ("```境界\n人間\nAI\n```", "線をまたぐもの"),
            ("```境界\n人間\n↓ 仕様\nAI\n↑ 戻す\n```", "離れています"),
            ("```境界\n↓ 仕様\nAI\n```", "線の上に何もありません"),
            ("```境界\n人間\n↓ 仕様\n```", "線の下に何もありません"),
        ],
    )
    def test_a_boundary_that_has_no_line_is_refused(self, block, message):
        """線が決まらない書き方は止める。

        黙って描くと **線の無い境界図** が資料に出る。図の崩れは画像を目で
        見るまで分からない(gen27)ので、書いた時点で知らせる。
        """
        with pytest.raises(ScenarioError) as error:
            deck_of(screen(block))
        assert message in str(error.value)

    def test_the_line_is_wider_than_the_boxes(self, tmp_path):
        """線は箱より左右へはみ出す。

        箱の幅ぴったりで止めると「箱と箱をつなぐ線」に見えて、
        越える・越えないの話に見えない。
        """
        rule, boxes, _ = _boundary_shapes(self._render(tmp_path))
        assert rule.left < min(b.left for b in boxes)
        assert rule.left + rule.width > max(b.left + b.width for b in boxes)

    def test_the_arrows_go_through_the_line(self, tmp_path):
        """矢印は線の上下へ突き抜ける。触れているだけでは、またいで見えない。"""
        rule, _, crossings = _boundary_shapes(self._render(tmp_path))
        assert crossings, "またぐ矢印が描かれていない"
        for arrow in crossings:
            assert arrow.top < rule.top
            assert arrow.top + arrow.height > rule.top + rule.height

    def test_the_label_sits_on_the_side_it_comes_from(self, tmp_path):
        """下りるものの札は線の上、戻るものの札は線の下。

        札を線の上に載せると、そこだけ線が途切れて見え、**1 本の線** という
        図の主題が消える。向きは札の位置で表す。
        """
        path = self._render(tmp_path)
        rule, _, _ = _boundary_shapes(path)
        labels = {language: shape for shape, language in _named_labels(path)}
        assert set(labels) == {"down", "up"}
        assert labels["down"].top + labels["down"].height <= rule.top + rule.height
        assert labels["up"].top >= rule.top

    def test_the_drawing_stays_inside_the_place_it_was_given(self, tmp_path):
        """描いた図形が、外枠(layout が配った場所)からはみ出さないこと。"""
        style = Style()
        body = layout.body_box(style)
        shapes = _diagram_shapes(self._render(tmp_path))
        outer = [s for s in shapes if _kind(s) == SHAPE_DIAGRAM][0]
        assert outer.left / 914400 >= body.left - 0.02
        assert outer.top / 914400 >= body.top - 0.02
        assert (outer.left + outer.width) / 914400 <= body.left + body.width + 0.02
        assert (outer.top + outer.height) / 914400 <= body.top + body.height + 0.02
        slack = 914400 * 0.02
        for shape in shapes:
            if _kind(shape) != SHAPE_DIAGRAM_ITEM:
                continue
            assert shape.left >= outer.left - slack
            assert shape.left + shape.width <= outer.left + outer.width + slack
            assert shape.top >= outer.top - slack
            assert shape.top + shape.height <= outer.top + outer.height + slack

    def test_the_narration_says_which_side_and_which_way(self):
        text = guidance.describe_diagram(
            DIAGRAM_BOUNDARY,
            ["人間が決める", "↓ 決まった仕様", "↑ 決められないこと", "AIが作る"],
        )
        assert text == (
            "画面の図をご覧ください。"
            "線の上は、人間が決める。線の下は、AIが作る。"
            "上から下へ渡るのは、決まった仕様です。"
            "下から上へ戻るのは、決められないことです。"
        )

    def test_the_direction_survives_the_presentation(self, tmp_path):
        """資料に書き出して読み直しても、どちらの向きだったかが残ること。

        案内文を作るのは資料を読む側(narration)なので、向きが資料に
        残っていないと「上から下へ」を言えなくなる。
        """
        path = self._render(tmp_path)
        prs = Presentation(path)
        parts, _ = narration._screen_guidance(prs.slides[0])
        assert parts and "上から下へ渡るのは、決まった仕様です。" in parts[0]
        assert "下から上へ戻るのは、AIだけで決められないことです。" in parts[0]

    def _render(self, tmp_path) -> str:
        deck = deck_of(screen(BOUNDARY))
        path = str(tmp_path / "b.pptx")
        render_deck(deck, path, Style())
        return path


def _named_labels(path: str):
    """境界図の札(向きが名前に残っているもの)を返す。"""
    found = []
    for shape in _diagram_shapes(path):
        kind, _, language = parse_shape_name(getattr(shape, "name", ""))
        if kind == SHAPE_DIAGRAM_ITEM and language in ("down", "up"):
            found.append((shape, language))
    return found


def _boundary_shapes(path: str):
    """境界図の図形を「線・箱・またぐ矢印」に分ける。

    線と矢印は文字を持たない図形で、線は横に長く、矢印は縦に長い。
    """
    labels = {id(s) for s, _ in _named_labels(path)}
    rule = None
    boxes = []
    crossings = []
    for shape in _diagram_shapes(path):
        if _kind(shape) != SHAPE_DIAGRAM_ITEM or id(shape) in labels:
            continue
        text = shape.text_frame.text if shape.has_text_frame else ""
        if text.strip():
            boxes.append(shape)
        elif shape.width > shape.height:
            rule = shape
        else:
            crossings.append(shape)
    return rule, boxes, crossings
