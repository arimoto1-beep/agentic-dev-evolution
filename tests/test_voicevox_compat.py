"""実際の VOICEVOX ENGINE を使って、記事 -> ナレーション音声を通しで確認する。

VOICEVOX が入っていない環境ではスキップする。合成に時間がかかるため slow を付ける。
ここで見るのは「ナレーションとして使える形で出ているか」で、次の 4 点を確かめる。

    * スライドと音声が番号で対応している
    * 全ファイルが同じ形式(48kHz / 16bit / モノラル)
    * 音量が目標にそろっている(YouTube の基準に合わせるため)
    * 実際に声が入っている(無音や欠落になっていない)
"""

import json
import wave

import pytest

from note2slides import convert_file, export_narration
from note2slides.audio import AudioOptions
from note2slides.reading import ReadingStyle
from note2slides.speech import SpeechError, SpeechJob, SpeechPiece
from note2slides.voice_survey import DEFAULT_TEXT, measure_styles
from note2slides.voicevox import NEMO, VoicevoxEngine, is_installed
from note2slides.waveform import loudness_lufs, peak_dbfs, read_wav

pytestmark = pytest.mark.slow

ARTICLE = """---
title: ナレーション音声の確認
---

## はじめに

これは音声合成の確認用の短い記事です。二つ目の文もあります。

## まとめ

ここまでが確認用の記事です。
"""


def start(edition=None):
    kwargs = {"edition": edition} if edition else {}
    label = edition.label if edition else "VOICEVOX"
    if not is_installed(**({"edition": edition} if edition else {})):
        pytest.skip(f"{label} が入っていません")
    engine = VoicevoxEngine(**kwargs)
    try:
        engine.ensure_ready()
    except SpeechError as exc:
        engine.close()
        pytest.skip(f"{label} ENGINE を使えません: {exc}")
    return engine


@pytest.fixture(scope="module")
def engine():
    engine = start()
    yield engine
    engine.close()


@pytest.fixture(scope="module")
def nemo():
    engine = start(NEMO)
    yield engine
    engine.close()


def test_narration_is_generated_for_every_slide(tmp_path, engine):
    article = tmp_path / "article.md"
    article.write_text(ARTICLE, encoding="utf-8")
    pptx_path = str(tmp_path / "deck.pptx")
    deck = convert_file(str(article), pptx_path)
    outdir = tmp_path / "audio"

    result = export_narration(
        pptx_path,
        str(outdir),
        AudioOptions(loudness=-16.0, reading=ReadingStyle(tail_silence=0.3)),
        engine=engine,
    )

    # スライドの枚数と音声の本数が一致し、番号も対応する。
    assert result.count == len(deck.slides)
    assert [clip.filename for clip in result.clips] == [
        f"narration_{n:03d}.wav" for n in range(1, len(deck.slides) + 1)
    ]

    formats = set()
    for clip in result.clips:
        with wave.open(clip.path, "rb") as reader:
            formats.add((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()))
    assert formats == {(48000, 1, 2)}  # 動画生成でつなげるよう、形式が揃っている

    with open(result.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["engine"] == "voicevox"
    assert manifest["credit"].startswith("VOICEVOX:")  # 公開時に必要な表示
    # 記事の文章がそのまま原稿になっている。
    assert "これは音声合成の確認用の短い記事です。" in manifest["clips"][1]["text"]


def test_loudness_matches_the_target(tmp_path, engine):
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "segments": [
                    {"index": 1, "text": "音量をそろえる確認です。二つ目の文もあります。"},
                    {"index": 2, "text": "こちらは別のスライドの文章です。"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = export_narration(
        str(script), str(tmp_path / "audio"), AudioOptions(loudness=-16.0), engine=engine
    )

    assert result.loudness.result_lufs == pytest.approx(-16.0, abs=1.0)
    for clip in result.clips:
        audio = read_wav(clip.path)
        assert peak_dbfs(audio) <= -1.4  # 音割れしない
        # 1 枚ごとに見ても、目標から大きく外れない。
        assert loudness_lufs(audio) == pytest.approx(-16.0, abs=3.0)


def test_a_slide_is_read_as_one_continuous_utterance(tmp_path, engine):
    """次の区切りの先頭が、文章の書き出しの音程にならないことを確かめる。

    VOICEVOX は渡された文字列を 1 つの発話として扱い、その書き出しの音程を
    高く取る。区切りごとに合成を頼むと、項目が変わるたびにその音程が現れ、
    そこだけ音程と抑揚が跳ね上がって聞こえる。
    """
    first = "情報を一度スライドの単位に分け直す必要があります。"
    second = "記事を見出しごとに分割する。"
    workdir = tmp_path / "work"
    style = engine.pick_style()

    engine.synthesize(
        [
            SpeechJob(
                index=1,
                pieces=[SpeechPiece(first, 0.6), SpeechPiece(second)],
                out_path=str(tmp_path / "out.wav"),
                slide=1,
            )
        ],
        str(workdir),
        timeout=120,
    )

    # 実際に合成へ渡した音程を、下書きから読む。
    with open(workdir / "query_0001.json", encoding="utf-8") as f:
        phrases = json.load(f)["accent_phrases"]
    alone = engine._accent_phrases(second, style.id, 60)
    start = len(engine._accent_phrases(first, style.id, 60))

    # 2 つ目の区切りの先頭の音程(対数なので、差がそのまま比になる)。
    in_context = phrases[start]["moras"][0]["pitch"]
    on_its_own = alone[0]["moras"][0]["pitch"]

    assert on_its_own > in_context  # 単独だと書き出しの音程で高くなる
    assert in_context == pytest.approx(phrases[0]["moras"][0]["pitch"], abs=0.25)
    # 前の文の読み終わりからの段差が、書き出しの音程で始めるより小さいこと。
    # 無声化した拍は音程を持たない(0)ので、声になっている最後の拍と比べる。
    before = [m["pitch"] for p in phrases[:start] for m in p["moras"] if m["pitch"] > 0][-1]
    assert abs(in_context - before) < abs(on_its_own - before)


def test_the_first_and_last_sounds_are_not_cut_off(tmp_path, nemo):
    """読み上げが、エンジンの決めた読みの長さぶん出ていることを確かめる。

    VOICEVOX は前後の余白も含めて 1 本の波形を作るため、余白なしで頼むと
    最初の拍が無音から立ち上がる形で作られ、本来の大きさになるまで 70ms ほど
    かかる(最後の拍も、消え際が途中で終わる)。拍の長さは 0.1 秒前後なので、
    痩せたぶんは前後の無音として切り落とされ、音声はエンジンが決めた読みより
    短くなる。動画の最初の一言はここで聞こえなくなる。
    """
    text = "eラーニング動画のつくり方。"  # 母音だけの拍で始まる(子音より痩せが目立つ)
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"segments": [{"index": 1, "text": text}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = export_narration(
        str(script),
        str(tmp_path / "audio"),
        # 前後の無音を入れない。ここで見たいのは読み上げそのものの長さ。
        AudioOptions(reading=ReadingStyle(lead_silence=0, opening_silence=0, tail_silence=0)),
        engine=nemo,
    )

    # エンジン自身が決めた読みの長さ(拍の長さの合計)と比べる。
    phrases = nemo.read(text, nemo.pick_style())
    planned = sum(
        (mora.get("consonant_length") or 0.0) + (mora.get("vowel_length") or 0.0)
        for phrase in phrases
        for mora in phrase["moras"]
    )

    assert read_wav(result.clips[0].path).duration >= planned


def test_sentences_get_a_pause_between_them(tmp_path, engine):
    """間は読み上げの中に置くので、間の長さの分だけ長くなる。"""
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"segments": [{"index": 1, "text": "最初の文です。次の文です。"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    outdir = tmp_path / "audio"

    tight = export_narration(
        str(script),
        str(outdir),
        AudioOptions(reading=ReadingStyle(sentence_pause=0, lead_silence=0, tail_silence=0)),
        engine=engine,
    )
    spaced = export_narration(
        str(script),
        str(outdir),
        AudioOptions(reading=ReadingStyle(sentence_pause=0.5, lead_silence=0, tail_silence=0)),
        engine=engine,
        force=True,
    )

    assert tight.clips[0].duration > 1.0  # 実際に声が入っている
    assert spaced.clips[0].duration == pytest.approx(tight.clips[0].duration + 0.5, abs=0.05)


def test_standard_narration_voice_exists_in_the_engine(nemo):
    """標準のナレーション音声が、実機の音声一覧にあることを確かめる。

    名前を書き間違えても `pick_style` は黙って一覧の先頭に下がるため、
    実際のエンジンで名前が引けるかどうかはここでしか分からない。
    """
    from note2slides import tts

    engine_name, voice = tts.default_narration()

    assert engine_name == nemo.name
    assert voice in [style.name for style in nemo.list_styles()]
    assert nemo.pick_style().name == voice  # 指定なしでこの声になる


def test_nemo_speaks_with_its_own_voice_and_credit(tmp_path, nemo):
    """VOICEVOX Nemo は別のエンジン。声も、公開時に必要な表示も違う。"""
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"segments": [{"index": 1, "text": "別のエンジンの確認です。"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = export_narration(
        str(script),
        str(tmp_path / "audio"),
        AudioOptions(engine="voicevox-nemo", voice="女声1/ノーマル"),
        engine=nemo,
    )

    assert result.engine == "voicevox-nemo"
    assert result.voice == "女声1/ノーマル"
    # Nemo の声には人格が無く、話者名を出さずに「VOICEVOX Nemo」と書けばよい。
    assert result.credit == "VOICEVOX Nemo"
    assert result.clips[0].duration > 0.5


def test_measured_pitch_is_in_the_range_of_a_human_voice(engine):
    """音程は自然対数の Hz で返ってくる。単位を取り違えると桁が変わる。"""
    styles = [s for s in engine.list_styles() if s.name == "No.7/アナウンス"]
    measured = measure_styles(engine, DEFAULT_TEXT, styles or engine.list_styles()[:1])[0]

    assert measured.ok
    assert 80.0 < measured.pitch_hz < 500.0  # 人の声の高さ
    assert 0.0 < measured.pitch_spread < 12.0  # 1 オクターブも振れることはない
    assert 3.0 < measured.rate < 15.0  # モーラ/秒
    assert measured.voiced <= measured.moras
