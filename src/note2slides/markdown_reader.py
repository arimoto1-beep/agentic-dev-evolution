"""Markdown 記事を中間表現(Article)へ変換する。

markdown-it-py のトークン列を走査するだけで、テキストの言い換えや要約は行わない。
出力されるテキストは原文の部分文字列である(記号の除去を除く)。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .model import (
    Article,
    CodeBlock,
    Heading,
    Image,
    ListBlock,
    ListItem,
    Paragraph,
    Run,
    SlideBreak,
    Table,
)

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_META_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def split_front_matter(text: str) -> Tuple[dict, str]:
    """先頭の YAML front matter を `key: value` の範囲で読み取る。

    複雑な YAML は対象外で、解釈できない行は無視する。
    """
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _META_LINE_RE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[m.group(1).lower()] = value
    return meta, text[match.end() :]


def parse_article(text: str, source_path: Optional[str] = None) -> Article:
    meta, body = split_front_matter(text)
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    tokens = md.parse(body)
    blocks = _Walker().walk(tokens)
    return Article(blocks=blocks, meta=meta, source_path=source_path)


def parse_article_file(path: str) -> Article:
    with open(path, "r", encoding="utf-8-sig") as f:
        return parse_article(f.read(), source_path=path)


class _Walker:
    """トークン列 -> ブロック列。"""

    def __init__(self) -> None:
        self.blocks: List[object] = []
        self.list_stack: List[dict] = []  # {ordered, index}
        self.pending_items: List[ListItem] = []
        self.quote_depth = 0

    def walk(self, tokens: List[Token]) -> List[object]:
        i = 0
        while i < len(tokens):
            i = self._handle(tokens, i)
        self._flush_list()
        return self.blocks

    # -- ヘルパ ---------------------------------------------------------
    def _flush_list(self) -> None:
        if self.pending_items:
            self.blocks.append(ListBlock(items=self.pending_items))
            self.pending_items = []

    def _append(self, block: object) -> None:
        self._flush_list()
        self.blocks.append(block)

    def _handle(self, tokens: List[Token], i: int) -> int:
        tok = tokens[i]
        t = tok.type

        if t == "heading_open":
            level = int(tok.tag[1])
            runs = inline_runs(tokens[i + 1])
            self._append(Heading(level=level, runs=runs))
            return i + 3  # heading_open / inline / heading_close

        if t == "paragraph_open":
            inline = tokens[i + 1]
            image_block = _standalone_image(inline)
            if image_block is not None and not self.list_stack:
                self._append(image_block)
            else:
                runs = inline_runs(inline)
                if self.list_stack:
                    self._add_list_item(runs)
                elif runs_have_text(runs):
                    self._append(Paragraph(runs=runs, quote=self.quote_depth > 0))
            return i + 3

        if t in ("bullet_list_open", "ordered_list_open"):
            start = tok.attrGet("start")  # `3.` から始まるリストなど
            first = int(start) if start else 1
            self.list_stack.append({"ordered": t.startswith("ordered"), "index": first - 1})
            return i + 1

        if t in ("bullet_list_close", "ordered_list_close"):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self._flush_list()
            return i + 1

        if t == "list_item_open":
            if self.list_stack:
                self.list_stack[-1]["index"] += 1
                self.list_stack[-1]["fresh"] = True
            return i + 1

        if t == "fence" or t == "code_block":
            lang = (tok.info or "").strip().split()[0] if tok.info else ""
            self._append(CodeBlock(text=tok.content.rstrip("\n"), lang=lang))
            return i + 1

        if t == "blockquote_open":
            self.quote_depth += 1
            return i + 1

        if t == "blockquote_close":
            self.quote_depth = max(0, self.quote_depth - 1)
            return i + 1

        if t == "hr":
            self._append(SlideBreak())
            return i + 1

        if t == "table_open":
            return self._handle_table(tokens, i)

        return i + 1

    def _add_list_item(self, runs: List[Run]) -> None:
        """リスト項目の段落。最初の段落だけが行頭、続きは同じ段で追加する。"""
        if not runs_have_text(runs):
            return
        depth = len(self.list_stack) - 1
        frame = self.list_stack[-1]
        fresh = frame.pop("fresh", False)
        number = f"{frame['index']}." if frame["ordered"] and fresh else None
        self.pending_items.append(
            ListItem(
                level=depth,
                runs=runs,
                ordered=bool(frame["ordered"]) and fresh,
                number=number,
                quote=self.quote_depth > 0,
            )
        )

    def _handle_table(self, tokens: List[Token], i: int) -> int:
        header: List[str] = []
        rows: List[List[str]] = []
        row: List[str] = []
        in_header = False
        j = i + 1
        while j < len(tokens) and tokens[j].type != "table_close":
            tok = tokens[j]
            if tok.type == "thead_open":
                in_header = True
            elif tok.type == "thead_close":
                in_header = False
            elif tok.type == "tr_open":
                row = []
            elif tok.type == "tr_close":
                if in_header:
                    header = row
                else:
                    rows.append(row)
            elif tok.type == "inline":
                row.append(runs_text(inline_runs(tok)))
            j += 1
        self._append(Table(header=header, rows=rows))
        return j + 1


# ---------------------------------------------------------------------------
# インライン解析
# ---------------------------------------------------------------------------


def inline_runs(inline_token: Token) -> List[Run]:
    """inline トークンの children を Run 列へ変換する。"""
    runs: List[Run] = []
    bold = 0
    italic = 0
    link: Optional[str] = None
    for child in inline_token.children or []:
        t = child.type
        if t == "text":
            _push(runs, Run(child.content, bold > 0, italic > 0, False, link))
        elif t == "code_inline":
            _push(runs, Run(child.content, bold > 0, italic > 0, True, link))
        elif t == "strong_open":
            bold += 1
        elif t == "strong_close":
            bold = max(0, bold - 1)
        elif t in ("em_open", "s_open"):
            italic += 1
        elif t in ("em_close", "s_close"):
            italic = max(0, italic - 1)
        elif t == "link_open":
            link = child.attrGet("href")
        elif t == "link_close":
            link = None
        elif t == "softbreak":
            _push(runs, Run(" ", bold > 0, italic > 0, False, link))
        elif t == "hardbreak":
            _push(runs, Run("\n", bold > 0, italic > 0, False, link))
        elif t == "image":
            alt = _image_alt(child)
            if alt:
                _push(runs, Run(alt, bold > 0, italic > 0, False, link))
    return _strip_runs(runs)


def _push(runs: List[Run], run: Run) -> None:
    if not run.text:
        return
    if runs:
        last = runs[-1]
        if (
            last.bold == run.bold
            and last.italic == run.italic
            and last.code == run.code
            and last.link == run.link
        ):
            runs[-1] = last.with_text(last.text + run.text)
            return
    runs.append(run)


def _strip_runs(runs: List[Run]) -> List[Run]:
    """先頭・末尾の空白を落とす。"""
    out = list(runs)
    while out and not out[0].text.strip():
        out.pop(0)
    while out and not out[-1].text.strip():
        out.pop()
    if out:
        out[0] = out[0].with_text(out[0].text.lstrip())
        out[-1] = out[-1].with_text(out[-1].text.rstrip())
    return [r for r in out if r.text]


def runs_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def runs_have_text(runs: List[Run]) -> bool:
    return bool(runs_text(runs).strip())


def _image_alt(token: Token) -> str:
    if token.children:
        return "".join(c.content for c in token.children)
    return token.content or ""


def _standalone_image(inline_token: Token) -> Optional[Image]:
    """段落が画像 1 つだけで構成される場合に Image ブロックとして扱う。"""
    children = [c for c in (inline_token.children or []) if c.type != "softbreak"]
    meaningful = [c for c in children if not (c.type == "text" and not c.content.strip())]
    if len(meaningful) == 1 and meaningful[0].type == "image":
        tok = meaningful[0]
        return Image(src=tok.attrGet("src") or "", alt=_image_alt(tok))
    return None
