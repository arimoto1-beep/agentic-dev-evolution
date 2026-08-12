"""外部コマンド(LibreOffice / PowerShell)を呼ぶときの共通処理。

このリポジトリの変換処理は外部ツールに任せている部分があり、失敗したときに
「何を実行して、何が返ってきたか」が分からないと原因を追えない。実行したコマンド
と出力をそのまま持ち回るための入れ物をここに置く。
"""

from __future__ import annotations

from typing import List, Optional


def format_command(command: List[str]) -> str:
    """ログ表示用にコマンドを 1 行で表す。そのまま貼り付けて再実行できる形にする。"""
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def decode_output(data: Optional[bytes]) -> str:
    """外部コマンドの出力を文字列にする(Windows の cp932 出力にも備える)。"""
    if not data:
        return ""
    for encoding in ("utf-8", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def indent_block(text: str) -> str:
    return "\n".join("  " + line for line in text.strip().splitlines())


class CommandError(RuntimeError):
    """外部コマンドの失敗。原因調査に必要な情報をすべて保持する。"""

    def __init__(
        self,
        reason: str,
        command: List[str],
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        hint: str = "",
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.hint = hint
        super().__init__(f"{reason}\n{self.details()}")

    def details(self) -> str:
        lines = ["実行したコマンド:", "  " + format_command(self.command)]
        if self.returncode is not None:
            lines.append(f"終了コード: {self.returncode}")
        if self.stdout.strip():
            lines.append("標準出力:\n" + indent_block(self.stdout))
        if self.stderr.strip():
            lines.append("標準エラー出力:\n" + indent_block(self.stderr))
        if not self.stdout.strip() and not self.stderr.strip() and self.hint:
            lines.append(self.hint)
        return "\n".join(lines)
