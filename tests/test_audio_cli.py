"""ナレーション音声コマンドの入出力と終了コードを確認する。"""

import json

import pytest

from note2slides import audio, audio_cli, tts, voicevox
from test_audio import FakeEngine, make_script  # 偽エンジンと原稿の組み立てを共用する


@pytest.fixture(autouse=True)
def without_voicevox(monkeypatch):
    """VOICEVOX が入っている環境でも、結果が変わらないようにする。

    --check は使える方式をすべて調べるため、これが無いと実行環境しだいで
    結果が変わる。VOICEVOX を含む --check は test_check_reports_voicevox で見る。
    """
    monkeypatch.setattr(voicevox, "find_engine_exe", lambda explicit=None: None)
    monkeypatch.setattr(voicevox.VoicevoxEngine, "_probe", lambda self, timeout=3.0: None)


@pytest.fixture
def fake_engine(monkeypatch):
    """音声合成の代わりに偽エンジンを使う(PowerShell を起動しない)。"""
    engine = FakeEngine()
    monkeypatch.setattr(audio.tts_mod, "select_engine", lambda *args, **kwargs: engine)
    return engine


def test_generates_one_file_per_slide(tmp_path, capsys, fake_engine):
    source = make_script(tmp_path, ["一枚目です。", "二枚目です。"])
    outdir = tmp_path / "audio"

    code = audio_cli.main([source, "-o", str(outdir)])

    assert code == audio_cli.EXIT_OK
    assert sorted(p.name for p in outdir.glob("*.wav")) == [
        "narration_001.wav",
        "narration_002.wav",
    ]
    out = capsys.readouterr().out
    assert "2 枚分" in out
    assert "narration.json" in out
    assert "narration_%03d.wav" in out  # 後続の動画生成で使うパターンを案内する


def test_default_outdir_is_next_to_the_input(tmp_path, fake_engine):
    source = make_script(tmp_path, ["あ"])

    assert audio_cli.main([source, "--quiet"]) == audio_cli.EXIT_OK

    assert (tmp_path / "script_audio" / "narration_001.wav").is_file()


def test_options_reach_the_engine(tmp_path, fake_engine):
    source = make_script(tmp_path, ["あ"])

    code = audio_cli.main(
        [
            source,
            "-o",
            str(tmp_path / "audio"),
            "--voice",
            "指定した音声",
            "--speed",
            "0.9",
            "--volume",
            "80",
            "--quiet",
        ]
    )

    assert code == audio_cli.EXIT_OK
    call = fake_engine.calls[0]
    assert call["voice"] == "指定した音声"
    assert call["speed"] == 0.9
    assert call["volume"] == 80


def test_silence_length_is_configurable(tmp_path, fake_engine):
    source = make_script(tmp_path, ["あ", ""])

    audio_cli.main([source, "-o", str(tmp_path / "audio"), "--silence", "3", "--quiet"])

    with open(tmp_path / "audio" / "narration.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["clips"][1]["duration"] == 3.0
    assert manifest["clips"][1]["silent"] is True


def test_script_only_writes_the_script(tmp_path, capsys, fake_engine):
    source = make_script(tmp_path, ["読み方を直したい文章"])
    outdir = tmp_path / "audio"

    code = audio_cli.main([source, "-o", str(outdir), "--script-only"])

    assert code == audio_cli.EXIT_OK
    assert not list(outdir.glob("*.wav"))
    with open(outdir / audio.SCRIPT_NAME, encoding="utf-8") as f:
        assert json.load(f)["segments"][0]["text"] == "読み方を直したい文章"
    assert "原稿" in capsys.readouterr().out


def test_dump_script_alongside_audio(tmp_path, fake_engine):
    source = make_script(tmp_path, ["あ"])
    dumped = tmp_path / "out" / "script.json"

    audio_cli.main([source, "-o", str(tmp_path / "audio"), "--dump-script", str(dumped), "--quiet"])

    assert dumped.is_file()
    assert (tmp_path / "audio" / "narration_001.wav").is_file()


# ---------------------------------------------------------------------------
# 終了コード
# ---------------------------------------------------------------------------


def test_missing_input(tmp_path, capsys):
    code = audio_cli.main([str(tmp_path / "none.pptx")])

    assert code == audio_cli.EXIT_USAGE
    assert "見つかりません" in capsys.readouterr().err


def test_no_input(capsys):
    assert audio_cli.main([]) == audio_cli.EXIT_USAGE
    assert "指定してください" in capsys.readouterr().err


def test_unsupported_input(tmp_path, capsys, fake_engine):
    source = tmp_path / "article.md"
    source.write_text("# 記事", encoding="utf-8")

    code = audio_cli.main([str(source)])

    assert code == audio_cli.EXIT_USAGE
    assert "未対応" in capsys.readouterr().err


def test_existing_output_needs_force(tmp_path, capsys, fake_engine):
    source = make_script(tmp_path, ["あ"])
    outdir = str(tmp_path / "audio")
    audio_cli.main([source, "-o", outdir, "--quiet"])

    code = audio_cli.main([source, "-o", outdir, "--quiet"])

    assert code == audio_cli.EXIT_EXISTS
    assert "--force" in capsys.readouterr().err
    assert audio_cli.main([source, "-o", outdir, "--quiet", "-f"]) == audio_cli.EXIT_OK


def test_synthesis_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        audio.tts_mod, "select_engine", lambda *args, **kwargs: FakeEngine(fail=[1])
    )
    source = make_script(tmp_path, ["あ"])

    code = audio_cli.main([source, "-o", str(tmp_path / "audio")])

    assert code == audio_cli.EXIT_SYNTHESIS
    err = capsys.readouterr().err
    assert "スライド 1" in err
    assert "実行したコマンド" in err


def test_no_engine(tmp_path, capsys, monkeypatch):
    def unavailable(*args, **kwargs):
        raise tts.SpeechNotAvailableError("日本語の音声がありません")

    monkeypatch.setattr(audio.tts_mod, "select_engine", unavailable)
    source = make_script(tmp_path, ["あ"])

    code = audio_cli.main([source, "-o", str(tmp_path / "audio")])

    assert code == audio_cli.EXIT_NO_ENGINE
    assert "日本語の音声がありません" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def test_check_lists_voices(monkeypatch, capsys, tmp_path):
    powershell = tmp_path / "powershell.exe"
    powershell.write_text("", encoding="utf-8")
    monkeypatch.setattr(tts, "find_powershell", lambda explicit=None: str(powershell))
    monkeypatch.setattr(
        tts.SpeechEngine,
        "list_voices",
        lambda self, timeout=120, refresh=False: [
            tts.Voice("Microsoft Ayumi", "ja-JP", "Female", self.name),
            tts.Voice("Microsoft Zira", "en-US", "Female", self.name),
        ],
    )

    code = audio_cli.main(["--check"])

    assert code == audio_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Microsoft Ayumi" in out
    assert "speech.ps1" in out


def test_check_reports_a_missing_powershell(monkeypatch, capsys):
    monkeypatch.setattr(tts, "find_powershell", lambda explicit=None: None)

    assert audio_cli.main(["--check"]) == audio_cli.EXIT_NO_ENGINE
    assert "PowerShell" in capsys.readouterr().err


def test_check_reports_voicevox(monkeypatch, capsys):
    """VOICEVOX が使える場合は、既定の音声とクレジット表記まで示す。"""
    styles = [
        voicevox.Style("No.7", "アナウンス", 30),
        voicevox.Style("ずんだもん", "ノーマル", 3),
    ]
    monkeypatch.setattr(voicevox.VoicevoxEngine, "_probe", lambda self, timeout=3.0: "0.25.2")
    monkeypatch.setattr(voicevox.VoicevoxEngine, "list_styles", lambda self, refresh=False: styles)

    code = audio_cli.main(["--check", "--engine", "voicevox", "--list-voices"])

    out = capsys.readouterr().out
    assert code == audio_cli.EXIT_OK
    assert "voicevox: 使えます" in out
    assert "0.25.2" in out
    assert "No.7/アナウンス" in out  # ナレーション向きの声を既定に選ぶ
    assert "VOICEVOX:No.7" in out  # 公開時に必要な表示
    assert "ずんだもん/ノーマル" in out


def test_check_reports_an_unreachable_voicevox(monkeypatch, capsys):
    code = audio_cli.main(["--check", "--engine", "voicevox", "--voicevox-url", "http://127.0.0.1:1"])

    assert code == audio_cli.EXIT_NO_ENGINE
    err = capsys.readouterr().err
    assert "voicevox: 使えません" in err
    assert "http://127.0.0.1:1" in err


def test_check_reports_a_missing_language(monkeypatch, capsys, tmp_path):
    powershell = tmp_path / "powershell.exe"
    powershell.write_text("", encoding="utf-8")
    monkeypatch.setattr(tts, "find_powershell", lambda explicit=None: str(powershell))
    monkeypatch.setattr(
        tts.SpeechEngine,
        "list_voices",
        lambda self, timeout=120, refresh=False: [tts.Voice("Zira", "en-US", "Female", self.name)],
    )

    code = audio_cli.main(["--check"])

    assert code == audio_cli.EXIT_NO_ENGINE
    assert "音声がありません" in capsys.readouterr().err
