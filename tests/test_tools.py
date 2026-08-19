"""工具注册与读写。被测:examples/22_trunk.py。"""

import pytest
from pydantic import BaseModel


@pytest.mark.parametrize(
    "tool_name", ["bash", "read_file", "write_file", "edit_file", "glob", "todo_write"]
)
def test_tools_are_importable(trunk, tool_name):
    entry = trunk.TOOL_REGISTRY[tool_name]
    assert entry.validator is not None
    assert issubclass(entry.validator, BaseModel)
    assert callable(entry.handler)


def test_write_read_roundtrip(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", tmp_path)
    out = sandbox.run_write("test.txt", "Hello, World!")
    content = sandbox.run_read("test.txt")
    assert content == "Hello, World!"
    assert str(len("Hello, World!")) in out
