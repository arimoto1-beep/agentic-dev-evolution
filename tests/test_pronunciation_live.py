"""実際の VOICEVOX に読みを聞いて、判定が空振りしていないことを確かめる。

`tests/test_pronunciation.py` は実測値を写した表で判定の中身を固定しているが、
それだけでは **エンジンが本当にそう読むか** が分からない。写した値が古くなれば、
判定は通るのに実物では取りこぼす。ここは本物に聞く。

VOICEVOX が入っていない環境では飛ばす(外部ツールを使うテストと同じ扱い)。
"""

from __future__ import annotations

import pytest

from note2slides import voicevox
from note2slides.pronunciation import (
    is_spelled_out,
    letter_readings,
    reading_kana_reader,
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def read_kana():
    problems = []
    for name in voicevox.EDITIONS:
        engine = voicevox.VoicevoxEngine(edition=voicevox.edition_for(name))
        try:
            engine.ensure_ready()
        except Exception as exc:  # 起動できない版は次を試す
            problems.append(f"{name}: {exc}")
            engine.close()
            continue
        try:
            yield reading_kana_reader(engine, engine.pick_style())
        finally:
            engine.close()
        return
    pytest.skip("VOICEVOX が使えません: " + " / ".join(problems))


def test_the_engine_really_spells_out_long_latin_words(read_kana):
    """gen28 の資料で実際に起きていたもの。ここが空振りすると誰も気付けない。"""
    table = letter_readings(read_kana)

    for word in ("SUCCEEDED", "PASS", "WARN", "FAIL", "JSON"):
        assert is_spelled_out(word, read_kana(word), table), word


def test_the_engine_does_not_spell_out_short_acronyms(read_kana):
    table = letter_readings(read_kana)

    for word in ("AI", "MCP", "API", "SDK", "ECS"):
        assert not is_spelled_out(word, read_kana(word), table), word


def test_english_words_are_read_as_words_not_letters(read_kana):
    """`status` の読み違いは 1 文字ずつではない。判定の対象外であることを固定する。"""
    table = letter_readings(read_kana)

    assert not is_spelled_out("status", read_kana("status"), table)
    # 読み違いそのものは残る。機械には正しい読みが決められないので、
    # `--dict` で書いた人が決める(README「読み方を確かめる」)。
    assert read_kana("status") != "ステータス"
