"""テスト全体の準備。

`pyproject.toml` で一時ファイルの置き場所を `build/pytest-tmp` に固定している。
`build/` はコミットしないため、複製した直後の作業環境には存在しない。pytest は
親ディレクトリまでは作らないので、先に作っておく(これが無いと、複製直後は
すべてのテストが「パスが見つかりません」で失敗する)。

あわせて、pytest 自身の出力も UTF-8 にする。このリポジトリのテストは失敗の理由を
日本語で書いているが、Windows で出力をパイプやファイルに渡すと cp932 で書かれる。
受け取る側は UTF-8 として読むため、肝心の失敗メッセージが化けて読めない。
"""

import os

from note2slides.console import use_utf8_output


def pytest_configure(config):
    use_utf8_output()
    basetemp = config.getoption("basetemp")
    if basetemp:
        os.makedirs(os.path.dirname(os.path.abspath(str(basetemp))), exist_ok=True)
