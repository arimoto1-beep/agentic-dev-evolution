"""画面いっぱいの場面に共通する「舞台」。

    画面の大きさ   1920 x 1080(動画のコマと同じ。スライドの枠には入れない)
    上の帯         左に「いま何章か」、右に札
    見出し         その場面の題。最初のコマから出ている
    中身           場面ごとに描く(題材の側の `draw.py`)
    結び           その場面で言い切ること。いつも同じ場所(下)に、同じ形で出す
    断り           模式図であることの断り。いちばん下。層に入れない(いつも出ている)

Gen37 で 1 本の題材の側に作り、Gen39 で別の題材でも章の名前を替えるだけで使えたので、ここへ移した。
章の並びと札の種類は題材ごとに違うので、引数で渡す。絵の中身はここに持たない(`sketch.py` と同じ線)。

札の色と形はここで決めている(札の中身の意味は、道具の側では決めない。Gen34)。色は部品の色と
重ならないように選び、色だけに頼らないよう形も変えている。

    確認できたこと   濃紺の塗り + チェック
    推測             黄土色の塗り
    未確認           灰色の破線の枠 + ？
    問い             赤の枠 + ？
"""

from __future__ import annotations

from typing import Optional, Sequence

from .sketch import VIDEO_SIZE, Sketch

W, H = VIDEO_SIZE

BG = (246, 247, 250)
DARK = (17, 28, 46)
DARK_SOFT = (30, 44, 68)
INK = (26, 32, 44)
MUTED = (104, 112, 125)
LINE = (214, 218, 226)
WHITE = (255, 255, 255)
PANEL = (236, 239, 244)
NAVY = (30, 58, 95)
SKY = (120, 180, 240)

ACCENT = (27, 110, 185)
ALERT = (196, 54, 54)
AMBER = (176, 110, 12)
AMBER_FILL = (252, 235, 196)
GRAY = (104, 112, 125)

#: 動きの連番は、動画のコマ(30fps)と 1 対 1。
FPS = 30

FACT, GUESS, UNKNOWN, QUESTION = "確認できたこと", "推測", "未確認", "問い"

LABELS = {
    FACT: dict(fill=NAVY, text=WHITE, outline=None, dash=None, mark="check", edge=NAVY),
    GUESS: dict(fill=AMBER_FILL, text=AMBER, outline=AMBER, dash=None, mark="", edge=(214, 150, 40)),
    UNKNOWN: dict(fill=WHITE, text=GRAY, outline=GRAY, dash=(12, 8), mark="？", edge=(150, 156, 168)),
    QUESTION: dict(fill=(253, 240, 240), text=ALERT, outline=ALERT, dash=None, mark="？", edge=ALERT),
}


def stage(
    chapter: int = 0,
    label: str = "",
    heading: str = "",
    dark: bool = False,
    chapters: Sequence[str] = (),
) -> Sketch:
    """舞台を置いた画用紙。ここで描いたものは層の外なので、どの段でも出ている。

    `chapters` は動画の章の並び、`chapter` はそのうち何番目か(1 から)。
    """
    sk = Sketch(W, H, background=DARK if dark else BG)
    sk.dark = dark  # 場面を描く側が、地の色を見て文字の色を選べるように
    if label:
        sk.rect(0, 0, W, 8, fill=LABELS[label]["edge"])
        chip(sk, label, W - 60, 62, anchor="r", dark=dark)
    if chapter and chapters:
        chapter_strip(sk, chapter, chapters, dark)
    if heading:
        sk.text((64, 164), heading, size=50, bold=True, anchor="lm", halo=False,
                color=WHITE if dark else INK)
    return sk


def chapter_strip(sk: Sketch, chapter: int, chapters: Sequence[str], dark: bool = False) -> None:
    """いま何章か。済んだ章は塗り、いまの章は番号、これからの章は輪。"""
    x, y = 72, 62
    done = SKY if dark else ACCENT
    todo = (86, 100, 124) if dark else (190, 196, 206)
    for number in range(1, len(chapters) + 1):
        if number < chapter:
            sk.circle((x, y), 7, fill=done)
            x += 26
        elif number == chapter:
            sk.circle((x + 10, y), 19, fill=done)
            sk.text((x + 10, y - 1), str(number), size=24, bold=True, color=DARK if dark else WHITE, halo=False)
            x += 46
        else:
            sk.circle((x, y), 7, outline=todo, width=3)
            x += 26
    sk.text((x + 8, y - 1), chapters[chapter - 1], size=26, bold=True, anchor="lm", halo=False,
            color=(196, 206, 222) if dark else MUTED)


def chip(sk: Sketch, label: str, x: float, cy: float, size: float = 28, anchor: str = "r",
         dark: bool = False) -> float:
    """札。`anchor` は x がどこを指すか(l = 左端、m = 中央、r = 右端)。幅を返す。"""
    style = LABELS[label]
    pad = size * 0.8
    mark = size * 1.25 if style["mark"] else 0.0
    text_width = sk.text_width(label, size, True)
    width, height = pad * 2 + mark + text_width, size * 2.0
    left = {"l": x, "m": x - width / 2, "r": x - width}[anchor]
    fill = style["fill"]
    if dark and label == UNKNOWN:
        fill = WHITE
    sk.rect(left, cy - height / 2, width, height, fill=fill, radius=height / 2)
    if style["outline"]:
        sk.rect(left, cy - height / 2, width, height, outline=style["outline"], width=max(3, size / 9),
                radius=height / 2, dash=style["dash"])
    elif dark:
        # 濃紺の札は、暗い地に沈む。縁を明るくして、札の形が見えるようにする。
        sk.rect(left, cy - height / 2, width, height, outline=SKY, width=max(3, size / 10), radius=height / 2)
    at = left + pad
    if style["mark"] == "check":
        s = size * 0.42
        cx = at + size * 0.45
        sk.line([(cx - s, cy), (cx - s * 0.25, cy + s * 0.75), (cx + s, cy - s * 0.7)], style["text"], size / 6.5)
    elif style["mark"]:
        sk.text((at + size * 0.45, cy - 1), style["mark"], size=size, bold=True, color=style["text"], halo=False)
    sk.text((at + mark, cy - 1), label, size=size, bold=True, color=style["text"], anchor="lm", halo=False)
    return width


def note(sk: Sketch, text: str) -> None:
    """模式図としての断り。いちばん下。層に入れない(どの段でも、動いている最中でも出す)。"""
    sk.text((W / 2, H - 34), text, size=24, color=(170, 180, 198) if sk.dark else MUTED, halo=False)


def takeaway(sk: Sketch, text: str, y: float = 960, size: float = 40, fill=NAVY, color=WHITE,
             outline=None, width: Optional[float] = None, x: float = W / 2) -> None:
    """その場面で言い切ること。いつも同じ場所・同じ形で出す(目が、結論の置き場所を覚える)。"""
    lines = text.split("\n")
    wide = width or (max(sk.text_width(line, size, True) for line in lines) + 110)
    height = size * 1.32 * len(lines) + size * 0.9
    sk.rect(x - wide / 2, y - height / 2, wide, height, fill=fill, outline=outline, width=4, radius=min(42, height / 2))
    sk.text((x, y - 2), text, size=size, bold=True, color=color, halo=False)


def card(sk: Sketch, x: float, y: float, w: float, h: float, fill=WHITE, radius: float = 26,
         outline=None, width: float = 3, shadow: bool = True, dash=None) -> None:
    """紙のように、地から少し浮いた箱。"""
    if shadow:
        sk.rect(x + 2, y + 8, w - 4, h, fill=(226, 229, 236), radius=radius)
    sk.rect(x, y, w, h, fill=fill, radius=radius)
    if outline:
        sk.rect(x, y, w, h, outline=outline, width=width, radius=radius, dash=dash)


def check_mark(sk: Sketch, center, size: float = 22, color=WHITE, width: float = 7) -> None:
    cx, cy = center
    sk.line([(cx - size, cy), (cx - size * 0.25, cy + size * 0.75), (cx + size, cy - size * 0.7)], color, width)


def cross_mark(sk: Sketch, center, size: float = 20, color=ALERT, width: float = 8) -> None:
    cx, cy = center
    sk.line([(cx - size, cy - size), (cx + size, cy + size)], color, width)
    sk.line([(cx - size, cy + size), (cx + size, cy - size)], color, width)


def steps(sk: Sketch, path: str, plan: Sequence[dict], opening: Optional[dict] = None, **options) -> None:
    """段の並びを書き出す。最初の合図までの姿は、何も出していない舞台(見出しと札だけ)。"""
    sk.save_steps(path, plan, opening=opening if opening is not None else dict(show=[]), fps=FPS, **options)
