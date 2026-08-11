"""note 記事(Markdown)から eラーニング用のスライド(.pptx)を生成する。

処理は 3 段階に分かれている。

    markdown_reader : Markdown -> Article(ブロック列)
    planner         : Article  -> Deck(スライド構成)
    renderer        : Deck     -> .pptx

いずれの段階でも本文の要約・言い換え・追記は行わない。スライド上の文章は
記事本文をそのまま並べ替えたものになる。
"""

from .markdown_reader import parse_article, parse_article_file
from .model import Article, Deck, Slide
from .planner import PlannerOptions, plan_deck
from .renderer import render_deck
from .style import Style

__all__ = [
    "Article",
    "Deck",
    "PlannerOptions",
    "Slide",
    "Style",
    "convert_file",
    "parse_article",
    "parse_article_file",
    "plan_deck",
    "render_deck",
]

__version__ = "0.1.0"


def convert_file(
    input_path: str,
    output_path: str,
    style: Style | None = None,
    options: PlannerOptions | None = None,
) -> Deck:
    """Markdown ファイルから .pptx を生成し、生成したスライド構成を返す。"""
    article = parse_article_file(input_path)
    deck = plan_deck(article, style=style, options=options)
    render_deck(deck, output_path, style=style)
    return deck
