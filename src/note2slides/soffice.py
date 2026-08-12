"""LibreOffice(soffice)を使って資料を PDF に変換する。

PowerPoint を必須にしないため、変換にはヘッドレスの LibreOffice を使う。PDF を
経由することで、スライドの見た目(フォント・図形・レイアウト)をベクタのまま
受け渡せる。

LibreOffice は失敗しても終了コード 0 を返すことがあるため、成否は出力ファイルの
有無で判定し、失敗時は実行したコマンドと出力を例外に載せる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from .proc import CommandError, decode_output as _decode

# soffice の探索順。SOFFICE_PATH で明示指定できる。
_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "soffice",
    "libreoffice",
)

_INSTALL_HINT = (
    "LibreOffice をインストールするか、実行ファイルの場所を "
    "--soffice または環境変数 SOFFICE_PATH で指定してください。"
)


class SofficeError(RuntimeError):
    """LibreOffice の呼び出しに関する失敗。"""


class SofficeNotFoundError(SofficeError):
    def __init__(self, tried: Optional[List[str]] = None) -> None:
        detail = ""
        if tried:
            detail = "\n探した場所:\n" + "\n".join(f"  {p}" for p in tried)
        super().__init__(f"LibreOffice(soffice)が見つかりません。{_INSTALL_HINT}{detail}")


class SofficeConversionError(CommandError, SofficeError):
    """変換そのものが失敗した場合。原因調査に必要な情報をすべて保持する。"""

    def __init__(
        self,
        reason: str,
        command: List[str],
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            reason,
            command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            hint=(
                "LibreOffice は何も出力していません。"
                "他の LibreOffice が起動中だと変換が無視されることがあります。"
            ),
        )


def candidate_paths(explicit: Optional[str] = None) -> List[str]:
    """探索対象のパスを優先順に返す。

    明示指定があるときは自動検出に戻らない。指定を取り違えたまま別の
    LibreOffice で変換され、原因が分からなくなるのを避けるため。
    """
    if explicit:
        return [explicit]
    paths = [os.environ.get("SOFFICE_PATH", ""), *_CANDIDATES]
    return [p for p in paths if p]


def find_soffice(explicit: Optional[str] = None) -> Optional[str]:
    """soffice の実行ファイルを探す。見つからなければ None。"""
    for candidate in candidate_paths(explicit):
        if os.path.isfile(candidate):
            return console_variant(os.path.abspath(candidate))
        found = shutil.which(candidate)
        if found:
            return console_variant(found)
    return None


def console_variant(path: str) -> str:
    """Windows では soffice.com を優先する。

    soffice.exe は GUI 用で、標準出力・標準エラー出力に何も返さない。失敗した
    理由を読めるように、同じ場所にある soffice.com(コンソール版)を使う。
    """
    base, ext = os.path.splitext(path)
    if ext.lower() == ".exe":
        console = base + ".com"
        if os.path.isfile(console):
            return console
    return path


def require_soffice(explicit: Optional[str] = None) -> str:
    soffice = find_soffice(explicit)
    if not soffice:
        raise SofficeNotFoundError(candidate_paths(explicit))
    return soffice


def get_version(soffice: str, timeout: float = 60) -> str:
    """`soffice --version` の出力を返す。取得できなければ空文字。"""
    try:
        completed = subprocess.run(
            [soffice, "--version"], capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _decode(completed.stdout).strip()


def build_command(soffice: str, source: str, outdir: str, profile_dir: str) -> List[str]:
    """PDF 変換のコマンドを組み立てる。

    起動中の LibreOffice に変換要求が吸われて何も起きない事故を避けるため、
    専用のユーザープロファイル(UserInstallation)を指定する。
    """
    profile_url = "file:///" + os.path.abspath(profile_dir).replace(os.sep, "/").lstrip("/")
    return [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_url}",
        "--convert-to",
        "pdf",
        "--outdir",
        os.path.abspath(outdir),
        os.path.abspath(source),
    ]


def convert_to_pdf(
    source: str,
    outdir: str,
    soffice: Optional[str] = None,
    timeout: float = 180,
) -> str:
    """資料(.pptx など)を PDF に変換し、生成された PDF のパスを返す。"""
    executable = require_soffice(soffice)
    os.makedirs(outdir, exist_ok=True)

    # プロファイルは変換ごとに使い捨てにする(壊れたプロファイルの持ち越しを防ぐ)。
    with tempfile.TemporaryDirectory(prefix="note2slides-lo-") as profile_dir:
        command = build_command(executable, source, outdir, profile_dir)
        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SofficeConversionError(
                f"LibreOffice が {timeout:g} 秒以内に終了しませんでした。",
                command,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
            ) from exc
        except OSError as exc:
            raise SofficeConversionError(
                f"LibreOffice を起動できませんでした: {exc}", command
            ) from exc

        stdout = _decode(completed.stdout)
        stderr = _decode(completed.stderr)
        pdf_path = os.path.join(
            os.path.abspath(outdir),
            os.path.splitext(os.path.basename(source))[0] + ".pdf",
        )
        if not os.path.isfile(pdf_path):
            raise SofficeConversionError(
                f"LibreOffice が PDF を出力しませんでした: {source}",
                command,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        if os.path.getsize(pdf_path) == 0:
            raise SofficeConversionError(
                f"LibreOffice が出力した PDF が空です: {pdf_path}",
                command,
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return pdf_path
