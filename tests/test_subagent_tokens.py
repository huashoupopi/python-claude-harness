"""子 agent token 单独记账。不混进 TOKEN_USAGE,没测到时不得写成 0。"""

from tests.test_stream_accumulate import make_chunk, make_usage_chunk


def _reset_sub(trunk):
    trunk.SUBAGENT_TOKEN_USAGE.update(
        prompt=0, completion=0, total=0, calls=0, measured_calls=0
    )


def test_subagent_records_usage_separately_from_loop(sandbox, monkeypatch):
    _reset_sub(sandbox)
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")
    before_loop = dict(sandbox.TOKEN_USAGE)

    def fake_create(**kwargs):
        assert kwargs.get("stream") is True
        assert kwargs.get("stream_options") == {"include_usage": True}
        return iter(
            [
                make_chunk(content="ok"),
                make_chunk(finish_reason="stop"),
                make_usage_chunk(prompt=11, completion=2),
            ]
        )

    monkeypatch.setattr(sandbox.client.chat.completions, "create", fake_create)
    out = sandbox.spawn_subagent("say hi")
    assert out == "ok"
    sub = sandbox.SUBAGENT_TOKEN_USAGE
    assert sub["calls"] == 1
    assert sub["measured_calls"] == 1
    assert sub["prompt"] == 11
    assert sub["completion"] == 2
    assert sub["total"] == 13
    assert sandbox.TOKEN_USAGE == before_loop


def test_subagent_unmeasured_when_usage_missing(sandbox, monkeypatch):
    _reset_sub(sandbox)
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")

    def fake_create(**kwargs):
        return iter([make_chunk(content="ok"), make_chunk(finish_reason="stop")])

    monkeypatch.setattr(sandbox.client.chat.completions, "create", fake_create)
    sandbox.spawn_subagent("say hi")
    sub = sandbox.SUBAGENT_TOKEN_USAGE
    assert sub["calls"] == 1
    assert sub["measured_calls"] == 0
    assert sub["total"] == 0  # 内部累加器仍是 0,对外由 runner 写成 null
