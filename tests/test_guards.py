"""F 组:三道闸门——worktree 名校验(安全)、后台执行判定(调度)、消融开关(实验)。

前两道是纯函数。worktree 那条是路径穿越的第一道防线,T19 搬 worktree 系统时不能丢。
第三道是 T22 stage-2 的消融开关(MEMORY_MODE / TODO_MODE):
它们决定「这一轮实验到底在测什么」,写错了不会崩,只会让整批数据无声地失真。
"""

import importlib.util
from pathlib import Path

TRUNK_PATH = Path(__file__).parent.parent / "examples" / "22_trunk.py"


# ---------- worktree 名校验 ----------


def test_valid_names_pass(trunk):
    """字母数字点下划线短横,1-64 字符,返回 None。"""
    assert trunk.validate_worktree_name("feature-1") is None
    assert trunk.validate_worktree_name("fix_bug.2") is None
    assert trunk.validate_worktree_name("a" * 64) is None


def test_empty_and_dot_names_rejected(trunk):
    """空名、. 、.. 单独拒——.. 是路径穿越的入口。"""
    assert "cannot be empty" in trunk.validate_worktree_name("")
    assert "not a valid" in trunk.validate_worktree_name(".")
    assert "not a valid" in trunk.validate_worktree_name("..")


def test_path_separators_and_specials_rejected(trunk):
    """带斜杠、空格、$ 的名字进不来:worktree 名会拼进文件路径。"""
    for bad in ["a/b", "../etc", "a b", "a$b", "a;rm -rf", "a" * 65]:
        assert trunk.validate_worktree_name(bad) is not None, f"{bad!r} 本该被拒"


# ---------- 后台执行判定 ----------


def test_explicit_flag_wins(trunk):
    """模型显式说要后台跑就后台跑。"""
    assert trunk.should_run_background(
        "bash", {"command": "ls", "run_in_background": True}
    )


def test_only_explicit_flag_triggers_background(trunk):
    """🔴 2026-08-14 行为变更:只认模型的显式意图,不再按关键词猜。

    原来有个 is_slow_operation 启发式,按 ["install","build","test","pytest",...]
    猜「这条命令是不是慢操作」。两个问题:
      ① 过宽 —— `cat test.py` 命中 "test"(2026-08-11 就记录过,当时判「钉住现状」)
      ② 🔴 致命 —— mini-bench 的题目全是「跑测试 → 看结果 → 改代码」,
         pytest 被自动丢后台后模型只拿到占位符,再跑一次又进后台,实测空转 15 步

    下面这批以前全会被判成「慢操作」,现在一律前台执行。
    (本条替换掉原 test_heuristic_catches_slow_commands / test_heuristic_only_applies_to_bash;
     那两条的 docstring 当时写着「T19 若改进判定,这条会红」—— 预言应验了。)
    """
    for cmd in ("npm install", "uv run pytest -q", "docker build .", "cat test.py", "make"):
        assert not trunk.should_run_background("bash", {"command": cmd}), cmd
    # 非 bash 工具本来就不该进后台
    assert not trunk.should_run_background("read_file", {"path": "x.py"})
    # 启发式函数已删除,不该再存在
    assert not hasattr(trunk, "is_slow_operation")


# ---------- 消融开关(T22 stage-2) ----------


def test_switches_gate_the_tool_pool(trunk, monkeypatch):
    """两个消融轴都靠 assemble_tool_pool 过滤,不靠动 TOOL_REGISTRY 本身。

    注册表是【全集】(有哪些工具),工具池是【这一轮开放给谁】——
    两个概念分开,所以 pop 只发生在 dict(TOOL_REGISTRY) 这份浅拷贝上。
    下面最后一条就是钉这个的:过滤完,全集必须原封不动。
    """
    for mode, present in (("none", False), ("tool", True), ("nudge", True)):
        monkeypatch.setattr(trunk, "TODO_MODE", mode)
        assert ("todo_write" in trunk.assemble_tool_pool()) is present, mode

    for mode, present in (("none", False), ("self", False), ("official", True)):
        monkeypatch.setattr(trunk, "MEMORY_MODE", mode)
        assert ("memory" in trunk.assemble_tool_pool()) is present, mode

    # 全集不许被过滤动过 —— 动了的话 official 档再也拿不到 memory
    assert "memory" in trunk.TOOL_REGISTRY
    assert "todo_write" in trunk.TOOL_REGISTRY


def test_defaults_preserve_pre_switch_behavior(monkeypatch):
    """🔴 什么开关都不设时,行为必须与「加开关之前」一模一样。

    这条钉的是 2026-08-14 真实踩过的一个 bug:TODO_MODE 默认值写成了 "tool",
    于是主干裸跑时「每三轮催一次 todo」被静默关掉 —— 而当时:
        三档读取 ✅ 绿    工具过滤 ✅ 绿    非法值 fail loud ✅ 绿    三处逻辑 ✅ 对
    每一项显式检查都是绿的,因为 "tool" 是个完全合法的值。

    📌 fail loud 只能挡住【写错的值】,挡不住【选错的默认】。
       后者只有一种抓法:什么都不设,跑一遍,跟改动前对照。
       ——「全绿了 ≠ 写对了」,绿灯的数量不等于覆盖的面积。

    要重新装载一份是因为 conftest 的 trunk 是 session 级,
    模块级的 os.getenv 早就读过了,改环境变量对它不起作用。
    """
    monkeypatch.delenv("TODO_MODE", raising=False)
    monkeypatch.delenv("MEMORY_MODE", raising=False)
    spec = importlib.util.spec_from_file_location("trunk_fresh", TRUNK_PATH)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    assert fresh.TODO_MODE == "nudge"  # 加开关前:每三轮催一次
    assert fresh.MEMORY_MODE == "self"  # 加开关前:系统自动提取+注入


def test_illegal_switch_values_fail_loud(monkeypatch):
    """拼错臂名要当场炸,不能静默退回默认值。

    静默退回的代价是整批数据:bench 跑完一整轮才发现「那个臂根本没生效」。
    """
    for var in ("TODO_MODE", "MEMORY_MODE"):
        monkeypatch.setenv(var, "bogus")
        spec = importlib.util.spec_from_file_location("trunk_bogus", TRUNK_PATH)
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
        except ValueError as e:
            assert "bogus" in str(e), f"{var} 炸了但消息里没说是什么值不合法"
        else:
            raise AssertionError(f"{var}='bogus' 本该当场炸")
        monkeypatch.delenv(var)


# ---------- 工具执行的兜底(T21 扫描) ----------


def test_execute_tool_never_raises(sandbox):
    """🔴 任何 handler 抛异常,execute_tool 都必须转成【错误字符串】而不是往外抛。

    2026-08-15 扫描实测:9 个工具里 8 个自己处理了异常,run_claim_task 没有 ——
    模型 claim 一个不存在的任务(它很容易这么干)就会 FileNotFoundError。
    而 execute_tool 当时是裸的 `return handler(**args)`,后果分两条路:
        前台  agent_loop 那句 execute_tool 不在 try 里 → 穿透到 main,程序崩掉
        后台  worker 线程直接死,status 永远 "running",模型永远等不到结果,
              而且【一点报错都看不到】—— daemon 线程死得无声无息
    🪝「27 个工具每个都记得自己 try」是列举,总有一个会漏;
       「execute_tool 统一兜住」是构造 —— 漏不漏都不影响结果。
    """

    class BoomFn:
        name = "boom"
        arguments = "{}"

    class BoomTC:
        id = "call_boom"
        function = BoomFn()

    def boom():
        raise RuntimeError("我炸了")

    registry = dict(sandbox.assemble_tool_pool())
    registry["boom"] = sandbox.ToolEntry("会炸的工具", {}, None, boom)

    out = sandbox.execute_tool(BoomTC(), registry)  # 不抛异常即通过
    assert isinstance(out, str)
    # 兜底不等于吞掉:消息要带工具名和异常类型,因为它会原样进模型的上下文
    assert "boom" in out and "RuntimeError" in out and "我炸了" in out


def test_task_tools_report_missing_task_uniformly(sandbox):
    """同族三个任务工具,对「任务不存在」要给一样的交代。

    原来只有 run_get_task 处理了,claim/complete 两个裸奔。
    🪝 同族函数写法不一致,就是漏的温床 —— 一眼扫过去看不出哪个没处理。
    """
    for fn in (sandbox.run_get_task, sandbox.run_claim_task, sandbox.run_complete_task):
        out = fn("no_such_task")
        assert "not found" in out.lower(), f"{fn.__name__} 没给出「找不到」的交代: {out!r}"


# ---------- worktree 三兄弟(T21 扫描) ----------


def test_keep_worktree_refuses_nonexistent(sandbox, tmp_path, monkeypatch):
    """🔴 keep 一个不存在的 worktree 必须报错,不能回一句「已保留」。

    2026-08-15 扫描发现:keep_worktree 从不检查存在性 ——
    create 检查了、remove 检查了,只有它没有。
    🪝 工具返回【假成功】比返回错误更糟:模型会拿它当事实继续往下走,
       而且这种错在日志里看起来一切正常。
    """
    monkeypatch.setattr(sandbox, "WORKTREES_DIR", tmp_path / "wt")
    (tmp_path / "wt").mkdir()
    out = sandbox.keep_worktree("no_such_tree")
    assert "not found" in out.lower(), f"居然说保留成功了: {out!r}"


def test_worktree_errors_share_one_format(sandbox):
    """三个 worktree 函数对同一种非法输入,要给一样格式的错误。

    原来 create 带 "Error: " 前缀、remove/keep 不带。
    🪝 错误消息是给【模型】看的 —— 格式一致它才容易识别「这是一次失败」。
    """
    bad = "../etc"  # 路径穿越,三个都该拒
    outs = [
        sandbox.create_worktree(bad),
        sandbox.remove_worktree(bad),
        sandbox.keep_worktree(bad),
    ]
    for o in outs:
        assert o.startswith("Error: "), f"格式不一致: {o!r}"


# ---------- 轨迹记录(T23 可观测性) ----------


def test_trace_records_blocked_calls_too(sandbox, tmp_path, monkeypatch):
    """🔴 被权限挡下的调用【必须】进轨迹 —— 那是最该记的一类事件。

    钉的是注册【顺序】:trigger_hook 遇到第一个非 None 就 return,后面的 hook 不跑;
    而 permission_hook 拦截时正是返回非 None。所以 trace_pre_hook 排在它后面的话,
    被拦的调用一条都记不到 —— 而且这种漏【完全静默】,轨迹看起来只是"少了几条"。
    🪝 安全事件不进审计,等于没有审计。
    """
    monkeypatch.setattr(sandbox, "TRACE_DIR", tmp_path / ".traces")
    monkeypatch.setattr(sandbox, "_trace_events", [])

    blocked = sandbox.trigger_hook("PreToolUse", "bash", {"command": "rm -rf /"})
    assert blocked is not None, "用例失效:这条命令本该被 DENY_LIST 拦下"
    sandbox.trigger_hook("PostToolUse", "bash", f"[Tool 'bash' blocked by hook: {blocked}]")

    calls = [e for e in sandbox._trace_events if e["kind"] == "tool_call"]
    assert calls, "被拦的调用没有进轨迹 —— 检查 trace_pre_hook 的注册顺序"
    results = [e for e in sandbox._trace_events if e["kind"] == "tool_result"]
    assert results[-1]["blocked"] is True
    assert results[-1]["ok"] is False


def test_trace_separates_blocked_from_error(sandbox, tmp_path, monkeypatch):
    """「被安全闸挡下」和「工具自己坏了」不能混成同一个 ok=False。

    前者是闸门生效(好事),后者是代码有病(坏事)。混成一个数,归因时就再也分不开。
    """
    monkeypatch.setattr(sandbox, "TRACE_DIR", tmp_path / ".traces")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    sandbox.trigger_hook("PostToolUse", "read_file", "Error: file not found")
    sandbox.trigger_hook("PostToolUse", "bash", "[Tool 'bash' blocked by hook: denied]")
    err, blk = [e for e in sandbox._trace_events if e["kind"] == "tool_result"]
    assert (err["ok"], err["blocked"]) == (False, False), "工具报错被误标成 blocked"
    assert (blk["ok"], blk["blocked"]) == (False, True), "被拦没被标成 blocked"


def test_trace_carries_its_own_provenance(sandbox, tmp_path, monkeypatch):
    """轨迹要能自证出身:哪一版 harness、哪个模型、哪组开关。

    2026-08-16 的教训:模型名没记进 results.jsonl,当事人纠正「不是 grok4.6 是 composer2.5」
    时,数据自己说不清。轨迹不许重犯。
    🪝 一份数据离开当时的对话之后,还能不能说清自己是怎么产生的?
    """
    import json

    monkeypatch.setattr(sandbox, "TRACE_DIR", tmp_path / ".traces")
    monkeypatch.setattr(sandbox, "_trace_events", [{"kind": "user", "text": "hi", "t": 0}])
    sandbox.trigger_hook("Stop", [])

    files = list((tmp_path / ".traces").glob("*.json"))
    assert files, "Stop 之后轨迹没落盘"
    t = json.loads(files[0].read_text(encoding="utf-8"))
    for key in ("harness", "model", "memory_mode", "todo_mode", "sandbox_mode"):
        assert key in t, f"轨迹缺少出身字段 {key}"
