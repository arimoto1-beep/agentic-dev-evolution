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

#: 読みが 1 つに定まらない表記と、その候補(ひらがな)。
#:
#: gen29 は「英字が 1 文字ずつ読まれている」という **機械が言い切れる 1 種類** を
#: 見つけて、確かめる範囲を 16 分から 30 秒に縮めた。それでも人が gen29 の動画を
#: 聞いて「値」を「ね」、「通っている」を「かよっている」と読む誤りを見つけている。
#: **日本語は読み上げ単位ごとの仮名を全部読まないと分からない** ままだった。
#:
#: ここで言い切れるのは、正しい読みではなく **読みが分かれること** である。
#: 「方」を「ほう」と読むか「かた」と読むかは書いた人にしか決められないが、
#: 「この語は 2 通りに読まれる」ことは原稿と関係なく言える。だから
#: **どちらが正しいかは決めず、分かれる語だけを名指しする。**
#:
#: 候補を仮名で書くのは、エンジンに読ませて突き合わせるため
#: (`A-Z` の表を書き写さずエンジンに聞いた gen29 と同じ理由。版が変われば
#: 仮名の書き方も変わる)。**この表は「正しい読み」の表ではなく「割れる語」の表** で、
#: 網羅もしていない。足りなければ足す。
AMBIGUOUS_READINGS: Dict[str, Sequence[str]] = {
    # 人が実際に踏んだもの
    "値": ("あたい", "ね"),
    "通って": ("かよって", "とおって"),
    "通っている": ("かよっている", "とおっている"),
    "通した": ("とおした", "かよした"),
    "方": ("ほう", "かた"),
    # 送り仮名で読みが割れるもの
    "行った": ("おこなった", "いった"),
    "行って": ("おこなって", "いって"),
    "入って": ("はいって", "いって"),
    "開いて": ("ひらいて", "あいて"),
    "空いて": ("あいて", "すいて"),
    "分けて": ("わけて", "ぶんけて"),
    "重ねて": ("かさねて", "じゅうねて"),
    "外して": ("はずして", "がいして"),
    # 熟語として読みが割れるもの
    "一日": ("いちにち", "ついたち"),
    "一角": ("いっかく", "ひとかど"),
    "最中": ("さいちゅう", "もなか"),
    "上手": ("じょうず", "うわて", "かみて"),
    "下手": ("へた", "したて", "しもて"),
    "大事": ("だいじ", "おおごと"),
    "市場": ("しじょう", "いちば"),
    "変化": ("へんか", "へんげ"),
    "見物": ("けんぶつ", "みもの"),
    "生物": ("せいぶつ", "なまもの"),
    "気質": ("きしつ", "かたぎ"),
    "細々": ("こまごま", "ほそぼそ"),
    "後で": ("あとで", "ごで"),
    "その後": ("そのご", "そのあと", "そののち"),
    "皆": ("みな", "みんな"),
    "私": ("わたし", "わたくし"),
    # 「型」は熟語の中で濁る(ひな型 -> ヒナガタ)。濁った形を候補に入れないと、
    # 同じ行に「設計」(セッケエ)があるだけで **ケエ と読まれた** ことになり、
    # 正しく読めている語に誤った読みが付く。挙げるなら、読まれうる形を挙げる。
    "型": ("かた", "がた", "けい"),
}
# 入れないもの:「何」「数」「間」「他」のように、候補の仮名が短く
# (ナン・スウ・マ・タ)、文中の別の語にもそのまま現れる表記。読まれた読みを
# 見分けられないので、挙げても毎回「特定できません」になる。**判定できないものを
# 判定した風に出すと出力全体が信用されなくなる**(gen29)ので、黙って落とす。
# 表に書き損じが混ざると、警告が出ない・出続けるという形で静かに効くので、
# 読み込み時に形だけ確かめる(候補が 1 つ以下の項目は突き合わせに使えない)。
AMBIGUOUS_READINGS = {
    surface: tuple(readings)
    for surface, readings in AMBIGUOUS_READINGS.items()
    if len(readings) >= 2 and all(r.strip() for r in readings)
}

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
class AmbiguousWord:
    """読みが分かれる表記 1 つと、今回そう読まれたらしい読み。

    `kana` は、候補のうち **その読み上げ単位の仮名に実際に現れたもの** 。
    見分けが付かなければ空にする。**分からないものを分かった風に出さない。**
    """

    surface: str
    kana: str = ""
    alternatives: List[str] = field(default_factory=list)
    slides: List[int] = field(default_factory=list)

    def describe(self) -> str:
        where = "、".join(f"{n}枚目" for n in self.slides[:6])
        if len(self.slides) > 6:
            where += f" ほか{len(self.slides) - 6}枚"
        others = "/".join(self.alternatives)
        if self.kana:
            return f"{self.surface} -> {self.kana}(ほかに {others} とも読む語)({where})"
        return f"{self.surface} -> 読みを特定できません({others} のいずれか)({where})"


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
    ambiguous: List[AmbiguousWord] = field(default_factory=list)

    @property
    def spelled(self) -> List[WordReading]:
        """1 文字ずつ読まれている語。機械が「間違い」と言い切れるのはここだけ。"""
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
            "ambiguous": [
                {
                    "surface": word.surface,
                    "kana": word.kana,
                    "alternatives": word.alternatives,
                    "slides": word.slides,
                }
                for word in self.ambiguous
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
    report.ambiguous = _collect_ambiguous(report.slides, read_kana, resolve=lines)
    return report


def _collect_ambiguous(
    slides: Sequence[SlideReading],
    read_kana: Callable[[str], str],
    resolve: bool = True,
) -> List[AmbiguousWord]:
    """読みが分かれる表記を集め、どちらで読まれたかが分かるものは示す。

    表記を見つけるのは文字を照らし合わせるだけなので、音にも問い合わせにも
    頼らない。**どちらで読まれたか** を見るときだけ、その読み上げ単位の仮名に
    候補の仮名が現れるかを確かめる(`resolve=False` なら表記だけを挙げる)。

    1 つの表記につき 1 行にまとめる。「方」が 12 回出ても、読む側が確かめるのは
    **1 行** で済む —— 44 枚ぶんの仮名を読み下すのとは、作業がまるで違う。
    """
    # 出てきた表記ごとに、候補の仮名を 1 度だけエンジンに聞く。
    readings: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    # (表記, 読まれた読み)ごとのスライド番号。同じ語が画面によって別々に
    # 読まれることがあるので(「進め方」はカタ、「見たい方は」はホオ)、
    # **occurrence ごとに** 見分けて、読みごとに分ける。まとめて union すると
    # 両方の音が現れて、いつまでも「特定できません」になる。
    groups: Dict[tuple, List[int]] = {}

    for slide in slides:
        for line in slide.lines:
            for surface, candidates in AMBIGUOUS_READINGS.items():
                if surface not in line.to_speak:
                    continue
                if surface not in readings:
                    readings[surface] = {c: normalize_kana(read_kana(c)) for c in candidates}
                    order.append(surface)
                chosen = ""
                if resolve and line.kana:
                    seen = normalize_kana(line.kana)
                    hit = {k for k in readings[surface].values() if k and k in seen}
                    # 候補どうしが含み合う場合は、長いほうを採る。
                    # 「入って」の候補 ハイッテ / イッテ は、ハイッテ と読まれると
                    # イッテ も必ず一致してしまう。ここで捨てないと、正しく
                    # 読めている語が毎回「特定できません」として挙がる。
                    hit = {k for k in hit if not any(k != o and k in o for o in hit)}
                    if len(hit) == 1:
                        chosen = next(iter(hit))
                key = (surface, chosen)
                if key not in groups:
                    groups[key] = []
                if slide.index not in groups[key]:
                    groups[key].append(slide.index)

    found: List[AmbiguousWord] = []
    for surface in order:
        for (word, chosen), where in groups.items():
            if word != surface:
                continue
            found.append(
                AmbiguousWord(
                    surface=surface,
                    kana=chosen,
                    alternatives=[
                        kana or name
                        for name, kana in readings[surface].items()
                        if kana != chosen
                    ],
                    slides=where,
                )
            )
    return found


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
