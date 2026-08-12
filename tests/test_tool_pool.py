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
    """没连 MCP 时,池子内容 == 内置注册表,但是一份拷贝不是同一个对象。"""
    monkeypatch.setattr(trunk, "mcp_clients", {})
    pool = trunk.assemble_tool_pool()
    assert pool.keys() == trunk.TOOL_REGISTRY.keys()
    assert pool is not trunk.TOOL_REGISTRY  # 拷贝,不然污染内置表


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
