"""記事のブロック列をスライド構成へ変換する。

方針:
* テキストは原文をそのまま使う。要約・言い換え・補足の生成は行わない。
* 見出しでスライドを区切り、入りきらない分は「続き」スライドへ送る。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

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

# 括弧の中の句点・疑問符は、文の終わりではない。
#
#     今回は「そもそもAIは文章をどう扱っているのか？」というところから整理します。
#
# 「？」で切ると、1 つの文が 2 行に割れて画面にも読み上げにも出てしまう。
# そこで対応の取れる括弧だけを数え、開いている間は文を切らない。閉じ忘れが
# あった場合は切らない側に倒れるが、切り所を間違えるより行が長くなるほうが害が
# 小さい(長い行は読み上げ側が読点で区切る → reading.ReadingStyle.max_chars)。
_BRACKET_PAIRS = {
    "「": "」", "『": "』", "（": "）", "(": ")", "【": "】", "〈": "〉",
    "《": "》", "〔": "〕", "［": "］", "[": "]", "｛": "｝", "{": "}",
    "“": "”", "‘": "’",
}
_OPENERS = "".join(_BRACKET_PAIRS)
_CLOSE_TO_OPEN = {close: open_ for open_, close in _BRACKET_PAIRS.items()}


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
        # 溜めている箇条書きが、記事のどのブロック(段落・項目)から来たか。
        # スライドが分かれたときに、そのスライドに出ている分だけを読み上げる
        # ノートにするために使う。
        self.pending_blocks: List[int] = []
        self.block_texts: Dict[int, str] = {}
        self.block_serial = 0

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
            source = self._open_block(_text(block.runs))
            for runs in self._split_sentences(block.runs):
                self._add(Bullet(runs=runs, level=0, kind=kind), source)
            return

        if isinstance(block, ListBlock):
            for item in block.items:
                kind = QUOTE if item.quote else (NUMBER if item.ordered else BULLET)
                self._add(
                    Bullet(runs=item.runs, level=min(item.level, 3), kind=kind, number=item.number),
                    self._open_block(_text(item.runs)),
                )
            return

        if isinstance(block, CodeBlock):
            self._flush()
            for index, chunk in enumerate(_chunk_lines(block.text, self.opt.max_code_lines)):
                self.deck.slides.append(
                    Slide(
                        kind=KIND_CODE,
                        title=self._next_title(),
                        code=chunk,
                        code_lang=block.lang,
                        continued=index > 0,
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
            for index, chunk in enumerate(chunks):
                self.deck.slides.append(
                    Slide(
                        kind=KIND_TABLE,
                        title=self._next_title(),
                        table_header=block.header,
                        table_rows=chunk,
                        continued=index > 0,
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
            text = f"[画像] {label}"
            self._add(Bullet(runs=[Run(text)], level=0, kind=BULLET), self._open_block(text))
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
    def _open_block(self, text: str) -> int:
        """記事のブロック(段落・項目)を 1 つ開き、その番号を返す。"""
        self.block_serial += 1
        self.block_texts[self.block_serial] = text
        return self.block_serial

    def _add(self, bullet: Bullet, block: int) -> None:
        self.pending.append(bullet)
        self.pending_blocks.append(block)

    def _flush(self) -> None:
        """溜まった箇条書きを、容量に収まる単位でスライドへ切り出す。"""
        if not self.pending:
            self._clear_pending()
            return
        pages = self._paginate(self.pending)
        totals: Dict[int, int] = {}
        for block in self.pending_blocks:
            totals[block] = totals.get(block, 0) + 1

        cursor = 0
        for page in pages:
            blocks = self.pending_blocks[cursor : cursor + len(page)]
            cursor += len(page)
            self.deck.slides.append(
                Slide(
                    kind=KIND_BULLETS,
                    title=self._next_title(),
                    bullets=page,
                    notes=self._notes_for(page, blocks, totals) if self.opt.include_notes else "",
                )
            )
        self._clear_pending()

    def _clear_pending(self) -> None:
        self.pending = []
        self.pending_blocks = []
        self.block_texts = {}

    def _notes_for(self, page: List[Bullet], blocks: List[int], totals: Dict[int, int]) -> str:
        """そのスライドに出ている行の、記事での文章を集める。

        ナレーションは画面に出ている内容と対応している必要がある。1 つの段落が
        次のスライドへ分かれた場合は、そのスライドに出ている文だけを読み上げる
        (分かれていなければ、段落を 1 文につなげた元の文章をそのまま読む)。

        `blocks` はこのスライドの各行がどのブロックから来たか、`totals` は
        ブロックごとの行数(全スライド分)。
        """
        lines: List[str] = []
        position = 0
        while position < len(blocks):
            end = position
            while end < len(blocks) and blocks[end] == blocks[position]:
                end += 1
            whole = end - position == totals[blocks[position]]
            text = (
                self.block_texts[blocks[position]]
                if whole
                else " ".join(b.text for b in page[position:end])
            )
            if text.strip():
                lines.append(text.strip())
            position = end
        return "\n".join(lines)

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
    depth = 0  # 開いている括弧の数(中の句点では切らない)
    for run in runs:
        text = run.text
        start = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch in _OPENERS:
                depth += 1
            elif ch in _CLOSE_TO_OPEN:
                depth = max(0, depth - 1)
            elif depth == 0 and ch in _TERMINATORS and (ch not in ".．" or _ascii_period_end(text, i)):
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
