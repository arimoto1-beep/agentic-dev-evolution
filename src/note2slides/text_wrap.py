"""日本語のタイトルを、自然な位置で折り返す。

PowerPoint 側の自動折り返しは、幅に入るところで機械的に切る。そのため
「開発環境をゼロか / ら作り直す」のように、語の途中で改行されることがある。
表紙や章扉のタイトルは大きく映るぶん、この不自然さがそのまま目につく。

そこでタイトルは、描く前にこちらで改行位置を決め、明示的な改行として書き込む
(`renderer` は文字列の中の "\n" を `<a:br/>` にする)。決め方は次の 2 つ。

* **禁則** — 行頭に来てはいけない文字(、。」など)、行末に来てはいけない文字
  (「(など)の前後では切らない。英単語の途中でも切らない。
* **切り目らしさと、行の長さのそろい** — 句読点のあと、助詞のあと、文字種の
  変わり目の順に切りたい。同時に、1 行目だけが極端に長い状態も避けたい。

行数は「収まる範囲で最も少ない行数」を選び、その中で上の 2 つの合計が最も
小さい切り方を採る。厳密な組版ではなく、機械的な折り返しより読みやすければよい。
"""

from __future__ import annotations

import unicodedata
from typing import List, Optional, Sequence, Tuple

from . import metrics

#: 行頭に置かない文字(行頭禁則)。
_NO_LINE_START = set(
    "、。，．,.)）]］}｝〕〉》」』】!?！？:;：；・…‥ーゝゞ々"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
    "%％‰℃°ﾟ/／"
)

#: 行末に置かない文字(行末禁則)。
_NO_LINE_END = set("([｛（[［{〔〈《「『【¥￥$＄#＃@＠")

#: 切り目としての良さ(小さいほど切ってよい)。
_PENALTY_SENTENCE = 0.0  # 。! ? のあと
_PENALTY_COMMA = 0.5  # 、, のあと
_PENALTY_SPACE = 1.0  # 空白のところ
_PENALTY_BRACKET = 2.0  # 閉じ括弧のあと / 開き括弧の前
_PENALTY_PARTICLE = 3.0  # 助詞のあと
_PENALTY_KANA_TO_WORD = 4.0  # ひらがな -> 漢字・カタカナ・英数
_PENALTY_CLASS_CHANGE = 6.0  # そのほかの文字種の変わり目
_PENALTY_INSIDE_WORD = 25.0  # 同じ文字種の途中(最後の手段)

#: 1 文字の助詞。このあとは文節の切れ目になりやすい。
_PARTICLES_1 = set("はがをにへとでもやかねよなの")
#: 2 文字以上の助詞・接続。前方一致で見る。
_PARTICLES_N = (
    "から", "まで", "より", "など", "ため", "ので", "のに",
    "こそ", "でも", "とは", "には", "では", "への", "との",
)

#: 行の長さのそろいを、切り目の良さと同じものさしに乗せるための重み。
_BALANCE_WEIGHT = 10.0


def _char_class(ch: str) -> str:
    if ch.isspace():
        return "space"
    if ch.isascii() and (ch.isalnum() or ch in "-_./"):
        return "latin"
    name = unicodedata.name(ch, "")
    # 長音符(ー)の正式名称は KATAKANA-HIRAGANA PROLONGED SOUND MARK なので、
    # カタカナを先に見る(ひらがな扱いにすると、カタカナ語の途中が
    # 「文字種の変わり目」に見えてしまう)。
    if "KATAKANA" in name:
        return "katakana"
    if "HIRAGANA" in name:
        return "hiragana"
    if "CJK" in name:
        return "kanji"
    return "other"


def _may_break(text: str, index: int) -> bool:
    """text[index-1] と text[index] のあいだで改行してよいか。"""
    prev, nxt = text[index - 1], text[index]
    if prev in _NO_LINE_END or nxt in _NO_LINE_START:
        return False
    if prev.isspace() or nxt.isspace():
        return True
    # 英単語・数値の途中では切らない。
    return not (_char_class(prev) == "latin" and _char_class(nxt) == "latin")


def _break_penalty(text: str, index: int) -> float:
    prev, nxt = text[index - 1], text[index]
    if prev in "。．.!?！？":
        return _PENALTY_SENTENCE
    if prev in "、，,・":
        return _PENALTY_COMMA
    if prev.isspace() or nxt.isspace():
        return _PENALTY_SPACE
    if prev in "）)」』】〉》]］":
        return _PENALTY_BRACKET
    if nxt in "（(「『【〈《[［":
        return _PENALTY_BRACKET
    if _is_particle_end(text, index):
        return _PENALTY_PARTICLE
    before, after = _char_class(prev), _char_class(nxt)
    if before == after:
        return _PENALTY_INSIDE_WORD
    if before == "hiragana":
        return _PENALTY_KANA_TO_WORD
    return _PENALTY_CLASS_CHANGE


def _is_particle_end(text: str, index: int) -> bool:
    """text[:index] が助詞で終わっているか(助詞だけの語は数えない)。"""
    if _splits_particle(text, index):
        return False
    for particle in _PARTICLES_N:
        if text[:index].endswith(particle) and index > len(particle):
            return True
    prev = text[index - 1]
    return prev in _PARTICLES_1 and index > 1 and _char_class(text[index - 2]) != "hiragana"


def _splits_particle(text: str, index: int) -> bool:
    """2 文字以上の助詞を、途中で切ってしまう位置かどうか。

    「から」の「か」は 1 文字の助詞でもあるため、これを見ないと
    「教材シナリオか / ら動画を作る」のような切り方を選んでしまう。
    """
    for particle in _PARTICLES_N:
        for offset in range(1, len(particle)):
            start = index - offset
            if start >= 0 and text[start : start + len(particle)] == particle:
                return True
    return False


def _candidates(text: str) -> List[int]:
    return [i for i in range(1, len(text)) if _may_break(text, i)]


def _line(text: str, start: int, end: int) -> str:
    return text[start:end].strip()


def _width(text: str, start: int, end: int) -> float:
    return metrics.text_width_em(_line(text, start, end))


def _best_split(
    text: str, max_em: float, lines: int, candidates: Sequence[int]
) -> Optional[Tuple[float, List[int]]]:
    """`lines` 行に分ける切り方のうち、費用が最も小さいものを返す。

    費用は「切り目の悪さの合計」と「行の長さのばらつき」の和。どの切り方でも
    幅に収まらない場合は None を返す(呼び出し側が行数を増やす)。
    """
    target = metrics.text_width_em(text.strip()) / lines
    # best[k][i]: 先頭から text[:i] までを k 行にしたときの最小費用。
    best: List[dict] = [{0: (0.0, [])} for _ in range(lines + 1)]
    ends = list(candidates) + [len(text)]
    for k in range(1, lines + 1):
        current: dict = {}
        for start, (cost, path) in best[k - 1].items():
            for end in ends:
                if end <= start:
                    continue
                if k < lines and end == len(text):
                    continue
                if k == lines and end != len(text):
                    continue
                width = _width(text, start, end)
                if width > max_em:
                    continue
                total = cost + ((width - target) / max(target, 1e-6)) ** 2 * _BALANCE_WEIGHT
                if end != len(text):
                    total += _break_penalty(text, end)
                if end not in current or total < current[end][0]:
                    current[end] = (total, path + [end])
        best[k] = current
    return best[lines].get(len(text))


def wrap_text(text: str, max_em: float, max_lines: int = 3) -> List[str]:
    """`max_em`(em 単位の幅)に収まるように、自然な位置で折り返す。

    既に改行が書かれている場合は、その改行を尊重してそれぞれを折り返す。
    自然な位置では収まらない場合は、幅だけを見て折り返す(語の途中で切れる)。
    """
    if "\n" in text:
        wrapped: List[str] = []
        for part in text.split("\n"):
            wrapped.extend(wrap_text(part, max_em, max_lines))
        return wrapped

    stripped = text.strip()
    if not stripped:
        return [""]
    natural = _natural_wrap(stripped, max_em, max_lines)
    if natural is not None:
        return natural
    return _hard_wrap(stripped, max_em, max_lines)


def natural_wrap(text: str, max_em: float, max_lines: int = 3) -> Optional[List[str]]:
    """自然な位置だけで折り返した行。切る場所が無ければ None を返す。

    語の途中で切るしかない場合も None にする。「収まらないなら文字を小さく
    する」という手を持っている呼び出し側(サムネイルの題)のための入口で、
    そちらは文字を小さくすれば、自然な位置で切れるようになる。
    """
    stripped = text.strip()
    if not stripped:
        return [""]
    if "\n" in stripped:
        lines: List[str] = []
        for part in stripped.split("\n"):
            wrapped = natural_wrap(part, max_em, max_lines)
            if wrapped is None:
                return None
            lines.extend(wrapped)
        return lines if len(lines) <= max_lines else None
    return _natural_wrap(stripped, max_em, max_lines, max_penalty=_PENALTY_INSIDE_WORD)


def _natural_wrap(
    text: str, max_em: float, max_lines: int, max_penalty: Optional[float] = None
) -> Optional[List[str]]:
    """`text`(前後の空白と改行が無いもの)を、自然な位置だけで折り返す。

    `max_penalty` を渡すと、それより悪い切り目(語の途中など)は候補にしない。
    そのぶん収まらないことが増えるが、収まらなければ None を返すので、
    呼び出し側は別の手(文字を小さくする)に移れる。
    """
    if max_em <= 0:
        return None
    if metrics.text_width_em(text) <= max_em:
        return [text]
    candidates = _candidates(text)
    if max_penalty is not None:
        candidates = [i for i in candidates if _break_penalty(text, i) < max_penalty]
    for lines in range(2, max_lines + 1):
        if len(candidates) < lines - 1:
            break
        found = _best_split(text, max_em, lines, candidates)
        if found:
            _, breaks = found
            bounds = [0] + breaks
            return [_line(text, bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    return None


def _hard_wrap(text: str, max_em: float, max_lines: int) -> List[str]:
    """自然な切り目では収まらない場合の、幅だけを見た折り返し。"""
    lines: List[str] = []
    current = ""
    width = 0.0
    for ch in text:
        char_width = metrics.char_width(ch)
        if current and width + char_width > max_em and len(lines) < max_lines - 1:
            lines.append(current)
            current, width = "", 0.0
        current += ch
        width += char_width
    if current:
        lines.append(current)
    return lines


def fit_lines(text: str, size_pt: float, width_pt: float, max_lines: int = 3) -> List[str]:
    """フォントサイズと幅(pt)から折り返す。`wrap_text` の呼びやすい形。"""
    return wrap_text(text, _max_em(size_pt, width_pt), max_lines)


def natural_fit_lines(
    text: str, size_pt: float, width_pt: float, max_lines: int = 3
) -> Optional[List[str]]:
    """フォントサイズと幅(pt)から、自然な位置だけで折り返す(`natural_wrap`)。"""
    return natural_wrap(text, _max_em(size_pt, width_pt), max_lines)


def _max_em(size_pt: float, width_pt: float) -> float:
    return width_pt / size_pt if size_pt > 0 else 0.0
