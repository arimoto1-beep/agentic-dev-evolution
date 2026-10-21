"""UTMOS(人が付けた自然さの点数を真似るように学習したモデル)で、ナレーションを採点する。
**聞いたことにはならない。** 使い方と、確かめた範囲は README.md。

    <評価用の環境>/python tools/voice_check/utmos.py 音声の場所 [比べる音声の場所]

長い音声は、0.25 秒以上の無音のところで 4 - 10 秒の塊に切って採点し、長さで重み付けて平均する
(このモデルは数秒の発話で学習している)。同じ番号のファイルどうしを並べ、差を出す。
"""

import glob
import os
import sys

import numpy as np
import soundfile as sf
import torch
import torchaudio

_predictor = None


def predictor():
    global _predictor
    if _predictor is None:
        _predictor = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True)
        _predictor = _predictor.to("cuda" if torch.cuda.is_available() else "cpu").eval()
    return _predictor


def chunks(x, sr, lo=4.0, hi=10.0):
    win = int(0.02 * sr)
    env = np.array([np.abs(x[i:i + win]).max() for i in range(0, len(x) - win, win)])
    quiet = env < 10 ** (-40 / 20)
    cuts, run = [0], 0
    for j, q in enumerate(quiet):
        run = run + 1 if q else 0
        if run == int(0.25 / 0.02):
            t = (j - run // 2) * win
            if (t - cuts[-1]) / sr >= lo:
                cuts.append(t)
        if (j * win - cuts[-1]) / sr > hi and run >= 3:
            cuts.append(j * win)
    cuts.append(len(x))
    out = [x[a:b] for a, b in zip(cuts, cuts[1:]) if (b - a) / sr >= 1.0]
    return out or [x]


def score(path):
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    x16 = torchaudio.functional.resample(torch.from_numpy(x), sr, 16000).numpy()
    model = predictor()
    device = next(model.parameters()).device
    scores, weights = [], []
    with torch.no_grad():
        for c in chunks(x16, 16000):
            scores.append(float(model(torch.from_numpy(c).unsqueeze(0).to(device), 16000).item()))
            weights.append(len(c))
    return float(np.average(scores, weights=weights))


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        print(__doc__)
        return 2
    folders = argv[:2]
    files = [sorted(glob.glob(os.path.join(f, "narration_*.wav"))) for f in folders]
    names = [os.path.basename(p) for p in files[0]]
    table = [[score(os.path.join(f, n)) if os.path.exists(os.path.join(f, n)) else None for f in folders]
             for n in names]
    for name, row in zip(names, table):
        cells = "  ".join(f"{s:.3f}" if s is not None else "  -  " for s in row)
        diff = f"  {row[1] - row[0]:+.3f}" if len(row) == 2 and None not in row else ""
        print(f"{name}  {cells}{diff}")
    for i, folder in enumerate(folders):
        values = [row[i] for row in table if row[i] is not None]
        print(f"平均 {np.mean(values):.3f}  {folder}")
    if len(folders) == 2:
        pairs = [(a, b) for a, b in table if a is not None and b is not None]
        better = sum(b > a for a, b in pairs)
        print(f"後者が高い: {better} / {len(pairs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
