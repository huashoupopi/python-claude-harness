"""C 组:system prompt 组装与缓存。

T19 要把 12_prompt_assembly.py 的能力并进主干,这块必动,先钉住现状。
关键行为:①常驻段永远在 ②memory 段按 context 条件加载 ③缓存按 context 内容失效。
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_prompt_cache(trunk):
    """get_system_prompt 用两个模块级全局做缓存,测试之间必须清干净,否则互相污染。"""
    trunk._last_context_key = None
    trunk._last_prompt = None
    yield
    trunk._last_context_key = None
    trunk._last_prompt = None


def test_always_on_sections_present(trunk):
    """identity / tools / workspace 三段无条件加载。"""
    prompt = trunk.assemble_system_prompt({})
    assert trunk.PROMPT_SECTIONS["identity"] in prompt
    assert trunk.PROMPT_SECTIONS["tools"] in prompt
    assert trunk.PROMPT_SECTIONS["workspace"] in prompt


def test_memory_section_is_conditional(trunk):
    """memories 为空时不加 memory 段,有内容时才加——按 context 条件装配。"""
    without = trunk.assemble_system_prompt({"memories": ""})
    assert "Relevant memories:" not in without

    with_mem = trunk.assemble_system_prompt({"memories": "用户偏好中文"})
    assert "Relevant memories:\n用户偏好中文" in with_mem


def test_cache_hits_on_identical_context(trunk):
    """同一个 context 连问两次,第二次走缓存,返回的是同一个对象。"""
    ctx = {"memories": "x", "workspace": "/tmp"}
    first = trunk.get_system_prompt(ctx)
    second = trunk.get_system_prompt(ctx)
    assert first is second  # 同一对象,不是重新拼的


def test_cache_invalidates_on_context_change(trunk):
    """context 内容变了就重拼。缓存 key 是 json 序列化,不是 hash()。"""
    first = trunk.get_system_prompt({"memories": ""})
    second = trunk.get_system_prompt({"memories": "新记忆"})
    assert first is not second
    assert "新记忆" in second


def test_cache_key_ignores_dict_ordering(trunk):
    """key 用 sort_keys=True,所以键序不同但内容相同的 context 应命中同一份缓存。"""
    first = trunk.get_system_prompt({"a": 1, "b": 2})
    second = trunk.get_system_prompt({"b": 2, "a": 1})
    assert first is second


def test_update_context_derives_from_real_state(sandbox):
    """context 不是手填的,是从真实状态派生:工具表 + 工作目录。

    ⚠️ 2026-08-13 行为变更:memories 那一格不再直接读 MEMORY.md,
    改成按 MEMORY_MODE 走 load_memories(会调 LLM 挑相关记忆)。
    记忆相关的断言拆到下面两条,用 monkeypatch 挡住真实请求。
    """
    ctx = sandbox.update_context({}, [])
    assert ctx["enabled_tools"] == list(sandbox.TOOL_REGISTRY.keys())
    assert ctx["workspace"] == str(sandbox.WORKDIR)


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
