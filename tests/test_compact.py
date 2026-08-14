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
