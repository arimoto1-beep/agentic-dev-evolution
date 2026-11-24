"""人が説明のまとまりごとに確かめるための束(gen39c)。動画は作らず、区切り方と前後の付け方だけを確かめる。"""

import importlib.util
import json
import os
import sys

_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "review_pack.py")
_SPEC = importlib.util.spec_from_file_location("review_pack", _PATH)
review_pack = importlib.util.module_from_spec(_SPEC)
sys.modules["review_pack"] = review_pack  # dataclass が自分のモジュールを引くため
_SPEC.loader.exec_module(review_pack)

SRT = """1
00:00:00,500 --> 00:00:03,000
はじめの文です。

2
00:00:03,000 --> 00:00:06,000
長い文は、読点で分かれて、

3
00:00:06,000 --> 00:00:09,500
二つの字幕になります。

4
00:00:10,200 --> 00:00:13,000
次の章の、

5
00:00:13,000 --> 00:00:15,000
最初の文です。

6
00:00:15,000 --> 00:00:19,000
もう一文。
"""


def listing():
    slide = lambda i, start, duration, name: {"index": i, "start": start, "duration": duration,
                                              "shots": [{"at": 0.0, "image": f"{name}@1.png"}]}
    return {"file": "v.mp4", "duration": 20.0,
            "chapters": [{"start": 0.0, "title": "一章"}, {"start": 10.0, "title": "二章"}],
            "slides": [slide(1, 0.0, 6.0, "01_a"), slide(2, 6.0, 4.0, "02_b"), slide(3, 10.0, 10.0, "03_c")]}


def test_srt_is_read_with_line_breaks():
    cues = review_pack.parse_srt(SRT)
    assert len(cues) == 6
    assert cues[1].start == 3.0 and cues[2].end == 9.5
    assert cues[0].text == "はじめの文です。"


def test_units_follow_chapters_and_scene_boundaries():
    units = review_pack.build_units(listing())
    assert [u.title for u in units] == ["一章", "二章"]
    assert [s.name for s in units[0].scenes] == ["01_a", "02_b"]
    assert [s.name for s in units[1].scenes] == ["03_c"]
    assert (units[1].start, units[1].end) == (10.0, 20.0)


def test_lead_in_starts_at_the_head_of_a_sentence():
    cues = review_pack.parse_srt(SRT)
    seconds, picked = review_pack.lead_in(cues, 10.0)
    # 最後の字幕「二つの字幕になります。」は文の途中から始まるので、文の頭(3.0 秒)までさかのぼる
    assert [c.text for c in picked] == ["長い文は、読点で分かれて、", "二つの字幕になります。"]
    assert abs(seconds - (10.0 - 3.0 + 0.3)) < 1e-9


def test_lead_out_runs_to_the_end_of_a_sentence():
    cues = review_pack.parse_srt(SRT)
    seconds, picked = review_pack.lead_out(cues, 10.0, 20.0)
    assert [c.text for c in picked] == ["次の章の、", "最初の文です。"]
    assert abs(seconds - (15.0 + 0.5 - 10.0)) < 1e-9


def test_band_covers_the_whole_clip_without_gaps():
    cues = review_pack.parse_srt(SRT)
    unit = review_pack.build_units(listing())[0]
    unit.lead_out = review_pack.lead_out(cues, unit.end, 20.0)[0]
    segments = review_pack.band_segments(unit, cues)
    assert segments[0][0] == 0.0
    assert abs(segments[-1][1] - (unit.clip_end - unit.clip_start)) < 1e-9
    for (a0, a1, *_), (b0, *_rest) in zip(segments, segments[1:]):
        assert abs(a1 - b0) < 1e-9
    assert {kind for *_, kind in segments} == {"main", "after"}
    assert any(text == "最初の文です。" and kind == "after" for _, _, text, kind in segments)


def test_notes_attach_to_the_unit_that_holds_the_scene(tmp_path):
    units = review_pack.build_units(listing())
    review_pack.attach_notes(units, {"scenes": {"03_c": {"changed": "直した", "check": ["見る"]}}})
    assert units[0].notes == []
    assert units[1].notes[0]["scene"] == "03_c" and units[1].notes[0]["check"] == ["見る"]


def test_scene_titles_are_used_only_when_the_count_matches(tmp_path):
    path = tmp_path / "s.md"
    path.write_text("---\ntitle: t\n---\n\n## あ\n\n### 設定\n\n## い\n\n## う\n", encoding="utf-8")
    assert review_pack.scene_titles(str(path), 3) == ["あ", "い", "う"]
    assert review_pack.scene_titles(str(path), 4) == []


def test_page_and_text_version_are_written(tmp_path):
    units = review_pack.build_units(listing())
    review_pack.attach_notes(units, {"scenes": {"02_b": {"changed": "直した", "check": ["見る"]}}})
    review_pack.write_html(str(tmp_path / "index.html"), "題", units, ["全体の注意"], "id")
    review_pack.write_markdown(str(tmp_path / "REVIEW.md"), "題", units, ["全体の注意"])
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    data = page.split("const DATA=", 1)[1].split(";\nconst KEY", 1)[0]
    assert [u["clip"] for u in json.loads(data)["units"]] == ["clips/01.mp4", "clips/02.mp4"]
    assert "http" not in page  # 外のものを読まない(手元で開くだけで動く)
    text = (tmp_path / "REVIEW.md").read_text(encoding="utf-8")
    assert "* [ ] 見る" in text and "全体の注意" in text


def test_clock_matches_chapter_times():
    # 章立ては切り捨てで書く(9:06.9 は 9:06)。帯と確認ページも同じ時刻を指す
    assert review_pack.clock(546.9) == "9:06"
