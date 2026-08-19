"""todo 状态。被测:examples/22_trunk.py。"""


def test_todo_write_updates_state(trunk, monkeypatch):
    monkeypatch.setattr(trunk, "CURRENT_TODOS", [])
    result = trunk.run_todo_write([{"content": "任务A", "status": "pending"}])
    assert "1" in result
    assert len(trunk.CURRENT_TODOS) == 1
    assert trunk.CURRENT_TODOS[0]["content"] == "任务A"
