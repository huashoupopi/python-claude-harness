"""主干冒烟闸。被测:examples/22_trunk.py

先放两条底座自检,确认 conftest 的 trunk / sandbox fixture 能用。
真正的行为断言待补。
"""


def test_trunk_loads_and_registry_is_populated(trunk):
    """装载主干不炸,且工具注册表非空。"""
    assert trunk.TOOL_REGISTRY, "TOOL_REGISTRY 为空,说明工具没注册上"
    assert callable(trunk.agent_loop)


def test_sandbox_redirects_paths_away_from_repo(sandbox, tmp_path):
    """sandbox 把运行期目录指到 tmp_path,真仓库不留痕。"""
    assert sandbox.TASKS_DIR.parent == tmp_path
    assert sandbox.TASKS_DIR.is_dir()
