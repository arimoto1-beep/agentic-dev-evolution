"""テスト全体の準備。

`pyproject.toml` で一時ファイルの置き場所を `build/pytest-tmp` に固定している。
`build/` はコミットしないため、複製した直後の作業環境には存在しない。pytest は
親ディレクトリまでは作らないので、先に作っておく(これが無いと、複製直後は
すべてのテストが「パスが見つかりません」で失敗する)。
"""

import os


def pytest_configure(config):
    basetemp = config.getoption("basetemp")
    if basetemp:
        os.makedirs(os.path.dirname(os.path.abspath(str(basetemp))), exist_ok=True)
