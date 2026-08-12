"""ナレーション音声の書き出しを確認する。

実際の音声合成は使わず、WAV を書くだけの偽エンジンで確かめる。
実機での合成は tests/test_speech_compat.py で確認する。
"""

import json
import os
import wave

import pytest

from note2slides import narration
from note2slides.audio import (
    AudioExportError,
    AudioOptions,
    OutputExistsError,
    export_narration,
    ffmpeg_pattern,
)
from note2slides.model import Bullet, Deck, Run, Slide
from note2slides.model import KIND_BULLETS
from note2slides.renderer import render_deck
from note2slides.tts import AudioFormat, SpeechFailure, SynthesisReport, Voice

SAMPLE_RATE = 22050


def write_wav(path, seconds, sample_rate=SAMPLE_RATE, channels=1, width=2):
    with wave.open(path, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00" * (int(seconds * sample_rate) * channels * width))


def duration_of(path):
    with wave.open(str(path), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


class FakeEngine:
    """読み上げの代わりに、文字数に比例した長さの WAV を書くエンジン。"""

    def __init__(self, name="fake", fail=(), sample_rate=SAMPLE_RATE, honors=True):
        self.name = name
        self.fail = set(fail)
        self.sample_rate = sample_rate
        self.honors = honors
        self.calls = []

    def pick_voice(self, name=None, language="ja"):
        return Voice(name or "偽の音声", "ja-JP", engine=self.name)

    def honors_sample_rate(self):
        return self.honors

    def default_format(self, sample_rate=None):
        return AudioFormat(sample_rate or self.sample_rate)

    def synthesize(self, jobs, workdir, on_done=None, **kwargs):
        self.calls.append({"jobs": list(jobs), "workdir": workdir, **kwargs})
        report = SynthesisReport(voice=kwargs.get("voice", ""), command=["fake", "-JobFile", "job.json"])
        for job in jobs:
            if job.index in self.fail:
                report.failures.append(SpeechFailure(job.index, "偽の失敗"))
                continue
            write_wav(job.out_path, 0.1 * len(job.text), self.sample_rate)
            if on_done:
                on_done(job.index)
        return report


def make_script(tmp_path, texts, name="script.json"):
    """原稿(JSON)を入力として用意する。空文字は無音のスライドを表す。"""
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "source": "deck.pptx",
                "segments": [
                    {"index": i, "title": f"見出し {i}", "text": text}
                    for i, text in enumerate(texts, start=1)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(path)


def make_pptx(tmp_path, notes, name="deck.pptx"):
    slides = [
        Slide(kind=KIND_BULLETS, title=f"見出し {i}", bullets=[Bullet(runs=[Run("要点")])], notes=note)
        for i, note in enumerate(notes, start=1)
    ]
    path = str(tmp_path / name)
    render_deck(Deck(slides=slides), path)
    return path


# ---------------------------------------------------------------------------
# 出力ファイル
# ---------------------------------------------------------------------------


def test_one_file_per_slide(tmp_path):
    source = make_script(tmp_path, ["一枚目です。", "二枚目です。", "三枚目です。"])
    outdir = tmp_path / "audio"

    result = export_narration(source, str(outdir), engine=FakeEngine())

    assert [clip.filename for clip in result.clips] == [
        "narration_001.wav",
        "narration_002.wav",
        "narration_003.wav",
    ]
    # 辞書順に並べてもスライドの順番と一致する。
    assert sorted(p.name for p in outdir.glob("*.wav")) == [c.filename for c in result.clips]
    assert [clip.index for clip in result.clips] == [1, 2, 3]


def test_index_matches_the_slide_number(tmp_path):
    source = make_pptx(tmp_path, ["1 枚目の本文。", "2 枚目の本文。"])

    result = export_narration(source, str(tmp_path / "audio"), engine=FakeEngine())

    assert result.count == 2
    assert result.clips[0].text == "1 枚目の本文。"
    assert result.clips[1].text == "2 枚目の本文。"
    assert result.clips[0].source == narration.SOURCE_NOTES


def test_silent_slide_still_gets_a_file(tmp_path):
    # 音声が 1 つ欠けると、以降のスライドと音声の対応が全部ずれてしまう。
    source = make_script(tmp_path, ["読み上げます。", "", "最後です。"])
    outdir = tmp_path / "audio"

    result = export_narration(
        source, str(outdir), AudioOptions(silent_duration=1.5), engine=FakeEngine()
    )

    assert result.count == 3
    assert result.clips[1].silent
    assert result.clips[1].duration == 1.5
    assert duration_of(outdir / "narration_002.wav") == 1.5
    assert not result.clips[0].silent


def test_tail_silence_is_added(tmp_path):
    source = make_script(tmp_path, ["あいうえお"])
    outdir = tmp_path / "audio"

    without = export_narration(
        source, str(outdir), AudioOptions(tail_silence=0), engine=FakeEngine()
    )
    base = without.clips[0].duration

    with_tail = export_narration(
        source, str(outdir), AudioOptions(tail_silence=0.5), engine=FakeEngine(), force=True
    )

    assert with_tail.clips[0].duration == pytest.approx(base + 0.5, abs=0.01)
    assert duration_of(outdir / "narration_001.wav") == pytest.approx(base + 0.5, abs=0.01)


def test_all_files_share_one_format(tmp_path):
    source = make_script(tmp_path, ["読み上げ", "", "読み上げ 2"])
    outdir = tmp_path / "audio"

    result = export_narration(source, str(outdir), engine=FakeEngine())

    assert result.audio_format == AudioFormat(SAMPLE_RATE, 1, 2)
    formats = set()
    for path in outdir.glob("*.wav"):
        with wave.open(str(path), "rb") as reader:
            formats.add((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()))
    assert formats == {(SAMPLE_RATE, 1, 2)}


def test_script_without_any_text_needs_no_engine(tmp_path):
    source = make_script(tmp_path, ["", ""])

    result = export_narration(source, str(tmp_path / "audio"), AudioOptions(silent_duration=1))

    assert result.count == 2
    assert all(clip.silent for clip in result.clips)
    assert any("無音" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 一覧(manifest)
# ---------------------------------------------------------------------------


def test_manifest_links_slides_and_audio(tmp_path):
    source = make_script(tmp_path, ["最初の文章。", ""])
    outdir = tmp_path / "audio"

    result = export_narration(
        source, str(outdir), AudioOptions(speed=1.2, silent_duration=2), engine=FakeEngine()
    )

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["count"] == 2
    assert manifest["engine"] == "fake"
    assert manifest["voice"] == "偽の音声"
    assert manifest["speed"] == 1.2
    assert manifest["format"]["sample_rate"] == SAMPLE_RATE
    assert manifest["clips"][0]["file"] == "narration_001.wav"
    assert manifest["clips"][0]["text"] == "最初の文章。"
    assert manifest["clips"][0]["index"] == 1
    assert manifest["clips"][1]["silent"] is True
    assert manifest["total_duration"] == pytest.approx(result.total_duration, abs=0.01)


def test_script_can_be_dumped(tmp_path):
    source = make_pptx(tmp_path, ["本文です。"])
    script_path = tmp_path / "out" / "script.json"

    result = export_narration(
        source, str(tmp_path / "audio"), engine=FakeEngine(), dump_script=str(script_path)
    )

    assert os.path.isfile(result.script_path)
    with open(script_path, encoding="utf-8") as f:
        assert json.load(f)["segments"][0]["text"] == "本文です。"


def test_ffmpeg_pattern():
    assert ffmpeg_pattern(AudioOptions()) == "narration_%03d.wav"
    assert ffmpeg_pattern(AudioOptions(prefix="a", digits=2)) == "a%02d.wav"


# ---------------------------------------------------------------------------
# 上書きと後始末
# ---------------------------------------------------------------------------


def test_existing_output_needs_force(tmp_path):
    source = make_script(tmp_path, ["あ"])
    outdir = str(tmp_path / "audio")
    export_narration(source, outdir, engine=FakeEngine())

    with pytest.raises(OutputExistsError) as excinfo:
        export_narration(source, outdir, engine=FakeEngine())

    assert "--force" in str(excinfo.value)
    assert export_narration(source, outdir, engine=FakeEngine(), force=True).count == 1


def test_old_files_are_removed(tmp_path):
    outdir = tmp_path / "audio"
    export_narration(make_script(tmp_path, ["あ", "い", "う"]), str(outdir), engine=FakeEngine())

    result = export_narration(
        make_script(tmp_path, ["あ"], name="short.json"),
        str(outdir),
        engine=FakeEngine(),
        force=True,
    )

    # 前回の残りが混ざると、動画生成でスライドと音声がずれる。
    assert sorted(p.name for p in outdir.glob("*.wav")) == ["narration_001.wav"]
    assert any("削除" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 失敗したとき
# ---------------------------------------------------------------------------


def test_failure_names_the_slides(tmp_path):
    source = make_script(tmp_path, ["あ", "い", "う"])

    with pytest.raises(AudioExportError) as excinfo:
        export_narration(source, str(tmp_path / "audio"), engine=FakeEngine(fail=[2]))

    message = str(excinfo.value)
    assert "スライド 2" in message
    assert "見出し 2" in message
    assert "偽の失敗" in message
    # 同じことを手元で再現できるように、コマンドと作業ファイルの場所を残す。
    assert "実行したコマンド" in message
    assert "-JobFile" in message


def test_workdir_is_kept_after_a_failure(tmp_path):
    source = make_script(tmp_path, ["あ"])

    with pytest.raises(AudioExportError) as excinfo:
        export_narration(source, str(tmp_path / "audio"), engine=FakeEngine(fail=[1]))

    workdir = str(excinfo.value).rsplit(": ", 1)[-1].strip()
    assert os.path.isdir(workdir)


def test_workdir_is_removed_after_a_success(tmp_path):
    source = make_script(tmp_path, ["あ"])
    engine = FakeEngine()

    result = export_narration(source, str(tmp_path / "audio"), engine=engine)

    assert result.workdir is None
    assert not os.path.exists(engine.calls[0]["workdir"])


def test_workdir_can_be_kept(tmp_path):
    source = make_script(tmp_path, ["あ"])

    result = export_narration(source, str(tmp_path / "audio"), engine=FakeEngine(), keep_work=True)

    assert os.path.isdir(result.workdir)


def test_sample_rate_that_cannot_be_used_is_reported(tmp_path):
    source = make_script(tmp_path, ["あ"])

    result = export_narration(
        source,
        str(tmp_path / "audio"),
        AudioOptions(sample_rate=48000),
        engine=FakeEngine(honors=False, sample_rate=16000),
    )

    assert any("--sample-rate" in w for w in result.warnings)
    assert result.audio_format.sample_rate == 16000


def test_invalid_options(tmp_path):
    source = make_script(tmp_path, ["あ"])

    for options in (
        AudioOptions(speed=0),
        AudioOptions(volume=200),
        AudioOptions(tail_silence=-1),
        AudioOptions(digits=0),
    ):
        with pytest.raises(AudioExportError):
            export_narration(source, str(tmp_path / "audio"), options, engine=FakeEngine())


def test_options_are_passed_to_the_engine(tmp_path):
    source = make_script(tmp_path, ["あ"])
    engine = FakeEngine()

    export_narration(
        source,
        str(tmp_path / "audio"),
        AudioOptions(voice="指定した音声", speed=0.9, volume=80, sample_rate=48000),
        engine=engine,
        timeout=42,
    )

    call = engine.calls[0]
    assert call["voice"] == "指定した音声"
    assert call["speed"] == 0.9
    assert call["volume"] == 80
    assert call["sample_rate"] == 48000
    assert call["timeout"] == 42
