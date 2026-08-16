"""実際に公開されている note 記事を取得して、通しで確認する。

差し替えた通信では「note が返す形が変わっていないか」を確かめられない。ここだけは
本物の記事を取りに行き、URL からスライド構成までが通ることを見る。

ネットワークが使えない環境では飛ばす(外部ツールを使うテストと同じ扱い)。
記事が消えた・非公開になった場合は失敗する。これは検知したい変化なので、
黙って飛ばさない。別の記事で確認する場合は環境変数で指定する。

    NOTE2SLIDES_TEST_URL=https://note.com/<ユーザー>/n/<記事キー>
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from note2slides import note_source as ns
from note2slides.model import Image, Paragraph
from note2slides.planner import plan_deck
from test_note_source import collected_text, visible_text

pytestmark = pytest.mark.slow

DEFAULT_URL = "https://note.com/sane_mink364/n/na01bf3bed64d"

#: 記事ページに出るが、記事本文ではない要素の決まり文句。本文だけを取っている
#: ことの目印にする(本文中にたまたま現れない言い回しを選ぶ)。
PAGE_FURNITURE = (
    "この記事が気に入ったら",
    "みんなにも読んでほしいですか",
    "フォローしてみませんか",
    "サポートをしてみませんか",
    "記事をマガジンに追加",
)


def article_url() -> str:
    return os.environ.get("NOTE2SLIDES_TEST_URL", DEFAULT_URL)


@pytest.fixture(scope="module")
def fetched():
    try:
        urllib.request.urlopen("https://note.com/", timeout=10).close()
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"note.com に接続できません: {exc}")
    return ns.fetch_note(article_url())


def test_the_title_and_body_are_returned(fetched):
    assert fetched.title.strip()
    assert len(fetched.body_html) > 500


def test_the_body_becomes_blocks_without_unknown_elements(fetched):
    blocks, warnings = ns.parse_note_body(fetched.body_html)

    assert warnings == [], "note の本文に、対応付けの無い要素が増えています"
    assert sum(isinstance(b, Paragraph) for b in blocks) > 10


def test_the_text_matches_the_article_body_exactly(fetched):
    """記事本文の文字と、取り込んだ文字が 1 文字も違わないこと。

    増えていない = ナビゲーション・関連記事・広告・プロフィールが混ざっていない。
    減っていない = 本文が欠けていない。
    """
    blocks, _ = ns.parse_note_body(fetched.body_html)

    assert collected_text(blocks) == visible_text(fetched.body_html)


def test_no_page_furniture_is_taken_in(fetched):
    """ページ側の決まり文句が本文として入っていないこと。"""
    blocks, _ = ns.parse_note_body(fetched.body_html)
    text = collected_text(blocks)

    for word in PAGE_FURNITURE:
        assert word not in text, f"記事本文ではない文字列が入っています: {word}"


def test_images_are_downloaded_at_their_original_size(fetched, tmp_path):
    blocks, _ = ns.parse_note_body(fetched.body_html)
    if not any(isinstance(b, Image) for b in blocks):
        pytest.skip("この記事には画像がありません")

    saved = ns.download_images(blocks, str(tmp_path), base_url=fetched.url)

    assert saved
    for image in saved:
        assert os.path.isfile(image.path)
        # 画面表示用に縮小されたものではなく、原寸を取れていること
        # (スライドは 1920 幅で描画するため、小さすぎると粗く見える)。
        assert image.width >= 600 and image.height >= 300


def test_the_whole_article_becomes_a_deck(fetched, tmp_path):
    result = ns.load_note_article(article_url(), str(tmp_path / "images"))
    deck = plan_deck(result.article)

    assert deck.title == fetched.title
    assert deck.warnings == []
    assert len(deck.slides) > 5
    # 画像はすべて資料に載っていること(取りこぼしがあると本文と対応しなくなる)。
    assert sum(1 for s in deck.slides if s.image_path) == len(result.images)
