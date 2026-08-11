"""python-pptx が直接扱わない DrawingML 要素を操作するための補助。

OOXML は子要素の順序が決まっているため、スキーマ順に基づいて挿入する。
"""

from __future__ import annotations

from typing import Optional

from lxml import etree
from pptx.oxml.ns import qn

# a:pPr の子要素順(ECMA-376 CT_TextParagraphProperties)
_PPR_SEQ = (
    "a:lnSpc",
    "a:spcBef",
    "a:spcAft",
    "a:buClrTx",
    "a:buClr",
    "a:buSzTx",
    "a:buSzPct",
    "a:buSzPts",
    "a:buFontTx",
    "a:buFont",
    "a:buNone",
    "a:buAutoNum",
    "a:buChar",
    "a:tabLst",
    "a:defRPr",
    "a:extLst",
)

# a:rPr / a:defRPr の子要素順(CT_TextCharacterProperties)
_RPR_SEQ = (
    "a:ln",
    "a:noFill",
    "a:solidFill",
    "a:gradFill",
    "a:blipFill",
    "a:pattFill",
    "a:grpFill",
    "a:effectLst",
    "a:effectDag",
    "a:highlight",
    "a:uLnTx",
    "a:uLn",
    "a:uFillTx",
    "a:uFill",
    "a:latin",
    "a:ea",
    "a:cs",
    "a:sym",
    "a:hlinkClick",
    "a:hlinkMouseOver",
    "a:rtl",
    "a:extLst",
)

# a:bodyPr の子要素順(先頭部分のみ)
_BODYPR_SEQ = (
    "a:prstTxWarp",
    "a:noAutofit",
    "a:normAutofit",
    "a:spAutoFit",
    "a:scene3d",
    "a:sp3d",
    "a:flatTx",
    "a:extLst",
)


def _insert_in_order(parent, element, sequence) -> None:
    tag = etree.QName(element).localname
    index = sequence.index(f"a:{tag}")
    successors = sequence[index + 1 :]
    for child in parent:
        child_tag = etree.QName(child).localname
        if f"a:{child_tag}" in successors:
            child.addprevious(element)
            return
    parent.append(element)


def _replace(parent, tag: str, sequence) -> etree._Element:
    for existing in parent.findall(qn(tag)):
        parent.remove(existing)
    element = parent.makeelement(qn(tag), {})
    _insert_in_order(parent, element, sequence)
    return element


def _clear(parent, tags) -> None:
    for tag in tags:
        for existing in parent.findall(qn(tag)):
            parent.remove(existing)


_BULLET_TAGS = ("a:buFont", "a:buNone", "a:buAutoNum", "a:buChar", "a:buClr")


def set_no_bullet(paragraph) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    _clear(pPr, _BULLET_TAGS)
    _replace(pPr, "a:buNone", _PPR_SEQ)


def set_char_bullet(paragraph, char: str, font: str = "Arial", color: Optional[str] = None) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    _clear(pPr, _BULLET_TAGS)
    if color:
        buClr = _replace(pPr, "a:buClr", _PPR_SEQ)
        srgb = buClr.makeelement(qn("a:srgbClr"), {"val": color})
        buClr.append(srgb)
    _replace(pPr, "a:buFont", _PPR_SEQ).set("typeface", font)
    _replace(pPr, "a:buChar", _PPR_SEQ).set("char", char)


def set_auto_number(paragraph, start_at: Optional[int] = None, fmt: str = "arabicPeriod") -> None:
    pPr = paragraph._p.get_or_add_pPr()
    _clear(pPr, _BULLET_TAGS)
    buAutoNum = _replace(pPr, "a:buAutoNum", _PPR_SEQ)
    buAutoNum.set("type", fmt)
    if start_at is not None:
        buAutoNum.set("startAt", str(max(1, start_at)))


def set_indent(paragraph, margin_left_emu: int, hanging_emu: int = 0) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    pPr.set("marL", str(int(margin_left_emu)))
    pPr.set("indent", str(int(-hanging_emu)))


def set_run_fonts(run, latin: str, east_asian: str, complex_script: Optional[str] = None) -> None:
    """欧文・日本語それぞれのフォントを指定する。"""
    rPr = run._r.get_or_add_rPr()
    _replace(rPr, "a:latin", _RPR_SEQ).set("typeface", latin)
    _replace(rPr, "a:ea", _RPR_SEQ).set("typeface", east_asian)
    _replace(rPr, "a:cs", _RPR_SEQ).set("typeface", complex_script or latin)


def set_normal_autofit(text_frame, font_scale: float, line_reduction: float = 0.0) -> None:
    """テキストがはみ出す場合の保険として縮小率を書き込む。"""
    bodyPr = text_frame._txBody.bodyPr
    _clear(bodyPr, ("a:noAutofit", "a:normAutofit", "a:spAutoFit"))
    normAutofit = _replace(bodyPr, "a:normAutofit", _BODYPR_SEQ)
    # 1000 分率ではなく 100000 分率(92.5% -> "92500")で表す。
    normAutofit.set("fontScale", str(int(round(font_scale * 100000))))
    if line_reduction:
        normAutofit.set("lnSpcReduction", str(int(round(line_reduction * 100000))))
