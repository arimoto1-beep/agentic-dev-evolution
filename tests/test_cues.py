"""原稿の合図(`〔2〕` `〔全体〕` `〔間 1.5〕`)。

合図は「この文から画面を次の段へ」「ここで一拍」を、文の並びの中に書く印。
崩れると困るのは次のところ:

* 合図が読み上げられないこと(字幕にも出ないこと)
* 合図が、直後の文に付くこと(ずれると、別の文で画面が変わる)
* 合図を書いていない原稿が、これまでと同じに読まれること
* 合図が何秒目にあたるかが、合成した音声の長さと合っていること
"""

import pytest

from note2slides import audio
from note2slides.reading import ReadingStyle, cue_names, plan_reading, strip_cues
from note2slides.voicevox import piece_offsets


def texts(plan):
    return [u.text for u in plan.utterances]


def cues(plan):
    return [u.cue for u in plan.utterances]


def test_cues_are_not_spoken_and_attach_to_the_next_sentence():
    plan = plan_reading("まず全体です。〔1〕車体です。〔2〕タイヤです。\n〔全体〕まとめです。")
    assert texts(plan) == ["まず全体です。", "車体です。", "タイヤです。", "まとめです。"]
    assert cues(plan) == ["", "1", "2", "全体"]
    assert "〔" not in plan.reading


def test_full_width_digits_are_accepted():
    plan = plan_reading("〔３〕三つ目です。")
    assert cues(plan) == ["3"]


def test_other_brackets_stay_in_the_text():
    """合図の形をしていない〔…〕は、これまでどおり本文として読む。"""
    plan = plan_reading("これは〔注〕です。")
    assert texts(plan) == ["これは〔注〕です。"]
    assert cues(plan) == [""]


def test_text_without_cues_is_planned_as_before():
    text = "一つ目。二つ目。\n別の行です。"
    style = ReadingStyle(sentence_pause=0.3, line_pause=0.6, tail_silence=0.7)
    plan = plan_reading(text, style)
    assert texts(plan) == ["一つ目。", "二つ目。", "別の行です。"]
    assert [u.pause_after for u in plan.utterances] == [0.3, 0.6, 0.0]
    assert plan.tail_silence == 0.7


def test_cue_does_not_change_the_pauses_between_sentences():
    style = ReadingStyle(sentence_pause=0.3, line_pause=0.6)
    plain = plan_reading("一つ目。二つ目。\n別の行です。", style)
    cued = plan_reading("〔1〕一つ目。〔2〕二つ目。\n〔3〕別の行です。", style)
    assert texts(cued) == texts(plain)
    assert [u.pause_after for u in cued.utterances] == [u.pause_after for u in plain.utterances]


def test_cue_in_the_middle_of_a_sentence_does_not_end_the_sentence():
    """文の途中の合図では、句点を補わず、間も置かない(続けて読む)。"""
    plan = plan_reading("一行目から始めて、〔2〕二つ目を調べます。")
    assert texts(plan) == ["一行目から始めて、", "二つ目を調べます。"]
    assert plan.utterances[0].pause_after == 0.0
    assert cues(plan) == ["", "2"]


def test_pause_cue_adds_silence_after_the_previous_sentence():
    style = ReadingStyle(sentence_pause=0.3, lead_silence=0.3, tail_silence=0.7)
    plan = plan_reading("問いです。〔間 1.5〕答えです。", style)
    assert plan.utterances[0].pause_after == pytest.approx(1.8)


def test_pause_cue_at_the_ends_moves_to_the_silence_around_the_slide():
    style = ReadingStyle(lead_silence=0.3, tail_silence=0.7)
    plan = plan_reading("〔間 1〕はじめます。〔間 2〕", style)
    assert plan.lead_silence == pytest.approx(1.3)
    assert plan.tail_silence == pytest.approx(2.7)
    assert plan.utterances[-1].pause_after == 0.0  # 最後の区切りの間は 0、という約束


def test_cue_names_and_strip_cues():
    text = "前置き。〔1〕一つ目。〔間 0.5〕〔全体〕まとめ。"
    assert cue_names(text) == ["1", "全体"]
    assert strip_cues(text) == "前置き。一つ目。まとめ。"


# ---------------------------------------------------------------------------
# 合図が何秒目にあたるか
# ---------------------------------------------------------------------------


def phrase(*lengths, pause=None):
    moras = [{"consonant_length": 0.0, "vowel_length": length} for length in lengths]
    return {"moras": moras, "pause_mora": {"vowel_length": pause} if pause else None}


def test_piece_offsets_add_up_what_comes_before():
    phrases = [phrase(0.2, 0.3, pause=0.5), phrase(0.4), phrase(0.1, pause=0.6), phrase(0.2)]
    # 区切り 1 = 句 2 つ、区切り 2 = 句 1 つ、区切り 3 = 句 1 つ
    offsets = piece_offsets(phrases, [2, 1, 1], speed=1.0, padding=0.3)
    assert offsets == pytest.approx([0.3, 0.3 + 1.4, 0.3 + 1.4 + 0.7])


def test_piece_offsets_follow_the_speed():
    phrases = [phrase(0.4), phrase(0.4)]
    assert piece_offsets(phrases, [1, 1], speed=2.0) == pytest.approx([0.0, 0.2])


def test_piece_without_reading_shares_the_time_of_the_next_piece():
    phrases = [phrase(0.5), phrase(0.5)]
    assert piece_offsets(phrases, [1, 0, 1], speed=1.0) == pytest.approx([0.0, 0.5, 0.5])


def test_cue_times_use_the_offsets_reported_by_the_engine():
    plan = plan_reading("前置きです。〔1〕一つ目です。〔2〕二つ目です。", ReadingStyle(lead_silence=0.3))
    # エンジンの時刻は、削る前の音声のもの。先頭の余白 0.28 秒が削られた。
    found = audio._cue_times(plan, 10.0, [0.3, 2.3, 4.8], trimmed=0.28)
    assert found == [{"step": "1", "at": 2.32}, {"step": "2", "at": 4.82}]


def test_cue_on_the_first_sentence_is_marked_as_the_opening():
    plan = plan_reading("〔1〕一つ目です。〔2〕二つ目です。")
    found = audio._cue_times(plan, 6.0, [0.3, 2.3], trimmed=0.3)
    assert found[0]["first"] is True
    assert "first" not in found[1]


def test_cue_times_are_estimated_when_the_engine_does_not_report_them():
    """時刻を返さないエンジンでも、合図は順番どおり、音声の長さの中に収まる。"""
    plan = plan_reading("前置きです。〔1〕一つ目です。〔2〕二つ目の、少し長い文です。")
    found = audio._cue_times(plan, 9.0, None, trimmed=0.0)
    times = [cue["at"] for cue in found]
    assert [cue["step"] for cue in found] == ["1", "2"]
    assert plan.lead_silence < times[0] < times[1] < 9.0 - plan.tail_silence


def test_no_cues_means_no_cue_times():
    plan = plan_reading("合図の無い原稿です。")
    assert audio._cue_times(plan, 3.0, [0.3], trimmed=0.3) == []
