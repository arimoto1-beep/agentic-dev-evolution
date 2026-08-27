"""合成した音声を聞かなくても、何と読まれるかを確かめられるようにする。

音声・字幕・動画は、原稿の文字が読み違えられても同じように生成できる。
終了コードは 0 で、ファイルの数も長さも正しい。**できたことと、聞いて意味が
通ることは別** で、後者は今のところ最後まで再生してみるまで分からない。
20 分の動画で 1 語の読み違いを見つけるには 20 分かかる。

ここでは合成の前に「何と読まれるか」を仮名として取り出す。音は作らないので、
1 枚あたり 1 秒もかからない。

    原稿 --(reading.plan_reading)--> 読み上げ単位 --(engine.read)--> 仮名

**正しい読みが何かは、機械には分からない。** 「方」は文脈によって「ほう」とも
「かた」とも読み、どちらが正しいかは書いた人にしか決められない。読みを機械が
決めれば、原稿に無いことを足すことになる(要約・言い換えを生成しないのと同じ線)。

そのため、ここが返すのは次の 2 つだけで、直すかどうかは書いた人が決める。

    1. 読み上げ単位ごとの「原稿」と「仮名」。読み違いが *読んで* 分かるようになる
    2. 英字の語ごとの仮名。合成エンジンは英語の辞書を持たず、
       ローマ字として読むため、英字はほぼここだけが危ない

このうち **機械が言い切れるのは 1 つ** ——「英字が 1 文字ずつ読まれている」こと。
`SUCCEEDED` が「エスユウシイシイイイイイディイイイディイ」と読まれるのは、
どんな原稿でも意図した読みではない。`AI` `MCP` のような略語は 1 文字ずつが
正しいので、`SPELLED_MIN_LETTERS` 文字以上のものだけを挙げる。

直し方は既にある(`ReadingStyle.dictionary` / `note2slides-audio --dict`)。
足りなかったのは、直すべき場所が **聞かないと分からない** ことだった。
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

#: 1 文字ずつ読まれていても意図どおりとみなす長さ。`AI` `MCP` `SDK` のような
#: 略語は 1 文字ずつが正しい読みなので、これを超えるものだけを挙げる。
#: 4 文字以上で 1 文字ずつ読まれるもの(`PASS` `JSON` `SUCCEEDED`)は、
#: 略語として読ませたい場合もあるため「間違い」ではなく「要確認」として扱う。
SPELLED_MIN_LETTERS = 4

#: 英字の語。`sfn_status` `result` `boto3` のように、識別子として書かれたものを
#: 1 語として取り出す(`.` `/` は合成エンジンもそこで切るため区切りとして扱う)。
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: エ段の仮名。この直後の「イ」は長音(エー)で、合成エンジンは同じ音を
#: 「エイ」とも「エエ」とも書く(`A` 単体は「エイ」、`PASS` の中では「エエ」)。
#: 1 文字ずつ読まれているかを比べるときは、どちらでも同じとみなす。
_E_ROW = "エケセテネヘメレゲゼデベペェ"
_LONG_E = re.compile(f"(?<=[{_E_ROW}])イ")


def normalize_kana(kana: str) -> str:
    """長音の書き方の違い(エイ / エエ)を吸収する。"""
    return _LONG_E.sub("エ", kana)


def latin_words(text: str) -> List[str]:
    """文字列に含まれる英字の語を、出てきた順に返す(重複は残す)。"""
    return _LATIN_WORD.findall(text)


def letter_readings(read_kana: Callable[[str], str]) -> Dict[str, str]:
    """A-Z を 1 文字ずつ読ませた仮名の対応表を作る。

    表を書き写さずにエンジンへ聞くのは、エンジンやその版が変わっても
    「1 文字ずつ読まれている」の判定がずれないようにするため。
    """
    return {letter: normalize_kana(read_kana(letter)) for letter in string.ascii_uppercase}


def spelled_kana(word: str, table: Dict[str, str]) -> str:
    """その語を 1 文字ずつ読んだ場合の仮名。数字は表に無いので飛ばす。"""
    return "".join(table.get(char.upper(), "") for char in word if char.isalpha())


def is_spelled_out(word: str, kana: str, table: Dict[str, str]) -> bool:
    """英字が 1 文字ずつ読まれているか。"""
    letters = [c for c in word if c.isalpha()]
    if len(letters) < SPELLED_MIN_LETTERS:
        return False
    expected = spelled_kana(word, table)
    return bool(expected) and normalize_kana(kana) == expected


@dataclass
class WordReading:
    """英字 1 語と、その読み。"""

    surface: str
    kana: str
    slides: List[int] = field(default_factory=list)
    #: 1 文字ずつ読まれている(`SPELLED_MIN_LETTERS` 文字以上)。
    spelled: bool = False

    def describe(self) -> str:
        where = "、".join(f"{n}枚目" for n in self.slides[:6])
        if len(self.slides) > 6:
            where += f" ほか{len(self.slides) - 6}枚"
        mark = " ← 1文字ずつ読まれています" if self.spelled else ""
        return f"{self.surface} -> {self.kana}{mark}({where})"


@dataclass
class LineReading:
    """読み上げ単位 1 つの、原稿と読み。

    `text` は画面に出る文字(字幕がこれを使う)、`spoken` は合成へ渡す文字。
    読み方辞書で直した区切りだけ 2 つが違う。英字を数えるのは `spoken` のほうで、
    **直した語が「まだ危ない」と出続けない** ようにする。
    """

    text: str
    kana: str
    spoken: str = ""

    @property
    def to_speak(self) -> str:
        return self.spoken or self.text


@dataclass
class SlideReading:
    """スライド 1 枚分の読み。"""

    index: int
    lines: List[LineReading] = field(default_factory=list)

    @property
    def kana(self) -> str:
        return "".join(line.kana for line in self.lines)


@dataclass
class PronunciationReport:
    """原稿全体を読ませた結果。"""

    slides: List[SlideReading] = field(default_factory=list)
    words: List[WordReading] = field(default_factory=list)

    @property
    def spelled(self) -> List[WordReading]:
        """1 文字ずつ読まれている語。機械が言い切れるのはここだけ。"""
        return [word for word in self.words if word.spelled]

    def warnings(self) -> List[str]:
        """音声を書き出すときに知らせる内容。"""
        spelled = self.spelled
        if not spelled:
            return []
        listed = "、".join(f"{w.surface}({w.kana})" for w in spelled)
        return [
            f"英字 {len(spelled)} 件が 1 文字ずつ読まれています: {listed}。"
            "意図した読みでなければ --dict で読み方を指定してください。"
        ]

    def dictionary_template(self) -> Dict[str, str]:
        """`--dict` にそのまま渡せる形。値は今の読みなので、直して使う。"""
        return {word.surface: word.kana for word in self.spelled}

    def to_dict(self) -> dict:
        return {
            "slides": [
                {
                    "index": slide.index,
                    "lines": [
                        {"text": l.text, "kana": l.kana}
                        | ({"spoken": l.spoken} if l.spoken else {})
                        for l in slide.lines
                    ],
                }
                for slide in self.slides
            ],
            "words": [
                {
                    "surface": word.surface,
                    "kana": word.kana,
                    "slides": word.slides,
                    "spelled": word.spelled,
                }
                for word in self.words
            ],
        }


def inspect_readings(
    plans: Dict[int, "object"],
    read_kana: Callable[[str], str],
    lines: bool = True,
    on_progress: Optional[Callable[[int], None]] = None,
) -> PronunciationReport:
    """読み上げ計画を仮名にする。`plans` は スライド番号 -> ReadingPlan。

    `read_kana` は文字列を仮名にする関数(`voicevox.VoicevoxEngine` を使う場合は
    `reading_kana_reader`)。エンジンに読みを聞くだけで、音は作らない。

    `lines=False` にすると、読み上げ単位ごとの仮名は作らず、英字の語だけを調べる。
    音声を書き出すついでに確かめるときは、こちらで足りる(問い合わせる回数が
    語の数まで減る)。
    """
    report = PronunciationReport()
    for index in sorted(plans):
        plan = plans[index]
        slide = SlideReading(index=index)
        for utterance in getattr(plan, "utterances", []):
            # 聞くのは合成へ渡す文字(読み方辞書を当てたあと)、見せるのは
            # 画面に出る文字。直した結果をそのまま確かめられるようにする。
            speak = getattr(utterance, "to_speak", utterance.text)
            slide.lines.append(
                LineReading(
                    utterance.text,
                    read_kana(speak) if lines else "",
                    speak if speak != utterance.text else "",
                )
            )
        report.slides.append(slide)
        if lines and on_progress:
            on_progress(index)
    report.words = _collect_words(report.slides, read_kana)
    return report


def _collect_words(
    slides: Sequence[SlideReading], read_kana: Callable[[str], str]
) -> List[WordReading]:
    """英字の語を集め、1 語ずつ読ませる。

    語だけを読ませるのは、読み上げ単位の仮名からは「どの仮名がどの語のものか」が
    分からないため(合成エンジンはアクセント句を返すが、元の文字との対応は返さない)。
    英字の読みは前後の日本語に影響されないので、1 語で聞いた読みが実際の読みになる。
    """
    order: List[str] = []
    where: Dict[str, List[int]] = {}
    for slide in slides:
        for line in slide.lines:
            for word in latin_words(line.to_speak):
                if word not in where:
                    where[word] = []
                    order.append(word)
                if slide.index not in where[word]:
                    where[word].append(slide.index)
    if not order:
        return []
    table = letter_readings(read_kana)
    words = [WordReading(surface=w, kana=read_kana(w), slides=where[w]) for w in order]
    for word in words:
        word.spelled = is_spelled_out(word.surface, word.kana, table)
    return words


def reading_kana_reader(engine, style) -> Callable[[str], str]:
    """VOICEVOX のエンジンを、文字列 -> 仮名 の関数にする。"""
    cache: Dict[str, str] = {}

    def read(text: str) -> str:
        if text not in cache:
            cache[text] = kana_of(engine.read(text, style))
        return cache[text]

    return read


def kana_of(phrases: Iterable[dict]) -> str:
    """`/accent_phrases` の返す形から仮名だけを取り出す。"""
    return "".join(
        "".join(mora.get("text", "") for mora in phrase.get("moras", []))
        for phrase in phrases
    )
