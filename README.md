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

Markdown 記事から、16:9 のプレゼンテーション資料(.pptx)と、動画制作で使うスライド画像・ナレーション音声を生成します。
動画化パイプラインのうち、「資料生成」「スライド画像化」「音声生成」にあたります。

```
                            +--[note2slides-images]--> slide_001.png ...
記事(.md) --[note2slides]--> 資料(.pptx)
                            +--[note2slides-audio]---> narration_001.wav ...
```

画像と音声は同じ番号(スライド番号)で対応します。`slide_001.png` に対応する音声が `narration_001.wav` です。

資料は Office Open XML なので、PowerPoint でも LibreOffice Impress でも開いて編集できます。

### セットアップ

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"         # macOS / Linux
```

外部ツールは、使う機能に応じて必要になります。どちらも無くても資料(.pptx)は生成できます。

| ツール | 必要な機能 | 無い場合 |
| --- | --- | --- |
| [LibreOffice](https://ja.libreoffice.org/) | スライド画像 | PDF を直接入力すれば不要 |
| [VOICEVOX](https://voicevox.hiroshiba.jp/) | ナレーション音声 | Windows 標準の音声合成に切り替わる(声の質は落ちます) |

### 使い方

```bash
# 記事 -> 資料
.venv/Scripts/python.exe -m note2slides.cli samples/sample_article.md -o build/sample.pptx

# 資料 -> スライド画像
.venv/Scripts/python.exe -m note2slides.images_cli build/sample.pptx -o build/slides

# 資料 -> ナレーション音声
.venv/Scripts/python.exe -m note2slides.audio_cli build/sample.pptx -o build/audio
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

## ナレーション音声への変換(note2slides-audio)

資料からスライド 1 枚ごとのナレーション音声(WAV)を書き出します。

```bash
.venv/Scripts/python.exe -m note2slides.audio_cli build/sample.pptx -o build/audio
```

```
build/audio/
  narration_001.wav   スライド 1 枚目のナレーション
  narration_002.wav
  ...
  narration.json      スライド番号・長さ・読み上げた文章・音量の一覧
```

* ファイル名の番号はスライド番号です。`slide_001.png` と `narration_001.wav` が対になります。
* 読み上げる文章が無いスライド(コードや表だけのスライドなど)も、番号がずれないように無音の WAV を出します。
* 全ファイルを **48000Hz / 16bit / モノラル** に揃えて出します。
* `narration.json` には 1 本ごとの長さが入っています。スライドを何秒映すかは、この長さから決められます。

### 音声合成の方式

| エンジン | 中身 | 用途 |
| --- | --- | --- |
| `voicevox` | [VOICEVOX](https://voicevox.hiroshiba.jp/) ENGINE | **公開する動画向け**。ニューラル音声合成で、長く聞いても違和感が少ない |
| `onecore` | Windows.Media.SpeechSynthesis | VOICEVOX が無い環境向け。16000Hz 固定 |
| `sapi` | System.Speech | 同上。出力形式を指定できる |

既定は `auto` で、この順に使えるものを選びます。どれも手元で動き、通信も従量課金もありません。

VOICEVOX は、起動していればそこに接続し、起動していなければ `run.exe` を探して起動します
(自分で起動した場合だけ、終了時に止めます)。開いている VOICEVOX アプリには触れません。

音声は「話者/スタイル」で指定します(`--voice "No.7/アナウンス"`)。
指定しない場合は、読み上げ向きの落ち着いた声を上から順に選びます。使える音声は `--list-voices` で確認できます。

> **公開時のクレジット**
> VOICEVOX で作った音声を含む動画を公開する場合、キャラクター名の表示(例: `VOICEVOX:No.7`)が必要です。
> 使った音声に対応する表記は `narration.json` の `credit` に入っています。

### 聞きやすさのために行っていること

「文章を音声にできる」ことと「動画のナレーションとして聞ける」ことは別なので、次の 3 つを合成の前後で行っています。

**1. 文ごとに合成して、間を置いてつなぐ**

1 枚分をまとめて合成すると、文の切れ目が詰まって早口に聞こえます。文・行の単位に分けて合成し、
間の長さをこちらで決めてからつなぎます。合成エンジンが付ける前後の余白は削ってから入れ直すので、
間の長さがスライドごとにばらつきません。

| オプション | 間の位置 | 既定 |
| --- | --- | --- |
| `--sentence-pause` | 文と文の間 | 0.35 秒 |
| `--line-pause` | 行(段落・箇条書きの項目)の間 | 0.6 秒 |
| `--lead-silence` | スライドが変わってから話し始めるまで | 0.3 秒 |
| `--tail-silence` | 話し終わってから次のスライドまで | 0.7 秒 |
| `--max-chars` | この長さを超える文は読点で区切る | 100 文字 |

**2. 読み上げに向かない文字を落とす**

| すること | 理由 |
| --- | --- |
| 全角英数などの表記をそろえる | `ＡＩ` と `AI` で読みが変わらないようにする |
| 行頭の箇条書き記号(`・` `-` `*` `#`)を落とす | 画面には出ているが、読み上げる文字ではない |
| 絵文字・矢印・罫線を落とす | 読み上げても音にならない |
| URL を落とす(`--read-urls` で読ませられる) | 1 文字ずつ読まれて意味が取れない |
| 句点が無い行に句点を補う | 無いと語尾が上がったまま次の行に続いて聞こえる |
| 読み方辞書(`--dict`)で置き換える | 固有名詞や英単語の読み間違いを直す |

句点を補う以外はすべて文字を減らすだけで、元の記事に無い内容は足しません。
実際に合成へ渡した文字列は `narration.json` の `reading` に、落とした・補った内容は `notes` に入ります。

読み方辞書は `{"表記": "よみ"}` の形の JSON です。

```json
{ "note": "ノート", "TTS": "ティーティーエス" }
```

**3. 全ファイルの音量を YouTube の基準にそろえる**

音量が上下すると、長時間聞いたときに疲れます。全ファイルを 1 本の動画として
[ITU-R BS.1770-4](https://www.itu.int/rec/R-REC-BS.1770/) で測り、**同じ補正値** を全ファイルにかけます
(ファイルごとにそろえると、静かなスライドだけが持ち上がって、スライドが変わるたびに音量が動いて聞こえます)。

既定の目標は **-16 LUFS** です。YouTube は約 -14 LUFS を基準に音量をそろえるので、
あとから BGM を足す余地を 2dB 残しています。

合成音声は平均に対してピークだけが 15dB ほど飛び出すため、目標まで持ち上げるとピークが振り切れます。
全体を下げると今度は音量が足りないので、**飛び出したところだけ** を滑らかに抑えます(`--peak-ceiling`、既定 -1.5 dBFS)。
実際にどうなったかは `narration.json` の `loudness` に入ります。

```json
"loudness": { "gain_db": 6.56, "measured_lufs": -22.56, "result_lufs": -16.21,
              "result_peak_dbfs": -1.5, "limited": true, "limit_db": 5.02 }
```

`--no-loudness` を付けると、この調整をせず合成したままの音量で出します。

### 主なオプション

| オプション | 説明 |
| --- | --- |
| `-o, --outdir` | 出力先(既定は入力と同じ場所の `<名前>_audio`) |
| `-f, --force` | 出力先に既に音声がある場合に上書きする(古い連番は削除する) |
| `--engine` | `auto`(既定)/ `voicevox` / `onecore` / `sapi` |
| `--voice` | 使う音声(voicevox は「話者/スタイル」) |
| `--speed` | 読み上げ速度の倍率(既定 1.0) |
| `--pitch` / `--intonation` | 声の高さ / 抑揚の強さ(voicevox のみ) |
| `--volume` | 音量(0-100、既定 100) |
| `--sample-rate` | 標本化周波数(既定 48000) |
| `--loudness` / `--no-loudness` / `--peak-ceiling` | 音量の目標と上限 |
| `--sentence-pause` ほか | 間の長さ(上表) |
| `--dict` / `--read-urls` / `--max-chars` | 読み方の調整(上表) |
| `--silence` | 読み上げる文章が無いスライドの長さ(秒、既定 2.0) |
| `--dump-script` / `--script-only` | ナレーション原稿を JSON で書き出す |
| `--list-voices` / `--check` | 使える音声と環境の状態を表示する |
| `--keep-work` | 合成に使った作業ファイル(文ごとの WAV と要求内容)を残す |
| `--voicevox-url` / `--voicevox-exe` | VOICEVOX ENGINE の場所(`VOICEVOX_URL` / `VOICEVOX_ENGINE_PATH` でも指定可) |
| `--no-voicevox-autostart` | VOICEVOX を自動起動せず、起動済みのものだけを使う |
| `--powershell` | PowerShell の場所(既定: 自動検出、環境変数 `POWERSHELL_PATH` でも指定可) |
| `--timeout` | 合成全体の待ち時間(秒、既定 600) |

### 何を読み上げるか

読み上げる文章は、資料に書かれている文字をそのまま使います。要約・言い換え・補足の生成は行いません。
スライドごとに、次の順で読み上げ元を選びます。選んだ結果は `narration.json` の `source` に入ります。

| source | 読み上げ元 |
| --- | --- |
| `notes` | 発表者ノート(note2slides が入れた記事の本文) |
| `body` | スライドのタイトルと本文(ノートが無い場合) |
| `title` | スライドのタイトルだけ(本文が無い場合) |
| `none` | 読み上げる文字が無い(無音になる) |

コードと表は読み上げません(そのまま読み上げても聞き取れないため)。ノートがあればノートを読みます。
タイトルに付く「（続き）」は画面上の目印なので、読み上げからは外します。

### 読み方を直したいとき

まず `narration.json` の `reading` を見て、実際に何が読み上げられたかを確認します。
語の読み方だけを直すなら読み方辞書が手軽です。

```bash
.venv/Scripts/python.exe -m note2slides.audio_cli build/sample.pptx -o build/audio -f --dict readings.json
```

文章そのものを変えたい場合は、原稿を書き出して直し、その原稿を入力にして合成します。
資料を作り直す必要はありません。

```bash
# 1. 原稿を書き出す
.venv/Scripts/python.exe -m note2slides.audio_cli build/sample.pptx -o build/audio --script-only

# 2. build/audio/script.json の text を編集する

# 3. 原稿から音声を作る
.venv/Scripts/python.exe -m note2slides.audio_cli build/audio/script.json -o build/audio -f
```

`index` はスライド番号です。1 からの連番になっていない原稿は、対応がずれるため受け付けません。

### 生成に失敗したとき

まず環境の状態を確認します。

```bash
.venv/Scripts/python.exe -m note2slides.audio_cli --check
```

失敗時のメッセージには、失敗したスライド番号・エンジンからのエラー(HTTP の応答内容を含む)・
再実行できるコマンドが載ります。合成に渡した内容と途中までの WAV は作業ディレクトリに残すので、
同じ要求を手元で送り直せます。

| 症状 | 確認すること |
| --- | --- |
| VOICEVOX が見つからない | インストール状況。`--voicevox-exe` か `VOICEVOX_ENGINE_PATH` で場所を指定する |
| VOICEVOX に接続できない | 別のポートで動いていないか。`--voicevox-url` か `VOICEVOX_URL` で指定する |
| 起動が時間切れになる | 初回は音声モデルの読み込みに時間がかかる。`--voicevox-startup-timeout` を延ばす |
| 日本語の音声が無いと言われる | Windows の設定 > 時刻と言語 > 言語と地域 から日本語の音声を追加する |
| PowerShell が見つからない | `--powershell` か環境変数 `POWERSHELL_PATH` で場所を指定する |
| 一部のスライドだけ失敗する | そのスライドの原稿(`--dump-script` で確認)。特殊な記号が含まれていないか |
| 時間切れになる | 原稿の量。`--timeout` を延ばす |
| 読み方が違う | `narration.json` の `reading` を確認し、`--dict` か原稿で直す(上記) |
| 音量が小さい・大きい | `narration.json` の `loudness`。`--loudness` で目標を変える |

終了コードは、成功 0 / 引数の誤り 2 / 出力先に既存 3 / 合成失敗 4 / 音声合成が使えない 5 です。

## 開発

```bash
.venv/Scripts/python.exe -m pytest                # テスト(外部ツールが無い環境では該当分をスキップ)
.venv/Scripts/python.exe -m pytest -m "not slow"  # LibreOffice や音声合成を使うテストを除く
```

外部ツールを使うテストは、そのツールが無ければスキップします(VOICEVOX が無い環境でも `pytest` は通ります)。

処理の流れです。

```
markdown_reader : Markdown -> Article(ブロック列)
planner         : Article  -> Deck(スライド構成)
renderer        : Deck     -> .pptx

soffice         : .pptx    -> PDF(LibreOffice をヘッドレスで呼ぶ)
slide_images    : PDF      -> slide_001.png ...(pypdfium2 で描画)

narration       : .pptx    -> ナレーション原稿(スライド 1 枚 = セリフ 1 本)
reading         : 原稿     -> 読み上げ単位 + 各単位の後ろに置く間
speech          : 合成エンジンの共通の型(どの方式でも同じ扱いにする)
voicevox        : 読み上げ単位 -> WAV(VOICEVOX ENGINE を HTTP で呼ぶ)
tts             : 読み上げ単位 -> WAV(speech.ps1 経由で Windows の音声合成を呼ぶ)
waveform        : WAV の読み書き・間を置いた連結・音量の測定(BS.1770)
audio           : 上記を束ねて narration_001.wav ... + narration.json
```

レイアウトの調整は `src/note2slides/style.py`、画像の出力条件は `ImageOptions`、
音声の出力条件は `AudioOptions`(間の取り方は `ReadingStyle`)に集約しています。
外部コマンドの呼び出しで共通する部分(実行したコマンドと出力を失敗時に残す)は `proc.py` にあります。

音声の質に効く判断は次の 3 か所に分かれています。どれを変えても他に影響しません。

| 変えたいこと | 見る場所 |
| --- | --- |
| 声そのもの(エンジン・話者) | `voicevox.py` の `PREFERRED_STYLES`、`tts.py` の `ENGINES` |
| 区切りと間、読み上げ用の整形 | `reading.py` |
| 音量・形式 | `waveform.py`、`audio.py` の `DEFAULT_*` |

`speech.ps1` は **UTF-8 BOM 付き** で保存してください。Windows PowerShell 5.1 は BOM の無いファイルを cp932 として読むため、
BOM を落とすとスクリプト内の日本語が壊れて構文エラーになります(`tests/test_tts.py` で確認しています)。
