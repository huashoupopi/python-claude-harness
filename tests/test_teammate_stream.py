"""F3:teammate 与主循环同一条流式路径。"""

import threading
import time

from tests.test_stream_accumulate import make_chunk


class _QuietBus:
    def read_inbox(self, _name):
        return []

    def send(self, *args, **kwargs):
        return None


def test_teammate_create_uses_stream(trunk, monkeypatch):
    seen = []

    def fake_create(**kwargs):
        seen.append(kwargs)
        return iter([make_chunk(content="done"), make_chunk(finish_reason="stop")])

    monkeypatch.setattr(trunk.client.chat.completions, "create", fake_create)
    monkeypatch.setattr(trunk, "idle_poll", lambda *a, **k: trunk.IdleResult.TIMEOUT)
    monkeypatch.setattr(trunk, "BUS", _QuietBus())
    monkeypatch.setattr(trunk, "active_teammates", {})

    trunk.spawn_teammate_thread("alice", "worker", "say hi")
    deadline = time.time() + 3
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    for t in threading.enumerate():
        if t is not threading.main_thread() and "alice" in t.name or t.daemon:
            t.join(timeout=1)

    assert seen, "teammate 线程没发到 LLM"
    assert seen[0].get("stream") is True
    assert "stream_options" in seen[0]
