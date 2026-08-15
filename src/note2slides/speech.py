"""音声合成の共通の型。

合成の方式は 1 つではない(手元の VOICEVOX、Windows 標準の音声合成)。
どれを使っても呼び出し側が同じように扱えるよう、やり取りする値と例外を
ここにまとめる。実装は次の 2 つ。

    voicevox.py  VOICEVOX ENGINE(ローカルの HTTP サーバ)
    tts.py       Windows 標準の音声合成(PowerShell 経由)

エンジンは次の呼び出しに答えられればよい。

    list_voices()                使える音声の一覧
    pick_voice(name, language)   使う音声を 1 つ決める
    default_format(sample_rate)  出力する WAV の形式
    honors_sample_rate()         標本化周波数を指定できるか
    synthesize(jobs, workdir...) まとめて合成し、1 件ごとの成否を返す
    close()                      後始末(起動したプロセスがあれば止める)

1 件(SpeechJob)はスライド 1 枚分で、区切りと間(SpeechPiece)を並べたもの。
エンジンは 1 件を続きとして読み上げ、間もその中に置いて 1 本の WAV にする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .proc import CommandError


class SpeechError(RuntimeError):
    """音声合成に関する失敗。"""


class SpeechNotAvailableError(SpeechError):
    """音声合成そのものが使えない場合(エンジンや音声が無い)。"""


class SynthesisError(CommandError, SpeechError):
    """合成の実行が失敗した場合。実行したコマンドと出力を保持する。"""

    def __init__(
        self,
        reason: str,
        command: List[str],
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(
            reason,
            command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            hint=hint or "エンジンは何も出力していません。",
        )


@dataclass(frozen=True)
class Voice:
    """使える音声 1 つ。VOICEVOX では「話者/スタイル」が 1 つの音声にあたる。"""

    name: str
    language: str = ""
    gender: str = ""
    engine: str = ""
    note: str = ""  # 用途の目安など(一覧表示にだけ使う)

    def speaks(self, language: str) -> bool:
        """指定した言語(`ja` や `ja-JP`)の音声かどうか。"""
        if not language:
            return True
        return self.language.lower().startswith(language.lower().split("-")[0])

    def describe(self) -> str:
        parts = [self.name]
        if self.language:
            parts.append(self.language)
        if self.gender:
            parts.append(self.gender)
        if self.note:
            parts.append(self.note)
        return " / ".join(parts)


@dataclass(frozen=True)
class AudioFormat:
    """WAV の形式。動画側で音声をつなぐときに揃っている必要がある。"""

    sample_rate: int
    channels: int = 1
    sample_width: int = 2  # バイト数(2 = 16bit)

    def describe(self) -> str:
        channels = "モノラル" if self.channels == 1 else f"{self.channels}ch"
        return f"{self.sample_rate}Hz / {self.sample_width * 8}bit / {channels}"

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width": self.sample_width,
        }


@dataclass(frozen=True)
class SpeechPiece:
    """続けて読み上げる 1 区切りと、その直後に置く間(秒)。

    区切りは間を置く位置を決めるためのもので、読み上げそのものは区切らない。
    """

    text: str
    pause_after: float = 0.0


@dataclass
class SpeechJob:
    """1 回分の読み上げ。1 枚のスライド分をまとめて 1 本の WAV にする。

    1 枚を区切って別々に合成すると、合成エンジンはそれぞれを文章の書き出しと
    して扱うため、区切りの先頭だけ音程が跳ね上がって聞こえる。区切りと間は
    pieces として渡し、続きとして読み上げさせる(間は読み上げの中に置く)。

    index は合成単位の通し番号。読み上げる文章が無いスライドには合成そのものが
    要らないため、スライド番号とは別に持つ(slide に対応するスライド番号が入る)。
    """

    index: int
    pieces: List[SpeechPiece]
    out_path: str
    slide: int = 0

    @property
    def text(self) -> str:
        """読み上げる文章(確認と失敗時の表示に使う)。"""
        return "".join(piece.text for piece in self.pieces)


@dataclass
class SpeechFailure:
    index: int  # SpeechJob.index
    message: str


@dataclass
class SynthesisReport:
    voice: str = ""
    failures: List[SpeechFailure] = field(default_factory=list)
    command: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    warnings: List[str] = field(default_factory=list)  # 合成はできたが伝えたいこと

    @property
    def ok(self) -> bool:
        return not self.failures
