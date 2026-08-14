"""ffmpeg の場所の解決と、実行結果(進捗・失敗・時間切れ)の受け取りを確認する。

実行の確認には ffmpeg そのものではなく Python を使う。進捗の読み取りと失敗の
伝え方は ffmpeg の出力の形だけで決まるため、これで同じ経路を通せる。
"""

import os
import sys

import pytest

from note2slides import ffmpeg as ffmpeg_mod
from note2slides.ffmpeg import (
    FfmpegNotFoundError,
    FfmpegRunError,
    candidate_paths,
    find_ffmpeg,
    require_ffmpeg,
    run,
)

requires_ffmpeg = pytest.mark.skipif(
    find_ffmpeg() is None, reason="ffmpeg が見つかりません"
)


def python(code):
    return [sys.executable, "-c", code]


# ---------------------------------------------------------------------------
# 場所の解決
# ---------------------------------------------------------------------------


def test_explicit_path_is_not_second_guessed():
    """指定を取り違えたまま別の ffmpeg で書き出されないようにする。"""
    assert candidate_paths("C:/tools/ffmpeg.exe") == ["C:/tools/ffmpeg.exe"]


def test_environment_variable_comes_first(monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", "/opt/ffmpeg")

    assert candidate_paths()[0] == "/opt/ffmpeg"


def test_bundled_ffmpeg_is_the_last_resort(monkeypatch):
    """自分で入れた ffmpeg があればそちらを使う(同梱は最後)。"""
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ffmpeg_mod, "bundled_ffmpeg", lambda: "/bundled/ffmpeg")

    paths = candidate_paths()

    assert paths[0] == "ffmpeg"
    assert paths[-1] == "/bundled/ffmpeg"


def test_missing_ffmpeg_tells_where_it_looked():
    with pytest.raises(FfmpegNotFoundError) as error:
        require_ffmpeg(str(os.path.join("does", "not", "exist.exe")))

    message = str(error.value)
    assert "見つかりません" in message
    assert "exist.exe" in message
    assert "FFMPEG_PATH" in message  # 指定の仕方まで伝える


@requires_ffmpeg
def test_found_ffmpeg_can_encode_what_we_need():
    found = find_ffmpeg()

    assert os.path.isfile(found)
    assert ffmpeg_mod.get_version(found).startswith("ffmpeg version")
    # 動画は H.264、音声は AAC で書き出すので、この 2 つが要る。
    assert ffmpeg_mod.has_encoder(found, "libx264")
    assert ffmpeg_mod.has_encoder(found, "aac")
    assert not ffmpeg_mod.has_encoder(found, "そんな符号化器はない")


def test_bundled_ffmpeg_is_marked_in_the_description():
    bundled = ffmpeg_mod.bundled_ffmpeg()
    if bundled is None:
        pytest.skip("同梱の ffmpeg(imageio-ffmpeg)が入っていません")

    assert "同梱" in ffmpeg_mod.describe(bundled)
    assert ffmpeg_mod.describe("/usr/bin/ffmpeg") == "/usr/bin/ffmpeg"


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def test_progress_is_reported_while_running():
    seen = []

    report = run(
        python("print('frame=42'); print('out_time=00:01:23.500000'); print('progress=end')"),
        on_progress=seen.append,
    )

    assert report.out_time == pytest.approx(83.5)
    assert report.frames == 42
    assert seen == [pytest.approx(83.5)]


def test_failure_keeps_the_command_and_the_output():
    with pytest.raises(FfmpegRunError) as error:
        run(python("import sys; sys.stderr.write('Invalid argument'); sys.exit(3)"))

    message = str(error.value)
    assert "実行したコマンド:" in message
    assert "終了コード: 3" in message
    assert "Invalid argument" in message  # ffmpeg が言っていることをそのまま残す


def test_long_output_does_not_block():
    """標準エラー出力を読まずにいると ffmpeg が書き込めずに止まる。"""
    report = run(python("import sys; sys.stderr.write('x' * 200000)"))

    assert len(report.stderr) == 200000


def test_timeout_stops_it_and_says_how_far_it_got():
    slow = python("import time; print('out_time=00:00:05.000000', flush=True); time.sleep(30)")

    with pytest.raises(FfmpegRunError) as error:
        run(slow, timeout=1.0)

    message = str(error.value)
    assert "1 秒以内に終了しませんでした" in message
    assert "5.0 秒目まで" in message


def test_missing_executable_is_reported():
    with pytest.raises(FfmpegRunError, match="起動できませんでした"):
        run([os.path.join("no", "such", "ffmpeg.exe")])
