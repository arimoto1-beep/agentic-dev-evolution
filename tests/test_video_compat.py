"""記事から動画までを、実際の外部ツールで通しで確認する。

    記事(.md) -> 資料(.pptx) -> スライド画像 + ナレーション音声 -> 動画(.mp4)

LibreOffice・音声合成・ffmpeg のどれかが無い環境ではスキップする。
書き出しに時間がかかるため slow を付ける。
"""

import json
import os
import subprocess

import pytest

from note2slides import convert_file, tts
from note2slides import ffmpeg as ffmpeg_mod
from note2slides.audio import AudioOptions
from note2slides.reading import ReadingStyle
from note2slides.soffice import find_soffice
from note2slides.video import VideoOptions, export_video

pytestmark = pytest.mark.slow

ARTICLE = """---
title: 動画生成の確認
---

## はじめに

これは動画生成の確認用の短い記事です。

## つづき

二枚目のスライドにも、読み上げる文章があります。

## おわりに

ここまでが確認用の記事です。
"""

soffice = find_soffice()
ffmpeg = ffmpeg_mod.find_ffmpeg()
requires_tools = pytest.mark.skipif(
    soffice is None or ffmpeg is None,
    reason="LibreOffice または ffmpeg が見つかりません",
)


@pytest.fixture(scope="module")
def engine_name():
    """使える音声合成の方式。無ければこのファイルごとスキップする。"""
    for name in tts.WINDOWS_ENGINES:
        try:
            tts.SpeechEngine(name).pick_voice(language="ja")
            return name
        except tts.SpeechError:
            continue
    pytest.skip("Windows の日本語音声合成が使えません")


@requires_tools
def test_article_becomes_a_narrated_video(tmp_path, engine_name):
    article = tmp_path / "article.md"
    article.write_text(ARTICLE, encoding="utf-8")
    pptx_path = str(tmp_path / "lesson.pptx")
    deck = convert_file(str(article), pptx_path)
    out_path = str(tmp_path / "lesson.mp4")

    result = export_video(
        pptx_path,
        out_path,
        options=VideoOptions(width=640, height=360, fps=15, preset="ultrafast", crf=30),
        audio_options=AudioOptions(
            engine=engine_name, reading=ReadingStyle(tail_silence=0.3)
        ),
        soffice_path=soffice,
        ffmpeg_path=ffmpeg,
    )

    # スライドの枚数と、動画に並べた区間の数が一致する。
    assert result.count == len(deck.slides)
    assert os.path.getsize(out_path) > 0
    # ナレーションの長さがそのまま動画の長さになる(足したのは端数分だけ)。
    assert result.duration > 3.0
    assert result.encoded_duration == pytest.approx(result.duration, abs=0.2)

    # 途中の素材も残り、あとから作り直せる。
    assert len(os.listdir(str(tmp_path / "lesson_slides"))) >= len(deck.slides)
    assert len(os.listdir(str(tmp_path / "lesson_audio"))) >= len(deck.slides)

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["source"] == "lesson.pptx"
    assert manifest["count"] == len(deck.slides)
    assert manifest["video"]["width"] == 640 and manifest["video"]["height"] == 360

    # 実際に開ける動画で、音声トラックが入っている。
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", out_path, "-f", "null", "-"],
        capture_output=True,
        text=True,
        errors="replace",
    ).stderr
    assert "Video: h264" in probe
    assert "Audio: aac" in probe
    assert "640x360" in probe
