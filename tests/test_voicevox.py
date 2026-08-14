"""VOICEVOX ENGINE の呼び出しを確認する。

実際のエンジンは起動せず、HTTP のやり取りを差し替えて確かめる。
実機での合成は tests/test_voicevox_compat.py で確認する。
"""

import json
import os
import wave

import numpy as np
import pytest

from note2slides import voicevox
from note2slides.speech import SpeechJob, SpeechNotAvailableError, SynthesisError
from note2slides.voicevox import Style, VoicevoxEngine

SPEAKERS = [
    {
        "name": "No.7",
        "speaker_uuid": "uuid-7",
        "styles": [
            {"name": "ノーマル", "id": 29, "type": "talk"},
            {"name": "アナウンス", "id": 30, "type": "talk"},
            {"name": "ハミング", "id": 300, "type": "humming"},
        ],
    },
    {
        "name": "ずんだもん",
        "speaker_uuid": "uuid-z",
        "styles": [{"name": "ノーマル", "id": 3, "type": "talk"}],
    },
]

QUERY = {
    "accent_phrases": [],
    "speedScale": 1.0,
    "pitchScale": 0.0,
    "intonationScale": 1.0,
    "volumeScale": 1.0,
    "prePhonemeLength": 0.1,
    "postPhonemeLength": 0.1,
    "outputSamplingRate": 24000,
    "outputStereo": False,
}


def wav_bytes(seconds=0.5, sample_rate=48000, path=None):
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(np.zeros(int(seconds * sample_rate), dtype="<i2").tobytes())
    return buffer.getvalue()


class FakeHttp:
    """/version /speakers /audio_query /synthesis に答える差し替え。"""

    def __init__(self, fail_on=None, missing=False):
        self.calls = []
        self.payloads = []
        self.fail_on = fail_on or ()
        self.missing = missing

    def __call__(self, url, data=None, timeout=60.0):
        self.calls.append(url)
        if self.missing:
            raise voicevox.HttpFailure(url, "接続できません")
        if "/version" in url:
            return b'"0.25.2"'
        if "/speakers" in url:
            return json.dumps(SPEAKERS).encode("utf-8")
        if "/initialize_speaker" in url:
            return b""
        if "/audio_query" in url:
            if any(bad in url for bad in self.fail_on):
                raise voicevox.HttpFailure(url, "読みを作れません", 422, '{"detail":"だめ"}')
            return json.dumps(QUERY).encode("utf-8")
        if "/synthesis" in url:
            self.payloads.append(json.loads(data.decode("utf-8")))
            return wav_bytes()
        raise AssertionError(f"知らない呼び出し: {url}")


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(voicevox, "_request", fake)
    return fake


@pytest.fixture
def engine(http):
    return VoicevoxEngine(url="http://127.0.0.1:50021", autostart=False)


# ---------------------------------------------------------------------------
# エンジンの場所と起動
# ---------------------------------------------------------------------------


def test_env_var_comes_first(monkeypatch):
    monkeypatch.setenv("VOICEVOX_ENGINE_PATH", "D:/somewhere/run.exe")

    assert voicevox.candidate_paths()[0] == "D:/somewhere/run.exe"


def test_explicit_path_is_used(tmp_path):
    exe = tmp_path / "run.exe"
    exe.write_text("", encoding="utf-8")

    assert voicevox.find_engine_exe(str(exe)) == os.path.abspath(str(exe))
    assert voicevox.candidate_paths("D:/no/such/run.exe") == ["D:/no/such/run.exe"]


def test_running_engine_is_used_as_is(engine, http):
    assert engine.ensure_ready() == "0.25.2"
    assert any("/version" in url for url in http.calls)


def test_engine_is_not_started_twice(engine, http):
    engine.ensure_ready()
    engine.ensure_ready()

    assert sum("/version" in url for url in http.calls) == 1


def test_unreachable_engine_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(voicevox, "_request", FakeHttp(missing=True))
    monkeypatch.setattr(voicevox, "find_engine_exe", lambda explicit=None: None)

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        VoicevoxEngine(url="http://127.0.0.1:50021").ensure_ready()

    message = str(excinfo.value)
    assert "VOICEVOX" in message
    assert "--engine sapi" in message  # 代わりの方法も示す
    assert "探した場所" in message


def test_autostart_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(voicevox, "_request", FakeHttp(missing=True))

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        VoicevoxEngine(autostart=False).ensure_ready()

    assert "起動してから" in str(excinfo.value)


def test_url_can_be_set_by_env(monkeypatch, http):
    monkeypatch.setenv("VOICEVOX_URL", "http://127.0.0.1:60000")

    assert VoicevoxEngine().base_url == "http://127.0.0.1:60000"


# ---------------------------------------------------------------------------
# 音声の選択
# ---------------------------------------------------------------------------


def test_styles_are_listed_with_their_speaker(engine):
    names = [style.name for style in engine.list_styles()]

    assert "No.7/アナウンス" in names
    assert "ずんだもん/ノーマル" in names


def test_style_list_is_read_once(engine, http):
    engine.list_styles()
    engine.list_styles()

    assert sum("/speakers" in url for url in http.calls) == 1


def test_narration_style_is_preferred(engine):
    """読み上げが目的の落ち着いた声を既定にする。"""
    assert engine.pick_style().name == "No.7/アナウンス"


def test_first_talk_style_is_used_when_none_is_preferred(engine, monkeypatch):
    monkeypatch.setattr(voicevox, "PREFERRED_STYLES", ())

    style = engine.pick_style()

    assert style.name == "No.7/ノーマル"
    assert style.kind == "talk"  # 歌唱用のスタイルは選ばない


def test_style_can_be_named(engine):
    assert engine.pick_style("ずんだもん/ノーマル").id == 3


def test_speaker_alone_picks_its_first_talk_style(engine):
    assert engine.pick_style("No.7").name == "No.7/ノーマル"


def test_unknown_style_says_how_to_look(engine):
    with pytest.raises(SpeechNotAvailableError) as excinfo:
        engine.pick_style("そんな話者")

    assert "--list-voices" in str(excinfo.value)


def test_credit_is_the_character_name(engine):
    assert engine.credit_for("No.7/アナウンス") == "VOICEVOX:No.7"
    assert Style("ずんだもん", "ノーマル", 3).credit == "VOICEVOX:ずんだもん"


def test_missing_speakers_is_an_error(engine, monkeypatch):
    monkeypatch.setattr(voicevox, "_request", lambda url, data=None, timeout=60.0: b"[]")
    engine._ready = True

    with pytest.raises(SynthesisError):
        engine.list_styles(refresh=True)


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


def jobs_in(tmp_path, texts):
    return [
        SpeechJob(index=i, text=text, out_path=str(tmp_path / f"part_{i}.wav"), slide=i)
        for i, text in enumerate(texts, start=1)
    ]


def test_each_job_becomes_a_wav(engine, tmp_path, http):
    jobs = jobs_in(tmp_path, ["こんにちは。", "さようなら。"])

    report = engine.synthesize(jobs, str(tmp_path / "work"), timeout=30)

    assert report.ok
    assert all(os.path.isfile(job.out_path) for job in jobs)
    assert report.voice == "No.7/アナウンス"
    assert sum("/synthesis" in url for url in http.calls) == 2


def test_speech_settings_are_sent(engine, tmp_path, http):
    engine.synthesize(
        jobs_in(tmp_path, ["こんにちは。"]),
        str(tmp_path / "work"),
        speed=0.9,
        pitch=0.03,
        intonation=1.2,
        volume=80,
        sample_rate=48000,
    )

    payload = http.payloads[0]
    assert payload["speedScale"] == 0.9
    assert payload["pitchScale"] == 0.03
    assert payload["intonationScale"] == 1.2
    assert payload["volumeScale"] == pytest.approx(0.8)
    assert payload["outputSamplingRate"] == 48000
    assert payload["outputStereo"] is False
    # 前後の余白は音声を組み立てる側で入れ直すので、エンジンには付けさせない。
    assert payload["prePhonemeLength"] == 0.0
    assert payload["postPhonemeLength"] == 0.0


def test_the_chosen_style_is_used(engine, tmp_path, http):
    engine.synthesize(jobs_in(tmp_path, ["あ。"]), str(tmp_path / "work"), voice="ずんだもん/ノーマル")

    assert any("/synthesis?speaker=3" in url for url in http.calls)


def test_query_is_kept_for_reproducing_a_failure(engine, tmp_path):
    workdir = tmp_path / "work"

    engine.synthesize(jobs_in(tmp_path, ["こんにちは。"]), str(workdir), timeout=30)

    saved = json.loads((workdir / "query_0001.json").read_text(encoding="utf-8"))
    assert saved["outputSamplingRate"] == voicevox.DEFAULT_SAMPLE_RATE


def test_one_failure_does_not_stop_the_rest(monkeypatch, tmp_path):
    fake = FakeHttp(fail_on=["%E3%81%84"])  # 2 件目(「い。」)だけ失敗させる
    monkeypatch.setattr(voicevox, "_request", fake)
    engine = VoicevoxEngine(autostart=False)
    jobs = jobs_in(tmp_path, ["あ。", "い。", "う。"])

    report = engine.synthesize(jobs, str(tmp_path / "work"), timeout=30)

    assert [failure.index for failure in report.failures] == [2]
    assert "422" in report.failures[0].message
    assert "だめ" in report.failures[0].message  # エンジンの応答をそのまま残す
    assert os.path.isfile(jobs[2].out_path)  # 残りは合成されている


def test_failure_shows_a_command_to_retry(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(voicevox, "_request", FakeHttp(fail_on=["speaker"]))
    engine._ready = True
    engine._styles = [Style("No.7", "アナウンス", 30)]

    report = engine.synthesize(jobs_in(tmp_path, ["あ。"]), str(tmp_path / "work"))

    assert "curl" in report.command
    assert any("/synthesis?speaker=30" in part for part in report.command)


def test_timeout_is_reported_per_job(engine, tmp_path):
    report = engine.synthesize(jobs_in(tmp_path, ["あ。", "い。"]), str(tmp_path / "work"), timeout=0)

    assert len(report.failures) == 2
    assert "--timeout" in report.failures[0].message


def test_no_jobs_needs_no_call(engine, http):
    report = engine.synthesize([], "workdir")

    assert report.ok
    assert not http.calls


def test_engine_reports_its_output_format(engine):
    assert engine.honors_sample_rate()
    assert engine.default_format().sample_rate == voicevox.DEFAULT_SAMPLE_RATE
    assert engine.default_format(24000).sample_rate == 24000
