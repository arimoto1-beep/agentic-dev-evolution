"""ナレーション原稿を音声ファイル(WAV)に合成する。

    .pptx --(narration)--> 原稿 --(tts)--> narration_001.wav ...

出力はスライド画像と同じ規則で番号を振る。`slide_001.png` に対応する音声は
`narration_001.wav` で、番号だけでスライドと音声の対応が分かる。

    build/audio/
      narration_001.wav
      narration_002.wav
      ...
      narration.json   スライド番号・長さ・読み上げた文章の一覧

読み上げる文章が無いスライドも、番号がずれないように無音の WAV を書き出す。
形式は全ファイルで揃える(動画生成でつなぐときに揃っている必要がある)。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import wave
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import narration as narration_mod
from . import tts as tts_mod
from .narration import NarrationScript, NarrationSegment
from .tts import AudioFormat, SpeechEngine, SpeechJob

MANIFEST_NAME = "narration.json"
SCRIPT_NAME = "script.json"

#: 合成しない(無音にする)場合の既定の形式。
DEFAULT_SAMPLE_RATE = tts_mod.SAPI_SAMPLE_RATE


class AudioExportError(RuntimeError):
    """音声の書き出しに失敗した場合。"""


class OutputExistsError(AudioExportError):
    """出力先に前回の音声が残っている場合(--force で上書きできる)。"""


@dataclass
class AudioOptions:
    """音声の出力条件。既定は日本語の音声で 1 スライド 1 ファイル。"""

    engine: str = tts_mod.ENGINE_AUTO
    voice: Optional[str] = None
    language: str = "ja"
    speed: float = 1.0  # 1.0 が標準の速さ
    volume: int = 100
    sample_rate: Optional[int] = None  # 指定できるのは sapi エンジンのみ
    tail_silence: float = 0.5  # 各スライドの読み上げ後に足す無音(秒)
    silent_duration: float = 2.0  # 読み上げる文章が無いスライドの長さ(秒)
    prefix: str = "narration_"
    digits: int = 3

    def filename(self, index: int) -> str:
        return f"{self.prefix}{index:0{self.digits}d}.wav"

    def validate(self) -> None:
        if self.speed <= 0:
            raise AudioExportError(f"読み上げ速度は 0 より大きくしてください: {self.speed}")
        if not 0 <= self.volume <= 100:
            raise AudioExportError(f"音量は 0〜100 で指定してください: {self.volume}")
        if self.tail_silence < 0 or self.silent_duration < 0:
            raise AudioExportError("無音の長さは 0 以上にしてください")
        if self.sample_rate is not None and self.sample_rate < 8000:
            raise AudioExportError(f"標本化周波数が小さすぎます: {self.sample_rate}")
        if self.digits < 1:
            raise AudioExportError("連番の桁数は 1 以上にしてください")


@dataclass
class NarrationClip:
    """1 枚のスライドに対応する音声。index はスライド番号(1 起点)。"""

    index: int
    path: str
    duration: float
    text: str = ""
    source: str = narration_mod.SOURCE_NONE
    silent: bool = False

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "file": self.filename,
            "duration": round(self.duration, 3),
            "source": self.source,
            "silent": self.silent,
            "text": self.text,
        }


@dataclass
class NarrationResult:
    clips: List[NarrationClip] = field(default_factory=list)
    script: Optional[NarrationScript] = None
    source: str = ""
    engine: str = ""
    voice: str = ""
    audio_format: Optional[AudioFormat] = None
    manifest_path: Optional[str] = None
    script_path: Optional[str] = None
    workdir: Optional[str] = None  # 調査用に残した作業ディレクトリ
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.clips)

    @property
    def total_duration(self) -> float:
        return sum(clip.duration for clip in self.clips)


def export_narration(
    source: str,
    outdir: str,
    options: Optional[AudioOptions] = None,
    engine: Optional[SpeechEngine] = None,
    powershell: Optional[str] = None,
    force: bool = False,
    keep_work: bool = False,
    timeout: float = 600,
    dump_script: Optional[str] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> NarrationResult:
    """資料(.pptx)または原稿(.json)から、スライド 1 枚ごとの音声を書き出す。"""
    options = options or AudioOptions()
    options.validate()

    script = narration_mod.extract_script(source)
    result = NarrationResult(
        script=script,
        source=os.path.abspath(source),
        warnings=list(script.warnings),
    )

    os.makedirs(outdir, exist_ok=True)
    if dump_script:
        os.makedirs(os.path.dirname(os.path.abspath(dump_script)) or ".", exist_ok=True)
        result.script_path = script.write(dump_script)

    existing = _existing_outputs(outdir, options)
    if existing and not force:
        raise OutputExistsError(
            f"出力先に既に音声があります: {outdir}({len(existing)} 件)\n"
            "上書きする場合は --force を付けてください。"
        )

    if any(not segment.is_empty for segment in script.segments):
        engine = engine or tts_mod.select_engine(options.engine, powershell, options.language)
        voice = engine.pick_voice(options.voice, options.language)
        result.engine = engine.name
        result.voice = voice.name
        if options.sample_rate and not engine.honors_sample_rate():
            result.warnings.append(
                f"{engine.name} は出力形式を選べないため、--sample-rate は使いません"
                f"({engine.default_format().describe()} で出力します)。"
            )
        _synthesize(script, outdir, options, engine, voice.name, timeout, keep_work, result, on_progress)
    else:
        result.warnings.append("読み上げる文章がありません。すべて無音として書き出します。")

    _finish_clips(script, outdir, options, result, on_progress)

    stale = sorted(set(existing) - {clip.path for clip in result.clips})
    for path in stale:
        os.remove(path)
    if stale:
        result.warnings.append(f"前回の音声 {len(stale)} 件を削除しました。")

    result.manifest_path = _write_manifest(outdir, options, result)
    return result


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


def _synthesize(
    script: NarrationScript,
    outdir: str,
    options: AudioOptions,
    engine: SpeechEngine,
    voice: str,
    timeout: float,
    keep_work: bool,
    result: NarrationResult,
    on_progress: Optional[Callable[[int], None]],
) -> None:
    jobs = [
        SpeechJob(segment.index, segment.text, os.path.join(outdir, options.filename(segment.index)))
        for segment in script.segments
        if not segment.is_empty
    ]
    # 作業ディレクトリ(読み上げた文章と合成の指示)は、成功したときだけ消す。
    # 失敗したときは残して、同じコマンドを手元で再実行できるようにする。
    workdir = tempfile.mkdtemp(prefix="note2slides-tts-")
    report = engine.synthesize(
        jobs,
        workdir,
        voice=voice,
        speed=options.speed,
        volume=options.volume,
        sample_rate=options.sample_rate,
        timeout=timeout,
        on_done=on_progress,
    )
    if report.voice:
        result.voice = report.voice

    if report.failures:
        result.workdir = workdir
        raise AudioExportError(_failure_message(report, workdir, script))
    if keep_work:
        result.workdir = workdir
    else:
        shutil.rmtree(workdir, ignore_errors=True)


def _failure_message(report, workdir: str, script: NarrationScript) -> str:
    lines = [f"{len(report.failures)} 枚分の音声を合成できませんでした。"]
    titles = {s.index: s.title for s in script.segments}
    for failure in report.failures:
        title = titles.get(failure.index) or ""
        head = f"  スライド {failure.index}"
        if title:
            head += f"（{title}）"
        lines.append(f"{head}: {failure.message}")
    lines.append("")
    lines.append("実行したコマンド:")
    lines.append("  " + tts_mod.describe_command(report.command))
    if report.stderr.strip():
        lines.append("標準エラー出力:")
        lines.append("\n".join("  " + line for line in report.stderr.strip().splitlines()))
    lines.append(f"読み上げた文章と出力先は次の場所に残しています: {workdir}")
    return "\n".join(lines)


def _finish_clips(
    script: NarrationScript,
    outdir: str,
    options: AudioOptions,
    result: NarrationResult,
    on_progress: Optional[Callable[[int], None]],
) -> None:
    """合成結果を整え、無音スライドを補い、形式と長さを確かめる。"""
    formats: List[AudioFormat] = []
    clips: List[NarrationClip] = []
    silent_segments: List[NarrationSegment] = []

    for segment in script.segments:
        if segment.is_empty:
            silent_segments.append(segment)
            continue
        path = os.path.join(outdir, options.filename(segment.index))
        if options.tail_silence:
            _append_silence(path, options.tail_silence)
        audio_format, duration = _wav_info(path)
        formats.append(audio_format)
        clips.append(
            NarrationClip(
                index=segment.index,
                path=os.path.abspath(path),
                duration=duration,
                text=segment.text,
                source=segment.source,
            )
        )

    audio_format = _common_format(formats, options, result)
    result.audio_format = audio_format

    for segment in silent_segments:
        path = os.path.join(outdir, options.filename(segment.index))
        _write_silence(path, options.silent_duration, audio_format)
        clips.append(
            NarrationClip(
                index=segment.index,
                path=os.path.abspath(path),
                duration=options.silent_duration,
                text="",
                source=segment.source,
                silent=True,
            )
        )
        if on_progress:
            on_progress(segment.index)

    result.clips = sorted(clips, key=lambda clip: clip.index)


def _common_format(
    formats: List[AudioFormat], options: AudioOptions, result: NarrationResult
) -> AudioFormat:
    """全ファイルの形式。揃っていない場合は警告して先頭に合わせる。"""
    if not formats:
        return AudioFormat(options.sample_rate or DEFAULT_SAMPLE_RATE)
    if len(set(formats)) > 1:
        described = " / ".join(sorted({f.describe() for f in formats}))
        result.warnings.append(
            f"音声ファイルの形式が揃っていません({described})。"
            "動画生成でつなぐ前に変換が必要です。"
        )
    return formats[0]


# ---------------------------------------------------------------------------
# WAV の読み書き
# ---------------------------------------------------------------------------


def _wav_info(path: str) -> Tuple[AudioFormat, float]:
    """WAV の形式と長さ(秒)を読む。"""
    try:
        with wave.open(path, "rb") as reader:
            params = reader.getparams()
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioExportError(
            f"音声ファイルを読み取れませんでした: {path}\n{type(exc).__name__}: {exc}"
        ) from exc
    if not params.framerate:
        raise AudioExportError(f"音声ファイルの形式が不正です(標本化周波数が 0): {path}")
    audio_format = AudioFormat(params.framerate, params.nchannels, params.sampwidth)
    return audio_format, params.nframes / params.framerate


def _append_silence(path: str, seconds: float) -> None:
    """読み上げの後ろに無音を足す。スライドの切り替わりが詰まらないようにする。"""
    if seconds <= 0:
        return
    try:
        with wave.open(path, "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(params.nframes)
        with wave.open(path, "wb") as writer:
            writer.setparams(params)
            writer.writeframes(frames + _silence_frames(seconds, params.framerate, params.nchannels, params.sampwidth))
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioExportError(
            f"音声ファイルに無音を足せませんでした: {path}\n{type(exc).__name__}: {exc}"
        ) from exc


def _write_silence(path: str, seconds: float, audio_format: AudioFormat) -> None:
    with wave.open(path, "wb") as writer:
        writer.setnchannels(audio_format.channels)
        writer.setsampwidth(audio_format.sample_width)
        writer.setframerate(audio_format.sample_rate)
        writer.writeframes(
            _silence_frames(
                seconds, audio_format.sample_rate, audio_format.channels, audio_format.sample_width
            )
        )


def _silence_frames(seconds: float, framerate: int, channels: int, sample_width: int) -> bytes:
    # 8bit PCM の無音は 0x80(符号なし表現の中央)、それ以外は 0。
    filler = b"\x80" if sample_width == 1 else b"\x00"
    return filler * (int(round(seconds * framerate)) * channels * sample_width)


# ---------------------------------------------------------------------------
# 出力先
# ---------------------------------------------------------------------------


def _existing_outputs(outdir: str, options: AudioOptions) -> List[str]:
    pattern = re.compile(re.escape(options.prefix) + r"\d+\.wav$", re.IGNORECASE)
    return [
        os.path.abspath(os.path.join(outdir, name))
        for name in sorted(os.listdir(outdir))
        if pattern.match(name)
    ]


def _write_manifest(outdir: str, options: AudioOptions, result: NarrationResult) -> str:
    """後続の動画生成が読む一覧。index はスライド番号と一致する。"""
    data = {
        "source": os.path.basename(result.source),
        "engine": result.engine,
        "voice": result.voice,
        "speed": options.speed,
        "format": result.audio_format.to_dict() if result.audio_format else {},
        "count": result.count,
        "total_duration": round(result.total_duration, 3),
        "clips": [clip.to_dict() for clip in result.clips],
        "warnings": result.warnings,
    }
    path = os.path.join(outdir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return os.path.abspath(path)


def ffmpeg_pattern(options: AudioOptions) -> str:
    """ffmpeg の concat などで使うときのファイル名パターン。"""
    return f"{options.prefix}%0{options.digits}d.wav"
