"""ナレーション原稿を「読み上げる単位」と「間」に分ける。

同じ合成エンジンでも、原稿を 1 枚分まとめて渡すか、文ごとに区切って間を置くかで
聞こえ方は大きく変わる。まとめて渡すと、文の切れ目が詰まって早口に聞こえ、
長く聞いていると疲れる。ここでは原稿を文・行の単位に分け、それぞれの後ろに
置く無音の長さを決める。

    原稿(1 枚分の文章) --> ReadingPlan(読み上げる単位 + 各単位の後ろの無音)

あわせて、読み上げに向かない文字を落とす。ここで行うのは次だけで、要約・
言い換え・補足の生成は行わない。落としたもの・補ったものは ReadingPlan.notes に
残し、`narration.json` からも確認できるようにする。

    * 全角英数などの表記ゆれをそろえる(Unicode NFKC 正規化)
    * 行頭の箇条書き記号を落とす(画面には出ているが読み上げる文字ではない)
    * 絵文字・罫線など、読み上げても音にならない文字を落とす
    * URL を落とす(1 文字ずつ読まれてしまい、聞いても意味が取れない)
    * 日本語の間に入った空白を詰める(下記)
    * 文末に句点が無い行に句点を補う(無いと語尾が上がったまま次の文に続く)
    * 利用者が指定した読み方辞書で置き換える

「文末に句点を補う」以外は文字を減らすだけで、元の記事に無い内容は足さない。

日本語の間の空白を詰めるのは、記事では「1 枚」「30 秒」のように数字と単位の
間に空白を入れて書くことがあり、合成エンジンがそこを語の切れ目とみなして
「いち……まい」と間を空けて読んでしまうため。空白は表記上のものなので、
詰めても意味は変わらない(画面には元の表記がそのまま出る)。英数字どうしの
空白は語の区切りなので残す(`note2slides article.md` は 2 語のまま読む)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

#: 文の終わりとみなす記号(NFKC 正規化のあとなので半角と全角の両方を見る)。
SENTENCE_ENDINGS = "。！？!?"
#: 文が長いときに区切る位置。
CLAUSE_ENDINGS = "、，,"
#: 文末記号のあとに続いたら、前の文に付ける記号。
CLOSING_BRACKETS = "」』）)】〉》〕｝}\"'”’"

_URL = re.compile(r"https?://\S+|www\.[^\s、。]+")
# 行頭の箇条書き記号。文中の「・」は区切り文字(「A・B」)なので、行頭だけを見る。
# 「-」「*」「#」は本文にも出るため、後ろに空白がある場合だけ記号とみなす。
_LIST_MARKER = re.compile(r"^[\s　]*(?:[•‣▪◦・][\s　]*|(?:[-*+]|[#＃]{1,6})[\s　]+)")
# 装飾用の罫線や区切り線だけの行。
_RULE_LINE = re.compile(r"^[\s　]*[-=_─━＝\*]{3,}[\s　]*$")
# 絵文字・記号のうち、読み上げても音にならないもの。
_PICTOGRAPH = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 絵文字
    "←-⇿"  # 矢印
    "─-╿"  # 罫線
    "▀-◿"  # ブロック・図形
    "☀-➿"  # その他の記号
    "️‍⃣"  # 異体字セレクタ・結合子
    "]"
)
_SPACES = re.compile(r"[ \t　]+")
# 日本語(かな・漢字・和文の記号)。NFKC 正規化のあとを見るので、半角カナは
# 全角に、全角英数は半角になっている。
_JAPANESE_CHAR = r"[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿]"
# 片側が日本語で、もう片側も語の一部(日本語か英数字)になっている空白。ここは
# 語の区切りではないので詰める。記号が隣にある空白(「1. 四つ目」の「.」など)は、
# 詰めると読み方が変わることがあるため残す。
_JAPANESE_SPACE = re.compile(
    rf"(?<={_JAPANESE_CHAR}) (?=(?:{_JAPANESE_CHAR}|[0-9A-Za-z]))"
    rf"|(?<=[0-9A-Za-z]) (?={_JAPANESE_CHAR})"
)


@dataclass
class ReadingStyle:
    """読み上げの間の取り方。既定は eラーニング向けに少しゆっくりめ。"""

    sentence_pause: float = 0.35  # 文と文の間
    clause_pause: float = 0.2  # 長い文を区切ったときの間
    line_pause: float = 0.6  # 行(段落・箇条書きの項目)の間
    lead_silence: float = 0.3  # スライドが切り替わってから話し始めるまで
    tail_silence: float = 0.7  # 話し終わってから次のスライドまで
    max_chars: int = 100  # これを超える文は読点で区切る
    terminate_sentences: bool = True  # 句点の無い行に句点を補う
    drop_urls: bool = True  # URL を読み上げない
    dictionary: Dict[str, str] = field(default_factory=dict)  # 読み方の置き換え

    def validate(self) -> None:
        for name in ("sentence_pause", "clause_pause", "line_pause", "lead_silence", "tail_silence"):
            if getattr(self, name) < 0:
                raise ValueError(f"間の長さは 0 以上にしてください: {name}={getattr(self, name)}")
        if self.max_chars < 10:
            raise ValueError(f"文を区切る長さは 10 文字以上にしてください: {self.max_chars}")

    def to_dict(self) -> dict:
        return {
            "sentence_pause": self.sentence_pause,
            "clause_pause": self.clause_pause,
            "line_pause": self.line_pause,
            "lead_silence": self.lead_silence,
            "tail_silence": self.tail_silence,
            "max_chars": self.max_chars,
        }


@dataclass(frozen=True)
class Utterance:
    """1 回で読み上げる文字列と、その後ろに置く無音の長さ(秒)。"""

    text: str
    pause_after: float


@dataclass
class ReadingPlan:
    """1 枚のスライド分の読み上げ計画。"""

    utterances: List[Utterance] = field(default_factory=list)
    lead_silence: float = 0.0
    notes: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.utterances

    @property
    def reading(self) -> str:
        """実際に合成へ渡す文字列(確認用に 1 本につなげたもの)。"""
        return " ".join(u.text for u in self.utterances)

    def to_dict(self) -> dict:
        return {
            "lead_silence": round(self.lead_silence, 3),
            "utterances": [
                {"text": u.text, "pause_after": round(u.pause_after, 3)} for u in self.utterances
            ],
            "notes": self.notes,
        }


def plan_reading(
    text: str, style: Optional[ReadingStyle] = None, hold: float = 0.0
) -> ReadingPlan:
    """原稿 1 枚分を、読み上げる単位と間に分ける。

    `hold` は読み終わったあとに足す無音(秒)。コードや図のように、聞くだけでは
    足りず画面を読む必要があるスライドで、読む時間を作るために使う。
    """
    style = style or ReadingStyle()
    plan = ReadingPlan(lead_silence=style.lead_silence)
    if not text or not text.strip():
        return plan

    lines, notes = _normalize(text, style)
    plan.notes = notes
    if not lines:
        return plan

    tail_silence = style.tail_silence + max(0.0, hold)
    for line_number, line in enumerate(lines):
        pieces = _split_line(line, style)
        last_line = line_number == len(lines) - 1
        for piece_number, (piece, at_sentence_end) in enumerate(pieces):
            last_piece = piece_number == len(pieces) - 1
            if last_piece:
                pause = tail_silence if last_line else style.line_pause
            elif at_sentence_end:
                pause = style.sentence_pause
            else:
                pause = style.clause_pause
            plan.utterances.append(Utterance(piece, pause))
    return plan


# ---------------------------------------------------------------------------
# 読み上げ用の整形
# ---------------------------------------------------------------------------


def _normalize(text: str, style: ReadingStyle) -> tuple[List[str], List[str]]:
    """読み上げに向かない文字を落とし、行ごとの文字列にする。"""
    notes: List[str] = []
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\v", "\n").replace("\r\n", "\n").replace("\r", "\n")

    if style.drop_urls:
        dropped = len(_URL.findall(normalized))
        if dropped:
            normalized = _URL.sub(" ", normalized)
            notes.append(f"URL {dropped} 件は読み上げから外しました(スライドには表示されます)。")

    if _PICTOGRAPH.search(normalized):
        normalized = _PICTOGRAPH.sub(" ", normalized)
        notes.append("絵文字・記号は読み上げから外しました。")

    for surface, reading in style.dictionary.items():
        if surface and surface in normalized:
            normalized = normalized.replace(surface, reading)
            notes.append(f"読み方辞書で置き換えました: {surface} -> {reading}")

    lines: List[str] = []
    for raw in normalized.split("\n"):
        if _RULE_LINE.match(raw):
            continue
        line = _SPACES.sub(" ", _LIST_MARKER.sub("", raw)).strip()
        line = _JAPANESE_SPACE.sub("", line)
        if line:
            lines.append(line)
    return lines, notes


def _split_line(line: str, style: ReadingStyle) -> List[tuple[str, bool]]:
    """1 行を読み上げる単位に分ける。返す真偽値は「文の終わりか」。"""
    pieces: List[tuple[str, bool]] = []
    for sentence in _split_sentences(line):
        chunks = _split_clauses(sentence, style.max_chars)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            pieces.append((_terminate(chunk, style) if last else chunk, last))
    return pieces


def _split_sentences(line: str) -> List[str]:
    """句点・感嘆符・疑問符で文に分ける(記号は文の側に残す)。"""
    sentences: List[str] = []
    current = ""
    position = 0
    while position < len(line):
        char = line[position]
        current += char
        position += 1
        if char not in SENTENCE_ENDINGS:
            continue
        # 「そうです。」と書いてある のように、閉じ括弧は前の文に付ける。
        while position < len(line) and line[position] in CLOSING_BRACKETS:
            current += line[position]
            position += 1
        sentences.append(current)
        current = ""
    if current.strip():
        sentences.append(current)
    return [s.strip() for s in sentences if s.strip()]


def _split_clauses(sentence: str, max_chars: int) -> List[str]:
    """長い文を読点で区切る。息継ぎの位置を作り、早口に聞こえないようにする。"""
    if len(sentence) <= max_chars:
        return [sentence]
    chunks: List[str] = []
    current = ""
    for char in sentence:
        current += char
        if char in CLAUSE_ENDINGS and len(current) >= max_chars // 2:
            chunks.append(current)
            current = ""
    if current:
        # 残りが短すぎるときは、前の区切りに戻して不自然な間を作らない。
        if chunks and len(current) < 8:
            chunks[-1] += current
        else:
            chunks.append(current)
    return [c.strip() for c in chunks if c.strip()] or [sentence]


def _terminate(chunk: str, style: ReadingStyle) -> str:
    """句点で終わっていない行に句点を補う。

    スライドの箇条書きは句点が無いことが多い。そのまま読み上げると語尾が
    下がりきらず、次の項目と続いているように聞こえる。
    """
    if not style.terminate_sentences:
        return chunk
    stripped = chunk.rstrip()
    if not stripped or stripped[-1] in SENTENCE_ENDINGS + CLAUSE_ENDINGS + CLOSING_BRACKETS + "：:；;":
        return chunk
    return stripped + "。"


# ---------------------------------------------------------------------------
# 読み方辞書
# ---------------------------------------------------------------------------


def load_dictionary(path: str) -> Dict[str, str]:
    """読み方辞書(JSON)を読む。`{"表記": "よみ"}` の形。

    合成エンジンが読み間違える固有名詞や英単語を、こちらで平仮名・片仮名に
    置き換えるために使う。置き換えた内容は `narration.json` に残る。
    """
    import json

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise ValueError(f"読み方辞書を開けませんでした: {path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"読み方辞書が JSON として読めませんでした: {path}\n"
            f"{exc.lineno} 行 {exc.colno} 文字目: {exc.msg}"
        ) from exc
    if not isinstance(data, Mapping):
        raise ValueError(f'読み方辞書は {{"表記": "よみ"}} の形にしてください: {path}')

    dictionary: Dict[str, str] = {}
    for surface, reading in data.items():
        if not isinstance(surface, str) or not isinstance(reading, str):
            raise ValueError(f"読み方辞書は文字列だけで書いてください: {path}({surface!r})")
        if surface:
            dictionary[surface] = reading
    # 長い表記から先に置き換える(短い表記に食われないようにする)。
    return dict(sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True))
