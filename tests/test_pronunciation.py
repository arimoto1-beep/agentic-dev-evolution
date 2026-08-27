"""合成前に読みを取り出して確かめる処理を確認する。

ここで守りたいのは 1 点だけ ——「英字が 1 文字ずつ読まれている」ことを、
合成エンジンの版が変わっても見つけられること。正しい読みが何かは機械には
決められないので、それ以外は判定しない(`pronunciation.py`)。

実際のエンジンは使わない。読みを返す関数を差し替えられる作りにしてあるので、
判定の中身だけをここで固定する。エンジンが本当にこう読むことは
`tests/test_pronunciation_live.py` が確かめる。
"""

import pytest

from note2slides.pronunciation import (
    SPELLED_MIN_LETTERS,
    LineReading,
    PronunciationReport,
    SlideReading,
    WordReading,
    inspect_readings,
    is_spelled_out,
    latin_words,
    letter_readings,
    normalize_kana,
    reading_kana_reader,
)
from note2slides.reading import plan_reading

# VOICEVOX が実際に返す読み(`engine.read()` に 1 文字ずつ聞いたもの)。
LETTERS = {
    "A": "エイ", "B": "ビイ", "C": "シイ", "D": "ディイ", "E": "イイ", "F": "エフ",
    "G": "ジイ", "H": "エイチ", "I": "アイ", "J": "ジェイ", "K": "ケイ", "L": "エル",
    "M": "エム", "N": "エヌ", "O": "オオ", "P": "ピイ", "Q": "キュウ", "R": "アアル",
    "S": "エス", "T": "ティイ", "U": "ユウ", "V": "ブイ", "W": "ダブリュウ",
    "X": "エックス", "Y": "ワイ", "Z": "ズィイ",
}
# 語として聞いたときの読み(同じエンジンの実測値)。
WORDS = {
    "PASS": "ピイエエエスエス",
    "SUCCEEDED": "エスユウシイシイイイイイディイイイディイ",
    "JSON": "ジェエエスオオエヌ",
    "AI": "エエアイ",
    "MCP": "エムシイピイ",
    "SDK": "エスディイケエ",
    "status": "スタタス",
    "Runner": "ランナア",
    "aws": "オオズ",
}


def fake_reader(extra=None):
    """文字列 -> 仮名 の関数。実測値に無いものは呼ばれたら分かるように印を付ける。"""
    table = dict(LETTERS)
    table.update(WORDS)
    table.update(extra or {})
    calls = []

    def read(text):
        calls.append(text)
        return table.get(text, f"<{text}>")

    read.calls = calls
    return read


# ---------------------------------------------------------------------------
# 1 文字ずつ読まれているかの判定
# ---------------------------------------------------------------------------


def test_long_words_read_letter_by_letter_are_found():
    table = letter_readings(fake_reader())

    assert is_spelled_out("SUCCEEDED", WORDS["SUCCEEDED"], table)
    assert is_spelled_out("PASS", WORDS["PASS"], table)
    assert is_spelled_out("JSON", WORDS["JSON"], table)


def test_short_acronyms_are_not_reported():
    """`AI` `MCP` `SDK` は 1 文字ずつが正しい読みなので、挙げない。"""
    table = letter_readings(fake_reader())

    assert not is_spelled_out("AI", WORDS["AI"], table)
    assert not is_spelled_out("MCP", WORDS["MCP"], table)
    assert not is_spelled_out("SDK", WORDS["SDK"], table)
    assert SPELLED_MIN_LETTERS == 4


def test_words_read_as_words_are_not_reported():
    table = letter_readings(fake_reader())

    assert not is_spelled_out("status", WORDS["status"], table)
    assert not is_spelled_out("Runner", WORDS["Runner"], table)
    # 読み違いではあるが、1 文字ずつではない。何が正しいかは機械には決められない。
    assert not is_spelled_out("aws", WORDS["aws"], table)


def test_long_vowel_written_two_ways_is_the_same_sound():
    """`A` 単体は「エイ」だが、語の中では「エエ」と書かれる。同じ音として扱う。

    ここを見ないと `PASS` `WARN` `FAIL` `JSON` を取りこぼす(いずれも A か J を含む)。
    """
    assert normalize_kana("エイ") == normalize_kana("エエ")
    assert normalize_kana("ジェイ") == normalize_kana("ジェエ")
    assert normalize_kana("ケイ") == normalize_kana("ケエ")
    # イ段の「イ」は長音ではないので、そのまま。
    assert normalize_kana("アイ") == "アイ"


def test_the_letter_table_comes_from_the_engine():
    """表を書き写さず、エンジンに聞く。版が変われば表も変わる。"""
    read = fake_reader()

    table = letter_readings(read)

    assert read.calls == list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert table["A"] == normalize_kana("エイ")


# ---------------------------------------------------------------------------
# 英字の語の取り出し
# ---------------------------------------------------------------------------


def test_identifiers_stay_one_word():
    """`sfn_status` は 1 語として扱う(辞書に書くときの単位に合わせる)。"""
    assert latin_words("sfn_statusとresult.jsonを見ます") == ["sfn_status", "result", "json"]


def test_digits_belong_to_the_word_before_them():
    assert latin_words("boto3 と S3 と case_001") == ["boto3", "S3", "case_001"]


def test_japanese_only_text_has_no_latin_words():
    assert latin_words("試験結果を判定する") == []


# ---------------------------------------------------------------------------
# 原稿全体を調べる
# ---------------------------------------------------------------------------


def test_report_lists_where_each_word_appears():
    plans = {
        1: plan_reading("statusを見ます。"),
        2: plan_reading("PASSかどうか。\nstatusも見ます。"),
    }
    read = fake_reader()

    report = inspect_readings(plans, read)

    where = {w.surface: w.slides for w in report.words}
    assert where == {"status": [1, 2], "PASS": [2]}


def test_report_flags_only_what_the_machine_can_be_sure_of():
    plans = {1: plan_reading("statusがPASSかどうかをAIが見ます。")}

    report = inspect_readings(plans, fake_reader())

    # 3 語とも読みは出すが、言い切れるのは PASS だけ。
    assert [w.surface for w in report.words] == ["status", "PASS", "AI"]
    assert [w.surface for w in report.spelled] == ["PASS"]


def test_warning_is_raised_only_when_something_is_spelled_out():
    quiet = inspect_readings({1: plan_reading("statusを見ます。")}, fake_reader())
    noisy = inspect_readings({1: plan_reading("PASSを見ます。")}, fake_reader())

    assert quiet.warnings() == []
    assert len(noisy.warnings()) == 1
    assert "PASS" in noisy.warnings()[0]


def test_dictionary_template_holds_the_current_reading():
    """雛形の値は今の読み。これを直して `--dict` に渡す。"""
    report = inspect_readings({1: plan_reading("PASSとstatus。")}, fake_reader())

    assert report.dictionary_template() == {"PASS": WORDS["PASS"]}


def test_words_only_mode_does_not_ask_for_each_line():
    """音声を書き出すついでに確かめるときは、問い合わせを語の数まで減らす。"""
    plans = {1: plan_reading("statusを見ます。もう一文あります。")}
    read = fake_reader()

    report = inspect_readings(plans, read, lines=False)

    assert "statusを見ます。" not in read.calls
    assert [w.surface for w in report.words] == ["status"]
    assert report.slides[0].lines[0].kana == ""


def test_lines_carry_the_script_and_its_reading():
    """漢字の読みは判定できないので、読んで確かめられる形で残す。"""
    plans = {1: plan_reading("見たい方は。")}
    read = fake_reader({"見たい方は。": "ミタイホオワ"})

    report = inspect_readings(plans, read)

    assert report.slides[0].lines == [LineReading("見たい方は。", "ミタイホオワ")]


def test_the_engine_is_asked_only_once_per_text():
    """同じ文字列は聞き直さない(A-Z の表と、繰り返し出てくる語のぶん)。"""

    class FakeEngine:
        def __init__(self):
            self.asked = []

        def read(self, text, style):
            self.asked.append(text)
            return [{"moras": [{"text": "ア"}]}]

    engine = FakeEngine()
    read = reading_kana_reader(engine, style=None)

    assert read("status") == "ア"
    assert read("status") == "ア"

    assert engine.asked == ["status"]


def test_empty_script_reports_nothing():
    report = inspect_readings({1: plan_reading("")}, fake_reader())

    assert report.words == []
    assert report.warnings() == []


def test_a_word_the_dictionary_already_fixed_is_not_reported_again():
    """直した語が「まだ危ない」と出続けないこと。

    読み方辞書は合成へ渡す文字だけを書き換える(画面の文字は `aws login` のまま)。
    英字を数えるのは合成へ渡すほうなので、直した語は一覧から消える。
    """
    from note2slides.reading import ReadingStyle

    style = ReadingStyle(dictionary={"aws": "エーダブリューエス"})
    plans = {1: plan_reading("awsとPASSを使います。", style)}

    report = inspect_readings(plans, fake_reader())

    assert [w.surface for w in report.words] == ["PASS"]
    # 画面に出る文字は元のまま。
    assert report.slides[0].lines[0].text == "awsとPASSを使います。"


def test_the_reading_shown_is_the_one_that_is_synthesized():
    """表示する仮名は、実際に合成へ渡す文字を読ませたもの。"""
    from note2slides.reading import ReadingStyle

    style = ReadingStyle(dictionary={"aws": "エーダブリューエス"})
    plans = {1: plan_reading("awsです。", style)}
    read = fake_reader({"エーダブリューエスです。": "エエダブリュウエスデス"})

    report = inspect_readings(plans, read)

    assert report.slides[0].lines[0].kana == "エエダブリュウエスデス"
