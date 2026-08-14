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
