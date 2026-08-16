"""公開 note 記事(URL)の取り込み。

外部への通信は行わない(通信そのものは `test_note_source_live.py`)。
ここでは URL の読み取り・本文 HTML の解析・失敗時の説明を確かめる。
"""

from __future__ import annotations

import io
import json
import os
import urllib.error

import pytest

from note2slides import note_source as ns
from note2slides.model import (
    CodeBlock,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    SlideBreak,
    Table,
)

ARTICLE_URL = "https://note.com/sane_mink364/n/na01bf3bed64d"


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


class TestUrl:
    @pytest.mark.parametrize(
        "text",
        ["https://note.com/x/n/nabc", "http://note.com/x/n/nabc"],
    )
    def test_http_url_is_detected(self, text):
        assert ns.is_http_url(text) is True

    @pytest.mark.parametrize("text", ["article.md", "build/a.md", "C:/tmp/a.md", ""])
    def test_file_path_is_not_a_url(self, text):
        assert ns.is_http_url(text) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://note.com/sane_mink364/n/na01bf3bed64d",
            "https://note.com/sane_mink364/n/na01bf3bed64d?magazine_key=x",
            "https://www.note.com/sane_mink364/n/na01bf3bed64d",
            "https://note.com/n/na01bf3bed64d",
            "https://note.mu/sane_mink364/n/na01bf3bed64d",
        ],
    )
    def test_key_is_read_from_the_article_url(self, url):
        assert ns.parse_note_key(url) == "na01bf3bed64d"

    def test_other_sites_are_refused_with_the_host_in_the_message(self):
        with pytest.raises(ns.NoteError) as exc:
            ns.parse_note_key("https://example.com/a/n/nabc")
        assert "example.com" in str(exc.value)

    def test_a_url_without_an_article_key_is_refused(self):
        with pytest.raises(ns.NoteError) as exc:
            ns.parse_note_key("https://note.com/sane_mink364")
        assert "記事キー" in str(exc.value)


# ---------------------------------------------------------------------------
# 本文 HTML -> ブロック
# ---------------------------------------------------------------------------


def blocks_of(html: str):
    blocks, warnings = ns.parse_note_body(html)
    return blocks


def texts_of(blocks) -> list:
    out = []
    for block in blocks:
        data = ns._block_to_dict(block)
        if data["type"] == "list":
            out.extend(item["text"] for item in data["items"])
        elif "text" in data:
            out.append(data["text"])
    return out


class TestBody:
    def test_paragraphs_keep_the_article_text(self):
        blocks = blocks_of("<p>一つ目です。</p><p>二つ目です。</p>")
        assert [type(b) for b in blocks] == [Paragraph, Paragraph]
        assert texts_of(blocks) == ["一つ目です。", "二つ目です。"]

    def test_headings_become_headings(self):
        blocks = blocks_of("<h2>大見出し</h2><h3>小見出し</h3>")
        assert all(isinstance(b, Heading) for b in blocks)
        assert [(b.level, b.runs[0].text) for b in blocks] == [(2, "大見出し"), (3, "小見出し")]

    def test_horizontal_rule_becomes_a_slide_break(self):
        blocks = blocks_of("<p>あ</p><hr><p>い</p>")
        assert [type(b) for b in blocks] == [Paragraph, SlideBreak, Paragraph]

    def test_emphasis_and_links_are_kept_as_formatting(self):
        blocks = blocks_of('<p>これは<strong>太字</strong>と<a href="https://x.test/">リンク</a>です。</p>')
        runs = blocks[0].runs
        assert "".join(r.text for r in runs) == "これは太字とリンクです。"
        assert [r.text for r in runs if r.bold] == ["太字"]
        assert [(r.text, r.link) for r in runs if r.link] == [("リンク", "https://x.test/")]

    def test_line_break_stays_inside_the_paragraph(self):
        # note の <br> は段落を分けずに行だけを変える。資料でも同じ扱いにする。
        blocks = blocks_of("<p>文章<br>↓<br>トークン</p>")
        assert texts_of(blocks) == ["文章\n↓\nトークン"]

    def test_bullet_list_items_keep_their_order(self):
        blocks = blocks_of("<ul><li><p>一</p></li><li><p>二</p></li></ul>")
        assert isinstance(blocks[0], ListBlock)
        assert [(i.level, i.number, i.runs[0].text) for i in blocks[0].items] == [
            (0, None, "一"),
            (0, None, "二"),
        ]

    def test_numbered_list_keeps_the_original_numbers(self):
        blocks = blocks_of('<ol start="3"><li><p>三番目</p></li><li><p>四番目</p></li></ol>')
        assert [i.number for i in blocks[0].items] == ["3.", "4."]

    def test_nested_list_is_indented(self):
        blocks = blocks_of("<ul><li><p>親</p><ul><li><p>子</p></li></ul></li></ul>")
        assert [(i.level, i.runs[0].text) for i in blocks[0].items] == [(0, "親"), (1, "子")]

    def test_blockquote_is_marked_as_a_quote(self):
        blocks = blocks_of("<blockquote><p>引用文です。</p></blockquote>")
        assert isinstance(blocks[0], Paragraph) and blocks[0].quote is True

    def test_code_block_keeps_its_indentation_and_language(self):
        blocks = blocks_of(
            '<pre><code class="language-python">def f():\n    return 1</code></pre>'
        )
        assert isinstance(blocks[0], CodeBlock)
        assert blocks[0].text == "def f():\n    return 1"
        assert blocks[0].lang == "python"

    def test_table_is_read_with_its_header(self):
        blocks = blocks_of(
            "<table><thead><tr><th>列</th><th>値</th></tr></thead>"
            "<tbody><tr><td>あ</td><td>1</td></tr></tbody></table>"
        )
        assert isinstance(blocks[0], Table)
        assert blocks[0].header == ["列", "値"]
        assert blocks[0].rows == [["あ", "1"]]

    def test_figure_becomes_an_image_with_its_caption(self):
        blocks = blocks_of(
            '<figure><img src="https://a.test/x.png" alt="代替"><figcaption>説明文</figcaption></figure>'
        )
        assert isinstance(blocks[0], Image)
        assert blocks[0].src == "https://a.test/x.png"
        assert blocks[0].alt == "説明文"  # キャプションがあればそちらを使う

    def test_figure_without_a_caption_falls_back_to_the_alt_text(self):
        blocks = blocks_of('<figure><img src="https://a.test/x.png" alt="代替"><figcaption></figcaption></figure>')
        assert blocks[0].alt == "代替"

    def test_a_figure_with_several_images_keeps_them_all(self):
        blocks = blocks_of(
            "<figure><img src='https://a.test/1.png'><img src='https://a.test/2.png'>"
            "<figcaption>ふたつの図</figcaption></figure>"
        )
        assert [b.src for b in blocks] == ["https://a.test/1.png", "https://a.test/2.png"]
        assert [b.alt for b in blocks] == ["ふたつの図", ""]

    def test_an_image_inside_a_sentence_is_reported(self):
        # 文章の途中の画像は 1 枚のスライドにできない。黙って消さずに知らせる。
        blocks, warnings = ns.parse_note_body("<p>前<img src='https://a.test/1.png' alt='図'>後</p>")
        assert texts_of(blocks) == ["前図後"]
        assert any("代替テキスト" in w for w in warnings)

    def test_image_keeps_its_place_between_the_paragraphs(self):
        blocks = blocks_of(
            "<p>前の文です。</p><figure><img src='https://a.test/x.png'></figure><p>後の文です。</p>"
        )
        assert [type(b) for b in blocks] == [Paragraph, Image, Paragraph]

    def test_ruby_keeps_the_base_text_and_drops_the_reading(self):
        blocks = blocks_of("<p><ruby>漢字<rt>かんじ</rt></ruby>です。</p>")
        assert texts_of(blocks) == ["漢字です。"]

    def test_entities_are_decoded(self):
        blocks = blocks_of("<p>a &amp; b &lt;c&gt;</p>")
        assert texts_of(blocks) == ["a & b <c>"]

    def test_whitespace_between_tags_does_not_become_text(self):
        blocks = blocks_of("<p>あ</p>\n  \n<p>い</p>")
        assert texts_of(blocks) == ["あ", "い"]

    def test_scripts_and_styles_are_dropped(self):
        blocks = blocks_of("<p>本文</p><script>alert(1)</script><style>p{}</style>")
        assert texts_of(blocks) == ["本文"]

    def test_unknown_elements_keep_their_text_and_are_reported(self):
        # 本文が黙って消えるより、知らない装飾が残るほうが確認しやすい。
        blocks, warnings = ns.parse_note_body("<p>これは<marquee>動く字</marquee>です。</p>")
        assert texts_of(blocks) == ["これは動く字です。"]
        assert any("marquee" in w for w in warnings)

    def test_text_outside_a_block_is_not_lost(self):
        blocks = blocks_of("むき出しの文です。<p>段落です。</p>")
        assert texts_of(blocks) == ["むき出しの文です。", "段落です。"]

    def test_missing_close_tags_do_not_lose_text(self):
        blocks = blocks_of("<p>一つ目<p>二つ目")
        assert texts_of(blocks) == ["一つ目", "二つ目"]


def visible_text(html: str) -> str:
    """HTML から、画面に出る文字だけを取り出す(空白は無視して比べるため詰める)。"""
    import html as html_module
    import re

    return "".join(html_module.unescape(re.sub(r"<[^>]+>", " ", html)).split())


def collected_text(blocks) -> str:
    """取り込んだブロックの文字を、同じ条件で 1 本につなげる。"""
    parts = []
    for block in blocks:
        data = ns._block_to_dict(block)
        if data["type"] == "list":
            parts.extend(item["text"] for item in data["items"])
        elif data["type"] == "image":
            parts.append(data["alt"])  # キャプションは画像の下に出る
        elif data["type"] == "table":
            parts.extend(data["header"] + [c for row in data["rows"] for c in row])
        elif "text" in data:
            parts.append(data["text"])
    return "".join("".join(parts).split())


def test_no_text_is_added_or_lost():
    """本文 HTML の可視テキストと、取り込んだテキストが 1 文字も違わないこと。

    「本文が欠落していないか」「本文でないものが混ざっていないか」を、目視では
    なく文字単位で確かめる。
    """
    source = (
        "<p>生成AIを使っていると、<strong>トークン</strong>という言葉を見かけます。</p>"
        "<h2>AIは文章をそのまま読んでいない</h2>"
        "<p>まず文章を一定の単位に分割します。</p>"
        "<ul><li><p>1文字だけのもの</p></li><li><p>単語の一部分</p></li></ul>"
        "<blockquote><p>引用です。</p></blockquote>"
        "<pre><code>tokenize(text)</code></pre>"
        "<figure><img src='https://a.test/x.png'><figcaption>図の説明</figcaption></figure>"
        "<hr>"
        '<p>詳しくは<a href="https://a.test/">こちら</a>を参照してください。</p>'
    )
    blocks, warnings = ns.parse_note_body(source)

    assert collected_text(blocks) == visible_text(source)
    assert warnings == []


# ---------------------------------------------------------------------------
# 取得(通信は差し替える)
# ---------------------------------------------------------------------------

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def fake_network(monkeypatch, note=None, status=None, body=None, images=True):
    """`urllib.request.urlopen` を差し替える。`requested` に URL を記録する。"""
    requested = []

    def urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        requested.append(url)
        if "/api/v3/notes/" in url:
            if status is not None:
                raise urllib.error.HTTPError(url, status, "Not Found", {}, io.BytesIO(b"{}"))
            payload = body if body is not None else json.dumps({"data": note}).encode()
            return _Response(payload)
        if not images:
            raise urllib.error.URLError("画像が置かれていません")
        return _Response(PNG_1X1)

    monkeypatch.setattr(ns.urllib.request, "urlopen", urlopen)
    return requested


def note_payload(**overrides) -> dict:
    data = {
        "name": "トークンとは？",
        "body": "<p>本文です。</p>",
        "status": "published",
        "can_read": True,
        "user": {"nickname": "アリさん"},
        "created_at": "2026-08-15T10:58:14.000+09:00",
    }
    data.update(overrides)
    return data


class TestFetch:
    def test_the_public_api_is_used_and_the_body_is_returned(self, monkeypatch):
        requested = fake_network(monkeypatch, note=note_payload())

        source = ns.fetch_note(ARTICLE_URL)

        assert requested == ["https://note.com/api/v3/notes/na01bf3bed64d"]
        assert source.title == "トークンとは？"
        assert source.body_html == "<p>本文です。</p>"
        assert source.author == "アリさん"
        assert source.date == "2026年8月15日"

    def test_a_missing_article_says_so_with_the_status_code(self, monkeypatch):
        fake_network(monkeypatch, status=404)

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        message = str(exc.value)
        assert "HTTP 404" in message
        assert "https://note.com/api/v3/notes/na01bf3bed64d" in message
        assert ARTICLE_URL in message

    def test_a_broken_response_shows_what_came_back(self, monkeypatch):
        fake_network(monkeypatch, body=b"<html>maintenance</html>")

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        assert "JSON" in str(exc.value)
        assert "maintenance" in str(exc.value)

    def test_a_paid_article_is_refused_before_reading_the_body(self, monkeypatch):
        fake_network(monkeypatch, note=note_payload(is_limited=True))

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        assert "有料" in str(exc.value)

    def test_an_article_that_needs_a_login_is_refused(self, monkeypatch):
        fake_network(monkeypatch, note=note_payload(can_read=False))

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        assert "ログイン" in str(exc.value)

    def test_a_draft_is_refused(self, monkeypatch):
        fake_network(monkeypatch, note=note_payload(is_draft=True, status="draft"))

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        assert "公開されていない" in str(exc.value)

    def test_an_empty_body_is_refused(self, monkeypatch):
        fake_network(monkeypatch, note=note_payload(body="   "))

        with pytest.raises(ns.NoteError) as exc:
            ns.fetch_note(ARTICLE_URL)

        assert "本文が空" in str(exc.value)


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------


class TestImages:
    def test_images_are_saved_in_the_order_they_appear(self, monkeypatch, tmp_path):
        fake_network(monkeypatch)
        blocks = blocks_of(
            "<figure><img src='https://a.test/1.png'></figure>"
            "<p>あいだの文です。</p>"
            "<figure><img src='https://a.test/2.png'></figure>"
        )

        saved = ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert [os.path.basename(i.path) for i in saved] == ["image_001.png", "image_002.png"]
        assert [i.url for i in saved] == ["https://a.test/1.png", "https://a.test/2.png"]
        assert all(os.path.isfile(i.path) for i in saved)

    def test_the_block_points_at_the_saved_file(self, monkeypatch, tmp_path):
        # ここが書き換わることで、以降はローカル画像と同じ経路で資料に載る。
        fake_network(monkeypatch)
        blocks = blocks_of("<figure><img src='https://a.test/1.png'></figure>")

        ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert os.path.isabs(blocks[0].src) and os.path.isfile(blocks[0].src)

    def test_the_size_is_recorded_so_it_can_be_checked(self, monkeypatch, tmp_path):
        fake_network(monkeypatch)
        blocks = blocks_of("<figure><img src='https://a.test/1.png'></figure>")

        saved = ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert (saved[0].width, saved[0].height) == (1, 1)
        assert saved[0].bytes == len(PNG_1X1)

    def test_a_relative_source_is_resolved_against_the_article(self, monkeypatch, tmp_path):
        requested = fake_network(monkeypatch)
        blocks = blocks_of("<figure><img src='/img/x.png'></figure>")

        ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert "https://note.com/img/x.png" in requested

    def test_an_image_that_cannot_be_fetched_says_which_one(self, monkeypatch, tmp_path):
        fake_network(monkeypatch, images=False)
        blocks = blocks_of("<figure><img src='https://a.test/1.png'></figure>")

        with pytest.raises(ns.NoteError) as exc:
            ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert "1 枚目" in str(exc.value)
        assert "https://a.test/1.png" in str(exc.value)

    def test_a_file_that_is_not_an_image_is_refused(self, monkeypatch, tmp_path):
        def urlopen(request, timeout=None):
            return _Response(b"<html>not found</html>")

        monkeypatch.setattr(ns.urllib.request, "urlopen", urlopen)
        blocks = blocks_of("<figure><img src='https://a.test/1.png'></figure>")

        with pytest.raises(ns.NoteError) as exc:
            ns.download_images(blocks, str(tmp_path), base_url=ARTICLE_URL)

        assert "画像として開けません" in str(exc.value)


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------


class TestLoadArticle:
    def test_the_article_carries_the_title_author_and_date(self, monkeypatch, tmp_path):
        fake_network(monkeypatch, note=note_payload())

        result = ns.load_note_article(ARTICLE_URL, str(tmp_path / "images"))

        assert result.article.meta["title"] == "トークンとは？"
        assert result.article.meta["author"] == "アリさん"
        assert result.article.meta["date"] == "2026年8月15日"
        assert result.article.meta["source"] == ARTICLE_URL

    def test_the_result_can_be_written_out_for_checking(self, monkeypatch, tmp_path):
        fake_network(
            monkeypatch,
            note=note_payload(
                body="<h2>見出し</h2><p>本文です。</p><figure><img src='https://a.test/1.png'></figure>"
            ),
        )

        result = ns.load_note_article(ARTICLE_URL, str(tmp_path / "images"))
        data = result.to_dict()

        assert [b["type"] for b in data["blocks"]] == ["heading", "paragraph", "image"]
        assert data["images"][0]["file"] == "image_001.png"
        assert data["url"] == ARTICLE_URL
