"""note2slides-thumb: 動画の外側で使う 1 枚絵(サムネイル)を書き出す。

    note2slides-thumb samples/lesson_scenario.md -o build/thumbnail.png
    note2slides-thumb build/lesson.pptx -o build/thumbnail.png --label "第23回"
    note2slides-thumb --title "教材シナリオから動画を作る" -o build/thumbnail.png

入力(記事 / 教材シナリオ / 資料)を渡すと、表紙の題と副題をそのまま使う。
--title / --subtitle を付けた場合は、そちらが優先される(サムネイルの文字だけ
言い換えたいことがあるため)。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from . import thumbnail as thumbnail_mod
from .console import use_utf8_output
from .slide_images import FORMATS
from .soffice import SofficeError
from .style import DEFAULT_THEME, Style, get_theme, theme_names
from .thumbnail import DEFAULT_WIDTH, Thumbnail, ThumbnailError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-thumb",
        description="記事・教材シナリオ・資料から、YouTube 用のサムネイル画像を生成します。",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="入力(記事 / 教材シナリオ の .md、または資料の .pptx)。"
        "省略する場合は --title を指定します。",
    )
    parser.add_argument(
        "-o", "--output", help="出力する画像のパス(既定: 入力と同じ場所の <名前>_thumbnail.png)"
    )
    parser.add_argument("-f", "--force", action="store_true", help="出力先を上書きする")
    parser.add_argument("--title", help="サムネイルに出す題(既定: 入力の表紙の題)")
    parser.add_argument("--subtitle", help="題の下に出す文字(既定: 入力の表紙の副題)")
    parser.add_argument("--no-subtitle", action="store_true", help="副題を出さない")
    parser.add_argument("--label", default="", help="左上に出す短い文字(例: 第23回)")
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, help=f"横の画素数(既定: {DEFAULT_WIDTH})"
    )
    parser.add_argument(
        "--format", default="png", choices=sorted(FORMATS), help="画像の形式(既定: png)"
    )
    parser.add_argument(
        "--theme",
        default=DEFAULT_THEME,
        choices=list(theme_names()),
        help="見た目(既定: %(default)s)。資料と同じものを指定します。",
    )
    parser.add_argument("--font-latin", default=Style.font_latin, help="欧文フォント")
    parser.add_argument("--font-ea", default=Style.font_ea, help="日本語フォント")
    parser.add_argument("--soffice", help="LibreOffice(soffice)の場所")
    parser.add_argument(
        "--keep-pptx", action="store_true", help="元にした 1 枚だけの .pptx も残す"
    )
    parser.add_argument("--quiet", action="store_true", help="結果を表示しない")
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    use_utf8_output()
    args = build_parser().parse_args(argv)

    if not args.input and not args.title:
        print("入力ファイルか --title のどちらかを指定してください。", file=sys.stderr)
        return EXIT_USAGE

    try:
        thumbnail = _resolve(args)
    except ThumbnailError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    output = args.output or _default_output(args.input, args.format)
    style = Style(
        theme=get_theme(args.theme), font_latin=args.font_latin, font_ea=args.font_ea
    )
    try:
        result = thumbnail_mod.export_thumbnail(
            thumbnail,
            output,
            style=style,
            width=args.width,
            fmt=args.format,
            soffice_path=args.soffice,
            force=args.force,
            keep_pptx=args.keep_pptx,
        )
    except (ThumbnailError, SofficeError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED

    if not args.quiet:
        print(f"サムネイルを出力しました: {result.path}")
        print(f"  サイズ: {result.width}x{result.height}")
        print(f"  題: {thumbnail.title}")
        if thumbnail.subtitle:
            print(f"  副題: {thumbnail.subtitle}")
        if result.pptx_path:
            print(f"  元の資料: {result.pptx_path}")
    return EXIT_OK


def _resolve(args) -> Thumbnail:
    """入力と指定から、サムネイルに出す文字を決める。"""
    base = thumbnail_mod.from_source(args.input) if args.input else Thumbnail(title="")
    title = args.title or base.title
    subtitle = "" if args.no_subtitle else (args.subtitle or base.subtitle)
    if not title.strip():
        raise ThumbnailError("サムネイルの題が空です。--title で指定してください。")
    return Thumbnail(title=title, subtitle=subtitle, label=args.label)


def _default_output(source: Optional[str], fmt: str) -> str:
    suffix = FORMATS[fmt][1]
    if not source:
        return f"thumbnail{suffix}"
    return os.path.splitext(source)[0] + "_thumbnail" + suffix


if __name__ == "__main__":
    raise SystemExit(main())
