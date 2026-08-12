"""実際の Windows 音声合成を使って、資料 -> ナレーション音声を通しで確認する。

日本語の音声が使えない環境ではスキップする。合成に時間がかかるため slow を付ける。
"""

import json
import os
import wave

import pytest

from note2slides import convert_file, export_narration, tts
from note2slides.audio import AudioOptions

pytestmark = pytest.mark.slow

ARTICLE = """---
title: 音声合成の確認
---

## はじめに

これは音声合成の確認用の短い記事です。

## おわりに

ここまでが確認用の記事です。
"""


@pytest.fixture(scope="module")
def engine():
    try:
        return tts.select_engine(tts.ENGINE_AUTO, language="ja")
    except tts.SpeechError as exc:
        pytest.skip(f"日本語の音声合成が使えません: {exc}")


def test_voices_can_be_listed(engine):
    voices = engine.list_voices()

    assert voices
    assert any(voice.speaks("ja") for voice in voices)


def test_article_becomes_one_audio_file_per_slide(tmp_path, engine):
    article = tmp_path / "article.md"
    article.write_text(ARTICLE, encoding="utf-8")
    pptx_path = str(tmp_path / "deck.pptx")
    deck = convert_file(str(article), pptx_path)
    outdir = tmp_path / "audio"

    result = export_narration(
        pptx_path, str(outdir), AudioOptions(tail_silence=0.3), engine=engine
    )

    # スライドの枚数と音声の本数が一致し、番号も対応する。
    assert result.count == len(deck.slides)
    assert [clip.filename for clip in result.clips] == [
        f"narration_{n:03d}.wav" for n in range(1, len(deck.slides) + 1)
    ]

    formats = set()
    for clip in result.clips:
        assert os.path.getsize(clip.path) > 0
        with wave.open(clip.path, "rb") as reader:
            formats.add((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()))
            # 無音ではなく、実際に読み上げた長さがあること。
            assert reader.getnframes() / reader.getframerate() > 0.5
    assert len(formats) == 1  # 動画生成でつなげるよう、形式が揃っている

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["count"] == len(deck.slides)
    assert manifest["source"] == "deck.pptx"
    assert manifest["voice"]
    # 記事の文章がそのまま原稿になっている。
    assert "これは音声合成の確認用の短い記事です。" in manifest["clips"][1]["text"]


def test_edited_script_is_used_as_written(tmp_path, engine):
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {"segments": [{"index": 1, "text": "読み方を直した原稿です。"}]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    outdir = tmp_path / "audio"

    result = export_narration(str(script), str(outdir), engine=engine)

    assert result.count == 1
    assert result.clips[0].text == "読み方を直した原稿です。"
    assert result.clips[0].duration > 0.5
