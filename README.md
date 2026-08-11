# Agentic Development Evolution Experiment

最小限のAI開発環境から実際のソフトウェア開発を始めたとき、開発環境そのものがどのように変化していくかを観察する実験です。

## 開発対象

note記事を入力として、eラーニング形式のYouTube通常動画を生成するシステムを開発します。

想定する最終成果物は、記事からプレゼンテーション資料、スライド画像、ナレーション音声を生成し、それらを組み合わせた動画です。

## 実験方針

完成した開発環境を最初から設計しません。

実際の開発を進める中で、現在の開発環境に不足や問題があるとAIが判断した場合は、開発環境そのものも改善対象とします。

改善の方法や構造はあらかじめ決めません。

特定の手法や構造の導入を前提とせず、実際の開発で必要と判断された改善を行います。

必要な仕組みが生まれなかった場合も実験結果として扱います。

---

## note2slides

Markdown 記事から、16:9 のプレゼンテーション資料(.pptx)を生成するコマンドです。
動画化パイプラインのうち、最初の「資料生成」にあたります。

生成物は Office Open XML なので、PowerPoint でも LibreOffice Impress でも開いて編集できます。

### セットアップ

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"         # macOS / Linux
```

### 使い方

```bash
.venv/Scripts/python.exe -m note2slides.cli samples/sample_article.md -o build/sample.pptx
```

主なオプション:

| オプション | 説明 |
| --- | --- |
| `-o, --output` | 出力先(既定は入力と同じ場所・同じ名前の .pptx) |
| `-f, --force` | 出力先が既にある場合に上書きする |
| `--title` | 表紙のタイトルを指定する |
| `--no-split-sentences` | 段落を文単位に分けず、1 段落を 1 項目にする |
| `--no-notes` | 発表者ノートに元の本文を入れない |
| `--font-latin` / `--font-ea` / `--font-mono` | 欧文 / 日本語 / 等幅フォント |
| `--dump-plan PATH` | スライド構成を JSON で書き出す(確認・差分比較用) |

### 記事の書き方とスライドの対応

| Markdown | スライド上の扱い |
| --- | --- |
| front matter の `title` / `subtitle` | 表紙 |
| `#` 見出し | 表紙と同じ内容なら表紙に統合、異なる場合は章扉 |
| `##` 以下の見出し | スライドの区切りとタイトル |
| 段落 | 文ごとの箇条書き(発表者ノートには段落のまま残す) |
| 箇条書き / 番号付きリスト | 箇条書き(入れ子は字下げ、番号は元の数字を維持) |
| 引用 | 縦線付きの項目 |
| コードブロック | コード用スライド |
| 表 | 表スライド |
| ローカル画像 | 画像スライド(参照先が無い場合は警告を出し、本文に `[画像] 代替テキスト` として残す) |
| `---` | 明示的なスライド区切り |

1 枚に収まらない場合は自動で次のスライドへ送り、タイトルに「（続き）」を付けます。

### 本文の扱いについて

スライドに載る文章は、記事の本文をそのまま並べ替えたものです。
要約・言い換え・補足の生成は行いません(記号の除去と、文単位への分割のみ行います)。
言い回しを変えたい場合は、生成後のファイルを直接編集するか、元の記事を修正してください。

### 開発

```bash
.venv/Scripts/python.exe -m pytest              # テスト(LibreOffice が無い環境では該当分をスキップ)
.venv/Scripts/python.exe -m pytest -m "not slow"  # LibreOffice を使うテストを除く

# 生成結果を画像で確認する(LibreOffice が必要)
.venv/Scripts/python.exe tools/preview_pptx.py build/sample.pptx --outdir build/preview
```

処理は 3 段階に分かれています。

```
markdown_reader : Markdown -> Article(ブロック列)
planner         : Article  -> Deck(スライド構成)
renderer        : Deck     -> .pptx
```

レイアウトの調整は `src/note2slides/style.py` に集約しています。
