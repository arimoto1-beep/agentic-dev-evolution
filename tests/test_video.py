"""動画の組み立て(時間の割り当て・素材の対応付け・ffmpeg への指示)を確認する。

ffmpeg を実際に呼ぶ経路は tests/test_video_encode.py で確認する。
"""

import json
import os

import numpy as np
import pytest
from PIL import Image

from note2slides import video
from note2slides.video import (
    SlideSource,
    VideoError,
    VideoOptions,
    align_to_frames,
    build_command,
    build_timeline,
    collect_sources,
    frames_for,
    timestamp,
)
from note2slides.waveform import Waveform, write_wav

SAMPLE_RATE = 48000


def make_image(path, size=(640, 360), color=(200, 200, 200)):
    Image.new("RGB", size, color).save(str(path))
    return str(path)


def make_wav(path, seconds, sample_rate=SAMPLE_RATE):
    samples = np.zeros(int(round(seconds * sample_rate)))
    write_wav(str(path), Waveform(samples, sample_rate))
    return str(path)


def make_sources(tmp_path, durations, size=(640, 360)):
    """スライド画像とナレーション音声の組を作る。"""
    sources = []
    for index, seconds in enumerate(durations, start=1):
        image = make_image(tmp_path / f"slide_{index:03d}.png", size=size)
        audio = make_wav(tmp_path / f"narration_{index:03d}.wav", seconds)
        sources.append(SlideSource(index, image, audio))
    return sources


# ---------------------------------------------------------------------------
# 時間の割り当て
# ---------------------------------------------------------------------------


def test_each_slide_is_shown_at_least_as_long_as_its_narration(tmp_path):
    sources = make_sources(tmp_path, [3.21, 1.04, 7.5])

    segments = build_timeline(sources, VideoOptions(fps=30, min_duration=0))

    for segment in segments:
        assert segment.duration >= segment.narration
        # ナレーションより長くするのは 1 コマ未満に留める(間延びさせない)。
        assert segment.duration - segment.narration < 1 / 30


def test_slides_start_where_the_previous_one_ends(tmp_path):
    sources = make_sources(tmp_path, [1.0, 2.0, 0.5])

    segments = build_timeline(sources, VideoOptions(fps=30))

    assert segments[0].start == 0.0
    for previous, following in zip(segments, segments[1:]):
        assert following.start == pytest.approx(previous.end)


def test_durations_are_whole_frames(tmp_path):
    sources = make_sources(tmp_path, [1.017, 2.333, 0.04])

    segments = build_timeline(sources, VideoOptions(fps=30))

    for segment in segments:
        frames = segment.duration * 30
        assert frames == pytest.approx(round(frames))


def test_short_narration_is_held_for_the_minimum(tmp_path):
    sources = make_sources(tmp_path, [0.2])

    segments = build_timeline(sources, VideoOptions(fps=30, min_duration=2.0))

    assert segments[0].duration == pytest.approx(2.0)
    assert segments[0].narration == pytest.approx(0.2, abs=0.01)


def test_slides_without_audio_get_the_minimum(tmp_path):
    source = SlideSource(1, make_image(tmp_path / "slide_001.png"), None)

    segments = build_timeline([source], VideoOptions(fps=30, min_duration=1.5))

    assert segments[0].duration == pytest.approx(1.5)
    assert segments[0].narration == 0.0


def test_slides_are_ordered_by_index(tmp_path):
    sources = make_sources(tmp_path, [1.0, 2.0, 3.0])

    segments = build_timeline(list(reversed(sources)), VideoOptions())

    assert [segment.index for segment in segments] == [1, 2, 3]


@pytest.mark.parametrize(
    "seconds, fps, expected",
    [
        (1.0, 30, 30),
        (1.001, 30, 31),  # 端数は切り上げる(音声が切れないように)
        (0.0, 30, 1),  # 0 秒でも 1 コマは映す
        (2.0, 25, 50),
        (1 / 30, 30, 1),  # ちょうど 1 コマ分は 1 コマのまま(誤差で増やさない)
    ],
)
def test_frames_for(seconds, fps, expected):
    assert frames_for(seconds, fps) == expected
    assert align_to_frames(seconds, fps) == pytest.approx(expected / fps)


def test_timestamp_is_readable():
    assert timestamp(0) == "0:00:00.0"
    assert timestamp(75.25) == "0:01:15.2"
    assert timestamp(3725.0) == "1:02:05.0"


# ---------------------------------------------------------------------------
# 音声トラック
# ---------------------------------------------------------------------------


def test_audio_track_matches_the_video_length(tmp_path):
    """音声を割り当てた長さちょうどにそろえる。ここがずれると同期が崩れる。"""
    sources = make_sources(tmp_path, [1.017, 0.5])
    options = VideoOptions(fps=30, min_duration=1.0, sample_rate=SAMPLE_RATE)
    segments = build_timeline(sources, options)
    result = video.VideoResult()

    path = video._write_audio_track(segments, str(tmp_path), options, result)

    from note2slides.waveform import probe_duration

    assert probe_duration(path) == pytest.approx(sum(s.duration for s in segments), abs=1e-6)


def test_audio_is_padded_not_cut(tmp_path):
    """短い音声は無音を足して伸ばす。切り詰めると読み上げが途中で切れる。"""
    sources = make_sources(tmp_path, [0.5])
    options = VideoOptions(fps=30, min_duration=2.0, sample_rate=SAMPLE_RATE)
    segments = build_timeline(sources, options)

    path = video._write_audio_track(segments, str(tmp_path), options, video.VideoResult())

    from note2slides.waveform import read_wav

    assert read_wav(path).duration == pytest.approx(2.0)


def test_audio_of_another_sample_rate_is_converted(tmp_path):
    image = make_image(tmp_path / "slide_001.png")
    source = SlideSource(1, image, make_wav(tmp_path / "a.wav", 1.0, 16000))
    options = VideoOptions(fps=30, sample_rate=SAMPLE_RATE)
    segments = build_timeline([source], options)
    result = video.VideoResult()

    video._write_audio_track(segments, str(tmp_path), options, result)

    assert any("48000Hz に変換" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# ffmpeg への指示
# ---------------------------------------------------------------------------


def test_filter_script_repeats_each_slide_for_its_frames(tmp_path):
    sources = make_sources(tmp_path, [1.0, 0.5])
    options = VideoOptions(width=1920, fps=30, min_duration=0.5)
    segments = build_timeline(sources, options)

    path = video._write_filter_script(segments, str(tmp_path), options)

    script = open(path, encoding="utf-8").read()
    # 30 コマ = 元の 1 枚 + 29 回の繰り返し。
    assert "[0:v]loop=loop=29:size=1:start=0[v0]" in script
    assert "[1:v]loop=loop=14:size=1:start=0[v1]" in script
    # つないだ後に時刻を振り直す(つなぎ目でコマが落ちないようにする)。
    assert "[v0][v1]concat=n=2:v=1:a=0,setpts=N/30/TB," in script


def test_filter_script_keeps_the_frame_16_9(tmp_path):
    sources = make_sources(tmp_path, [1.0])
    options = VideoOptions(width=1920, background=(0, 0, 0))
    segments = build_timeline(sources, options)

    script = open(
        video._write_filter_script(segments, str(tmp_path), options), encoding="utf-8"
    ).read()

    # 引き伸ばさずに収め、余った分を背景色で埋める。
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in script
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x000000" in script
    assert "setsar=1[v]" in script


def test_command_uses_formats_players_can_open(tmp_path):
    sources = make_sources(tmp_path, [1.0, 1.0])
    options = VideoOptions(fps=24, crf=18, preset="fast", audio_bitrate="128k")
    segments = build_timeline(sources, options)

    command = build_command(
        "ffmpeg", segments, "filters.txt", "track.wav", "out.mp4", options
    )

    assert command[0] == "ffmpeg"
    # スライド 1 枚が入力 1 つ。音声はその次。
    assert command.count("-i") == 3
    assert command[-1] == "out.mp4"
    for expected in (
        ["-c:v", "libx264"],
        ["-pix_fmt", "yuv420p"],  # 古い機器でも再生できる
        ["-c:a", "aac"],
        ["-b:a", "128k"],
        ["-r", "24"],
        ["-crf", "18"],
        ["-preset", "fast"],
        ["-movflags", "+faststart"],
        ["-map", "[v]"],
        ["-map", "2:a:0"],  # 入力 2 番目(= スライドの次)が音声
    ):
        assert any(command[i : i + 2] == expected for i in range(len(command)))
    # 長さは合わせてあるので、切り詰めさせない。
    assert "-shortest" not in command


# ---------------------------------------------------------------------------
# 素材の対応付け
# ---------------------------------------------------------------------------


def write_manifest(directory, name, key, entries):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        json.dump({key: entries}, f)


def test_sources_come_from_the_manifests(tmp_path):
    slides = tmp_path / "slides"
    audio = tmp_path / "audio"
    slides.mkdir()
    audio.mkdir()
    for index in (1, 2):
        make_image(slides / f"slide_{index:03d}.png")
        make_wav(audio / f"narration_{index:03d}.wav", 1.0)
    write_manifest(str(slides), "slides.json", "slides", [
        {"index": 1, "file": "slide_001.png"}, {"index": 2, "file": "slide_002.png"}
    ])
    write_manifest(str(audio), "narration.json", "clips", [
        {"index": 1, "file": "narration_001.wav"}, {"index": 2, "file": "narration_002.wav"}
    ])

    sources = collect_sources(str(slides), str(audio))

    assert [s.index for s in sources] == [1, 2]
    assert sources[0].image.endswith("slide_001.png")
    assert sources[0].audio.endswith("narration_001.wav")


def test_sources_fall_back_to_the_numbers_in_the_filenames(tmp_path):
    """一覧が無くても、手で並べた素材から作れるようにする。"""
    slides = tmp_path / "slides"
    audio = tmp_path / "audio"
    slides.mkdir()
    audio.mkdir()
    for index in (1, 2, 3):
        make_image(slides / f"slide_{index:03d}.png")
        make_wav(audio / f"narration_{index:03d}.wav", 1.0)

    sources = collect_sources(str(slides), str(audio))

    assert [s.index for s in sources] == [1, 2, 3]


def test_missing_audio_for_a_slide_is_reported(tmp_path):
    slides = tmp_path / "slides"
    audio = tmp_path / "audio"
    slides.mkdir()
    audio.mkdir()
    for index in (1, 2, 3):
        make_image(slides / f"slide_{index:03d}.png")
    make_wav(audio / "narration_001.wav", 1.0)

    with pytest.raises(VideoError) as error:
        collect_sources(str(slides), str(audio))

    message = str(error.value)
    assert "3 枚" in message and "1 本" in message
    assert "2, 3" in message  # どの番号が足りないかまで示す


def test_manifest_pointing_at_a_deleted_file_is_reported(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()
    write_manifest(str(slides), "slides.json", "slides", [{"index": 1, "file": "gone.png"}])

    with pytest.raises(VideoError, match="一覧に載っているファイルがありません"):
        collect_sources(str(slides))


def test_empty_slides_directory_is_reported(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()

    with pytest.raises(VideoError, match="スライド画像が見つかりません"):
        collect_sources(str(slides), None)


def test_missing_directory_is_reported(tmp_path):
    with pytest.raises(VideoError, match="ディレクトリが見つかりません"):
        collect_sources(str(tmp_path / "none"))


# ---------------------------------------------------------------------------
# 指定の検証
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        VideoOptions(width=1921),  # 奇数は H.264 で扱えない
        VideoOptions(width=1920, height=1081),
        VideoOptions(width=8),
        VideoOptions(fps=0),
        VideoOptions(fps=200),
        VideoOptions(crf=60),
        VideoOptions(min_duration=-1),
        VideoOptions(sample_rate=100),
        VideoOptions(channels=3),
    ],
)
def test_invalid_options_are_rejected(options):
    with pytest.raises(VideoError):
        options.validate()


def test_non_16_9_frame_is_warned_about():
    assert VideoOptions(width=1920).aspect_warning() is None
    assert VideoOptions(width=1280, height=720).aspect_warning() is None
    assert "16:9" in VideoOptions(width=1920, height=1440).aspect_warning()


def test_output_must_be_mp4(tmp_path):
    sources = make_sources(tmp_path, [1.0])

    with pytest.raises(VideoError, match=r"\.mp4"):
        video.compose_video(sources, str(tmp_path / "out.avi"))


def test_existing_video_needs_force(tmp_path):
    sources = make_sources(tmp_path, [1.0])
    out = tmp_path / "out.mp4"
    out.write_bytes(b"")

    with pytest.raises(video.OutputExistsError, match="--force"):
        video.compose_video(sources, str(out))


def test_no_slides_is_rejected(tmp_path):
    with pytest.raises(VideoError, match="1 枚もありません"):
        video.compose_video([], str(tmp_path / "out.mp4"))


def test_images_of_different_sizes_are_rejected(tmp_path):
    """途中で大きさが変わると ffmpeg が止まる。始める前に気付けるようにする。"""
    sources = [
        SlideSource(1, make_image(tmp_path / "a.png", size=(640, 360)), None),
        SlideSource(2, make_image(tmp_path / "b.png", size=(800, 450)), None),
    ]
    options = VideoOptions(width=640, height=360)
    segments = build_timeline(sources, options)

    with pytest.raises(VideoError, match="大きさが揃っていません"):
        video._check_images(segments, options)


def test_images_of_another_size_are_converted_with_a_warning(tmp_path):
    sources = [SlideSource(1, make_image(tmp_path / "a.png", size=(640, 360)), None)]
    options = VideoOptions(width=1920)
    segments = build_timeline(sources, options)

    warnings = video._check_images(segments, options)

    assert any("1920x1080 に変換" in warning for warning in warnings)


def test_missing_image_is_reported(tmp_path):
    sources = [SlideSource(1, str(tmp_path / "none.png"), None)]
    segments = build_timeline(sources, VideoOptions())

    with pytest.raises(VideoError, match="スライド画像が見つかりません"):
        video._check_images(segments, VideoOptions())


@pytest.mark.skipif(os.name != "nt", reason="コマンド行の長さの上限は Windows の事情")
def test_too_many_slides_is_explained(tmp_path):
    """長すぎるコマンドは「ファイル名が長すぎます」という失敗になって分かりにくい。"""
    long_name = "x" * 200
    command = ["ffmpeg"] + ["-i", str(tmp_path / f"{long_name}.png")] * 200

    with pytest.raises(VideoError, match="コマンドが長すぎます"):
        video._check_command_length(command)

    video._check_command_length(["ffmpeg", "-i", "slide_001.png"])  # 普通の長さは通る


def test_missing_audio_file_is_reported(tmp_path):
    sources = [SlideSource(1, make_image(tmp_path / "a.png"), str(tmp_path / "none.wav"))]

    with pytest.raises(VideoError, match="ナレーション音声が見つかりません"):
        build_timeline(sources, VideoOptions())
