"""画面の動きと声が重なっているかを測る。**見たことにも、聞いたことにもならない。**

    .venv/Scripts/python.exe tools/voice_check/av_check.py シナリオ.md 音声の場所 [--min 1.0]

シナリオの画面に置いた絵(`figures/名前.png`)の隣に、段へ移る動きの連番(`名前@3/000.png` …)が
あれば、その段の合図ごとに次を出す:

* 動きの始まり(合図の時刻 - 0.2 秒。画面を変える時刻と同じ)と長さ
* 動きの最中に声が出ている割合
* 動きが終わってから、次に声が出るまでの無音
* 次の合図が、動きが終わる前に来ていないか(来ると、動きが途中で切れる)

Gen38 で見つけた形: 合図を文の頭に書くと、文を読むあいだに動きが終わり、そのあとに書いた
`〔間〕` は止まった絵の前の無音になる(Gen37 の 4 か所)。動きを言う語に合図を移すと、
動きが間の中まで続く。どちらが良いかは場面による。ここで分かるのは重なり方だけ。
"""

import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from note2slides.console import use_utf8_output  # noqa: E402
from note2slides.waveform import read_wav  # noqa: E402

FPS = 30  # 動きの連番の速さ(sketch.save_steps と同じ)
LEAD = 0.2  # 合図の文より先に画面を変える時間(staging と同じ)


def figures_of(scenario):
    """画面ごとの絵の名前(`## ` の順。絵が無い画面は None)。"""
    names = []
    for block in re.split(r"^## ", open(scenario, encoding="utf-8").read(), flags=re.M)[1:]:
        matched = re.search(r"!\[\]\(([^)]+)\.png\)", block)
        names.append(matched.group(1) if matched else None)
    return names


def voiced(path):
    wav = read_wav(path)
    x = wav.samples if wav.samples.ndim == 1 else wav.samples.mean(axis=1)
    win = int(0.01 * wav.sample_rate)
    env = np.array([np.abs(x[i:i + win]).max() for i in range(0, len(x) - win, win)])
    return env > 10 ** (-40 / 20)


def check(scenario, audio_dir, minimum=1.0):
    base = os.path.dirname(os.path.abspath(scenario))
    names = figures_of(scenario)
    data = json.load(open(os.path.join(audio_dir, "narration.json"), encoding="utf-8"))
    rows = []
    for clip in data["clips"]:
        name = names[clip["index"] - 1] if clip["index"] - 1 < len(names) else None
        cues = clip.get("cues") or []
        if not name or not cues:
            continue
        v = voiced(os.path.join(audio_dir, clip["file"]))
        for i, cue in enumerate(cues):
            folder = os.path.join(base, f"{name}@{cue['step']}")
            if not os.path.isdir(folder):
                continue
            length = len(os.listdir(folder)) / FPS
            if length < minimum:
                continue
            start = max(0.0, cue["at"] - LEAD)
            end = start + length
            during = v[int(start * 100):int(end * 100)]
            after = v[int(end * 100):]
            next_at = cues[i + 1]["at"] - LEAD if i + 1 < len(cues) else clip["duration"]
            rows.append({
                "slide": clip["index"], "figure": os.path.basename(name), "step": cue["step"],
                "start": round(start, 2), "length": round(length, 2),
                "voiced": round(float(during.mean()) if len(during) else 0.0, 2),
                "silence_after": round(float(np.argmax(after)) / 100, 2) if after.any() else None,
                "cut": next_at < end,
            })
    return rows


def main(argv):
    use_utf8_output()
    if len(argv) < 2:
        print(__doc__)
        return 2
    minimum = float(argv[argv.index("--min") + 1]) if "--min" in argv else 1.0
    rows = check(argv[0], argv[1], minimum)
    print(f"{'画面':>4} {'絵':<18}{'段':>3} {'始まり':>7} {'長さ':>5} {'動き中の声':>9} {'終わり→次の声':>12}")
    for r in rows:
        after = f"{r['silence_after']:.2f}s" if r["silence_after"] is not None else "-"
        mark = "  動きが途中で切れる" if r["cut"] else ""
        print(f"{r['slide']:>4} {r['figure']:<18}{r['step']:>3} {r['start']:7.2f} {r['length']:5.2f} "
              f"{r['voiced'] * 100:8.0f}% {after:>12}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
