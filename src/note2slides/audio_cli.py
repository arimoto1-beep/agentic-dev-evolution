"""プレゼンテーション資料からナレーション音声を生成するコマンド。

    python -m note2slides.audio_cli build/sample.pptx -o build/audio
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from . import tts as tts_mod
from .audio import (
    SCRIPT_NAME,
    AudioExportError,
    AudioOptions,
    NarrationResult,
    OutputExistsError,
    export_narration,
    ffmpeg_pattern,
)
from .narration import NarrationError, extract_script

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_SYNTHESIS = 4
EXIT_NO_ENGINE = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-audio",
        description=(
            "プレゼンテーション資料(.pptx)から、スライド 1 枚ごとの"
            "ナレーション音声(WAV)を生成します。原稿(.json)も入力にできます。"
        ),
    )
    parser.add_argument("input", nargs="?", help="入力する資料(.pptx)または原稿(.json)")
    parser.add_argument(
        "-o", "--outdir", help="出力先ディレクトリ(既定: 入力と同じ場所の <名前>_audio)"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="出力先に既に音声がある場合に上書きする"
    )
    parser.add_argument(
        "--engine",
        choices=(tts_mod.ENGINE_AUTO,) + tts_mod.ENGINES,
        default=tts_mod.ENGINE_AUTO,
        help="音声合成の方式(既定: auto)",
    )
    parser.add_argument("--voice", help="使う音声の名前(既定: 指定言語の最初の音声)")
    parser.add_argument("--language", default="ja", help="音声を選ぶときの言語(既定: ja)")
    parser.add_argument(
        "--speed", type=float, default=1.0, help="読み上げ速度の倍率(既定: 1.0)"
    )
    parser.add_argument("--volume", type=int, default=100, help="音量(0-100、既定: 100)")
    parser.add_argument(
        "--sample-rate", type=int, help="標本化周波数(既定: 48000 / sapi のみ指定できる)"
    )
    parser.add_argument(
        "--tail-silence",
        type=float,
        default=0.5,
        help="各スライドの読み上げ後に足す無音の秒数(既定: 0.5)",
    )
    parser.add_argument(
        "--silence",
        type=float,
        default=2.0,
        help="読み上げる文章が無いスライドの長さ(秒、既定: 2.0)",
    )
    parser.add_argument("--prefix", default="narration_", help="ファイル名の接頭辞")
    parser.add_argument("--digits", type=int, default=3, help="連番の桁数(既定: 3)")
    parser.add_argument(
        "--dump-script", metavar="PATH", help="ナレーション原稿を JSON で書き出す"
    )
    parser.add_argument(
        "--script-only",
        action="store_true",
        help=f"原稿を書き出すだけで音声は作らない(既定の出力先: <出力先>/{SCRIPT_NAME})",
    )
    parser.add_argument(
        "--keep-work", action="store_true", help="合成に使った作業ファイルを残す"
    )
    parser.add_argument("--powershell", help="PowerShell の場所(既定: 自動検出 / POWERSHELL_PATH)")
    parser.add_argument(
        "--timeout", type=float, default=600, help="合成の待ち時間(秒、既定: 600)"
    )
    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument(
        "--list-voices", action="store_true", help="使える音声の一覧を表示する"
    )
    parser.add_argument(
        "--check", action="store_true", help="音声合成に必要な環境の状態だけを表示する"
    )
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check or args.list_voices:
        return _check(args.powershell, args.engine, args.language)
    if not args.input:
        print("入力する資料(.pptx)または原稿(.json)を指定してください。", file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    outdir = args.outdir or os.path.splitext(os.path.abspath(args.input))[0] + "_audio"
    options = AudioOptions(
        engine=args.engine,
        voice=args.voice,
        language=args.language,
        speed=args.speed,
        volume=args.volume,
        sample_rate=args.sample_rate,
        tail_silence=args.tail_silence,
        silent_duration=args.silence,
        prefix=args.prefix,
        digits=args.digits,
    )

    if args.script_only:
        return _dump_script(args, outdir)

    def on_progress(index: int) -> None:
        if not args.quiet:
            print(f"  {index:>3}: {options.filename(index)}")

    try:
        result = export_narration(
            args.input,
            outdir,
            options=options,
            powershell=args.powershell,
            force=args.force,
            keep_work=args.keep_work,
            timeout=args.timeout,
            dump_script=args.dump_script,
            on_progress=on_progress,
        )
    except tts_mod.SpeechNotAvailableError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_ENGINE
    except OutputExistsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_EXISTS
    except NarrationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except (AudioExportError, tts_mod.SpeechError) as exc:
        print(f"音声を生成できませんでした: {exc}", file=sys.stderr)
        return EXIT_SYNTHESIS

    for warning in result.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    if not args.quiet:
        _report(result, options, outdir)
    return EXIT_OK


def _dump_script(args, outdir: str) -> int:
    path = args.dump_script or os.path.join(outdir, SCRIPT_NAME)
    try:
        script = extract_script(args.input)
    except NarrationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    script.write(path)
    for warning in script.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    if not args.quiet:
        print(f"{script.count} 枚分の原稿を書き出しました: {path}")
        print("内容を直してから、この原稿を入力にすると読み方を変えられます。")
    return EXIT_OK


def _report(result: NarrationResult, options: AudioOptions, outdir: str) -> None:
    print(f"{result.count} 枚分のナレーション音声を出力しました: {outdir}")
    if result.engine:
        print(f"  エンジン: {result.engine} / 音声: {result.voice}")
    if result.audio_format:
        print(f"  形式: WAV {result.audio_format.describe()}")
    print(f"  合計の長さ: {_hms(result.total_duration)}")
    print(f"  一覧: {os.path.basename(result.manifest_path)}(index はスライド番号)")
    print(f"  ffmpeg で読む場合のパターン: {ffmpeg_pattern(options)}")
    if result.script_path:
        print(f"  原稿: {result.script_path}")
    if result.workdir:
        print(f"  作業ファイル: {result.workdir}")


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}分{total % 60:02d}秒"


def _check(powershell: Optional[str], engine: str, language: str) -> int:
    """音声合成に使える環境かどうかを表示する。失敗したときの切り分けに使う。"""
    found = tts_mod.find_powershell(powershell)
    if not found:
        print("PowerShell: 見つかりません", file=sys.stderr)
        print(
            "  Windows PowerShell が必要です。--powershell か環境変数 POWERSHELL_PATH "
            "で場所を指定してください。",
            file=sys.stderr,
        )
        return EXIT_NO_ENGINE

    print(f"PowerShell: {found}")
    print(f"合成スクリプト: {tts_mod.script_path()}")

    targets = tts_mod.ENGINES if engine == tts_mod.ENGINE_AUTO else (engine,)
    usable = 0
    for name in targets:
        try:
            speech_engine = tts_mod.SpeechEngine(name, powershell)
            voices = speech_engine.list_voices()
        except tts_mod.SpeechError as exc:
            print(f"{name}: 使えません", file=sys.stderr)
            print("\n".join("  " + line for line in str(exc).splitlines()), file=sys.stderr)
            continue
        matched = [v for v in voices if v.speaks(language)]
        print(f"{name}: 使えます(音声 {len(voices)} 種類 / {language} は {len(matched)} 種類)")
        for voice in voices:
            mark = "*" if voice in matched else " "
            print(f"  {mark} {voice.describe()}")
        if matched:
            usable += 1
        else:
            print(
                f"  {language} の音声がありません。Windows の設定 > 時刻と言語 > 言語と地域 "
                "から音声を追加できます。",
                file=sys.stderr,
            )

    if not usable:
        return EXIT_NO_ENGINE
    print(f"(* が {language} の音声。--voice で名前を指定できます)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
