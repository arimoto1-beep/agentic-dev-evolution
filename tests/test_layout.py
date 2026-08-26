"""1 画面に複数の中身を並べたときの、場所の決め方。

確認したいのは「書いた順に縦へ並ぶこと」と「足りないときに何が譲るか」で、
描画結果そのもの(test_renderer.py)ではない。
"""

from __future__ import annotations

from note2slides import layout
from note2slides.model import (
    DIAGRAM_FLOW,
    KIND_BULLETS,
    KIND_CODE,
    KIND_DIAGRAM,
    KIND_IMAGE,
    Bullet,
    Content,
    Run,
)
from note2slides.style import Style

STYLE = Style()


def text_part(lines: int = 1) -> Content:
    return Content(
        kind=KIND_BULLETS,
        bullets=[Bullet(runs=[Run(f"{i} 行目の文です")]) for i in range(lines)],
    )


def image_part(path: str = "") -> Content:
    return Content(kind=KIND_IMAGE, image_path=path or None)


def diagram_part(items: int = 5) -> Content:
    return Content(
        kind=KIND_DIAGRAM,
        diagram_shape=DIAGRAM_FLOW,
        diagram_items=[f"工程{i}" for i in range(items)],
    )


def fit(parts):
    return layout.fit(parts, STYLE, layout.body_box(STYLE))


class TestStacking:
    def test_parts_are_placed_in_order_without_overlapping(self):
        placed = fit([text_part(2), image_part(), text_part(1)]).parts

        assert [p.content.kind for p in placed] == [KIND_BULLETS, KIND_IMAGE, KIND_BULLETS]
        for above, below in zip(placed, placed[1:]):
            assert above.box.top + above.box.height <= below.box.top

    def test_everything_stays_inside_the_body_area(self):
        box = layout.body_box(STYLE)

        placed = fit([text_part(6), image_part(), text_part(2)]).parts

        last = placed[-1]
        assert placed[0].box.top == box.top
        assert last.box.top + last.box.height <= box.top + box.height + 1e-9

    def test_one_part_uses_the_whole_body_area(self):
        """中身が 1 つの画面は、これまでと同じ場所に描かれる。"""
        box = layout.body_box(STYLE)

        placed = fit([text_part(1)]).parts[0].box

        assert (placed.top, placed.height) == (box.top, box.height)


class TestWhenThereIsNotEnoughRoom:
    def test_a_figure_gives_up_its_room_first(self):
        """図は縮めても読めるので、文章より先に譲る。"""
        alone = fit([image_part()]).parts[0].box.height

        with_text = fit([text_part(8), image_part()])

        assert with_text.parts[1].box.height < alone
        assert with_text.fits  # 文章が入る限り「収まらない」とは言わない

    def test_text_that_cannot_fit_is_reported(self):
        crowded = fit([text_part(40)])

        assert not crowded.fits
        assert crowded.overflow > 1.0

    def test_a_figure_alone_is_never_reported_as_too_much(self):
        """図は必ず縮めて収まるので、警告の対象にしない。"""
        assert fit([image_part()]).fits

    def test_a_figure_keeps_a_minimum_share_of_the_screen(self):
        box = layout.body_box(STYLE)

        placed = fit([text_part(40), image_part()]).parts

        assert placed[1].box.height > 0
        assert placed[1].box.height < box.height * layout.MIN_FIGURE_SHARE

    def test_a_diagram_gives_up_its_room_the_same_way_a_figure_does(self):
        """図解(流れ・枠)も、図と同じく縮めて描かれるので先に譲る。

        renderer は図解を box の高さに合わせて縮める。ここで図解を
        「縮められないもの」として数えると、代わりに文章が縮み、それでも
        入りきらずにページ番号の帯へはみ出す。
        """
        alone = fit([diagram_part()]).parts[0].box.height
        text = text_part(6)
        needed = layout.content_height(text, STYLE, layout.body_box(STYLE).width)

        placed = fit([text, diagram_part()]).parts

        assert placed[1].box.height < alone, "図解が譲ること"
        assert placed[0].box.height >= needed - 1e-9, "譲るのは図解であって、文章ではない"

    def test_text_next_to_a_diagram_keeps_the_room_it_needs(self):
        text_alone = fit([text_part(3)]).parts[0]
        needed = layout.content_height(text_alone.content, STYLE, text_alone.box.width)

        placed = fit([diagram_part(7), text_part(3)]).parts

        assert placed[1].box.height >= needed - 1e-9

    def test_a_diagram_alone_is_never_reported_as_too_much(self):
        """項目が多い図解は縮めて収まる(多すぎることは別の警告が伝える)。"""
        assert fit([diagram_part(9)]).fits


class TestHeights:
    def test_a_missing_image_still_gets_a_height(self):
        """図を読み取れなくても、並べ方の見積りは止めない。"""
        assert layout.content_height(image_part("none.png"), STYLE, 10.0) > 0

    def test_code_is_measured_by_its_lines(self):
        short = Content(kind=KIND_CODE, code="ls")
        long = Content(kind=KIND_CODE, code="\n".join("ls" for _ in range(10)))

        assert layout.content_height(long, STYLE, 10.0) > layout.content_height(short, STYLE, 10.0)
