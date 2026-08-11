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

Markdown 記事から、16:9 のプレゼンテーション資料(.pptx)と、動画制作で使うスライド画像を生成します。
動画化パイプラインのうち、「資料生成」と「スライド画像化」にあたります。

```
記事(.md) --[note2slides]--> 資料(.pptx) --[note2slides-images]--> slide_001.png ...
```

資料は Office Open XML なので、PowerPoint でも LibreOffice Impress でも開いて編集できます。

### セットアップ

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"         # macOS / Linux
```

### 使い方

```bash
# 記事 -> 資料
.venv/Scripts/python.exe -m note2slides.cli samples/sample_article.md -o build/sample.pptx

# 資料 -> スライド画像
.venv/Scripts/python.exe -m note2slides.images_cli build/sample.pptx -o build/slides
```

資料生成(note2slides)の主なオプション:

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

## スライド画像への変換(note2slides-images)

資料の各スライドを、1 枚ずつ画像として書き出します。後続の動画生成はこの画像を並べて使います。

```bash
.venv/Scripts/python.exe -m note2slides.images_cli build/sample.pptx -o build/slides
```

```
build/slides/
  slide_001.png   1920x1080 / RGB(アルファなし)
  slide_002.png
  ...
  slides.json     スライドの順番と枚数の一覧
```

* ファイル名は 1 起点の連番なので、辞書順とスライド順が一致します。
* 全スライドが同じ画素数で出ます(動画のフレームサイズをそろえるため)。
* 元が 16:9 でない資料は、引き伸ばさずに背景色の余白を足して合わせます。
* ffmpeg から使う場合は `slide_%03d.png` で読み込めます。

変換は **LibreOffice(ヘッドレス)で PDF にしてから、PDF を目的の解像度で描画** します。
PowerPoint は不要です。PDF を経由するので、拡大しても文字や図形が劣化しません。
資料の代わりに PDF を直接入力することもできます(その場合 LibreOffice は不要)。

主なオプション:

| オプション | 説明 |
| --- | --- |
| `-o, --outdir` | 出力先(既定は入力と同じ場所の `<名前>_slides`) |
| `-f, --force` | 出力先に既に画像がある場合に上書きする(古い連番は削除する) |
| `--width` / `--height` | 画像サイズ(既定は 1920、高さは 16:9 で自動計算) |
| `--format` | `png`(既定)または `jpg` |
| `--prefix` / `--digits` | ファイル名の接頭辞と連番の桁数 |
| `--keep-pdf` | 中間生成物の PDF を残す |
| `--soffice` | LibreOffice の場所(既定: 自動検出、環境変数 `SOFFICE_PATH` でも指定可) |
| `--timeout` | LibreOffice の待ち時間(秒、既定 180) |
| `--check` | 変換に必要な外部ツールの状態だけを表示する |

### 変換に失敗したとき

まず外部ツールの状態を確認します。

```bash
.venv/Scripts/python.exe -m note2slides.images_cli --check
```

失敗時のメッセージには、実行した LibreOffice のコマンド・終了コード・出力がそのまま載ります。
同じコマンドを手元で実行すれば、同じ失敗を再現できます。

| 症状 | 確認すること |
| --- | --- |
| LibreOffice が見つからない | インストール状況。`--soffice` か `SOFFICE_PATH` でパスを指定する |
| LibreOffice が何も出力しない | 他の LibreOffice が起動中でないか(変換専用のプロファイルを使いますが、環境によっては影響します) |
| 時間切れになる | 資料の枚数や画像の重さ。`--timeout` を延ばす |
| 枚数が合わない(警告が出る) | 非表示スライドの有無 |
| 文字化け・フォントが違う | 変換した環境にそのフォントが入っているか(LibreOffice は代替フォントで描画します) |

終了コードは、成功 0 / 引数の誤り 2 / 出力先に既存 3 / 変換失敗 4 / LibreOffice なし 5 です。

## 開発

```bash
.venv/Scripts/python.exe -m pytest                # テスト(LibreOffice が無い環境では該当分をスキップ)
.venv/Scripts/python.exe -m pytest -m "not slow"  # LibreOffice を使うテストを除く
```

処理の流れです。

```
markdown_reader : Markdown -> Article(ブロック列)
planner         : Article  -> Deck(スライド構成)
renderer        : Deck     -> .pptx
soffice         : .pptx    -> PDF(LibreOffice をヘッドレスで呼ぶ)
slide_images    : PDF      -> slide_001.png ...(pypdfium2 で描画)
```

レイアウトの調整は `src/note2slides/style.py`、画像の出力条件は `ImageOptions` に集約しています。
