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
