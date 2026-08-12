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
    """context 不是手填的,是从真实状态派生:工具表 + 工作目录 + 记忆文件。"""
    ctx = sandbox.update_context({}, [])
    assert ctx["enabled_tools"] == list(sandbox.TOOL_REGISTRY.keys())
    assert ctx["memories"] == ""  # sandbox 里 MEMORY.md 还不存在

    sandbox.MEMORY_INDEX.write_text("记住:用中文")
    ctx2 = sandbox.update_context({}, [])
    assert ctx2["memories"] == "记住:用中文"
