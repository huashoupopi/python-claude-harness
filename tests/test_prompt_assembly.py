"""C 组:system prompt 组装。

T19 要把 12_prompt_assembly.py 的能力并进主干,这块必动,先钉住现状。
关键行为:①常驻段永远在 ②memory 段按 context 条件加载
        ③工具清单报的是当轮池子(消融纯度) ④不许返回过期 prompt。

【2026-08-14】原本还有第 ③ 条「缓存按 context 内容失效」和一个清缓存的 autouse fixture,
随进程内缓存一起删了 —— 那个缓存的 key 只覆盖 context、不覆盖工具池,而修正 key 的
成本 ≈ 重新组装的成本,留着是负收益。原三条缓存测试由末尾那条 staleness 测试接替:
它钉的是【行为】(工具池变了就得拿到新 prompt),而不是【实现】(有没有缓存命中)。
🪝 测行为不测实现 —— 这样将来若真需要一个正确的缓存,这条测试不用改。
"""


def test_always_on_sections_present(trunk):
    """identity / tools / workspace 三段无条件加载。

    🔴 2026-08-14 行为变更:tools 那段从 PROMPT_SECTIONS 字典里【搬出来了】,
    改成在 assemble_system_prompt 里当场现算。两个原因:
      ① 模块级字典里的 f-string 是【导入时拍照】—— 之后工具池怎么变都不跟着变
         (MCP 是运行时 connect 进来的,永远进不了那句话)
      ② 它必须报【当轮工具池】而不是 TOOL_REGISTRY 全集
    所以这里不能再拿字典里的值来比,改为验证「清单在 prompt 里且内容是当轮池子」。
    """
    prompt = trunk.assemble_system_prompt({})
    assert trunk.PROMPT_SECTIONS["identity"] in prompt
    assert trunk.PROMPT_SECTIONS["workspace"] in prompt
    assert "Available tools:" in prompt
    for name in trunk.assemble_tool_pool():
        assert name in prompt, f"当轮池子里的 {name} 没出现在 prompt 里"
    # tools 不该再留在字典里 —— 留着就会有人再从那儿取,拍照问题原地复活
    assert "tools" not in trunk.PROMPT_SECTIONS


def test_tools_section_reports_pool_not_registry(trunk, monkeypatch):
    """🔴 工具清单报的必须是当轮池子,不是注册表全集 —— 这是消融臂的纯度。

    2026-08-14 修的 bug:原来是 f"...{TOOL_REGISTRY.keys()}",于是 TODO_MODE=none
    那一臂,工具池里没有 todo_write、prompt 里却还写着它。
    模型收到「文字说有、接口里没有」的矛盾(tools= 参数才是硬约束,它调不到,
    所以不会崩,但 none 臂里 todo 的痕迹没擦干净 —— 消融臂的定义是「这层完全不存在」)。
    """
    monkeypatch.setattr(trunk, "TODO_MODE", "none")
    assert "todo_write" not in trunk.assemble_system_prompt({})
    monkeypatch.setattr(trunk, "TODO_MODE", "nudge")
    assert "todo_write" in trunk.assemble_system_prompt({})
    # 全集里始终有 —— 证明差异来自过滤,而不是注册表被改坏了
    assert "todo_write" in trunk.TOOL_REGISTRY


def test_memory_section_is_conditional(trunk):
    """memories 为空时不加 memory 段,有内容时才加——按 context 条件装配。"""
    without = trunk.assemble_system_prompt({"memories": ""})
    assert "Relevant memories:" not in without

    with_mem = trunk.assemble_system_prompt({"memories": "用户偏好中文"})
    assert "Relevant memories:\n用户偏好中文" in with_mem


def test_context_change_is_reflected(trunk):
    """context 变了,prompt 跟着变。

    (2026-08-14:这条从原 test_cache_invalidates_on_context_change 改写而来 ——
     原版断言的是 `first is not second`「不是同一个对象」,那测的是【缓存实现】;
     缓存删掉后,该断言的是【内容确实跟着变了】这个行为。)
    """
    without = trunk.get_system_prompt({"memories": ""})
    with_mem = trunk.get_system_prompt({"memories": "新记忆"})
    assert "新记忆" not in without
    assert "新记忆" in with_mem


def test_update_context_derives_from_real_state(sandbox):
    """context 不是手填的,是从真实状态派生:工具表 + 工作目录。

    ⚠️ 2026-08-13 行为变更:memories 那一格不再直接读 MEMORY.md,
    改成按 MEMORY_MODE 走 load_memories(会调 LLM 挑相关记忆)。
    记忆相关的断言拆到下面两条,用 monkeypatch 挡住真实请求。
    """
    ctx = sandbox.update_context({}, [])
    # 🔴 2026-08-14:enabled_tools 报的是【当轮工具池】,不是 TOOL_REGISTRY 全集。
    # 字段名叫 enabled_tools 就该是「这一轮真的开着的」;报全集则 context 不完整 ——
    # 而 context 的定义是「prompt 全部输入的快照」。
    assert ctx["enabled_tools"] == list(sandbox.assemble_tool_pool().keys())
    assert ctx["workspace"] == str(sandbox.WORKDIR)


def test_context_tracks_ablation_switches(sandbox, monkeypatch):
    """context 必须跟着消融开关走 —— 否则「快照」这个说法就是假的。"""
    monkeypatch.setattr(sandbox, "TODO_MODE", "nudge")
    assert "todo_write" in sandbox.update_context({}, [])["enabled_tools"]
    monkeypatch.setattr(sandbox, "TODO_MODE", "none")
    assert "todo_write" not in sandbox.update_context({}, [])["enabled_tools"]


def test_memory_mode_controls_injection(sandbox, monkeypatch):
    """三模式各自的注入行为:只有 self 往 context 里放记忆。

    none      模型完全不知道有记忆
    self      系统挑好塞给它
    official  模型自己调 memory 工具去看,系统不代劳
    """
    monkeypatch.setattr(sandbox, "load_memories", lambda msgs: "FAKE_MEMORY")
    msgs = [{"role": "user", "content": "x"}]
    for mode, expected in (("none", ""), ("official", ""), ("self", "FAKE_MEMORY")):
        monkeypatch.setattr(sandbox, "MEMORY_MODE", mode)
        monkeypatch.setattr(sandbox, "_memories_cache", None)  # 每种模式重新挑
        ctx = sandbox.update_context({}, msgs)
        assert ctx["memories"] == expected, f"MEMORY_MODE={mode}"


def test_memories_selected_once_per_turn(sandbox, monkeypatch):
    """🔴 缓存守卫:一次用户输入只挑一次记忆,不是每轮都挑。

    update_context 有三个调用点,其中一个在 agent_loop 循环内。
    没有 _memories_cache 的话,一轮对话最多触发 26 次额外 LLM 请求 ——
    既烧钱,也让 stage-1 的 self_memory 那组数据不可比。
    这条测试守的就是那个缓存。
    """
    calls = []

    def fake_load(msgs):
        calls.append(msgs)
        return "M"

    monkeypatch.setattr(sandbox, "load_memories", fake_load)
    monkeypatch.setattr(sandbox, "MEMORY_MODE", "self")
    monkeypatch.setattr(sandbox, "_memories_cache", None)

    for _ in range(5):
        sandbox.update_context({}, [{"role": "user", "content": "x"}])

    assert len(calls) == 1, f"挑了 {len(calls)} 次,应该只挑 1 次"


def test_prompt_follows_tool_pool_without_staleness(trunk, monkeypatch):
    """🔴 工具池一变,下一次取到的 system prompt 必须跟着变。

    2026-08-14:原来 get_system_prompt 有个进程内缓存,key 只由 context 决定。
    但 prompt 的真实输入是【两个】:context 和当轮工具池。
    context 没变、工具池变了 → 缓存命中 → 返回过期的 prompt。
    🪝 缓存 key 必须覆盖它实际依赖的全部输入。
    """
    ctx = {"memories": ""}
    monkeypatch.setattr(trunk, "TODO_MODE", "nudge")
    assert "todo_write" in trunk.get_system_prompt(ctx)
    monkeypatch.setattr(trunk, "TODO_MODE", "none")
    # context 一模一样,只有工具池变了
    assert "todo_write" not in trunk.get_system_prompt(ctx)
