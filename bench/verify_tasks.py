"""出题双头验:每道题必须【原题红 + 标准答案绿】。

    cd python-claude-harness && uv run python bench/verify_tasks.py

为什么两头都要验(2026-08-11 stage-1 立的纪律,吃过两次亏):
    只验红   → 题目可能【无解】或答案根本过不了(判分口径有缺陷时尤其常见:
               浮点 == 冤枉正确实现、裸 assert 无 test_ 函数导致 pytest 收集 0 项退出码 5)
    只验绿   → 题目可能【本来就是绿的】,agent 什么都不做也 PASS,这一题白出

目录约定:
    tasks/<题名>/repo/       考卷(会被 run_bench 拷进考场)
    tasks/<题名>/solution/   标准答案,覆盖到 repo 副本之上;【不进考场】
                             特殊文件 _deletions.txt: 每行一个要删除的文件(答案含"删掉某文件"时用)
    tasks/<题名>/grade.sh    判分,第一个参数 = 考场路径
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from taskmeta import is_diagnostic

TASKS_DIR = HERE / "tasks"
# 🔴 2026-08-15:发卷/判分一律排除字节码缓存。
# 踩过的坑:copytree 把原卷的 __pycache__ 一起拷进考场,而 shutil.copy2 覆盖 .py 时
# 【保留原 mtime】;Python 判断 .pyc 是否有效看的是「记录的 (mtime,size) 与 .py 是否一致」,
# 而 solution 与 repo 的同名文件是同一个脚本同秒生成、连字节数都一样
# (`len(v) == 10` vs `len(v) == 11`) → 检查通过 → 加载的是【带 bug 的旧字节码】,
# 于是【标准答案被判失败】。dry-run 与 verify_tasks 全中招。
# 🪝 冒烟闸自己也会被污染,它同样需要被验证。
# 🪝 同族:代码依赖了「当前环境恰好有/没有某个东西」(ensure_dirs 缺 parents 靠别处建好目录、
#    load_dotenv 靠 cwd 恰好能往上找到 .env)。
IGNORE_CACHES = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


DELETIONS = "_deletions.txt"

# pytest 汇总行末尾的 " in 0.02s" —— 比较稳定性时要先抹掉,它每次都不一样
_STRIP_TIME = re.compile(r"\s+in\s+[\d.]+s\s*$")


# 判分脚本里的解释器不靠 PATH 猜:调用方知道自己跑在哪个 Python 上,直接传过去。
# 不传时回落到 `python` —— 容器内考场(python:3.13-slim)和 `uv run` 下都有,行为不变。
# 起因:绕过 uv 直接跑 pytest 时,grade.sh 报 `python: command not found`(exit 127),
# 而同仓对 docker 依赖是有守卫会 skip 的(test_sandbox.py::_image_ready),这里没有。
GRADE_ENV = {**os.environ, "BENCH_PYTHON": sys.executable}


def grade(task_dir: Path, repo: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        ["bash", str(task_dir / "grade.sh"), str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
        env=GRADE_ENV,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, (tail[-1] if tail else "(no output)")


def apply_solution(sol_dir: Path, repo: Path):
    for f in sol_dir.iterdir():
        if f.name == DELETIONS:
            for line in f.read_text(encoding="utf-8").splitlines():
                target = repo / line.strip()
                if line.strip() and target.exists():
                    target.unlink()
        else:
            shutil.copy(f, repo / f.name)


failures: list[str] = []
for task_dir in sorted(TASKS_DIR.iterdir()):
    if not task_dir.is_dir():
        continue
    name = task_dir.name
    if is_diagnostic(task_dir):
        print(f"{name:24s} ⏭ diagnostic_only，跳过双头验")
        continue
    sol_dir = task_dir / "solution"

    # 🔴 原题连判 REPEATS 次:每次都是【新进程】,所以哈希种子不同。
    # 2026-08-16 抓到的真事:t15 的 dedupe 用例只有 3 个元素,而被测实现是
    # list(set(...)) —— set 顺序取决于字符串哈希,Python 每进程随机。
    # 3 个元素 6 种排列,【约 1/6 的概率蒙对】,实测 30 次蒙对 5 次。
    # 后果不是「数字不稳」这么轻:它让【没修 bug 的实现有时能过关】。
    # 🪝 一条有概率自己变绿的测试,比没有这条测试更糟。
    # 单跑一次是抓不到的 —— 抓它的唯一办法就是多跑几次看结果一不一样。
    # 📌 CI 里设 VERIFY_REPEATS=1:连判 5 次抓的是 flaky,而 flaky 靠的是
    #    「每个进程哈希种子不同」—— 本地跑 5 次和 CI 跑 5 次是同一件事,CI 多跑纯属重复。
    #    CI 要的是另一件价值:pytest 完全不看 bench/tasks/,题库被改坏没有任何东西会发现。
    #    那件事跑 1 次就拿到了。
    REPEATS = int(os.getenv("VERIFY_REPEATS", "5"))
    reds = []
    for _ in range(REPEATS):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            shutil.copytree(task_dir / "repo", repo, ignore=IGNORE_CACHES)
            reds.append(grade(task_dir, repo))
    red_ok = not reds[0][0]  # 原题【必须失败】
    red_msg = reds[0][1]
    # ⚠️ 比之前要先把【耗时】抹掉:pytest 那行末尾是 "... in 0.02s",
    #    每次都不一样。第一版直接比整行,当场把 t05/t11 误判成不稳定 ——
    #    🪝 新装的闸门自己也要先验一遍,否则它第一次响就是误报,
    #       而误报的闸门用两次就没人信了(同族:冒烟闸自己也会被污染)。
    seen = {_STRIP_TIME.sub("", m) for _, m in reds}
    if len(seen) > 1:
        failures.append(
            f"{name}: 🔴 原题结果不稳定 —— 跑 {REPEATS} 次出现了 {len(seen)} 种结果 {sorted(seen)}。"
            "\n        多半是测试依赖了不确定的东西(set/dict 顺序、时间、随机数)。"
            "\n        这种题会让【没修好的实现有时也能过关】,数据不可用。"
        )

    if not sol_dir.is_dir():
        print(f"{name:24s} 红:{'✅' if red_ok else '❌'}  绿:⚠️ 无 solution/,验不了")
        if not red_ok:
            failures.append(f"{name}: 原题本来就是绿的 —— {red_msg}")
        continue

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        shutil.copytree(task_dir / "repo", repo, ignore=IGNORE_CACHES)
        apply_solution(sol_dir, repo)
        green_ok, green_msg = grade(task_dir, repo)

    # 原题红了【几条】也要看:L2 题的意义就在「多点」,只红 1 条说明没达到设计意图
    print(
        f"{name:24s} 红:{'✅' if red_ok else '❌'}  绿:{'✅' if green_ok else '❌'}  "
        f"原题[{red_msg}]  答案[{green_msg}]"
    )
    if not red_ok:
        failures.append(f"{name}: 原题本来就是绿的 —— agent 什么都不做也 PASS")
    if not green_ok:
        failures.append(f"{name}: 标准答案过不了 —— {green_msg}")

print()
if failures:
    print(f"❌ {len(failures)} 处不合格:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("✅ 全部题目双头验通过(原题红 + 标准答案绿)")
