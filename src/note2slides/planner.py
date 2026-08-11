"""記事のブロック列をスライド構成へ変換する。

方針:
* テキストは原文をそのまま使う。要約・言い換え・補足の生成は行わない。
* 見出しでスライドを区切り、入りきらない分は「続き」スライドへ送る。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from . import metrics
from .model import (
    BULLET,
    KIND_BULLETS,
    KIND_CODE,
    KIND_IMAGE,
    KIND_SECTION,
    KIND_TABLE,
    KIND_TITLE,
    NUMBER,
    QUOTE,
    Article,
    Bullet,
    CodeBlock,
    Deck,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Run,
    Slide,
    SlideBreak,
    Table,
)
from .style import Style

# 文末とみなす文字と、その直後に続けて拾う閉じ記号。
_TERMINATORS = "。．.！？!?"
_CLOSERS = "」』）)”\"'】〉》〕］]"


@dataclass
class PlannerOptions:
    deck_title: Optional[str] = None
    split_sentences: bool = True
    include_notes: bool = True
    continuation_suffix: str = "（続き）"
    max_code_lines: int = 18
    max_table_rows: int = 10
    title_slide: bool = True


def plan_deck(
    article: Article, style: Optional[Style] = None, options: Optional[PlannerOptions] = None
) -> Deck:
    return _Planner(article, style or Style(), options or PlannerOptions()).run()


class _Planner:
    def __init__(self, article: Article, style: Style, options: PlannerOptions) -> None:
        self.article = article
        self.style = style
        self.opt = options
        self.deck = Deck()
        self.current_title = ""
        self.title_use_count: dict = {}
        self.pending: List[Bullet] = []
        self.pending_notes: List[str] = []

    # -- 実行 -----------------------------------------------------------
    def run(self) -> Deck:
        blocks = list(self.article.blocks)
        deck_title, blocks = self._resolve_title(blocks)
        self.deck.title = deck_title

        if self.opt.title_slide:
            self.deck.slides.append(
                Slide(
                    kind=KIND_TITLE,
                    title=deck_title,
                    subtitle=self._subtitle(),
                )
            )

        for block in blocks:
            self._handle(block)
        self._flush()
        return self.deck

    def _resolve_title(self, blocks: List[object]):
        """デッキタイトルを決める。

        記事の先頭にある H1 は記事タイトルとみなす。それが表紙のタイトルと同じ
        内容になる場合だけ本文から取り除き、表紙と同じ見出しが二重に出るのを防ぐ。
        指定タイトルと異なる場合は、本文の情報を落とさないようそのまま残す。
        """
        heading_index, heading_text = self._leading_h1(blocks)
        explicit = self.opt.deck_title or self.article.meta.get("title")

        if explicit:
            title = explicit
        elif heading_text is not None:
            title = heading_text
        elif self.article.source_path:
            title = os.path.splitext(os.path.basename(self.article.source_path))[0]
        else:
            title = ""

        if heading_index is not None and heading_text == title:
            blocks = blocks[:heading_index] + blocks[heading_index + 1 :]
        return title, blocks

    def _leading_h1(self, blocks: List[object]):
        """本文が始まる前に現れる最初の H1 を探す。"""
        for i, block in enumerate(blocks):
            if isinstance(block, Heading):
                return (i, _text(block.runs)) if block.level == 1 else (None, None)
            if isinstance(block, (Paragraph, ListBlock, CodeBlock, Table, Image)):
                break
        return None, None

    def _subtitle(self) -> str:
        meta = self.article.meta
        for key in ("subtitle", "description", "summary"):
            if meta.get(key):
                return meta[key]
        parts = [meta[k] for k in ("author", "date") if meta.get(k)]
        return "  ".join(parts)

    # -- ブロック処理 ---------------------------------------------------
    def _handle(self, block: object) -> None:
        if isinstance(block, Heading):
            self._flush()
            if block.level <= 1:
                self.deck.slides.append(Slide(kind=KIND_SECTION, title=_text(block.runs)))
                self.current_title = _text(block.runs)
                self.title_use_count.pop(self.current_title, None)
            else:
                self.current_title = _text(block.runs)
                self.title_use_count.pop(self.current_title, None)
            return

        if isinstance(block, SlideBreak):
            self._flush()
            return

        if isinstance(block, Paragraph):
            kind = QUOTE if block.quote else BULLET
            for runs in self._split_sentences(block.runs):
                self.pending.append(Bullet(runs=runs, level=0, kind=kind))
            self.pending_notes.append(_text(block.runs))
            return

        if isinstance(block, ListBlock):
            for item in block.items:
                kind = QUOTE if item.quote else (NUMBER if item.ordered else BULLET)
                self.pending.append(
                    Bullet(runs=item.runs, level=min(item.level, 3), kind=kind, number=item.number)
                )
                self.pending_notes.append(_text(item.runs))
            return

        if isinstance(block, CodeBlock):
            self._flush()
            for chunk in _chunk_lines(block.text, self.opt.max_code_lines):
                self.deck.slides.append(
                    Slide(
                        kind=KIND_CODE,
                        title=self._next_title(),
                        code=chunk,
                        code_lang=block.lang,
                    )
                )
            return

        if isinstance(block, Table):
            self._flush()
            rows = block.rows or []
            chunks = [
                rows[i : i + self.opt.max_table_rows]
                for i in range(0, len(rows), self.opt.max_table_rows)
            ] or [[]]
            for chunk in chunks:
                self.deck.slides.append(
                    Slide(
                        kind=KIND_TABLE,
                        title=self._next_title(),
                        table_header=block.header,
                        table_rows=chunk,
                    )
                )
            return

        if isinstance(block, Image):
            self._handle_image(block)
            return

    def _handle_image(self, block: Image) -> None:
        path = self._resolve_image(block.src)
        if path is None:
            # 参照先が見つからない画像は本文に事実として残す(内容は補わない)。
            self.deck.warnings.append(f"画像が見つかりません: {block.src}")
            label = block.alt or block.src
            self.pending.append(Bullet(runs=[Run(f"[画像] {label}")], level=0, kind=BULLET))
            return
        self._flush()
        self.deck.slides.append(
            Slide(kind=KIND_IMAGE, title=self._next_title(), image_path=path, image_alt=block.alt)
        )

    def _resolve_image(self, src: str) -> Optional[str]:
        if not src or "://" in src:
            return None
        base = os.path.dirname(os.path.abspath(self.article.source_path or "."))
        candidate = src if os.path.isabs(src) else os.path.join(base, src)
        return candidate if os.path.isfile(candidate) else None

    # -- スライド確定 ---------------------------------------------------
    def _flush(self) -> None:
        """溜まった箇条書きを、容量に収まる単位でスライドへ切り出す。"""
        if not self.pending:
            self.pending_notes = []
            return
        pages = self._paginate(self.pending)
        notes = "\n".join(n for n in self.pending_notes if n) if self.opt.include_notes else ""
        for index, page in enumerate(pages):
            self.deck.slides.append(
                Slide(
                    kind=KIND_BULLETS,
                    title=self._next_title(),
                    bullets=page,
                    notes=notes if index == 0 else "",
                )
            )
        self.pending = []
        self.pending_notes = []

    def _paginate(self, bullets: List[Bullet]) -> List[List[Bullet]]:
        limit = self.style.body_height_pt
        pages: List[List[Bullet]] = []
        page: List[Bullet] = []
        used = 0.0
        for bullet in bullets:
            height = self._bullet_height(bullet, first=not page)
            if page and used + height > limit:
                pages.append(page)
                page = []
                used = self._bullet_height(bullet, first=True)
                page.append(bullet)
                continue
            page.append(bullet)
            used += height
        if page:
            pages.append(page)
        return pages

    def _bullet_height(self, bullet: Bullet, first: bool) -> float:
        size = self.style.body_size(bullet.level)
        avail = self.style.body_width_pt - self.style.bullet_indent_pt(bullet.level)
        lines = metrics.line_count(bullet.text, size, avail)
        height = lines * self.style.line_height_pt(size)
        if not first:
            height += self.style.space_before_pt
        return height

    def _next_title(self) -> str:
        """同じ見出しで複数スライドになる場合に「続き」を付ける。"""
        title = self.current_title
        count = self.title_use_count.get(title, 0)
        self.title_use_count[title] = count + 1
        if count == 0 or not title:
            return title
        return f"{title}{self.opt.continuation_suffix}"

    # -- 文分割 ---------------------------------------------------------
    def _split_sentences(self, runs: List[Run]) -> List[List[Run]]:
        if not self.opt.split_sentences:
            return [runs] if runs else []
        return split_sentences(runs)


def split_sentences(runs: List[Run]) -> List[List[Run]]:
    """Run 列を文単位へ分割する。文字は削除も追加もしない。"""
    sentences: List[List[Run]] = []
    current: List[Run] = []
    for run in runs:
        text = run.text
        start = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch in _TERMINATORS and (ch not in ".．" or _ascii_period_end(text, i)):
                end = i + 1
                while end < len(text) and text[end] in _CLOSERS:
                    end += 1
                current.append(run.with_text(text[start:end]))
                sentences.append(current)
                current = []
                start = end
                i = end
                continue
            i += 1
        if start < len(text):
            current.append(run.with_text(text[start:]))
    if current:
        sentences.append(current)
    return [_trim(s) for s in sentences if _text(s).strip()]


def _ascii_period_end(text: str, i: int) -> bool:
    """半角ピリオドは、後ろが空白か行末のときだけ文末とみなす。"""
    return i + 1 >= len(text) or text[i + 1] in " \t"


def _trim(runs: List[Run]) -> List[Run]:
    out = [r.with_text(r.text) for r in runs]
    if out:
        out[0] = out[0].with_text(out[0].text.lstrip())
        out[-1] = out[-1].with_text(out[-1].text.rstrip())
    return [r for r in out if r.text]


def _text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def _chunk_lines(text: str, max_lines: int) -> List[str]:
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return ["\n".join(lines)]
    return ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]
