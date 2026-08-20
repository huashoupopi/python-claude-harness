"""单次考试:装载一份 harness,喂一道题,跑完退出。由 run_bench.py 起子进程调用。

    python agent_runner.py <task.md 路径> [被测文件名]

【2026-08-14 改造:stage-1 跑散件 → stage-2 跑主干】
stage-1 的四个臂是四个【散件文件】(03/09/10/11),靠改 SRC 切换。
stage-2 只有一个被测对象(22_trunk.py),四个臂靠【环境变量 MEMORY_MODE】切换 ——
所以 CONFIGS 的含义从「文件名」变成了「一组环境变量」,见 run_bench.py。

⚠️ 两组数据不可直接比:
    stage-1  散件 —— 测「单独加一层的成本」
    stage-2  主干 —— 测「九件共存时开关一层的成本」
    基线不同,数字不能放同一张表。

【为什么这里要显式调 ensure_dirs / init_session】
主干 22_trunk.py 已改造成「可 import 而不启动」——模块级零副作用,
准备工作全收在 main() 里。而本脚本走的是 importlib 装载,__name__ 不是 "__main__",
main() 根本不会执行。所以 main() 里那两步准备必须自己补做:
    ensure_dirs()   工具要往 .tasks/ .memory/ 写文件,目录不在就炸
    init_session()  扫技能 + 算 context + 建 messages[0] 的 system prompt
散件不需要这些,因为它们模块级就把活干了 —— 这是「可 import」那次改造的账单。

【为什么调 run_agent_turn_locked 而不是自己拼 messages 调 agent_loop】
自己拼会漏掉真实路径上的东西:UserPromptSubmit hook、self 模式的记忆提取、
inbox 消费、session_context 的更新。那样测的是「阉割版主干」。
本脚本下面三行 = main() 里一次性模式那个分支的完整复制。
🪝 bench 要测「真实运行的东西」;凡是自己重新拼一遍的地方,都是行为漂移的入口。
"""

import importlib.util
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TRACE_PATH = Path.cwd() / ".bench_trace.json"  # cwd = 考场副本;run_bench 跑完来读
# 不传就跑主干;想对比散件时仍可从命令行传文件名(保持 stage-1 的接口)
TARGET = sys.argv[2] if len(sys.argv) > 2 else "22_trunk.py"
SRC = EXAMPLES / TARGET

# 三行咒语:按文件路径装载模块(文件名以数字开头,不能 import)
spec = importlib.util.spec_from_file_location("harness", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# ─────────────────────────────────────────────────────────────────────────
# 轨迹探针:在【发请求那一刻】记录,不是跑完再看
#
# 为什么不能跑完读 m.session_history:agent_loop 里的 compact 会 messages[:] = ...
# 原地替换,压缩掉的内容(比如注入过的 <reminder>)就再也数不到了。
# 而三个消融臂的差异恰恰有一半在这些【看不见的注入】上 ——
# stdout 里既没有 reminder、也没有 system prompt、也没有注入的记忆。
#
# 只记聚合数字不记全文:一次 bench 跑 120 个单元,存全文会撑爆磁盘,
# 而归因需要的是「注入了几次」「上下文涨到多大」,原文在 agent.log 里已经够查。
# 📌 包装 client 而不改主干 —— 测量问题在 bench 侧解决,主干不为被测而改。
# ─────────────────────────────────────────────────────────────────────────
_orig_create = m.client.chat.completions.create
# 🔴 2026-08-16:把【被测对象是谁】记进轨迹。
# 起因是当事人纠正我一句「我们用的不是 grok4.6,是 composer2.5」——
# 而我翻遍 results.jsonl 与 trace,【哪里都没记端点】,只能靠人记住。
# 08-15 那批与这批之间换过端点,历史数据一旦离开当时的对话就再也说不清自己是谁跑的。
# 🪝 与 CONFIGS 那段「实验配置必须自我说明」是同一条规矩 —— 当时只落实到了消融变量上,
#    漏了最要紧的那一项:模型本身。
# ⚠️ 只记模型名。base_url 与 key 一概不进文件 —— 轨迹是要交付、要进仓库的。
_trace = {
    "turns": [],
    "system_prompt_chars": 0,
    "model": getattr(m, "model", None),
}
# 附加层的账:主循环之外那几次【非流式】LLM 调用。
# 2026-08-15 grep 出 7 个 create 调用点,主干的 TOKEN_USAGE 只覆盖主循环那一个,
# 漏掉的四处恰恰【就是消融的两个轴】:
#     select_relevant_memories / extract_memories / consolidate_memories  → memory 层
#     summarize_history                                                    → compact 层
# 不记的话,self 臂比 none 臂多花的钱一分都看不见 —— memory 轴的成本差异是假的。
# calls 这个数本身就有价值:它直接量化「这一层额外调了几次 LLM」。
_aux = {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "cached": 0, "cached_reported": 0}
_cached_of = getattr(m, "_usage_cached_tokens", lambda u: 0)
_cached_seen = getattr(m, "_usage_cached_field_present", lambda u: False)


def _spy_create(**kwargs):
    # 🔴 流式=主循环的一轮,非流式=附加层的一次调用,两者【绝不能混进同一个 turns】。
    #    混了的话 turns 虚高,而 reminders/todo_calls 取 turns[-1] 会取到
    #    extract_memories 那次(它传的是 pre_compress 完整历史,数出来的 reminder 是别人的)。
    if not kwargs.get("stream"):
        resp = _orig_create(**kwargs)
        u = getattr(resp, "usage", None)  # 探针实测:ChatCompletion 有,Stream 没有
        if u:
            _aux["prompt"] += u.prompt_tokens
            _aux["completion"] += u.completion_tokens
            _aux["total"] += u.total_tokens
            _aux["cached"] += _cached_of(u)
            if _cached_seen(u):
                _aux["cached_reported"] += 1
        _aux["calls"] += 1
        return resp

    msgs = kwargs.get("messages", [])
    sys_msgs = [x for x in msgs if x.get("role") == "system"]
    if sys_msgs:
        _trace["system_prompt_chars"] = len(str(sys_msgs[0].get("content", "")))
    _trace["turns"].append(
        {
            "messages": len(msgs),
            # 上下文规模:粗算字符数(token 数等 22_trunk 支持 usage 后再换成真值)
            "chars": sum(len(str(x.get("content", "") or "")) for x in msgs),
            # 系统主动催了几次 —— reminder 是直接 append 进 messages 的,stdout 里看不到
            "reminders": sum(
                1
                for x in msgs
                if x.get("role") == "user" and "<reminder>" in str(x.get("content", ""))
            ),
            # 模型自己调了几次 todo_write(累计值,取最后一轮即总数)
            "todo_calls": sum(
                1
                for x in msgs
                for tc in (x.get("tool_calls") or [])
                if (tc.get("function", {}) or {}).get("name") == "todo_write"
            ),
            # self 模式注入的记忆有没有真的进 prompt(空 = 这一臂的记忆层没起作用)
            "memory_injected": any(
                "Relevant memories:" in str(x.get("content", "")) for x in sys_msgs
            ),
        }
    )
    return _orig_create(**kwargs)


m.client.chat.completions.create = _spy_create

task_text = Path(sys.argv[1]).read_text()

# ↓↓↓ 以下三行 = main() 里 `if args.task:` 那个分支 ↓↓↓
try:
    m.ensure_dirs()
    m.init_session()
    with m.agent_lock:
        m.run_agent_turn_locked(task_text)
finally:
    # 沙箱容器:正常路径下自己收尸(超时被 kill 时轮不到这里跑,由 run_bench 兜底)
    try:
        m.stop_sandbox()
    except Exception:
        pass
    # 真实 token 账单(2026-08-15 起):主干在 agent_loop 里按轮累加。
    # 🪝 这是消融的【分母】—— 之前只能拿 steps 当成本的近似,现在是真数字:
    #    「todo_write 那 947 字符 schema 值不值」届时是一道除法,不是感觉。
    # 分两本账记,不合并:
    #   loop = 主循环(流式),由主干 TOKEN_USAGE 在 agent_loop 里逐轮累加
    #   aux  = 附加层(非流式),由上面的 spy 从 response.usage 直接读
    # 🪝 分开记比合并更有价值 —— 「这一层自己烧了多少」正是消融要回答的问题,
    #    合成一个总数就再也拆不开了。
    _trace["tokens_loop"] = dict(getattr(m, "TOKEN_USAGE", {}))
    _trace["tokens_aux"] = dict(_aux)
    sub = getattr(m, "SUBAGENT_TOKEN_USAGE", None)
    if isinstance(sub, dict) and sub.get("measured_calls"):
        _trace["tokens_subagent"] = sub.get("total")
        _trace["tokens_subagent_detail"] = dict(sub)
    else:
        # API 没返回 usage 时记 null,不得伪造 0
        _trace["tokens_subagent"] = None
        _trace["tokens_subagent_detail"] = dict(sub) if isinstance(sub, dict) else None
    cals = [
        e
        for e in getattr(m, "_trace_events", [])
        if isinstance(e, dict) and e.get("kind") == "token_calibration"
    ]
    _trace["token_calibration"] = cals
    if hasattr(m, "summarize_token_calibration"):
        _trace["token_estimate"] = m.summarize_token_calibration(cals)
    events = [
        e
        for e in getattr(m, "_trace_events", [])
        if isinstance(e, dict)
    ]
    _trace["harness_events"] = events
    hist = getattr(m, "session_history", None) or []
    final = ""
    for msg in reversed(hist):
        if msg.get("role") == "assistant":
            content = msg.get("content") or ""
            final = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            break
    _trace["final_assistant"] = final
    # finally:超时被 kill 之外的任何退出路径都要留下轨迹 ——
    # 崩溃那次的轨迹恰恰最该看(㉚:success 会骗人,只有轨迹说实话)
    TRACE_PATH.write_text(json.dumps(_trace, ensure_ascii=False), encoding="utf-8")
