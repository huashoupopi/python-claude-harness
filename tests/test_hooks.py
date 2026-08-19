"""hook 短路契约。被测:examples/22_trunk.py 的 trigger_hook。"""


def test_trigger_hook_short_circuits(trunk, monkeypatch):
    evidence = []

    def hook_none(*args):
        return None

    def hook_block(*args):
        return "blocked!"

    def hook_after(*args):
        evidence.append("hook_after ran")
        return None

    monkeypatch.setitem(trunk.HOOKS, "TestEvent", [hook_none, hook_block, hook_after])
    result = trunk.trigger_hook("TestEvent")
    assert result == "blocked!"
    assert evidence == []


def test_trigger_hook_all_none(trunk, monkeypatch):
    evidence = []

    def hook_none(*args):
        evidence.append("hook_none ran")
        return None

    def hook_after(*args):
        evidence.append("hook_after ran")
        return None

    monkeypatch.setitem(trunk.HOOKS, "TestEvent", [hook_none, hook_after])
    result = trunk.trigger_hook("TestEvent")
    assert result is None
    assert evidence == ["hook_none ran", "hook_after ran"]
