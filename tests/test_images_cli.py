"""スライド画像コマンドの入出力と終了コードを確認する。"""

from note2slides import images_cli
from test_slide_images import make_pdf  # PDF の組み立てを共用する


def test_converts_a_pdf(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=2)
    outdir = tmp_path / "out"

    code = images_cli.main([pdf, "-o", str(outdir), "--width", "640"])

    assert code == images_cli.EXIT_OK
    assert sorted(p.name for p in outdir.glob("*.png")) == ["slide_001.png", "slide_002.png"]
    out = capsys.readouterr().out
    assert "2 枚" in out
    assert "640x360" in out
    assert "slide_%03d.png" in out  # 後続の動画生成で使うパターンを案内する


def test_default_outdir_is_next_to_the_input(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)

    assert images_cli.main([pdf, "--width", "320"]) == images_cli.EXIT_OK

    assert (tmp_path / "deck_slides" / "slide_001.png").is_file()


def test_missing_input(tmp_path, capsys):
    code = images_cli.main([str(tmp_path / "none.pptx")])

    assert code == images_cli.EXIT_USAGE
    assert "見つかりません" in capsys.readouterr().err


def test_no_input(capsys):
    assert images_cli.main([]) == images_cli.EXIT_USAGE
    assert "指定してください" in capsys.readouterr().err


def test_existing_output_needs_force(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)
    outdir = str(tmp_path / "out")
    images_cli.main([pdf, "-o", outdir, "--width", "320"])

    code = images_cli.main([pdf, "-o", outdir, "--width", "320"])
    assert code == images_cli.EXIT_EXISTS
    assert "--force" in capsys.readouterr().err

    assert images_cli.main([pdf, "-o", outdir, "--width", "320", "-f"]) == images_cli.EXIT_OK


def test_missing_soffice_is_reported(tmp_path, capsys):
    # LibreOffice が要る経路(.pptx)で、指定した soffice が無い場合。
    from pptx import Presentation

    pptx_path = tmp_path / "deck.pptx"
    Presentation().save(str(pptx_path))

    code = images_cli.main(
        [str(pptx_path), "-o", str(tmp_path / "out"), "--soffice", str(tmp_path / "nope.exe")]
    )

    assert code == images_cli.EXIT_NO_SOFFICE
    err = capsys.readouterr().err
    assert "LibreOffice" in err
    assert "SOFFICE_PATH" in err


def test_broken_input_is_reported(tmp_path, capsys):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    code = images_cli.main([str(broken), "-o", str(tmp_path / "out")])

    assert code == images_cli.EXIT_CONVERT
    assert "変換に失敗しました" in capsys.readouterr().err


def test_invalid_size_is_reported(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)

    code = images_cli.main([pdf, "-o", str(tmp_path / "out"), "--width", "641"])

    assert code == images_cli.EXIT_CONVERT
    assert "偶数" in capsys.readouterr().err


def test_check_reports_tool_status(capsys):
    code = images_cli.main(["--check", "--soffice", "no-such-soffice"])

    captured = capsys.readouterr()
    assert code == images_cli.EXIT_NO_SOFFICE
    assert "LibreOffice: 見つかりません" in captured.err
    # 画像側の依存は入っているので、切り分けができる。
    assert "pypdfium2: 利用できます" in captured.out


def test_quiet_prints_nothing(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "deck.pdf", pages=1)

    images_cli.main([pdf, "-o", str(tmp_path / "out"), "--width", "320", "--quiet"])

    assert capsys.readouterr().out == ""
