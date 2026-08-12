"""E 组:消息总线 + 请求/响应协议状态机。

T19 搬 teams 时会动这两块。总线的「读即销毁」和协议的「幂等 + 类型校验」
都是不写测试就看不出被改坏的行为。
"""

import pytest


# ---------- MessageBus ----------


def test_send_then_read_delivers_message(sandbox):
    """发出去的消息能被收件人读到,字段齐全。"""
    sandbox.BUS.send("lead", "worker", "去修 bug")
    msgs = sandbox.BUS.read_inbox("worker")
    assert len(msgs) == 1
    assert msgs[0]["from"] == "lead"
    assert msgs[0]["to"] == "worker"
    assert msgs[0]["content"] == "去修 bug"
    assert msgs[0]["type"] == "message"  # 默认类型


def test_read_inbox_is_destructive(sandbox):
    """读即销毁:read_inbox 读完就 unlink,第二次读是空的。

    这是「消息只投递一次」的实现方式。T19 若改成非破坏性读,
    agent 会反复处理同一条消息——这条测试就是那个改动的报警器。
    """
    sandbox.BUS.send("lead", "worker", "第一条")
    assert len(sandbox.BUS.read_inbox("worker")) == 1
    assert sandbox.BUS.read_inbox("worker") == []  # 再读为空


def test_read_empty_inbox_returns_empty_list(sandbox):
    """从没收过信的 agent 读收件箱,返回空列表而不是炸。"""
    assert sandbox.BUS.read_inbox("从未存在过的人") == []


def test_messages_accumulate_until_read(sandbox):
    """收件箱是 jsonl 追加,读之前会累积。"""
    for i in range(3):
        sandbox.BUS.send("lead", "worker", f"第{i}条")
    msgs = sandbox.BUS.read_inbox("worker")
    assert len(msgs) == 3
    assert [m["content"] for m in msgs] == ["第0条", "第1条", "第2条"]  # 保序


# ---------- 协议状态机 ----------


@pytest.fixture
def pending(trunk, monkeypatch):
    """造一个待处理的 shutdown 请求。"""
    monkeypatch.setattr(trunk, "pending_requests", {})
    state = trunk.ProtocolState(
        request_id="req_000001",
        type="shutdown",
        sender="lead",
        target="worker",
        payload="收工",
    )
    trunk.pending_requests["req_000001"] = state
    return trunk, state


def test_matching_response_settles_the_request(pending):
    """类型对得上的响应把状态从 pending 推到 approved/rejected。"""
    trunk, state = pending
    assert state.status == trunk.ProtocolStatus.PENDING
    trunk.match_response("shutdown_response", "req_000001", approve=True)
    assert state.status == trunk.ProtocolStatus.APPROVED


def test_type_mismatch_leaves_state_untouched(pending):
    """拿计划审批的响应去回复关机请求,不生效——请求类型必须对得上。"""
    trunk, state = pending
    trunk.match_response("plan_approval_response", "req_000001", approve=True)
    assert state.status == trunk.ProtocolStatus.PENDING  # 没被推动


def test_duplicate_response_is_ignored(pending):
    """已结算的请求再收到响应就忽略,不会被翻案——幂等。"""
    trunk, state = pending
    trunk.match_response("shutdown_response", "req_000001", approve=True)
    trunk.match_response("shutdown_response", "req_000001", approve=False)
    assert state.status == trunk.ProtocolStatus.APPROVED  # 仍是第一次的结果


def test_unknown_request_id_does_not_raise(pending):
    """回复一个不存在的 request_id 只打日志,不抛异常。"""
    trunk, _ = pending
    trunk.match_response("shutdown_response", "req_999999", approve=True)  # 不炸即通过
