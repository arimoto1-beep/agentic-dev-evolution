"""ナレーションの声の聴き比べ用の生成を確認する。

実際の音声合成は使わず、文字数に比例した音を書く偽エンジンで確かめる
(tests/test_audio.py と同じ考え方)。ここで見たいのは声の質ではなく、
候補ごとに条件をそろえて音声・比較表・一覧が揃うかどうか。
"""

import json
import os

import pytest

from note2slides import voice_compare
from note2slides.audio import AudioOptions
from note2slides.reading import ReadingStyle
from note2slides.voice_compare import (
    CANDIDATES,
    CONTINUOUS_NAME,
    INDEX_NAME,
    PREVIEW_NAME,
    REPORT_NAME,
    Candidate,
    OutputExistsError,
    VoiceCompareError,
    compare_voices,
    load_candidates,
)
from note2slides.waveform import read_wav

from test_audio import SAMPLE_RATE, FakeEngine, duration_of, make_script


class PickyEngine(FakeEngine):
    """特定の声だけを持つ偽エンジン(声の指定を確かめるために使う)。"""

    def __init__(self, voices, **kwargs):
        super().__init__(**kwargs)
        self.voices = list(voices)

    def pick_voice(self, name=None, language="ja"):
        from note2slides.speech import SpeechNotAvailableError, Voice

        if name and name not in self.voices:
            raise SpeechNotAvailableError(f"音声が見つかりません: {name}")
        return Voice(name or self.voices[0], "ja-JP", engine=self.name)


def two_candidates():
    return [
        Candidate(id="alpha", voice="話者A/ノーマル", reason="基準にする声", role="現行の標準"),
        Candidate(id="beta", voice="話者B/ノーマル", reason="低い声を比べる", speed=0.95),
    ]


def base_options():
    # 偽エンジンの出力に合わせる(本物と違い 22050Hz で書き出す)。
    return AudioOptions(sample_rate=SAMPLE_RATE, reading=ReadingStyle())


def run(tmp_path, candidates=None, engine=None, **kwargs):
    script = make_script(tmp_path, ["最初のスライドです。", "次のスライドです。"])
    return compare_voices(
        script,
        str(tmp_path / "out"),
        candidates=candidates or two_candidates(),
        options=base_options(),
        engine=engine or FakeEngine(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 候補
# ---------------------------------------------------------------------------


def test_candidate_overrides_only_voice_settings():
    base = AudioOptions(speed=1.0, volume=80, reading=ReadingStyle(sentence_pause=0.35))
    candidate = Candidate(
        id="calm", voice="No.7/アナウンス", reason="", speed=0.95, intonation=0.9, sentence_pause=0.5
    )

    options = candidate.options(base)

    assert options.voice == "No.7/アナウンス"
    assert (options.speed, options.intonation) == (0.95, 0.9)
    assert options.reading.sentence_pause == 0.5
    # 比較の条件になる部分は基準のまま
    assert options.volume == 80
    assert options.reading.line_pause == base.reading.line_pause
    # 基準側は書き換わらない(次の候補が影響を受けない)
    assert base.speed == 1.0 and base.reading.sentence_pause == 0.35


def test_candidate_describes_only_changed_settings():
    base = AudioOptions()
    assert Candidate(id="a", voice="v", reason="").describe_settings(base) == "既定のまま"
    described = Candidate(id="b", voice="v", reason="", speed=0.95, line_pause=0.9).describe_settings(base)
    assert "速さ 0.95" in described and "行の間 0.9秒" in described


def test_builtin_candidates_include_current_standard():
    """標準の声を比較の基準として必ず含める(無いと良し悪しを判断できない)。"""
    from note2slides import tts

    ids = [c.id for c in CANDIDATES]
    assert len(ids) == len(set(ids))
    engine, voice = tts.default_narration()
    assert any(
        c.voice == voice and c.engine == engine and c.speed == 1.0 for c in CANDIDATES
    )
    # Gen7 までの標準も、次に見直すときに比べられるよう残す。
    assert any(c.voice == "No.7/アナウンス" and c.speed == 1.0 for c in CANDIDATES)
    # 選んだ理由は成果物に書き出すので、空のものを混ぜない
    assert all(len(c.reason) > 20 for c in CANDIDATES)


def test_builtin_candidates_name_a_known_engine():
    """エンジン名を書き間違えると、合成を始めてから止まる。"""
    from note2slides import tts

    assert all(not c.engine or c.engine in tts.ENGINES for c in CANDIDATES)


def test_duplicate_id_is_rejected(tmp_path):
    candidates = [Candidate(id="same", voice="A", reason=""), Candidate(id="same", voice="B", reason="")]
    with pytest.raises(VoiceCompareError, match="重複"):
        run(tmp_path, candidates)


def test_invalid_id_is_rejected(tmp_path):
    with pytest.raises(VoiceCompareError, match="id"):
        run(tmp_path, [Candidate(id="話者 A", voice="A", reason="")])


def test_load_candidates_roundtrip(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps([c.to_dict() for c in two_candidates()], ensure_ascii=False), encoding="utf-8"
    )
    assert load_candidates(str(path)) == two_candidates()


def test_load_candidates_accepts_generated_index(tmp_path):
    """生成された compare.json をそのまま直して読み込める。"""
    result = run(tmp_path)
    loaded = load_candidates(result.index_path)
    assert [c.id for c in loaded] == ["alpha", "beta"]
    assert loaded[1].speed == 0.95


def test_load_candidates_reports_broken_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('[{"voice": "A"}]', encoding="utf-8")
    with pytest.raises(VoiceCompareError, match="id"):
        load_candidates(str(path))


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def test_writes_audio_and_index_for_each_candidate(tmp_path):
    result = run(tmp_path)

    assert [r.candidate.id for r in result.succeeded] == ["alpha", "beta"]
    for item in result.succeeded:
        assert os.path.isdir(item.outdir)
        assert item.duration > 0
        assert duration_of(item.continuous_path) == pytest.approx(item.duration, abs=0.01)
        assert os.path.isfile(os.path.join(item.outdir, "narration.json"))
    assert os.path.isfile(result.index_path)
    assert os.path.isfile(result.report_path)


def test_continuous_joins_every_slide(tmp_path):
    result = run(tmp_path)

    item = result.succeeded[0]
    total = sum(clip.duration for clip in item.narration.clips)
    assert item.duration == pytest.approx(total, abs=0.01)
    assert read_wav(item.continuous_path).sample_rate == SAMPLE_RATE


def test_each_candidate_uses_its_own_voice(tmp_path):
    engine = FakeEngine()
    run(tmp_path, engine=engine)

    assert [call["voice"] for call in engine.calls] == ["話者A/ノーマル", "話者B/ノーマル"]
    assert [call["speed"] for call in engine.calls] == [1.0, 0.95]


def test_conditions_other_than_voice_are_identical(tmp_path):
    """声以外の条件がそろっていないと、聴き比べの結果を声の差として読めない。"""
    engine = FakeEngine()
    result = run(tmp_path, engine=engine)

    readings = [json.load(open(os.path.join(r.outdir, "narration.json"), encoding="utf-8")) for r in result.succeeded]
    assert readings[0]["reading"] == readings[1]["reading"]
    assert [clip["reading"] for clip in readings[0]["clips"]] == [
        clip["reading"] for clip in readings[1]["clips"]
    ]
    # 音量は候補ごとにそろえる(声の大きさの差が印象に混ざらないようにする)
    assert readings[0]["loudness"]["result_lufs"] == pytest.approx(
        readings[1]["loudness"]["result_lufs"], abs=0.5
    )


def test_manifest_keeps_voice_settings(tmp_path):
    """どの設定で作った音声かを、成果物だけから確認できる。"""
    result = run(tmp_path)

    manifest = json.load(
        open(os.path.join(result.succeeded[1].outdir, "narration.json"), encoding="utf-8")
    )
    assert manifest["voice"] == "話者B/ノーマル"
    assert manifest["speed"] == 0.95
    assert manifest["pitch"] == 0.0 and manifest["intonation"] == 1.0


def test_preview_lists_where_each_candidate_starts(tmp_path):
    result = run(tmp_path, preview_seconds=1.0)

    assert os.path.isfile(result.preview_path)
    assert [mark.id for mark in result.preview_marks] == ["alpha", "beta"]
    assert result.preview_marks[0].start == 0.0
    # 2 つ目は 1 つ目の長さと間のぶんだけ後ろから始まる
    assert result.preview_marks[1].start > result.preview_marks[0].duration
    assert duration_of(result.preview_path) >= result.preview_marks[1].start


def test_preview_cuts_at_slide_boundaries(tmp_path):
    """文の途中で切ると比べにくいので、スライド単位で足す。"""
    result = run(tmp_path, preview_seconds=0.1)

    first = result.succeeded[0].narration.clips[0]
    assert result.preview_marks[0].duration == pytest.approx(first.duration, abs=0.01)


def test_preview_can_be_skipped(tmp_path):
    result = run(tmp_path, preview_seconds=0)
    assert result.preview_path == ""
    assert not os.path.exists(os.path.join(result.outdir, PREVIEW_NAME))


def test_candidate_can_use_another_engine(tmp_path, monkeypatch):
    """VOICEVOX と VOICEVOX Nemo は別のエンジンで動く。同じ比較の中で混ぜられる。"""
    others = {}

    def fake_select(name, powershell=None, language="ja", voicevox_options=None):
        others[name] = others.get(name) or FakeEngine(name=name)
        return others[name]

    monkeypatch.setattr(voice_compare.tts_mod, "select_engine", fake_select)
    base = FakeEngine()
    candidates = [
        Candidate(id="alpha", voice="話者A/ノーマル", reason="基準"),
        Candidate(id="beta", voice="女声1/ノーマル", reason="別のエンジン", engine="voicevox-nemo"),
        Candidate(id="gamma", voice="女声2/ノーマル", reason="同じエンジン", engine="voicevox-nemo"),
    ]

    result = run(tmp_path, candidates, engine=base)

    assert [r.candidate.id for r in result.succeeded] == ["alpha", "beta", "gamma"]
    # 基準のエンジンは呼び出し側のもの、指定のある候補だけが別のエンジンを使う。
    assert [call["voice"] for call in base.calls] == ["話者A/ノーマル"]
    assert [call["voice"] for call in others["voicevox-nemo"].calls] == [
        "女声1/ノーマル",
        "女声2/ノーマル",
    ]
    # 起動と音声モデルの読み込みが候補ごとに起きないよう、エンジンは 1 つだけ作る。
    assert list(others) == ["voicevox-nemo"]
    assert result.engine == "fake / voicevox-nemo"


def test_candidate_engine_is_closed_but_the_given_one_is_kept(tmp_path, monkeypatch):
    """自分で作ったエンジンだけを閉じる(呼び出し側のものを閉じると次が動かない)。"""
    created = FakeEngine(name="voicevox-nemo")
    monkeypatch.setattr(
        voice_compare.tts_mod,
        "select_engine",
        lambda name, powershell=None, language="ja", voicevox_options=None: created,
    )
    base = FakeEngine()

    run(
        tmp_path,
        [
            Candidate(id="alpha", voice="A", reason=""),
            Candidate(id="beta", voice="B", reason="", engine="voicevox-nemo"),
        ],
        engine=base,
    )

    assert created.closed and not base.closed


def test_index_and_report_record_the_engine(tmp_path, monkeypatch):
    """どのエンジンで作った音声かで、公開時の表示も変わる。"""
    monkeypatch.setattr(
        voice_compare.tts_mod,
        "select_engine",
        lambda name, powershell=None, language="ja", voicevox_options=None: FakeEngine(name=name),
    )
    result = run(
        tmp_path,
        [Candidate(id="beta", voice="女声1/ノーマル", reason="別のエンジン", engine="voicevox-nemo")],
        engine=FakeEngine(),
    )

    index = json.load(open(result.index_path, encoding="utf-8"))
    assert index["candidates"][0]["engine"] == "voicevox-nemo"
    assert "voicevox-nemo" in open(result.report_path, encoding="utf-8").read()


def test_markdown_input_is_converted_once(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# 見出し\n\n本文です。\n", encoding="utf-8")

    result = compare_voices(
        str(article),
        str(tmp_path / "out"),
        candidates=two_candidates()[:1],
        options=base_options(),
        engine=FakeEngine(),
    )

    assert result.source.endswith("source.pptx")
    assert os.path.isfile(result.source)


# ---------------------------------------------------------------------------
# 失敗の扱い
# ---------------------------------------------------------------------------


def test_unknown_voice_stops_before_synthesis(tmp_path):
    """合成は候補 1 つで数分かかる。名前の間違いは始める前に伝える。"""
    engine = PickyEngine(["話者A/ノーマル"])

    with pytest.raises(VoiceCompareError, match="話者B/ノーマル"):
        run(tmp_path, engine=engine)
    assert engine.calls == []


def test_one_failed_candidate_keeps_the_others(tmp_path):
    engine = FakeEngine(fail=[1])  # 1 件目のスライドだけ合成に失敗する
    script = make_script(tmp_path, ["最初のスライドです。"])
    result = compare_voices(
        script,
        str(tmp_path / "out"),
        candidates=two_candidates(),
        options=base_options(),
        engine=engine,
    )

    assert [r.candidate.id for r in result.failed] == ["alpha", "beta"]
    assert all("偽の失敗" in r.error for r in result.failed)


def test_failure_is_recorded_in_the_report(tmp_path):
    class HalfBrokenEngine(FakeEngine):
        def synthesize(self, jobs, workdir, on_done=None, **kwargs):
            self.fail = {1} if kwargs.get("voice") == "話者B/ノーマル" else set()
            return super().synthesize(jobs, workdir, on_done=on_done, **kwargs)

    result = run(tmp_path, engine=HalfBrokenEngine())

    assert [r.candidate.id for r in result.succeeded] == ["alpha"]
    assert [r.candidate.id for r in result.failed] == ["beta"]
    report = open(result.report_path, encoding="utf-8").read()
    assert "生成できなかった候補" in report and "偽の失敗" in report
    index = json.load(open(result.index_path, encoding="utf-8"))
    assert index["candidates"][1]["ok"] is False
    assert "偽の失敗" in index["candidates"][1]["error"]
    # 成功した候補だけで聴き比べは続けられる
    assert [mark.id for mark in result.preview_marks] == ["alpha"]


def test_existing_output_is_kept_without_force(tmp_path):
    run(tmp_path)
    with pytest.raises(OutputExistsError, match="--force"):
        run(tmp_path)
    run(tmp_path, force=True)


# ---------------------------------------------------------------------------
# 比較表
# ---------------------------------------------------------------------------


def test_report_keeps_the_reason_for_each_candidate(tmp_path):
    """なぜその候補を選んだのかを、あとから確認できるようにする。"""
    result = run(tmp_path)

    report = open(result.report_path, encoding="utf-8").read()
    for candidate in two_candidates():
        assert candidate.reason in report
        assert candidate.voice in report
    assert "現行の標準" in report
    assert "速さ 0.95" in report
    assert f"beta/{CONTINUOUS_NAME}" in report
    assert PREVIEW_NAME in report
    # いま何が標準かと、このコマンドがそれを変えないことを明記する
    from note2slides import tts

    engine, voice = tts.default_narration()
    assert f"現在の標準は `{engine}` の「{voice}」" in report
    assert "このコマンドは標準を変更しない" in report


def test_report_shows_how_much_each_candidate_was_limited(tmp_path):
    """処理は同じでも、ピークを抑えた量は声によって違う。聞く前に分かるようにする。"""
    result = run(tmp_path)

    report = open(result.report_path, encoding="utf-8").read()
    assert "ピークを抑えた量" in report
    for item in result.succeeded:
        assert f"| {item.candidate.id} | {item.narration.loudness.measured_lufs:.1f} LUFS" in report


def test_index_records_settings_and_files(tmp_path):
    result = run(tmp_path, preview_seconds=1.0)

    index = json.load(open(result.index_path, encoding="utf-8"))
    assert index["base"]["reading"]["sentence_pause"] == 0.35
    assert index["preview"]["file"] == PREVIEW_NAME
    assert [mark["id"] for mark in index["preview"]["marks"]] == ["alpha", "beta"]

    entry = index["candidates"][1]
    assert entry["speaker"] == "話者B" and entry["style"] == "ノーマル"
    assert entry["speed"] == 0.95
    assert entry["reason"] == "低い声を比べる"
    assert entry["continuous"] == f"beta/{CONTINUOUS_NAME}"
    assert entry["manifest"] == "beta/narration.json"
    assert entry["slides"] == 2


def test_output_names_are_stable(tmp_path):
    result = run(tmp_path)
    names = set(os.listdir(result.outdir))
    assert {INDEX_NAME, REPORT_NAME, PREVIEW_NAME, "alpha", "beta"} <= names
