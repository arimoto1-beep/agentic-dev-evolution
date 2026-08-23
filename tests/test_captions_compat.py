"""実際の合成結果と、字幕の時刻の見積もりがどれだけ合っているかを確かめる。

字幕は音声を測らず、読み上げにかかる長さの目安(`captions.estimate_moras`)で
1 枚の中を按分する。目安が実際とかけ離れていれば、字幕は話しているところと
ずれて表示される。ここでは VOICEVOX 自身が決めた拍の長さ(実際に合成される音の
長さ)を正解として、見積もりとの差を測る。

VOICEVOX が入っていない環境ではスキップする。
"""

import pytest

from note2slides import captions as cap
from note2slides.reading import ReadingStyle, plan_reading
from note2slides.speech import SpeechPiece
from note2slides.voicevox import NEMO, VoicevoxEngine, is_installed

pytestmark = pytest.mark.slow

#: 1 枚の中で許すずれ(秒)。字幕が話し始めとずれて見えない範囲。
TOLERANCE = 0.6

SLIDES = [
    "トークンとは、AIが文章を扱うときの最小の単位です。"
    "日本語では、1文字が1トークンになることもあれば、2文字で1トークンになることもあります。"
    "長い文章を入れると、そのぶんだけトークンが増えていきます。",
    "この教材では、記事から動画を作るまでの流れを three steps で説明します。\n"
    "まず記事をスライドに分け、次にナレーションを付け、最後に動画として書き出します。\n"
    "それぞれの工程は、あとから作り直せるようになっています。",
    # 読点の多い文と、読点の無い文を並べたもの。読点で取る間を見積もりに
    # 入れないと、ここで 1 秒近くずれる。
    "ここは、たとえば、こういうふうに、読点の多い文です。\n"
    "こちらは読点をまったく含まない少し長めの文章になっているので読み上げに時間がかかります。\n"
    "最後にもう一度、短い文を置きます。",
]


def engine():
    if not is_installed(edition=NEMO):
        pytest.skip("VOICEVOX Nemo が入っていません")
    return VoicevoxEngine(edition=NEMO)


def spoken_seconds(engine, style_id, pieces):
    """読み上げ単位ごとに、実際に合成される音の長さ(秒)を求める。

    `voicevox.py` が合成に渡すのと同じ手順(区切りごとに読みを作り、つないで
    から音程を決める)で拍の長さを取り、単位ごとに足す。
    """
    groups = [engine._accent_phrases(piece.text, style_id, 60.0) for piece in pieces]
    phrases = engine._mora_data([p for group in groups for p in group], style_id, 60.0)
    seconds, at = [], 0
    for group in groups:
        total = 0.0
        for phrase in phrases[at : at + len(group)]:
            for mora in list(phrase["moras"]) + [phrase.get("pause_mora")]:
                if mora:
                    total += (mora.get("consonant_length") or 0.0) + (
                        mora.get("vowel_length") or 0.0
                    )
        at += len(group)
        seconds.append(total)
    return seconds


def test_the_estimated_times_match_the_real_synthesis():
    with engine() as voicevox:
        style = voicevox.pick_style(None)
        for text in SLIDES:
            plan = plan_reading(text, ReadingStyle())
            pieces = [SpeechPiece(u.text, u.pause_after) for u in plan.utterances]
            assert len(pieces) > 1

            real = spoken_seconds(voicevox, style.id, pieces)
            pauses = sum(piece.pause_after for piece in pieces[:-1])
            duration = plan.lead_silence + sum(real) + pauses + plan.tail_silence

            cues = cap.build_cues(
                [
                    cap.SlideCaption(
                        index=1,
                        start=0.0,
                        duration=duration,
                        lead_silence=plan.lead_silence,
                        tail_silence=plan.tail_silence,
                        pieces=pieces,
                    )
                ]
            )

            # 実際に各単位が始まる時刻(前の単位の長さと間を足したもの)。
            at = plan.lead_silence
            starts = []
            for piece, seconds in zip(pieces, real):
                starts.append(at)
                at += seconds + piece.pause_after

            # 字幕は 1 単位が複数に分かれることがあるので、単位の先頭だけを見る。
            shown = []
            for start in starts:
                shown.append(min(cues, key=lambda cue: abs(cue.start - start)).start)
            drift = max(abs(a - b) for a, b in zip(starts, shown))
            assert drift < TOLERANCE, f"{drift:.2f} 秒ずれています: {text[:20]}"
