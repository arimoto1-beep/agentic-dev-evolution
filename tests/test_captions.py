"""字幕(.srt / .vtt)と章立て。

字幕は音声を聞き取り直さず、「何を読み上げたか」と「それが何秒目か」から
組み立てる。ここで確かめるのは主に次の 3 つ。

    * 時刻が音声と食い違わないこと(前後の無音・間の扱い)
    * 画面に入る大きさに収まること(行数と 1 行の文字数)
    * 章立てが YouTube の決まり(0:00 から / 10 秒以上)を満たすこと
"""

import json
import os

import pytest

from note2slides import captions as cap
from note2slides.reading import ReadingStyle, plan_reading
from note2slides.speech import SpeechPiece


def slide(index, start, duration, pieces, lead=0.3, tail=0.7, title=""):
    return cap.SlideCaption(
        index=index,
        start=start,
        duration=duration,
        lead_silence=lead,
        tail_silence=tail,
        pieces=[SpeechPiece(text, pause) for text, pause in pieces],
        title=title,
    )


# ---------------------------------------------------------------------------
# 時刻の割り当て
# ---------------------------------------------------------------------------


def test_the_first_caption_waits_for_the_silence_before_the_narration():
    """読み始めるまでの無音のあいだは、まだ字幕を出さない。"""
    cues = cap.build_cues([slide(1, start=10.0, duration=5.0, pieces=[("こんにちは。", 0.0)])])

    assert len(cues) == 1
    assert cues[0].start == pytest.approx(10.3)
    assert cues[0].end == pytest.approx(14.3)  # 5.0 - 0.7(読み終わったあとの無音)


def test_captions_split_the_speech_by_how_long_each_part_takes_to_read():
    """1 枚の中は、読み上げにかかる長さの目安で按分する。"""
    cues = cap.build_cues(
        [
            slide(
                1,
                start=0.0,
                duration=11.0,
                pieces=[("あああ", 0.0), ("あああああああああ", 0.0)],
                lead=0.0,
                tail=1.0,
            )
        ]
    )

    # 読み上げに使えるのは 10 秒。拍数の比が 3:9 なので 2.5 秒と 7.5 秒。
    assert cues[0].start == pytest.approx(0.0)
    assert cues[0].end == pytest.approx(2.5)
    assert cues[1].end == pytest.approx(10.0)


def test_the_pause_between_sentences_keeps_the_previous_caption_on_screen():
    """文と文の間で字幕が消えないようにする(点滅を避ける)。"""
    cues = cap.build_cues(
        [
            slide(
                1,
                start=0.0,
                duration=11.0,
                pieces=[("あああ", 1.0), ("あああああああああ", 0.0)],
                lead=0.0,
                tail=1.0,
            )
        ]
    )

    # 間の 1 秒を除いた 9 秒を 3:9 で按分し、間は前の字幕に含める。
    assert cues[0].end == pytest.approx(cues[1].start)
    assert cues[0].end == pytest.approx(3.25)
    assert cues[1].end == pytest.approx(10.0)


def test_captions_never_run_past_the_narration():
    """字幕が音声より後ろへはみ出さない(次のスライドに残らない)。"""
    text = "これは長めの文章です。\n二つ目の行もあります。\n三つ目の行で終わります。"
    plan = plan_reading(text, ReadingStyle())
    pieces = [(u.text, u.pause_after) for u in plan.utterances]
    duration = 12.0
    cues = cap.build_cues(
        [
            slide(1, 0.0, duration, pieces, lead=plan.lead_silence, tail=plan.tail_silence),
            slide(2, duration, 4.0, [("次の画面です。", 0.0)]),
        ]
    )

    first = [cue for cue in cues if cue.slide == 1]
    assert first[-1].end <= duration - plan.tail_silence + 1e-9
    assert all(cue.start < cue.end for cue in cues)
    assert [cue.index for cue in cues] == list(range(1, len(cues) + 1))


def test_slides_without_narration_have_no_caption():
    """読み上げる文章が無いスライドには字幕を出さない。"""
    cues = cap.build_cues([cap.SlideCaption(index=1, start=0.0, duration=2.0)])

    assert cues == []


def test_a_mismatched_duration_does_not_produce_a_backwards_caption():
    """音声が無音の合計より短くても、逆向きの字幕を作らない。"""
    cues = cap.build_cues(
        [slide(1, start=0.0, duration=0.5, pieces=[("あ。", 0.0)], lead=0.3, tail=0.7)]
    )

    assert all(cue.start <= cue.end for cue in cues)


# ---------------------------------------------------------------------------
# 画面に入る大きさ
# ---------------------------------------------------------------------------


def test_a_long_sentence_is_split_into_several_captions():
    """1 文が長いときは、読点で分けて複数の字幕にする。"""
    long_text = "最初の部分がここにあり、" + "次の部分がここにあります、" * 5 + "終わりです。"
    style = cap.CaptionStyle()
    cues = cap.build_cues([slide(1, 0.0, 30.0, [(long_text, 0.0)])], style)

    assert len(cues) > 1
    for cue in cues:
        lines = cue.text.split("\n")
        assert len(lines) <= style.max_lines
        assert max(len(line) for line in lines) <= style.line_chars + 1
    # 分けても、読み上げの時間からはみ出さない。
    assert cues[-1].end == pytest.approx(30.0 - 0.7)


def test_captions_keep_the_words_of_the_narration():
    """分けても、読み上げた文章の文字はそのまま残す。"""
    text = "ここは短い文です、" * 8
    cues = cap.build_cues([slide(1, 0.0, 40.0, [(text, 0.0)])])

    joined = "".join(cue.text.replace("\n", "") for cue in cues)
    assert joined == text


def test_a_short_caption_is_merged_into_the_one_before_it():
    """読む前に消えてしまう短い字幕は、前の字幕にまとめる。"""
    style = cap.CaptionStyle(min_duration=1.0)
    cues = cap.build_cues(
        [slide(1, 0.0, 5.0, [("あああああああああ。", 0.4), ("はい。", 0.0)])], style
    )

    assert len(cues) == 1
    assert "はい。" in cues[0].text


def test_the_line_length_can_be_changed():
    style = cap.CaptionStyle(line_chars=12)
    cues = cap.build_cues([slide(1, 0.0, 20.0, [("これは長い文章の例で、行の折り返しを確かめます。", 0.0)])], style)

    for cue in cues:
        assert max(len(line) for line in cue.text.split("\n")) <= 13


def test_an_impossible_style_is_rejected():
    with pytest.raises(cap.CaptionError):
        cap.CaptionStyle(line_chars=1).validate()


# ---------------------------------------------------------------------------
# 読み上げの長さの見積もり
# ---------------------------------------------------------------------------


def test_small_kana_do_not_add_a_beat():
    """「しゃ」は 2 文字だが 1 拍。"""
    assert cap.estimate_moras("しゃ") == pytest.approx(cap.estimate_moras("し"))


def test_kanji_take_longer_to_read_than_kana():
    assert cap.estimate_moras("漢字") > cap.estimate_moras("かじ")


def test_a_comma_costs_time_because_the_engine_pauses_there():
    """読点は音にならないが、合成エンジンはそこで間を取る。"""
    assert cap.estimate_moras("あ、あ") > cap.estimate_moras("あああ")


def test_brackets_and_spaces_are_not_read():
    assert cap.estimate_moras("「」() 　") == 0


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def test_srt_has_a_number_a_time_range_and_the_text():
    cues = cap.build_cues([slide(1, 0.0, 5.0, [("こんにちは。", 0.0)], lead=0.0, tail=0.0)])

    text = cap.format_srt(cues)

    assert text.startswith("1\n00:00:00,000 --> 00:00:05,000\nこんにちは。\n")


def test_vtt_starts_with_the_header():
    cues = cap.build_cues([slide(1, 0.0, 5.0, [("こんにちは。", 0.0)], lead=0.0, tail=0.0)])

    text = cap.format_vtt(cues)

    assert text.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:05.000" in text


def test_times_past_an_hour_are_written_with_the_hour():
    assert cap.srt_time(3725.5) == "01:02:05,500"
    assert cap.vtt_time(3725.5) == "01:02:05.500"


def test_written_captions_use_line_feeds(tmp_path):
    """CRLF で書くとプレイヤーによっては読めないため、LF にそろえる。"""
    cues = cap.build_cues([slide(1, 0.0, 5.0, [("こんにちは。", 0.0)])])
    path = cap.write_captions(cues, str(tmp_path / "sample.srt"))

    with open(path, "rb") as f:
        assert b"\r\n" not in f.read()


# ---------------------------------------------------------------------------
# 章立て
# ---------------------------------------------------------------------------


def test_chapters_start_at_the_beginning_of_the_video():
    """YouTube は最初の章が 0:00 でないと目次を出さない。"""
    slides = [
        slide(1, 0.0, 20.0, [("表紙です。", 0.0)], title="はじめに"),
        slide(2, 20.0, 20.0, [("本題です。", 0.0)], title="本題"),
        slide(3, 40.0, 20.0, [("まとめです。", 0.0)], title="まとめ"),
    ]

    chapters = cap.build_chapters(slides, 60.0)

    assert [c.title for c in chapters] == ["はじめに", "本題", "まとめ"]
    assert chapters[0].start == 0.0


def test_slides_that_continue_a_section_do_not_start_a_new_chapter():
    """同じ見出しが続くスライドと、見出しの無いスライドは前の章の続き。"""
    slides = [
        slide(1, 0.0, 20.0, [("あ。", 0.0)], title="はじめに"),
        slide(2, 20.0, 20.0, [("い。", 0.0)], title="はじめに"),
        slide(3, 40.0, 20.0, [("う。", 0.0)], title=""),
        slide(4, 60.0, 20.0, [("え。", 0.0)], title="まとめ"),
    ]

    chapters = cap.build_chapters(slides, 80.0)

    assert [(c.title, c.start) for c in chapters] == [("はじめに", 0.0), ("まとめ", 60.0)]


def test_short_chapters_are_folded_into_the_one_before():
    """10 秒に満たない章は YouTube が受け付けないため、前の章にまとめる。"""
    slides = [
        slide(1, 0.0, 30.0, [("あ。", 0.0)], title="はじめに"),
        slide(2, 30.0, 3.0, [("い。", 0.0)], title="ごく短い章"),
        slide(3, 33.0, 30.0, [("う。", 0.0)], title="まとめ"),
    ]

    chapters = cap.build_chapters(slides, 63.0)

    assert [c.title for c in chapters] == ["はじめに", "まとめ"]


def test_a_short_last_chapter_is_folded_into_the_one_before():
    slides = [
        slide(1, 0.0, 30.0, [("あ。", 0.0)], title="はじめに"),
        slide(2, 30.0, 2.0, [("い。", 0.0)], title="おわり"),
    ]

    chapters = cap.build_chapters(slides, 32.0)

    assert [c.title for c in chapters] == ["はじめに"]


def test_chapters_are_written_in_the_form_youtube_expects(tmp_path):
    chapters = [cap.Chapter(0.0, "はじめに"), cap.Chapter(75.0, "本題"), cap.Chapter(3725.0, "まとめ")]

    path = cap.write_chapters(chapters, str(tmp_path / "movie_chapters.txt"))

    with open(path, encoding="utf-8") as f:
        assert f.read() == "0:00 はじめに\n1:15 本題\n1:02:05 まとめ\n"


def test_no_titles_means_no_chapters():
    assert cap.build_chapters([slide(1, 0.0, 20.0, [("あ。", 0.0)])], 20.0) == []


# ---------------------------------------------------------------------------
# narration.json から材料を読む
# ---------------------------------------------------------------------------


def write_manifest(directory, clips):
    with open(os.path.join(directory, "narration.json"), "w", encoding="utf-8") as f:
        json.dump({"clips": clips}, f, ensure_ascii=False)


def test_the_reading_units_come_from_the_narration_manifest(tmp_path):
    write_manifest(
        str(tmp_path),
        [
            {
                "index": 1,
                "duration": 5.0,
                "title": "はじめに",
                "reading": "こんにちは。",
                "lead_silence": 0.3,
                "tail_silence": 0.7,
                "pieces": [{"text": "こんにちは。", "pause_after": 0.0}],
            }
        ],
    )

    loaded, warnings = cap.load_captions(str(tmp_path))

    assert warnings == []
    assert loaded[1].title == "はじめに"
    assert loaded[1].pieces == [SpeechPiece("こんにちは。", 0.0)]


def test_materials_without_a_manifest_are_left_alone(tmp_path):
    """手で並べた素材のときは、字幕を作らないことをいちいち言わない。"""
    loaded, warnings = cap.load_captions(str(tmp_path))

    assert loaded == {}
    assert warnings == []


def test_an_old_manifest_says_how_to_get_captions(tmp_path):
    """読み上げ単位が無い一覧なら、書き出し直せばよいことを伝える。"""
    write_manifest(str(tmp_path), [{"index": 1, "duration": 5.0, "reading": "こんにちは。"}])

    loaded, warnings = cap.load_captions(str(tmp_path))

    assert loaded == {}
    assert len(warnings) == 1
    assert "note2slides-audio" in warnings[0]


def test_a_silent_slide_is_kept_for_its_title(tmp_path):
    """読み上げる文章が無い画面も、見出しは章の切れ目になりうる。"""
    write_manifest(
        str(tmp_path),
        [{"index": 1, "duration": 2.0, "silent": True, "reading": "", "title": "図を見る"}],
    )

    loaded, warnings = cap.load_captions(str(tmp_path))

    assert warnings == []
    assert loaded[1].title == "図を見る"
    assert cap.build_cues(loaded.values()) == []  # 字幕にはならない


def test_a_silent_slide_can_start_a_chapter():
    slides = [
        slide(1, 0.0, 30.0, [("あ。", 0.0)], title="はじめに"),
        cap.SlideCaption(index=2, start=30.0, duration=20.0, title="図で見る"),
        slide(3, 50.0, 30.0, [("う。", 0.0)], title="まとめ"),
    ]

    chapters = cap.build_chapters(slides, 80.0)

    assert [c.title for c in chapters] == ["はじめに", "図で見る", "まとめ"]
