"""記事とスライドの中間表現。

Markdown の解析結果(ブロック)と、スライド構成結果(スライド)をこのモジュールの
データ構造で表現する。解析・構成・描画の各段階はこの表現だけを介して連携する。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# インライン
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """書式が一様なテキスト片。text は原文の文字列をそのまま保持する。"""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: Optional[str] = None

    def with_text(self, text: str) -> "Run":
        return replace(self, text=text)


def runs_to_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def plain_run(text: str) -> List[Run]:
    return [Run(text)]


# ---------------------------------------------------------------------------
# ブロック(Markdown 由来)
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    level: int
    runs: List[Run]


@dataclass
class Paragraph:
    runs: List[Run]
    quote: bool = False


@dataclass
class ListItem:
    level: int
    runs: List[Run]
    ordered: bool = False
    number: Optional[str] = None
    quote: bool = False


@dataclass
class ListBlock:
    items: List[ListItem]


@dataclass
class CodeBlock:
    text: str
    lang: str = ""


@dataclass
class Table:
    header: List[str]
    rows: List[List[str]]


@dataclass
class Image:
    src: str
    alt: str = ""


@dataclass
class SlideBreak:
    """`---` による明示的なスライド区切り。"""


Block = object  # 上記いずれかのブロック


@dataclass
class Article:
    """記事全体。meta は front matter の内容。"""

    blocks: List[Block] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    source_path: Optional[str] = None


# ---------------------------------------------------------------------------
# スライド(描画用)
# ---------------------------------------------------------------------------

KIND_TITLE = "title"
KIND_SECTION = "section"
KIND_BULLETS = "bullets"
KIND_CODE = "code"
KIND_TABLE = "table"
KIND_IMAGE = "image"
#: 1 枚に複数の中身を縦に並べた画面(文章と図、表と補足文など)。中身は
#: `Slide.parts` に書かれた順で入る。教材シナリオだけが作る(記事入力は
#: 1 枚 = 1 つの中身のまま)。
KIND_CONTENT = "content"
#: 図解(流れ・枠)。ASCII アートではなく図形として描く。教材の図は
#: 「順に進む」か「何かの中に入っている」かのどちらかで書けることが多いので、
#: その 2 つだけを持つ。中身は `Content.diagram_items` に書かれた順で入る。
KIND_DIAGRAM = "diagram"
#: 動画の外側で使う 1 枚絵(YouTube のサムネイルなど)。資料には入れず、
#: `thumbnail.py` が単独の .pptx を作るときだけ使う。表紙より文字を大きくし、
#: 小さく表示されても題が読めるようにする。
KIND_THUMBNAIL = "thumbnail"

#: 上から下へ、矢印でつなぐ図(工程・流れ)。
DIAGRAM_FLOW = "flow"
#: 枠の中に並べる図(何が含まれているか)。
DIAGRAM_FRAME = "frame"
#: 1 本の線で上下に分け、線をまたぐものを矢印で示す図(役割の分かれ目)。
#: 流れ・枠と違って「何が線を越えるか」を書けるのが要で、越えるものを書けないなら
#: 枠 2 つで足りる。
DIAGRAM_BOUNDARY = "boundary"
#: 2 つのレーン(誰が受け持つか)を縦線で分け、時間を上から下へ流す図。
#: 流れ・枠・境界のどれでも書けないのは「順番と担当を同時に言う」ことで、
#: 担当が変わるところで矢印が線をまたぐ。全体像の 1 枚に使う。
DIAGRAM_LANES = "lanes"
#: 段を上から下へ 1 段ずつずらして積み、**どこまで届いたか** を段の横に置く図。
#: 流れ・枠・境界・レーンのどれでも書けないのは「同じ物差しの上で、届いた高さ
#: (深さ)が違う」ことで、流れは順番しか言えず、境界は 2 段しか作れない。
DIAGRAM_STEPS = "steps"
#: 升目に書いたとおりの位置へ、もの(部品・部屋・機器など)を置く図。
#: 流れ・枠・境界・レーン・階段はどれも **話の筋**(順番・包含・分かれ目・担当・
#: 深さ)を描くもので、「何がどこにあって、何と隣り合っているか」は書けない。
#: 文を箱に入れて並べても、位置は読む人が頭の中で組み立てることになる。
DIAGRAM_PLACEMENT = "placement"
DIAGRAM_SHAPES = (
    DIAGRAM_FLOW,
    DIAGRAM_FRAME,
    DIAGRAM_BOUNDARY,
    DIAGRAM_LANES,
    DIAGRAM_STEPS,
    DIAGRAM_PLACEMENT,
)
#: 境界図で「線をまたぐもの」を書くときに行の先頭に置く印。上から下へ渡るものが
#: `↓`、下から上へ戻るものが `↑`。シナリオに書く印と、資料に残す印と、
#: 案内文が読み取る印を同じにしておく(3 か所で別々の書き方をすると、
#: どこかがずれても画像を見るまで分からない)。
CROSSING_DOWN = "↓"
CROSSING_UP = "↑"
CROSSING_MARKS = (CROSSING_DOWN, CROSSING_UP)
#: 流れを **横に並べて** 描いた場合の目印。シナリオに書ける種類ではなく、
#: 描き方(場所に収まるかどうかで `layout.diagram_geometry` が決める)。
#: 図形の名前に残すのは、ナレーションの「上から順に」を「左から順に」に
#: 合わせるため。画面と言っていることが食い違うと、聞いている側だけが混乱する。
DIAGRAM_FLOW_ACROSS = "flow-across"
#: コードブロックの言語名として書く、図解の指定。日本語と英語のどちらでも書ける。
DIAGRAM_LANGS = {
    "流れ": DIAGRAM_FLOW,
    "flow": DIAGRAM_FLOW,
    "枠": DIAGRAM_FRAME,
    "frame": DIAGRAM_FRAME,
    "境界": DIAGRAM_BOUNDARY,
    "boundary": DIAGRAM_BOUNDARY,
    "レーン": DIAGRAM_LANES,
    "lanes": DIAGRAM_LANES,
    "階段": DIAGRAM_STEPS,
    "steps": DIAGRAM_STEPS,
    "配置": DIAGRAM_PLACEMENT,
    "placement": DIAGRAM_PLACEMENT,
}

#: 配置図で、升目を横に区切る印。全角と半角のどちらでも書ける。
PLACEMENT_SEPARATORS = ("|", "｜")
#: 配置図で、何も置かない升目。**空のまま(`| |`)は書けない** ことにしてある。
#: 区切りの数え違いと「わざと空けた」を、書いた時点で見分けるため。
PLACEMENT_EMPTY = (".", "．")
#: 配置図で、強調するものの名前の先頭に付ける印。どれを強調するかは書いた人が
#: 決める(描く側が語の意味から色を選ぶことはしない。札と同じ線)。
PLACEMENT_STRONG = ("*", "＊")
#: 配置図に書けない向きの印(向きは上下にしか置けない)。
PLACEMENT_SIDEWAYS = ("←", "→")

#: 階段図で「どこまで届いたか」を書くときに行の先頭に置く印。段は右下がりに
#: 並び、届いた印は段の右側に置くので、矢印は左を向く(印と絵の向きを揃える)。
REACH_MARK = "←"

#: レーン図で「誰が」と「何を」を分ける印。全角と半角のどちらでも書ける
#: (日本語で書いていると、コロンだけ半角にするのは間違えやすい)。
#: 最初に出てきた 1 つで切るので、項目の文にコロンがあっても構わない。
LANE_SEPARATORS = ("：", ":")


def diagram_shape_of(lang: str) -> Optional[str]:
    """コードブロックの言語名が図解の指定なら、その形を返す。"""
    return DIAGRAM_LANGS.get((lang or "").strip().lower())


@dataclass(frozen=True)
class Crossing:
    """境界図で、線をまたぐもの。`down` なら上から下へ、そうでなければ下から上へ。"""

    down: bool
    label: str

    @property
    def mark(self) -> str:
        return CROSSING_DOWN if self.down else CROSSING_UP


@dataclass(frozen=True)
class BoundaryParts:
    """境界図の中身を「線の上・線をまたぐもの・線の下」に分けたもの。"""

    upper: List[str]
    crossings: List["Crossing"]
    lower: List[str]
    #: またぐものの並びのあとに、さらに `↓` / `↑` の行があったか。線が 2 本
    #: あることになり、どこが境目か決まらない(呼び出し側がエラーにする)。
    split: bool = False


def crossing_of(line: str) -> Optional["Crossing"]:
    """行が `↓` / `↑` で始まるなら、線をまたぐものとして読み取る。"""
    text = line.strip()
    for mark in CROSSING_MARKS:
        if text.startswith(mark):
            return Crossing(down=mark == CROSSING_DOWN, label=text[len(mark) :].strip())
    return None


def boundary_parts(items: List[str]) -> "BoundaryParts":
    """境界図の行を、線の上・線をまたぐもの・線の下に分ける。

    区切りの記号を別に決めず、**`↓` / `↑` の行そのものが線の位置** になる。
    またぐものを書かない境界図は、枠を 2 つ置くのと変わらないので、
    ここでは分けられないまま返し、書き方の誤りとして呼び出し側が知らせる。
    """
    upper: List[str] = []
    crossings: List[Crossing] = []
    lower: List[str] = []
    split = False
    for line in items:
        crossing = crossing_of(line)
        if crossing is not None:
            if lower:
                split = True  # 線の下にもう 1 本引かれている
            else:
                crossings.append(crossing)
            continue
        (upper if not crossings else lower).append(line)
    return BoundaryParts(upper=upper, crossings=crossings, lower=lower, split=split)


@dataclass(frozen=True)
class LaneStep:
    """レーン図の 1 手順。`lane` が受け持つ側の名前、`text` が箱に出る文字。"""

    lane: str
    text: str


@dataclass(frozen=True)
class LaneReturn:
    """レーン図の戻り。`after` 番目の手順から、反対のレーンの先頭へ戻る。"""

    after: int
    label: str


@dataclass(frozen=True)
class LaneParts:
    """レーン図の行を、レーン名・手順・戻りに分けたもの。

    `bad` と `forward` は書き方の誤り(呼び出し側がエラーにする)。ここでは
    落とさずに残す —— どの行が悪いのかを、そのまま人に見せられるようにする。
    """

    lanes: List[str]
    steps: List["LaneStep"]
    returns: List["LaneReturn"]
    #: レーン名が書かれていない行(`:` が無い)。
    bad: List[str] = field(default_factory=list)
    #: `↓` で始まる行。レーン図では前へ進む矢印は自動で引かれるので、書けない。
    forward: List[str] = field(default_factory=list)


def lane_parts(items: List[str]) -> "LaneParts":
    """レーン図の行を読み取る。

    1 行は `レーン名: 手順` で、レーン名は **出てきた順** に左・右となる
    (どちらを左に置くかを別に書かせない。先に書いたものが先に読まれる)。

    `↑ ラベル` の行は戻りで、**その行の直前の手順から、反対のレーンの
    先頭の手順へ** 戻る矢印になる。位置が意味を持つのは境界図の `↓` `↑` と
    同じ考え方で、区切りの記号を別に決めない。
    """
    lanes: List[str] = []
    steps: List[LaneStep] = []
    returns: List[LaneReturn] = []
    bad: List[str] = []
    forward: List[str] = []
    for line in items:
        text = line.strip()
        if not text:
            continue
        if text.startswith(CROSSING_DOWN):
            forward.append(text)
            continue
        if text.startswith(CROSSING_UP):
            returns.append(LaneReturn(after=len(steps) - 1, label=text[len(CROSSING_UP) :].strip()))
            continue
        lane, body = _split_lane(text)
        if lane is None:
            bad.append(text)
            continue
        if lane not in lanes:
            lanes.append(lane)
        steps.append(LaneStep(lane=lane, text=body))
    return LaneParts(lanes=lanes, steps=steps, returns=returns, bad=bad, forward=forward)


def _split_lane(text: str) -> Tuple[Optional[str], str]:
    """`レーン名: 手順` を分ける。最初に見つかったコロンで切る。"""
    positions = [text.find(mark) for mark in LANE_SEPARATORS]
    positions = [i for i in positions if i > 0]
    if not positions:
        return None, text
    cut = min(positions)
    return text[:cut].strip(), text[cut + 1 :].strip()


def lane_index(parts: "LaneParts", lane: str) -> int:
    """そのレーンが左(0)か右(1)か。"""
    return parts.lanes.index(lane) if lane in parts.lanes else 0


@dataclass(frozen=True)
class StepLevel:
    """階段図の 1 段。`name` が段の名前(札)、`text` が段に出る文字。"""

    name: str
    text: str


@dataclass(frozen=True)
class StepReach:
    """階段図の到達点。`after` 番目の段まで届いた、という印。"""

    after: int
    label: str


@dataclass(frozen=True)
class StepParts:
    """階段図の行を、段と到達点に分けたもの。

    `bad` と `wrong` は書き方の誤り(呼び出し側がエラーにする)。レーン図と
    同じで、落とさずに残す —— どの行が悪いのかをそのまま人に見せられるように。
    """

    levels: List["StepLevel"]
    reaches: List["StepReach"]
    #: 段の名前が書かれていない行(`:` が無い)。
    bad: List[str] = field(default_factory=list)
    #: `↓` `↑` で始まる行。階段図では段のつながりは書いた順から決まるので、
    #: 境界図・レーン図の印は使えない。
    wrong: List[str] = field(default_factory=list)


def step_parts(items: List[str]) -> "StepParts":
    """階段図の行を、段と到達点に分ける。

    1 行は `段の名前: 中身` で、**書いた順に上から下へ** 1 段ずつ深くなる
    (レーン図の `レーン名: 手順` と同じ書き方・同じ区切り記号にしてある。
    覚えることを増やさないため)。

    `← ラベル` の行は到達点で、**その行の直前の段まで届いた** ことを表す。
    位置が意味を持つのは境界図の `↓` `↑`、レーン図の `↑` と同じ考え方で、
    どの段に付けるかを別に書かせない。
    """
    levels: List[StepLevel] = []
    reaches: List[StepReach] = []
    bad: List[str] = []
    wrong: List[str] = []
    for line in items:
        text = line.strip()
        if not text:
            continue
        if text.startswith(REACH_MARK):
            reaches.append(
                StepReach(after=len(levels) - 1, label=text[len(REACH_MARK) :].strip())
            )
            continue
        if text[0] in CROSSING_MARKS:
            wrong.append(text)
            continue
        name, body = _split_lane(text)
        if name is None:
            bad.append(text)
            continue
        levels.append(StepLevel(name=name, text=body))
    return StepParts(levels=levels, reaches=reaches, bad=bad, wrong=wrong)



@dataclass(frozen=True)
class PlacedPart:
    """配置図に置く 1 つのもの。升目の `row` 行 `col` 列から、`rows` x `cols` を占める。"""

    name: str
    row: int
    col: int
    rows: int = 1
    cols: int = 1
    strong: bool = False


@dataclass(frozen=True)
class PlacementParts:
    """配置図の行を、置くもの・升目の大きさ・向きの印に分けたもの。

    `ragged` `broken` `blank` `sideways` は書き方の誤り(呼び出し側がエラーにする)。
    ここでは落とさずに、描ける形に直して残す —— 記事入力では確かめずに描くので、
    誤りがあっても図が消えないようにする。
    """

    parts: List["PlacedPart"]
    rows: int
    cols: int
    #: 図の上・下が何の向きか(`↑ 前` なら上が前)。境界図と同じ印を使う。
    marks: List["Crossing"] = field(default_factory=list)
    #: 升目の数が、いちばん長い行と合っていない行。
    ragged: List[str] = field(default_factory=list)
    #: 同じ名前が隣り合っているのに、四角にならないもの(L 字など)。
    broken: List[str] = field(default_factory=list)
    #: 空のままの升目がある行(`.` と書かれていない)。
    blank: List[str] = field(default_factory=list)
    #: `←` `→` で始まる行。
    sideways: List[str] = field(default_factory=list)


def _split_cells(text: str) -> List[str]:
    for mark in PLACEMENT_SEPARATORS[1:]:
        text = text.replace(mark, PLACEMENT_SEPARATORS[0])
    return [cell.strip() for cell in text.split(PLACEMENT_SEPARATORS[0])]


def _strong_name(cell: str) -> Tuple[str, bool]:
    """升目の文字から、強調の印を外した名前と、印があったかどうかを返す。"""
    for mark in PLACEMENT_STRONG:
        if cell.startswith(mark):
            return cell[len(mark) :].strip(), True
    return cell, False


def placement_parts(items: List[str]) -> "PlacementParts":
    """配置図の行を読み取る。

    1 行が升目の 1 段で、`|` で横に区切る。**書いた位置が、そのまま置く位置** に
    なる(座標や「〜の右」を別に書かせない。見たままが図になる)。

    * **同じ名前が隣り合っていれば、1 つのもの** として、またがる升目いっぱいに
      描く。長いもの・大きいものは、名前を繰り返して書く。
    * 離れた場所に同じ名前があれば、別々のもの(左右に 1 本ずつある部品など)。
    * `.` は何も置かない升目。**隣り合うものは接して描かれる** ので、つながって
      いないものの間には `.` を置く。
    * `↑ ラベル` / `↓ ラベル` の行は升目ではなく、図の上・下がどちら向きかの印。
    """
    grid: List[List[str]] = []
    sources: List[str] = []
    marks: List[Crossing] = []
    sideways: List[str] = []
    blank: List[str] = []
    for line in items:
        text = line.strip()
        if not text:
            continue
        if text[0] in PLACEMENT_SIDEWAYS:
            sideways.append(text)
            continue
        mark = crossing_of(text)
        if mark is not None:
            marks.append(mark)
            continue
        cells = _split_cells(text)
        if any(not cell for cell in cells):
            blank.append(text)
        grid.append(["" if cell in PLACEMENT_EMPTY else cell for cell in cells])
        sources.append(text)

    cols = max((len(row) for row in grid), default=0)
    ragged = [src for src, row in zip(sources, grid) if len(row) != cols]
    for row in grid:
        row.extend([""] * (cols - len(row)))

    parts: List[PlacedPart] = []
    broken: List[str] = []
    seen = [[False] * cols for _ in grid]
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if not cell or seen[r][c]:
                continue
            name, _ = _strong_name(cell)
            if not name:
                seen[r][c] = True
                continue
            region = _region(grid, r, c, name)
            strong = any(_strong_name(grid[y][x])[1] for y, x in region)
            bottom = max(y for y, _ in region)
            right = max(x for _, x in region)
            if len(region) == (bottom - r + 1) * (right - c + 1) and all(x >= c for _, x in region):
                for y, x in region:
                    seen[y][x] = True
                parts.append(PlacedPart(name, r, c, bottom - r + 1, right - c + 1, strong))
            else:
                # 四角にならない。升目 1 つずつに分けて残す(図からは消さない)。
                if name not in broken:
                    broken.append(name)
                for y, x in sorted(region):
                    seen[y][x] = True
                    parts.append(PlacedPart(name, y, x, 1, 1, strong))
    parts.sort(key=lambda part: (part.row, part.col))
    return PlacementParts(
        parts=parts,
        rows=len(grid),
        cols=cols,
        marks=marks,
        ragged=ragged,
        broken=broken,
        blank=blank,
        sideways=sideways,
    )


def _region(grid: List[List[str]], row: int, col: int, name: str) -> List[Tuple[int, int]]:
    """`(row, col)` から、同じ名前で隣り合っている升目をたどって集める。"""
    found = {(row, col)}
    stack = [(row, col)]
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if (ny, nx) in found or not (0 <= ny < len(grid) and 0 <= nx < len(grid[ny])):
                continue
            if _strong_name(grid[ny][nx])[0] == name and grid[ny][nx]:
                found.add((ny, nx))
                stack.append((ny, nx))
    return sorted(found)


BULLET = "bullet"
NUMBER = "number"
QUOTE = "quote"
#: 記号を付けずに置く行。教材シナリオで、箇条書きではなく説明の文として
#: 書かれた段落に使う(記事入力では、段落も文ごとの箇条書きになる)。
PLAIN = "plain"


@dataclass
class Bullet:
    """本文スライドの 1 行。level は 0 起点のインデント段数。"""

    runs: List[Run]
    level: int = 0
    kind: str = BULLET
    number: Optional[str] = None

    @property
    def text(self) -> str:
        return runs_to_text(self.runs)


@dataclass
class Content:
    """画面に出す 1 つの中身。kind で、どのフィールドを使うかが決まる。

    1 枚に 1 つだけ置く場合は `Slide` がそのまま中身になり、複数を並べる場合は
    `Slide.parts` に並ぶ(`Slide` はこの `Content` に見出しとナレーションを
    足したもの)。描画側は中身の種類ごとに描くだけなので、1 枚に 1 つでも
    複数でも同じ処理で扱える。
    """

    kind: str
    bullets: List[Bullet] = field(default_factory=list)
    code: str = ""
    code_lang: str = ""
    table_header: List[str] = field(default_factory=list)
    table_rows: List[List[str]] = field(default_factory=list)
    image_path: Optional[str] = None
    image_alt: str = ""
    diagram_shape: str = ""
    diagram_items: List[str] = field(default_factory=list)

    def content_dict(self) -> dict:
        """中身のフィールドだけを辞書にする(種類・見出しは含めない)。"""
        data: dict = {}
        if self.bullets:
            data["bullets"] = [
                {"level": b.level, "kind": b.kind, "text": b.text} for b in self.bullets
            ]
        if self.code:
            data["code"] = self.code
            data["code_lang"] = self.code_lang
        if self.table_header or self.table_rows:
            data["table"] = {"header": self.table_header, "rows": self.table_rows}
        if self.image_path:
            data["image"] = {"path": self.image_path, "alt": self.image_alt}
        if self.diagram_items:
            data["diagram"] = {"shape": self.diagram_shape, "items": list(self.diagram_items)}
        return data

    def to_dict(self) -> dict:
        return {"kind": self.kind, **self.content_dict()}

    def as_slide(self, title: str = "", notes: str = "") -> "Slide":
        """この中身だけを置いた 1 枚にする。"""
        return Slide(
            kind=self.kind,
            title=title,
            bullets=self.bullets,
            code=self.code,
            code_lang=self.code_lang,
            table_header=self.table_header,
            table_rows=self.table_rows,
            image_path=self.image_path,
            image_alt=self.image_alt,
            diagram_shape=self.diagram_shape,
            diagram_items=list(self.diagram_items),
            notes=notes,
        )


@dataclass
class Slide(Content):
    title: str = ""
    subtitle: str = ""
    #: 短く添える札。サムネイルでは左上(教材名・回数など)、本文の画面では
    #: 見出しの行の右端に置く(その画面がどういう話なのかを示す目印)。
    label: str = ""
    notes: str = ""
    #: 1 枚に並べる中身(`KIND_CONTENT` のときだけ入る)。書かれた順に縦へ並ぶ。
    parts: List[Content] = field(default_factory=list)
    #: 直前のスライドから続いている表・コード(1 枚に収まらず分けたもの)。
    #: ナレーションで「表の続きです」と案内するために使う。
    continued: bool = False

    def to_dict(self) -> dict:
        """--dump-plan 用。スライド構成の確認・差分比較に使う。"""
        data = {"kind": self.kind, "title": self.title}
        if self.subtitle:
            data["subtitle"] = self.subtitle
        if self.label:
            data["label"] = self.label
        data.update(self.content_dict())
        if self.parts:
            data["parts"] = [part.to_dict() for part in self.parts]
        if self.continued:
            data["continued"] = True
        if self.notes:
            data["notes"] = self.notes
        return data


# ---------------------------------------------------------------------------
# 図形の名前(資料に残す目印)
# ---------------------------------------------------------------------------
#
# 表とコードは、画面には出ているが文字としては読み上げられない。ナレーション側
# (narration.py)が「何が映っているのか」を判断できるよう、描画側(renderer.py)は
# 図形の名前に種類を残す。名前は `種類[-continued][:言語]` の形にする。
#
#     code            コード
#     code:bash       bash のコード
#     table-continued 前のスライドから続いている表
#     footer          資料名・ページ番号(読み上げない飾り)
#     label           見出しの行の右端に置く札(読み上げない)

SHAPE_CODE = "code"
SHAPE_TABLE = "table"
#: 図解の外枠。図解は複数の図形でできているので、案内文を二重に出さないよう、
#: この名前を付けるのは外枠の 1 つだけにする(中の図形は SHAPE_DIAGRAM_ITEM)。
SHAPE_DIAGRAM = "diagram"
SHAPE_DIAGRAM_ITEM = "diagram-item"
#: 見た目のために置く文字(資料名・ページ番号)。画面には出るが、内容ではない
#: ので読み上げない。ナレーション側(narration.py)がこの名前で除外する。
SHAPE_FOOTER = "footer"
#: 見出しの行の右端に置く札。画面に出るが、ナレーションは読み上げない
#: (書いた人がナレーションで言う文字であって、機械が読み足すものではない)。
SHAPE_LABEL = "label"
_SHAPE_CONTINUED = "-continued"


def shape_name(kind: str, continued: bool = False, language: str = "") -> str:
    """描画側が図形に付ける名前を組み立てる。"""
    name = kind + (_SHAPE_CONTINUED if continued else "")
    language = (language or "").strip()
    return f"{name}:{language}" if language else name


def parse_shape_name(name: Optional[str]) -> Tuple[str, bool, str]:
    """図形の名前から「種類・続きかどうか・言語」を読み取る。"""
    base, _, language = (name or "").strip().lower().partition(":")
    continued = base.endswith(_SHAPE_CONTINUED)
    if continued:
        base = base[: -len(_SHAPE_CONTINUED)]
    return base, continued, language


@dataclass
class Deck:
    slides: List[Slide] = field(default_factory=list)
    title: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "slides": [s.to_dict() for s in self.slides],
            "warnings": self.warnings,
        }
