"""F 组:两道闸门——worktree 名校验(安全)、后台执行判定(调度)。

都是纯函数。worktree 那条是路径穿越的第一道防线,T19 搬 worktree 系统时不能丢。
"""


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
    """模型显式说要后台跑就后台跑,不再看启发式。"""
    assert trunk.should_run_background("bash", {"command": "ls", "run_in_background": True})


def test_heuristic_catches_slow_commands(trunk):
    """没显式说时看关键词:install/build/test/pytest 这类判定为慢操作。"""
    assert trunk.should_run_background("bash", {"command": "npm install"})
    assert trunk.should_run_background("bash", {"command": "uv run pytest -q"})
    assert trunk.should_run_background("bash", {"command": "docker build ."})
    assert not trunk.should_run_background("bash", {"command": "ls -la"})


def test_heuristic_only_applies_to_bash(trunk):
    """非 bash 工具一律前台——read_file 再慢也不该被丢到后台。

    注意副作用:命令里出现 "test" 就算慢,所以 `cat test.py` 会被误判。
    现状如此,钉住;T19 若改进判定,这条会红。
    """
    assert not trunk.is_slow_operation("read_file", {"command": "npm install"})
    assert trunk.is_slow_operation("bash", {"command": "cat test.py"})  # 误判,现状
