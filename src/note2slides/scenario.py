"""教材シナリオ(Markdown)をスライド構成へ変換する。

記事入力(`markdown_reader` + `planner`)は、記事の文章構造からスライド構成を
推測する。教材シナリオはその逆で、「この画面にこれを出し、こう説明する」を
人(または AI)が先に決めて書いたものである。ここでは推測を一切しない。
書いてあるとおりの枚数・順番・文言でスライドとナレーションを作る。

    ## スライドの見出し      1 枚 = 1 つの `##`(見出しがそのままタイトル)
    ### 画面                 画面に出すもの(文章・箇条書き・図・表・コード)
    ### ナレーション         その画面で読み上げる文章
    ### 設定                 レイアウト・見る時間

1 つの画面には、文章と図、表と補足の文のように複数の中身を書ける。書いた順に
上から縦へ並ぶ(置き場所の決め方は `layout.py`)。ナレーションは画面ごとに
1 つなので、中身が増えても「この画面ではこう説明する」という対応は変わらない。

画面とナレーションの中身は Markdown のままなので、解析には `markdown_reader`
をそのまま使う。できあがるのは記事入力とまったく同じ `Deck` で、そこから先
(資料・スライド画像・ナレーション音声・動画)の処理は 1 つも分岐しない。

ナレーションは発表者ノートに入れる。ノートがあれば `narration.py` はそれを
そのまま読み上げるので、シナリオに書いた文章が画面と 1 対 1 で対応する。
「ナレーションを置かない画面」と「読み終わったあとに画面を見せる時間」だけは
ノートの文章では表せないため、`narration.py` の指示行として残す。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import layout as layout_mod
from . import narration as narration_mod
from . import planner as planner_mod
from .markdown_reader import parse_article, runs_text, split_front_matter
from .model import (
    BULLET,
    KIND_BULLETS,
    KIND_CODE,
    KIND_CONTENT,
    KIND_DIAGRAM,
    KIND_IMAGE,
    KIND_SECTION,
    KIND_TABLE,
    KIND_TITLE,
    NUMBER,
    PLAIN,
    QUOTE,
    Bullet,
    CodeBlock,
    Content,
    Deck,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Slide,
    SlideBreak,
    Table,
    DIAGRAM_BOUNDARY,
    DIAGRAM_LANES,
    boundary_parts,
    lane_parts,
    diagram_shape_of,
)
from .style import Style

#: front matter の `type`。この宣言がある Markdown だけを教材シナリオとして扱う。
#: 記事とシナリオは書き方が違うので、取り違えたまま生成しないようにする。
SCENARIO_TYPE = "scenario"

#: 画面の見せ方。
LAYOUT_BODY = "本文"
LAYOUT_SECTION = "章扉"
LAYOUT_TITLE = "表紙"

_LAYOUTS = {
    "本文": LAYOUT_BODY,
    "body": LAYOUT_BODY,
    "章扉": LAYOUT_SECTION,
    "section": LAYOUT_SECTION,
    "表紙": LAYOUT_TITLE,
    "title": LAYOUT_TITLE,
    "cover": LAYOUT_TITLE,
}

_SCREEN = "screen"
_NARRATION = "narration"
_SETTINGS = "settings"

_SECTIONS = {
    "画面": _SCREEN,
    "screen": _SCREEN,
    "ナレーション": _NARRATION,
    "narration": _NARRATION,
    "設定": _SETTINGS,
    "settings": _SETTINGS,
}
_SECTION_LABEL = {_SCREEN: "画面", _NARRATION: "ナレーション", _SETTINGS: "設定"}

_KEY_LAYOUT = "layout"
_KEY_HOLD = "hold"
_KEYS = {
    "レイアウト": _KEY_LAYOUT,
    "layout": _KEY_LAYOUT,
    "見る時間": _KEY_HOLD,
    "hold": _KEY_HOLD,
}
_KEY_LABEL = {_KEY_LAYOUT: "レイアウト", _KEY_HOLD: "見る時間"}

_SLIDE_HEADING = re.compile(r"^##(?!#)\s*(.*)$")
_SECTION_HEADING = re.compile(r"^###(?!#)\s*(.*)$")
_DECK_HEADING = re.compile(r"^#(?!#)\s*(.*)$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SECONDS = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:秒|s|sec|secs|second|seconds)?$", re.IGNORECASE)


class ScenarioError(RuntimeError):
    """教材シナリオを読み取れなかった場合。

    シナリオは動画構成の正本なので、書き間違いは黙って別の構成に倒さず、
    どこを直せばよいか(ファイルと行、スライド番号と見出し)を添えて止める。
    """


# ---------------------------------------------------------------------------
# シナリオの内容
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSlide:
    """1 画面分の指定。`line` はシナリオでの `##` の行(エラー表示に使う)。"""

    number: int
    line: int
    title: str = ""
    layout: str = LAYOUT_BODY
    screen: str = ""  # 画面セクションの Markdown(そのまま)
    narration: str = ""  # ナレーションセクションの Markdown(そのまま)
    hold: float = 0.0  # 読み終わったあとに画面を見せる時間(秒)


@dataclass
class Scenario:
    slides: List[ScenarioSlide] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    title: str = ""
    source_path: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self.slides)


# ---------------------------------------------------------------------------
# 入力の種別
# ---------------------------------------------------------------------------


def is_scenario_text(text: str) -> bool:
    meta, _ = split_front_matter(text)
    return str(meta.get("type", "")).strip().lower() == SCENARIO_TYPE


def is_scenario_file(path: str) -> bool:
    """front matter の `type: scenario` だけで判断する(本文は見ない)。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            head = f.read(4096)
    except OSError:
        return False
    return is_scenario_text(head)


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def read_scenario(path: str) -> Scenario:
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as exc:
        raise ScenarioError(f"教材シナリオを開けませんでした: {path}\n{exc}") from exc
    return parse_scenario(text, source_path=path)


def parse_scenario(text: str, source_path: Optional[str] = None) -> Scenario:
    """教材シナリオ(Markdown)を読み取る。"""
    meta, body = split_front_matter(text)
    if str(meta.get("type", "")).strip().lower() != SCENARIO_TYPE:
        raise ScenarioError(
            f"教材シナリオではありません: {source_path or '(入力)'}\n"
            f"  先頭の front matter に `type: {SCENARIO_TYPE}` を書いてください。\n"
            "  記事から構成を組み立てる場合は、この指定を付けずに渡してください。"
        )
    offset = text[: len(text) - len(body)].count("\n")
    return _Reader(body, meta, offset, source_path).run()


class _Reader:
    """行を順に見て、`##` ごとのスライドと `###` ごとのセクションに分ける。"""

    def __init__(self, body: str, meta: dict, offset: int, source_path: Optional[str]) -> None:
        self.body = body
        self.meta = meta
        self.offset = offset  # front matter のぶんの行数(行番号を元の位置に戻す)
        self.source_path = source_path
        # 資料全体のタイトルは front matter の `title` を優先し、無ければ本文の
        # `#` 見出し、それも無ければ表紙のスライドの見出しを使う。
        self.scenario = Scenario(
            meta=meta, title=str(meta.get("title", "")).strip(), source_path=source_path
        )
        self.slide: Optional[ScenarioSlide] = None
        self.section: Optional[str] = None
        self.section_line = 0
        self.seen: dict = {}  # セクション -> 最初に現れた行
        self.lines: List[str] = []
        self.settings: List[Tuple[int, str]] = []

    # -- 実行 -----------------------------------------------------------
    def run(self) -> Scenario:
        fence = ""
        for number, raw in enumerate(self.body.splitlines(), start=self.offset + 1):
            match = _FENCE.match(raw)
            if match:
                # コードブロックの中の `##` は画面に出す文字なので、見出しにしない。
                marker = match.group(1)
                if not fence:
                    fence = marker[0]
                elif marker[0] == fence:
                    fence = ""
                self._content(number, raw)
                continue
            if fence:
                self._content(number, raw)
                continue

            slide = _SLIDE_HEADING.match(raw)
            if slide:
                self._open_slide(number, slide.group(1).strip())
                continue
            section = _SECTION_HEADING.match(raw)
            if section:
                self._open_section(number, section.group(1).strip())
                continue
            deck = _DECK_HEADING.match(raw)
            if deck:
                self._deck_heading(number, deck.group(1).strip())
                continue
            self._content(number, raw)

        self._close_slide()
        if not self.scenario.slides:
            raise ScenarioError(
                f"スライドが 1 枚もありません: {self._name()}\n"
                "  `## 見出し` を 1 つ書くと 1 枚になります。"
            )
        self.scenario.title = self.scenario.title or self._default_title()
        return self.scenario

    # -- 見出し ---------------------------------------------------------
    def _deck_heading(self, number: int, title: str) -> None:
        if self.slide is not None:
            raise ScenarioError(
                f"{self._at(number)}: スライドの中に `#` の見出しは書けません。\n"
                "  次の画面へ移る場合は `##`、画面の中の区切りは箇条書きにしてください。"
            )
        if not self.scenario.title:
            self.scenario.title = title

    def _open_slide(self, number: int, title: str) -> None:
        self._close_slide()
        self.slide = ScenarioSlide(number=len(self.scenario.slides) + 1, line=number, title=title)
        self.section = None
        self.seen = {}
        self.lines = []
        self.settings = []

    def _open_section(self, number: int, name: str) -> None:
        if self.slide is None:
            raise ScenarioError(
                f"{self._at(number)}: `### {name}` の前に `## 見出し` がありません。\n"
                "  どの画面についての指定かが決まらないため、先に `##` を書いてください。"
            )
        self._close_section()
        key = _SECTIONS.get(name.strip().lower()) or _SECTIONS.get(name.strip())
        if key is None:
            raise ScenarioError(
                f"{self._at(number)}: 知らない見出しです: {name}\n"
                "  書けるのは "
                + " / ".join(f"### {label}" for label in _SECTION_LABEL.values())
                + " です。"
            )
        if key in self.seen:
            raise ScenarioError(
                f"{self._at(number)}: `### {_SECTION_LABEL[key]}` が"
                f" {self.seen[key]} 行目と重複しています。\n"
                "  1 枚につき 1 つにまとめてください。"
            )
        self.seen[key] = number
        self.section = key
        self.section_line = number
        self.lines = []

    def _content(self, number: int, raw: str) -> None:
        if not raw.strip():
            if self.section is not None:
                self.lines.append(raw)
            return
        if self.slide is None:
            raise ScenarioError(
                f"{self._at(number)}: どの画面のものか分からない文章があります。\n"
                "  教材シナリオでは、すべての文章を `## 見出し` の下の"
                " `### 画面` か `### ナレーション` に書いてください。"
            )
        if self.section is None:
            raise ScenarioError(
                f"{self._at(number)}: `## {self.slide.title}` の直下に文章があります。\n"
                "  画面に出す内容は `### 画面`、読み上げる文章は `### ナレーション` に"
                "書いてください(画面とナレーションを取り違えないためです)。"
            )
        if self.section == _SETTINGS:
            self.settings.append((number, raw))
            return
        self.lines.append(raw)

    # -- 確定 -----------------------------------------------------------
    def _close_section(self) -> None:
        if self.slide is None or self.section is None:
            return
        text = "\n".join(self.lines).strip("\n")
        if self.section == _SCREEN:
            self.slide.screen = text
        elif self.section == _NARRATION:
            self.slide.narration = text
        self.section = None
        self.lines = []

    def _close_slide(self) -> None:
        if self.slide is None:
            return
        self._close_section()
        self._apply_settings(self.slide)
        self.scenario.slides.append(self.slide)
        self.slide = None

    def _apply_settings(self, slide: ScenarioSlide) -> None:
        for number, raw in self.settings:
            line = _LIST_MARKER.sub("", raw).strip()
            if not line:
                continue
            name, separator, value = _partition_setting(line)
            if not separator:
                raise ScenarioError(
                    f"{self._at(number)}: 設定は「キー: 値」の形で書いてください: {line}\n"
                    "  書けるのは " + " / ".join(_KEY_LABEL.values()) + " です。"
                )
            key = _KEYS.get(name.strip().lower()) or _KEYS.get(name.strip())
            if key is None:
                raise ScenarioError(
                    f"{self._at(number)}: 知らない設定です: {name.strip()}\n"
                    "  書けるのは " + " / ".join(_KEY_LABEL.values()) + " です。"
                )
            value = value.strip()
            if key == _KEY_LAYOUT:
                slide.layout = self._layout(number, value)
            else:
                slide.hold = self._seconds(number, value)

    def _layout(self, number: int, value: str) -> str:
        layout = _LAYOUTS.get(value.lower()) or _LAYOUTS.get(value)
        if layout is None:
            raise ScenarioError(
                f"{self._at(number)}: 知らないレイアウトです: {value}\n"
                "  指定できるのは " + " / ".join(sorted({LAYOUT_BODY, LAYOUT_SECTION, LAYOUT_TITLE}))
                + " です(既定は " + LAYOUT_BODY + ")。"
            )
        return layout

    def _seconds(self, number: int, value: str) -> float:
        match = _SECONDS.match(value)
        if not match:
            raise ScenarioError(
                f"{self._at(number)}: 見る時間は秒数で書いてください: {value}\n"
                "  例: `見る時間: 3` / `見る時間: 2.5秒`"
            )
        return float(match.group(1))

    # -- 表示用 ---------------------------------------------------------
    def _name(self) -> str:
        return os.path.basename(self.source_path) if self.source_path else "教材シナリオ"

    def _at(self, number: int) -> str:
        return f"{self._name()} の {number} 行目"

    def _default_title(self) -> str:
        for slide in self.scenario.slides:
            if slide.layout == LAYOUT_TITLE and slide.title:
                return slide.title
        if self.source_path:
            return os.path.splitext(os.path.basename(self.source_path))[0]
        return self.scenario.slides[0].title


def _partition_setting(line: str) -> Tuple[str, str, str]:
    """`キー: 値` を分ける(全角のコロンも受け付ける)。"""
    for separator in (":", "："):
        name, found, value = line.partition(separator)
        if found:
            return name, found, value
    return line, "", ""


# ---------------------------------------------------------------------------
# スライド構成へ
# ---------------------------------------------------------------------------


def build_deck(
    scenario: Scenario, style: Optional[Style] = None, deck_title: Optional[str] = None
) -> Deck:
    """教材シナリオを、記事入力と同じ `Deck` にする。

    枚数・順番・画面の内容・ナレーションは、すべてシナリオに書かれたとおりに
    する。1 枚に収まらない場合も勝手に分けず、警告して知らせる(分け方を決める
    のはシナリオを書いた人の仕事で、こちらが決めると画面とナレーションの対応が
    崩れる)。
    """
    style = style or Style()
    deck = Deck(title=deck_title or scenario.title)
    for slide in scenario.slides:
        deck.slides.append(_build_slide(scenario, slide, style, deck.warnings))
    return deck


def _build_slide(
    scenario: Scenario, source: ScenarioSlide, style: Style, warnings: List[str]
) -> Slide:
    blocks = _screen_blocks(scenario, source)
    notes = narration_mod.compose_notes(_narration_text(scenario, source), hold=source.hold)

    if source.layout == LAYOUT_TITLE:
        return Slide(
            kind=KIND_TITLE,
            title=source.title,
            subtitle=_subtitle(scenario, source, blocks),
            notes=notes,
        )
    if source.layout == LAYOUT_SECTION:
        if blocks:
            raise ScenarioError(
                f"{_where(scenario, source)}: 章扉には画面の内容を書けません。\n"
                "  見出しだけを大きく映すレイアウトです。内容も出す場合は"
                f" `レイアウト: {LAYOUT_BODY}` にしてください。"
            )
        return Slide(kind=KIND_SECTION, title=source.title, notes=notes)

    parts = _screen_parts(scenario, source, blocks, warnings)
    _warn_if_crowded(scenario, source, parts, style, warnings)
    if not parts:  # 見出しだけの画面
        return Slide(kind=KIND_BULLETS, title=source.title, notes=notes)
    if len(parts) == 1:
        return parts[0].as_slide(title=source.title, notes=notes)
    return Slide(kind=KIND_CONTENT, title=source.title, parts=parts, notes=notes)


def _screen_parts(
    scenario: Scenario, source: ScenarioSlide, blocks: List[object], warnings: List[str]
) -> List[Content]:
    """画面のブロックを、上から順に並べる中身にする。

    続いている文章(段落・箇条書き)はひとまとまりの中身にし、図・表・コードは
    それぞれ 1 つの中身にする。並べ替えはしない(シナリオに書いた順が、画面の
    上から下になる)。
    """
    parts: List[Content] = []
    texts: List[object] = []
    for block in blocks:
        if isinstance(block, (Paragraph, ListBlock)):
            texts.append(block)
            continue
        if texts:
            parts.append(_text_part(texts))
            texts = []
        if isinstance(block, Image):
            parts.append(_image_part(scenario, source, block))
        elif isinstance(block, Table):
            parts.append(_table_part(scenario, source, block, warnings))
        elif isinstance(block, CodeBlock):
            shape = diagram_shape_of(block.lang)
            if shape:
                parts.append(_diagram_part(scenario, source, block, shape, warnings))
            else:
                parts.append(_code_part(scenario, source, block, warnings))
    if texts:
        parts.append(_text_part(texts))
    return parts


def _warn_if_crowded(
    scenario: Scenario,
    source: ScenarioSlide,
    parts: List[Content],
    style: Style,
    warnings: List[str],
) -> None:
    """1 枚に収まらない見込みなら知らせる(勝手には分けない)。

    分け方を決めるのはシナリオを書いた人の仕事で、こちらが決めると画面と
    ナレーションの対応が崩れる。小さく表示して収めたうえで、どの画面かを伝える。
    """
    placed = layout_mod.fit(parts, style, layout_mod.body_box(style))
    if placed.fits:
        return
    warnings.append(
        f"{_where(scenario, source)}: 画面に置いたものが多く、1 枚に収まらない"
        f"可能性があります（目安の {placed.overflow:.1f} 倍）。"
        "小さく表示して収めますが、減らすか `##` で画面を分けると読みやすくなります。"
    )


def _screen_blocks(scenario: Scenario, source: ScenarioSlide) -> List[object]:
    """画面セクションの Markdown をブロック列にする。"""
    if not source.screen.strip():
        return []
    article = parse_article(source.screen, source_path=scenario.source_path)
    blocks = []
    for block in article.blocks:
        if isinstance(block, Heading):
            raise ScenarioError(
                f"{_where(scenario, source)}: 画面の中に見出しは書けません:"
                f" {runs_text(block.runs)}\n"
                "  画面のタイトルは `##` の見出しです。中身は文章・箇条書き・図・表・"
                "コードで書いてください。"
            )
        if isinstance(block, SlideBreak):
            raise ScenarioError(
                f"{_where(scenario, source)}: 画面の中に区切り線(`---`)があります。\n"
                "  画面を分ける場合は `##` を足してください。"
            )
        blocks.append(block)
    return blocks


def _narration_text(scenario: Scenario, source: ScenarioSlide) -> str:
    """ナレーションセクションを、読み上げる行の並びにする。

    空行で分けた段落が 1 行になる(行の間には `reading.ReadingStyle.line_pause`
    の間が入る)。段落の中の改行はつながって 1 行になるので、書きやすい幅で
    折り返して構わない。
    """
    if not source.narration.strip():
        return ""
    article = parse_article(source.narration, source_path=scenario.source_path)
    lines: List[str] = []
    for block in article.blocks:
        if isinstance(block, Paragraph):
            lines.append(runs_text(block.runs).strip())
        elif isinstance(block, ListBlock):
            lines.extend(runs_text(item.runs).strip() for item in block.items)
        elif isinstance(block, SlideBreak):
            continue
        else:
            raise ScenarioError(
                f"{_where(scenario, source)}: ナレーションには文章だけを書いてください"
                f"（{_block_label(block)}は読み上げられません）。\n"
                "  画面に出すものは `### 画面` に書き、その内容の説明を"
                "ナレーションとして書いてください。"
            )
    return "\n".join(line for line in lines if line)


def _subtitle(scenario: Scenario, source: ScenarioSlide, blocks: List[object]) -> str:
    """表紙の副題。画面に書いた文章をそのまま副題にする。"""
    lines: List[str] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            lines.append(runs_text(block.runs).strip())
        elif isinstance(block, ListBlock):
            lines.extend(runs_text(item.runs).strip() for item in block.items)
        else:
            raise ScenarioError(
                f"{_where(scenario, source)}: 表紙の画面に置けるのは文章だけです"
                f"（{_block_label(block)}は置けません）。\n"
                f"  図や表を出す場合は `レイアウト: {LAYOUT_BODY}` にしてください。"
            )
    return "  ".join(line for line in lines if line)


def _image_part(scenario: Scenario, source: ScenarioSlide, block: Image) -> Content:
    path = _resolve_image(scenario, block.src)
    if path is None:
        base = os.path.dirname(os.path.abspath(scenario.source_path or "."))
        raise ScenarioError(
            f"{_where(scenario, source)}: 画面に指定した図が見つかりません: {block.src}\n"
            f"  探した場所: {os.path.join(base, block.src) if '://' not in block.src else block.src}\n"
            "  シナリオのある場所からの相対パスで書いてください"
            "(教材シナリオでは、指定した図が無いまま動画にはしません)。"
        )
    return Content(kind=KIND_IMAGE, image_path=path, image_alt=block.alt)


def _resolve_image(scenario: Scenario, src: str) -> Optional[str]:
    if not src or "://" in src:
        return None
    base = os.path.dirname(os.path.abspath(scenario.source_path or "."))
    candidate = src if os.path.isabs(src) else os.path.join(base, src)
    return candidate if os.path.isfile(candidate) else None


def _table_part(
    scenario: Scenario, source: ScenarioSlide, block: Table, warnings: List[str]
) -> Content:
    if len(block.rows) > planner_mod.PlannerOptions.max_table_rows:
        warnings.append(
            f"{_where(scenario, source)}: 表の行数が {len(block.rows)} 行あります"
            f"（1 枚に収まるのは {planner_mod.PlannerOptions.max_table_rows} 行程度です）。"
        )
    return Content(kind=KIND_TABLE, table_header=block.header, table_rows=block.rows)


def _code_part(
    scenario: Scenario, source: ScenarioSlide, block: CodeBlock, warnings: List[str]
) -> Content:
    lines = block.text.count("\n") + 1
    if lines > planner_mod.PlannerOptions.max_code_lines:
        warnings.append(
            f"{_where(scenario, source)}: コードが {lines} 行あります"
            f"（1 枚に収まるのは {planner_mod.PlannerOptions.max_code_lines} 行程度です）。"
        )
    return Content(kind=KIND_CODE, code=block.text, code_lang=block.lang)


#: 1 枚に無理なく置ける図解の項目数。これを超えると 1 つ 1 つが小さくなる。
MAX_DIAGRAM_ITEMS = 6


def _diagram_part(
    scenario: Scenario,
    source: ScenarioSlide,
    block: CodeBlock,
    shape: str,
    warnings: List[str],
) -> Content:
    """図解のブロック。1 行が 1 つの項目になる(空行は読み飛ばす)。"""
    items = [line.strip() for line in block.text.splitlines() if line.strip()]
    if not items:
        raise ScenarioError(
            f"{_where(scenario, source)}: 図の中身が空です。\n"
            "  1 行に 1 つずつ、図に出す項目を書いてください。"
        )
    if len(items) > MAX_DIAGRAM_ITEMS:
        warnings.append(
            f"{_where(scenario, source)}: 図の項目が {len(items)} 個あります"
            f"（1 枚に収まるのは {MAX_DIAGRAM_ITEMS} 個程度です）。"
        )
    if shape == DIAGRAM_BOUNDARY:
        _check_boundary(scenario, source, items)
    if shape == DIAGRAM_LANES:
        _check_lanes(scenario, source, items)
    return Content(kind=KIND_DIAGRAM, diagram_shape=shape, diagram_items=items)


def _check_boundary(scenario: Scenario, source: ScenarioSlide, items: List[str]) -> None:
    """境界図の書き方を確かめる。

    線の位置を決めるのは `↓` / `↑` の行そのものなので、それが無い・
    2 か所に分かれている・片側に何も無い、のいずれでも図が決まらない。
    ここで止めないと **線の無い境界図** が資料に出て、画像を見るまで
    分からない(gen27 以降、図の崩れは目で見るまで分からないのが前提)。
    """
    parts = boundary_parts(items)
    where = _where(scenario, source)
    if not parts.crossings:
        raise ScenarioError(
            f"{where}: 境界図に、線をまたぐものが書かれていません。\n"
            "  ↓ か ↑ で始まる行を 1 つ以上書いてください"
            "（その行の位置が、線の位置になります）。\n"
            "  例: ↑ AIだけで決められないこと"
        )
    if parts.split:
        raise ScenarioError(
            f"{where}: 境界図の ↓ ↑ の行が離れています。\n"
            "  線は 1 本なので、またぐものは続けて書いてください。"
        )
    if not parts.upper or not parts.lower:
        raise ScenarioError(
            f"{where}: 境界図の、線の{'上' if not parts.upper else '下'}に何もありません。\n"
            "  ↓ ↑ の行の前と後ろに、それぞれ 1 つ以上書いてください。"
        )


def _check_lanes(scenario: Scenario, source: ScenarioSlide, items: List[str]) -> None:
    """レーン図の書き方を確かめる。

    境界図と同じ考え方で、**図が決まらない書き方は資料にする前に止める。**
    レーンが 1 つしかない図は流れ図と同じもので、レーンが 3 つ以上ある図は
    描き方が決まっていない。どちらも黙って描くと、担当の分かれ目が無い
    「ただの流れ」が全体像として出てしまい、画像を見るまで気付けない。
    """
    parts = lane_parts(items)
    where = _where(scenario, source)
    if parts.bad:
        raise ScenarioError(
            f"{where}: レーン図に、誰の手順か書かれていない行があります。\n"
            f"  {parts.bad[0]}\n"
            "  1 行を「レーン名: 手順」の形で書いてください。\n"
            "  例: 人間が決める: 仕様を承認する"
        )
    if parts.forward:
        raise ScenarioError(
            f"{where}: レーン図に ↓ の行があります。\n"
            f"  {parts.forward[0]}\n"
            "  前へ進む矢印は、書いた順から自動で引かれます。"
            "書けるのは ↑(戻り)だけです。"
        )
    if len(parts.lanes) != 2:
        found = "、".join(parts.lanes) if parts.lanes else "なし"
        raise ScenarioError(
            f"{where}: レーン図のレーンが {len(parts.lanes)} つあります（{found}）。\n"
            "  レーンはちょうど 2 つにしてください"
            "（1 つなら流れ図と同じもので、3 つ以上は 1 枚に収まりません）。"
        )
    for ret in parts.returns:
        if ret.after < 0:
            raise ScenarioError(
                f"{where}: レーン図の ↑ の行が、手順より前にあります。\n"
                f"  ↑ {ret.label}\n"
                "  戻りは「どの手順から戻るか」なので、その手順の次の行に書いてください。"
            )


def _text_part(texts: List[object]) -> Content:
    """文章と箇条書き。段落は 1 行、箇条書きは項目ごとに 1 行になる。"""
    bullets: List[Bullet] = []
    for block in texts:
        if isinstance(block, Paragraph):
            # 段落は、記号を付けずにそのまま並べる(説明の文として書かれたもの)。
            bullets.append(
                Bullet(runs=block.runs, level=0, kind=QUOTE if block.quote else PLAIN)
            )
            continue
        for item in block.items:
            kind = QUOTE if item.quote else (NUMBER if item.ordered else BULLET)
            bullets.append(
                Bullet(runs=item.runs, level=min(item.level, 3), kind=kind, number=item.number)
            )

    return Content(kind=KIND_BULLETS, bullets=bullets)


def _block_label(block: object) -> str:
    for kind, label in (
        (Image, "図"),
        (Table, "表"),
        (CodeBlock, "コード"),
        (Heading, "見出し"),
    ):
        if isinstance(block, kind):
            return label
    return "この内容"


def _where(scenario: Scenario, source: ScenarioSlide) -> str:
    name = os.path.basename(scenario.source_path) if scenario.source_path else "教材シナリオ"
    return f"{name} の {source.line} 行目（{source.number} 枚目「{source.title}」）"
