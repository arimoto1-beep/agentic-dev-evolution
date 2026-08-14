"""note 記事(Markdown)から eラーニング用のスライドと、その画像を生成する。

資料の生成は 3 段階に分かれている。

    markdown_reader : Markdown -> Article(ブロック列)
    planner         : Article  -> Deck(スライド構成)
    renderer        : Deck     -> .pptx

いずれの段階でも本文の要約・言い換え・追記は行わない。スライド上の文章は
記事本文をそのまま並べ替えたものになる。

生成した資料は、動画制作で使うスライド画像とナレーション音声に変換できる。

    slide_images    : .pptx    -> slide_001.png ...(LibreOffice + pypdfium2)
    narration       : .pptx    -> ナレーション原稿
    reading         : 原稿     -> 読み上げ単位 + 間の取り方
    tts / voicevox  : 読み上げ単位 -> 合成した音声
    waveform + audio: 音声     -> narration_001.wav ...(間と音量をそろえる)

画像と音声は同じ番号(スライド番号)で対応する。
"""

from .audio import AudioOptions, NarrationClip, NarrationResult, export_narration
from .markdown_reader import parse_article, parse_article_file
from .model import Article, Deck, Slide
from .narration import NarrationScript, NarrationSegment, extract_script
from .planner import PlannerOptions, plan_deck
from .reading import ReadingStyle, plan_reading
from .renderer import render_deck
from .slide_images import ExportResult, ImageOptions, SlideImage, export_slide_images
from .style import Style

__all__ = [
    "Article",
    "AudioOptions",
    "Deck",
    "ExportResult",
    "ImageOptions",
    "NarrationClip",
    "NarrationResult",
    "NarrationScript",
    "NarrationSegment",
    "PlannerOptions",
    "ReadingStyle",
    "Slide",
    "SlideImage",
    "Style",
    "convert_file",
    "export_narration",
    "export_slide_images",
    "extract_script",
    "parse_article",
    "parse_article_file",
    "plan_deck",
    "plan_reading",
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
