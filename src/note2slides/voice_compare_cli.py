"""ナレーションの声の候補を聴き比べるための音声を作るコマンド。

    python -m note2slides.voice_compare_cli samples/narration_sample.md -o build/voice_compare

標準の音声は変更しない。決めるのは、生成された音声を聞いた人。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from . import tts as tts_mod
from . import voicevox as voicevox_mod
from .audio import DEFAULT_LOUDNESS_LUFS, AudioExportError, AudioOptions
from .audio_cli import check_tools, voicevox_options_from
from .reading import ReadingStyle, load_dictionary
from .voice_compare import (
    CANDIDATES,
    DEFAULT_PREVIEW_SECONDS,
    Candidate,
    CompareResult,
    OutputExistsError,
    VoiceCompareError,
    compare_voices,
    load_candidates,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_SYNTHESIS = 4
EXIT_NO_ENGINE = 5

DEFAULT_OUTDIR = os.path.join("build", "voice_compare")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-voices",
        description=(
            "ナレーションの声の候補ごとに、同じ原稿を読み上げた音声を作ります。"
            "候補を選んだ理由と設定も一緒に書き出すので、聞いてから標準を決められます。"
        ),
    )
    parser.add_argument(
        "input", nargs="?", help="読み上げる記事(.md)・資料(.pptx)・原稿(.json)"
    )
    parser.add_argument(
        "-o", "--outdir", default=DEFAULT_OUTDIR, help=f"出力先(既定: {DEFAULT_OUTDIR})"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="出力先に前回の結果がある場合に上書きする"
    )

    group = parser.add_argument_group("候補")
    group.add_argument(
        "--candidates", metavar="PATH", help="候補の一覧(JSON。既定は組み込みの候補)"
    )
    group.add_argument(
        "--only",
        metavar="ID[,ID...]",
        help="指定した id の候補だけで比較材料を作る(比較表もその候補だけになる)",
    )
    group.add_argument(
        "--list-candidates", action="store_true", help="候補と選んだ理由を表示する"
    )

    listen = parser.add_argument_group("聴き比べ方")
    listen.add_argument(
        "--preview-seconds",
        type=float,
        default=DEFAULT_PREVIEW_SECONDS,
        help=f"頭出しに入れる長さ(秒、既定: {DEFAULT_PREVIEW_SECONDS:g}。0 で作らない)",
    )
    listen.add_argument(
        "--loudness",
        type=float,
        default=DEFAULT_LOUDNESS_LUFS,
        help=f"全候補でそろえる音量(LUFS、既定: {DEFAULT_LOUDNESS_LUFS:g})",
    )
    listen.add_argument(
        "--no-loudness", action="store_true", help="音量の調整をしない(声の大きさの差も比べる)"
    )
    listen.add_argument("--dict", metavar="PATH", help="読み方辞書(全候補で同じものを使う)")

    engine_group = parser.add_argument_group("エンジンの場所")
    engine_group.add_argument("--voicevox-url", help=f"VOICEVOX ENGINE の場所(既定: {voicevox_mod.DEFAULT_URL})")
    engine_group.add_argument("--voicevox-exe", help="VOICEVOX ENGINE の run.exe(既定: 自動検出)")
    engine_group.add_argument(
        "--no-voicevox-autostart",
        action="store_true",
        help="VOICEVOX ENGINE を自動で起動しない",
    )
    engine_group.add_argument(
        "--voicevox-startup-timeout",
        type=float,
        default=voicevox_mod.DEFAULT_STARTUP_TIMEOUT,
        help=f"VOICEVOX ENGINE の起動を待つ秒数(既定: {voicevox_mod.DEFAULT_STARTUP_TIMEOUT:g})",
    )
    engine_group.add_argument("--powershell", help="PowerShell の場所(既定: 自動検出)")

    parser.add_argument(
        "--timeout", type=float, default=1800, help="候補 1 つあたりの待ち時間(秒、既定: 1800)"
    )
    parser.add_argument("--keep-work", action="store_true", help="合成に使った作業ファイルを残す")
    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument("--check", action="store_true", help="音声合成に必要な環境の状態を表示する")
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    voicevox_options = voicevox_options_from(args)

    if args.check:
        return check_tools(
            tts_mod.ENGINE_AUTO, args.powershell, "ja", args.voicevox_exe, voicevox_options
        )

    try:
        candidates = _candidates(args)
    except VoiceCompareError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    if args.list_candidates:
        _list_candidates(candidates)
        return EXIT_OK

    if not args.input:
        print(
            "読み上げる記事(.md)・資料(.pptx)・原稿(.json)を指定してください。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    try:
        options = AudioOptions(
            loudness=None if args.no_loudness else args.loudness,
            reading=ReadingStyle(dictionary=load_dictionary(args.dict) if args.dict else {}),
        )
        options.validate()
    except (ValueError, AudioExportError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    # 候補 1 つで数分かかる。どこまで進んだかが分かるよう、スライド単位で出す
    # (途中で止まっているのか、時間がかかっているだけなのかを見分けられる)。
    def on_candidate(position: int, total: int, candidate: Candidate) -> None:
        if not args.quiet:
            print(f"[{position}/{total}] {candidate.id}（{candidate.voice}）", flush=True)

    def on_progress(index: int) -> None:
        if not args.quiet:
            print(f"    スライド {index}", flush=True)

    try:
        result = compare_voices(
            args.input,
            args.outdir,
            candidates=candidates,
            options=options,
            powershell=args.powershell,
            voicevox_options=voicevox_options,
            force=args.force,
            keep_work=args.keep_work,
            timeout=args.timeout,
            preview_seconds=args.preview_seconds,
            on_candidate=on_candidate,
            on_progress=on_progress,
        )
    except tts_mod.SpeechNotAvailableError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_ENGINE
    except OutputExistsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_EXISTS
    except VoiceCompareError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_SYNTHESIS

    _report(result, args.quiet)
    return EXIT_SYNTHESIS if result.failed else EXIT_OK


def _candidates(args) -> List[Candidate]:
    candidates = load_candidates(args.candidates) if args.candidates else list(CANDIDATES)
    if not args.only:
        return candidates

    wanted = [name.strip() for name in args.only.split(",") if name.strip()]
    known = {candidate.id: candidate for candidate in candidates}
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise VoiceCompareError(
            f"候補が見つかりません: {', '.join(unknown)}\n"
            f"指定できる id: {', '.join(known)}"
        )
    return [known[name] for name in wanted]


def _list_candidates(candidates: List[Candidate]) -> None:
    base = AudioOptions()
    for candidate in candidates:
        print(f"{candidate.id}: {candidate.voice}")
        if candidate.role:
            print(f"  位置づけ: {candidate.role}")
        print(f"  設定: {candidate.describe_settings(base)}")
        print(f"  理由: {candidate.reason}")


def _report(result: CompareResult, quiet: bool) -> None:
    for item in result.failed:
        print(
            f"{item.candidate.id}（{item.candidate.voice}）の音声を作れませんでした:",
            file=sys.stderr,
        )
        print("\n".join("  " + line for line in item.error.splitlines()), file=sys.stderr)
    if quiet:
        return

    print(f"{len(result.succeeded)} 候補の音声を出力しました: {result.outdir}")
    for item in result.succeeded:
        print(f"  {item.candidate.id}: {item.voice} / {_hms(item.duration)}")
    if result.preview_path:
        print(f"  頭出し: {os.path.basename(result.preview_path)}(候補を続けて並べたもの)")
    print(f"  比較表: {os.path.basename(result.report_path)}(候補を選んだ理由と設定)")
    print(f"  一覧: {os.path.basename(result.index_path)}")
    credits = sorted({item.credit for item in result.succeeded if item.credit})
    if credits:
        print(f"  公開する場合の表示: {' / '.join(credits)}")
    print("標準の音声は変更していません。聞いてから決めてください。")


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}分{total % 60:02d}秒"


if __name__ == "__main__":
    raise SystemExit(main())
