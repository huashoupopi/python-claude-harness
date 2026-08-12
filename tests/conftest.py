"""冒烟闸底座。

被测对象是 examples/22_trunk.py(21_mcp_real.py 的可 import 副本,21 保持课程原状当基准)。
文件名以数字开头不能 import,用 importlib 按路径装载。
22 已把模块级副作用(mkdir / cron 线程 / 会话初态)收进 ensure_dirs / start_cron_scheduler
/ init_session,所以这里装载它是干净的——不建目录、不起线程。
"""

import importlib.util
from pathlib import Path

import pytest

TRUNK = Path(__file__).parent.parent / "examples" / "22_trunk.py"


@pytest.fixture(scope="session")
def trunk():
    """装载主干模块。session 级:2003 行只装一次。"""
    spec = importlib.util.spec_from_file_location("trunk", TRUNK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def sandbox(trunk, tmp_path, monkeypatch):
    """把主干的运行期路径常量重指到 tmp_path,并建好目录。

    有效的原因:ensure_dirs / _task_path 等函数在**调用时**才查模块全局,
    所以 monkeypatch 换掉全局名就够,不必改函数签名。
    测完 tmp_path 自动清理,真仓库不留痕。
    """
    for name, dirname in (
        ("MEMORY_DIR", ".memory"),
        ("TASKS_DIR", ".tasks"),
        ("MAILBOX_DIR", ".mailboxes"),
        ("WORKTREES_DIR", ".worktrees"),
    ):
        monkeypatch.setattr(trunk, name, tmp_path / dirname)
    monkeypatch.setattr(trunk, "MEMORY_INDEX", trunk.MEMORY_DIR / "MEMORY.md")
    monkeypatch.setattr(trunk, "DURABLE_PATH", tmp_path / ".scheduled_tasks.json")
    trunk.ensure_dirs()
    return trunk
