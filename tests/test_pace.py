"""話す調子の合図(`〔声 ゆっくり〕` `〔声 速め〕` `〔声 ふつう〕`)。

1 本を同じ調子で読み続けると、山場の文もつなぎの文も同じ重さで聞こえる。
調子は文の並びの中に書き、合成へ渡す拍の長さと高さを書き換えて実現する。
崩れると困るのは次のところ:

* 合図が読み上げられないこと
* 書いた文から効き、その画面の終わりか、次の〔声 …〕まで続くこと
* 調子を変えても、画面を変える時刻(区切りの始まり)が音声と合っていること
* 合図を書いていない原稿が、これまでと同じに読まれること
"""

import pytest

from note2slides.reading import PACES, plan_reading, strip_cues
from note2slides.voicevox import _apply_paces, piece_offsets


def paces(plan):
    return [u.pace for u in plan.utterances]


def test_voice_cue_is_not_spoken_and_applies_from_the_sentence_it_precedes():
    plan = plan_reading("前置きです。〔声 ゆっくり〕ここが問いです。\n次の行です。")
    assert [u.text for u in plan.utterances] == ["前置きです。", "ここが問いです。", "次の行です。"]
    assert paces(plan) == ["", "ゆっくり", "ゆっくり"]  # その画面の終わりまで続く


def test_voice_cue_can_be_turned_back_to_normal():
    plan = plan_reading("〔声 速め〕一つ目。二つ目。〔声 ふつう〕まとめです。")
    assert paces(plan) == ["速め", "速め", ""]


def test_voice_cue_does_not_carry_over_to_the_next_screen():
    plan_reading("〔声 ゆっくり〕問いです。")
    assert paces(plan_reading("次の画面です。")) == [""]


def test_voice_cue_works_together_with_step_and_pause_cues():
    plan = plan_reading("前置き。〔間 0.5〕〔声 ゆっくり〕〔3〕問いです。〔間 1.0〕〔声 ふつう〕〔4〕続きです。")
    assert [(u.cue, u.pace) for u in plan.utterances] == [("", ""), ("3", "ゆっくり"), ("4", "")]
    assert plan.utterances[0].pause_after == pytest.approx(0.35 + 0.5)
    assert plan.utterances[1].pause_after == pytest.approx(0.35 + 1.0)


def test_unknown_voice_name_stays_in_the_text():
    # 知らない名前を黙って捨てると、書いた人は効いていないことに気付けない。
    plan = plan_reading("〔声 おおげさ〕問いです。")
    assert "声" in plan.utterances[0].text


def test_text_without_voice_cues_is_planned_as_before():
    assert paces(plan_reading("一つ目です。二つ目です。")) == ["", ""]
    assert plan_reading("一つ目です。").to_dict()["utterances"] == [{"text": "一つ目です。", "pause_after": 0.0}]


def test_strip_cues_removes_voice_cues():
    assert strip_cues("〔声 ゆっくり〕問いです。") == "問いです。"


# ---------------------------------------------------------------------------
# 合成へ渡す読みを書き換える
# ---------------------------------------------------------------------------


def phrase(*moras):
    return {
        "moras": [
            {"consonant_length": 0.05, "vowel_length": length, "pitch": pitch} for length, pitch in moras
        ],
        "pause_mora": None,
    }


def test_slow_pace_lengthens_the_moras_of_its_piece_only():
    phrases = [phrase((0.10, 5.0), (0.10, 5.2)), phrase((0.10, 5.0)), phrase((0.10, 5.1))]
    _apply_paces(phrases, [1, 1, 1], ["", "ゆっくり", ""])
    slow = PACES["ゆっくり"][0]
    assert phrases[0]["moras"][0]["vowel_length"] == pytest.approx(0.10)
    assert phrases[1]["moras"][0]["vowel_length"] == pytest.approx(0.10 * slow)
    assert phrases[1]["moras"][0]["consonant_length"] == pytest.approx(0.05 * slow)
    assert phrases[2]["moras"][0]["vowel_length"] == pytest.approx(0.10)


def test_slow_pace_widens_the_pitch_movement_around_its_own_average():
    phrases = [phrase((0.1, 5.0), (0.1, 5.4), (0.1, 0.0))]  # 高さ 0 は無声(動かさない)
    _apply_paces(phrases, [1], ["ゆっくり"])
    lilt = PACES["ゆっくり"][1]
    pitches = [m["pitch"] for m in phrases[0]["moras"]]
    assert pitches[0] == pytest.approx(5.2 - 0.2 * lilt)
    assert pitches[1] == pytest.approx(5.2 + 0.2 * lilt)
    assert pitches[2] == 0.0
    assert (pitches[0] + pitches[1]) / 2 == pytest.approx(5.2)  # 高さそのものは変えない


def test_fast_pace_shortens_and_keeps_the_pitch():
    phrases = [phrase((0.2, 5.0), (0.2, 5.4))]
    _apply_paces(phrases, [1], ["速め"])
    assert phrases[0]["moras"][0]["vowel_length"] == pytest.approx(0.2 * PACES["速め"][0])
    assert [m["pitch"] for m in phrases[0]["moras"]] == pytest.approx([5.0, 5.4])


def test_screen_changes_stay_on_the_sentence_after_the_pace_changes_the_lengths():
    # 区切りの始まりは、書き換えたあとの長さから求める(画面を変える時刻に使う)。
    phrases = [phrase((0.25, 5.0)), phrase((0.25, 5.0)), phrase((0.25, 5.0))]
    _apply_paces(phrases, [1, 1, 1], ["ゆっくり", "", ""])
    offsets = piece_offsets(phrases, [1, 1, 1], speed=1.0)
    first = 0.30 * PACES["ゆっくり"][0]
    assert offsets == pytest.approx([0.0, first, first + 0.30])


def test_paces_are_kept_small():
    # 大きく変えると、合成音声は別人のように聞こえる。耳で確かめていない幅には広げない。
    for slow, lilt in PACES.values():
        assert 0.85 <= slow <= 1.2 and 0.9 <= lilt <= 1.2
