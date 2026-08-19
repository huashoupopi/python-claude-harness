"""路径闸。被测:examples/22_trunk.py 的 safe_path。"""

import pytest


def test_safe_path_blocks_escape(trunk):
    with pytest.raises(ValueError):
        trunk.safe_path("../../etc/passwd")
