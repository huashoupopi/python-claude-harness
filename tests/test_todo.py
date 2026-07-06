import pytest


@pytest.fixture
def clean_todos():
    """每个用到它的测试跑之前，先清空全局 todo 状态；测完再清一次"""
    import harness.tools as tools

    tools.CURRENT_TODOS = []
    yield  # yield 之前 = 测试前的准备；之后 = 测试后的打扫
    tools.CURRENT_TODOS = []


def test_todo_write_updates_state(clean_todos):
    import harness.tools as tools

    result = tools.run_todo_write([{"content": "任务A", "status": "pending"}])
    assert "1" in result
    assert len(tools.CURRENT_TODOS) == 1
    assert tools.CURRENT_TODOS[0]["content"] == "任务A"
