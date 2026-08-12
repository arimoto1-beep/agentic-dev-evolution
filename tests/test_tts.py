"""音声合成の呼び出し(組み立てと結果の読み取り)を確認する。

実際の PowerShell は起動せず、subprocess.run を差し替えて確かめる。
実機での合成は tests/test_speech_compat.py で確認する。
"""

import json
import os
import subprocess

import pytest

from note2slides import tts
from note2slides.tts import (
    SpeechEngine,
    SpeechJob,
    SpeechNotAvailableError,
    SynthesisError,
    Voice,
)


@pytest.fixture
def fake_powershell(tmp_path, monkeypatch):
    path = tmp_path / "powershell.exe"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("POWERSHELL_PATH", str(path))
    return str(path)


def stub_run(monkeypatch, stdout="", stderr="", returncode=0, capture=None):
    def fake_run(command, capture_output=False, timeout=None):
        if capture is not None:
            capture.append(command)
        return subprocess.CompletedProcess(
            command, returncode, stdout.encode("utf-8"), stderr.encode("utf-8")
        )

    monkeypatch.setattr(tts.subprocess, "run", fake_run)


VOICE_LINES = (
    '{"kind":"voice","name":"Microsoft Ayumi","language":"ja-JP","gender":"Female"}\n'
    '{"kind":"voice","name":"Microsoft Zira","language":"en-US","gender":"Female"}\n'
)


# ---------------------------------------------------------------------------
# PowerShell の場所
# ---------------------------------------------------------------------------


def test_explicit_powershell_is_used(tmp_path):
    path = tmp_path / "pwsh.exe"
    path.write_text("", encoding="utf-8")

    assert tts.find_powershell(str(path)) == os.path.abspath(str(path))
    assert tts.candidate_paths("そんなパスはない") == ["そんなパスはない"]


def test_env_var_comes_first(fake_powershell):
    assert tts.candidate_paths()[0] == fake_powershell


def test_missing_powershell_says_where_it_looked(monkeypatch):
    monkeypatch.setattr(tts, "find_powershell", lambda explicit=None: None)

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        tts.require_powershell("D:/no/such/powershell.exe")

    assert "探した場所" in str(excinfo.value)


def test_unknown_engine(fake_powershell):
    with pytest.raises(SpeechNotAvailableError):
        SpeechEngine("そんなエンジン")


def test_script_is_packaged_with_a_bom():
    """PowerShell 5.1 は BOM の無いファイルを cp932 として読む。

    BOM を落とすと、スクリプト内の日本語が壊れて構文エラーになる。
    """
    with open(tts.script_path(), "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# 音声の一覧と選択
# ---------------------------------------------------------------------------


def test_voices_are_read_from_the_output(fake_powershell, monkeypatch):
    commands = []
    stub_run(monkeypatch, stdout=VOICE_LINES, capture=commands)

    voices = SpeechEngine("sapi").list_voices()

    assert [v.name for v in voices] == ["Microsoft Ayumi", "Microsoft Zira"]
    assert voices[0].engine == "sapi"
    assert "-ListVoices" in commands[0]
    assert commands[0][-1] == "sapi"
    assert tts.script_path() in commands[0]


def test_voice_list_is_read_once(fake_powershell, monkeypatch):
    commands = []
    stub_run(monkeypatch, stdout=VOICE_LINES, capture=commands)
    engine = SpeechEngine("sapi")

    engine.list_voices()
    engine.pick_voice()

    assert len(commands) == 1


def test_no_voice_is_an_error(fake_powershell, monkeypatch):
    stub_run(monkeypatch, stdout="", stderr="何かの失敗", returncode=1)

    with pytest.raises(SynthesisError) as excinfo:
        SpeechEngine("sapi").list_voices()

    # 原因を追えるように、実行したコマンドと出力を残す。
    assert "実行したコマンド" in str(excinfo.value)
    assert "何かの失敗" in str(excinfo.value)


def test_language_decides_the_default_voice(fake_powershell, monkeypatch):
    stub_run(monkeypatch, stdout=VOICE_LINES)

    assert SpeechEngine("sapi").pick_voice().name == "Microsoft Ayumi"
    assert SpeechEngine("sapi").pick_voice(language="en").name == "Microsoft Zira"


def test_named_voice_wins(fake_powershell, monkeypatch):
    stub_run(monkeypatch, stdout=VOICE_LINES)

    assert SpeechEngine("sapi").pick_voice("Microsoft Zira").name == "Microsoft Zira"


def test_unknown_voice_lists_what_is_available(fake_powershell, monkeypatch):
    stub_run(monkeypatch, stdout=VOICE_LINES)

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        SpeechEngine("sapi").pick_voice("そんな音声")

    assert "Microsoft Ayumi" in str(excinfo.value)


def test_missing_language_explains_how_to_add_it(fake_powershell, monkeypatch):
    stub_run(
        monkeypatch,
        stdout='{"kind":"voice","name":"Microsoft Zira","language":"en-US"}\n',
    )

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        SpeechEngine("sapi").pick_voice(language="ja")

    assert "言語と地域" in str(excinfo.value)


def test_auto_falls_back_to_the_next_engine(fake_powershell, monkeypatch):
    def fake_run(command, capture_output=False, timeout=None):
        # onecore には日本語の音声が無く、sapi にはある、という環境を作る。
        stdout = VOICE_LINES if command[-1] == "sapi" else '{"kind":"voice","name":"Zira","language":"en-US"}\n'
        return subprocess.CompletedProcess(command, 0, stdout.encode("utf-8"), b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    assert tts.select_engine("auto").name == "sapi"


def test_auto_reports_every_engine_it_tried(fake_powershell, monkeypatch):
    stub_run(monkeypatch, stdout='{"kind":"voice","name":"Zira","language":"en-US"}\n')

    with pytest.raises(SpeechNotAvailableError) as excinfo:
        tts.select_engine("auto")

    message = str(excinfo.value)
    assert "[onecore]" in message and "[sapi]" in message


def test_voice_language_match():
    voice = Voice("Ayumi", "ja-JP")

    assert voice.speaks("ja") and voice.speaks("ja-JP") and voice.speaks("")
    assert not voice.speaks("en")


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


def _jobs(tmp_path, count=2):
    return [
        SpeechJob(i, f"{i} 枚目の読み上げ", str(tmp_path / f"out_{i}.wav"))
        for i in range(1, count + 1)
    ]


def done_lines(*indexes):
    return "".join('{"kind":"done","index":%d,"ok":true}\n' % i for i in indexes)


def test_job_file_carries_text_as_utf8(fake_powershell, tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    commands = []

    def fake_run(command, capture_output=False, timeout=None):
        commands.append(command)
        for job in _jobs(tmp_path):  # 実際の合成の代わりにファイルを作る
            open(job.out_path, "wb").write(b"wav")
        return subprocess.CompletedProcess(command, 0, done_lines(1, 2).encode("utf-8"), b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    report = SpeechEngine("sapi").synthesize(
        _jobs(tmp_path), str(workdir), voice="Ayumi", speed=1.0, sample_rate=48000
    )

    assert report.ok
    job = json.loads((workdir / "job.json").read_text(encoding="utf-8"))
    assert job["engine"] == "sapi"
    assert job["voice"] == "Ayumi"
    assert job["sample_rate"] == 48000
    assert [item["index"] for item in job["items"]] == [1, 2]
    # 日本語は引数ではなくファイルで渡す(コマンドラインの文字コードに依存しない)。
    text_file = job["items"][0]["text_file"]
    with open(text_file, encoding="utf-8") as f:
        assert f.read() == "1 枚目の読み上げ"
    assert "-JobFile" in commands[0]


def test_failed_items_are_reported_individually(fake_powershell, tmp_path, monkeypatch):
    jobs = _jobs(tmp_path, 3)

    def fake_run(command, capture_output=False, timeout=None):
        open(jobs[0].out_path, "wb").write(b"wav")
        open(jobs[2].out_path, "wb").write(b"wav")
        stdout = (
            done_lines(1)
            + '{"kind":"done","index":2,"ok":false,"error":"音声が壊れています"}\n'
            + done_lines(3)
        )
        return subprocess.CompletedProcess(command, 0, stdout.encode("utf-8"), b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    finished = []

    report = SpeechEngine("sapi").synthesize(
        jobs, str(tmp_path / "work"), on_done=finished.append
    )

    # 1 件失敗しても残りは合成する。どれが失敗したかが分かる。
    assert [f.index for f in report.failures] == [2]
    assert "音声が壊れています" in report.failures[0].message
    assert finished == [1, 3]


def test_missing_output_counts_as_a_failure(fake_powershell, tmp_path, monkeypatch):
    stub_run(monkeypatch, stdout=done_lines(1, 2))

    report = SpeechEngine("sapi").synthesize(_jobs(tmp_path), str(tmp_path / "work"))

    assert [f.index for f in report.failures] == [1, 2]
    assert "作られませんでした" in report.failures[0].message


def test_result_without_any_report_is_an_error(fake_powershell, tmp_path, monkeypatch):
    stub_run(monkeypatch, stdout="", stderr="スクリプトが落ちました", returncode=1)

    with pytest.raises(SynthesisError) as excinfo:
        SpeechEngine("sapi").synthesize(_jobs(tmp_path), str(tmp_path / "work"))

    assert "スクリプトが落ちました" in str(excinfo.value)
    assert "終了コード: 1" in str(excinfo.value)


def test_timeout_says_what_to_do(fake_powershell, tmp_path, monkeypatch):
    def fake_run(command, capture_output=False, timeout=None):
        raise subprocess.TimeoutExpired(command, timeout, output=b"", stderr=b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    with pytest.raises(SynthesisError) as excinfo:
        SpeechEngine("sapi").synthesize(_jobs(tmp_path), str(tmp_path / "work"), timeout=1)

    assert "--timeout" in str(excinfo.value)


def test_no_jobs_needs_no_call(fake_powershell, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("呼ばれないはず")

    monkeypatch.setattr(tts.subprocess, "run", fail)

    assert SpeechEngine("sapi").synthesize([], "work").ok


# ---------------------------------------------------------------------------
# 出力形式と速度
# ---------------------------------------------------------------------------


def test_sample_rate_is_only_for_sapi(fake_powershell):
    assert SpeechEngine("sapi").honors_sample_rate()
    assert SpeechEngine("sapi").default_format(24000).sample_rate == 24000
    assert not SpeechEngine("onecore").honors_sample_rate()
    assert SpeechEngine("onecore").default_format().sample_rate == tts.ONECORE_SAMPLE_RATE


def test_speed_becomes_a_sapi_rate():
    assert tts.sapi_rate(1.0) == 0
    assert tts.sapi_rate(1.4) == 1
    assert tts.sapi_rate(0.7) == -1
    # 範囲外は SAPI が受け付ける範囲に丸める。
    assert tts.sapi_rate(1000) == 10
    assert tts.sapi_rate(0.001) == -10


def test_non_json_output_is_ignored():
    lines = '警告: 何か\n{"kind":"done","index":1,"ok":true}\n壊れた {json\n'

    assert tts._parse_lines(lines) == [{"kind": "done", "index": 1, "ok": True}]
