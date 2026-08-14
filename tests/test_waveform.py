"""WAV の読み書きと音量測定を確認する。

音量の測定は放送規格(ITU-R BS.1770-4)に合わせている。規格に合っているかは、
値の分かっている信号を測って確かめる。1kHz の正弦波(モノラル)は、実効値と
同じ値になる(振幅 A なら 20*log10(A/√2) LUFS)。
"""

import math
import wave

import numpy as np
import pytest

from note2slides import waveform as wf
from note2slides.waveform import Waveform, WaveformError


def sine(dbfs, seconds=5.0, sample_rate=48000, frequency=1000.0):
    amplitude = 10.0 ** (dbfs / 20.0)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return Waveform(amplitude * np.sin(2 * math.pi * frequency * t), sample_rate)


# ---------------------------------------------------------------------------
# 音量の測定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample_rate", [48000, 44100, 24000, 16000])
def test_sine_measures_its_rms_level(sample_rate):
    """正弦波の音量は実効値と一致する(振幅 -20dBFS なら -23 LUFS)。"""
    measured = wf.loudness_lufs(sine(-20.0, sample_rate=sample_rate))

    assert measured == pytest.approx(-23.01, abs=0.1)  # 規格の許容差は 0.1 LU


def test_level_difference_is_preserved():
    assert wf.loudness_lufs(sine(-20.0)) - wf.loudness_lufs(sine(-26.0)) == pytest.approx(6.0, abs=0.05)


def test_silence_has_no_loudness():
    assert wf.loudness_lufs(wf.silence(2.0, 48000)) == -math.inf
    assert wf.loudness_lufs(Waveform(np.zeros(0), 48000)) == -math.inf


def test_quiet_parts_do_not_drag_the_measurement_down():
    """規格どおり無音区間を測定から外す(間を長くしても音量が下がらない)。"""
    speech = sine(-20.0, seconds=5.0)
    with_gaps = wf.concat([speech, wf.silence(5.0, 48000), speech], 48000)

    assert wf.loudness_lufs(with_gaps) == pytest.approx(wf.loudness_lufs(speech), abs=0.2)


def test_peak_is_measured_in_dbfs():
    assert wf.peak_dbfs(sine(-6.0)) == pytest.approx(-6.0, abs=0.05)
    assert wf.peak_dbfs(wf.silence(1.0, 48000)) == -math.inf


# ---------------------------------------------------------------------------
# 音量をそろえる
# ---------------------------------------------------------------------------


def test_one_gain_is_chosen_for_every_file():
    parts, adjustment = wf.normalize(
        [sine(-20.0), sine(-26.0)], target_lufs=-16.0, peak_ceiling_dbfs=-1.0
    )

    assert adjustment.result_lufs == pytest.approx(-16.0, abs=0.1)
    assert not adjustment.limited
    # 2 本まとめて 1 本として測るので、それぞれの差はそのまま残る。
    assert wf.loudness_lufs(parts[0]) - wf.loudness_lufs(parts[1]) == pytest.approx(6.0, abs=0.05)


def test_peaks_are_limited_instead_of_turning_everything_down():
    """合成音声はピークだけが飛び出す。全体を下げると今度は音量が足りない。"""
    # 短い山を 1 つだけ持つ信号。平均は小さいがピークは大きい。
    speech = sine(-26.0, seconds=5.0)
    spike = speech.samples.copy()
    spike[24000:24240] *= 12.0
    parts, adjustment = wf.normalize(
        [Waveform(spike, 48000)], target_lufs=-16.0, peak_ceiling_dbfs=-1.0
    )

    assert adjustment.limited
    assert wf.peak_dbfs(parts[0]) == pytest.approx(-1.0, abs=0.1)  # 上限は必ず守る
    # ピークを抑えた分だけ目標より少し下がる。実際の値は narration.json に残す。
    assert adjustment.result_lufs == pytest.approx(-16.0, abs=1.0)


def test_limiting_is_gradual_and_local():
    """飛び出したところだけを下げ、その前後は元のままにする。"""
    samples = np.full(48000, 0.2)
    samples[24000:24100] = 0.95
    limited = wf.limit_peaks(Waveform(samples, 48000), ceiling_dbfs=-6.0)

    ceiling = 10 ** (-6.0 / 20)
    assert np.max(np.abs(limited.samples)) <= ceiling + 1e-9
    assert limited.samples[0] == pytest.approx(0.2)  # 離れたところは変わらない
    assert limited.samples[47999] == pytest.approx(0.2)
    # 山の手前で段差にならず、なだらかに下がっている。
    approach = limited.samples[23800:24000]
    assert np.all(np.diff(approach) <= 1e-9)
    assert np.max(np.abs(np.diff(approach))) < 0.01


def test_signals_below_the_ceiling_are_untouched():
    quiet = sine(-20.0, seconds=1.0)

    limited = wf.limit_peaks(quiet, ceiling_dbfs=-1.0)

    assert np.array_equal(limited.samples, quiet.samples)


def test_silence_needs_no_adjustment():
    parts, adjustment = wf.normalize(
        [wf.silence(2.0, 48000)], target_lufs=-16.0, peak_ceiling_dbfs=-1.0
    )

    assert adjustment.gain_db == 0.0
    assert adjustment.measured_lufs == -math.inf
    assert parts[0].is_empty is False


# ---------------------------------------------------------------------------
# 読み書きと組み立て
# ---------------------------------------------------------------------------


def test_round_trip_keeps_the_waveform(tmp_path):
    path = str(tmp_path / "tone.wav")
    original = sine(-6.0, seconds=0.5)

    wf.write_wav(path, original)
    loaded = wf.read_wav(path)

    assert loaded.sample_rate == original.sample_rate
    assert len(loaded.samples) == len(original.samples)
    # 16bit に量子化した分の誤差だけに収まる。
    assert np.max(np.abs(loaded.samples - original.samples)) < 1e-4


def test_stereo_input_becomes_mono(tmp_path):
    path = str(tmp_path / "stereo.wav")
    left = np.full(1000, 0.5)
    right = np.full(1000, -0.1)
    interleaved = np.empty(2000)
    interleaved[0::2], interleaved[1::2] = left, right
    with wave.open(path, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes(np.round(interleaved * 32767).astype("<i2").tobytes())

    loaded = wf.read_wav(path)

    assert len(loaded.samples) == 1000
    assert loaded.samples[0] == pytest.approx(0.2, abs=0.001)


def test_output_is_always_16bit_mono(tmp_path):
    path = str(tmp_path / "out.wav")

    wf.write_wav(path, sine(-6.0, seconds=0.1, sample_rate=24000))

    with wave.open(path, "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 24000)


def test_clipping_is_prevented_on_write(tmp_path):
    path = str(tmp_path / "loud.wav")

    wf.write_wav(path, Waveform(np.array([3.0, -3.0, 0.0]), 48000))

    assert np.max(np.abs(wf.read_wav(path).samples)) <= 1.0


def test_unreadable_file_says_which(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not a wav")

    with pytest.raises(WaveformError) as excinfo:
        wf.read_wav(str(path))

    assert "broken.wav" in str(excinfo.value)


def test_concat_rejects_mixed_sample_rates():
    with pytest.raises(WaveformError) as excinfo:
        wf.concat([sine(-6.0, 0.1, 48000), sine(-6.0, 0.1, 24000)], 48000)

    assert "48000" in str(excinfo.value) and "24000" in str(excinfo.value)


def test_leading_and_trailing_silence_is_trimmed():
    tone = sine(-6.0, seconds=1.0)
    padded = wf.concat([wf.silence(0.5, 48000), tone, wf.silence(0.5, 48000)], 48000)

    trimmed = wf.trim_silence(padded, margin=0.0)

    assert trimmed.duration == pytest.approx(1.0, abs=0.01)


def test_trimming_all_silence_leaves_nothing():
    assert wf.trim_silence(wf.silence(1.0, 48000)).is_empty


def test_fade_removes_the_step_at_both_ends():
    block = Waveform(np.full(48000, 0.5), 48000)

    faded = wf.fade(block, seconds=0.005)

    assert faded.samples[0] == pytest.approx(0.0, abs=1e-9)
    assert faded.samples[-1] < 0.5
    assert faded.samples[24000] == pytest.approx(0.5)  # 中身は変えない


def test_resample_changes_the_rate_and_keeps_the_length():
    resampled = wf.resample(sine(-6.0, seconds=1.0, sample_rate=16000), 48000)

    assert resampled.sample_rate == 48000
    assert resampled.duration == pytest.approx(1.0, abs=0.001)
    # 変換しても音量は変わらない。
    assert wf.loudness_lufs(resampled) == pytest.approx(-9.0, abs=0.3)
