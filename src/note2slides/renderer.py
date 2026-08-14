"""スライド構成(Deck)を .pptx として書き出す。

出力は Office Open XML なので、PowerPoint だけでなく LibreOffice Impress でも
そのまま開いて編集できる。プレースホルダを使いつつ 16:9 に合わせて配置し直す。
"""

from __future__ import annotations

from typing import List, Optional

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from . import metrics, oxml_utils
from .model import (
    KIND_CODE,
    KIND_IMAGE,
    KIND_SECTION,
    KIND_TABLE,
    KIND_TITLE,
    NUMBER,
    QUOTE,
    SHAPE_CODE,
    SHAPE_TABLE,
    Bullet,
    Deck,
    Run,
    Slide,
    shape_name,
)
from .style import SLIDE_HEIGHT_EMU, SLIDE_WIDTH_EMU, Style

_LAYOUT_TITLE = 0
_LAYOUT_SECTION = 2
_LAYOUT_TITLE_ONLY = 5

_BULLET_CHARS = {0: "●", 1: "–", 2: "・", 3: "・"}


def render_deck(deck: Deck, output_path: str, style: Optional[Style] = None) -> str:
    Renderer(style or Style()).render(deck, output_path)
    return output_path


class Renderer:
    def __init__(self, style: Style) -> None:
        self.style = style

    def render(self, deck: Deck, output_path: str) -> None:
        prs = Presentation()
        prs.slide_width = Emu(SLIDE_WIDTH_EMU)
        prs.slide_height = Emu(SLIDE_HEIGHT_EMU)
        prs.core_properties.title = deck.title

        for slide in deck.slides:
            self._render_slide(prs, slide)

        prs.save(output_path)

    # -- スライド種別ごとの描画 -----------------------------------------
    def _render_slide(self, prs: Presentation, slide: Slide) -> None:
        if slide.kind == KIND_TITLE:
            pptx_slide = self._title_slide(prs, slide)
        elif slide.kind == KIND_SECTION:
            pptx_slide = self._section_slide(prs, slide)
        else:
            pptx_slide = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_TITLE_ONLY])
            self._draw_slide_title(pptx_slide, slide.title)
            if slide.kind == KIND_CODE:
                self._draw_code(pptx_slide, slide)
            elif slide.kind == KIND_TABLE:
                self._draw_table(pptx_slide, slide)
            elif slide.kind == KIND_IMAGE:
                self._draw_image(pptx_slide, slide)
            else:
                self._draw_bullets(pptx_slide, slide.bullets)

        if slide.notes:
            pptx_slide.notes_slide.notes_text_frame.text = slide.notes

    def _title_slide(self, prs: Presentation, slide: Slide):
        pptx_slide = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_TITLE])
        style = self.style
        title_ph = pptx_slide.shapes.title
        _place(title_ph, 1.0, 2.2, 11.33, 1.6)
        self._fill_text(
            title_ph.text_frame,
            [[Run(slide.title)]],
            size=style.deck_title_size,
            color=style.color_title,
            bold=True,
            align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.BOTTOM,
        )

        subtitle_ph = _placeholder(pptx_slide, 1)
        if slide.subtitle and subtitle_ph is not None:
            _place(subtitle_ph, 1.0, 4.3, 11.33, 0.9)
            self._fill_text(
                subtitle_ph.text_frame,
                [[Run(slide.subtitle)]],
                size=style.deck_subtitle_size,
                color=style.color_muted,
                align=PP_ALIGN.CENTER,
                anchor=MSO_ANCHOR.TOP,
            )
        elif subtitle_ph is not None:
            _remove_shape(subtitle_ph)

        rule = pptx_slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(5.67), Inches(4.05), Inches(2.0), Inches(0.045)
        )
        _paint(rule, self.style.color_accent)
        return pptx_slide

    def _section_slide(self, prs: Presentation, slide: Slide):
        pptx_slide = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_SECTION])
        style = self.style
        title_ph = pptx_slide.shapes.title
        _place(title_ph, 0.9, 2.7, 11.53, 1.6)
        self._fill_text(
            title_ph.text_frame,
            [[Run(slide.title)]],
            size=style.section_title_size,
            color=style.color_title,
            bold=True,
            align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE,
        )
        for ph in list(pptx_slide.placeholders):
            if ph != title_ph:
                _remove_shape(ph)
        rule = pptx_slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(5.67), Inches(4.45), Inches(2.0), Inches(0.045)
        )
        _paint(rule, style.color_accent)
        return pptx_slide

    def _draw_slide_title(self, pptx_slide, title: str) -> None:
        style = self.style
        title_ph = pptx_slide.shapes.title
        if title_ph is None:
            return
        if not title:
            _remove_shape(title_ph)
            return
        _place(title_ph, style.title_left, style.title_top, style.title_width, style.title_height)
        self._fill_text(
            title_ph.text_frame,
            [[Run(title)]],
            size=style.slide_title_size,
            color=style.color_title,
            bold=True,
            align=PP_ALIGN.LEFT,
            anchor=MSO_ANCHOR.MIDDLE,
            shrink_to_fit=True,
        )
        rule = pptx_slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(style.title_left),
            Inches(style.title_rule_top),
            Inches(style.title_width),
            Inches(style.title_rule_height),
        )
        _paint(rule, style.color_accent)

    # -- 本文 -----------------------------------------------------------
    def _draw_bullets(self, pptx_slide, bullets: List[Bullet]) -> None:
        if not bullets:
            return
        style = self.style
        box = pptx_slide.shapes.add_textbox(
            Inches(style.body_left),
            Inches(style.body_top),
            Inches(style.body_width),
            Inches(style.body_height),
        )
        frame = box.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.TOP
        _zero_insets(frame)

        used = 0.0
        for index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            size = style.body_size(bullet.level)
            self._write_runs(paragraph, bullet.runs, size, style.color_body)
            paragraph.line_spacing = style.line_spacing
            if index > 0:
                paragraph.space_before = Pt(style.space_before_pt)
            self._apply_bullet(paragraph, bullet)
            avail = style.body_width_pt - style.bullet_indent_pt(bullet.level)
            used += metrics.line_count(bullet.text, size, avail) * style.line_height_pt(size)
            if index > 0:
                used += style.space_before_pt

        if used > style.body_height_pt:
            scale = max(0.6, style.body_height_pt / used)
            oxml_utils.set_normal_autofit(frame, scale, line_reduction=0.1 * (1 - scale))

    def _apply_bullet(self, paragraph, bullet: Bullet) -> None:
        style = self.style
        indent = Inches(style.indent_per_level * (bullet.level + 1))
        hanging = Inches(style.indent_per_level)
        oxml_utils.set_indent(paragraph, indent, hanging)
        accent = "%02X%02X%02X" % style.color_accent
        if bullet.kind == NUMBER:
            oxml_utils.set_auto_number(paragraph, start_at=_number_of(bullet))
        elif bullet.kind == QUOTE:
            oxml_utils.set_char_bullet(paragraph, "│", font="Arial", color=accent)
        else:
            char = _BULLET_CHARS.get(bullet.level, "・")
            oxml_utils.set_char_bullet(paragraph, char, font="Arial", color=accent)

    def _draw_code(self, pptx_slide, slide: Slide) -> None:
        style = self.style
        lines = slide.code.split("\n")
        line_height = style.line_height_pt(style.code_size, style.code_line_spacing)
        height = min(style.body_height, max(0.8, (len(lines) * line_height) / 72.0 + 0.4))
        box = pptx_slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(style.body_left),
            Inches(style.body_top),
            Inches(style.body_width),
            Inches(height),
        )
        # ナレーション生成がコードをそのまま読み上げず、画面の案内文にできるよう、
        # 図形の名前に種類を残す(model.shape_name)。
        box.name = shape_name(SHAPE_CODE, slide.continued, slide.code_lang)
        _paint(box, style.color_code_bg)
        frame = box.text_frame
        frame.word_wrap = False
        frame.vertical_anchor = MSO_ANCHOR.TOP
        frame.margin_left = Inches(0.22)
        frame.margin_right = Inches(0.22)
        frame.margin_top = Inches(0.16)
        frame.margin_bottom = Inches(0.16)

        for index, line in enumerate(lines):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.line_spacing = style.code_line_spacing
            paragraph.alignment = PP_ALIGN.LEFT
            oxml_utils.set_no_bullet(paragraph)
            run = paragraph.add_run()
            run.text = line if line else " "
            run.font.size = Pt(style.code_size)
            run.font.color.rgb = RGBColor(*style.color_body)
            oxml_utils.set_run_fonts(run, style.font_mono, style.font_mono)

        widest = max((metrics.text_width_em(line) for line in lines), default=0)
        avail_pt = style.body_width_pt - 40
        if widest * style.code_size > avail_pt:
            scale = max(0.55, avail_pt / (widest * style.code_size))
            oxml_utils.set_normal_autofit(frame, scale)

    def _draw_table(self, pptx_slide, slide: Slide) -> None:
        style = self.style
        header = slide.table_header
        rows = slide.table_rows
        n_cols = max([len(header)] + [len(r) for r in rows]) or 1
        n_rows = (1 if header else 0) + len(rows)
        if n_rows == 0:
            return
        height = min(style.body_height, 0.45 * n_rows + 0.2)
        shape = pptx_slide.shapes.add_table(
            n_rows,
            n_cols,
            Inches(style.body_left),
            Inches(style.body_top),
            Inches(style.body_width),
            Inches(height),
        )
        shape.name = shape_name(SHAPE_TABLE, slide.continued)
        table = shape.table
        table.first_row = bool(header)

        all_rows = ([header] if header else []) + rows
        for r, row in enumerate(all_rows):
            for c in range(n_cols):
                cell = table.cell(r, c)
                text = row[c] if c < len(row) else ""
                frame = cell.text_frame
                frame.word_wrap = True
                paragraph = frame.paragraphs[0]
                oxml_utils.set_no_bullet(paragraph)
                run = paragraph.add_run()
                run.text = text
                run.font.size = Pt(style.table_size)
                run.font.bold = bool(header) and r == 0
                oxml_utils.set_run_fonts(run, style.font_latin, style.font_ea)
                cell.margin_left = Inches(0.1)
                cell.margin_right = Inches(0.1)

    def _draw_image(self, pptx_slide, slide: Slide) -> None:
        style = self.style
        caption_space = 0.45 if slide.image_alt else 0.0
        max_w = Inches(style.body_width)
        max_h = Inches(style.body_height - caption_space)
        picture = pptx_slide.shapes.add_picture(slide.image_path, 0, 0, width=max_w)
        if picture.height > max_h:
            ratio = max_h / picture.height
            picture.height = int(picture.height * ratio)
            picture.width = int(picture.width * ratio)
        picture.left = int((SLIDE_WIDTH_EMU - picture.width) / 2)
        picture.top = Inches(style.body_top)

        if slide.image_alt:
            top = picture.top + picture.height + Inches(0.1)
            box = pptx_slide.shapes.add_textbox(
                Inches(style.body_left), top, Inches(style.body_width), Inches(0.35)
            )
            self._fill_text(
                box.text_frame,
                [[Run(slide.image_alt)]],
                size=style.caption_size,
                color=style.color_muted,
                align=PP_ALIGN.CENTER,
            )

    # -- テキスト共通 ---------------------------------------------------
    def _fill_text(
        self,
        frame,
        paragraphs: List[List[Run]],
        size: float,
        color,
        bold: bool = False,
        align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP,
        shrink_to_fit: bool = False,
    ) -> None:
        frame.word_wrap = True
        frame.vertical_anchor = anchor
        _zero_insets(frame)
        for index, runs in enumerate(paragraphs):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.alignment = align
            oxml_utils.set_no_bullet(paragraph)
            self._write_runs(paragraph, runs, size, color, bold=bold)
        if shrink_to_fit:
            text = "".join(r.text for runs in paragraphs for r in runs)
            width_pt = metrics.text_width_em(text) * size
            avail = self.style.title_width * 72.0
            if width_pt > avail:
                oxml_utils.set_normal_autofit(frame, max(0.6, avail / width_pt))

    def _write_runs(self, paragraph, runs: List[Run], size: float, color, bold: bool = False) -> None:
        style = self.style
        for source in runs:
            run = paragraph.add_run()
            run.text = source.text
            font = run.font
            font.size = Pt(size * (0.94 if source.code else 1.0))
            font.bold = bold or source.bold
            font.italic = source.italic
            if source.link:
                run.hyperlink.address = source.link
                font.color.rgb = RGBColor(*style.color_accent)
            else:
                font.color.rgb = RGBColor(*color)
            if source.code:
                oxml_utils.set_run_fonts(run, style.font_mono, style.font_mono)
            else:
                oxml_utils.set_run_fonts(run, style.font_latin, style.font_ea)


# ---------------------------------------------------------------------------
# 小さなヘルパ
# ---------------------------------------------------------------------------


def _placeholder(pptx_slide, idx: int):
    for shape in pptx_slide.placeholders:
        if shape.placeholder_format.idx == idx:
            return shape
    return None


def _remove_shape(shape) -> None:
    element = shape._element
    element.getparent().remove(element)


def _paint(shape, rgb) -> None:
    """図形を単色で塗る。テーマ由来の影や枠線は明示的に打ち消す。"""
    # add_shape が付ける <p:style>(テーマの効果参照)を外さないと、
    # LibreOffice では effectLst の指定より優先されて影が残る。
    style_element = shape._element.find(qn("p:style"))
    if style_element is not None:
        shape._element.remove(style_element)
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*rgb)
    shape.line.fill.background()
    shape.shadow.inherit = False


def _zero_insets(frame) -> None:
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0


def _number_of(bullet: Bullet) -> Optional[int]:
    if not bullet.number:
        return None
    digits = bullet.number.rstrip(".)")
    return int(digits) if digits.isdigit() else None


def _place(shape, left: float, top: float, width: float, height: float) -> None:
    shape.left = Inches(left)
    shape.top = Inches(top)
    shape.width = Inches(width)
    shape.height = Inches(height)
