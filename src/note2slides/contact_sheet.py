"""スライド画像を並べた一覧(コンタクトシート)を作る。

音には「合成する前に何と読まれるかを確かめる」手立てがある(`pronunciation`)。
絵には無い。**何が正しい絵かを決められないので、自動では判定できない** ためで、
gen28 / gen29 / gen30 のいずれも、確かめる方法は「全部を画像にして目で見る」
だけだった。3 回とも、そのための並べ方をその場限りに書いている。

ここが引き受けるのは、**判定ではなく見比べやすさ** だけにする。

    slide_001.png ... slide_044.png --> contact_001.png(12 枚ずつ並べた 1 枚)

1 枚ずつ開くと、44 枚では 44 回開くことになり、**そもそも見ないほうへ倒れる**。
並べて 4 枚にすれば、崩れている画面は目に飛び込む(場所が余りすぎている、
文字が帯に重なっている、1 枚だけ極端に文字が小さい、といったものは、
隣と比べたときにいちばん見つけやすい)。

番号を必ず添えるのは、**見つけたものを指させるようにする** ため。
「3 枚目の図解が小さい」と言えなければ、直す場所が分からない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence

#: 1 枚のシートに並べる列数。16:9 を 4 列並べると横長になりすぎないため。
DEFAULT_COLUMNS = 4

#: 1 枚のシートに並べる行数。列数と合わせて 12 枚。44 枚なら 4 シートになる。
DEFAULT_ROWS = 3

#: 並べる 1 枚あたりの幅(画素)。元が 1920 でも、崩れは縮めても分かる。
DEFAULT_CELL_WIDTH = 640

#: 画面の周りの余白と、番号を書く帯の高さ(1 枚あたりの幅に対する割合)。
_GAP_RATIO = 0.025
_LABEL_RATIO = 0.075

_BACKGROUND = (24, 26, 30)
_LABEL_COLOR = (235, 236, 238)
_BORDER_COLOR = (70, 74, 82)


class ContactSheetError(RuntimeError):
    """一覧の生成に失敗した場合。"""


@dataclass
class ContactSheet:
    """書き出した一覧 1 枚。"""

    path: str
    #: この 1 枚に載っているスライド番号。
    slides: List[int]
    width: int
    height: int


def _load_font(size: int):
    """番号に使うフォント。環境ごとの有無に左右されないものを選ぶ。

    番号は半角数字だけなので、字形の good/bad は問題にならない。
    見つからないフォントを探し回るより、Pillow が同梱しているもので確実に出す。
    """
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Pillow 10.1 より前は大きさを指定できない。
        return ImageFont.load_default()


def build_contact_sheets(
    image_paths: Sequence[str],
    outdir: str,
    numbers: Optional[Sequence[int]] = None,
    columns: int = DEFAULT_COLUMNS,
    rows: int = DEFAULT_ROWS,
    cell_width: int = DEFAULT_CELL_WIDTH,
    prefix: str = "contact_",
    fmt: str = "png",
) -> List[ContactSheet]:
    """スライド画像を `columns` x `rows` ずつ並べた一覧を書き出す。

    `numbers` はそれぞれの画像に添える番号(省略すると 1 から数える)。
    元の画像の縦横比はそのまま保つ。**縮めるだけで、切り取らない** ——
    はみ出しを探すための一覧で切り取ると、探しているものが消える。
    """
    from PIL import Image, ImageDraw

    if not image_paths:
        raise ContactSheetError("並べる画像がありません。")
    if columns < 1 or rows < 1:
        raise ContactSheetError("列数と行数は 1 以上で指定してください。")
    if numbers is None:
        numbers = list(range(1, len(image_paths) + 1))
    if len(numbers) != len(image_paths):
        raise ContactSheetError("画像の数と番号の数が一致しません。")

    with Image.open(image_paths[0]) as first:
        source_w, source_h = first.size
    if source_w <= 0 or source_h <= 0:
        raise ContactSheetError(f"画像の大きさを取得できませんでした: {image_paths[0]}")

    cell_h = max(1, round(cell_width * source_h / source_w))
    gap = max(2, round(cell_width * _GAP_RATIO))
    label_h = max(12, round(cell_width * _LABEL_RATIO))
    font = _load_font(max(11, round(label_h * 0.62)))

    sheet_w = gap + columns * (cell_width + gap)
    sheet_h = gap + rows * (cell_h + label_h + gap)

    os.makedirs(outdir, exist_ok=True)
    per_sheet = columns * rows
    sheets: List[ContactSheet] = []

    for start in range(0, len(image_paths), per_sheet):
        chunk = list(zip(image_paths[start : start + per_sheet], numbers[start : start + per_sheet]))
        sheet = Image.new("RGB", (sheet_w, sheet_h), _BACKGROUND)
        draw = ImageDraw.Draw(sheet)
        for position, (path, number) in enumerate(chunk):
            col, row = position % columns, position // columns
            left = gap + col * (cell_width + gap)
            top = gap + row * (cell_h + label_h + gap)
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize((cell_width, cell_h), Image.LANCZOS), (left, top))
            # 白い資料が背景に溶けないよう、1 枚ずつ枠で囲う。
            draw.rectangle(
                [left, top, left + cell_width - 1, top + cell_h - 1], outline=_BORDER_COLOR
            )
            draw.text(
                (left, top + cell_h + label_h * 0.18),
                f"{number}",
                fill=_LABEL_COLOR,
                font=font,
            )
        index = len(sheets) + 1
        path = os.path.join(outdir, f"{prefix}{index:03d}.{fmt}")
        sheet.save(path)
        sheets.append(
            ContactSheet(
                path=path, slides=[n for _, n in chunk], width=sheet_w, height=sheet_h
            )
        )
    return sheets
