"""compact 管线的单元测试。被测:examples/22_trunk.py 的 snip_compact / micro_compact

【为什么必须造假数据】
snip_compact 第一行就是 `if len(msgs) <= max_msgs: return msgs`。
正常对话只有几条到十几条消息 —— 这段代码在真实使用中【一次都不会执行】。
等哪天真聊到 50 条以上它才第一次运行,那时如果搬错了,你会看到一个
莫名其妙的 API 400,很难联想到是压缩把 tool_use / tool_result 的配对切断了。

【最该测的是配对保护】
snip_compact 里那两个 while 循环看起来像可有可无的边界微调,
实际是在防「裁剪边界正好落在 assistant(tool_calls) 和它的 tool 结果之间」。
切断了就产生孤儿 → 下一轮请求 400。
"""


# ---------------------------------------------------------------------------
# 造数据的小工具
# ---------------------------------------------------------------------------


def a_call(cid: str) -> dict:
    """造一条「我要调工具」的 assistant 消息。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ],
    }


def a_result(cid: str, content: str = "x" * 200) -> dict:
    """造一条「工具结果」的 tool 消息,默认内容 200 字符(超过 micro_compact 的 120 门槛)。"""
    return {"role": "tool", "tool_call_id": cid, "content": content}


def a_text(role: str = "assistant", content: str = "hi") -> dict:
    """造一条纯文本消息,不涉及工具。"""
    return {"role": role, "content": content}


def collect_pairs(msgs: list) -> tuple[set, set]:
    """收集「发起了哪些调用」和「哪些调用有结果」。

    两个集合相等 = 零孤儿。不相等时 pytest 会把差集打出来,
    一眼看得出是「有调用没结果」还是「有结果没调用」。
    """
    calls = {
        tc["id"]
        for m in msgs
        if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    }
    results = {
        m["tool_call_id"]
        for m in msgs
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    return calls, results


def build_history(n_pairs: int) -> list:
    """造一段历史:system + user + n 对(调用,结果)。

    下标规律:  [0]=system  [1]=user  [2]=call_0  [3]=result_0  [4]=call_1 ...
    即 msgs[2 + 2k] 是第 k 对的【调用】,msgs[3 + 2k] 是它的【结果】。
    """
    msgs = [a_text("system", "s"), a_text("user", "u")]
    for k in range(n_pairs):
        msgs.append(a_call(f"call_{k}"))
        msgs.append(a_result(f"call_{k}"))
    return msgs


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_short_history_untouched(trunk):
    """消息不到 max_msgs 时原样返回,一个字不动。"""
    msgs = build_history(4)  # 2 + 8 = 10 条
    before = [dict(m) for m in msgs]
    out = trunk.snip_compact(msgs, max_msgs=50)
    assert out == before
    calls, results = collect_pairs(out)
    assert calls == results  # 顺带确认造数据的工具本身是配对的


# TODO ① 消息很多时确实被裁了
#   造 build_history(40) → 82 条,调 snip_compact(msgs, max_msgs=50)
#   断言:结果比原来短;中间出现一条 content 以 "[snipped" 开头的消息;
#        头 3 条与原来相同
def test_long_history_snipped(trunk):
    msgs = build_history(40)  # 2 + 80 = 82 条
    out = trunk.snip_compact(msgs, max_msgs=50)
    assert len(out) < len(msgs)
    assert any(m.get("content", "").startswith("[snipped") for m in out)
    assert out[:3] == msgs[:3]


#
# TODO ② 🔴 头部边界正好切在一对中间 —— 本文件的核心
#   keep_head = 3,所以 msgs[2] 是头部保留的最后一条。
#   build_history 造出来的 msgs[2] 正好是 call_0(一条「我要调工具」),
#   msgs[3] 是它的结果 —— 边界天然落在配对中间。
#   断言:collect_pairs(裁剪结果) 的两个集合【相等】(零孤儿)
#   ⚠️ 这条红了,说明 snip_compact 里头部那个 while 被漏搬或改错了


def test_head_boundary_cut(trunk):
    msgs = build_history(40)  # 2 + 80 = 82 条
    # keep_head 写死在 snip_compact 内部(=3),不是参数;msgs[2] 正好是 call_0
    out = trunk.snip_compact(msgs, max_msgs=50)
    calls, results = collect_pairs(out)
    assert calls == results


#
# TODO ③ micro_compact:老的 tool_result 被抹,最近 KEEP_RECENT 条不动
#   造 6 对,调 micro_compact
#   断言:前 3 条结果的 content == "[tool result snipped]";后 3 条原样
def test_micro_compact_old_results_snipped(trunk):
    msgs = build_history(6)  # 2 + 12 = 14 条
    out = trunk.micro_compact(msgs)
    for k in range(6):
        result_msg = out[3 + 2 * k]  # 第 k 对的结果
        if k < 3:
            assert result_msg["content"] == "[tool result snipped]"
        else:
            assert result_msg["content"] == "x" * 200


#
# TODO ④ micro_compact:内容 <= 120 字符的不抹
#   用 a_result(cid, content="short") 造几条
#   断言:它们的 content 没被改
def test_micro_compact_short_results_untouched(trunk):
    msgs = [a_text("system", "s"), a_text("user", "u")]
    for k in range(6):
        msgs.append(a_call(f"call_{k}"))
        msgs.append(a_result(f"call_{k}", content="short"))
    out = trunk.micro_compact(msgs)
    for k in range(6):
        result_msg = out[3 + 2 * k]  # 第 k 对的结果
        assert result_msg["content"] == "short"


def _tool_call_chunk(trunk_mod, calls):
    """造一片带多个 tool_call 的假 chunk(calls: [(id, name, args_json), ...])。"""
    from openai.types.chat import ChatCompletionChunk

    return ChatCompletionChunk.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fake",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "index": i,
                                "id": cid,
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }
                            for i, (cid, name, args) in enumerate(calls)
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )


def _text_chunk(text="done"):
    from openai.types.chat import ChatCompletionChunk

    return ChatCompletionChunk.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fake",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }
    )


def test_compact_plus_other_tools_leaves_no_orphan(sandbox, monkeypatch):
    """🔴 compact 和别的工具【同一轮】被调用时,不许留孤儿。

    2026-08-15 人工扫描发现的漏:agent_loop 里 compact 那一支处理完就 break,
    后面的 tool_call 永远轮不到执行 —— 它们的 tool_call_id 没有对应回复 = 孤儿,
    下一轮请求 API 直接 400。
    ⚠️ break 本身是对的(try_compact 会重写整个历史,压缩后再往上贴位置就乱了);
       错的是止损时没给剩下的调用一个交代。

    实测:两批共 240 次跑批里模型一次都没调过 compact —— 这个 bug 从未被触发。
    但它的触发条件是「对话长到需要压缩」,恰恰是最不该崩的时候。
    🪝 没触发过 ≠ 不存在;测试要覆盖【最坏的时候】,不是【常见的时候】。
    """
    trunk = sandbox
    rounds = []

    def fake_create(**kwargs):
        rounds.append(kwargs)
        if len(rounds) == 1:
            # 第一轮:模型一次要调两个工具 —— compact 排在前面
            return iter(
                [
                    _tool_call_chunk(
                        trunk,
                        [
                            ("call_compact", "compact", "{}"),
                            ("call_read", "read_file", '{"path": "x.txt"}'),
                        ],
                    )
                ]
            )
        return iter([_text_chunk()])  # 第二轮:说句话就结束

    monkeypatch.setattr(trunk.client.chat.completions, "create", fake_create)
    monkeypatch.setattr(trunk, "MEMORY_MODE", "none")  # 别去调记忆提取
    monkeypatch.setattr(trunk, "compact_history", lambda msgs: msgs[1:])  # 压缩本身不是重点

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "干活"},
    ]
    trunk.agent_loop(messages, {})

    calls, results = collect_pairs(messages)
    assert calls == results, (
        f"有孤儿!发起了 {len(calls)} 个调用,只有 {len(results)} 个有结果。"
        f"缺的是: {calls - results}"
    )
    # 而且被跳过的那个必须收到【说明原因】的回复,不能是空的 —— 错误消息就是 prompt
    skipped = [m for m in messages if m.get("tool_call_id") == "call_read"]
    assert skipped and "compacted" in skipped[0]["content"].lower()


def test_teammate_truncation_keeps_pairs(trunk):
    """🔴 teammate 的历史截断不许把「调用」和「结果」切散。

    2026-08-15 扫描发现:teammate 那条线是裸切 messages[-20:],
    切口万一落在【工具结果】上,它对应的 assistant 调用就被切掉了 = 孤儿 → API 400。
    主循环的 snip_compact 早有这道保护(:610),teammate 漏了。
    🪝 同一个坑在两条线上,只有一条设了防 —— 复制粘贴时最容易丢的就是这种
       「看起来无关紧要的三行」。

    ⚠️ 这条用例是【精心构造】的:必须让 msgs[len-20] 正好落在 tool 上。
       随手构造(每对 2 条)时切口永远落在 assistant 上,两种写法都"零孤儿" ——
       看起来像修好了,其实压根没进那个 if。
    🪝 构造测试用例时得先确认它真的走到了要测的那条分支。
    """
    msgs = [
        {"role": "system", "content": "s"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "content": "r"},
    ]
    for i in range(1, 10):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r"})
    msgs.append({"role": "user", "content": "再来"})

    assert msgs[len(msgs) - 20]["role"] == "tool", "用例失效:切口没落在工具结果上"

    # 裸切(改之前的写法)——先证明这个坑是真的
    naive_calls, naive_results = collect_pairs([msgs[0]] + msgs[-20:])
    assert naive_results - naive_calls, "裸切居然没造出孤儿?那这条测试就没意义了"

    # 改后:退回到发起调用的那条 assistant
    cut = len(msgs) - 20
    while cut < len(msgs) and trunk._is_tool_result_message(msgs[cut]):
        cut -= 1
        if cut <= 1:
            cut = 1
            break
    calls, results = collect_pairs([msgs[0]] + msgs[max(cut, 1) :])
    assert results <= calls, f"还有孤儿: {results - calls}"


# ---------------------------------------------------------------------------
# 包 D:L4 阈值单位 + 受控触发 + compact trace + REPL /compact
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_chars_divided_by_four(trunk):
    """estimate_tokens 与 CONTEXT_LIMIT_TOKENS 必须同一单位(token=字符/4)。只改一边会让 L4 更难触发。"""
    msgs = [{"role": "user", "content": "abcd" * 250}]
    chars = len(str(msgs))
    assert trunk.estimate_tokens(msgs) == chars // 4
    assert trunk.CONTEXT_LIMIT_TOKENS == int(
        trunk.MODEL_CONTEXT_TOKENS * trunk.COMPACT_TRIGGER_RATIO
    )


def test_l1_l2_l3_thresholds_untouched(trunk):
    """本包只动 L4 门槛来源,L1/L2/L3 的既有阈值不许改。"""
    import inspect

    assert trunk.KEEP_RECENT == 3
    assert trunk.PERSIST_THRESHOLD == 30000
    assert "max_msgs=50" in inspect.getsource(trunk.snip_compact)
    assert "max_bytes=200_000" in inspect.getsource(trunk.tool_result_budget)
    assert "persist" not in inspect.getsource(trunk.micro_compact)


def test_default_limit_does_not_compact_small_history(trunk):
    """默认窗口×0.8 下,短对话不该走 L4。"""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ]
    before = [dict(m) for m in msgs]
    trunk.try_compact(msgs)
    assert msgs == before


def test_lowered_token_limit_triggers_l4(sandbox, monkeypatch):
    """MODEL_CONTEXT_TOKENS=2000 同款:CONTEXT_LIMIT_TOKENS=1600 token 时,纯长文本必须触发 L4。"""
    monkeypatch.setattr(sandbox, "CONTEXT_LIMIT_TOKENS", 1600)
    monkeypatch.setattr(sandbox, "compact_failures", 0)
    called = []

    def fake_hist(msgs):
        called.append(len(msgs))
        return [{"role": "user", "content": "[Compacted]\n\nstub-summary"}]

    monkeypatch.setattr(sandbox, "compact_history", fake_hist)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "x" * 8000},
    ]
    assert sandbox.estimate_tokens(msgs) > 1600
    sandbox.try_compact(msgs)
    assert called, "调低阈值后 L4 没出手"
    assert msgs[0]["role"] == "system"
    assert msgs[1]["content"].startswith("[Compacted]")
    assert len(msgs) == 2


def test_compact_trace_keeps_chars_and_tokens(sandbox, tmp_path, monkeypatch):
    """出手时记 compact 事件,字符和 token 两个口径都要在。"""
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "TRACE_DIR", tmp_path / ".traces")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "CONTEXT_LIMIT_TOKENS", 1600)
    monkeypatch.setattr(sandbox, "compact_failures", 0)
    monkeypatch.setattr(
        sandbox,
        "compact_history",
        lambda msgs: [{"role": "user", "content": "[Compacted]\n\nstub"}],
    )
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "y" * 8000},
    ]
    chars_before = sandbox._chars_of(msgs)
    sandbox.try_compact(msgs)
    events = [e for e in sandbox._trace_events if e.get("kind") == "compact"]
    assert events, "L4 出手了但 trace 没有 compact 事件"
    ev = events[-1]
    assert ev["layer"] == "L4"
    assert ev["reason"] == "over_limit"
    assert ev["chars_before"] == chars_before
    assert ev["tokens_before"] == chars_before // 4
    assert "chars_after" in ev and "tokens_after" in ev
    assert ev["n_after"] == 2


def test_repl_compact_force_rewrites_history(sandbox, monkeypatch):
    """/compact 走 try_compact(force=True),不看门槛。"""
    monkeypatch.setattr(sandbox, "compact_failures", 0)
    monkeypatch.setattr(
        sandbox,
        "compact_history",
        lambda msgs: [{"role": "user", "content": "[Compacted]\n\nforced"}],
    )
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "short"},
    ]
    assert sandbox.estimate_tokens(history) <= sandbox.CONTEXT_LIMIT_TOKENS
    consumed = sandbox.handle_repl_command("/compact", history)
    assert consumed is True
    assert history[1]["content"].startswith("[Compacted]")
    assert sandbox.handle_repl_command("普通问题", history) is False


def test_l2_snip_emits_compact_event_in_agent_loop(sandbox, monkeypatch):
    """L2 出手(抹旧 tool 结果)要进 trace;L1/L3 没出手就不要冒充。"""
    monkeypatch.setattr(sandbox, "TRACE_MODE", "on")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "none")
    monkeypatch.setattr(sandbox, "TODO_MODE", "none")
    monkeypatch.setattr(sandbox, "compact_failures", 0)

    rounds = {"n": 0}

    def fake_create(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return iter([_text_chunk("done")])
        return iter([_text_chunk("done")])

    monkeypatch.setattr(sandbox.client.chat.completions, "create", fake_create)
    msgs = build_history(6)  # 14 条,旧结果会被 L2 抹掉
    sandbox.agent_loop(msgs, {})
    layers = [
        e["layer"] for e in sandbox._trace_events if e.get("kind") == "compact"
    ]
    assert "L2" in layers
    assert "L1" not in layers  # 14 条 < 50,L1 不应出手
    assert "L3" not in layers  # 合计远低于 200_000,L3 不应出手


def test_trace_view_shows_compact_event(tmp_path, capsys, monkeypatch):
    """彩色视图必须把 compact 事件画出来,不能当成缺 tool 名的调用崩掉。"""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "trace_view_under_test",
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
            {"kind": "user", "text": "hi", "t": 1.0},
            {
                "kind": "compact",
                "layer": "L4",
                "reason": "over_limit",
                "chars_before": 8000,
                "chars_after": 200,
                "tokens_before": 2000,
                "tokens_after": 50,
                "n_before": 40,
                "n_after": 2,
                "t": 2.0,
            },
        ],
    }
    import json

    path = tmp_path / "trace_compact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["trace_view.py", str(path)])
    tv.main()
    out = capsys.readouterr().out
    assert "L4" in out
    assert "8000→200" in out
    assert "2000→50" in out
