"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .markdown_reader import parse_article_file
from .planner import PlannerOptions, plan_deck
from .renderer import render_deck
from .style import Style


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides",
        description="note 記事(Markdown)から 16:9 のプレゼンテーション(.pptx)を生成します。",
    )
    parser.add_argument("input", help="入力する Markdown ファイル")
    parser.add_argument(
        "-o", "--output", help="出力する .pptx のパス(既定: 入力と同じ場所・同じ名前)"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="出力先が既に存在する場合に上書きする"
    )
    parser.add_argument("--title", help="表紙のタイトル(既定: front matter または最初の見出し)")
    parser.add_argument(
        "--no-title-slide", action="store_true", help="表紙スライドを作らない"
    )
    parser.add_argument(
        "--no-split-sentences",
        action="store_true",
        help="段落を文単位に分けず、1 段落を 1 項目として扱う",
    )
    parser.add_argument(
        "--no-notes", action="store_true", help="発表者ノートに元の本文を入れない"
    )
    parser.add_argument("--font-latin", default=Style.font_latin, help="欧文フォント")
    parser.add_argument("--font-ea", default=Style.font_ea, help="日本語フォント")
    parser.add_argument("--font-mono", default=Style.font_mono, help="等幅フォント")
    parser.add_argument(
        "--dump-plan", metavar="PATH", help="スライド構成を JSON として書き出す"
    )
    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return 2

    output = args.output or os.path.splitext(args.input)[0] + ".pptx"
    if os.path.exists(output) and not args.force:
        print(
            f"出力先が既に存在します: {output}\n上書きする場合は --force を付けてください。",
            file=sys.stderr,
        )
        return 3

    style = Style(
        font_latin=args.font_latin,
        font_ea=args.font_ea,
        font_mono=args.font_mono,
    )
    options = PlannerOptions(
        deck_title=args.title,
        split_sentences=not args.no_split_sentences,
        include_notes=not args.no_notes,
        title_slide=not args.no_title_slide,
    )

    article = parse_article_file(args.input)
    deck = plan_deck(article, style=style, options=options)

    output_dir = os.path.dirname(os.path.abspath(output))
    os.makedirs(output_dir, exist_ok=True)
    render_deck(deck, output, style=style)

    if args.dump_plan:
        with open(args.dump_plan, "w", encoding="utf-8") as f:
            json.dump(deck.to_dict(), f, ensure_ascii=False, indent=2)

    for warning in deck.warnings:
        print(f"警告: {warning}", file=sys.stderr)

    if not args.quiet:
        print(f"{len(deck.slides)} 枚のスライドを生成しました: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
