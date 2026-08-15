"""原稿を読み上げ単位と間に分ける処理を確認する。

聞こえ方の良し悪しは耳で確かめるしかないが、「どこで区切って、どれだけ間を置き、
どの文字を読み上げから外したか」は決まった結果になるので、ここで固定する。
"""

import json

import pytest

from note2slides.reading import ReadingStyle, load_dictionary, plan_reading


def texts(plan):
    return [u.text for u in plan.utterances]


def pauses(plan):
    return [round(u.pause_after, 3) for u in plan.utterances]


# ---------------------------------------------------------------------------
# 区切りと間
# ---------------------------------------------------------------------------


def test_sentences_become_separate_utterances():
    plan = plan_reading("最初の文です。次の文です。")

    assert texts(plan) == ["最初の文です。", "次の文です。"]


def test_pause_lengths_follow_the_structure():
    style = ReadingStyle(sentence_pause=0.3, line_pause=0.6, tail_silence=0.9, lead_silence=0.2)

    plan = plan_reading("一つ目。二つ目。\n別の行です。", style)

    assert texts(plan) == ["一つ目。", "二つ目。", "別の行です。"]
    # 文の間は短く、行の間は長く取る。読み終わったあとの無音は読み上げの外側に
    # あるので、最後の区切りの後ろには間を置かない。
    assert pauses(plan) == [0.3, 0.6, 0.0]
    assert plan.lead_silence == 0.2
    assert plan.tail_silence == 0.9


def test_question_and_exclamation_end_a_sentence():
    plan = plan_reading("そうでしょうか？そうです！")

    # 表記ゆれをそろえる正規化で、全角の ？！ は半角になる(読み上げは変わらない)。
    assert texts(plan) == ["そうでしょうか?", "そうです!"]


def test_closing_bracket_stays_with_its_sentence():
    plan = plan_reading("「これは大事です。」と書かれています。")

    assert texts(plan) == ["「これは大事です。」", "と書かれています。"]


def test_long_sentence_is_split_at_a_comma():
    long_sentence = "あ" * 60 + "、" + "い" * 60 + "。"

    plan = plan_reading(long_sentence, ReadingStyle(max_chars=50, clause_pause=0.15, tail_silence=0.7))

    assert len(plan.utterances) == 2
    assert plan.utterances[0].text.endswith("、")
    assert plan.utterances[0].pause_after == 0.15  # 文の切れ目より短い間にする
    assert "".join(texts(plan)) == long_sentence


def test_short_sentence_is_not_split():
    plan = plan_reading("短い文です、これで終わりです。", ReadingStyle(max_chars=100))

    assert len(plan.utterances) == 1


def test_empty_text_has_no_utterances():
    assert plan_reading("").is_empty
    assert plan_reading("   \n  ").is_empty


# ---------------------------------------------------------------------------
# 読み上げ用の整形
# ---------------------------------------------------------------------------


def test_sentence_ending_is_added_to_a_bare_line():
    """句点が無いと語尾が下がりきらず、次の項目と続いて聞こえる。"""
    plan = plan_reading("箇条書きの項目\n次の項目")

    assert texts(plan) == ["箇条書きの項目。", "次の項目。"]


def test_lines_ending_with_punctuation_are_left_alone():
    plan = plan_reading("項目です、\n次は。\n注意点:")

    assert texts(plan) == ["項目です、", "次は。", "注意点:"]


def test_sentence_ending_can_be_turned_off():
    plan = plan_reading("箇条書きの項目", ReadingStyle(terminate_sentences=False))

    assert texts(plan) == ["箇条書きの項目"]


def test_list_markers_are_dropped():
    plan = plan_reading("・一つ目\n- 二つ目\n* 三つ目\n1. 四つ目")

    assert texts(plan) == ["一つ目。", "二つ目。", "三つ目。", "1. 四つ目。"]


def test_separator_dot_inside_a_line_is_kept():
    """「A・B」の中黒は区切り文字なので、行頭の記号と区別する。"""
    plan = plan_reading("設計・実装・確認を行います。")

    assert texts(plan) == ["設計・実装・確認を行います。"]


def test_urls_are_not_read():
    plan = plan_reading("詳細は https://example.com/page を見てください。")

    assert texts(plan) == ["詳細はを見てください。"]
    assert any("URL" in note for note in plan.notes)


def test_urls_can_be_read():
    plan = plan_reading("https://example.com です。", ReadingStyle(drop_urls=False))

    assert "https://example.com" in texts(plan)[0]


def test_pictographs_are_dropped():
    plan = plan_reading("できました🎉 次へ→進みます。")

    assert texts(plan) == ["できました次へ進みます。"]
    assert any("絵文字" in note for note in plan.notes)


def test_rule_lines_are_dropped():
    plan = plan_reading("前の行です。\n---\n次の行です。")

    assert texts(plan) == ["前の行です。", "次の行です。"]


def test_spaces_inside_japanese_are_closed_up():
    """「1 枚」の空白を残すと「いち……まい」と間が空いて読まれてしまう。"""
    plan = plan_reading("1 枚のスライドに、口頭で 30 秒から 60 秒で説明する。")

    assert texts(plan) == ["1枚のスライドに、口頭で30秒から60秒で説明する。"]


def test_spaces_between_japanese_and_latin_words_are_closed_up():
    plan = plan_reading("Markdown 記事から資料を作る。")

    assert texts(plan) == ["Markdown記事から資料を作る。"]


def test_spaces_between_latin_words_are_kept():
    """英数字どうしの空白は語の区切りなので、詰めると別の語に聞こえてしまう。"""
    plan = plan_reading("note2slides article.md -o article.pptx")

    assert texts(plan) == ["note2slides article.md -o article.pptx。"]


def test_space_after_a_symbol_is_kept():
    """「1. 四つ目」を「1.四つ目」にすると、番号が小数のように読まれかねない。"""
    plan = plan_reading("1. 四つ目")

    assert texts(plan) == ["1. 四つ目。"]


def test_hold_extends_the_silence_after_the_last_utterance():
    """コードや図は、聞くだけでなく画面を読む時間が要る。"""
    style = ReadingStyle(tail_silence=0.7)

    plan = plan_reading("画面のコマンドをご覧ください。", style, hold=2.0)

    assert plan.tail_silence == 2.7
    assert plan_reading("画面のコマンドをご覧ください。", style).tail_silence == 0.7


def test_full_width_alphanumerics_are_normalized():
    plan = plan_reading("ＡＩは１２３です。")

    assert texts(plan) == ["AIは123です。"]


def test_dictionary_replaces_readings():
    style = ReadingStyle(dictionary={"note": "ノート", "TTS": "ティーティーエス"})

    plan = plan_reading("note の TTS を使います。", style)

    assert texts(plan) == ["ノートのティーティーエスを使います。"]
    assert len(plan.notes) == 2


def test_longer_dictionary_entries_are_applied_first():
    dictionary = load_dictionary_from({"AI": "エーアイ", "AIエージェント": "エーアイエージェント"})
    style = ReadingStyle(dictionary=dictionary)

    plan = plan_reading("AIエージェントの話です。", style)

    assert texts(plan) == ["エーアイエージェントの話です。"]


def test_reading_is_recorded_for_review():
    plan = plan_reading("最初の文です。次の文です。")

    assert plan.reading == "最初の文です。 次の文です。"
    assert plan.to_dict()["utterances"][0]["pause_after"] == pytest.approx(0.35)
    assert plan.to_dict()["tail_silence"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 読み方辞書
# ---------------------------------------------------------------------------


def load_dictionary_from(data, tmp_path=None):
    import tempfile

    directory = tmp_path or tempfile.mkdtemp()
    path = f"{directory}/dict.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return load_dictionary(path)


def test_dictionary_file_is_read(tmp_path):
    assert load_dictionary_from({"note": "ノート"}, tmp_path) == {"note": "ノート"}


def test_broken_dictionary_says_where(tmp_path):
    path = tmp_path / "dict.json"
    path.write_text("{壊れている", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_dictionary(str(path))

    assert "1 行" in str(excinfo.value)


def test_dictionary_must_be_strings(tmp_path):
    path = tmp_path / "dict.json"
    path.write_text('{"note": 1}', encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_dictionary(str(path))

    assert "文字列" in str(excinfo.value)


def test_missing_dictionary_says_so(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        load_dictionary(str(tmp_path / "none.json"))

    assert "開けませんでした" in str(excinfo.value)


def test_invalid_style_is_rejected():
    with pytest.raises(ValueError):
        ReadingStyle(sentence_pause=-1).validate()
    with pytest.raises(ValueError):
        ReadingStyle(max_chars=1).validate()
