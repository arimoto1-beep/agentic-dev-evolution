"""文の形と間の並び、話す役割、強調(gen38)。

gen37 までの読み上げは、1 枚分を「切れ目の無い 1 本の文」として音程を決めさせ、
あとから間を差し込んでいた。そのため文末が下がらず、次の文が続きとして低く始まり、
読点の間(エンジンが置く 0.39 秒)が文の間(0.35 秒)より長かった。崩れると困るのは次のところ:

* 切れ目(文の終わり・読点で終わる区切り)を、音程を決める **前に** エンジンへ伝えること
* 間が「読点 < 文 < 段落」の順に長いこと
* 文の途中の合図で、読点の間が消えないこと
* 片仮名の語の「・」(ド・ディオン)で止まらないこと。字幕は書いたとおりのまま
* 役割(〔声 問い〕など)と強調(〔強 …〕)が読み上げられず、字幕には語だけが残ること
* 強調する語が見つからないときは、黙って捨てずに知らせること
"""

import json
import math
import urllib.parse

import pytest

from note2slides import voicevox
from note2slides.pronunciation import inspect_readings
from note2slides.reading import PACES, STRESS, ReadingStyle, plan_reading, strip_cues
from note2slides.speech import SpeechJob, SpeechPiece
from note2slides.voicevox import VoicevoxEngine

# ---------------------------------------------------------------------------
# 原稿の読み取り
# ---------------------------------------------------------------------------


def test_pauses_grow_from_clause_to_sentence_to_paragraph():
    style = ReadingStyle()
    assert style.clause_pause < style.sentence_pause < style.line_pause
    # エンジンが読点に置く間(縮めたあとの上限)も、文の間より短い
    assert voicevox.INNER_PAUSE_RANGE[1] < style.sentence_pause


def test_stress_cue_is_spoken_as_its_word_and_recorded():
    plan = plan_reading("片方のはしだけが〔強 回ります〕。")
    [utterance] = plan.utterances
    assert utterance.text == "片方のはしだけが回ります。"  # 字幕には語だけが残る
    assert utterance.stress == ("回ります",)


def test_stress_cue_accepts_a_full_width_space():
    # 日本語のあいだの空白は読み上げの前に詰めるので、その前に読み取る必要がある。
    assert plan_reading("〔強　ねじれます〕。").utterances[0].stress == ("ねじれます",)


def test_stress_follows_the_reading_dictionary():
    plan = plan_reading("〔強 Honda〕の説明です。", ReadingStyle(dictionary={"Honda": "ホンダ"}))
    [utterance] = plan.utterances
    assert utterance.text == "Hondaの説明です。"
    assert utterance.stress == ("ホンダ",)  # 合成へ渡す文字の中で探すので、読みの側


def test_stress_inside_a_sentence_keeps_the_sentence_whole():
    plan = plan_reading("左は〔強 一緒に動きます〕。右は〔強 残ります〕。")
    assert [u.text for u in plan.utterances] == ["左は一緒に動きます。", "右は残ります。"]
    assert [u.stress for u in plan.utterances] == [("一緒に動きます",), ("残ります",)]


def test_a_stress_cue_without_a_word_is_read_as_text():
    # 黙って捨てると、書いた人は効いていないことに気付けない(下読みで見つかる)。
    assert "強" in plan_reading("〔強〕です。").utterances[0].text


def test_strip_cues_keeps_the_stressed_word():
    assert strip_cues("〔声 問い〕〔2〕それは〔強 ねじれる〕のか。") == "それはねじれるのか。"


@pytest.mark.parametrize("role", ["問い", "発見", "結論", "補足", "列挙"])
def test_role_cues_are_not_spoken(role):
    plan = plan_reading(f"前置きです。〔声 {role}〕本題です。")
    assert [u.text for u in plan.utterances] == ["前置きです。", "本題です。"]
    assert [u.pace for u in plan.utterances] == ["", role]


def test_roles_stay_small():
    # 耳で確かめていない幅には広げない(大きく変えると、合成音声は別人に聞こえる)。
    for slow, lilt, lift in PACES.values():
        assert 0.9 <= slow <= 1.15
        assert 0.8 <= lilt <= 1.2
        assert -1.5 <= lift <= 1.5
    lift, slow, after = STRESS
    assert 0 < lift <= 3 and 1.0 <= slow <= 1.1 and 0.7 <= after <= 1.0


def test_conclusion_is_lower_and_slower_than_a_question():
    assert PACES["結論"][2] < 0 < PACES["問い"][2]
    assert PACES["結論"][0] > 1.0 and PACES["問い"][0] > 1.0


def test_dots_are_handed_to_the_engine_as_written():
    # 「・」を詰めると 1 語として読まれ、アクセントが変わる。止まる長さは合成の側で縮める。
    for text in ("ド・ディオン式です。", "東京・大阪です。", "A・Bです。"):
        assert plan_reading(text).utterances[0].to_speak == text


def test_reading_report_shows_the_voice_and_stress():
    plan = plan_reading("〔声 結論〕どちらにも〔強 倒さない〕。")
    report = inspect_readings({1: plan}, read_kana=lambda text: "カナ")
    [line] = report.slides[0].lines
    assert line.pace == "結論" and line.stress == ("倒さない",)
    data = report.to_dict()["slides"][0]["lines"][0]
    assert data["pace"] == "結論" and data["stress"] == ["倒さない"]


# ---------------------------------------------------------------------------
# VOICEVOX へ渡す読み
# ---------------------------------------------------------------------------


def mora(text, pitch=5.0, length=0.1):
    return {"text": text, "consonant": None, "consonant_length": None, "vowel": "a",
            "vowel_length": length, "pitch": pitch}


class Engine:
    """本物に近い差し替え。読み = 1 文字 1 拍で、読点は直前のアクセント句の間になる。

    /mora_data は本物と同じく、間(pause_mora)を残したまま長さを決め直す(ここでは 0.4 秒)。
    """

    ENGINE_PAUSE = 0.4

    def __init__(self):
        self.mora_data_calls = []
        self.payloads = []

    def __call__(self, url, data=None, timeout=60.0):
        if "/version" in url:
            return b'"0.24.0"'
        if "/speakers" in url:
            return json.dumps([{"name": "男声3", "speaker_uuid": "u",
                                "styles": [{"name": "ノーマル", "id": 1, "type": "talk"}]}]).encode()
        if "/initialize_speaker" in url:
            return b""
        if "/accent_phrases" in url:
            text = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["text"][0]
            phrases = []
            for char in text:
                if char in "、。・":
                    if phrases and char in "、・":  # 本物も「・」を読点と同じ印で返す
                        phrases[-1]["pause_mora"] = mora("、", 0.0, 0.33) | {"vowel": "pau"}
                    continue
                phrases.append({"moras": [mora(char)], "accent": 1, "pause_mora": None,
                                "is_interrogative": False})
            # 末尾の読点は、本物と同じく落ちる
            if phrases and text.endswith("、"):
                phrases[-1]["pause_mora"] = None
            return json.dumps(phrases).encode()
        if "/mora_data" in url:
            phrases = json.loads(data.decode("utf-8"))
            self.mora_data_calls.append(json.loads(json.dumps(phrases)))
            for phrase in phrases:
                if phrase.get("pause_mora"):
                    phrase["pause_mora"]["vowel_length"] = self.ENGINE_PAUSE
            return json.dumps(phrases).encode()
        if "/synthesis" in url:
            self.payloads.append(json.loads(data.decode("utf-8")))
            import io
            import wave
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(48000)
                writer.writeframes(b"\0\0" * 4800)
            return buffer.getvalue()
        raise AssertionError(url)


@pytest.fixture
def http(monkeypatch):
    fake = Engine()
    monkeypatch.setattr(voicevox, "_request", fake)
    return fake


def synthesize(tmp_path, pieces):
    engine = VoicevoxEngine(url="http://127.0.0.1:50121", autostart=False)
    job = SpeechJob(index=1, pieces=pieces, out_path=str(tmp_path / "part.wav"), slide=1)
    report = engine.synthesize([job], str(tmp_path / "work"), timeout=30)
    return job, report


def pauses(phrases):
    return [(p["pause_mora"]["vowel_length"] if p.get("pause_mora") else None) for p in phrases]


def test_sentence_breaks_are_told_before_the_pitch_is_decided(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("あい。", 0.6), SpeechPiece("うえ。")])
    sent = http.mora_data_calls[0]
    # 文の終わり(2 つ目のアクセント句)に、音程を決める前から間がある
    assert pauses(sent)[1] is not None
    assert pauses(sent)[3] is None  # 最後の文のあとには置かない


def test_sentence_pause_is_placed_exactly_after_the_pitch(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("あい。", 0.6), SpeechPiece("うえ。")])
    assert pauses(http.payloads[0]["accent_phrases"]) == [None, pytest.approx(0.6), None, None]


def test_inner_pauses_are_shortened_below_the_sentence_pause(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("あ、い。", 0.6), SpeechPiece("う。")])
    inner = pauses(http.payloads[0]["accent_phrases"])[0]
    low, high = voicevox.INNER_PAUSE_RANGE
    assert inner == pytest.approx(min(high, max(low, Engine.ENGINE_PAUSE * voicevox.INNER_PAUSE_SCALE)))
    assert inner < 0.6


def test_a_comma_before_a_mid_sentence_cue_keeps_its_pause(tmp_path, http):
    # 「まず、〔1〕教科書に…」: 合図で区切っても、読点の間は残す(gen37 までは消えていた)
    synthesize(tmp_path, [SpeechPiece("まず、", 0.0), SpeechPiece("みる。")])
    assert pauses(http.mora_data_calls[0])[1] is not None  # 音程を決める前に伝える
    kept = pauses(http.payloads[0]["accent_phrases"])[1]
    assert kept is not None and kept < 0.6


def test_a_katakana_name_is_not_cut_at_its_dot(tmp_path, http):
    # ド・ディオン: 句は 2 つのまま(アクセントを保つ)、止まる長さだけ縮める。読点はそのまま。
    synthesize(tmp_path, [SpeechPiece("ド・ディ、ア。")])
    sent = pauses(http.payloads[0]["accent_phrases"])
    assert sent[0] == pytest.approx(voicevox.WORD_DOT_PAUSE)
    assert sent[2] >= voicevox.INNER_PAUSE_RANGE[0]  # 「ディ」のあとの読点


def test_a_dot_between_other_words_keeps_its_pause(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("東・西。")])
    assert pauses(http.payloads[0]["accent_phrases"])[0] >= voicevox.INNER_PAUSE_RANGE[0]


def test_a_split_without_a_comma_stays_continuous(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("まず", 0.0), SpeechPiece("みる。")])
    assert pauses(http.payloads[0]["accent_phrases"]) == [None, None, None, None]


def test_screen_changes_stay_on_their_sentence_with_the_new_pauses(tmp_path, http):
    job, _ = synthesize(tmp_path, [SpeechPiece("あ、い。", 0.6), SpeechPiece("う。")])
    # あ(0.1) + 読点の間 + い(0.1) + 文の間 0.6 のあとに、2 つ目の区切りが始まる
    inner = pauses(http.payloads[0]["accent_phrases"])[0]
    assert job.offsets[1] - job.offsets[0] == pytest.approx(0.1 + inner + 0.1 + 0.6)


def pitch_of(phrases, index):
    return phrases[index]["moras"][0]["pitch"]


def test_a_conclusion_is_said_lower_without_changing_its_shape(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("あい。", 0.6), SpeechPiece("うえ。", 0.0, "結論")])
    phrases = http.payloads[0]["accent_phrases"]
    lift = PACES["結論"][2] / 12 * math.log(2)
    assert pitch_of(phrases, 0) == pytest.approx(5.0)
    assert pitch_of(phrases, 2) == pytest.approx(5.0 + lift)


def test_stress_raises_and_lengthens_only_its_word(tmp_path, http):
    synthesize(tmp_path, [SpeechPiece("あいう。", 0.0, "", ("い",))])
    phrases = http.payloads[0]["accent_phrases"]
    lift, slow, _ = STRESS
    assert pitch_of(phrases, 0) == pytest.approx(5.0)
    assert pitch_of(phrases, 1) == pytest.approx(5.0 + lift / 12 * math.log(2))
    assert phrases[1]["moras"][0]["vowel_length"] == pytest.approx(0.1 * slow)
    assert phrases[2]["moras"][0]["vowel_length"] == pytest.approx(0.1)


def test_a_stressed_word_that_is_not_found_is_reported(tmp_path, http):
    job, report = synthesize(tmp_path, [SpeechPiece("あいう。", 0.0, "", ("か",))])
    assert report.ok
    assert any("か" in warning for warning in report.warnings)
