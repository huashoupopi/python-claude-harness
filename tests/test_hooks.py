import pytest  # noqa: F401

from harness.hooks import HOOKS, trigger_hook


def test_trigger_hook_short_circuits(monkeypatch):
    evidence = []  # 证据列表:哪个 hook 被执行了,就往里记一笔

    def hook_none(*args):  # 假 hook ①:不拦(返回 None)
        return None

    def hook_block(*args):  # 假 hook ②:拦!(返回非 None)
        return "blocked!"

    def hook_after(*args):  # 假 hook ③:一旦被执行就留下证据
        evidence.append("hook_after ran")
        return None

    # 给 HOOKS 临时塞一个测试专用事件(测完 pytest 自动撤走,不污染真表)
    monkeypatch.setitem(HOOKS, "TestEvent", [hook_none, hook_block, hook_after])

    result = trigger_hook("TestEvent")

    assert result == "blocked!"  # 契约1:返回第一个非 None 的结果
    assert evidence == []  # 契约2:短路生效——②后面的③根本没被执行


def test_trigger_hook_all_none(monkeypatch):
    evidence = []

    def hook_none(*args):
        evidence.append("hook_none ran")
        return None

    def hook_after(*args):
        evidence.append("hook_after ran")
        return None

    monkeypatch.setitem(HOOKS, "TestEvent", [hook_none, hook_after])

    result = trigger_hook("TestEvent")

    assert result is None  # 契约1:全 None 就返回 None
    assert evidence == ["hook_none ran", "hook_after ran"]  # 契约2:③被执行了
