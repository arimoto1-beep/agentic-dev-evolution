"""公開されている note 記事(URL)を、記事の中間表現(Article)へ変換する。

    https://note.com/<ユーザー>/n/<記事キー>
        --> note の公開 API --> 記事本文の HTML --> Article(ブロック列)
                                                +--> 記事中の画像(ローカルへ保存)

Web ページの HTML ではなく、note が公開している記事 API
(`https://note.com/api/v3/notes/<記事キー>`)から本文だけを取得する。
ページ HTML を読むと、ナビゲーション・関連記事・広告・プロフィール・
コメント欄なども一緒に入ってくるため、それらを後から取り除く判断が必要になる。
API の `body` は記事本文だけなので、**本文以外が混ざる余地がそもそも無い**。

得られる HTML は note の編集画面で作られたもので、使われる要素は限られている
(段落・見出し・箇条書き・引用・コード・図・区切り線・強調・リンク)。
ここではそれらをブロックへ対応付けるだけで、要約・言い換え・補足の生成は
行わない。出力されるテキストは記事本文の部分文字列である。

対応付けを持たない要素に出会った場合は、**中身のテキストを捨てずに**
そのまま取り込み、どの要素だったかを警告に残す(本文の欠落より、
知らない装飾が本文として残るほうが確認しやすいため)。

対象は公開記事だけで、ログインが必要な記事・有料部分・下書きは扱わない。
これらは API の応答から判別できるので、取得の時点で理由を示して中断する。
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

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

#: 記事 API。`key` は URL の `/n/` の後ろにある記事キー。
API_TEMPLATE = "https://note.com/api/v3/notes/{key}"
#: note の記事 URL のホスト(`note.mu` は旧ドメイン)。
NOTE_HOSTS = ("note.com", "note.mu")

DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "note2slides (+https://github.com/; e-learning video generator)"

#: 保存する画像の拡張子。ここに無い形式は Pillow が判別した形式で保存する。
_IMAGE_EXTENSIONS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tif",
}


class NoteError(RuntimeError):
    """note 記事の取得・解析に失敗した場合。

    メッセージには「何をしようとして」「どこで」「どう失敗したか」を入れる。
    URL の打ち間違い・非公開・有料部分・通信の失敗を区別できるようにする。
    """


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


def is_http_url(text: str) -> bool:
    """入力が http(s) の URL かどうか。ファイル入力と見分けるために使う。"""
    return bool(text) and urllib.parse.urlparse(text.strip()).scheme in ("http", "https")


def parse_note_key(url: str) -> str:
    """記事 URL から記事キー(`na01bf3bed64d` など)を取り出す。"""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise NoteError(f"note 記事の URL を http(s) で指定してください: {url}")

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in NOTE_HOSTS:
        raise NoteError(
            f"note.com の記事 URL を指定してください: {url}\n"
            f"  受け取ったホスト: {parsed.hostname or '(なし)'}\n"
            "  note 以外のサイトには対応していません。"
        )

    parts = [p for p in parsed.path.split("/") if p]
    # https://note.com/<ユーザー>/n/<キー> と https://note.com/n/<キー> の両方。
    for index, part in enumerate(parts[:-1]):
        if part == "n":
            key = parts[index + 1]
            if re.fullmatch(r"[A-Za-z0-9_-]+", key):
                return key
            break
    raise NoteError(
        f"記事 URL から記事キーを読み取れませんでした: {url}\n"
        "  https://note.com/<ユーザー>/n/<記事キー> の形で指定してください。"
    )


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------


@dataclass
class NoteSource:
    """API から受け取った記事の内容(まだ解析していないもの)。"""

    key: str
    url: str
    api_url: str
    title: str
    body_html: str
    author: str = ""
    published_at: str = ""
    eyecatch: str = ""

    @property
    def date(self) -> str:
        """`2026-08-15T10:58:14.000+09:00` を `2026年8月15日` にする。

        表紙に出るだけでなく読み上げられるので、日本語の日付にする。
        `2026-08-15` のままだと、合成エンジンが「ニイゼロニイロクゼロハチイチゴオ」と
        1 桁ずつ読んでしまう。
        """
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", self.published_at or "")
        if not match:
            return ""
        year, month, day = (int(part) for part in match.groups())
        return f"{year}年{month}月{day}日"


def fetch_note(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
) -> NoteSource:
    """公開記事の本文を note の API から取得する。"""
    key = parse_note_key(url)
    api_url = API_TEMPLATE.format(key=key)
    payload = _get_json(api_url, timeout=timeout, user_agent=user_agent, source=url)

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise NoteError(
            f"記事の内容を読み取れませんでした: {url}\n"
            f"  取得先: {api_url}\n"
            f"  応答に data がありません: {_snippet(json.dumps(payload, ensure_ascii=False))}"
        )

    _ensure_readable(data, url, api_url)

    body = data.get("body")
    if not isinstance(body, str) or not body.strip():
        raise NoteError(
            f"記事の本文が空でした: {url}\n"
            f"  取得先: {api_url}\n"
            "  本文のある公開記事か確認してください(画像や音声だけの記事は扱えません)。"
        )

    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    return NoteSource(
        key=key,
        url=url,
        api_url=api_url,
        title=str(data.get("name") or ""),
        body_html=body,
        author=str(user.get("nickname") or ""),
        published_at=str(data.get("publish_at") or data.get("created_at") or ""),
        eyecatch=str(data.get("eyecatch") or ""),
    )


def _ensure_readable(data: dict, url: str, api_url: str) -> None:
    """ログインが必要・有料・非公開の記事を、本文を読む前に見分ける。"""
    if data.get("is_draft") or data.get("status") not in (None, "published"):
        raise NoteError(
            f"公開されていない記事です: {url}\n"
            f"  状態: {data.get('status') or '(不明)'}\n"
            "  公開済みの記事を指定してください。"
        )
    if data.get("can_read") is False:
        raise NoteError(
            f"この記事は本文を取得できません(ログインが必要か、有料の記事です): {url}\n"
            f"  取得先: {api_url}\n"
            "  公開記事だけに対応しています。"
        )
    if data.get("is_limited") and not data.get("is_purchased"):
        raise NoteError(
            f"有料部分のある記事です: {url}\n"
            "  無料で読める範囲だけを取り込むと本文が欠けるため、扱いません。"
        )


def _get_json(api_url: str, timeout: float, user_agent: str, source: str) -> object:
    request = urllib.request.Request(
        api_url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = _snippet(_decode(exc.read() if exc.fp else b""))
        hint = ""
        if exc.code == 404:
            hint = "\n  記事キーが違うか、記事が削除・非公開になっている可能性があります。"
        raise NoteError(
            f"記事を取得できませんでした: {source}\n"
            f"  取得先: {api_url}\n"
            f"  HTTP {exc.code} {exc.reason}{hint}\n"
            f"  応答: {body or '(なし)'}"
        ) from exc
    except urllib.error.URLError as exc:
        raise NoteError(
            f"記事を取得できませんでした: {source}\n"
            f"  取得先: {api_url}\n"
            f"  通信に失敗しました: {exc.reason}\n"
            "  ネットワーク接続やプロキシの設定を確認してください。"
        ) from exc
    except OSError as exc:  # タイムアウトなど
        raise NoteError(
            f"記事を取得できませんでした: {source}\n"
            f"  取得先: {api_url}\n"
            f"  {type(exc).__name__}: {exc}"
        ) from exc

    try:
        return json.loads(_decode(raw))
    except json.JSONDecodeError as exc:
        raise NoteError(
            f"記事の応答を JSON として読めませんでした: {source}\n"
            f"  取得先: {api_url}\n"
            f"  {exc.lineno} 行 {exc.colno} 文字目: {exc.msg}\n"
            f"  応答: {_snippet(_decode(raw))}"
        ) from exc


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _snippet(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# HTML -> ブロック列
# ---------------------------------------------------------------------------

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
     "param", "source", "track", "wbr"}
)
#: 読み上げにも表示にも使わない要素。中身ごと落とす(`rt`/`rp` はふりがな)。
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template", "rt", "rp"})
#: ブロックとして扱う要素。ここに無い要素は、段落の中の飾り(インライン)とみなす。
_BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
     "pre", "figure", "figcaption", "hr", "table", "div", "section", "article",
     "main", "header", "footer", "aside", "dl", "dt", "dd", "address"}
)
#: 意味を持ち、対応付けが決まっているインライン要素。
_KNOWN_INLINE_TAGS = frozenset(
    {"br", "img", "strong", "b", "em", "i", "cite", "dfn", "code", "kbd", "samp",
     "var", "tt", "a", "span", "ruby", "u", "s", "del", "ins", "mark", "small",
     "sub", "sup", "abbr", "time", "font", "wbr"}
)

#: 閉じタグが省略されていても段落が入れ子にならないよう、開始タグが暗黙に
#: 閉じる要素。`<p>一つ目<p>二つ目` を 1 つの段落にしないために要る。
_IMPLIED_END: Dict[str, Tuple[str, ...]] = {tag: ("p",) for tag in _BLOCK_TAGS}
_IMPLIED_END.update(
    {
        "li": ("p", "li"),
        "tr": ("p", "td", "th", "tr"),
        "td": ("p", "td", "th"),
        "th": ("p", "td", "th"),
        "dt": ("p", "dt", "dd"),
        "dd": ("p", "dt", "dd"),
    }
)

_WHITESPACE = re.compile(r"[ \t\r\n\f\v]+")
_LANGUAGE_CLASS = re.compile(r"(?:language|lang)-([A-Za-z0-9_+#-]+)")


@dataclass
class _Element:
    tag: str
    attrs: Dict[str, str]
    children: List[object] = field(default_factory=list)


class _DomBuilder(HTMLParser):
    """HTML を、走査しやすい入れ子(_Element)にする。

    note の本文 HTML は編集画面が出力したもので構造が素直だが、閉じタグの
    無い要素があっても落ちないようにしておく。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("", {})
        self._stack: List[_Element] = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        closable = _IMPLIED_END.get(tag)
        while closable and len(self._stack) > 1 and self._stack[-1].tag in closable:
            self._stack.pop()
        element = _Element(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(element)
        if tag not in _VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._stack[-1].children.append(_Element(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # 対応する開始タグが無い閉じタグは無視する。

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_note_body(html: str) -> Tuple[List[object], List[str]]:
    """記事本文の HTML を、ブロック列と警告に変換する。"""
    builder = _DomBuilder()
    builder.feed(html or "")
    builder.close()
    walker = _BodyWalker()
    walker.walk(builder.root.children)
    return walker.blocks, walker.warnings


class _BodyWalker:
    """入れ子の要素を、Article のブロック列へ落とし込む。"""

    def __init__(self) -> None:
        self.blocks: List[object] = []
        self.warnings: List[str] = []
        self._inline: List[Run] = []
        self._items: List[ListItem] = []
        self._lists: List[dict] = []
        self._quote = 0
        self._unknown: set = set()

    # -- 走査 -----------------------------------------------------------
    def walk(self, nodes: List[object]) -> None:
        self._walk_blocks(nodes)
        self._flush_inline()
        self._flush_items()

    def _walk_blocks(self, nodes: List[object]) -> None:
        for node in nodes:
            if isinstance(node, str):
                self._collect_inline([node], out=self._inline)
            elif node.tag in _IGNORED_TAGS:
                continue
            elif node.tag in _BLOCK_TAGS:
                self._flush_inline()
                self._handle_block(node)
            else:
                self._collect_inline([node], out=self._inline)

    def _handle_block(self, node: _Element) -> None:
        tag = node.tag
        if tag == "p" or tag == "figcaption" or tag == "dd" or tag == "address":
            self._handle_paragraph(node)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            runs = _strip_runs(self._collect_inline(node.children))
            if _has_text(runs):
                self._emit(Heading(level=int(tag[1]), runs=runs))
        elif tag == "hr":
            self._emit(SlideBreak())
        elif tag in ("ul", "ol"):
            self._handle_list(node)
        elif tag == "li":
            # ul/ol の外にある li。箇条書きの体裁を保てないので段落として扱う。
            self._handle_paragraph(node)
        elif tag == "blockquote":
            self._quote += 1
            self._walk_blocks(node.children)
            self._flush_inline()
            self._quote -= 1
        elif tag == "pre":
            self._handle_pre(node)
        elif tag == "figure":
            self._handle_figure(node)
        elif tag == "table":
            self._handle_table(node)
        elif tag == "dt":
            self._handle_paragraph(node)
        else:  # div / section / article など、入れ物だけの要素
            self._walk_blocks(node.children)
            self._flush_inline()

    def _handle_paragraph(self, node: _Element) -> None:
        image = self._standalone_image(node)
        if image is not None:
            self._emit(image)
            return
        runs = _strip_runs(self._collect_inline(node.children))
        if _has_text(runs):
            self._emit(Paragraph(runs=runs, quote=self._quote > 0))

    def _standalone_image(self, node: _Element) -> Optional[Image]:
        """段落が画像 1 つだけの場合、画像ブロックとして扱う。"""
        images = [c for c in node.children if isinstance(c, _Element) and c.tag == "img"]
        others = [
            c
            for c in node.children
            if not (isinstance(c, str) and not c.strip())
            and not (isinstance(c, _Element) and c.tag == "img")
        ]
        if len(images) == 1 and not others:
            return Image(src=images[0].attrs.get("src", ""), alt=images[0].attrs.get("alt", "").strip())
        return None

    # -- 箇条書き -------------------------------------------------------
    def _handle_list(self, node: _Element) -> None:
        start = node.attrs.get("start")
        first = int(start) if (start or "").isdigit() else 1
        self._lists.append({"ordered": node.tag == "ol", "index": first - 1})
        for child in node.children:
            if isinstance(child, str):
                continue  # タグの間の空白
            if child.tag == "li":
                self._handle_list_item(child)
            elif child.tag in ("ul", "ol"):
                self._handle_list(child)
            elif child.tag not in _IGNORED_TAGS:
                self._handle_block(child)
        self._lists.pop()
        if not self._lists:
            self._flush_items()

    def _handle_list_item(self, node: _Element) -> None:
        frame = self._lists[-1]
        frame["index"] += 1
        level = min(len(self._lists) - 1, 3)
        state = {"first": True}
        pending: List[Run] = []

        def emit(runs: List[Run]) -> None:
            runs = _strip_runs(runs)
            if not _has_text(runs):
                return
            numbered = bool(frame["ordered"]) and state["first"]
            self._items.append(
                ListItem(
                    level=level,
                    runs=runs,
                    ordered=numbered,
                    number=f"{frame['index']}." if numbered else None,
                    quote=self._quote > 0,
                )
            )
            state["first"] = False

        for child in node.children:
            if isinstance(child, str):
                self._collect_inline([child], out=pending)
                continue
            if child.tag in _IGNORED_TAGS:
                continue
            if child.tag in ("ul", "ol"):
                emit(pending)
                pending = []
                self._handle_list(child)
            elif child.tag == "p":
                emit(pending)
                pending = []
                emit(self._collect_inline(child.children))
            elif child.tag in _BLOCK_TAGS:
                # 箇条書きの中の図・コード・表。行として書けないので、
                # そこまでの項目を確定させてからブロックとして出す。
                emit(pending)
                pending = []
                self._flush_items()
                self._handle_block(child)
            else:
                self._collect_inline([child], out=pending)
        emit(pending)

    # -- コード・図・表 --------------------------------------------------
    def _handle_pre(self, node: _Element) -> None:
        code = _find(node, "code")
        target = code or node
        language = _language_of(code) or _language_of(node)
        text = _raw_text(target).strip("\n")
        if text.strip():
            self._emit(CodeBlock(text=text, lang=language))

    def _handle_figure(self, node: _Element) -> None:
        images = _find_all(node, "img")
        if not images:
            # 外部サービスの埋め込みなど。画像ではないが本文の一部なので、
            # 中のテキストは落とさずに残す。
            self._warn("figure", "画像ではない埋め込みは、中の文章だけを取り込みました。")
            self._walk_blocks(node.children)
            self._flush_inline()
            return
        caption_element = _find(node, "figcaption")
        caption = _text_of(self._collect_inline(caption_element.children)) if caption_element else ""
        for image in images:
            # キャプションは図全体に付くので、複数枚あっても最初の 1 枚に添える。
            alt = (caption.strip() if image is images[0] else "") or image.attrs.get("alt", "").strip()
            self._emit(Image(src=image.attrs.get("src", ""), alt=alt))

    def _handle_table(self, node: _Element) -> None:
        header: List[str] = []
        rows: List[List[str]] = []
        for row in _find_all(node, "tr"):
            cells = [
                _text_of(self._collect_inline(cell.children)).strip()
                for cell in row.children
                if isinstance(cell, _Element) and cell.tag in ("td", "th")
            ]
            if not cells:
                continue
            is_header = all(
                c.tag == "th" for c in row.children if isinstance(c, _Element) and c.tag in ("td", "th")
            )
            if is_header and not header and not rows:
                header = cells
            else:
                rows.append(cells)
        if header or rows:
            self._emit(Table(header=header, rows=rows))

    # -- インライン ------------------------------------------------------
    def _collect_inline(
        self,
        nodes: List[object],
        bold: bool = False,
        italic: bool = False,
        code: bool = False,
        link: Optional[str] = None,
        out: Optional[List[Run]] = None,
    ) -> List[Run]:
        runs = [] if out is None else out
        for node in nodes:
            if isinstance(node, str):
                _push(runs, Run(_WHITESPACE.sub(" ", node), bold, italic, code, link))
                continue
            tag = node.tag
            if tag in _IGNORED_TAGS:
                continue
            if tag == "br":
                _push(runs, Run("\n", bold, italic, code, link))
            elif tag == "img":
                # 文章の途中に置かれた画像。図として 1 枚のスライドにはできないので、
                # 代替テキストだけを残し、資料に載らなかったことを知らせる。
                self._warn("img", "文章の途中にある画像は、代替テキストだけを取り込みました。")
                alt = node.attrs.get("alt", "").strip()
                if alt:
                    _push(runs, Run(alt, bold, italic, code, link))
            elif tag in ("strong", "b"):
                self._collect_inline(node.children, True, italic, code, link, runs)
            elif tag in ("em", "i", "cite", "dfn"):
                self._collect_inline(node.children, bold, True, code, link, runs)
            elif tag in ("code", "kbd", "samp", "var", "tt"):
                self._collect_inline(node.children, bold, italic, True, link, runs)
            elif tag == "a":
                self._collect_inline(
                    node.children, bold, italic, code, node.attrs.get("href") or link, runs
                )
            else:
                if tag not in _KNOWN_INLINE_TAGS:
                    self._warn(tag, "対応付けの無い要素は、中の文章をそのまま取り込みました。")
                self._collect_inline(node.children, bold, italic, code, link, runs)
        return runs

    # -- 確定 -----------------------------------------------------------
    def _emit(self, block: object) -> None:
        self._flush_items()
        self.blocks.append(block)

    def _flush_inline(self) -> None:
        """ブロック要素の外に直接書かれた文章を、段落として確定する。"""
        runs = _strip_runs(self._inline)
        self._inline = []
        if _has_text(runs):
            self._emit(Paragraph(runs=runs, quote=self._quote > 0))

    def _flush_items(self) -> None:
        if self._items:
            items, self._items = self._items, []
            self.blocks.append(ListBlock(items=items))

    def _warn(self, tag: str, message: str) -> None:
        if tag in self._unknown:
            return
        self._unknown.add(tag)
        self.warnings.append(f"<{tag}> {message}")


# -- 小さなヘルパ ------------------------------------------------------------


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
    out = list(runs)
    while out and not out[0].text.strip():
        out.pop(0)
    while out and not out[-1].text.strip():
        out.pop()
    if out:
        out[0] = out[0].with_text(out[0].text.lstrip())
        out[-1] = out[-1].with_text(out[-1].text.rstrip())
    return [r for r in out if r.text]


def _text_of(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def _has_text(runs: List[Run]) -> bool:
    return bool(_text_of(runs).strip())


def _find(node: _Element, tag: str) -> Optional[_Element]:
    for child in node.children:
        if isinstance(child, _Element):
            if child.tag == tag:
                return child
            found = _find(child, tag)
            if found is not None:
                return found
    return None


def _find_all(node: _Element, tag: str) -> List[_Element]:
    found: List[_Element] = []
    for child in node.children:
        if isinstance(child, _Element):
            if child.tag == tag:
                found.append(child)
            else:
                found.extend(_find_all(child, tag))
    return found


def _raw_text(node: _Element) -> str:
    """コードブロック用。空白と改行をそのまま残す。"""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag in _IGNORED_TAGS:
            continue
        elif child.tag == "br":
            parts.append("\n")
        else:
            parts.append(_raw_text(child))
    return "".join(parts)


def _language_of(node: Optional[_Element]) -> str:
    if node is None:
        return ""
    for key in ("class", "data-language", "data-lang"):
        value = node.attrs.get(key, "")
        match = _LANGUAGE_CLASS.search(value)
        if match:
            return match.group(1).lower()
        if key != "class" and value.strip():
            return value.strip().lower()
    return ""


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------


@dataclass
class NoteImage:
    """記事から取り込んだ画像 1 枚。"""

    index: int
    url: str
    path: str
    width: int
    height: int
    bytes: int
    alt: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "url": self.url,
            "file": os.path.basename(self.path),
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
            "alt": self.alt,
        }


def download_images(
    blocks: List[object],
    outdir: str,
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
) -> List[NoteImage]:
    """記事中の画像を保存し、ブロックの参照先をそのファイルに書き換える。

    掲載順(ブロックの並び)をそのままファイル名の連番にするので、記事の
    どこにあった画像かを後から確認できる。
    """
    images = [b for b in blocks if isinstance(b, Image)]
    if not images:
        return []

    os.makedirs(outdir, exist_ok=True)
    saved: List[NoteImage] = []
    for index, block in enumerate(images, start=1):
        source = urllib.parse.urljoin(base_url, block.src) if base_url else block.src
        if not source:
            raise NoteError(f"{index} 枚目の画像に参照先がありません(記事: {base_url})")
        saved.append(_download_image(source, outdir, index, block.alt, timeout, user_agent))
        block.src = saved[-1].path
    return saved


def _download_image(
    url: str, outdir: str, index: int, alt: str, timeout: float, user_agent: str
) -> NoteImage:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise NoteError(
            f"{index} 枚目の画像を取得できませんでした: {url}\n"
            f"  HTTP {exc.code} {exc.reason}"
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NoteError(
            f"{index} 枚目の画像を取得できませんでした: {url}\n  {reason}"
        ) from exc

    from PIL import Image as PILImage, UnidentifiedImageError

    try:
        with PILImage.open(io.BytesIO(raw)) as opened:
            opened.load()
            width, height = opened.size
            fmt = (opened.format or "").upper()
    except (UnidentifiedImageError, OSError) as exc:
        raise NoteError(
            f"{index} 枚目の画像を読み取れませんでした: {url}\n"
            f"  {len(raw)} バイト受け取りましたが、画像として開けません: {exc}"
        ) from exc

    extension = _IMAGE_EXTENSIONS.get(fmt) or os.path.splitext(urllib.parse.urlparse(url).path)[1]
    path = os.path.abspath(os.path.join(outdir, f"image_{index:03d}{extension or '.img'}"))
    with open(path, "wb") as f:
        f.write(raw)
    return NoteImage(
        index=index, url=url, path=path, width=width, height=height, bytes=len(raw), alt=alt
    )


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------


@dataclass
class NoteResult:
    """URL 1 本から取り込んだ結果。"""

    article: Article
    source: NoteSource
    images: List[NoteImage] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """`--dump-article` 用。何を本文として取り込んだかを確認する。"""
        return {
            "url": self.source.url,
            "api": self.source.api_url,
            "key": self.source.key,
            "title": self.source.title,
            "author": self.source.author,
            "published_at": self.source.published_at,
            "blocks": [_block_to_dict(b) for b in self.article.blocks],
            "images": [i.to_dict() for i in self.images],
            "warnings": self.warnings,
        }


def load_note_article(
    url: str,
    image_dir: str,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
) -> NoteResult:
    """公開 note 記事の URL から、そのまま資料生成へ渡せる Article を作る。"""
    source = fetch_note(url, timeout=timeout, user_agent=user_agent)
    blocks, warnings = parse_note_body(source.body_html)
    if not blocks:
        raise NoteError(
            f"記事本文から取り込める内容がありませんでした: {url}\n"
            f"  取得先: {source.api_url}\n"
            f"  本文の先頭: {_snippet(source.body_html)}"
        )
    images = download_images(
        blocks, image_dir, base_url=source.url, timeout=timeout, user_agent=user_agent
    )

    meta = {"title": source.title, "source": source.url}
    if source.author:
        meta["author"] = source.author
    if source.date:
        meta["date"] = source.date
    article = Article(blocks=blocks, meta=meta, source_path=source.url)
    return NoteResult(article=article, source=source, images=images, warnings=warnings)


def _block_to_dict(block: object) -> dict:
    if isinstance(block, Heading):
        return {"type": "heading", "level": block.level, "text": _text_of(block.runs)}
    if isinstance(block, Paragraph):
        return {"type": "quote" if block.quote else "paragraph", "text": _text_of(block.runs)}
    if isinstance(block, ListBlock):
        return {
            "type": "list",
            "items": [
                {"level": i.level, "number": i.number, "text": _text_of(i.runs)}
                for i in block.items
            ],
        }
    if isinstance(block, CodeBlock):
        return {"type": "code", "lang": block.lang, "text": block.text}
    if isinstance(block, Table):
        return {"type": "table", "header": block.header, "rows": block.rows}
    if isinstance(block, Image):
        return {"type": "image", "src": block.src, "alt": block.alt}
    if isinstance(block, SlideBreak):
        return {"type": "break"}
    return {"type": type(block).__name__}
