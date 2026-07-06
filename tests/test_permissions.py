import pytest

from harness.tools import safe_path


def test_safe_path_blocks_escape():
    # 契约:越界路径必须抛 ValueError
    with pytest.raises(ValueError):  # 断言"这段代码必须炸"的写法
        safe_path("../../etc/passwd")
