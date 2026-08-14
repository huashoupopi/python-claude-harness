"""D 组:工具池装配与 MCP 名字规整。

T19 每搬进一件工具都会动 TOOL_REGISTRY,这组钉住「池子怎么拼出来的」。
"""


def test_normalize_replaces_illegal_chars(trunk):
    """MCP 服务器名可能带斜杠、点、@,这些进不了工具名,统一换成下划线。"""
    assert trunk.normalize_mcp_name("weather") == "weather"
    assert trunk.normalize_mcp_name("my.server") == "my_server"
    assert trunk.normalize_mcp_name("org/repo") == "org_repo"
    assert trunk.normalize_mcp_name("a@b c") == "a_b_c"
    assert trunk.normalize_mcp_name("keep-dash_ok") == "keep-dash_ok"  # -和_ 保留


def test_pool_without_mcp_equals_builtin_registry(trunk, monkeypatch):
    """没连 MCP 时,池子内容 == 内置注册表,但是一份拷贝不是同一个对象。

    ⚠️ 2026-08-13 起池子多了一条「按 MEMORY_MODE 过滤」的规则,
    非 official 模式会摘掉 memory 工具。这里固定成 official,
    才能验「池子 == 注册表」这个原本的意图。
    """
    monkeypatch.setattr(trunk, "mcp_clients", {})
    monkeypatch.setattr(trunk, "MEMORY_MODE", "official")
    pool = trunk.assemble_tool_pool()
    assert pool.keys() == trunk.TOOL_REGISTRY.keys()
    assert pool is not trunk.TOOL_REGISTRY  # 拷贝,不然污染内置表


def test_memory_tool_only_in_official_mode(trunk, monkeypatch):
    """🔴 memory 工具只在 official 模式下进池子 —— T22 消融轴的开关。

    与 test_memory_mode_controls_injection(test_prompt_assembly.py) 是一对:
        那条守【注入】那一侧(只有 self 往 context 里塞记忆)
        这条守【工具】那一侧(只有 official 把 memory 工具给模型)
    合起来才是完整的三模式契约:
        none      不注入 + 无工具   模型完全不知道有记忆
        self      注入   + 无工具   系统喂给它
        official  不注入 + 有工具   系统不喂,它自己去拿
    self 与 official 是二选一,不是叠加 —— 这正是消融要分离的变量。
    """
    monkeypatch.setattr(trunk, "mcp_clients", {})
    for mode, expected in (("none", False), ("self", False), ("official", True)):
        monkeypatch.setattr(trunk, "MEMORY_MODE", mode)
        pool = trunk.assemble_tool_pool()
        assert ("memory" in pool) is expected, f"MEMORY_MODE={mode}"


def test_mcp_tools_get_triple_underscore_prefix(trunk, monkeypatch):
    """MCP 工具进池时改名成 mcp__<服务器>__<工具>,防止和内置工具重名。"""

    class FakeClient:
        tools = [
            {
                "name": "get.weather",
                "description": "查天气",
                "inputSchema": {"type": "object"},
            }
        ]

        def call_tool(self, name, args):
            return "sunny"

    monkeypatch.setattr(trunk, "mcp_clients", {"my.server": FakeClient()})
    pool = trunk.assemble_tool_pool()

    assert "mcp__my_server__get_weather" in pool  # 两处名字都被 normalize 过
    assert "get.weather" not in pool  # 原名不进池

    entry = pool["mcp__my_server__get_weather"]
    assert entry.description == "查天气"
    assert entry.validator is None  # MCP 工具没有 pydantic 校验器,schema 来自服务器
