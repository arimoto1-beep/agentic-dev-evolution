"""使える声の測定(声を音にする前に測って候補を絞る)を確認する。

実際のエンジンは起動せず、読み(アクセント句)を返す偽エンジンで確かめる。
実機での測定は tests/test_voicevox_compat.py で確認する。
"""

import json
import math
import os

import pytest

from note2slides import voice_survey
from note2slides.speech import SpeechNotAvailableError
from note2slides.voice_survey import DEFAULT_SCREEN, Measurement, Screen, measure, measure_styles
from note2slides.voicevox import NEMO, Style


def mora(pitch_hz, length=0.1):
    """音程は自然対数の Hz で返ってくる(無声のモーラは 0)。"""
    return {
        "text": "あ",
        "consonant": None,
        "consonant_length": None,
        "vowel": "a",
        "vowel_length": length,
        "pitch": math.log(pitch_hz) if pitch_hz else 0.0,
    }


def phrases(pitches, length=0.1, pause=None):
    return [
        {
            "moras": [mora(p, length) for p in pitches],
            "accent": 1,
            "pause_mora": mora(0, pause) if pause else None,
        }
    ]


class FakeEngine:
    """スタイルごとに決まった読みを返すだけのエンジン。"""

    name = "fake"

    def __init__(self, styles, readings=None, broken=()):
        self.styles = list(styles)
        self.readings = readings or {}
        self.broken = set(broken)
        self.read_calls = []

    def list_styles(self):
        return self.styles

    def read(self, text, style, timeout=60.0):
        self.read_calls.append((text, style.name))
        if style.name in self.broken:
            raise SpeechNotAvailableError(f"読めません: {style.name}")
        return self.readings.get(style.name, phrases([200, 220, 210]))


def styles(*names):
    return [
        Style(speaker=name.split("/")[0], style=name.split("/")[1], id=i)
        for i, name in enumerate(names, start=1)
    ]


# ---------------------------------------------------------------------------
# 測定
# ---------------------------------------------------------------------------


def test_pitch_is_the_geometric_mean():
    """高さは対数のまま平均する(音の高さは比で感じるため)。"""
    pitch_hz, spread, rate, moras, voiced, seconds = measure(phrases([100, 400], length=0.25))

    assert pitch_hz == pytest.approx(200)  # 100 と 400 の相乗平均
    assert spread == pytest.approx(12.0)  # 平均から上下 1 オクターブ分ずれている
    assert (moras, voiced) == (2, 2)
    assert (rate, seconds) == (4.0, 0.5)


def test_pauses_are_not_counted_in_the_rate():
    """間の長さはこちらで決め直すので、話者の速さには混ぜない。"""
    _, _, rate, _, _, seconds = measure(phrases([200, 200], length=0.25, pause=1.0))

    assert (rate, seconds) == (4.0, 0.5)


def test_whispered_styles_have_no_pitch():
    """ささやき声は声帯が鳴らない。0 を平均に混ぜると値が壊れる。"""
    pitch_hz, spread, rate, _, voiced, _ = measure(phrases([0, 0, 0], length=0.1))

    assert (pitch_hz, spread, voiced) == (0.0, 0.0, 0)
    assert rate > 0  # 速さは測れる


def test_every_style_is_measured_and_one_failure_does_not_stop_the_rest():
    engine = FakeEngine(styles("A/ノーマル", "A/怒り", "B/ノーマル"), broken=["A/怒り"])

    results = measure_styles(engine, "文章")

    assert [r.voice for r in results] == ["A/ノーマル", "A/怒り", "B/ノーマル"]
    assert [r.ok for r in results] == [True, False, True]
    assert "読めません" in results[1].error


def test_the_first_style_of_each_speaker_is_the_default_one():
    """感情や演技のスタイルを外すために、話者ごとの先頭を覚えておく。"""
    engine = FakeEngine(styles("A/ノーマル", "A/怒り", "B/ふつう"))

    results = measure_styles(engine, "文章")

    assert [r.default_style for r in results] == [True, False, True]


def test_measurement_matches_the_credit_of_its_engine():
    engine = FakeEngine([Style("女声1", "ノーマル", 10005, edition=NEMO)])

    assert measure_styles(engine, "文章")[0].credit == "VOICEVOX Nemo"


# ---------------------------------------------------------------------------
# 絞り込み
# ---------------------------------------------------------------------------


def item(voice="A/ノーマル", pitch_hz=200.0, spread=2.0, rate=8.0, default=True, voiced=3):
    return Measurement(
        "voicevox", voice, 1, "", pitch_hz, spread, rate, 3, voiced, 0.4, default_style=default
    )


def test_screen_keeps_voices_inside_every_range():
    assert DEFAULT_SCREEN.passes(item())
    assert not DEFAULT_SCREEN.passes(item(pitch_hz=350.0))  # 高すぎる
    assert not DEFAULT_SCREEN.passes(item(spread=3.8))  # 抑揚が大きい
    assert not DEFAULT_SCREEN.passes(item(rate=10.0))  # 速すぎる


def test_screen_drops_acted_styles_but_keeps_reading_styles():
    """説明の読み上げに使わないスタイルまで残すと、聞く時間だけが増える。"""
    assert not DEFAULT_SCREEN.passes(item(voice="A/あまあま", default=False))
    assert DEFAULT_SCREEN.passes(item(voice="No.7/アナウンス", default=False))
    assert not DEFAULT_SCREEN.passes(item(voiced=0))  # ささやき声


def test_screen_can_be_changed():
    assert Screen(pitch_hz=(100.0, 150.0)).passes(item(pitch_hz=140.0))
    assert not Screen(pitch_hz=(100.0, 150.0)).passes(item(pitch_hz=200.0))


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def test_survey_writes_the_shortlist_and_the_reason_to_look_at_it(tmp_path):
    results = [
        item(voice="低い/ノーマル", pitch_hz=120.0),
        item(voice="高すぎる/ノーマル", pitch_hz=350.0),
        Measurement("voicevox", "壊れた/ノーマル", 3, "", error="読めません"),
    ]

    index_path, report_path = voice_survey.write_survey(
        str(tmp_path), results, text="文章", marked=["低い/ノーマル"]
    )

    report = open(report_path, encoding="utf-8").read()
    assert "低い/ノーマル" in report and "高すぎる/ノーマル" in report
    assert "壊れた/ノーマル" in report  # 測れなかったものも残す
    assert "人気や知名度ではない基準" in report

    index = json.load(open(index_path, encoding="utf-8"))
    assert index["text"] == "文章"
    assert index["screen"]["pitch_hz"] == [100.0, 260.0]
    passed = [v for v in index["voices"] if v.get("passes")]
    assert [v["voice"] for v in passed] == ["低い/ノーマル"]
    assert passed[0]["candidate"] is True


def test_survey_writes_next_to_the_comparison(tmp_path):
    voice_survey.write_survey(str(tmp_path / "out"), [item()])

    assert os.path.isfile(tmp_path / "out" / voice_survey.INDEX_NAME)
    assert os.path.isfile(tmp_path / "out" / voice_survey.REPORT_NAME)
