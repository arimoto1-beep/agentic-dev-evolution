"""聴き比べコマンドの入出力と終了コードを確認する。"""

import json
import os

import pytest

from note2slides import voice_compare, voice_compare_cli, voicevox
from test_audio import FakeEngine, make_script


@pytest.fixture
def fake_engine(monkeypatch):
    """音声合成の代わりに偽エンジンを使う(VOICEVOX を起動しない)。"""
    engine = FakeEngine()
    monkeypatch.setattr(voice_compare.tts_mod, "select_engine", lambda *a, **kw: engine)
    return engine


@pytest.fixture
def candidates(monkeypatch):
    picked = [
        voice_compare.Candidate(id="alpha", voice="話者A/ノーマル", reason="基準にする声"),
        voice_compare.Candidate(id="beta", voice="話者B/ノーマル", reason="別の声と比べる"),
    ]
    monkeypatch.setattr(voice_compare_cli, "CANDIDATES", picked)
    return picked


def test_generates_material_for_every_candidate(tmp_path, capsys, fake_engine, candidates):
    source = make_script(tmp_path, ["一枚目です。", "二枚目です。"])
    outdir = tmp_path / "voices"

    code = voice_compare_cli.main([source, "-o", str(outdir)])

    assert code == voice_compare_cli.EXIT_OK
    assert (outdir / "alpha" / voice_compare.CONTINUOUS_NAME).is_file()
    assert (outdir / "beta" / voice_compare.CONTINUOUS_NAME).is_file()
    assert (outdir / voice_compare.PREVIEW_NAME).is_file()
    out = capsys.readouterr().out
    assert "2 候補の音声を出力しました" in out
    assert "標準の音声は変更していません" in out


def test_existing_output_needs_force(tmp_path, fake_engine, candidates):
    source = make_script(tmp_path, ["一枚目です。"])
    outdir = tmp_path / "voices"

    assert voice_compare_cli.main([source, "-o", str(outdir)]) == voice_compare_cli.EXIT_OK
    assert voice_compare_cli.main([source, "-o", str(outdir)]) == voice_compare_cli.EXIT_EXISTS
    assert voice_compare_cli.main([source, "-o", str(outdir), "-f"]) == voice_compare_cli.EXIT_OK


def test_only_narrows_the_comparison(tmp_path, fake_engine, candidates):
    source = make_script(tmp_path, ["一枚目です。"])
    outdir = tmp_path / "voices"

    code = voice_compare_cli.main([source, "-o", str(outdir), "--only", "beta"])

    assert code == voice_compare_cli.EXIT_OK
    index = json.loads((outdir / voice_compare.INDEX_NAME).read_text(encoding="utf-8"))
    assert [c["id"] for c in index["candidates"]] == ["beta"]


def test_unknown_only_is_reported(tmp_path, capsys, fake_engine, candidates):
    source = make_script(tmp_path, ["一枚目です。"])

    code = voice_compare_cli.main([source, "-o", str(tmp_path / "voices"), "--only", "gamma"])

    assert code == voice_compare_cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "gamma" in err and "alpha, beta" in err


def test_failed_candidate_exits_with_synthesis_error(tmp_path, capsys, monkeypatch, candidates):
    monkeypatch.setattr(
        voice_compare.tts_mod, "select_engine", lambda *a, **kw: FakeEngine(fail=[1])
    )
    source = make_script(tmp_path, ["一枚目です。"])

    code = voice_compare_cli.main([source, "-o", str(tmp_path / "voices")])

    assert code == voice_compare_cli.EXIT_SYNTHESIS
    err = capsys.readouterr().err
    assert "偽の失敗" in err  # 原因がそのまま出る
    assert "alpha" in err


def test_missing_input_is_reported(capsys, candidates):
    assert voice_compare_cli.main([]) == voice_compare_cli.EXIT_USAGE
    assert "指定してください" in capsys.readouterr().err
    assert voice_compare_cli.main(["no_such_file.md"]) == voice_compare_cli.EXIT_USAGE
    assert "見つかりません" in capsys.readouterr().err


def test_list_candidates_shows_reasons(capsys):
    """どの声をなぜ候補にしたかを、音声を作らずに確認できる。"""
    code = voice_compare_cli.main(["--list-candidates"])

    assert code == voice_compare_cli.EXIT_OK
    out = capsys.readouterr().out
    for candidate in voice_compare.CANDIDATES:
        assert candidate.id in out
        assert candidate.voice in out
        assert candidate.reason[:20] in out


def test_broken_candidates_file_is_reported(tmp_path, capsys):
    path = tmp_path / "candidates.json"
    path.write_text("[", encoding="utf-8")

    code = voice_compare_cli.main(["--list-candidates", "--candidates", str(path)])

    assert code == voice_compare_cli.EXIT_USAGE
    assert "JSON" in capsys.readouterr().err


def test_no_engine_is_reported(tmp_path, capsys, monkeypatch, candidates):
    monkeypatch.setattr(voicevox, "find_engine_exe", lambda explicit=None, edition=None: None)
    monkeypatch.setattr(voicevox.VoicevoxEngine, "_probe", lambda self, timeout=3.0: None)
    monkeypatch.setattr(voice_compare.tts_mod, "find_powershell", lambda explicit=None: None)
    source = make_script(tmp_path, ["一枚目です。"])

    code = voice_compare_cli.main([source, "-o", str(tmp_path / "voices")])

    assert code == voice_compare_cli.EXIT_NO_ENGINE
    assert "音声合成" in capsys.readouterr().err


def test_article_can_be_given_directly(tmp_path, fake_engine, candidates):
    article = tmp_path / "article.md"
    article.write_text("# 見出し\n\n本文です。\n", encoding="utf-8")
    outdir = tmp_path / "voices"

    code = voice_compare_cli.main([str(article), "-o", str(outdir)])

    assert code == voice_compare_cli.EXIT_OK
    assert (outdir / voice_compare.SOURCE_NAME).is_file()
    assert os.path.isfile(outdir / voice_compare.REPORT_NAME)


def test_invalid_loudness_is_reported(tmp_path, capsys, fake_engine, candidates):
    source = make_script(tmp_path, ["一枚目です。"])

    code = voice_compare_cli.main([source, "-o", str(tmp_path / "voices"), "--loudness", "-100"])

    assert code == voice_compare_cli.EXIT_USAGE
    assert "LUFS" in capsys.readouterr().err
