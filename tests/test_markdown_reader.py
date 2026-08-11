from note2slides.markdown_reader import parse_article, split_front_matter
from note2slides.model import (
    CodeBlock,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    SlideBreak,
    Table,
    runs_to_text,
)


def test_front_matter_is_separated():
    meta, body = split_front_matter('---\ntitle: "記事タイトル"\nauthor: 著者\n---\n本文\n')
    assert meta == {"title": "記事タイトル", "author": "著者"}
    assert body == "本文\n"


def test_no_front_matter_keeps_body():
    meta, body = split_front_matter("# 見出し\n")
    assert meta == {}
    assert body == "# 見出し\n"


def test_headings_and_paragraphs():
    article = parse_article("# タイトル\n\n## 節\n\n本文です。\n")
    kinds = [type(b) for b in article.blocks]
    assert kinds == [Heading, Heading, Paragraph]
    assert article.blocks[0].level == 1
    assert article.blocks[1].level == 2
    assert runs_to_text(article.blocks[2].runs) == "本文です。"


def test_nested_list_levels():
    article = parse_article("- 親\n  - 子\n    - 孫\n")
    items = article.blocks[0].items
    assert [i.level for i in items] == [0, 1, 2]
    assert [runs_to_text(i.runs) for i in items] == ["親", "子", "孫"]


def test_ordered_list_numbers():
    article = parse_article("3. さん\n4. よん\n")
    items = article.blocks[0].items
    assert all(i.ordered for i in items)
    assert [i.number for i in items] == ["3.", "4."]


def test_inline_styles_and_links():
    article = parse_article("**太字**と*斜体*と`コード`と[リンク](https://example.com)\n")
    runs = article.blocks[0].runs
    assert runs_to_text(runs) == "太字と斜体とコードとリンク"
    assert runs[0].bold and not runs[1].bold
    assert runs[2].italic
    assert runs[4].code
    assert runs[-1].link == "https://example.com"


def test_code_fence_keeps_content():
    article = parse_article("```python\nprint(1)\nprint(2)\n```\n")
    block = article.blocks[0]
    assert isinstance(block, CodeBlock)
    assert block.lang == "python"
    assert block.text == "print(1)\nprint(2)"


def test_table_parsing():
    article = parse_article("| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n")
    table = article.blocks[0]
    assert isinstance(table, Table)
    assert table.header == ["A", "B"]
    assert table.rows == [["1", "2"], ["3", "4"]]


def test_blockquote_is_marked():
    article = parse_article("> 引用文です。\n")
    assert isinstance(article.blocks[0], Paragraph)
    assert article.blocks[0].quote is True


def test_horizontal_rule_is_slide_break():
    article = parse_article("A\n\n---\n\nB\n")
    assert [type(b) for b in article.blocks] == [Paragraph, SlideBreak, Paragraph]


def test_standalone_image_becomes_image_block():
    article = parse_article("![説明](img/a.png)\n")
    block = article.blocks[0]
    assert isinstance(block, Image)
    assert block.src == "img/a.png"
    assert block.alt == "説明"


def test_soft_break_becomes_space():
    article = parse_article("一行目\n二行目\n")
    assert runs_to_text(article.blocks[0].runs) == "一行目 二行目"


def test_list_block_is_grouped():
    article = parse_article("段落\n\n- a\n- b\n\n段落2\n")
    assert [type(b) for b in article.blocks] == [Paragraph, ListBlock, Paragraph]
    assert len(article.blocks[1].items) == 2
