"""プレゼンテーション資料からナレーション付き動画を生成するコマンド。

    python -m note2slides.video_cli build/sample.pptx -o build/sample.mp4

途中で作るスライド画像とナレーション音声は残す。読み方や見た目を直したあとは、
その工程だけをやり直して、素材から動画を組み立て直せる。

    python -m note2slides.video_cli --slides build/sample_slides --audio build/sample_audio \
        -o build/sample.mp4 -f
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

from . import __version__
from . import audio_cli, images_cli
from . import audio as audio_mod
from . import captions as captions_mod
from . import ffmpeg as ffmpeg_mod
from . import slide_images as images_mod
from . import tts as tts_mod
from . import thumbnail as thumbnail_mod
from . import video as video_mod
from . import voicevox as voicevox_mod
from .audio import AudioExportError, AudioOptions
from .narration import NarrationError
from .reading import ReadingStyle, load_dictionary
from .soffice import SofficeError, SofficeNotFoundError
from .thumbnail import ThumbnailError
from .video import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_CRF,
    DEFAULT_FPS,
    DEFAULT_MIN_DURATION,
    DEFAULT_PRESET,
    OutputExistsError,
    VideoError,
    VideoOptions,
    VideoResult,
    timestamp,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_FAILED = 4
EXIT_NO_TOOL = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note2slides-video",
        description=(
            "プレゼンテーション資料(.pptx)から、スライドごとのナレーションが付いた"
            "動画(MP4 / 16:9)を生成します。"
        ),
    )
    parser.add_argument("input", nargs="?", help="入力する資料(.pptx / .odp / .pdf)")
    parser.add_argument("-o", "--output", help="出力する動画(.mp4、既定: 入力と同じ名前)")
    parser.add_argument(
        "-f", "--force", action="store_true", help="出力先に既にファイルがある場合に上書きする"
    )

    material_group = parser.add_argument_group("素材の置き場所")
    material_group.add_argument(
        "--slides",
        metavar="DIR",
        help="スライド画像の場所(既定: 出力と同じ場所の <名前>_slides)。"
        "資料を指定しない場合は、ここにある画像から組み立てる",
    )
    material_group.add_argument(
        "--audio",
        metavar="DIR",
        help="ナレーション音声の場所(既定: 出力と同じ場所の <名前>_audio)",
    )

    screen_group = parser.add_argument_group("画面")
    screen_group.add_argument("--width", type=int, default=1920, help="幅(既定: 1920)")
    screen_group.add_argument("--height", type=int, help="高さ(既定: 幅から 16:9 で決める)")
    screen_group.add_argument(
        "--fps", type=int, default=DEFAULT_FPS, help=f"フレームレート(既定: {DEFAULT_FPS})"
    )
    screen_group.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help=f"画質(0-51。小さいほど高画質で大きいファイル、既定: {DEFAULT_CRF})",
    )
    screen_group.add_argument(
        "--preset", default=DEFAULT_PRESET, help=f"符号化の速さ(既定: {DEFAULT_PRESET})"
    )
    screen_group.add_argument(
        "--min-duration",
        type=float,
        default=DEFAULT_MIN_DURATION,
        help=f"1 枚を映す最短の長さ(秒、既定: {DEFAULT_MIN_DURATION:g})",
    )
    screen_group.add_argument(
        "--background", default="#ffffff", help="16:9 に足す余白の色(既定: #ffffff)"
    )

    voice_group = parser.add_argument_group("声")
    voice_group.add_argument(
        "--engine",
        choices=(tts_mod.ENGINE_AUTO,) + tts_mod.ENGINES,
        default=tts_mod.ENGINE_AUTO,
        help=f"音声合成の方式(既定: auto = {tts_mod.ENGINES[0]} を優先)",
    )
    voice_group.add_argument("--voice", help="使う音声(voicevox は「話者/スタイル」)")
    voice_group.add_argument(
        "--speed", type=float, default=1.0, help="読み上げ速度の倍率(既定: 1.0)"
    )
    voice_group.add_argument(
        "--dict", metavar="PATH", help='読み方辞書(JSON。{"表記": "よみ"} の形)'
    )
    voice_group.add_argument(
        "--silence",
        type=float,
        default=2.0,
        help="読み上げる文章が無いスライドの長さ(秒、既定: 2.0)",
    )
    voice_group.add_argument(
        "--no-loudness", action="store_true", help="音量の調整をしない"
    )
    voice_group.add_argument(
        "--audio-bitrate",
        default=DEFAULT_AUDIO_BITRATE,
        help=f"動画の音声の符号化量(既定: {DEFAULT_AUDIO_BITRATE})",
    )

    tool_group = parser.add_argument_group("外部ツールの場所")
    tool_group.add_argument(
        "--ffmpeg", help="ffmpeg の場所(既定: 自動検出 / FFMPEG_PATH / 同梱のもの)"
    )
    tool_group.add_argument(
        "--soffice", help="LibreOffice(soffice)の場所(既定: 自動検出 / SOFFICE_PATH)"
    )
    tool_group.add_argument(
        "--powershell", help="PowerShell の場所(既定: 自動検出 / POWERSHELL_PATH)"
    )
    tool_group.add_argument(
        "--voicevox-url",
        help=f"VOICEVOX ENGINE の場所(既定: {voicevox_mod.DEFAULT_URL} / VOICEVOX_URL)",
    )
    tool_group.add_argument(
        "--voicevox-exe", help="VOICEVOX ENGINE の run.exe(既定: 自動検出 / VOICEVOX_ENGINE_PATH)"
    )
    tool_group.add_argument(
        "--no-voicevox-autostart",
        action="store_true",
        help="VOICEVOX ENGINE を自動で起動しない",
    )
    tool_group.add_argument(
        "--voicevox-startup-timeout",
        type=float,
        default=voicevox_mod.DEFAULT_STARTUP_TIMEOUT,
        help=f"VOICEVOX ENGINE の起動を待つ秒数(既定: {voicevox_mod.DEFAULT_STARTUP_TIMEOUT:g})",
    )

    parser.add_argument(
        "--slides-timeout", type=float, default=300, help="LibreOffice の待ち時間(秒、既定: 300)"
    )
    parser.add_argument(
        "--audio-timeout", type=float, default=1800, help="音声合成の待ち時間(秒、既定: 1800)"
    )
    parser.add_argument(
        "--timeout", type=float, default=1800, help="動画の書き出しの待ち時間(秒、既定: 1800)"
    )
    parser.add_argument(
        "--keep-work", action="store_true", help="書き出しに使った作業ファイルを残す"
    )
    publish_group = parser.add_argument_group("投稿の準備")
    publish_group.add_argument(
        "--thumbnail",
        nargs="?",
        const="",
        metavar="PATH",
        help="投稿用のサムネイル画像も書き出す"
        "(既定: 動画と同じ場所の <名前>_thumbnail.png。資料からの生成時のみ)",
    )
    publish_group.add_argument(
        "--thumbnail-label", default="", help="サムネイルの左上に出す短い文字(例: 第23回)"
    )
    publish_group.add_argument(
        "--captions",
        choices=captions_mod.FORMATS,
        default=captions_mod.FORMAT_SRT,
        help=f"一緒に書き出す字幕の形式(既定: {captions_mod.FORMAT_SRT}、"
        f"{captions_mod.FORMAT_NONE} で作らない)",
    )
    publish_group.add_argument(
        "--caption-chars",
        type=int,
        default=captions_mod.CaptionStyle().line_chars,
        help=f"字幕 1 行の文字数(既定: {captions_mod.CaptionStyle().line_chars})",
    )
    publish_group.add_argument(
        "--no-chapters", action="store_true", help="概要欄に貼る章立てを書き出さない"
    )

    parser.add_argument("--quiet", action="store_true", help="進捗を表示しない")
    parser.add_argument(
        "--check", action="store_true", help="生成に必要な外部ツールの状態だけを表示する"
    )
    parser.add_argument("--version", action="version", version=f"note2slides {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        return check_tools(args)

    try:
        options = _video_options(args)
        options.validate()
    except (ValueError, VideoError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    if args.input:
        return _from_presentation(args, options)
    if args.slides:
        return _from_materials(args, options)
    print(
        "入力する資料(.pptx)を指定してください。"
        "書き出し済みの素材から作る場合は --slides と --audio を指定します。",
        file=sys.stderr,
    )
    return EXIT_USAGE


# ---------------------------------------------------------------------------
# 資料から通しで作る
# ---------------------------------------------------------------------------


def _from_presentation(args, options: VideoOptions) -> int:
    if not os.path.isfile(args.input):
        print(f"入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return EXIT_USAGE

    default = os.path.splitext(os.path.abspath(args.input))[0] + video_mod.VIDEO_SUFFIX
    out_path = args.output or default
    try:
        audio_options = _audio_options(args, options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    reporter = _Reporter(args.quiet)
    try:
        result = video_mod.export_video(
            args.input,
            out_path,
            slides_dir=args.slides,
            audio_dir=args.audio,
            options=options,
            audio_options=audio_options,
            soffice_path=args.soffice,
            ffmpeg_path=args.ffmpeg,
            powershell=args.powershell,
            voicevox_options=audio_cli.voicevox_options_from(args),
            force=args.force,
            keep_work=args.keep_work,
            slides_timeout=args.slides_timeout,
            audio_timeout=args.audio_timeout,
            encode_timeout=args.timeout,
            on_stage=reporter.stage,
            on_slide=reporter.slide,
            on_encode=reporter.encode,
        )
    except (
        SofficeNotFoundError,
        ffmpeg_mod.FfmpegNotFoundError,
        tts_mod.SpeechNotAvailableError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_TOOL
    except (OutputExistsError, images_mod.OutputExistsError, audio_mod.OutputExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_EXISTS
    except NarrationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except (
        VideoError,
        SofficeError,
        images_mod.SlideImageError,
        AudioExportError,
        tts_mod.SpeechError,
        ffmpeg_mod.FfmpegError,
    ) as exc:
        print(f"動画を生成できませんでした: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _report(result, options, args.quiet)
    if args.thumbnail is not None:
        return _write_thumbnail(args, out_path)
    return EXIT_OK


def _write_thumbnail(args, video_path: str) -> int:
    """動画と同じ題で、投稿に使う 1 枚絵も書き出す。

    動画ができたあとに作る。ここで失敗しても動画は残るので、何が起きたかを
    知らせたうえで、失敗として返す(頼まれたものを作れていないため)。
    """
    path = args.thumbnail or _default_thumbnail(video_path)
    try:
        thumbnail = thumbnail_mod.from_source(args.input)
        thumbnail.label = args.thumbnail_label
        result = thumbnail_mod.export_thumbnail(
            thumbnail, path, soffice_path=args.soffice, force=args.force
        )
    except (ThumbnailError, SofficeError) as exc:
        print(f"サムネイルを生成できませんでした: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if not args.quiet:
        print(f"サムネイル: {result.path}({result.width}x{result.height})")
    return EXIT_OK


def _default_thumbnail(video_path: str) -> str:
    return os.path.splitext(video_path)[0] + "_thumbnail.png"


# ---------------------------------------------------------------------------
# 書き出し済みの素材から作る
# ---------------------------------------------------------------------------


def _from_materials(args, options: VideoOptions) -> int:
    if args.thumbnail is not None:
        print(
            "--thumbnail は資料(.pptx)から動画を作るときに使えます"
            "(書き出し済みの素材には題が入っていないため)。\n"
            "  題を指定して作る場合は note2slides-thumb を使ってください。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not args.audio:
        print(
            "--slides だけでは音声が入りません。--audio でナレーション音声の場所も"
            "指定してください。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    out_path = args.output or _default_output(args.slides)
    reporter = _Reporter(args.quiet)
    try:
        sources = video_mod.collect_sources(args.slides, args.audio)
        reporter.stage(f"{len(sources)} 枚の素材から動画を書き出しています: {out_path}")
        result = video_mod.compose_video(
            sources,
            out_path,
            options=options,
            ffmpeg_path=args.ffmpeg,
            force=args.force,
            keep_work=args.keep_work,
            timeout=args.timeout,
            credit=_credit_of(args.audio),
            source_name=os.path.basename(os.path.abspath(args.slides)),
            slides_dir=args.slides,
            audio_dir=args.audio,
            on_progress=reporter.encode,
        )
    except ffmpeg_mod.FfmpegNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_TOOL
    except OutputExistsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_EXISTS
    except (VideoError, ffmpeg_mod.FfmpegError) as exc:
        print(f"動画を生成できませんでした: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _report(result, options, args.quiet)
    return EXIT_OK


def _default_output(slides_dir: str) -> str:
    """`build/sample_slides` から `build/sample.mp4` を決める。"""
    path = os.path.abspath(slides_dir).rstrip(os.sep)
    for suffix in ("_slides", "_images"):
        if path.endswith(suffix):
            return path[: -len(suffix)] + video_mod.VIDEO_SUFFIX
    return path + video_mod.VIDEO_SUFFIX


def _credit_of(audio_dir: str) -> str:
    """音声の一覧から、公開時に必要なクレジット表記を拾う。"""
    path = os.path.join(audio_dir, audio_mod.MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("credit") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


# ---------------------------------------------------------------------------
# 指定の組み立て
# ---------------------------------------------------------------------------


def _video_options(args) -> VideoOptions:
    return VideoOptions(
        width=args.width,
        height=args.height,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        background=_color(args.background),
        min_duration=args.min_duration,
        captions=args.captions,
        caption_style=captions_mod.CaptionStyle(line_chars=args.caption_chars),
        chapters=not args.no_chapters,
    )


def _audio_options(args, options: VideoOptions) -> AudioOptions:
    dictionary = load_dictionary(args.dict) if args.dict else {}
    return AudioOptions(
        engine=args.engine,
        voice=args.voice,
        speed=args.speed,
        sample_rate=options.sample_rate,
        reading=ReadingStyle(dictionary=dictionary),
        loudness=None if args.no_loudness else audio_mod.DEFAULT_LOUDNESS_LUFS,
        silent_duration=args.silence,
    )


def _color(value: str) -> Tuple[int, int, int]:
    """`#ffffff` や `ffffff` を (r, g, b) にする。"""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"色は #RRGGBB の形で指定してください: {value}")
    try:
        red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError as exc:
        raise ValueError(f"色は #RRGGBB の形で指定してください: {value}") from exc
    return red, green, blue


# ---------------------------------------------------------------------------
# 進捗と結果の表示
# ---------------------------------------------------------------------------


class _Reporter:
    """工程ごとの進み具合を表示する。

    動画の書き出しは資料の長さに比例して数分かかる。何も出ないまま待たせない
    よう、10% ごとに「何秒目まで書けたか」を出す。
    """

    def __init__(self, quiet: bool, step: int = 10) -> None:
        self.quiet = quiet
        self.step = step
        self._shown = -1

    def stage(self, message: str) -> None:
        if not self.quiet:
            print(message)

    def slide(self, kind: str, index: int) -> None:
        if not self.quiet:
            print(f"  {index:>3}: {'画像' if kind == 'image' else '音声'}")

    def encode(self, seconds: float, total: float) -> None:
        if self.quiet or total <= 0:
            return
        percent = min(100, int(seconds / total * 100))
        if percent // self.step > self._shown:
            self._shown = percent // self.step
            print(f"  {percent:>3}% ({timestamp(seconds)} / {timestamp(total)})")


def _report(result: VideoResult, options: VideoOptions, quiet: bool) -> None:
    for warning in result.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    if quiet:
        return

    print(f"動画を書き出しました: {result.path}")
    print(f"  長さ: {timestamp(result.duration)} / スライド {result.count} 枚")
    print(
        f"  画面: {result.width}x{result.height} / {result.fps}fps / "
        f"H.264 (crf {options.crf}) / 16:9"
    )
    print(
        f"  音声: AAC {options.audio_bitrate} / {options.sample_rate}Hz / "
        f"{'モノラル' if options.channels == 1 else 'ステレオ'}"
    )
    print(f"  ファイルの大きさ: {_size(result.size_bytes)}")
    if result.manifest_path:
        print(f"  一覧: {os.path.basename(result.manifest_path)}(何秒目にどのスライドが出るか)")
    for path in result.caption_paths:
        print(f"  字幕: {os.path.basename(path)}({len(result.cues)} 個。投稿時に添える)")
    if result.chapters_path:
        print(
            f"  章立て: {os.path.basename(result.chapters_path)}"
            f"({len(result.chapters)} 章。概要欄の先頭に貼る)"
        )
    if result.slides_dir:
        print(f"  素材: {result.slides_dir}")
    if result.audio_dir:
        print(f"        {result.audio_dir}")
    if result.workdir:
        print(f"  作業ファイル: {result.workdir}")
    if result.credit:
        print(f"  公開する動画には次の表示が必要です: {result.credit}")


def _size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


# ---------------------------------------------------------------------------
# 環境の確認
# ---------------------------------------------------------------------------


def check_tools(args) -> int:
    """動画の生成に必要な外部ツールの状態をまとめて表示する。"""
    ok = _check_ffmpeg(args.ffmpeg)

    print()
    if images_cli.check_tools(args.soffice) != images_cli.EXIT_OK:
        ok = False

    print()
    if (
        audio_cli.check_tools(
            args.engine,
            args.powershell,
            "ja",
            audio_cli.voicevox_options_from(args),
        )
        != audio_cli.EXIT_OK
    ):
        ok = False

    return EXIT_OK if ok else EXIT_NO_TOOL


def _check_ffmpeg(explicit: Optional[str]) -> bool:
    found = ffmpeg_mod.find_ffmpeg(explicit)
    if not found:
        print("ffmpeg: 見つかりません", file=sys.stderr)
        print(f"  {ffmpeg_mod.INSTALL_HINT}", file=sys.stderr)
        return False

    print(f"ffmpeg: {ffmpeg_mod.describe(found)}")
    print(f"  バージョン: {ffmpeg_mod.get_version(found) or '取得できませんでした'}")
    ok = True
    for encoder, purpose in ((video_mod.VIDEO_CODEC, "映像"), (video_mod.AUDIO_CODEC, "音声")):
        if ffmpeg_mod.has_encoder(found, encoder):
            print(f"  {encoder}: 使えます({purpose})")
        else:
            ok = False
            print(
                f"  {encoder}: この ffmpeg では使えません({purpose})。"
                "符号化器を含むビルドを使ってください。",
                file=sys.stderr,
            )
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
