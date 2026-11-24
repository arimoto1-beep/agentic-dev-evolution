"""声に合わせる動きと繰り返しの崩れを探す。**見たことにはならない。**

    .venv/Scripts/python.exe tools/motion_check.py 絵のフォルダ 動画の一覧.json

* 繰り返し(`図@n/loop/`)の、最後の絵から最初の絵への跳び。止まったコマを見ても分からない
  (Gen39 で、行程の途中だけを繰り返してピストンが飛ぶ場面を 5 か所見つけた)
* 広げた動き(`fit`)の倍率。1 倍を超えると同じ絵が続き、動きが粗くなる

動画の一覧は、動画と一緒に書き出される `名前_video.json`。
"""

import glob
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from note2slides.console import use_utf8_output  # noqa: E402

JUMP = 4.0  # 普段の 1 コマの変化の何倍で「飛ぶ」とするか
VISIBLE = 0.2  # これより小さい変化は、倍率が大きくても見えない
ROUGH = 1.0  # 広げる倍率がこれを超えると知らせる


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("L").resize((480, 270)), dtype=np.int16)


def difference(first, second):
    return float(np.abs(pixels(first) - pixels(second)).mean())


def seams(folder):
    """継ぎ目が飛ぶ繰り返しを(場所, 継ぎ目の変化, 普段の変化)で返す。"""
    jumps = []
    directories = sorted(glob.glob(os.path.join(folder, "*@*", "loop")))
    for directory in directories:
        frames = sorted(glob.glob(os.path.join(directory, "*.png")))
        if len(frames) < 2:
            continue
        stride = max(1, len(frames) // 12)
        typical = max(np.median([difference(frames[i], frames[i + 1]) for i in range(0, len(frames) - 1, stride)]), 0.01)
        seam = difference(frames[-1], frames[0])
        if seam > typical * JUMP and seam > VISIBLE:
            jumps.append((os.path.relpath(directory, folder), round(seam, 2), round(float(typical), 2)))
    return len(directories), jumps


def stretches(listing):
    """広げた動きの倍率を、大きい順に(倍率, 画面, 合図)で返す。"""
    with open(listing, encoding="utf-8") as f:
        data = json.load(f)
    found = []
    for slide in data["slides"]:
        shots = slide.get("shots") or []
        times = [shot["at"] for shot in shots] + [slide["duration"]]
        for shot, start, end in zip(shots, times, times[1:]):
            if shot.get("fit"):
                found.append((round((end - start) * 30 / shot["motion_frames"], 2), slide["index"], shot["cue"]))
    return sorted(found, reverse=True)


def main(argv):
    use_utf8_output()
    if len(argv) != 2:
        print(__doc__)
        return 2
    count, jumps = seams(argv[0])
    for place, seam, typical in jumps:
        print(f"継ぎ目が飛ぶ: {place}(継ぎ目 {seam} / 普段 {typical})")
    print(f"繰り返し {count} 個 / 継ぎ目が飛ぶ {len(jumps)} 個")
    found = stretches(argv[1])
    rough = [entry for entry in found if entry[0] > ROUGH]
    for ratio, slide, cue in rough:
        print(f"粗くなる: 画面 {slide} の〔{cue}〕 {ratio} 倍に広げている(枚数を増やす)")
    print(f"広げた動き {len(found)} 個 / 1 倍を超える {len(rough)} 個")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
