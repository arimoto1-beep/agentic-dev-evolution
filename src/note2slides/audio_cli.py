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
from . import voicevox as voicevox_mod
from .audio import (
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PEAK_CEILING_DBFS,
    DEFAULT_SAMPLE_RATE,
    SCRIPT_NAME,
    AudioExportError,
    AudioOptions,
    NarrationResult,
    OutputExistsError,
    export_narration,
    ffmpeg_pattern,
)
from .narration import NarrationError, extract_script
from .reading import ReadingStyle, load_dictionary

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

    voice_group = parser.add_argument_group("声")
    voice_group.add_argument(
        "--engine",
        choices=(tts_mod.ENGINE_AUTO,) + tts_mod.ENGINES,
        default=tts_mod.ENGINE_AUTO,
        help=f"音声合成の方式(既定: auto = {tts_mod.ENGINES[0]} を優先)",
    )
    voice_group.add_argument(
        "--voice",
        help=(
            "使う音声の名前(voicevox は「話者/スタイル」の形で指定する。"
            "既定は標準のナレーション音声)"
        ),
    )
    voice_group.add_argument("--language", default="ja", help="音声を選ぶときの言語(既定: ja)")
    voice_group.add_argument(
        "--speed", type=float, default=1.0, help="読み上げ速度の倍率(既定: 1.0)"
    )
    voice_group.add_argument(
        "--pitch", type=float, default=0.0, help="声の高さ(voicevox のみ、既定: 0.0)"
    )
    voice_group.add_argument(
        "--intonation",
        type=float,
        default=1.0,
        help="抑揚の強さ(voicevox のみ、既定: 1.0。小さくすると平坦になる)",
    )
    voice_group.add_argument("--volume", type=int, default=100, help="音量(0-100、既定: 100)")

    reading_group = parser.add_argument_group("読み方と間")
    reading_group.add_argument(
        "--sentence-pause", type=float, default=0.35, help="文と文の間(秒、既定: 0.35)"
    )
    reading_group.add_argument(
        "--line-pause", type=float, default=0.6, help="行と行の間(秒、既定: 0.6)"
    )
    reading_group.add_argument(
        "--lead-silence",
        type=float,
        default=0.3,
        help="スライドが変わってから話し始めるまでの無音(秒、既定: 0.3)",
    )
    reading_group.add_argument(
        "--opening-silence",
        type=float,
        default=1.0,
        help="最初のスライドで話し始めるまでの無音(秒、既定: 1.0)",
    )
    reading_group.add_argument(
        "--tail-silence",
        type=float,
        default=0.7,
        help="話し終わってから次のスライドまでの無音(秒、既定: 0.7)",
    )
    reading_group.add_argument(
        "--max-chars", type=int, default=100, help="この長さを超える文は読点で区切る(既定: 100)"
    )
    reading_group.add_argument(
        "--dict", metavar="PATH", help='読み方辞書(JSON。{"表記": "よみ"} の形)'
    )
    reading_group.add_argument(
        "--read-urls", action="store_true", help="URL も読み上げる(既定は読み上げない)"
    )
    reading_group.add_argument(
        "--silence",
        type=float,
        default=2.0,
        help="読み上げる文章が無いスライドの長さ(秒、既定: 2.0)",
    )

    output_group = parser.add_argument_group("音声ファイル")
    output_group.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"標本化周波数(既定: {DEFAULT_SAMPLE_RATE})",
    )
    output_group.add_argument(
        "--loudness",
        type=float,
        default=DEFAULT_LOUDNESS_LUFS,
        help=f"全体でそろえる音量(LUFS、既定: {DEFAULT_LOUDNESS_LUFS:g})",
    )
    output_group.add_argument(
        "--no-loudness", action="store_true", help="音量の調整をしない(合成したままの音量にする)"
    )
    output_group.add_argument(
        "--peak-ceiling",
        type=float,
        default=DEFAULT_PEAK_CEILING_DBFS,
        help=f"最大振幅の上限(dBFS、既定: {DEFAULT_PEAK_CEILING_DBFS:g})",
    )
    output_group.add_argument("--prefix", default="narration_", help="ファイル名の接頭辞")
    output_group.add_argument("--digits", type=int, default=3, help="連番の桁数(既定: 3)")

    engine_group = parser.add_argument_group("エンジンの場所")
    engine_group.add_argument(
        "--voicevox-url",
        help=f"VOICEVOX ENGINE の場所(既定: {voicevox_mod.DEFAULT_URL} / VOICEVOX_URL)",
    )
    engine_group.add_argument(
        "--voicevox-exe", help="VOICEVOX ENGINE の run.exe(既定: 自動検出 / VOICEVOX_ENGINE_PATH)"
    )
    engine_group.add_argument(
        "--no-voicevox-autostart",
        action="store_true",
        help="VOICEVOX ENGINE を自動で起動しない(既に起動しているものだけを使う)",
    )
    engine_group.add_argument(
        "--voicevox-startup-timeout",
        type=float,
        default=voicevox_mod.DEFAULT_STARTUP_TIMEOUT,
        help=f"VOICEVOX ENGINE の起動を待つ秒数(既定: {voicevox_mod.DEFAULT_STARTUP_TIMEOUT:g})",
    )
    engine_group.add_argument(
        "--powershell", help="PowerShell の場所(既定: 自動検出 / POWERSHELL_PATH)"
    )

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
    voicevox_options = voicevox_options_from(args)

    if args.check or args.list_voices:
        return check_tools(
            args.engine,
            args.powershell,
            args.language,
            voicevox_options,
            args.list_voices,
        )
    if not args.input:
        print("入力する資料(.pptx)または原稿(.json)を指定してください。", file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    try:
        options = _audio_options(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    outdir = args.outdir or os.path.splitext(os.path.abspath(args.input))[0] + "_audio"
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
            voicevox_options=voicevox_options,
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


def voicevox_options_from(args) -> dict:
    """VOICEVOX の接続先と起動の指定をまとめる(video_cli からも使う)。"""
    return {
        "url": args.voicevox_url,
        "exe": args.voicevox_exe,
        "autostart": not args.no_voicevox_autostart,
        "startup_timeout": args.voicevox_startup_timeout,
        "on_startup": None if args.quiet else lambda message: print(message),
    }


def _audio_options(args) -> AudioOptions:
    dictionary = load_dictionary(args.dict) if args.dict else {}
    return AudioOptions(
        engine=args.engine,
        voice=args.voice,
        language=args.language,
        speed=args.speed,
        pitch=args.pitch,
        intonation=args.intonation,
        volume=args.volume,
        sample_rate=args.sample_rate,
        reading=ReadingStyle(
            sentence_pause=args.sentence_pause,
            line_pause=args.line_pause,
            lead_silence=args.lead_silence,
            opening_silence=args.opening_silence,
            tail_silence=args.tail_silence,
            max_chars=args.max_chars,
            drop_urls=not args.read_urls,
            dictionary=dictionary,
        ),
        loudness=None if args.no_loudness else args.loudness,
        peak_ceiling=args.peak_ceiling,
        silent_duration=args.silence,
        prefix=args.prefix,
        digits=args.digits,
    )


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
    if result.loudness:
        print(
            f"  音量: {result.loudness.result_lufs:.1f} LUFS / "
            f"最大 {result.loudness.result_peak_dbfs:.1f} dBFS"
            f"(合成時 {result.loudness.measured_lufs:.1f} LUFS から "
            f"{result.loudness.gain_db:+.1f}dB)"
        )
    print(f"  合計の長さ: {_hms(result.total_duration)}")
    print(f"  一覧: {os.path.basename(result.manifest_path)}(index はスライド番号)")
    print(f"  ffmpeg で読む場合のパターン: {ffmpeg_pattern(options)}")
    if result.script_path:
        print(f"  原稿: {result.script_path}")
    if result.workdir:
        print(f"  作業ファイル: {result.workdir}")
    if result.credit:
        print(f"  公開する動画には次の表示が必要です: {result.credit}")


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}分{total % 60:02d}秒"


def check_tools(
    engine: str,
    powershell: Optional[str],
    language: str,
    voicevox_options: dict,
    list_voices: bool = False,
) -> int:
    """音声合成に使える環境かどうかを表示する。失敗したときの切り分けに使う。

    動画コマンド(video_cli)の `--check` からも同じ表示を使う。
    """
    targets = tts_mod.ENGINES if engine == tts_mod.ENGINE_AUTO else (engine,)
    usable = 0
    for name in targets:
        edition = voicevox_mod.edition_for(name)
        if edition is not None:
            usable += _check_voicevox(edition, voicevox_options, list_voices)
        else:
            usable += _check_windows(name, powershell, language)
    if not usable:
        print(
            "使える音声合成がありません。VOICEVOX を入れるか、Windows に日本語の音声を"
            "追加してください。",
            file=sys.stderr,
        )
        return EXIT_NO_ENGINE
    return EXIT_OK


def _check_voicevox(edition, voicevox_options: dict, list_voices: bool) -> int:
    options = tts_mod.voicevox_engine_options(edition, voicevox_options)
    exe = voicevox_mod.find_engine_exe(options.get("exe") or None, edition)
    url = options.get("url") or os.environ.get(edition.url_env) or edition.default_url
    engine = voicevox_mod.VoicevoxEngine(edition=edition, **options)
    try:
        version = engine.ensure_ready()
        styles = engine.list_styles()
    except tts_mod.SpeechError as exc:
        print(f"{edition.engine}: 使えません({url})", file=sys.stderr)
        print("\n".join("  " + line for line in str(exc).splitlines()), file=sys.stderr)
        return 0
    finally:
        engine.close()

    talk = [s for s in styles if s.kind == "talk"]
    print(f"{edition.engine}: 使えます(ENGINE {version} / {url} / 音声 {len(talk)} 種類)")
    if exe:
        print(f"  実行ファイル: {exe}")
    default = engine.pick_style()
    print(f"  既定の音声: {default.name}(公開時の表示: {default.credit})")
    if list_voices:
        for style in talk:
            mark = "*" if style.name == default.name else " "
            print(f"  {mark} {style.name}(id={style.id})")
    else:
        print("  (--list-voices で音声の一覧を表示します)")
    return 1


def _check_windows(name: str, powershell: Optional[str], language: str) -> int:
    found = tts_mod.find_powershell(powershell)
    if not found:
        print(f"{name}: 使えません(PowerShell が見つかりません)", file=sys.stderr)
        return 0
    try:
        engine = tts_mod.SpeechEngine(name, powershell)
        voices = engine.list_voices()
    except tts_mod.SpeechError as exc:
        print(f"{name}: 使えません", file=sys.stderr)
        print("\n".join("  " + line for line in str(exc).splitlines()), file=sys.stderr)
        return 0

    matched = [v for v in voices if v.speaks(language)]
    print(f"{name}: 使えます(音声 {len(voices)} 種類 / {language} は {len(matched)} 種類)")
    print(f"  PowerShell: {found}")
    print(f"  合成スクリプト: {tts_mod.script_path()}")
    for voice in voices:
        mark = "*" if voice in matched else " "
        print(f"  {mark} {voice.describe()}")
    if not matched:
        print(
            f"  {language} の音声がありません。Windows の設定 > 時刻と言語 > 言語と地域 "
            "から音声を追加できます。",
            file=sys.stderr,
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
