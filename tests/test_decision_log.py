"""改善の記録(`docs/decisions/`)が、次の Run から使える形になっているかを確かめる。

この記録は、Run をまたいで「なぜそう決めたか」を引き継ぐために置いてある
(-> `docs/decisions/README.md`)。ただし、置いてあるだけでは引き継げない。
次の 3 つが崩れると、記録はあっても届かない。

1. **見つかること**  : 新しい Run が最初に読むファイルからたどり着けること。
   AI が読み込むのは `CLAUDE.md` -> `AGENTS.md` の連なりなので、そこから
   `docs/decisions/` へ参照が通っているかを見る。
2. **読めること**    : 判断・見送り・次の提案が、記録ごとに同じ場所にあること。
   見出しが揃っていないと、読む側は毎回全文を読むことになる。
3. **たどれること**  : 一覧と実体、記録どうしのリンクが食い違っていないこと。

中身が良いかどうかはここでは分からない。形が崩れていないことだけを見る。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "docs" / "decisions"
INDEX = DECISIONS / "README.md"

#: Run ごとの記録のファイル名(`gen25.md`)。他の .md は補足資料として扱う。
ENTRY_NAME = re.compile(r"^gen\d+\.md$")

#: どの記録にも要る見出し。順序も揃える(読む側が同じ順で読めるように)。
REQUIRED_HEADINGS = [
    "## この Run が見た状態",
    "## 判断",
    "## 見送り",
    "## 検証",
    "## 過去の判断の再評価",
    "## 次の Run への提案",
]

#: 見出しだけ置いて中身が無い記録を弾くための下限(全角で 30 字程度)。
MIN_SECTION_CHARS = 30


def read(path):
    return path.read_text(encoding="utf-8")


def entries():
    return sorted(p for p in DECISIONS.glob("*.md") if ENTRY_NAME.match(p.name))


def sections(text):
    """`## 見出し` で区切って {見出し行: 本文} にする。"""
    found = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line.strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}


def linked_paths(text, base):
    """Markdown リンクのうち、同じディレクトリの .md を指すものを返す。"""
    out = []
    for target in re.findall(r"\]\(([^)]+)\)", text):
        target = target.split("#")[0]
        if target and not target.startswith(("http://", "https://")) and target.endswith(".md"):
            out.append((target, (base.parent / target).resolve()))
    return out


def test_記録の置き場所がある():
    assert DECISIONS.is_dir(), "docs/decisions/ が無い"
    assert INDEX.is_file(), "docs/decisions/README.md(読み方と一覧)が無い"
    assert entries(), "Run ごとの記録が 1 件も無い"


def test_最初に読むファイルからたどり着ける():
    """CLAUDE.md から参照をたどって docs/decisions/ に届くか。

    新しい Run は、リポジトリを端から読むわけではない。自動で読み込まれる
    `CLAUDE.md` を起点に、そこから参照されているものだけを読む。この経路が
    切れていると、記録は「あるのに見つからない」状態になる。
    """
    goal = INDEX.resolve()
    seen = set()
    frontier = [ROOT / "CLAUDE.md"]
    hops = 0
    trail = []
    while frontier and hops < 4:
        nxt = []
        for path in frontier:
            path = path.resolve()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            trail.append(path.relative_to(ROOT).as_posix())
            text = read(path)
            # `@AGENTS.md` のような取り込み指定と、素のパス表記の両方を拾う。
            for token in re.findall(r"[@`(\s]([\w./-]+\.md)", text) + re.findall(
                r"`(docs/decisions/)`?", text
            ):
                cand = (ROOT / token).resolve()
                if cand.is_dir():
                    cand = cand / "README.md"
                if cand.is_file():
                    nxt.append(cand)
        if goal in seen:
            return
        frontier = nxt
        hops += 1
    assert goal in seen, f"CLAUDE.md から docs/decisions/ にたどり着けない(たどった先: {trail})"


def test_読み書きの手順が指示側に書いてある():
    """`AGENTS.md` から `docs/decisions/` への参照が生きているか。

    たどり着けること(上のテスト)と、読み書きすると分かることは別。経路は
    README 経由でも通るが、**手順** は AI が必ず読む指示側(`CLAUDE.md` から
    取り込まれる `AGENTS.md`)に無いと実行されない。ここが落ちると、記録は
    「読めるが、誰も更新しない」状態になる。
    """
    text = read(ROOT / "AGENTS.md")
    assert "docs/decisions/" in text, "AGENTS.md に記録の置き場所が書かれていない"
    assert "AGENTS.md" in read(ROOT / "CLAUDE.md"), "CLAUDE.md から AGENTS.md が取り込まれていない"


def test_記録の見出しが揃っている():
    for path in entries():
        text = read(path)
        found = sections(text)
        for heading in REQUIRED_HEADINGS:
            assert heading in found, f"{path.name}: 「{heading}」が無い"
        order = [h for h in found if h in REQUIRED_HEADINGS]
        assert order == REQUIRED_HEADINGS, f"{path.name}: 見出しの順が違う({order})"


def test_どの見出しにも中身がある():
    """空の見出しを残すと、次の Run は「書いていない」のか「無かった」のかを判断できない。

    見送りが無かった Run もあるはずなので、その場合は「なし」と理由を書く。
    """
    for path in entries():
        for heading, body in sections(read(path)).items():
            if heading not in REQUIRED_HEADINGS:
                continue
            assert len(body) >= MIN_SECTION_CHARS, f"{path.name}: 「{heading}」の中身が薄い"


def test_一覧と実体が食い違っていない():
    index_text = read(INDEX)
    listed = {p.name for _, p in linked_paths(index_text, INDEX)}
    for path in DECISIONS.glob("*.md"):
        if path.name == "README.md":
            continue
        assert path.name in listed, f"{path.name} が一覧に載っていない"
    for target, resolved in linked_paths(index_text, INDEX):
        assert resolved.is_file(), f"一覧のリンク先が無い: {target}"


def test_記録どうしのリンクが切れていない():
    for path in entries() + [p for p in DECISIONS.glob("*.md") if p.name != "README.md"]:
        for target, resolved in linked_paths(read(path), path):
            assert resolved.is_file(), f"{path.name}: リンク先が無い: {target}"


def test_過去の記録を書き換えずに評価すると書いてある():
    """この仕組みの肝は「過去の判断を指示として扱わないこと」。

    ここが README から落ちると、次の Run は記録を作業指示として実行してしまう。
    """
    index_text = read(INDEX)
    for phrase in ["提案", "却下"]:
        assert phrase in index_text, f"docs/decisions/README.md に「{phrase}」の説明が無い"
