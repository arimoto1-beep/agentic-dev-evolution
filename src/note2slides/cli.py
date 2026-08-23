"""コマンドラインインターフェース。

入力は 3 種類あるが、どれも `Deck`(スライド構成)になったあとは同じ処理を
通る。資料・スライド画像・音声・動画の側に分岐は無い。

    記事(.md)        markdown_reader -> Article -> planner -> Deck
    公開 note 記事    note_source     -> Article -> planner -> Deck
    教材シナリオ(.md) scenario        -> Deck

記事入力は、記事の文章構造からスライド構成を推測する。教材シナリオは
「この画面にこれを出し、こう説明する」を先に決めて書いたものなので、推測は
行わず、書かれたとおりの枚数・画面・ナレーションにする。front matter に
`type: scenario` があるものを教材シナリオとして扱う。

    python -m note2slides samples/sample_article.md -o build/sample.pptx
    python -m note2slides https://note.com/<ユーザー>/n/<記事キー> -o build/article.pptx
    python -m note2slides samples/lesson_scenario.md -o build/lesson.pptx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from . import note_source
from . import scenario as scenario_mod
from .markdown_reader import parse_article_file
from .model import Article, Deck
from .note_source import NoteError
from .planner import PlannerOptions, plan_deck
from .renderer import render_deck
from .scenario import ScenarioError
from .style import DEFAULT_THEME, Style, get_theme, theme_names

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_FETCH_FAILED = 4

#: 記事の構成を組み立てるための指定。教材シナリオには構成が書かれているので、
#: 指定されていたら黙って無視せずに知らせる(シナリオどおりに作らなくなる)。
_ARTICLE_ONLY = (
    ("no_notes", "--no-notes"),
    ("no_split_sentences", "--no-split-sentences"),
    ("no_title_slide", "--no-title-slide"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides",
        description=(
            "note 記事(Markdown ファイル、または公開記事の URL)から "
            "16:9 のプレゼンテーション(.pptx)を生成します。"
        ),
    )
    parser.add_argument(
        "input",
        help="入力する Markdown ファイル(記事 / 教材シナリオ)、または公開 note 記事の URL",
    )
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
    parser.add_argument(
        "--theme",
        default=DEFAULT_THEME,
        choices=list(theme_names()),
        help="スライドの見た目(既定: %(default)s。plain は装飾なしの白地)",
    )
    parser.add_argument("--font-latin", default=Style.font_latin, help="欧文フォント")
    parser.add_argument("--font-ea", default=Style.font_ea, help="日本語フォント")
    parser.add_argument("--font-mono", default=Style.font_mono, help="等幅フォント")
    parser.add_argument(
        "--dump-plan", metavar="PATH", help="スライド構成を JSON として書き出す"
    )
    parser.add_argument(
        "--scenario",
        action="store_true",
        help="入力を教材シナリオとして読む(front matter の `type: scenario` が"
        "無い場合はエラーにする)",
    )

    url_group = parser.add_argument_group("URL を入力にする場合")
    url_group.add_argument(
        "--image-dir",
        metavar="DIR",
        help="取り込んだ記事の画像を置く場所(既定: 出力と同じ場所の <名前>_images)",
    )
    url_group.add_argument(
        "--dump-article",
        metavar="PATH",
        help="取り込んだ記事の内容を JSON として書き出す(本文の欠落・混入の確認用)",
    )
    url_group.add_argument(
        "--fetch-timeout",
        type=float,
        default=note_source.DEFAULT_TIMEOUT,
        help=f"記事と画像の取得の待ち時間(秒、既定: {note_source.DEFAULT_TIMEOUT:g})",
    )
    url_group.add_argument(
        "--user-agent",
        default=note_source.DEFAULT_USER_AGENT,
        help="取得に使う User-Agent",
    )

    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from_url = note_source.is_http_url(args.input)

    if not from_url and not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    if args.scenario and from_url:
        print(
            "教材シナリオは Markdown ファイルとして渡してください"
            "(URL からは記事として取り込みます)。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    from_scenario = not from_url and (args.scenario or scenario_mod.is_scenario_file(args.input))
    if from_scenario:
        rejected = [option for name, option in _ARTICLE_ONLY if getattr(args, name)]
        if rejected:
            print(
                f"教材シナリオでは使えない指定です: {' / '.join(rejected)}\n"
                "  記事から構成を組み立てるときの指定で、シナリオには構成が"
                "書かれているため使いません。",
                file=sys.stderr,
            )
            return EXIT_USAGE

    try:
        output = args.output or _default_output(args.input, from_url)
    except NoteError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    if os.path.exists(output) and not args.force:
        print(
            f"出力先が既に存在します: {output}\n上書きする場合は --force を付けてください。",
            file=sys.stderr,
        )
        return EXIT_EXISTS

    style = Style(
        theme=get_theme(args.theme),
        font_latin=args.font_latin,
        font_ea=args.font_ea,
        font_mono=args.font_mono,
    )
    if from_scenario:
        try:
            deck = _load_scenario(args, style)
        except ScenarioError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
    else:
        options = PlannerOptions(
            deck_title=args.title,
            split_sentences=not args.no_split_sentences,
            include_notes=not args.no_notes,
            title_slide=not args.no_title_slide,
        )
        if from_url:
            try:
                article = _fetch(args, output)
            except NoteError as exc:
                print(str(exc), file=sys.stderr)
                return EXIT_FETCH_FAILED
        else:
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
    return EXIT_OK


# ---------------------------------------------------------------------------
# 教材シナリオ
# ---------------------------------------------------------------------------


def _load_scenario(args, style: Style) -> Deck:
    """教材シナリオを、書かれたとおりのスライド構成にする。"""
    scenario = scenario_mod.read_scenario(args.input)
    deck = scenario_mod.build_deck(scenario, style=style, deck_title=args.title)
    if not args.quiet:
        narrated = sum(1 for slide in scenario.slides if slide.narration.strip())
        print(f"教材シナリオを読み込みました: {scenario.title}")
        print(f"  {scenario.count} 画面 / ナレーションのある画面 {narrated} 枚")
    return deck


# ---------------------------------------------------------------------------
# URL からの取り込み
# ---------------------------------------------------------------------------


def _default_output(source: str, from_url: bool) -> str:
    """出力先の既定。ファイルは同じ名前、URL は記事キーを名前にする。"""
    if not from_url:
        return os.path.splitext(source)[0] + ".pptx"
    return note_source.parse_note_key(source) + ".pptx"


def _fetch(args, output: str) -> Article:
    """公開 note 記事を取り込み、Article として返す。"""
    image_dir = args.image_dir or os.path.splitext(os.path.abspath(output))[0] + "_images"
    result = note_source.load_note_article(
        args.input,
        image_dir,
        timeout=args.fetch_timeout,
        user_agent=args.user_agent,
    )

    if args.dump_article:
        _write_json(args.dump_article, result.to_dict())

    for warning in result.warnings:
        print(f"警告: {warning}", file=sys.stderr)

    if not args.quiet:
        print(f"記事を取り込みました: {result.source.title}")
        print(f"  {result.source.url}")
        print(f"  本文 {len(result.article.blocks)} ブロック / 画像 {len(result.images)} 枚")
        for image in result.images:
            print(f"    {os.path.basename(image.path)}  {image.width}x{image.height}")
    return result.article


def _write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
