"""動画コマンドの入出力と終了コードを確認する。"""

import json
import os

import numpy as np
import pytest
from PIL import Image

from note2slides import ffmpeg as ffmpeg_mod
from note2slides import video_cli
from note2slides.waveform import Waveform, write_wav

requires_ffmpeg = pytest.mark.skipif(
    ffmpeg_mod.find_ffmpeg() is None, reason="ffmpeg が見つかりません"
)

SMALL = ["--width", "320", "--height", "180", "--fps", "10", "--preset", "ultrafast", "--crf", "30"]


def make_materials(tmp_path, count=2, seconds=0.5, name="sample"):
    """書き出し済みの素材(スライド画像とナレーション音声)を用意する。"""
    slides = tmp_path / f"{name}_slides"
    audio = tmp_path / f"{name}_audio"
    slides.mkdir()
    audio.mkdir()
    for index in range(1, count + 1):
        Image.new("RGB", (320, 180), (40 * index, 80, 200)).save(
            str(slides / f"slide_{index:03d}.png")
        )
        write_wav(
            str(audio / f"narration_{index:03d}.wav"),
            Waveform(np.zeros(int(seconds * 48000)), 48000),
        )
    return str(slides), str(audio)


# ---------------------------------------------------------------------------
# 素材から動画を作る
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_builds_a_video_from_materials(tmp_path, capsys):
    slides, audio = make_materials(tmp_path)
    out = str(tmp_path / "movie.mp4")

    code = video_cli.main(["--slides", slides, "--audio", audio, "-o", out, *SMALL])

    assert code == video_cli.EXIT_OK
    assert os.path.getsize(out) > 0
    printed = capsys.readouterr().out
    assert "動画を書き出しました" in printed
    assert "16:9" in printed
    assert "スライド 2 枚" in printed


@requires_ffmpeg
def test_output_defaults_to_the_name_of_the_slides_directory(tmp_path):
    slides, audio = make_materials(tmp_path, name="lesson")

    code = video_cli.main(["--slides", slides, "--audio", audio, "--quiet", *SMALL])

    assert code == video_cli.EXIT_OK
    assert (tmp_path / "lesson.mp4").is_file()
    assert (tmp_path / "lesson_video.json").is_file()


@requires_ffmpeg
def test_manifest_lists_when_each_slide_appears(tmp_path):
    slides, audio = make_materials(tmp_path, count=3, seconds=0.4)
    out = str(tmp_path / "movie.mp4")

    video_cli.main(["--slides", slides, "--audio", audio, "-o", out, "--quiet", *SMALL])

    with open(tmp_path / "movie_video.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert [s["index"] for s in manifest["slides"]] == [1, 2, 3]
    assert manifest["slides"][0]["start"] == 0.0
    assert manifest["slides"][1]["start"] == manifest["slides"][0]["duration"]
    assert manifest["materials"]["slides"] == os.path.abspath(slides)


@requires_ffmpeg
def test_credit_is_carried_over_from_the_audio(tmp_path, capsys):
    slides, audio = make_materials(tmp_path)
    with open(os.path.join(audio, "narration.json"), "w", encoding="utf-8") as f:
        json.dump({"credit": "VOICEVOX:No.7"}, f, ensure_ascii=False)

    video_cli.main(["--slides", slides, "--audio", audio, "-o", str(tmp_path / "m.mp4"), *SMALL])

    assert "VOICEVOX:No.7" in capsys.readouterr().out


@requires_ffmpeg
def test_existing_video_needs_force(tmp_path, capsys):
    slides, audio = make_materials(tmp_path)
    out = str(tmp_path / "movie.mp4")
    args = ["--slides", slides, "--audio", audio, "-o", out, "--quiet", *SMALL]
    assert video_cli.main(args) == video_cli.EXIT_OK

    assert video_cli.main(args) == video_cli.EXIT_EXISTS
    assert "--force" in capsys.readouterr().err
    assert video_cli.main(args + ["-f"]) == video_cli.EXIT_OK


# ---------------------------------------------------------------------------
# 指定の誤りと失敗
# ---------------------------------------------------------------------------


def test_no_input_at_all(capsys):
    assert video_cli.main([]) == video_cli.EXIT_USAGE
    assert "--slides" in capsys.readouterr().err


def test_missing_input_file(tmp_path, capsys):
    assert video_cli.main([str(tmp_path / "none.pptx")]) == video_cli.EXIT_USAGE
    assert "見つかりません" in capsys.readouterr().err


def test_slides_without_audio(tmp_path, capsys):
    slides, _ = make_materials(tmp_path)

    assert video_cli.main(["--slides", slides]) == video_cli.EXIT_USAGE
    assert "--audio" in capsys.readouterr().err


def test_materials_that_do_not_match(tmp_path, capsys):
    slides, audio = make_materials(tmp_path, count=3)
    os.remove(os.path.join(audio, "narration_003.wav"))

    code = video_cli.main(["--slides", slides, "--audio", audio, "-o", str(tmp_path / "m.mp4")])

    assert code == video_cli.EXIT_FAILED
    assert "対応が取れません" in capsys.readouterr().err


def test_invalid_screen_size(tmp_path, capsys):
    slides, audio = make_materials(tmp_path)

    code = video_cli.main(["--slides", slides, "--audio", audio, "--width", "321"])

    assert code == video_cli.EXIT_USAGE
    assert "偶数" in capsys.readouterr().err


def test_invalid_background_color(tmp_path, capsys):
    slides, audio = make_materials(tmp_path)

    code = video_cli.main(["--slides", slides, "--audio", audio, "--background", "みどり"])

    assert code == video_cli.EXIT_USAGE
    assert "#RRGGBB" in capsys.readouterr().err


def test_missing_ffmpeg_is_its_own_exit_code(tmp_path, capsys, monkeypatch):
    """外部ツールが無いだけの失敗は、生成の失敗と区別できるようにする。"""
    slides, audio = make_materials(tmp_path)
    monkeypatch.setattr(ffmpeg_mod, "find_ffmpeg", lambda explicit=None: None)

    code = video_cli.main(["--slides", slides, "--audio", audio, "-o", str(tmp_path / "m.mp4")])

    assert code == video_cli.EXIT_NO_TOOL
    assert "ffmpeg が見つかりません" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value, expected",
    [("#ffffff", (255, 255, 255)), ("000000", (0, 0, 0)), ("#1a2B3c", (26, 43, 60))],
)
def test_background_color_is_read_as_rgb(value, expected):
    assert video_cli._color(value) == expected


def test_default_output_drops_the_materials_suffix():
    assert video_cli._default_output(os.path.join("build", "sample_slides")).endswith(
        os.path.join("build", "sample.mp4")
    )


# ---------------------------------------------------------------------------
# 環境の確認
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_check_reports_ffmpeg(capsys):
    assert video_cli._check_ffmpeg(None) is True

    printed = capsys.readouterr().out
    assert "ffmpeg:" in printed
    assert "libx264" in printed and "aac" in printed


def test_check_reports_a_missing_ffmpeg(capsys, monkeypatch):
    monkeypatch.setattr(ffmpeg_mod, "find_ffmpeg", lambda explicit=None: None)

    assert video_cli._check_ffmpeg(None) is False
    assert "見つかりません" in capsys.readouterr().err
