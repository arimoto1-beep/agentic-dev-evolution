"""プレゼンテーション資料をスライド画像に変換するコマンド。

    python -m note2slides.images_cli build/sample.pptx -o build/slides
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__, contact_sheet
from .console import use_utf8_output
from .slide_images import (
    FORMATS,
    ImageOptions,
    OutputExistsError,
    SlideImage,
    SlideImageError,
    export_slide_images,
    ffmpeg_pattern,
)
from .proc import format_command
from .soffice import SofficeError, SofficeNotFoundError, find_soffice, get_version

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_CONVERT = 4
EXIT_NO_SOFFICE = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-images",
        description=(
            "プレゼンテーション資料(.pptx / .odp / .pdf)を、"
            "スライド 1 枚ずつの画像に変換します。"
        ),
    )
    parser.add_argument("input", nargs="?", help="入力する資料")
    parser.add_argument(
        "-o", "--outdir", help="出力先ディレクトリ(既定: 入力と同じ場所の <名前>_slides)"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="出力先に既に画像がある場合に上書きする"
    )
    parser.add_argument("--width", type=int, default=1920, help="画像の幅(既定: 1920)")
    parser.add_argument(
        "--height", type=int, help="画像の高さ(既定: 幅から 16:9 で決める)"
    )
    parser.add_argument(
        "--format", dest="fmt", choices=sorted(FORMATS), default="png", help="画像形式"
    )
    parser.add_argument(
        "--jpeg-quality", type=int, default=92, help="JPEG の品質(1-95、既定: 92)"
    )
    parser.add_argument("--prefix", default="slide_", help="ファイル名の接頭辞")
    parser.add_argument("--digits", type=int, default=3, help="連番の桁数(既定: 3)")
    parser.add_argument(
        "--keep-pdf", action="store_true", help="変換の中間生成物の PDF を残す"
    )
    parser.add_argument(
        "--contact-sheet",
        action="store_true",
        help="スライドを並べた一覧(contact_001.png ...)も作る。全体を見比べて確かめる用",
    )
    parser.add_argument(
        "--contact-columns",
        type=int,
        default=contact_sheet.DEFAULT_COLUMNS,
        help=f"一覧の列数(既定: {contact_sheet.DEFAULT_COLUMNS})",
    )
    parser.add_argument(
        "--contact-rows",
        type=int,
        default=contact_sheet.DEFAULT_ROWS,
        help=f"一覧の行数(既定: {contact_sheet.DEFAULT_ROWS})",
    )
    parser.add_argument(
        "--soffice", help="LibreOffice(soffice)の場所(既定: 自動検出 / SOFFICE_PATH)"
    )
    parser.add_argument(
        "--timeout", type=float, default=180, help="LibreOffice の待ち時間(秒、既定: 180)"
    )
    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument(
        "--check", action="store_true", help="変換に必要な外部ツールの状態だけを表示する"
    )
    parser.add_argument(
        "--version", action="version", version=f"note2slides {__version__}"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    use_utf8_output()
    args = build_parser().parse_args(argv)

    if args.check:
        return check_tools(args.soffice)
    if not args.input:
        print("入力する資料を指定してください。", file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    outdir = args.outdir or os.path.splitext(os.path.abspath(args.input))[0] + "_slides"
    options = ImageOptions(
        width=args.width,
        height=args.height,
        fmt=args.fmt,
        jpeg_quality=args.jpeg_quality,
        prefix=args.prefix,
        digits=args.digits,
    )

    def on_progress(image: SlideImage) -> None:
        if not args.quiet:
            print(f"  {image.index:>3}: {image.filename}")

    try:
        result = export_slide_images(
            args.input,
            outdir,
            options=options,
            soffice_path=args.soffice,
            force=args.force,
            keep_pdf=args.keep_pdf,
            timeout=args.timeout,
            on_progress=on_progress,
        )
    except SofficeNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SOFFICE
    except OutputExistsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_EXISTS
    except (SofficeError, SlideImageError) as exc:
        print(f"変換に失敗しました: {exc}", file=sys.stderr)
        return EXIT_CONVERT

    for warning in result.warnings:
        print(f"警告: {warning}", file=sys.stderr)

    sheets = []
    if args.contact_sheet:
        try:
            sheets = contact_sheet.build_contact_sheets(
                [image.path for image in result.images],
                outdir,
                numbers=[image.index for image in result.images],
                columns=args.contact_columns,
                rows=args.contact_rows,
                fmt="png" if options.fmt not in ("png", "jpg") else options.fmt,
            )
        except contact_sheet.ContactSheetError as exc:
            print(f"一覧の生成に失敗しました: {exc}", file=sys.stderr)
            return EXIT_CONVERT

    if not args.quiet:
        width, height = options.size()
        print(f"{result.count} 枚のスライド画像を出力しました: {outdir}")
        print(f"  サイズ: {width}x{height} / 形式: {options.fmt}")
        print(f"  一覧: {os.path.basename(result.manifest_path)}")
        print(f"  ffmpeg で読む場合のパターン: {ffmpeg_pattern(options)}")
        for sheet in sheets:
            print(f"  一覧: {os.path.basename(sheet.path)}({len(sheet.slides)} 枚)")
        if result.pdf_path:
            print(f"  中間 PDF: {result.pdf_path}")
    return EXIT_OK


def check_tools(explicit: Optional[str] = None) -> int:
    """外部ツールの状態を表示する。変換が失敗したときの切り分けに使う。

    動画コマンド(video_cli)の `--check` からも同じ表示を使う。
    """
    ok = True

    soffice = find_soffice(explicit)
    if soffice:
        print(f"LibreOffice: {soffice}")
        version = get_version(soffice)
        print(f"  バージョン: {version or '取得できませんでした'}")
        print(f"  変換コマンド例: {format_command([soffice, '--headless', '--convert-to', 'pdf', 'input.pptx'])}")
    else:
        ok = False
        print("LibreOffice: 見つかりません", file=sys.stderr)
        print(
            "  インストールするか、--soffice / 環境変数 SOFFICE_PATH で場所を指定してください。",
            file=sys.stderr,
        )

    for module, purpose in (("pypdfium2", "PDF の描画"), ("PIL", "画像の書き出し")):
        try:
            __import__(module)
        except ImportError as exc:
            ok = False
            print(f"{module}: 見つかりません({purpose})- {exc}", file=sys.stderr)
        else:
            print(f"{module}: 利用できます({purpose})")

    return EXIT_OK if ok else EXIT_NO_SOFFICE


if __name__ == "__main__":
    raise SystemExit(main())
