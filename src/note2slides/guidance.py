"""スライドに映っているものを、ナレーションで案内する文にする。

表・コード・図は、画面には出ているが、そのままでは読み上げる文字にならない。
何も言わないまま映すと、視聴者は何を見ればよいのか分からないまま次のスライドへ
進んでしまう。ここでは、画面に出ている文字だけを使って、どこを見ればよいかが
分かる案内文を組み立てる。

    表 --> 「画面の表をご覧ください。列は左から、工程、入力、出力です。
            資料生成の行は、入力が Markdown 記事、出力がプレゼンテーションです。…」

記事に無い事実や意味は足さない。案内文に使うのは次の 2 つだけで、どちらも
画面を見れば確かめられる。

    * 画面に出ている文字(表のセル、コードのコメント、コードの行数)
    * 画面上の位置や種類を指す言葉(「画面の表」「列は左から」「〜の行」)

表の内容を並べ替えたり、まとめたり、そこから言えることを付け加えたりはしない。
コードは 1 文字ずつ読み上げても聞き取れないため、何が何行出ているかを伝え、
日本語で書かれたコメント(記事の書き手が読み手に向けて書いた文)だけを読む。

読み上げるだけでは足りない画面(コード・図)には、視聴者が画面を読む時間
(`hold_for_*`)を用意する。この時間は音声の後ろの無音になり、そのぶん
スライドが長く映る。
"""

from __future__ import annotations

import re
from typing import List, Sequence

#: 画面を指す言い方。「続き」は、1 枚に収まらず次のスライドへ分かれた場合。
TABLE_LEAD = "画面の表をご覧ください。"
TABLE_CONTINUED = "表の続きです。"
IMAGE_LEAD = "画面の図をご覧ください。"
IMAGE_CONTINUED = "図の続きです。"

#: シェルのコードブロックに付く言語名。これらは「コード」ではなく「コマンド」と呼ぶ。
SHELL_LANGUAGES = frozenset(
    {"bash", "sh", "shell", "zsh", "fish", "console", "terminal", "shell-session",
     "powershell", "pwsh", "ps1", "cmd", "bat", "batch", "dos"}
)

#: 読み上げても聞き取れる言語名。ここに無い言語名は読み上げない(合成エンジンが
#: 読めない綴りをそのまま渡すと、意味の取れない音になるため)。
LANGUAGE_NAMES = {
    "python": "パイソン",
    "javascript": "ジャバスクリプト",
    "js": "ジャバスクリプト",
    "typescript": "タイプスクリプト",
    "ts": "タイプスクリプト",
    "java": "ジャバ",
    "kotlin": "コトリン",
    "swift": "スイフト",
    "go": "ゴー",
    "golang": "ゴー",
    "rust": "ラスト",
    "ruby": "ルビー",
    "php": "ピーエイチピー",
    "sql": "エスキューエル",
    "html": "エイチティーエムエル",
    "css": "シーエスエス",
    "json": "ジェイソン",
    "yaml": "ヤムル",
    "yml": "ヤムル",
    "toml": "トムル",
    "xml": "エックスエムエル",
    "markdown": "マークダウン",
    "md": "マークダウン",
}

#: コードのコメントの始まり。行頭にこれがある行をコメントとみなす。
COMMENT_MARKERS = ("#", "//")

#: 画面を読む時間(秒)。コードは行数に比例させ、短すぎ・長すぎを避ける。
CODE_HOLD_PER_LINE = 0.6
CODE_HOLD_MIN = 1.0
CODE_HOLD_MAX = 6.0
IMAGE_HOLD = 1.5

#: 日本語が含まれるか(コメントが読み手向けの文かどうかの判断に使う)。
_JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龥々〆ー]")
_SPACES = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------


def describe_table(
    header: Sequence[str], rows: Sequence[Sequence[str]], continued: bool = False
) -> str:
    """表を、画面と対応付けて読み上げられる文にする。

    見出しがある場合は「列は左から〜」で列の並びを示してから、行ごとに
    「〜の行は、〜が〜です」と読む。視聴者が読み上げを聞きながら、画面の
    どの行を見ればよいか分かるようにするため、行の呼び名には左端のセルを使う。
    """
    header = [_cell(value) for value in header or []]
    rows = [[_cell(value) for value in row] for row in rows or []]
    rows = [row for row in rows if any(row)]
    if not header and not rows:
        return ""

    lines = [TABLE_CONTINUED if continued else TABLE_LEAD]
    if header:
        lines.append(f"列は左から、{_enumerate(header)}です。")

    columns = max([len(header)] + [len(row) for row in rows])
    if columns <= 1:
        values = [row[0] for row in rows if row and row[0]]
        if values:
            lines.append(f"{_enumerate(values)}が並んでいます。")
        return "\n".join(lines)

    for number, row in enumerate(rows, start=1):
        sentence = _row_sentence(header, row, number)
        if sentence:
            lines.append(sentence)
    return "\n".join(lines)


def _row_sentence(header: Sequence[str], row: Sequence[str], number: int) -> str:
    """1 行分の文。見出しがあれば「見出しが値」の形で組み合わせる。"""
    values = [value for value in row if value]
    if not values:
        return ""
    if not header:
        return f"{number}行目は、{_enumerate(values)}です。"

    label = row[0] if row else ""
    pairs = []
    for column in range(1, len(row)):
        value = row[column]
        if not value:
            continue
        name = header[column] if column < len(header) else ""
        pairs.append(f"{name}が{value}" if name else value)

    if label and pairs:
        return f"{label}の行は、{_enumerate(pairs)}です。"
    if pairs:
        return f"{number}行目は、{_enumerate(pairs)}です。"
    return f"{number}行目は、{label}です。"


def _enumerate(values: Sequence[str]) -> str:
    return "、".join(values)


def _cell(value: object) -> str:
    """セルの文字を 1 行に整える(表の中の改行は読み上げでは区切りにしない)。"""
    if value is None:
        return ""
    return _SPACES.sub(" ", str(value).replace("\v", " ")).strip()


# ---------------------------------------------------------------------------
# コード
# ---------------------------------------------------------------------------


def describe_code(text: str, language: str = "", continued: bool = False) -> str:
    """コードやコマンドの画面を案内する文にする。

    コードそのものは読み上げない(1 文字ずつ読まれても聞き取れない)。
    何がどれだけ出ているかを伝えたうえで、日本語のコメントだけを読む。
    """
    lines = [line for line in (text or "").replace("\v", "\n").split("\n") if line.strip()]
    if not lines:
        return ""

    name = (language or "").strip().lower()
    kind = "コマンド" if name in SHELL_LANGUAGES else "コード"
    spoken = LANGUAGE_NAMES.get(name, "")

    out = [f"{kind}の続きです。" if continued else f"画面の{kind}をご覧ください。"]
    if spoken:
        out.append(f"{spoken}の{kind}を{len(lines)}行示しています。")
    elif len(lines) > 1:
        out.append(f"{kind}を{len(lines)}行示しています。")
    out.extend(comment_lines(lines))
    return "\n".join(out)


def comment_lines(lines: Sequence[str]) -> List[str]:
    """日本語で書かれたコメント行を取り出す。

    コメントは書き手が読み手に向けて書いた文なので、そのまま読み上げても
    意味が通る。日本語を含む行だけに限るのは、`#!/usr/bin/env` のような
    コード側の記述を読み上げないため。
    """
    found = []
    for line in lines:
        stripped = line.strip()
        for marker in COMMENT_MARKERS:
            if not stripped.startswith(marker):
                continue
            body = stripped[len(marker) :].lstrip("#/ 　")
            if body and _JAPANESE.search(body):
                found.append(body)
            break
    return found


def hold_for_code(text: str) -> float:
    """コードを画面で読むための時間(秒)。行数に比例させる。"""
    lines = len([line for line in (text or "").split("\n") if line.strip()])
    if not lines:
        return 0.0
    return min(CODE_HOLD_MAX, max(CODE_HOLD_MIN, CODE_HOLD_PER_LINE * lines))


# ---------------------------------------------------------------------------
# 図
# ---------------------------------------------------------------------------


def describe_image(continued: bool = False) -> str:
    """図の画面を案内する文。図の内容は説明しない(見て分からない絵は補えない)。

    画面に説明(キャプション)が出ている場合は、この案内文のあとに、その文字が
    そのまま読み上げられる。
    """
    return IMAGE_CONTINUED if continued else IMAGE_LEAD


def hold_for_image() -> float:
    """図を画面で見るための時間(秒)。"""
    return IMAGE_HOLD
