"""LibreOffice 呼び出しの組み立てと、失敗時の情報を確認する。

実際に LibreOffice は起動せず、subprocess.run を差し替えて確かめる。
"""

import os
import subprocess

import pytest

from note2slides import soffice


def _fake_exe(tmp_path, name="soffice.exe"):
    path = tmp_path / name
    path.write_text("", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 探索
# ---------------------------------------------------------------------------


def test_explicit_path_is_used(tmp_path):
    path = _fake_exe(tmp_path)
    assert soffice.find_soffice(path) == os.path.abspath(path)


def test_explicit_path_does_not_fall_back(tmp_path):
    # 指定を間違えたときに、たまたま入っている別の LibreOffice を使わない。
    assert soffice.candidate_paths("そんなパスはない") == ["そんなパスはない"]
    assert soffice.find_soffice("そんなパスはない") is None


def test_console_variant_is_preferred(tmp_path):
    # soffice.exe は何も出力しないため、隣にある soffice.com を使う。
    exe = _fake_exe(tmp_path, "soffice.exe")
    com = _fake_exe(tmp_path, "soffice.com")

    assert soffice.find_soffice(exe) == com


def test_console_variant_is_optional(tmp_path):
    exe = _fake_exe(tmp_path, "soffice.exe")

    assert soffice.find_soffice(exe) == os.path.abspath(exe)


def test_env_var_comes_first(tmp_path, monkeypatch):
    path = _fake_exe(tmp_path)
    monkeypatch.setenv("SOFFICE_PATH", path)
    assert soffice.candidate_paths()[0] == path
    assert soffice.find_soffice() == os.path.abspath(path)


def test_not_found_message_shows_where_it_looked():
    with pytest.raises(soffice.SofficeNotFoundError) as excinfo:
        soffice.require_soffice("D:/no/such/soffice.exe")

    message = str(excinfo.value)
    assert "SOFFICE_PATH" in message
    assert "D:/no/such/soffice.exe" in message


# ---------------------------------------------------------------------------
# コマンドの組み立て
# ---------------------------------------------------------------------------


def test_command_converts_to_pdf_headlessly(tmp_path):
    command = soffice.build_command(
        "soffice", str(tmp_path / "deck.pptx"), str(tmp_path / "out"), str(tmp_path / "p")
    )

    assert "--headless" in command
    assert command[command.index("--convert-to") + 1] == "pdf"
    assert command[command.index("--outdir") + 1] == os.path.abspath(tmp_path / "out")
    assert command[-1] == os.path.abspath(tmp_path / "deck.pptx")


def test_command_uses_a_dedicated_profile(tmp_path):
    # 起動中の LibreOffice に変換要求を吸われないよう、専用プロファイルを渡す。
    command = soffice.build_command("soffice", "a.pptx", "out", str(tmp_path / "prof"))

    profile = next(p for p in command if p.startswith("-env:UserInstallation="))
    url = profile.split("=", 1)[1]
    assert url.startswith("file:///")
    assert "\\" not in url


# ---------------------------------------------------------------------------
# 失敗時の情報
# ---------------------------------------------------------------------------


def test_failure_reports_command_and_output(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"Error: source file could not be loaded")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(soffice.SofficeConversionError) as excinfo:
        soffice.convert_to_pdf("deck.pptx", str(tmp_path / "out"), soffice=_fake_exe(tmp_path))

    message = str(excinfo.value)
    assert "PDF を出力しませんでした" in message
    assert "--convert-to pdf" in message  # 手元で再実行できるコマンドが載る
    assert "終了コード: 1" in message
    assert "source file could not be loaded" in message


def test_silent_failure_is_explained(tmp_path, monkeypatch):
    # 終了コード 0 で何も出力しない場合が実際にある。
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"", b"")
    )

    with pytest.raises(soffice.SofficeConversionError) as excinfo:
        soffice.convert_to_pdf("deck.pptx", str(tmp_path / "out"), soffice=_fake_exe(tmp_path))

    assert "起動中" in str(excinfo.value)


def test_timeout_is_reported(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 5, output=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(soffice.SofficeConversionError, match="秒以内に終了しませんでした"):
        soffice.convert_to_pdf(
            "deck.pptx", str(tmp_path / "out"), soffice=_fake_exe(tmp_path), timeout=5
        )


def test_empty_pdf_is_treated_as_failure(tmp_path):
    outdir = tmp_path / "out"

    def fake_run(command, **kwargs):
        outdir.mkdir(exist_ok=True)
        (outdir / "deck.pdf").write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "run", fake_run)
        with pytest.raises(soffice.SofficeConversionError, match="空です"):
            soffice.convert_to_pdf("deck.pptx", str(outdir), soffice=_fake_exe(tmp_path))


def test_successful_conversion_returns_the_pdf(tmp_path):
    outdir = tmp_path / "out"

    def fake_run(command, **kwargs):
        outdir.mkdir(exist_ok=True)
        (outdir / "deck.pdf").write_bytes(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(command, 0, b"convert deck.pptx", b"")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "run", fake_run)
        pdf = soffice.convert_to_pdf(
            str(tmp_path / "deck.pptx"), str(outdir), soffice=_fake_exe(tmp_path)
        )

    assert pdf == os.path.abspath(outdir / "deck.pdf")
