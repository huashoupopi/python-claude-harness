import pytest
from pydantic import BaseModel


@pytest.mark.parametrize(
    "tool_name", ["bash", "read_file", "write_file", "edit_file", "glob", "todo_write"]
)
def test_tools_are_importable(tool_name):
    from harness import tools

    _, ArgModel, handler = tools.TOOL_REGISTRY[tool_name]
    assert issubclass(ArgModel, BaseModel)
    assert callable(handler)


def test_write_read_roundtrip(tmp_path, monkeypatch):
    import harness.tools as tools

    monkeypatch.setattr(tools, "WORKDIR", tmp_path)
    str1 = tools.run_write("test.txt", "Hello, World!")
    content = tools.run_read("test.txt")
    assert content == "Hello, World!"
    assert str(len("Hello, World!")) in str1
