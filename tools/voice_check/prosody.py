"""出来上がったナレーションの「声の形」を測る。**聞いたことにはならない。**

    .venv/Scripts/python.exe tools/voice_check/prosody.py 音声の場所 [比べる音声の場所]

音声の場所は note2slides-audio / note2slides-video が書き出したフォルダ(narration.json のあるところ)。
2 つ渡すと並べて出す(前の版と比べるため)。

測るもの(どれも、聞き続けやすさを損なう形が「起きていないか」を見るためのもの):

* 間で区切った発話の終わりが下がっているか / 上がって終わっていないか
  日本語の言い切りは文末で下がる。下がらずに間が来ると、言いかけで止まったように聞こえる。
* 間のあとで高さが戻るか / 下がって始まっていないか
  文の頭で高さが戻らないと、前の文の続きに聞こえる。
* 間の長さの分布。読点・文・段落の間が、別々の長さの山になっているか。
  全部が同じ長さだと、聞く人は間から文の切れ目を聞き分けられない。
* ひと続きに話す長さ。短く刻まれすぎていないか。

Gen38 で、Gen37 の音声(上がって終わる 10% / 下がって始まる 18% / 間の 97% が 0.25 - 0.7 秒)と、
直したあとの音声(3% / 3% / 読点と文で 2 つの山)を見分けられることを確かめている。
**良い数字は「崩れた形が見つからない」ことまでしか言わない。** 自然かどうかは聞いて決める。
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from note2slides.console import use_utf8_output  # noqa: E402
from note2slides.waveform import read_wav  # noqa: E402

GAP = 0.25  # これ以上の無音を「間」とみなす(秒)
EDGE = 0.30  # 発話の終わり・始まりとして見る長さ(秒)


def f0_track(x, sr, fmin=60, fmax=320):
    """10ms ごとの基本周波数(自己相関。無声・無音は 0)。"""
    hop, win = int(0.010 * sr), int(0.040 * sr)
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for i in range(0, len(x) - win, hop):
        f = x[i:i + win].astype(np.float64)
        if np.sqrt((f * f).mean()) < 10 ** (-38 / 20):
            out.append(0.0)
            continue
        f = f - f.mean()
        spec = np.fft.rfft(f * np.hanning(len(f)), 2 * len(f))
        ac = np.fft.irfft(spec * np.conj(spec))[:hi + 1]
        if ac[0] <= 0:
            out.append(0.0)
            continue
        ac = ac / ac[0]
        k = lo + int(np.argmax(ac[lo:hi + 1]))
        out.append(sr / k if ac[k] > 0.45 else 0.0)
    return np.array(out)


def segments(x, sr):
    """無音(GAP 秒以上)で区切った発話の (始まり, 終わり) 秒。"""
    win = int(0.02 * sr)
    env = np.array([np.abs(x[i:i + win]).max() for i in range(0, len(x) - win, win)])
    loud = env > 10 ** (-40 / 20)
    segs, start, quiet = [], None, 0
    for j, v in enumerate(loud):
        if v:
            if start is None:
                start = j
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * 0.02 >= GAP:
                segs.append((start * 0.02, (j - quiet + 1) * 0.02))
                start, quiet = None, 0
    if start is not None:
        segs.append((start * 0.02, len(loud) * 0.02))
    return segs


def semitones(f):
    return 12 * np.log2(f / 100.0)


def measure(audio_dir):
    data = json.load(open(os.path.join(audio_dir, "narration.json"), encoding="utf-8"))
    fall, reset, gaps, runs = [], [], [], []
    for clip in data["clips"]:
        if clip.get("silent"):
            continue
        wav = read_wav(os.path.join(audio_dir, clip["file"]))
        x = wav.samples if wav.samples.ndim == 1 else wav.samples.mean(axis=1)
        step = max(1, wav.sample_rate // 12000)
        f0 = f0_track(x[::step], wav.sample_rate // step)
        segs = segments(x, wav.sample_rate)
        info = []
        for a, b in segs:
            runs.append(b - a)
            f = f0[int(a / 0.01):int(b / 0.01)]
            voiced = f[f > 0]
            head = f[: int(EDGE / 0.01)]
            tail = f[-int(EDGE / 0.01):]
            head, tail = head[head > 0], tail[tail > 0]
            ok = len(voiced) >= 10 and len(head) >= 3 and len(tail) >= 3
            info.append((float(np.median(semitones(voiced))), float(np.median(semitones(head))),
                         float(np.median(semitones(tail)))) if ok else None)
        for n in range(len(segs) - 1):
            gaps.append(segs[n + 1][0] - segs[n][1])
            p, q = info[n], info[n + 1]
            if p and q:
                fall.append(p[2] - p[0])
                reset.append(q[1] - p[2])
    return np.array(fall), np.array(reset), np.array(gaps), np.array(runs)


def report(label, measured):
    fall, reset, gaps, runs = measured
    hist = np.histogram(gaps, bins=[0.25, 0.35, 0.45, 0.55, 0.7, 1.0, 1.5, 99])[0]
    return [
        label,
        f"  間の数                         {len(gaps)}",
        f"  終わりが下がる(-1 半音以下)    {np.mean(fall <= -1) * 100:5.0f}%",
        f"  上がって終わる(+1 半音以上)    {np.mean(fall >= 1) * 100:5.0f}%",
        f"  間のあとで戻る(+2 半音以上)    {np.mean(reset >= 2) * 100:5.0f}%",
        f"  下がって始まる(-2 半音以下)    {np.mean(reset <= -2) * 100:5.0f}%",
        "  間の長さ(秒) .25-.35:%d .35-.45:%d .45-.55:%d .55-.7:%d .7-1:%d 1-1.5:%d 1.5-:%d" % tuple(hist),
        f"  ひと続きに話す長さ 中央値      {np.median(runs):.2f} 秒",
    ]


def main(argv):
    use_utf8_output()
    if not argv:
        print(__doc__)
        return 2
    for path in argv[:2]:
        print("\n".join(report(path, measure(path))))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
