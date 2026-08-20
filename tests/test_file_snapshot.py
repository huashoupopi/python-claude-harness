"""BENCH_FILE_SNAPSHOT 默认关；打开时轮末有 file_snapshot。"""

from tests.test_stream_accumulate import make_chunk


def test_snapshot_off_by_default(trunk):
    assert trunk.BENCH_FILE_SNAPSHOT is False


def test_snapshot_records_exam_files(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "BENCH_FILE_SNAPSHOT", True)
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")
    monkeypatch.setattr(sandbox, "TODO_MODE", "none")
    monkeypatch.setattr(sandbox, "WORKDIR", tmp_path)
    (tmp_path / "seen.py").write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(
        sandbox.client.chat.completions,
        "create",
        lambda **kwargs: iter(
            [make_chunk(content="cannot"), make_chunk(finish_reason="stop")]
        ),
    )
    sandbox.agent_loop(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        {},
    )
    snaps = [e for e in sandbox._trace_events if e.get("kind") == "file_snapshot"]
    assert snaps, "打开开关后应该有 file_snapshot"
    files = snaps[-1]["files"]
    assert "seen.py" in files


def test_permission_denied_event_on_deny_list(sandbox, monkeypatch):
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    reason = sandbox.permission_hook("bash", {"command": "sudo rm -rf /"})
    assert reason
    ev = [e for e in sandbox._trace_events if e.get("kind") == "permission_denied"]
    assert ev and ev[0]["tool"] == "bash"
