"""コマンドの出力が、環境によらず UTF-8 で出ることを確かめる。

Windows で出力をパイプやファイルに渡すと、Python は標準出力を locale の符号化
(日本語環境では cp932)で書く。この状態で cp932 に無い文字を出そうとすると
`UnicodeEncodeError` になり、変換そのものは成功しているのにコマンドが落ちる。
落ちなくても、出力を受け取る側は UTF-8 として読むため日本語が化ける。

ここでは `PYTHONIOENCODING=cp932` で同じ状況を作る。この指定は OS によらず
効くので、Windows 以外でも同じ失敗を再現できる。
"""

import glob
import io
import os
import subprocess
import sys

import pytest

from note2slides.console import use_utf8_output

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

#: 入口として公開しているコマンド。ファイル名から拾うので、
#: 新しいコマンドを足したときも、ここを直さずに同じ確認がかかる。
MODULES = [
    "note2slides." + os.path.basename(path)[:-3]
    for path in sorted(glob.glob(os.path.join(SRC, "note2slides", "*cli.py")))
]


def run(args, cwd=None):
    """出力先が cp932 になる状況で、コマンドを子プロセスとして動かす。"""
    env = dict(os.environ, PYTHONIOENCODING="cp932", PYTHONPATH=SRC)
    return subprocess.run(
        [sys.executable, "-m", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# 出力の切り替え
# ---------------------------------------------------------------------------


def wrapper(encoding):
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


class Stream:
    """符号化の名前だけを決められる出力先(TextIOWrapper の encoding は書き換えられない)。"""

    def __init__(self, encoding):
        self.encoding = encoding
        self.calls = []

    def reconfigure(self, **kw):
        self.calls.append(kw)
        if "encoding" in kw:
            self.encoding = kw["encoding"]


def test_switches_a_cp932_stream_to_utf8(monkeypatch):
    out, err = wrapper("cp932"), wrapper("cp932")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    use_utf8_output()

    assert out.encoding == "utf-8" and err.encoding == "utf-8"


def test_a_utf8_stream_is_left_alone(monkeypatch):
    """既に UTF-8 なら触らない(コンソールへの出力はこちら)。"""
    calls = []
    out = wrapper("utf-8")
    monkeypatch.setattr(out, "reconfigure", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", wrapper("utf-8"))

    use_utf8_output()

    assert calls == []


@pytest.mark.parametrize("name", ["UTF-8", "utf8", "UTF8"])
def test_utf8_is_recognized_by_any_of_its_names(monkeypatch, name):
    out = Stream(name)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", wrapper("utf-8"))

    use_utf8_output()

    assert out.calls == []


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch):
    """差し替えられた出力先(テストの捕捉など)でも落ちない。"""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    use_utf8_output()  # 例外が出ないこと


def test_falls_back_to_not_failing_when_the_encoding_cannot_be_changed(monkeypatch):
    """符号化を変えられない出力先でも、せめて落ちないようにする。"""
    calls = []

    def reconfigure(**kw):
        if "encoding" in kw:
            raise ValueError("この出力先は符号化を変えられません")
        calls.append(kw)

    out = wrapper("cp932")
    monkeypatch.setattr(out, "reconfigure", reconfigure)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", wrapper("cp932"))

    use_utf8_output()

    assert calls == [{"errors": "backslashreplace"}]


def test_gives_up_quietly_when_nothing_can_be_changed(monkeypatch):
    def reconfigure(**kw):
        raise ValueError("変えられません")

    out = wrapper("cp932")
    monkeypatch.setattr(out, "reconfigure", reconfigure)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", wrapper("cp932"))

    use_utf8_output()  # 例外が出ないこと


def test_an_unknown_encoding_name_is_treated_as_not_utf8(monkeypatch):
    out = Stream("この名前の符号化は無い")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", wrapper("cp932"))

    use_utf8_output()

    assert out.encoding == "utf-8"


# ---------------------------------------------------------------------------
# 入口のコマンド
# ---------------------------------------------------------------------------


def test_the_commands_were_found():
    """一覧が空だと、下の確認が 0 件で素通りする(置き場所を変えたときに気づけるように)。"""
    assert MODULES


@pytest.mark.parametrize("module", MODULES)
def test_help_is_readable_as_utf8(module):
    """`--help` の日本語が、パイプ越しでも UTF-8 で読めること。

    引数の解析より前に切り替えていないと、ここが化ける。
    """
    done = run([module, "--help"])

    assert done.returncode == 0
    assert "既定" in done.stdout.decode("utf-8")


def test_a_title_outside_cp932_does_not_break_the_command(tmp_path):
    """cp932 に無い文字(絵文字)が題にあっても、資料の生成は成功する。

    note 記事の題や見出しに絵文字は珍しくない。切り替えが無いと、資料は
    問題なく作れるのに、題を表示するところで UnicodeEncodeError で落ちる。
    """
    source = tmp_path / "scenario.md"
    source.write_text(
        "---\ntype: scenario\ntitle: 🎬絵文字の入った題\n---\n\n"
        "## 表紙\n\n### 設定\n\n- レイアウト: 表紙\n\n"
        "### 画面\n\nテスト\n\n### ナレーション\n\nテストです。\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.pptx"

    done = run(["note2slides.cli", str(source), "-o", str(output)])

    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    assert "🎬絵文字の入った題" in done.stdout.decode("utf-8")
    assert output.is_file()


def test_an_error_message_is_readable_as_utf8(tmp_path):
    """失敗したときの案内も同じ扱いにする(読めないと原因が分からない)。"""
    done = run(["note2slides.cli", str(tmp_path / "無い.md")])

    assert done.returncode == 2
    assert "入力ファイルが見つかりません" in done.stderr.decode("utf-8")


# ---------------------------------------------------------------------------
# テストの実行そのもの
# ---------------------------------------------------------------------------


def test_a_failing_test_is_readable_as_utf8(tmp_path):
    """pytest 自身の出力も UTF-8 で読めること(`tests/conftest.py` が切り替える)。

    このリポジトリのテストは失敗の理由を日本語で書いている。パイプ越しに化けると、
    失敗を見ても何が起きたのか分からない。切り替えは pytest が出力先を決めたあとに
    効かせる必要があるため、実際に pytest を動かして確かめる。

    本物の `tests/conftest.py` の `pytest_configure` をそのまま使う(写しを確かめても、
    本物から呼び出しが消えたことに気づけない)。
    """
    real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
    (tmp_path / "conftest.py").write_text(
        f"""
import runpy, sys
sys.path.insert(0, {SRC!r})
pytest_configure = runpy.run_path({real!r})["pytest_configure"]
""",
        encoding="utf-8",
    )
    (tmp_path / "test_failure.py").write_text(
        """
def test_x():
    assert False, "日本語の失敗メッセージ"
""",
        encoding="utf-8",
    )

    done = run(
        ["pytest", "test_failure.py", "-q", "-p", "no:cacheprovider", "--basetemp", "tmp"],
        cwd=str(tmp_path),
    )

    assert done.returncode == 1
    assert "日本語の失敗メッセージ" in done.stdout.decode("utf-8")
