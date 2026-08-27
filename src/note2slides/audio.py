"""ナレーション原稿を音声ファイル(WAV)に合成する。

    .pptx --(narration)--> 原稿 --(reading)--> 読み上げ単位 + 間
                                    --(tts)--> 1 枚分の読み上げ
                               --(waveform)--> narration_001.wav ...

出力はスライド画像と同じ規則で番号を振る。`slide_001.png` に対応する音声は
`narration_001.wav` で、番号だけでスライドと音声の対応が分かる。

    build/audio/
      narration_001.wav
      narration_002.wav
      ...
      narration.json   スライド番号・長さ・読み上げた文章の一覧

文と文の間はこちらで決める。エンジン任せだと間が詰まって早口に聞こえ、
長時間のナレーションに耐えない。間の長さは `reading.py` が決める。

ただし、間ごとに合成を分けて音声をつなぐことはしない。合成エンジンは渡された
文字列を 1 つの発話として扱うため、分けて頼むと区切りの先頭が毎回「文章の
書き出し」の音程になり、文や箇条書きの項目が変わるたびにそこだけ音程と抑揚が
跳ね上がって聞こえる。1 枚分は区切りと間を添えてまとめて渡し、間も読み上げの
中に置いてもらう(`voicevox.py` は無音の拍として、`tts.py` は SSML の
`<break>` として入れる)。ここで足すのは、スライドの前後に置く無音だけ。

つないだあと、全ファイルに同じ音量補正をかける。ファイルごとにそろえると、
静かなスライドだけが持ち上がってスライドごとに音量が動いて聞こえるため、
全体を 1 本の動画として測り、同じ補正値をかける(`waveform.py`)。

読み上げる文章が無いスライドも、番号がずれないように無音の WAV を書き出す。
長さは既定で `AudioOptions.silent_duration` だが、原稿に画面を見せる時間
(`NarrationSegment.hold`)が書かれていればその長さにする。教材シナリオで
「ここは読み上げず、何秒か見せる」と指定した画面がこれにあたる。
形式は全ファイルで揃える(動画生成でつなぐときに揃っている必要がある)。
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import narration as narration_mod
from . import pronunciation as pron_mod
from . import tts as tts_mod
from . import waveform as wave_mod
from .narration import NarrationScript
from .reading import ReadingPlan, ReadingStyle, plan_reading
from .speech import AudioFormat, SpeechJob, SpeechPiece
from .waveform import LoudnessAdjustment, Waveform

MANIFEST_NAME = "narration.json"
SCRIPT_NAME = "script.json"

#: 出力する標本化周波数。動画の音声トラックに合わせる。
DEFAULT_SAMPLE_RATE = 48000
#: 目標の音量(LUFS)。YouTube は約 -14 LUFS を基準に音量をそろえるため、
#: あとから BGM を足す余地を 2dB 残した値にしている。
DEFAULT_LOUDNESS_LUFS = -16.0
#: 音割れを避ける上限(dBFS)。
DEFAULT_PEAK_CEILING_DBFS = -1.5


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
    pitch: float = 0.0  # 声の高さ(voicevox のみ)
    intonation: float = 1.0  # 抑揚の強さ(voicevox のみ)
    volume: int = 100
    sample_rate: int = DEFAULT_SAMPLE_RATE
    reading: ReadingStyle = field(default_factory=ReadingStyle)
    loudness: Optional[float] = DEFAULT_LOUDNESS_LUFS  # None で音量補正をしない
    peak_ceiling: float = DEFAULT_PEAK_CEILING_DBFS
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
        if self.silent_duration < 0:
            raise AudioExportError("無音の長さは 0 以上にしてください")
        if self.sample_rate < 8000:
            raise AudioExportError(f"標本化周波数が小さすぎます: {self.sample_rate}")
        if self.digits < 1:
            raise AudioExportError("連番の桁数は 1 以上にしてください")
        if self.loudness is not None and not -60 <= self.loudness <= 0:
            raise AudioExportError(f"目標の音量は -60〜0 LUFS で指定してください: {self.loudness}")
        try:
            self.reading.validate()
        except ValueError as exc:
            raise AudioExportError(str(exc)) from exc


@dataclass
class NarrationClip:
    """1 枚のスライドに対応する音声。index はスライド番号(1 起点)。"""

    index: int
    path: str
    duration: float
    text: str = ""  # 原稿の文章(元の記事の文言のまま)
    reading: str = ""  # 実際に合成へ渡した文字列
    title: str = ""  # スライドの見出し(章立てに使う)
    source: str = narration_mod.SOURCE_NONE
    silent: bool = False
    utterances: int = 0  # いくつに分けて読み上げたか
    # 読み上げ単位と間、前後の無音。字幕はこれと音声の長さから、どの文章が
    # 何秒目にあたるかを割り出す(`captions.py`)。
    pieces: List[SpeechPiece] = field(default_factory=list)
    lead_silence: float = 0.0
    tail_silence: float = 0.0
    lufs: Optional[float] = None  # このファイルの音量(補正後)
    notes: List[str] = field(default_factory=list)  # 読み上げ用に整形した内容

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def to_dict(self) -> dict:
        data = {
            "index": self.index,
            "file": self.filename,
            "duration": round(self.duration, 3),
            "source": self.source,
            "silent": self.silent,
            "utterances": self.utterances,
            "title": self.title,
            "text": self.text,
            "reading": self.reading,
        }
        if self.pieces:
            data["lead_silence"] = round(self.lead_silence, 3)
            data["tail_silence"] = round(self.tail_silence, 3)
            data["pieces"] = [
                {"text": piece.text, "pause_after": round(piece.pause_after, 3)}
                for piece in self.pieces
            ]
        if self.lufs is not None and self.lufs != -math.inf:
            data["lufs"] = round(self.lufs, 2)
        if self.notes:
            data["notes"] = self.notes
        return data


@dataclass
class NarrationResult:
    clips: List[NarrationClip] = field(default_factory=list)
    script: Optional[NarrationScript] = None
    source: str = ""
    engine: str = ""
    voice: str = ""
    credit: str = ""  # 公開時に表示が必要なクレジット表記
    audio_format: Optional[AudioFormat] = None
    loudness: Optional[LoudnessAdjustment] = None
    manifest_path: Optional[str] = None
    script_path: Optional[str] = None
    workdir: Optional[str] = None  # 調査用に残した作業ディレクトリ
    warnings: List[str] = field(default_factory=list)
    #: 英字がどう読まれたか(読みを聞けるエンジンの場合だけ)。
    pronunciation: Optional[pron_mod.PronunciationReport] = None

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
    engine=None,
    powershell: Optional[str] = None,
    voicevox_options: Optional[dict] = None,
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
    # 最初のスライドだけ、話し始めるまでの無音を長く取る(前にスライドが無く、
    # 動画の再生が始まった直後にあたるため)。
    first = min((s.index for s in script.segments), default=None)
    plans = {
        s.index: plan_reading(
            s.text, options.reading, hold=s.hold, opening=(s.index == first)
        )
        for s in script.segments
    }

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

    owns_engine = engine is None
    try:
        if any(not plan.is_empty for plan in plans.values()):
            if engine is None:
                engine = tts_mod.select_engine(
                    options.engine, powershell, options.language, voicevox_options
                )
            # 合成の前に、英字が何と読まれるかを確かめる。音を作らないので速く、
            # 直す必要があることが「聞く前に」分かる(pronunciation.py)。
            _check_pronunciation(plans, options, engine, result)
            _synthesize(
                script, plans, outdir, options, engine, timeout, keep_work, result, on_progress
            )
        else:
            result.warnings.append("読み上げる文章がありません。すべて無音として書き出します。")
            _assemble(script, plans, {}, outdir, options, result, on_progress)
    finally:
        if owns_engine and engine is not None:
            engine.close()

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


def _check_pronunciation(
    plans: Dict[int, ReadingPlan],
    options: AudioOptions,
    engine,
    result: NarrationResult,
) -> None:
    """英字が 1 文字ずつ読まれていないかを、合成の前に確かめる。

    読みを聞けるのは VOICEVOX だけなので、聞けないエンジンでは何もしない
    (Windows 標準の音声合成には読みを返す API が無い)。読み違いは音を聞くまで
    分からないため、分かる範囲だけでも先に知らせる。
    """
    read = getattr(engine, "read", None)
    pick_style = getattr(engine, "pick_style", None)
    if read is None or pick_style is None:
        return
    try:
        style = pick_style(options.voice)
        report = pron_mod.inspect_readings(
            plans, pron_mod.reading_kana_reader(engine, style), lines=False
        )
    except Exception as exc:  # 読みの確認は本題ではないので、失敗しても合成は続ける
        result.warnings.append(f"読み方を確認できませんでした: {exc}")
        return
    result.pronunciation = report
    result.warnings.extend(report.warnings())


def _synthesize(
    script: NarrationScript,
    plans: Dict[int, ReadingPlan],
    outdir: str,
    options: AudioOptions,
    engine,
    timeout: float,
    keep_work: bool,
    result: NarrationResult,
    on_progress: Optional[Callable[[int], None]],
) -> None:
    """読み上げ単位ごとに合成し、つないで 1 枚分の音声にする。"""
    voice = engine.pick_voice(options.voice, options.language)
    result.engine = engine.name
    result.voice = voice.name
    if options.sample_rate and not engine.honors_sample_rate():
        result.warnings.append(
            f"{engine.name} は出力形式を選べないため、"
            f"{engine.default_format().describe()} で合成してから "
            f"{options.sample_rate}Hz に変換します(音質は元の形式のままです)。"
        )

    workdir = tempfile.mkdtemp(prefix="note2slides-tts-")

    jobs: List[SpeechJob] = []
    owner: Dict[int, int] = {}  # 合成単位の通し番号 -> スライド番号
    parts: Dict[int, str] = {}  # スライド番号 -> 1 枚分の WAV
    for segment in script.segments:
        plan = plans[segment.index]
        if plan.is_empty:
            continue
        job_index = len(jobs) + 1
        out_path = os.path.join(workdir, f"part_{job_index:04d}.wav")
        jobs.append(
            SpeechJob(
                index=job_index,
                # 合成へ渡すのは読み(読み方辞書を当てたあと)。字幕は元の文字を
                # 使うので、ここだけが `to_speak` になる(`reading.Utterance`)。
                pieces=[SpeechPiece(u.to_speak, u.pause_after) for u in plan.utterances],
                out_path=out_path,
                slide=segment.index,
            )
        )
        owner[job_index] = segment.index
        parts[segment.index] = out_path

    def on_done(job_index: int) -> None:
        slide = owner.get(job_index)
        if slide is not None and on_progress:
            on_progress(slide)

    report = engine.synthesize(
        jobs,
        workdir,
        voice=voice.name,
        speed=options.speed,
        volume=options.volume,
        sample_rate=options.sample_rate,
        timeout=timeout,
        on_done=on_done,
        pitch=options.pitch,
        intonation=options.intonation,
    )
    if report.voice:
        result.voice = report.voice
    result.credit = _credit_of(engine, result.voice)
    result.warnings.extend(report.warnings)

    if report.failures:
        result.workdir = workdir
        raise AudioExportError(_failure_message(report, workdir, script, owner))

    _assemble(script, plans, parts, outdir, options, result, on_progress)

    # 作業ディレクトリ(合成した部品と合成の指示)は、成功したときだけ消す。
    # 失敗したときは残して、同じ内容を手元で再実行できるようにする。
    if keep_work:
        result.workdir = workdir
    else:
        shutil.rmtree(workdir, ignore_errors=True)


def _credit_of(engine, voice: str) -> str:
    getter = getattr(engine, "credit_for", None)
    return getter(voice) if callable(getter) else ""


def _failure_message(report, workdir: str, script: NarrationScript, owner: Dict[int, int]) -> str:
    titles = {s.index: s.title for s in script.segments}
    slides = sorted({owner.get(f.index, 0) for f in report.failures})
    lines = [f"{len(slides)} 枚分の音声を合成できませんでした。"]
    for failure in report.failures:
        slide = owner.get(failure.index, 0)
        head = f"  スライド {slide}"
        if titles.get(slide):
            head += f"（{titles[slide]}）"
        lines.append(f"{head}: {failure.message}")
    lines.append("")
    lines.append("実行したコマンド:")
    lines.append("  " + tts_mod.describe_command(report.command))
    if report.stderr.strip():
        lines.append("標準エラー出力:")
        lines.append("\n".join("  " + line for line in report.stderr.strip().splitlines()))
    lines.append(f"読み上げた文章と出力先は次の場所に残しています: {workdir}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


def _assemble(
    script: NarrationScript,
    plans: Dict[int, ReadingPlan],
    parts: Dict[int, str],
    outdir: str,
    options: AudioOptions,
    result: NarrationResult,
    on_progress: Optional[Callable[[int], None]],
) -> None:
    """1 枚分の読み上げに前後の無音を付け、音量をそろえて書き出す。

    parts はスライド番号から合成済み WAV への対応。文と文の間は読み上げの中に
    入っている(そこで切ってつなぐと、つなぎ目だけ音程が変わって聞こえる)ため、
    ここで足すのはスライドの前後の無音だけ。
    """
    rate = options.sample_rate
    spoken: Dict[int, Waveform] = {}

    for segment in script.segments:
        plan = plans[segment.index]
        if plan.is_empty:
            continue
        speech = wave_mod.read_wav(parts[segment.index])
        if speech.sample_rate != rate:
            speech = wave_mod.resample(speech, rate)
        # エンジンが付ける前後の余白は長さがまちまちなので、いったん削って
        # こちらで決めた長さの無音を入れ直す(この余白は、最初と最後の拍を
        # 痩せさせないために付けさせているもの。`voicevox.SYNTH_PADDING`)。
        speech = wave_mod.fade(wave_mod.trim_silence(speech))
        spoken[segment.index] = wave_mod.concat(
            [
                wave_mod.silence(plan.lead_silence, rate),
                speech,
                wave_mod.silence(plan.tail_silence, rate),
            ],
            rate,
        )

    spoken = _adjust_loudness(spoken, options, result)

    clips: List[NarrationClip] = []
    for segment in script.segments:
        plan = plans[segment.index]
        path = os.path.join(outdir, options.filename(segment.index))
        if segment.index in spoken:
            audio = spoken[segment.index]
            wave_mod.write_wav(path, audio)
            clips.append(
                NarrationClip(
                    index=segment.index,
                    path=os.path.abspath(path),
                    duration=audio.duration,
                    text=segment.text,
                    reading=plan.reading,
                    title=segment.title,
                    source=segment.source,
                    utterances=len(plan.utterances),
                    pieces=[SpeechPiece(u.text, u.pause_after) for u in plan.utterances],
                    lead_silence=plan.lead_silence,
                    tail_silence=plan.tail_silence,
                    lufs=wave_mod.loudness_lufs(audio),
                    notes=plan.notes,
                )
            )
        else:
            # 読み上げる文章が無いスライド。原稿に画面を見せる時間(hold)が
            # 書かれていればその長さ、無ければ既定の長さだけ映す。
            duration = segment.hold if segment.hold > 0 else options.silent_duration
            wave_mod.write_wav(path, wave_mod.silence(duration, rate))
            clips.append(
                NarrationClip(
                    index=segment.index,
                    path=os.path.abspath(path),
                    duration=duration,
                    text=segment.text,
                    title=segment.title,
                    source=segment.source,
                    silent=True,
                    notes=plan.notes,
                )
            )
            if on_progress:
                on_progress(segment.index)

    result.clips = sorted(clips, key=lambda clip: clip.index)
    result.audio_format = AudioFormat(rate, 1, wave_mod.SAMPLE_WIDTH)


def _adjust_loudness(
    spoken: Dict[int, Waveform], options: AudioOptions, result: NarrationResult
) -> Dict[int, Waveform]:
    """全ファイルに共通の音量補正をかける。"""
    if options.loudness is None or not spoken:
        return spoken

    indexes = list(spoken)
    adjusted, adjustment = wave_mod.normalize(
        [spoken[index] for index in indexes], options.loudness, options.peak_ceiling
    )
    if adjustment.measured_lufs == -math.inf:
        result.warnings.append("音声に音が入っていないため、音量の調整をしていません。")
        return spoken

    result.loudness = adjustment
    if adjustment.limit_db > 6.0:
        result.warnings.append(
            f"飛び出したピークを最大 {adjustment.limit_db:.1f}dB 抑えました。"
            "抑える量が大きいので、--loudness を下げるか --volume を下げて合成し直すと"
            "自然な音になります。"
        )
    return dict(zip(indexes, adjusted))


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
        "credit": result.credit,
        # 声の指定は、あとから同じ音声を作り直せるようにすべて残す
        # (どの設定で合成したものかは、聞いただけでは分からない)。
        "speed": options.speed,
        "pitch": options.pitch,
        "intonation": options.intonation,
        "volume": options.volume,
        "format": result.audio_format.to_dict() if result.audio_format else {},
        "loudness": result.loudness.to_dict() if result.loudness else None,
        "reading": options.reading.to_dict(),
        "count": result.count,
        "total_duration": round(result.total_duration, 3),
        "clips": [clip.to_dict() for clip in result.clips],
        "warnings": result.warnings,
    }
    if result.pronunciation is not None:
        # 英字が何と読まれたか。あとから聞き直さずに確かめられるようにする。
        data["latin_readings"] = [
            {"surface": w.surface, "kana": w.kana, "slides": w.slides, "spelled": w.spelled}
            for w in result.pronunciation.words
        ]
    path = os.path.join(outdir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return os.path.abspath(path)


def ffmpeg_pattern(options: AudioOptions) -> str:
    """ffmpeg の concat などで使うときのファイル名パターン。"""
    return f"{options.prefix}%0{options.digits}d.wav"
