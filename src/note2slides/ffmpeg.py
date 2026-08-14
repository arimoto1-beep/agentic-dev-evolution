"""ffmpeg の場所を探し、実行して、進捗と失敗の内容を受け取る。

動画の書き出しだけは自前で行わず ffmpeg に任せている。H.264 / AAC の MP4 は
再生環境がもっとも広く、YouTube もそのまま受け取れるが、規格に沿った符号化を
自前で書くのは現実的ではないため。

実行ファイルは次の順に探す。どれで動いたかは `--check` で確認できる。

    1. 明示指定(--ffmpeg / 環境変数 FFMPEG_PATH)
    2. PATH と、よくある導入先
    3. imageio-ffmpeg が同梱するもの(pip で入るため、別途の導入が要らない)

3 があるので、追加の導入なしに動く。1・2 を先に見るのは、利用者が意図して
入れた ffmpeg があるならそれを使う方が予想どおりになるため。

失敗したときは、実行したコマンドと ffmpeg の出力をそのまま例外に載せる
(`proc.CommandError`)。同じコマンドを手元で実行すれば同じ失敗を再現できる。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .proc import CommandError, decode_output as _decode

#: PATH と、よくある導入先。
_CANDIDATES = (
    "ffmpeg",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
)

INSTALL_HINT = (
    "`pip install -e .` で同梱の ffmpeg(imageio-ffmpeg)が入ります。"
    "自分で入れた ffmpeg を使う場合は、--ffmpeg または環境変数 FFMPEG_PATH で"
    "実行ファイルの場所を指定してください。"
)

#: 進捗の行に出る経過時間。`out_time=00:01:23.456000`
_OUT_TIME = re.compile(r"^out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)$")
_FRAME = re.compile(r"^frame=(\d+)$")


class FfmpegError(RuntimeError):
    """ffmpeg の呼び出しに関する失敗。"""


class FfmpegNotFoundError(FfmpegError):
    def __init__(self, tried: Optional[List[str]] = None) -> None:
        detail = ""
        if tried:
            detail = "\n探した場所:\n" + "\n".join(f"  {p}" for p in tried)
        super().__init__(f"ffmpeg が見つかりません。{INSTALL_HINT}{detail}")


class FfmpegRunError(CommandError, FfmpegError):
    """ffmpeg の実行が失敗した場合。原因調査に必要な情報をすべて保持する。"""


@dataclass
class RunReport:
    """ffmpeg が最後に報告した状態。"""

    command: List[str]
    returncode: int = 0
    stderr: str = ""
    out_time: float = 0.0  # 書き出した動画の長さ(秒)
    frames: int = 0


# ---------------------------------------------------------------------------
# 場所の解決
# ---------------------------------------------------------------------------


def bundled_ffmpeg() -> Optional[str]:
    """imageio-ffmpeg が同梱する実行ファイル。入っていなければ None。"""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - 同梱物が壊れている環境向け
        return None
    return path if path and os.path.isfile(path) else None


def candidate_paths(explicit: Optional[str] = None) -> List[str]:
    """探索対象のパスを優先順に返す。

    明示指定があるときは自動検出に戻らない。指定を取り違えたまま別の ffmpeg で
    書き出され、原因が分からなくなるのを避けるため。
    """
    if explicit:
        return [explicit]
    paths = [os.environ.get("FFMPEG_PATH", ""), *_CANDIDATES, bundled_ffmpeg() or ""]
    return [p for p in paths if p]


def find_ffmpeg(explicit: Optional[str] = None) -> Optional[str]:
    """ffmpeg の実行ファイルを探す。見つからなければ None。"""
    for candidate in candidate_paths(explicit):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        found = shutil.which(candidate)
        if found:
            return found
    return None


def require_ffmpeg(explicit: Optional[str] = None) -> str:
    found = find_ffmpeg(explicit)
    if not found:
        raise FfmpegNotFoundError(candidate_paths(explicit))
    return found


def get_version(ffmpeg: str, timeout: float = 60) -> str:
    """`ffmpeg -version` の 1 行目を返す。取得できなければ空文字。"""
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"], capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = _decode(completed.stdout).strip().splitlines()
    return first[0] if first else ""


def has_encoder(ffmpeg: str, name: str, timeout: float = 60) -> bool:
    """符号化器(libx264 など)が使えるビルドかどうか。

    ffmpeg には符号化器を絞ったビルドもあり、その場合は書き出しの直前まで
    進んでから失敗する。`--check` で先に気付けるようにする。
    """
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return False
    pattern = re.compile(r"^\s*\S+\s+" + re.escape(name) + r"\s", re.MULTILINE)
    return bool(pattern.search(_decode(completed.stdout)))


def is_bundled(path: str) -> bool:
    bundled = bundled_ffmpeg()
    return bool(bundled) and os.path.normcase(os.path.abspath(bundled)) == os.path.normcase(path)


def describe(path: str) -> str:
    return f"{path}(同梱の imageio-ffmpeg)" if is_bundled(path) else path


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def run(
    command: Sequence[str],
    timeout: float = 1800,
    on_progress: Optional[Callable[[float], None]] = None,
) -> RunReport:
    """ffmpeg を実行し、進捗を読みながら終わるのを待つ。

    `-progress pipe:1` を付けて呼ぶ前提で、標準出力に流れる進捗を読み取り、
    `on_progress` に「いま何秒目まで書き出したか」を渡す。長い動画では数分
    かかるため、何も表示されないまま待たせない。

    標準エラー出力(失敗の理由が出る)は別のスレッドで最後まで読み切る。
    読まずに放っておくと、出力が溜まった時点で ffmpeg が書き込めずに止まる。
    """
    command = list(command)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise FfmpegRunError(f"ffmpeg を起動できませんでした: {exc}", command) from exc

    captured: List[bytes] = []
    drain = threading.Thread(target=lambda: captured.append(process.stderr.read()), daemon=True)
    drain.start()

    timed_out = threading.Event()

    def give_up() -> None:
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(timeout, give_up)
    watchdog.start()

    report = RunReport(command=command)
    try:
        with process:
            for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                matched = _OUT_TIME.match(line)
                if matched:
                    hours, minutes, seconds = matched.groups()
                    report.out_time = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                    if on_progress:
                        on_progress(report.out_time)
                    continue
                matched = _FRAME.match(line)
                if matched:
                    report.frames = int(matched.group(1))
            report.returncode = process.wait()
            drain.join(timeout=10)
    finally:
        watchdog.cancel()

    report.stderr = _decode(captured[0] if captured else b"")
    if timed_out.is_set():
        raise FfmpegRunError(
            f"ffmpeg が {timeout:g} 秒以内に終了しませんでした。"
            "--timeout を延ばすか、スライドの枚数と長さを確認してください。",
            command,
            stdout=f"{report.out_time:.1f} 秒目まで書き出していました。",
            stderr=report.stderr,
        )
    if report.returncode != 0:
        raise FfmpegRunError(
            "ffmpeg が動画を書き出せませんでした。",
            command,
            returncode=report.returncode,
            stderr=report.stderr,
        )
    return report
