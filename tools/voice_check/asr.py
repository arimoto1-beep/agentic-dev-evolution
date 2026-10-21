"""音声認識(Whisper large-v3)で、ナレーションが何と聞き取られるかを見る。
**聞いたことにはならない。** 使い方と、確かめた範囲は README.md。

    <評価用の環境>/python tools/voice_check/asr.py 音声の場所 [比べる音声の場所]

narration.json の原稿(画面に出す文字)と、聞き取った文字を比べる:

* 文字の誤り率(CER。句読点と空白を除く): 読み違い・崩れた音・聞き取りにくさの目安
* 聞き取った文字に打たれた句点の数 / 原稿の文の数: 文の終わりが声から伝わっているかの、
  ごく粗い目安(Whisper は句点を「全部打つか、まったく打たないか」に振れやすい)
* 原稿に無い読点が入った名前(「ド、ディオン」など): 語の途中で止まっている疑い
"""

import json
import os
import re
import sys
import unicodedata

import soundfile as sf
import torch
import torchaudio
from faster_whisper import WhisperModel

_DROP = re.compile(r"[\s、。,.，．！？!?「」『』()（）・〔〕\[\]]")
_model = None


def model():
    global _model
    if _model is None:
        cuda = torch.cuda.is_available()
        _model = WhisperModel("large-v3", device="cuda" if cuda else "cpu",
                              compute_type="float16" if cuda else "int8")
    return _model


def norm(text):
    return _DROP.sub("", unicodedata.normalize("NFKC", text))


def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / max(1, len(r))


def transcribe(path):
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    # faster-whisper にファイル名を渡すと PyAV で読む。版によって読めないので、配列で渡す。
    x16 = torchaudio.functional.resample(torch.from_numpy(x), sr, 16000).numpy()
    segments, _ = model().transcribe(x16, language="ja", beam_size=5,
                                     condition_on_previous_text=False, vad_filter=False)
    return "".join(s.text for s in segments)


def run(folder):
    data = json.load(open(os.path.join(folder, "narration.json"), encoding="utf-8"))
    rows = []
    for clip in data["clips"]:
        if clip.get("silent"):
            continue
        ref = "".join(p["text"] for p in clip.get("pieces") or [])
        hyp = transcribe(os.path.join(folder, clip["file"]))
        rows.append({"index": clip["index"], "ref": ref, "hyp": hyp, "cer": cer(ref, hyp),
                     "periods": hyp.count("。") + hyp.count("?") + hyp.count("？"),
                     "sentences": sum(ref.count(c) for c in "。？?！!")})
    return rows


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        print(__doc__)
        return 2
    for folder in argv[:2]:
        rows = run(folder)
        total = sum(len(norm(r["ref"])) for r in rows)
        weighted = sum(r["cer"] * len(norm(r["ref"])) for r in rows) / max(1, total)
        periods = sum(r["periods"] for r in rows)
        sentences = sum(r["sentences"] for r in rows)
        print(f"{folder}: 文字の誤り率 {weighted:.3f} / 句点 {periods} / 原稿の文 {sentences}")
        # 比べる相手(前の版)のフォルダには書き込まない。書き起こしは画面に出すだけ。
        for r in rows:
            print(f"  [{r['index']:>2}] CER {r['cer']:.2f}  {r['hyp']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
