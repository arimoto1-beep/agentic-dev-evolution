"""ffmpeg を実際に呼んで、書き出した動画の中身を確認する。

小さい画面・低いコマ数で書き出すので、1 本あたり 1 秒もかからない。
確認したいのは画質ではなく「どのスライドが何秒目に、どれだけ映っているか」。
"""

import json
import os
import subprocess

import numpy as np
import pytest
from PIL import Image

from note2slides import ffmpeg as ffmpeg_mod
from note2slides import video
from note2slides.video import SlideSource, VideoOptions, compose_video
from note2slides.waveform import Waveform, read_wav, write_wav

ffmpeg_path = ffmpeg_mod.find_ffmpeg()
requires_ffmpeg = pytest.mark.skipif(ffmpeg_path is None, reason="ffmpeg が見つかりません")

SAMPLE_RATE = 48000
COLORS = [(220, 30, 30), (30, 180, 60), (40, 60, 220), (230, 200, 20)]
FAST = dict(width=320, height=180, fps=10, preset="ultrafast", crf=30)
#: ffmpeg が受け付けない指定。失敗したときの伝え方を確かめるために使う。
BROKEN = VideoOptions(**{**FAST, "preset": "no-such-preset"})


def make_sources(tmp_path, durations, size=(320, 180)):
    """スライドごとに色の違う画像と、その長さの音を用意する。"""
    sources = []
    for index, seconds in enumerate(durations, start=1):
        image = str(tmp_path / f"slide_{index:03d}.png")
        Image.new("RGB", size, COLORS[(index - 1) % len(COLORS)]).save(image)
        audio = str(tmp_path / f"narration_{index:03d}.wav")
        time = np.arange(int(round(seconds * SAMPLE_RATE))) / SAMPLE_RATE
        write_wav(audio, Waveform(0.3 * np.sin(2 * np.pi * 440 * time), SAMPLE_RATE))
        sources.append(SlideSource(index, image, audio))
    return sources


def decoded_frames(path, outdir):
    """動画のコマをすべて取り出し、1 コマずつどのスライドかを返す。"""
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(
        [ffmpeg_path, "-y", "-loglevel", "error", "-i", path, "-vsync", "0",
         os.path.join(outdir, "f_%05d.png")],
        check=True,
    )
    frames = []
    for name in sorted(os.listdir(outdir)):
        with Image.open(os.path.join(outdir, name)) as image:
            pixel = image.getpixel((image.width // 2, image.height // 2))
            nearest = min(COLORS, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, pixel)))
            frames.append(COLORS.index(nearest) + 1)
    return frames


def decoded_audio(path, out_path):
    subprocess.run(
        [ffmpeg_path, "-y", "-loglevel", "error", "-i", path, "-vn", out_path], check=True
    )
    return read_wav(out_path)


# ---------------------------------------------------------------------------
# 書き出した動画の中身
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_each_slide_is_shown_for_its_own_narration(tmp_path):
    """ナレーションの途中でスライドが変わらないことを、1 コマずつ確かめる。"""
    sources = make_sources(tmp_path, [0.55, 1.24, 0.31])
    out = str(tmp_path / "out.mp4")

    result = compose_video(sources, out, VideoOptions(min_duration=0.5, **FAST))

    frames = decoded_frames(out, str(tmp_path / "frames"))
    assert len(frames) == sum(round(s.duration * 10) for s in result.segments)
    for segment in result.segments:
        first = round(segment.start * 10)
        last = round(segment.end * 10) - 1
        # 割り当てた区間は、最初から最後までそのスライドだけが映っている。
        assert set(frames[first : last + 1]) == {segment.index}


@requires_ffmpeg
def test_audio_is_not_cut_off(tmp_path):
    sources = make_sources(tmp_path, [0.55, 1.24])
    out = str(tmp_path / "out.mp4")

    result = compose_video(sources, out, VideoOptions(min_duration=0.5, **FAST))

    audio = decoded_audio(out, str(tmp_path / "out.wav"))
    assert audio.duration >= result.duration - 0.01
    assert audio.sample_rate == SAMPLE_RATE
    # 音が入っている(無音の動画になっていない)。
    assert float(np.max(np.abs(audio.samples))) > 0.05


@requires_ffmpeg
def test_video_is_an_mp4_that_starts_quickly(tmp_path):
    sources = make_sources(tmp_path, [0.5])
    out = str(tmp_path / "out.mp4")

    compose_video(sources, out, VideoOptions(**FAST))

    data = open(out, "rb").read()
    assert data[4:8] == b"ftyp"  # MP4 として読める
    # 再生開始に必要な情報(moov)が本体(mdat)より前にある。
    assert 0 <= data.find(b"moov") < data.find(b"mdat")


@requires_ffmpeg
def test_frame_is_16_9_even_from_4_3_slides(tmp_path):
    sources = make_sources(tmp_path, [0.5], size=(240, 180))  # 4:3
    out = str(tmp_path / "out.mp4")

    compose_video(sources, out, VideoOptions(background=(255, 255, 255), **FAST))

    first = str(tmp_path / "first.png")
    subprocess.run(
        [ffmpeg_path, "-y", "-loglevel", "error", "-i", out, "-frames:v", "1", first],
        check=True,
    )
    with Image.open(first) as image:
        assert image.size == (320, 180)  # 16:9
        assert image.getpixel((2, 90))[0] > 240  # 左端は余白
        assert image.getpixel((160, 90)) != image.getpixel((2, 90))  # 中央は元のスライド


@requires_ffmpeg
def test_result_and_manifest_describe_the_timeline(tmp_path):
    sources = make_sources(tmp_path, [0.55, 1.24])
    out = str(tmp_path / "out.mp4")

    result = compose_video(
        sources, out, VideoOptions(min_duration=0.5, **FAST), credit="VOICEVOX:No.7"
    )

    assert result.encoded_duration == pytest.approx(result.duration, abs=0.2)
    assert result.size_bytes > 0
    assert result.warnings == []

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["file"] == "out.mp4"
    assert manifest["video"] == {
        "width": 320, "height": 180, "fps": 10,
        "codec": "libx264", "crf": 30, "preset": "ultrafast", "pix_fmt": "yuv420p",
    }
    assert manifest["audio"]["codec"] == "aac"
    assert manifest["credit"] == "VOICEVOX:No.7"
    assert [s["index"] for s in manifest["slides"]] == [1, 2]
    assert manifest["slides"][1]["at"] == video.timestamp(result.segments[1].start)
    assert "ffmpeg" in manifest["command"]  # 同じ書き出しを手元で再現できる


@requires_ffmpeg
def test_work_files_are_removed_when_it_succeeds(tmp_path):
    sources = make_sources(tmp_path, [0.5])

    result = compose_video(sources, str(tmp_path / "out.mp4"), VideoOptions(**FAST))

    assert result.workdir is None


@requires_ffmpeg
def test_force_overwrites(tmp_path):
    sources = make_sources(tmp_path, [0.5])
    out = str(tmp_path / "out.mp4")
    compose_video(sources, out, VideoOptions(**FAST))

    result = compose_video(sources, out, VideoOptions(**FAST), force=True)

    assert os.path.getsize(out) == result.size_bytes


# ---------------------------------------------------------------------------
# 失敗したとき
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_failure_keeps_what_is_needed_to_investigate(tmp_path):
    """失敗の理由・実行したコマンド・渡した内容がすべて残ることを確かめる。"""
    sources = make_sources(tmp_path, [0.5])
    out = str(tmp_path / "out.mp4")

    with pytest.raises(ffmpeg_mod.FfmpegError) as error:
        compose_video(sources, out, BROKEN)

    message = str(error.value)
    assert "実行したコマンド:" in message
    assert "ffmpeg" in message
    assert "終了コード:" in message
    # 途中まで書けたファイルは残さない(完成したものと見分けが付かないため)。
    assert not os.path.exists(out)


@requires_ffmpeg
def test_failure_leaves_the_work_directory(tmp_path, monkeypatch):
    sources = make_sources(tmp_path, [0.5])
    kept = {}

    original = video._write_filter_script

    def remember(segments, workdir, options):
        kept["workdir"] = workdir
        return original(segments, workdir, options)

    monkeypatch.setattr(video, "_write_filter_script", remember)
    with pytest.raises(ffmpeg_mod.FfmpegError):
        compose_video(
            sources, str(tmp_path / "out.mp4"), BROKEN
        )

    # 何を指示したかを後から読めるように、作業ファイルを残す。
    assert os.path.isfile(os.path.join(kept["workdir"], "filters.txt"))
    assert os.path.isfile(os.path.join(kept["workdir"], "narration.wav"))
