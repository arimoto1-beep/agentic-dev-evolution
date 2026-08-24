"""コマンドの出力を、実行環境によらず UTF-8 で出す。

Windows では、出力先がコンソールでないとき(パイプ・リダイレクト)、Python は
標準出力を locale の符号化で書く。日本語環境では cp932 になり、次の 2 つが起きる。

* **落ちる。** cp932 に無い文字を出そうとすると `UnicodeEncodeError` になる。
  note 記事の題や見出しに絵文字は珍しくないので、読み取りは成功しているのに、
  その題を表示するところでコマンドが終わってしまう。
* **読めない。** 出力を受け取る側(ログファイル・別のツール・このリポジトリを
  開発する AI)は UTF-8 として読むため、日本語がすべて化ける。せっかく
  失敗の理由を詳しく出しても、読む側に届かない。

どちらも変換の中身とは関係のない失敗なので、入口で UTF-8 に切り替える。
コンソールへ出す場合、Python は Windows でも UTF-8 を使うため何も変わらない。
"""

from __future__ import annotations

import codecs
import sys
from typing import Any


def use_utf8_output() -> None:
    """標準出力・標準エラー出力を UTF-8 にする。CLI の入口で最初に呼ぶ。

    引数の解析より前に呼ぶ。`--help` や引数の誤りの案内も日本語で出るため、
    そこも同じ扱いにする必要がある。
    """
    for stream in (sys.stdout, sys.stderr):
        _use_utf8(stream)


def _use_utf8(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        # 符号化を切り替えられない出力先(io.StringIO など)。触らない。
        return
    if _is_utf8(getattr(stream, "encoding", None)):
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError, LookupError):
        # UTF-8 にできない出力先もある。せめて落ちないようにする
        # (読みにくい形になっても、何も出ないよりはよい)。
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError, LookupError):
            pass


def _is_utf8(encoding: Any) -> bool:
    if not encoding:
        return False
    try:
        return codecs.lookup(encoding).name == "utf-8"
    except LookupError:
        return False
