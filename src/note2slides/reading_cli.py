"""ナレーションが何と読まれるかを、音声を作らずに確かめるコマンド。

    python -m note2slides.reading_cli build/sample.pptx

音声・字幕・動画は、読み違えたままでも同じように生成できる。読み違いに気付くには
最後まで聞くしかなく、20 分の動画なら 20 分かかる。ここでは合成せずに読みだけを
取り出して表示するので、読んで確かめられる(`pronunciation.py`)。

直すときは、読み方辞書(JSON)を書いて `--dict` に渡す。`--dump-dict` で、
1 文字ずつ読まれている語だけを並べた雛形を書き出せる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from . import pronunciation as pron_mod
from . import tts as tts_mod
from . import voicevox as voicevox_mod
from .audio_cli import voicevox_options_from
from .console import use_utf8_output
from .narration import NarrationError, extract_script
from .reading import ReadingStyle, load_dictionary, plan_reading

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_ENGINE = 5
#: 1 文字ずつ読まれている語が残っている場合。気付かずに公開しないための合図。
EXIT_FOUND = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-reading",
        description=(
            "プレゼンテーション資料(.pptx)または原稿(.json)のナレーションが、"
            "何と読まれるかを表示します(音声は作りません)。"
        ),
    )
    parser.add_argument("input", help="入力する資料(.pptx)または原稿(.json)")
    parser.add_argument(
        "--words",
        action="store_true",
        help="英字の語だけを調べる(読み上げ単位ごとの仮名を出さない。速い)",
    )
    parser.add_argument("--dict", help="読み方辞書(JSON)。ここで直した結果を確かめられる")
    parser.add_argument(
        "--dump-dict",
        help="1 文字ずつ読まれている語だけを並べた読み方辞書の雛形を書き出す先(JSON)",
    )
    parser.add_argument("-o", "--out", help="結果を JSON として書き出す先")
    parser.add_argument("--max-chars", type=int, default=100, help="文を区切る長さ(既定: 100)")
    parser.add_argument("--read-urls", action="store_true", help="URL も読み上げる")

    voice_group = parser.add_argument_group("声")
    voice_group.add_argument("--voice", help="読みを聞く話者(既定: 音声生成と同じ既定の声)")
    voice_group.add_argument(
        "--engine",
        choices=voicevox_mod.EDITIONS,
        default=None,
        help="読みを聞くエンジン(既定: auto = 見つかったほう)",
    )

    engine_group = parser.add_argument_group("エンジンの場所")
    engine_group.add_argument("--voicevox-url", help="VOICEVOX ENGINE の接続先 URL")
    engine_group.add_argument("--voicevox-exe", help="VOICEVOX の run.exe の場所")
    engine_group.add_argument(
        "--no-voicevox-autostart", action="store_true", help="VOICEVOX を自動起動しない"
    )
    engine_group.add_argument(
        "--voicevox-startup-timeout",
        type=float,
        default=voicevox_mod.DEFAULT_STARTUP_TIMEOUT,
        help="VOICEVOX の起動を待つ秒数",
    )

    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    use_utf8_output()
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    try:
        dictionary = load_dictionary(args.dict) if args.dict else {}
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    style = ReadingStyle(
        max_chars=args.max_chars, drop_urls=not args.read_urls, dictionary=dictionary
    )
    try:
        script = extract_script(args.input)
    except NarrationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    first = min((s.index for s in script.segments), default=None)
    plans = {
        s.index: plan_reading(s.text, style, hold=s.hold, opening=(s.index == first))
        for s in script.segments
    }
    if not any(plan.utterances for plan in plans.values()):
        print("読み上げる文章がありません。", file=sys.stderr)
        return EXIT_USAGE

    engine = _open_engine(args)
    if engine is None:
        return EXIT_NO_ENGINE

    def on_progress(index: int) -> None:
        if not args.quiet:
            print(f"  {index:>3} 枚目", file=sys.stderr)

    try:
        voice = engine.pick_style(args.voice)
        report = pron_mod.inspect_readings(
            plans,
            pron_mod.reading_kana_reader(engine, voice),
            lines=not args.words,
            on_progress=on_progress,
        )
    except voicevox_mod.HttpFailure as exc:
        print(f"読みを取得できませんでした: {exc}", file=sys.stderr)
        return EXIT_NO_ENGINE
    finally:
        engine.close()

    _report(report, show_lines=not args.words)

    if args.out:
        _write_json(args.out, report.to_dict())
        print(f"\n読みを書き出しました: {args.out}")
    if args.dump_dict:
        _write_json(args.dump_dict, report.dictionary_template())
        print(f"読み方辞書の雛形を書き出しました: {args.dump_dict}")

    return EXIT_FOUND if report.spelled else EXIT_OK


def _open_engine(args):
    editions = [args.engine] if args.engine else list(voicevox_mod.EDITIONS)
    options = voicevox_options_from(args)
    last = ""
    for name in editions:
        edition = voicevox_mod.edition_for(name)
        engine = voicevox_mod.VoicevoxEngine(edition=edition, **options)
        try:
            engine.ensure_ready()
            return engine
        except tts_mod.SpeechNotAvailableError as exc:
            last = str(exc)
            engine.close()
    print(
        "読みを聞けるエンジンが見つかりませんでした。"
        "読みを返せるのは VOICEVOX だけです。\n" + last,
        file=sys.stderr,
    )
    return None


def _report(report: pron_mod.PronunciationReport, show_lines: bool) -> None:
    if show_lines:
        print("原稿と読み")
        print("=" * 60)
        for slide in report.slides:
            if not slide.lines:
                continue
            print(f"\n[{slide.index}]")
            for line in slide.lines:
                print(f"  {line.text}")
                print(f"    {line.kana}")
        print()

    if report.words:
        print("英字の読み")
        print("=" * 60)
        print("合成エンジンは英語の辞書を持たないため、英字はここだけが危ない。")
        for word in report.words:
            print(f"  {word.describe()}")
        print()

    spelled = report.spelled
    print("要確認")
    print("=" * 60)
    if spelled:
        for word in spelled:
            print(f"  {word.describe()}")
        print(
            f"\n{len(spelled)} 件が 1 文字ずつ読まれています。"
            "略語としてそれでよければ、そのままで構いません。\n"
            "直す場合は読み方辞書(JSON)を書いて --dict に渡してください"
            "(雛形は --dump-dict)。"
        )
    else:
        print("  1 文字ずつ読まれている英字はありませんでした。")
    print(
        "\n漢字の読み(「方」を「ほう」と読むか「かた」と読むかなど)は、"
        "正しい読みを機械が決められないため判定していません。"
        "上の「原稿と読み」で確かめてください。"
    )


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
