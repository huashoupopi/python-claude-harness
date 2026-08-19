"""D5.1:字符/4 估算 vs API prompt_tokens。不改公式,只记账。"""

import json
import sys
from pathlib import Path

from tests.test_stream_accumulate import make_chunk, make_usage_chunk


def test_estimate_tokens_formula_unchanged(trunk):
    """D5.1 不许改秤,仍是字符/4。"""
    msgs = [{"role": "user", "content": "abcd" * 80}]
    assert trunk.estimate_tokens(msgs) == len(str(msgs)) // 4


def test_summarize_bias_is_actual_over_estimate(trunk):
    events = [
        {"kind": "token_calibration", "estimated_tokens": 100, "prompt_tokens": 220},
        {"kind": "token_calibration", "estimated_tokens": 50, "prompt_tokens": 80},
        {"kind": "compact", "layer": "L2"},
    ]
    s = trunk.summarize_token_calibration(events)
    assert s["n"] == 2
    assert s["estimated_tokens"] == 150
    assert s["prompt_tokens"] == 300
    assert s["bias"] == 2.0


def test_summarize_bias_none_when_no_estimate(trunk):
    assert trunk.summarize_token_calibration([])["bias"] is None


def test_stream_turn_writes_calibration_event(sandbox, monkeypatch):
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")
    monkeypatch.setattr(sandbox, "TODO_MODE", "none")
    monkeypatch.setattr(sandbox, "compact_failures", 0)

    def fake_create(**kwargs):
        return iter(
            [
                make_chunk(content="ok"),
                make_chunk(finish_reason="stop"),
                make_usage_chunk(prompt=99, completion=4),
            ]
        )

    monkeypatch.setattr(sandbox.client.chat.completions, "create", fake_create)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hello"},
    ]
    sandbox.agent_loop(msgs, {})
    evs = [e for e in sandbox._trace_events if e.get("kind") == "token_calibration"]
    assert len(evs) == 1
    ev = evs[0]
    assert ev["turn"] == 0
    assert ev["prompt_tokens"] == 99
    # 记账在 append 本轮 assistant 之前,所以比事后的 messages 少一条
    assert ev["estimated_tokens"] == sandbox.estimate_tokens(msgs[:-1])


def test_no_calibration_when_usage_missing(sandbox, monkeypatch):
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")
    monkeypatch.setattr(sandbox, "TODO_MODE", "none")

    def fake_create(**kwargs):
        return iter([make_chunk(content="ok"), make_chunk(finish_reason="stop")])

    monkeypatch.setattr(sandbox.client.chat.completions, "create", fake_create)
    sandbox.agent_loop(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        {},
    )
    assert not [
        e for e in sandbox._trace_events if e.get("kind") == "token_calibration"
    ]


def test_trace_view_prints_calibration(tmp_path, capsys, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "trace_view_calib",
        Path(__file__).parent.parent / "bench" / "trace_view.py",
    )
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)
    payload = {
        "harness": "test",
        "model": "fake",
        "memory_mode": "none",
        "todo_mode": "nudge",
        "sandbox_mode": "off",
        "events": [
            {
                "kind": "token_calibration",
                "turn": 0,
                "estimated_tokens": 100,
                "prompt_tokens": 210,
                "t": 1.0,
            }
        ],
    }
    path = tmp_path / "trace_cal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["trace_view.py", str(path)])
    tv.main()
    out = capsys.readouterr().out
    assert "CAL" in out
    assert "est=100" in out
    assert "actual=210" in out
    assert "2.10" in out
