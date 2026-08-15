"""ナレーション音声の書き出しを確認する。

実際の音声合成は使わず、渡された文字列の長さだけ音を鳴らす偽エンジンで確かめる。
実機での合成は tests/test_speech_compat.py(Windows 標準)と
tests/test_voicevox_compat.py(VOICEVOX)で確認する。
"""

import json
import math
import os
import wave

import numpy as np
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
from note2slides.reading import ReadingStyle
from note2slides.renderer import render_deck
from note2slides.speech import AudioFormat, SpeechFailure, SynthesisReport, Voice
from note2slides.waveform import Waveform, loudness_lufs, read_wav

SAMPLE_RATE = 22050
#: 偽エンジンが鳴らす音の振幅。無音だと前後の切り詰めで消えてしまう。
TONE_AMPLITUDE = 0.5


def tone(seconds, sample_rate=SAMPLE_RATE, amplitude=TONE_AMPLITUDE):
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return np.round(amplitude * np.sin(2 * math.pi * 440 * t) * 32767).astype("<i2")


def write_tone(path, seconds, sample_rate=SAMPLE_RATE, amplitude=TONE_AMPLITUDE):
    """読み上げの代わりになる、一定の大きさの音を書く。"""
    write_samples(path, tone(seconds, sample_rate, amplitude), sample_rate)


def write_samples(path, data, sample_rate=SAMPLE_RATE):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(data.tobytes())


def duration_of(path):
    with wave.open(str(path), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


class FakeEngine:
    """読み上げの代わりに、文字数に比例した長さの音を書くエンジン。

    1 件はスライド 1 枚分なので、区切りごとの音と、その後ろの間(無音)を
    つないだものを 1 本の WAV として書く(本物のエンジンと同じ形にする)。
    """

    def __init__(self, name="fake", fail=(), sample_rate=SAMPLE_RATE, honors=True, amplitude=TONE_AMPLITUDE):
        self.name = name
        self.fail = set(fail)
        self.sample_rate = sample_rate
        self.honors = honors
        self.amplitude = amplitude
        self.calls = []
        self.closed = False

    def pick_voice(self, name=None, language="ja"):
        return Voice(name or "偽の音声", "ja-JP", engine=self.name)

    def honors_sample_rate(self):
        return self.honors

    def default_format(self, sample_rate=None):
        return AudioFormat(sample_rate or self.sample_rate)

    def close(self):
        self.closed = True

    def synthesize(self, jobs, workdir, on_done=None, **kwargs):
        self.calls.append({"jobs": list(jobs), "workdir": workdir, **kwargs})
        rate = kwargs.get("sample_rate") if self.honors else None
        report = SynthesisReport(
            voice=kwargs.get("voice", ""), command=["fake", "-JobFile", "job.json"]
        )
        sample_rate = rate or self.sample_rate
        for job in jobs:
            if job.index in self.fail:
                report.failures.append(SpeechFailure(job.index, "偽の失敗"))
                continue
            parts = []
            for piece in job.pieces:
                # 音量測定の区間(400ms)より短いと音量をそろえられないので下限を置く。
                parts.append(tone(max(0.6, 0.1 * len(piece.text)), sample_rate, self.amplitude))
                parts.append(np.zeros(int(piece.pause_after * sample_rate), dtype="<i2"))
            write_samples(job.out_path, np.concatenate(parts), sample_rate)
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


def manifest_of(outdir):
    with open(os.path.join(str(outdir), "narration.json"), encoding="utf-8") as f:
        return json.load(f)


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


def test_all_files_share_one_format(tmp_path):
    source = make_script(tmp_path, ["読み上げ", "", "読み上げ 2"])
    outdir = tmp_path / "audio"

    result = export_narration(source, str(outdir), engine=FakeEngine())

    assert result.audio_format == AudioFormat(48000, 1, 2)
    formats = set()
    for path in outdir.glob("*.wav"):
        with wave.open(str(path), "rb") as reader:
            formats.add((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()))
    assert formats == {(48000, 1, 2)}


def test_engine_output_is_converted_to_the_requested_rate(tmp_path):
    """出力形式を選べないエンジンでも、全ファイルを同じ形式にそろえる。"""
    source = make_script(tmp_path, ["あいうえお"])
    outdir = tmp_path / "audio"

    result = export_narration(
        source,
        str(outdir),
        AudioOptions(sample_rate=48000),
        engine=FakeEngine(honors=False, sample_rate=16000),
    )

    assert result.audio_format.sample_rate == 48000
    with wave.open(str(outdir / "narration_001.wav"), "rb") as reader:
        assert reader.getframerate() == 48000
    assert any("16000Hz" in w and "48000Hz" in w for w in result.warnings)


def test_script_without_any_text_needs_no_engine(tmp_path):
    source = make_script(tmp_path, ["", ""])

    result = export_narration(source, str(tmp_path / "audio"), AudioOptions(silent_duration=1))

    assert result.count == 2
    assert all(clip.silent for clip in result.clips)
    assert any("無音" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 読み上げ単位と間
# ---------------------------------------------------------------------------


def test_a_slide_is_synthesized_as_one_continuous_reading(tmp_path):
    """区切って別々に合成すると、区切りの先頭だけ音程が跳ね上がって聞こえる。

    文の切れ目は「間」として渡し、読み上げそのものは 1 枚分を続けて行わせる。
    """
    source = make_script(tmp_path, ["最初の文です。次の文です。", "別のスライドです。"])
    engine = FakeEngine()

    export_narration(
        source,
        str(tmp_path / "audio"),
        AudioOptions(reading=ReadingStyle(sentence_pause=0.35)),
        engine=engine,
    )

    jobs = engine.calls[0]["jobs"]
    assert len(jobs) == 2  # スライド 1 枚につき 1 回
    assert [[piece.text for piece in job.pieces] for job in jobs] == [
        ["最初の文です。", "次の文です。"],
        ["別のスライドです。"],
    ]
    # 文の間は読み上げの中に置く。最後の区切りの後ろには置かない。
    assert [piece.pause_after for piece in jobs[0].pieces] == [0.35, 0.0]
    # どの合成単位がどのスライドのものかを保持する(失敗時に枚数で示すため)。
    assert [job.slide for job in jobs] == [1, 2]


def test_silent_slides_are_not_sent_to_the_engine(tmp_path):
    source = make_script(tmp_path, ["読み上げます。", "", "最後です。"])
    engine = FakeEngine()

    export_narration(source, str(tmp_path / "audio"), engine=engine)

    assert [job.slide for job in engine.calls[0]["jobs"]] == [1, 3]


def test_pauses_are_inserted_between_sentences(tmp_path):
    source = make_script(tmp_path, ["最初の文です。次の文です。"])
    outdir = tmp_path / "audio"
    style = ReadingStyle(sentence_pause=0.5, lead_silence=0.2, tail_silence=0.4)

    quiet = export_narration(
        source,
        str(outdir),
        AudioOptions(reading=ReadingStyle(sentence_pause=0, lead_silence=0, tail_silence=0)),
        engine=FakeEngine(),
    )
    spaced = export_narration(
        source, str(outdir), AudioOptions(reading=style), engine=FakeEngine(), force=True
    )

    # 文間 0.5 + 先頭 0.2 + 末尾 0.4 の分だけ長くなる。
    assert spaced.clips[0].duration == pytest.approx(quiet.clips[0].duration + 1.1, abs=0.02)


def test_tail_silence_is_added(tmp_path):
    source = make_script(tmp_path, ["あいうえお"])
    outdir = tmp_path / "audio"

    without = export_narration(
        source,
        str(outdir),
        AudioOptions(reading=ReadingStyle(tail_silence=0, lead_silence=0)),
        engine=FakeEngine(),
    )
    base = without.clips[0].duration

    with_tail = export_narration(
        source,
        str(outdir),
        AudioOptions(reading=ReadingStyle(tail_silence=0.5, lead_silence=0)),
        engine=FakeEngine(),
        force=True,
    )

    assert with_tail.clips[0].duration == pytest.approx(base + 0.5, abs=0.02)
    assert duration_of(outdir / "narration_001.wav") == pytest.approx(base + 0.5, abs=0.02)


def test_reading_text_is_recorded(tmp_path):
    """合成に渡した文字列を残し、原稿とどう違うかを後から確認できるようにする。"""
    source = make_script(tmp_path, ["・箇条書きの項目\n詳しくは https://example.com/a を見てください"])
    outdir = tmp_path / "audio"

    result = export_narration(source, str(outdir), engine=FakeEngine())

    clip = result.clips[0]
    assert clip.text.startswith("・箇条書きの項目")  # 原稿は元のまま
    assert "・" not in clip.reading
    assert "https" not in clip.reading
    assert clip.reading == "箇条書きの項目。 詳しくはを見てください。"
    assert any("URL" in note for note in clip.notes)
    assert manifest_of(outdir)["clips"][0]["reading"] == clip.reading


def test_text_that_becomes_unreadable_is_silent(tmp_path):
    """読み上げる文字が残らない場合も、番号がずれないよう無音を出す。"""
    source = make_script(tmp_path, ["https://example.com/only-a-link"])

    result = export_narration(source, str(tmp_path / "audio"), engine=FakeEngine())

    assert result.count == 1
    assert result.clips[0].silent
    assert result.clips[0].text  # 原稿には文章が残っている


def test_hold_makes_the_slide_longer(tmp_path):
    """コードや図のスライドは、読み上げが終わっても画面を読む時間が要る。"""
    path = tmp_path / "script.json"
    path.write_text(
        json.dumps(
            {
                "segments": [
                    {"index": 1, "text": "画面のコマンドをご覧ください。"},
                    {"index": 2, "text": "画面のコマンドをご覧ください。", "hold": 3.0},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = export_narration(str(path), str(tmp_path / "audio"), engine=FakeEngine())

    assert result.clips[1].duration == pytest.approx(result.clips[0].duration + 3.0, abs=0.01)


def test_reading_dictionary_is_applied(tmp_path):
    source = make_script(tmp_path, ["note の記事です。"])
    style = ReadingStyle(dictionary={"note": "ノート"})

    result = export_narration(
        source, str(tmp_path / "audio"), AudioOptions(reading=style), engine=FakeEngine()
    )

    assert result.clips[0].reading == "ノートの記事です。"
    assert any("ノート" in note for note in result.clips[0].notes)


# ---------------------------------------------------------------------------
# 音量
# ---------------------------------------------------------------------------


def test_loudness_is_matched_to_the_target(tmp_path):
    source = make_script(tmp_path, ["読み上げる文章です。", "もう一枚あります。"])
    outdir = tmp_path / "audio"

    result = export_narration(
        source, str(outdir), AudioOptions(loudness=-16.0), engine=FakeEngine()
    )

    assert result.loudness is not None
    assert result.loudness.result_lufs == pytest.approx(-16.0, abs=0.5)
    for clip in result.clips:
        assert loudness_lufs(read_wav(clip.path)) == pytest.approx(-16.0, abs=1.0)


def test_the_same_gain_is_applied_to_every_file(tmp_path):
    """ファイルごとにそろえると、スライドが変わるたびに音量が動いて聞こえる。"""
    source = make_script(tmp_path, ["大きい方の文章です。", "小さい方の文章です。"])
    outdir = tmp_path / "audio"

    class UnevenEngine(FakeEngine):
        def synthesize(self, jobs, workdir, on_done=None, **kwargs):
            for job in jobs:
                # 2 枚目だけ半分の大きさで合成する。
                write_tone(
                    job.out_path,
                    1.0,
                    kwargs.get("sample_rate") or self.sample_rate,
                    TONE_AMPLITUDE / (2 if job.slide == 2 else 1),
                )
                if on_done:
                    on_done(job.index)
            self.calls.append({"jobs": list(jobs), "workdir": workdir, **kwargs})
            return SynthesisReport(voice="偽の音声", command=["fake"])

    export_narration(source, str(outdir), engine=UnevenEngine())

    louder = loudness_lufs(read_wav(str(outdir / "narration_001.wav")))
    quieter = loudness_lufs(read_wav(str(outdir / "narration_002.wav")))
    # 元の 6dB 差はそのまま残る(全体に同じ補正をかけただけ)。
    assert louder - quieter == pytest.approx(6.0, abs=0.5)


def test_loudness_can_be_turned_off(tmp_path):
    source = make_script(tmp_path, ["読み上げる文章です。"])
    outdir = tmp_path / "audio"

    result = export_narration(
        source, str(outdir), AudioOptions(loudness=None), engine=FakeEngine()
    )

    assert result.loudness is None
    # 合成したままの大きさ(振幅 0.5 の音)で出る。
    assert np.max(np.abs(read_wav(result.clips[0].path).samples)) == pytest.approx(0.5, abs=0.01)


def test_peak_is_kept_below_the_ceiling(tmp_path):
    """目標まで持ち上げてピークが上限を超える場合は、そこだけ抑える。"""
    source = make_script(tmp_path, ["読み上げる文章です。"])
    outdir = tmp_path / "audio"

    result = export_narration(
        source,
        str(outdir),
        AudioOptions(loudness=-3.0, peak_ceiling=-1.5),
        engine=FakeEngine(),
    )

    assert result.loudness.limited
    assert result.loudness.result_peak_dbfs == pytest.approx(-1.5, abs=0.1)
    assert np.max(np.abs(read_wav(result.clips[0].path).samples)) < 1.0


# ---------------------------------------------------------------------------
# 一覧(manifest)
# ---------------------------------------------------------------------------


def test_manifest_links_slides_and_audio(tmp_path):
    source = make_script(tmp_path, ["最初の文章。", ""])
    outdir = tmp_path / "audio"

    result = export_narration(
        source, str(outdir), AudioOptions(speed=1.2, silent_duration=2), engine=FakeEngine()
    )

    manifest = manifest_of(outdir)

    assert manifest["count"] == 2
    assert manifest["engine"] == "fake"
    assert manifest["voice"] == "偽の音声"
    assert manifest["speed"] == 1.2
    assert manifest["format"]["sample_rate"] == 48000
    assert manifest["loudness"]["result_lufs"] == pytest.approx(-16.0, abs=0.5)
    assert manifest["clips"][0]["file"] == "narration_001.wav"
    assert manifest["clips"][0]["text"] == "最初の文章。"
    assert manifest["clips"][0]["index"] == 1
    assert manifest["clips"][0]["utterances"] == 1
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


def test_failure_points_at_the_slide_not_the_synthesis_order(tmp_path):
    """無音のスライドには合成を頼まないので、合成の順番と枚数はずれる。

    失敗を追うのはスライド番号なので、ずれていても資料のどこを直せばよいかが
    分かるようにする。
    """
    source = make_script(tmp_path, ["", "最初の文。次の文。"])

    with pytest.raises(AudioExportError) as excinfo:
        export_narration(source, str(tmp_path / "audio"), engine=FakeEngine(fail=[1]))

    assert "1 枚分の音声を合成できませんでした" in str(excinfo.value)
    assert "スライド 2" in str(excinfo.value)


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


def test_invalid_options(tmp_path):
    source = make_script(tmp_path, ["あ"])

    for options in (
        AudioOptions(speed=0),
        AudioOptions(volume=200),
        AudioOptions(reading=ReadingStyle(tail_silence=-1)),
        AudioOptions(digits=0),
        AudioOptions(loudness=-100),
        AudioOptions(sample_rate=100),
    ):
        with pytest.raises(AudioExportError):
            export_narration(source, str(tmp_path / "audio"), options, engine=FakeEngine())


def test_options_are_passed_to_the_engine(tmp_path):
    source = make_script(tmp_path, ["あ"])
    engine = FakeEngine()

    export_narration(
        source,
        str(tmp_path / "audio"),
        AudioOptions(
            voice="指定した音声", speed=0.9, volume=80, sample_rate=44100, pitch=0.05, intonation=1.2
        ),
        engine=engine,
        timeout=42,
    )

    call = engine.calls[0]
    assert call["voice"] == "指定した音声"
    assert call["speed"] == 0.9
    assert call["volume"] == 80
    assert call["sample_rate"] == 44100
    assert call["pitch"] == 0.05
    assert call["intonation"] == 1.2
    assert call["timeout"] == 42


def test_engine_passed_in_is_not_closed(tmp_path):
    """呼び出し側が用意したエンジンは、こちらで止めない。"""
    source = make_script(tmp_path, ["あ"])
    engine = FakeEngine()

    export_narration(source, str(tmp_path / "audio"), engine=engine)

    assert not engine.closed


def test_engine_we_created_is_closed(tmp_path, monkeypatch):
    from note2slides import audio as audio_mod

    engine = FakeEngine()
    monkeypatch.setattr(audio_mod.tts_mod, "select_engine", lambda *a, **k: engine)

    export_narration(make_script(tmp_path, ["あ"]), str(tmp_path / "audio"))

    assert engine.closed


def test_waveform_round_trip(tmp_path):
    """書き出した WAV を読み直すと同じ波形になる(組み立てが壊れていないこと)。"""
    path = tmp_path / "tone.wav"
    write_tone(path, 0.5, 48000)

    loaded = read_wav(str(path))

    assert isinstance(loaded, Waveform)
    assert loaded.sample_rate == 48000
    assert loaded.duration == pytest.approx(0.5, abs=0.001)
    assert np.max(np.abs(loaded.samples)) == pytest.approx(TONE_AMPLITUDE, abs=0.01)
