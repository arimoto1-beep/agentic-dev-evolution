"""スライド画像とナレーション音声を、1 本の動画(MP4)にまとめる。

    slide_001.png + narration_001.wav
    slide_002.png + narration_002.wav   --> sample.mp4
    ...

スライドは「そのスライドのナレーションが終わるまで」映す。動画では、音声と
映像の長さが少しでも食い違うと、ずれが後ろのスライドへ積み重なり、最後には
別のスライドを読み上げているように見える。そうならないよう、長さの決め方を
1 か所(`build_timeline`)に集約し、次の順で決めている。

    1. 1 枚を映す長さは、その音声の長さ以上にする(切り上げるだけで、縮めない)
    2. 長さをコマ単位に切り上げる(動画は 1/fps より細かい時刻を表せない)
    3. 音声側にも無音を足して、2 で決めた長さちょうどにそろえる

こうすると各スライドの開始時刻が映像と音声で必ず一致するため、ナレーションの
途中でスライドが変わることも、音声が途中で切れることもない。実際に何秒目に
どのスライドが出るかは `<名前>_video.json` に書き出す(人が確認するときに使う)。

書き出しは ffmpeg に任せる(`ffmpeg.py`)。動画は H.264 / 音声は AAC の MP4 で、
一般的なプレイヤーと YouTube がそのまま扱える形式にそろえる。画面はつねに
16:9 で、元のスライドが 16:9 でない場合だけ背景色の余白を足す。

ffmpeg には「画像 1 枚 = 入力 1 つ」として渡し、何コマ映すかを整数で指定する
(`_write_filter_script`)。画像と表示時間の並び(concat デマルチプレクサ)を
渡す方法もあるが、表示時間が画像読み込み側の刻み(既定 1/25 秒)に丸められ、
そのずれがスライドをまたいで積み重なる。コマ数で指定すれば丸めが起きない。
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import audio as audio_mod
from . import captions as captions_mod
from . import ffmpeg as ffmpeg_mod
from . import slide_images as images_mod
from . import waveform as wave_mod
from .proc import format_command

MANIFEST_SUFFIX = "_video.json"
VIDEO_SUFFIX = ".mp4"

#: 既定の書き出し条件。eラーニング向けの静止画中心の動画を想定している。
DEFAULT_FPS = 30
DEFAULT_CRF = 20  # 小さいほど高画質・大きいファイル。文字の多い画面は 20 前後で十分。
DEFAULT_PRESET = "medium"
DEFAULT_AUDIO_BITRATE = "192k"
#: 1 枚を最低これだけは映す(ナレーションが極端に短いスライド向け)。
DEFAULT_MIN_DURATION = 1.0

#: 動画の符号化。再生できる環境がもっとも広い組み合わせにそろえる。
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"


class VideoError(RuntimeError):
    """動画の書き出しに失敗した場合。"""


class OutputExistsError(VideoError):
    """出力先に前回の動画が残っている場合(--force で上書きできる)。"""


@dataclass
class VideoOptions:
    """動画の出力条件。既定は 1920x1080 / 30fps の MP4。"""

    width: int = 1920
    height: Optional[int] = None  # 未指定なら width から 16:9 で決める
    fps: int = DEFAULT_FPS
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE
    sample_rate: int = audio_mod.DEFAULT_SAMPLE_RATE
    channels: int = 2  # 合成音声はモノラルだが、左右どちらでも同じに聞こえるようにする
    background: Tuple[int, int, int] = (255, 255, 255)
    min_duration: float = DEFAULT_MIN_DURATION
    #: 一緒に書き出す字幕の形式(`captions.FORMATS`)。`none` で作らない。
    captions: str = captions_mod.FORMAT_SRT
    caption_style: captions_mod.CaptionStyle = field(
        default_factory=captions_mod.CaptionStyle
    )
    chapters: bool = True  # 概要欄に貼る章立てを書き出すか

    def resolved_height(self) -> int:
        if self.height:
            return self.height
        return round(self.width * 9 / 16)

    def size(self) -> Tuple[int, int]:
        return self.width, self.resolved_height()

    def frame_seconds(self) -> float:
        return 1.0 / self.fps

    def validate(self) -> None:
        width, height = self.size()
        if width < 16 or height < 16:
            raise VideoError(f"画面の大きさが小さすぎます: {width}x{height}")
        # H.264 は偶数の画素数を前提にする(yuv420p は色差を 2x2 でまとめるため)。
        if width % 2 or height % 2:
            raise VideoError(f"画面の大きさは偶数にしてください: {width}x{height}")
        if not 1 <= self.fps <= 120:
            raise VideoError(f"フレームレートは 1〜120 で指定してください: {self.fps}")
        if not 0 <= self.crf <= 51:
            raise VideoError(f"画質(crf)は 0〜51 で指定してください: {self.crf}")
        if self.min_duration < 0:
            raise VideoError("1 枚を映す最短の長さは 0 以上にしてください")
        if self.sample_rate < 8000:
            raise VideoError(f"標本化周波数が小さすぎます: {self.sample_rate}")
        if self.channels not in (1, 2):
            raise VideoError(f"音声のチャンネル数は 1 か 2 にしてください: {self.channels}")
        if self.captions not in captions_mod.FORMATS:
            known = " / ".join(captions_mod.FORMATS)
            raise VideoError(f"字幕の形式は {known} から選んでください: {self.captions}")
        try:
            self.caption_style.validate()
        except captions_mod.CaptionError as exc:
            raise VideoError(str(exc)) from exc

    def caption_formats(self) -> Tuple[str, ...]:
        """書き出す字幕の形式。"""
        if self.captions == captions_mod.FORMAT_NONE:
            return ()
        if self.captions == captions_mod.FORMAT_BOTH:
            return (captions_mod.FORMAT_SRT, captions_mod.FORMAT_VTT)
        return (self.captions,)

    def aspect_warning(self) -> Optional[str]:
        width, height = self.size()
        if abs(width / height - 16 / 9) < 0.01:
            return None
        return (
            f"画面の縦横比が 16:9 ではありません({width}x{height})。"
            "YouTube の通常動画は 16:9 を前提にしています。"
        )


@dataclass(frozen=True)
class SlideSource:
    """1 枚のスライドの素材。index はスライド番号(1 起点)。"""

    index: int
    image: str
    audio: Optional[str] = None  # 無ければ最短の長さだけ無音で映す


@dataclass(frozen=True)
class Segment:
    """動画の中での 1 枚の位置。"""

    index: int
    image: str
    audio: Optional[str]
    start: float
    duration: float
    narration: float  # 音声そのものの長さ(duration はこれ以上になる)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "image": os.path.basename(self.image),
            "audio": os.path.basename(self.audio) if self.audio else None,
            "start": round(self.start, 3),
            "at": timestamp(self.start),
            "duration": round(self.duration, 3),
            "narration": round(self.narration, 3),
        }


@dataclass
class VideoResult:
    path: str = ""
    segments: List[Segment] = field(default_factory=list)
    width: int = 0
    height: int = 0
    fps: int = DEFAULT_FPS
    duration: float = 0.0  # 組み立てた時点で意図した長さ
    encoded_duration: float = 0.0  # ffmpeg が実際に書き出した長さ
    size_bytes: int = 0
    slides_dir: Optional[str] = None  # 動画のもとにしたスライド画像の場所
    audio_dir: Optional[str] = None  # 同じくナレーション音声の場所
    command: List[str] = field(default_factory=list)
    credit: str = ""  # 公開時に表示が必要なクレジット表記
    cues: List[captions_mod.Cue] = field(default_factory=list)  # 字幕
    chapters: List[captions_mod.Chapter] = field(default_factory=list)  # 章立て
    caption_paths: List[str] = field(default_factory=list)  # 書き出した字幕ファイル
    chapters_path: Optional[str] = None
    manifest_path: Optional[str] = None
    workdir: Optional[str] = None  # 調査用に残した作業ディレクトリ
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.segments)


# ---------------------------------------------------------------------------
# 素材を集める
# ---------------------------------------------------------------------------


def collect_sources(slides_dir: str, audio_dir: Optional[str] = None) -> List[SlideSource]:
    """スライド画像とナレーション音声の一覧を、スライド番号で対応付ける。

    それぞれの工程が書き出した一覧(`slides.json` / `narration.json`)を読む。
    無い場合は連番のファイル名から拾う(手で並べた素材も使えるようにする)。
    """
    images = _list_images(slides_dir)
    if not images:
        raise VideoError(
            f"スライド画像が見つかりません: {slides_dir}\n"
            "note2slides-images でスライド画像を書き出してから指定してください。"
        )
    if audio_dir is None:
        return [SlideSource(index, path) for index, path in sorted(images.items())]

    clips = _list_audio(audio_dir)
    if not clips:
        raise VideoError(
            f"ナレーション音声が見つかりません: {audio_dir}\n"
            "note2slides-audio でナレーション音声を書き出してから指定してください。"
        )
    if set(images) != set(clips):
        raise VideoError(_mismatch_message(slides_dir, images, audio_dir, clips))
    return [SlideSource(index, images[index], clips[index]) for index in sorted(images)]


def _mismatch_message(
    slides_dir: str, images: Dict[int, str], audio_dir: str, clips: Dict[int, str]
) -> str:
    """対応が取れないときに、どの番号が足りないかまで示す。

    ここを黙って進めると、別のスライドを読み上げる動画ができあがってしまう。
    """
    lines = [
        "スライド画像とナレーション音声の対応が取れません。",
        f"  スライド画像: {len(images)} 枚 ({slides_dir})",
        f"  ナレーション音声: {len(clips)} 本 ({audio_dir})",
    ]
    missing_audio = sorted(set(images) - set(clips))
    missing_image = sorted(set(clips) - set(images))
    if missing_audio:
        lines.append(f"  音声が無いスライド番号: {_numbers(missing_audio)}")
    if missing_image:
        lines.append(f"  画像が無いスライド番号: {_numbers(missing_image)}")
    lines.append("同じ資料から書き出した画像と音声を指定してください。")
    return "\n".join(lines)


def _numbers(values: Sequence[int], limit: int = 10) -> str:
    shown = ", ".join(str(v) for v in values[:limit])
    return shown if len(values) <= limit else f"{shown} ほか {len(values) - limit} 件"


def _list_images(slides_dir: str) -> Dict[int, str]:
    if not os.path.isdir(slides_dir):
        raise VideoError(f"スライド画像のディレクトリが見つかりません: {slides_dir}")
    listed = _from_manifest(
        os.path.join(slides_dir, images_mod.MANIFEST_NAME), "slides", slides_dir
    )
    if listed is not None:
        return listed
    suffixes = tuple(suffix for _, suffix in images_mod.FORMATS.values())
    return _from_filenames(slides_dir, suffixes)


def _list_audio(audio_dir: str) -> Dict[int, str]:
    if not os.path.isdir(audio_dir):
        raise VideoError(f"ナレーション音声のディレクトリが見つかりません: {audio_dir}")
    listed = _from_manifest(
        os.path.join(audio_dir, audio_mod.MANIFEST_NAME), "clips", audio_dir
    )
    if listed is not None:
        return listed
    return _from_filenames(audio_dir, (".wav",))


def _from_manifest(path: str, key: str, directory: str) -> Optional[Dict[int, str]]:
    """一覧ファイルから「スライド番号 -> ファイル」を読む。無ければ None。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoError(f"一覧ファイルを読み取れませんでした: {path}\n{exc}") from exc

    entries = data.get(key) if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return None

    found: Dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "index" not in entry or "file" not in entry:
            raise VideoError(f"一覧ファイルの中身が読み取れません: {path}")
        target = os.path.join(directory, str(entry["file"]))
        if not os.path.isfile(target):
            raise VideoError(
                f"一覧に載っているファイルがありません: {target}\n"
                f"({os.path.basename(path)} を書き出したあとに消された可能性があります)"
            )
        found[int(entry["index"])] = os.path.abspath(target)
    return found


def _from_filenames(directory: str, suffixes: Sequence[str]) -> Dict[int, str]:
    """`名前_001.png` のような連番から番号を拾う。"""
    pattern = re.compile(r"^(.*?)(\d+)(" + "|".join(re.escape(s) for s in suffixes) + r")$")
    found: Dict[int, str] = {}
    for name in sorted(os.listdir(directory)):
        matched = pattern.match(name)
        if matched:
            found[int(matched.group(2))] = os.path.abspath(os.path.join(directory, name))
    return found


# ---------------------------------------------------------------------------
# 時間の割り当て
# ---------------------------------------------------------------------------


def build_timeline(sources: Sequence[SlideSource], options: VideoOptions) -> List[Segment]:
    """各スライドを何秒目から何秒間映すかを決める。

    音声より短くならないように切り上げるだけで、音声を縮めることはしない。
    """
    segments: List[Segment] = []
    start = 0.0
    for source in sorted(sources, key=lambda s: s.index):
        narration = _narration_duration(source)
        duration = align_to_frames(max(narration, options.min_duration), options.fps)
        segments.append(
            Segment(
                index=source.index,
                image=source.image,
                audio=source.audio,
                start=start,
                duration=duration,
                narration=narration,
            )
        )
        start += duration
    return segments


def align_to_frames(seconds: float, fps: int) -> float:
    """コマ単位に切り上げる(動画は 1/fps より細かい時刻を表せない)。

    切り上げなので、割り当てる長さが音声より短くなることはない(足すのは
    1 コマ分に満たない無音で、聞いて分かる長さではない)。1 マイクロ秒未満の
    端数は、浮動小数の誤差で 1 コマ増えるのを避けるため無視する。
    """
    return frames_for(seconds, fps) / fps


def frames_for(seconds: float, fps: int) -> int:
    """その長さを映すのに要るコマ数(1 以上)。"""
    return max(1, math.ceil(seconds * fps - 1e-6))


def _narration_duration(source: SlideSource) -> float:
    if not source.audio:
        return 0.0
    if not os.path.isfile(source.audio):
        raise VideoError(f"ナレーション音声が見つかりません: {source.audio}")
    try:
        return wave_mod.probe_duration(source.audio)
    except wave_mod.WaveformError as exc:
        raise VideoError(str(exc)) from exc


def timestamp(seconds: float) -> str:
    """人が動画を確認するときに使える形(`0:01:23.4`)にする。"""
    total = max(0.0, seconds)
    hours = int(total // 3600)
    minutes = int(total // 60) % 60
    return f"{hours}:{minutes:02d}:{total % 60:04.1f}"


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def compose_video(
    sources: Sequence[SlideSource],
    out_path: str,
    options: Optional[VideoOptions] = None,
    ffmpeg_path: Optional[str] = None,
    force: bool = False,
    keep_work: bool = False,
    timeout: float = 1800,
    credit: str = "",
    source_name: str = "",
    slides_dir: Optional[str] = None,
    audio_dir: Optional[str] = None,
    warnings: Sequence[str] = (),
    on_progress: Optional[Callable[[float, float], None]] = None,
) -> VideoResult:
    """素材から動画を書き出す。返り値には各スライドの開始時刻が入る。

    `warnings` には、前の工程(画像・音声)で出た警告を渡せる。人が確認する
    ときに 1 か所(`<名前>_video.json`)を見れば済むようにするため。
    """
    options = options or VideoOptions()
    options.validate()
    if not sources:
        raise VideoError("スライドが 1 枚もありません。")

    out_path = os.path.abspath(out_path)
    if os.path.splitext(out_path)[1].lower() != VIDEO_SUFFIX:
        raise VideoError(
            f"出力は {VIDEO_SUFFIX} にしてください: {out_path}\n"
            "一般的なプレイヤーと YouTube がそのまま扱える形式として MP4 を使います。"
        )
    if os.path.exists(out_path) and not force:
        raise OutputExistsError(
            f"出力先に既に動画があります: {out_path}\n上書きする場合は --force を付けてください。"
        )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    executable = ffmpeg_mod.require_ffmpeg(ffmpeg_path)
    result = VideoResult(
        path=out_path,
        credit=credit,
        slides_dir=os.path.abspath(slides_dir) if slides_dir else None,
        audio_dir=os.path.abspath(audio_dir) if audio_dir else None,
        warnings=list(warnings),
    )
    result.width, result.height = options.size()
    result.fps = options.fps

    aspect = options.aspect_warning()
    if aspect:
        result.warnings.append(aspect)

    result.segments = build_timeline(sources, options)
    result.duration = sum(segment.duration for segment in result.segments)
    result.warnings.extend(_check_images(result.segments, options))

    workdir = tempfile.mkdtemp(prefix="note2slides-video-")
    try:
        script_path = _write_filter_script(result.segments, workdir, options)
        track_path = _write_audio_track(result.segments, workdir, options, result)
        result.command = build_command(
            executable, result.segments, script_path, track_path, out_path, options
        )
        _check_command_length(result.command)

        total = result.duration

        def report(seconds: float) -> None:
            if on_progress:
                on_progress(seconds, total)

        try:
            encoded = ffmpeg_mod.run(result.command, timeout=timeout, on_progress=report)
        except ffmpeg_mod.FfmpegError:
            # 途中まで書けたファイルは消す。完成したものと見分けが付かないため。
            if os.path.exists(out_path):
                os.remove(out_path)
            result.workdir = workdir
            keep_work = True
            raise
        result.encoded_duration = _encoded_duration(encoded, options)
    finally:
        if keep_work:
            result.workdir = workdir
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise VideoError(f"動画が書き出されていません: {out_path}")
    result.size_bytes = os.path.getsize(out_path)

    # 意図した長さと実際の長さが食い違っていたら、どこかで音声か映像が落ちて
    # いる。人が全編を見る前に気付けるよう、2 コマを超える差は警告にする。
    gap = abs(result.encoded_duration - result.duration)
    if result.encoded_duration and gap > 2.0 / options.fps:
        result.warnings.append(
            f"書き出した動画の長さ({result.encoded_duration:.2f} 秒)が、"
            f"割り当てた長さの合計({result.duration:.2f} 秒)と一致しません。"
            "ナレーションが途中で切れていないか確認してください。"
        )

    _write_captions(result, options)
    result.manifest_path = _write_manifest(result, options, source_name)
    return result


def _write_captions(result: VideoResult, options: VideoOptions) -> None:
    """字幕と章立てを、動画と同じ場所に書き出す。

    どちらも動画そのものには焼き込まない。YouTube では字幕を別ファイルで
    渡すほうが、視聴者が表示を切り替えられて、あとから直すこともできる。

    材料が無いとき(手で並べた素材など)は作らない。動画はもう書き出せている
    ので、ここでの取りこぼしで失敗にはしない。
    """
    base = os.path.splitext(result.path)[0]
    # 前回の字幕が残っていると、作り直した動画と食い違ったまま投稿してしまう。
    for suffix in (captions_mod.SRT_SUFFIX, captions_mod.VTT_SUFFIX, captions_mod.CHAPTERS_SUFFIX):
        if os.path.isfile(base + suffix):
            os.remove(base + suffix)

    formats = options.caption_formats()
    if not formats and not options.chapters:
        return

    readings, warnings = captions_mod.load_captions(result.audio_dir)
    result.warnings.extend(warnings)
    if not readings:
        return

    slides = [
        replace(
            readings[segment.index],
            start=segment.start,
            duration=segment.narration or readings[segment.index].duration,
        )
        for segment in result.segments
        if segment.index in readings
    ]

    if formats:
        result.cues = captions_mod.build_cues(slides, options.caption_style)
        if result.cues:
            for fmt in formats:
                suffix = (
                    captions_mod.VTT_SUFFIX
                    if fmt == captions_mod.FORMAT_VTT
                    else captions_mod.SRT_SUFFIX
                )
                result.caption_paths.append(
                    captions_mod.write_captions(result.cues, base + suffix, fmt)
                )

    if options.chapters:
        result.chapters = captions_mod.build_chapters(slides, result.duration)
        if result.chapters:
            result.chapters_path = captions_mod.write_chapters(
                result.chapters, base + captions_mod.CHAPTERS_SUFFIX
            )
            if len(result.chapters) < captions_mod.MIN_CHAPTERS:
                result.warnings.append(
                    f"章立てが {len(result.chapters)} 個しかありません。"
                    f"YouTube は {captions_mod.MIN_CHAPTERS} 個以上ないと目次を表示しません。"
                )


def _encoded_duration(report: ffmpeg_mod.RunReport, options: VideoOptions) -> float:
    """ffmpeg が実際に書き出した長さ(秒)。

    進捗の `out_time` ではなくコマ数から求める。`out_time` は最後に書き出した
    パケットの復号時刻なので、符号化が先読みする分(既定の preset では 2 コマ)
    だけ手前で止まって見え、長さの食い違いとして誤検出される。コマ数は
    書き出した枚数そのものなので、この差が出ない。
    """
    if report.frames:
        return report.frames / options.fps
    return report.out_time


def build_command(
    executable: str,
    segments: Sequence[Segment],
    script_path: str,
    track_path: str,
    out_path: str,
    options: VideoOptions,
) -> List[str]:
    """ffmpeg のコマンドを組み立てる。

    映像はスライド画像を 1 枚ずつ入力として渡し(何コマ映すかは
    `_write_filter_script` が指定する)、音声は 1 本につないだ WAV を渡す。
    どちらも長さを合わせてあるので、ここでは切り詰めや引き伸ばしをしない
    (`-shortest` を使わないのはそのため)。
    """
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    for segment in segments:
        # 入力側のコマ数を指定しておかないと、つなぎ目の時刻がずれる。
        command += ["-framerate", str(options.fps), "-i", segment.image]
    command += [
        "-i",
        track_path,
        "-filter_complex_script",
        script_path,
        "-map",
        "[v]",
        "-map",
        f"{len(segments)}:a:0",
        "-r",
        str(options.fps),
        "-c:v",
        VIDEO_CODEC,
        "-preset",
        options.preset,
        "-crf",
        str(options.crf),
        "-pix_fmt",
        "yuv420p",  # これ以外だと再生できない機器がある
        "-g",
        str(options.fps * 2),  # 2 秒ごとにキーフレーム(頭出しと配信に効く)
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        options.audio_bitrate,
        "-ar",
        str(options.sample_rate),
        "-ac",
        str(options.channels),
        "-movflags",
        "+faststart",  # 再生開始に必要な情報を先頭へ置く(web 再生が速くなる)
        out_path,
    ]
    return command


#: Windows がコマンド行に渡せる長さの上限(32767 文字)に対する余裕を見た値。
_COMMAND_LIMIT = 30000


def _check_command_length(command: Sequence[str]) -> None:
    """コマンドが長すぎないか先に確かめる。

    スライド 1 枚が入力 1 つなので、枚数が多いとコマンドが長くなる。Windows は
    長すぎるコマンドを起動できず、そのままだと「ファイル名が長すぎます」という
    分かりにくい失敗になる。何が起きたのかを先に伝える。
    """
    if os.name != "nt":
        return
    length = len(format_command(list(command)))
    if length > _COMMAND_LIMIT:
        raise VideoError(
            f"スライドの枚数が多く、ffmpeg に渡すコマンドが長すぎます({length} 文字)。\n"
            "資料を分けて動画にするか、素材を浅い階層(短いパス)に置いてから"
            "やり直してください。"
        )


def _check_images(segments: Sequence[Segment], options: VideoOptions) -> List[str]:
    """画像が揃っているか、大きさが同じかを先に確かめる。

    途中で大きさが変わると ffmpeg は書き出しの最中に止まる。どの画像が
    食い違っているのかを、始める前に伝える。
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 依存が入っていない環境向け
        raise VideoError(
            "Pillow が見つかりません。`pip install -e .` で依存を入れてください。"
        ) from exc

    sizes: Dict[Tuple[int, int], List[int]] = {}
    for segment in segments:
        if not os.path.isfile(segment.image):
            raise VideoError(f"スライド画像が見つかりません: {segment.image}")
        try:
            with Image.open(segment.image) as image:
                size = image.size
        except OSError as exc:
            raise VideoError(
                f"スライド画像を読み取れませんでした: {segment.image}\n{exc}"
            ) from exc
        sizes.setdefault(size, []).append(segment.index)

    if len(sizes) > 1:
        detail = " / ".join(
            f"{w}x{h}(スライド {_numbers(indexes, 5)})" for (w, h), indexes in sizes.items()
        )
        raise VideoError(
            f"スライド画像の大きさが揃っていません: {detail}\n"
            "同じ条件で書き出した画像を使ってください(note2slides-images は揃えて出します)。"
        )

    (source_size,) = sizes
    if source_size != options.size():
        return [
            f"スライド画像({source_size[0]}x{source_size[1]})を "
            f"{options.width}x{options.resolved_height()} に変換して書き出します。"
            "文字をはっきり出すには、同じ大きさで画像を書き出してください。"
        ]
    return []


def _write_filter_script(
    segments: Sequence[Segment], workdir: str, options: VideoOptions
) -> str:
    """スライドを何コマ映して、どうつなぐかを ffmpeg に渡す形で書き出す。

    1 枚ずつ「同じ絵を N コマに増やす(loop)」を書き、すべてをつないで
    (concat)から、時刻を通し番号で振り直し(setpts)、画面の大きさをそろえる。
    コマ数は整数なので、指定した長さがそのまま出る。

    時刻の振り直しをつないだ後に 1 回だけ行うのは、つなぎ目の時刻を ffmpeg に
    計算させないため。1 枚ずつ時刻を振ると、つなぎ目ごとに端数の丸めが入り、
    枚数と長さの組み合わせによっては最後の 1〜2 コマが落ちる。つないだ後に
    通し番号から振り直せば、コマの間隔は必ず 1/fps になる。

    コマンドではなくファイルに書くのは、枚数が増えても実行できるようにする
    ため(Windows のコマンド行の長さには上限がある)。失敗したときは作業
    ディレクトリごと残すので、このファイルを見れば何を指示したか分かる。
    """
    width, height = options.size()
    red, green, blue = options.background
    lines = []
    for position, segment in enumerate(segments):
        frames = frames_for(segment.duration, options.fps)
        lines.append(f"[{position}:v]loop=loop={frames - 1}:size=1:start=0[v{position}]")
    lines.append(
        "".join(f"[v{position}]" for position in range(len(segments)))
        + f"concat=n={len(segments)}:v=1:a=0,"
        # 通し番号から時刻を振り直す(コマの間隔をきっちり 1/fps にする)。
        + f"setpts=N/{options.fps}/TB,"
        # 画面の大きさをそろえる。引き伸ばさず、足りない分は余白にする。
        + f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        + f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:"
        + f"color=0x{red:02x}{green:02x}{blue:02x},"
        # 画素を正方形にして、16:9 が 16:9 のまま表示されるようにする。
        + "setsar=1[v]"
    )

    path = os.path.join(workdir, "filters.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(";\n".join(lines) + "\n")
    return path


def _write_audio_track(
    segments: Sequence[Segment],
    workdir: str,
    options: VideoOptions,
    result: VideoResult,
) -> str:
    """全スライドの音声を、割り当てた長さちょうどにそろえて 1 本につなぐ。

    足りない分は無音を足す。ここで長さをそろえておくと、映像と音声の開始時刻が
    スライドごとに必ず一致する。
    """
    path = os.path.join(workdir, "narration.wav")
    resampled: List[int] = []
    with wave_mod.WavWriter(path, options.sample_rate) as writer:
        for segment in segments:
            samples = int(round(segment.duration * options.sample_rate))
            if segment.audio:
                try:
                    piece = wave_mod.read_wav(segment.audio)
                except wave_mod.WaveformError as exc:
                    raise VideoError(str(exc)) from exc
                if piece.sample_rate != options.sample_rate:
                    piece = wave_mod.resample(piece, options.sample_rate)
                    resampled.append(segment.index)
            else:
                piece = wave_mod.silence(0.0, options.sample_rate)
            writer.append(wave_mod.fit(piece, samples))

    if resampled:
        result.warnings.append(
            f"{len(resampled)} 本の音声を {options.sample_rate}Hz に変換しました"
            f"(スライド {_numbers(resampled, 5)})。"
            "音質を保つには、同じ標本化周波数で音声を書き出してください。"
        )
    return path


def _write_manifest(result: VideoResult, options: VideoOptions, source_name: str) -> str:
    """何秒目にどのスライドが出るかの一覧。人が内容を確認するときに使う。"""
    data = {
        "file": os.path.basename(result.path),
        "source": source_name,
        "duration": round(result.duration, 3),
        "length": timestamp(result.duration),
        "size_bytes": result.size_bytes,
        "video": {
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "codec": VIDEO_CODEC,
            "crf": options.crf,
            "preset": options.preset,
            "pix_fmt": "yuv420p",
        },
        "audio": {
            "codec": AUDIO_CODEC,
            "bitrate": options.audio_bitrate,
            "sample_rate": options.sample_rate,
            "channels": options.channels,
        },
        "credit": result.credit,
        "materials": {"slides": result.slides_dir, "audio": result.audio_dir},
        "captions": [os.path.basename(path) for path in result.caption_paths],
        "chapters": [chapter.to_dict() for chapter in result.chapters],
        "count": result.count,
        "slides": [segment.to_dict() for segment in result.segments],
        "command": format_command(result.command),
        "warnings": result.warnings,
    }
    path = os.path.splitext(result.path)[0] + MANIFEST_SUFFIX
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# 資料からの通し実行
# ---------------------------------------------------------------------------


def export_video(
    source: str,
    out_path: str,
    slides_dir: Optional[str] = None,
    audio_dir: Optional[str] = None,
    options: Optional[VideoOptions] = None,
    image_options: Optional[images_mod.ImageOptions] = None,
    audio_options: Optional[audio_mod.AudioOptions] = None,
    soffice_path: Optional[str] = None,
    ffmpeg_path: Optional[str] = None,
    powershell: Optional[str] = None,
    voicevox_options: Optional[dict] = None,
    force: bool = False,
    keep_work: bool = False,
    slides_timeout: float = 300,
    audio_timeout: float = 1800,
    encode_timeout: float = 1800,
    on_stage: Optional[Callable[[str], None]] = None,
    on_slide: Optional[Callable[[str, int], None]] = None,  # 工程の名前とスライド番号
    on_encode: Optional[Callable[[float, float], None]] = None,
) -> VideoResult:
    """資料(.pptx)から、スライド画像・ナレーション音声を経て動画まで作る。

    途中の画像と音声は残す。読み方や見た目を直したいときは、その工程だけを
    やり直して、この関数を使わずに `compose_video` で組み立て直せる。
    """
    options = options or VideoOptions()
    options.validate()

    stem = os.path.splitext(os.path.abspath(out_path))[0]
    slides_dir = slides_dir or stem + "_slides"
    audio_dir = audio_dir or stem + "_audio"

    # 画像と音声は、動画の条件に合わせて書き出す(あとで変換し直さずに済む)。
    image_options = image_options or images_mod.ImageOptions(
        width=options.width, height=options.resolved_height()
    )
    audio_options = replace(
        audio_options or audio_mod.AudioOptions(), sample_rate=options.sample_rate
    )

    if on_stage:
        on_stage(f"スライド画像を書き出しています: {slides_dir}")
    images = images_mod.export_slide_images(
        source,
        slides_dir,
        options=image_options,
        soffice_path=soffice_path,
        force=force,
        timeout=slides_timeout,
        on_progress=(lambda image: on_slide("image", image.index)) if on_slide else None,
    )

    if on_stage:
        on_stage(f"ナレーション音声を合成しています: {audio_dir}")
    narration = audio_mod.export_narration(
        source,
        audio_dir,
        options=audio_options,
        powershell=powershell,
        voicevox_options=voicevox_options,
        force=force,
        timeout=audio_timeout,
        on_progress=(lambda index: on_slide("audio", index)) if on_slide else None,
    )

    if on_stage:
        on_stage(f"動画を書き出しています: {out_path}")
    sources = [
        SlideSource(image.index, image.path, _clip_path(narration, image.index))
        for image in images.images
    ]
    if len(narration.clips) != len(sources):
        raise VideoError(
            _mismatch_message(
                slides_dir,
                {image.index: image.path for image in images.images},
                audio_dir,
                {clip.index: clip.path for clip in narration.clips},
            )
        )

    return compose_video(
        sources,
        out_path,
        options=options,
        ffmpeg_path=ffmpeg_path,
        force=force,
        keep_work=keep_work,
        timeout=encode_timeout,
        credit=narration.credit,
        source_name=os.path.basename(source),
        slides_dir=slides_dir,
        audio_dir=audio_dir,
        warnings=images.warnings + narration.warnings,
        on_progress=on_encode,
    )


def _clip_path(narration: audio_mod.NarrationResult, index: int) -> Optional[str]:
    for clip in narration.clips:
        if clip.index == index:
            return clip.path
    return None
