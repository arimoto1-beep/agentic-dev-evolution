"""WAV の読み書きと、音量をそろえるための測定。

ナレーションを長時間聞き続けられるかどうかは、合成の質と同じくらい「音量が
最後まで一定か」で決まる。スライドごとに合成した音声をそのまま並べると、文の
長さや声の高さで音量が上下し、聞いていて疲れる。

ここでは次の 3 つを扱う。

    読み書き   16bit PCM の WAV <-> float の配列(-1.0〜1.0、モノラル)
    組み立て   前後の無音を削り、指定した長さの無音でつなぐ
    測定       ITU-R BS.1770-4 の音量(LUFS)と最大振幅

音量の測定を放送規格(BS.1770-4)に合わせているのは、YouTube が同じ規格で
音量を判定するため。この値に合わせておけば、公開後に音量を勝手に下げられたり、
他の動画と比べて小さすぎたりしにくい。

規格が定めるフィルタ(K特性)は再帰フィルタだが、そのまま Python で 1 標本ずつ
回すと 10 分の音声で分単位かかる。ここではインパルス応答を先に求めて FFT で
畳み込む(結果は同じで、実時間は 1 秒未満)。
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

#: 16bit PCM。動画の音声トラックとして扱いやすく、どの環境でも再生できる。
SAMPLE_WIDTH = 2
#: BS.1770 の測定に使う区間の長さと間隔(秒)。
_BLOCK_SECONDS = 0.400
_STEP_SECONDS = 0.100
#: 無音とみなす区間を測定から外すための門(BS.1770-4)。
_ABSOLUTE_GATE_LUFS = -70.0
_RELATIVE_GATE_LU = -10.0


class WaveformError(RuntimeError):
    """WAV の読み書きに失敗した場合。"""


@dataclass(frozen=True)
class Waveform:
    """モノラルの音声。samples は -1.0〜1.0 の float64。"""

    samples: np.ndarray
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0

    @property
    def is_empty(self) -> bool:
        return len(self.samples) == 0


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------


def read_wav(path: str) -> Waveform:
    """WAV を読み、モノラルの float に直す。

    合成エンジンが返す形式(8/16/24/32bit、モノラル/ステレオ)の違いをここで
    吸収する。以降の処理はすべて float のモノラルとして扱う。
    """
    try:
        with wave.open(path, "rb") as reader:
            params = reader.getparams()
            raw = reader.readframes(params.nframes)
    except (wave.Error, EOFError, OSError) as exc:
        raise WaveformError(
            f"音声ファイルを読み取れませんでした: {path}\n{type(exc).__name__}: {exc}"
        ) from exc
    if not params.framerate:
        raise WaveformError(f"音声ファイルの形式が不正です(標本化周波数が 0): {path}")

    samples = _decode(raw, params.sampwidth, path)
    if params.nchannels > 1:
        usable = len(samples) - len(samples) % params.nchannels
        samples = samples[:usable].reshape(-1, params.nchannels).mean(axis=1)
    return Waveform(samples, params.framerate)


def _decode(raw: bytes, sample_width: int, path: str) -> np.ndarray:
    """PCM のバイト列を -1.0〜1.0 の float に直す。"""
    if sample_width == 1:  # 8bit だけは符号なし(128 が無音)
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    if sample_width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if sample_width == 3:  # 24bit は numpy に型が無いので 4 バイトに詰め直す
        packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        widened = np.zeros((len(packed), 4), dtype=np.uint8)
        widened[:, 1:] = packed
        return widened.view("<i4").ravel().astype(np.float64) / 2147483648.0
    if sample_width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    raise WaveformError(f"対応していない量子化ビット数です({sample_width * 8}bit): {path}")


def write_wav(path: str, waveform: Waveform) -> None:
    """16bit PCM のモノラル WAV として書き出す。"""
    clipped = np.clip(waveform.samples, -1.0, 1.0)
    # -32768 側に飛ばさないよう 32767 で量子化する(振り切れによる歪みを避ける)。
    data = np.round(clipped * 32767.0).astype("<i2")
    try:
        with wave.open(path, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(SAMPLE_WIDTH)
            writer.setframerate(waveform.sample_rate)
            writer.writeframes(data.tobytes())
    except (wave.Error, OSError) as exc:
        raise WaveformError(
            f"音声ファイルを書き出せませんでした: {path}\n{type(exc).__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


def silence(seconds: float, sample_rate: int) -> Waveform:
    return Waveform(np.zeros(max(0, int(round(seconds * sample_rate)))), sample_rate)


def concat(parts: Sequence[Waveform], sample_rate: int) -> Waveform:
    """同じ標本化周波数の音声をつなぐ。"""
    usable = [p for p in parts if not p.is_empty]
    mismatched = {p.sample_rate for p in usable if p.sample_rate != sample_rate}
    if mismatched:
        raise WaveformError(
            f"標本化周波数が揃っていないためつなげません: {sample_rate}Hz と "
            + " / ".join(f"{rate}Hz" for rate in sorted(mismatched))
        )
    if not usable:
        return Waveform(np.zeros(0), sample_rate)
    return Waveform(np.concatenate([p.samples for p in usable]), sample_rate)


def trim_silence(waveform: Waveform, threshold_db: float = -50.0, margin: float = 0.02) -> Waveform:
    """前後の無音を削る。

    合成エンジンは読み上げの前後に独自の余白を入れる。その長さはエンジンや
    文によって変わるため、いったん削ってから、こちらで決めた長さの無音を
    入れ直す。そうしないと文と文の間隔がスライドごとにばらつく。
    """
    if waveform.is_empty:
        return waveform
    threshold = 10.0 ** (threshold_db / 20.0)
    loud = np.flatnonzero(np.abs(waveform.samples) > threshold)
    if len(loud) == 0:
        return Waveform(np.zeros(0), waveform.sample_rate)
    pad = int(round(margin * waveform.sample_rate))
    start = max(0, int(loud[0]) - pad)
    end = min(len(waveform.samples), int(loud[-1]) + pad + 1)
    return Waveform(waveform.samples[start:end], waveform.sample_rate)


def fade(waveform: Waveform, seconds: float = 0.005) -> Waveform:
    """先頭と末尾に短いフェードをかける。

    波形が 0 でない値から始まったり終わったりすると、再生時に「プツッ」と鳴る。
    数ミリ秒なので聞こえ方は変わらない。
    """
    if waveform.is_empty or seconds <= 0:
        return waveform
    length = min(int(round(seconds * waveform.sample_rate)), len(waveform.samples) // 2)
    if length < 1:
        return waveform
    samples = waveform.samples.copy()
    ramp = np.linspace(0.0, 1.0, length, endpoint=False)
    samples[:length] *= ramp
    samples[-length:] *= ramp[::-1]
    return Waveform(samples, waveform.sample_rate)


def apply_gain(waveform: Waveform, gain_db: float) -> Waveform:
    if waveform.is_empty or gain_db == 0.0:
        return waveform
    return Waveform(waveform.samples * (10.0 ** (gain_db / 20.0)), waveform.sample_rate)


# ---------------------------------------------------------------------------
# 測定
# ---------------------------------------------------------------------------


def peak_dbfs(waveform: Waveform) -> float:
    """最大振幅(dBFS)。0 dBFS を超えると音が割れる。"""
    if waveform.is_empty:
        return -math.inf
    peak = float(np.max(np.abs(waveform.samples)))
    return 20.0 * math.log10(peak) if peak > 0 else -math.inf


def block_energies(waveform: Waveform) -> np.ndarray:
    """BS.1770 の測定区間ごとの平均パワー。

    複数のファイルをまとめて 1 本として測るために、区間ごとの値を返す
    (つないだ 1 本の配列を作らずに済み、長い動画でも記憶領域を食わない)。
    """
    if waveform.is_empty:
        return np.zeros(0)
    block = int(round(_BLOCK_SECONDS * waveform.sample_rate))
    step = int(round(_STEP_SECONDS * waveform.sample_rate))
    if block < 1 or len(waveform.samples) < block:
        return np.zeros(0)

    filtered = _k_weight(waveform.samples, waveform.sample_rate)
    # 400ms ごとの平均パワー。各区間は 100ms ずつずらして重ねて取る。
    squared = np.concatenate(([0.0], np.cumsum(filtered**2)))
    starts = np.arange(0, len(filtered) - block + 1, step)
    return (squared[starts + block] - squared[starts]) / block


def integrated_lufs(energies: np.ndarray) -> float:
    """測定区間から総合音量(LUFS)を求める。無音なら -inf を返す。

    静かな部分に引きずられないよう、規格どおり 2 段階の門(絶対 -70 LUFS、
    相対 -10 LU)で無音区間を測定から外す。
    """
    if len(energies) == 0:
        return -math.inf
    with np.errstate(divide="ignore"):
        kept = energies[-0.691 + 10.0 * np.log10(energies) > _ABSOLUTE_GATE_LUFS]
    if len(kept) == 0:
        return -math.inf
    relative_gate = -0.691 + 10.0 * math.log10(float(np.mean(kept))) + _RELATIVE_GATE_LU
    with np.errstate(divide="ignore"):
        kept = kept[-0.691 + 10.0 * np.log10(kept) > relative_gate]
    if len(kept) == 0:
        return -math.inf
    return -0.691 + 10.0 * math.log10(float(np.mean(kept)))


def loudness_lufs(waveform: Waveform) -> float:
    """1 本の音声の総合音量(LUFS)。"""
    return integrated_lufs(block_energies(waveform))


def _k_weight(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """BS.1770 の K特性フィルタをかける(高域を持ち上げ、超低域を落とす)。"""
    return _fft_filter(samples, _k_weight_impulse(sample_rate))


_IMPULSE_CACHE: dict = {}


def _k_weight_impulse(sample_rate: int) -> np.ndarray:
    """K特性フィルタのインパルス応答。

    規格は 48kHz の係数表を示すが、他の周波数でも使えるよう、規格が定める
    シェルビングフィルタとハイパスフィルタを標本化周波数から組み立て直す
    (48kHz では係数表と一致する)。
    """
    cached = _IMPULSE_CACHE.get(sample_rate)
    if cached is not None:
        return cached

    stages = (_high_shelf(sample_rate), _high_pass(sample_rate))
    # 一番低い極(38Hz)でも 0.25 秒あれば十分に減衰する。
    length = max(int(round(0.25 * sample_rate)), 256)
    impulse = np.zeros(length)
    impulse[0] = 1.0
    for stage in stages:
        impulse = _biquad(impulse, stage)
    _IMPULSE_CACHE[sample_rate] = impulse
    return impulse


def _high_shelf(sample_rate: int):
    """1681Hz から上を約 4dB 持ち上げる(頭部の音響特性の近似)。"""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sample_rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh**0.4996667741545416
    denom = 1.0 + k / q + k * k
    b = (
        (vh + vb * k / q + k * k) / denom,
        2.0 * (k * k - vh) / denom,
        (vh - vb * k / q + k * k) / denom,
    )
    a = (2.0 * (k * k - 1.0) / denom, (1.0 - k / q + k * k) / denom)
    return b, a


def _high_pass(sample_rate: int):
    """38Hz 以下を落とす(耳に聞こえない低音で測定値がぶれないようにする)。"""
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sample_rate)
    denom = 1.0 + k / q + k * k
    b = (1.0, -2.0, 1.0)
    a = (2.0 * (k * k - 1.0) / denom, (1.0 - k / q + k * k) / denom)
    return b, a


def _biquad(samples: np.ndarray, stage) -> np.ndarray:
    """2 次フィルタを 1 標本ずつ適用する。インパルス応答を作るときだけ使う。"""
    (b0, b1, b2), (a1, a2) = stage
    out = np.zeros(len(samples))
    x1 = x2 = y1 = y2 = 0.0
    for i, x0 in enumerate(samples):
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out[i] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return out


def _fft_filter(samples: np.ndarray, impulse: np.ndarray) -> np.ndarray:
    """インパルス応答を FFT の重畳加算で畳み込む。"""
    taps = len(impulse)
    if taps <= 1:
        return samples * (impulse[0] if taps else 0.0)
    size = 1 << int(math.ceil(math.log2(max(taps * 4, 4096))))
    hop = size - taps + 1
    spectrum = np.fft.rfft(impulse, size)
    out = np.zeros(len(samples) + taps - 1)
    for start in range(0, len(samples), hop):
        chunk = samples[start : start + hop]
        block = np.fft.irfft(np.fft.rfft(chunk, size) * spectrum, size)
        out[start : start + len(chunk) + taps - 1] += block[: len(chunk) + taps - 1]
    return out[: len(samples)]


# ---------------------------------------------------------------------------
# 音量そろえ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoudnessAdjustment:
    """全ファイルに共通してかけた音量の補正。"""

    gain_db: float
    measured_lufs: float
    result_lufs: float
    result_peak_dbfs: float
    limited: bool = False  # 飛び出したピークを抑えたか
    limit_db: float = 0.0  # 抑えた最大の量(dB)

    def to_dict(self) -> dict:
        return {
            "gain_db": round(self.gain_db, 2),
            "measured_lufs": round(self.measured_lufs, 2),
            "result_lufs": round(self.result_lufs, 2),
            "result_peak_dbfs": round(self.result_peak_dbfs, 2),
            "limited": self.limited,
            "limit_db": round(self.limit_db, 2),
        }


def normalize(
    parts: Sequence[Waveform],
    target_lufs: float,
    peak_ceiling_dbfs: float,
) -> tuple[List[Waveform], LoudnessAdjustment]:
    """全ファイルの音量を目標にそろえる。

    ファイルごとにそろえると、静かなスライドだけが持ち上がって、スライドが
    変わるたびに音量が動いて聞こえる。ここでは全ファイルを 1 本の動画として
    測り、同じ補正値を全ファイルにかける。スライド間の音量差は元のまま残る。

    合成音声は、平均の音量に対してピークだけが 15dB ほど飛び出している。
    そのため目標の音量まで持ち上げると、平均はまだ小さいのにピークだけが
    振り切れる。全体を下げて合わせると今度は音量が足りないので、飛び出した
    ところだけを抑える(下げる量は数 dB で、下げている間も滑らかに変える)。
    """
    measured = integrated_lufs(
        np.concatenate([block_energies(part) for part in parts]) if parts else np.zeros(0)
    )
    if measured == -math.inf:
        peak = max((peak_dbfs(part) for part in parts), default=-math.inf)
        return list(parts), LoudnessAdjustment(0.0, measured, measured, peak)

    gain_db = target_lufs - measured
    amplified = [apply_gain(part, gain_db) for part in parts]

    limit_db = max((peak_dbfs(part) for part in amplified), default=-math.inf) - peak_ceiling_dbfs
    limited = limit_db > 0.01
    if limited:
        amplified = [limit_peaks(part, peak_ceiling_dbfs) for part in amplified]

    result_lufs = integrated_lufs(
        np.concatenate([block_energies(part) for part in amplified]) if amplified else np.zeros(0)
    )
    return amplified, LoudnessAdjustment(
        gain_db=gain_db,
        measured_lufs=measured,
        result_lufs=result_lufs,
        result_peak_dbfs=max((peak_dbfs(part) for part in amplified), default=-math.inf),
        limited=limited,
        limit_db=max(0.0, limit_db),
    )


def limit_peaks(
    waveform: Waveform,
    ceiling_dbfs: float,
    attack: float = 0.005,
    release: float = 0.06,
    frame: float = 0.0005,
) -> Waveform:
    """上限を超えるところだけ音量を下げる。

    その場で切り落とすと音が歪むため、超える手前から下げ始め、超えたあとは
    ゆっくり戻す。下げ幅は `frame` ごとに求め、直線で補間して滑らかにする。
    """
    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    if waveform.is_empty or np.max(np.abs(waveform.samples)) <= ceiling:
        return waveform

    size = max(1, int(round(frame * waveform.sample_rate)))
    count = -(-len(waveform.samples) // size)  # 切り上げ
    padded = np.zeros(count * size)
    padded[: len(waveform.samples)] = np.abs(waveform.samples)
    peaks = padded.reshape(count, size).max(axis=1)

    with np.errstate(divide="ignore", invalid="ignore"):
        needed = np.where(peaks > ceiling, ceiling / peaks, 1.0)

    # 前後に傾斜を付ける。「1 区間あたりの最大の変化量」を決めておけば、
    # 累積最小値で「どこまで下げておく必要があるか」を一度に求められる。
    index = np.arange(count, dtype=np.float64)
    attack_step = size / max(attack * waveform.sample_rate, size)
    release_step = size / max(release * waveform.sample_rate, size)
    # 山の手前から下げ始める(後ろから見た累積最小値)。
    gain = np.minimum.accumulate((needed + index * attack_step)[::-1])[::-1] - index * attack_step
    # 山を過ぎたらゆっくり戻す(前から見た累積最小値)。
    gain = np.minimum.accumulate(gain - index * release_step) + index * release_step

    centers = index * size + (size - 1) / 2.0
    envelope = np.interp(np.arange(len(waveform.samples)), centers, np.clip(gain, 0.0, 1.0))
    # 補間で上限をわずかに超えることがあるので、最後に上限で押さえる。
    return Waveform(np.clip(waveform.samples * envelope, -ceiling, ceiling), waveform.sample_rate)


def resample(waveform: Waveform, sample_rate: int) -> Waveform:
    """標本化周波数を変える(線形補間)。

    合成エンジンが目的の周波数で出せない場合にだけ使う。音質の劣化を避ける
    ため、まずはエンジン側に目的の周波数で出させることを優先する。
    """
    if waveform.sample_rate == sample_rate or waveform.is_empty:
        return Waveform(waveform.samples, sample_rate)
    count = int(round(len(waveform.samples) * sample_rate / waveform.sample_rate))
    if count < 1:
        return Waveform(np.zeros(0), sample_rate)
    source = np.arange(len(waveform.samples))
    target = np.linspace(0, len(waveform.samples) - 1, count)
    return Waveform(np.interp(target, source, waveform.samples), sample_rate)


def describe_format(sample_rate: int) -> str:
    return f"{sample_rate}Hz / {SAMPLE_WIDTH * 8}bit / モノラル"


def durations(parts: Sequence[Waveform]) -> List[float]:
    return [p.duration for p in parts]
