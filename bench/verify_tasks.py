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

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()
TASKS_DIR = HERE / "tasks"
DELETIONS = "_deletions.txt"


def grade(task_dir: Path, repo: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        ["bash", str(task_dir / "grade.sh"), str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
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
            shutil.copy2(f, repo / f.name)


failures = []
for task_dir in sorted(TASKS_DIR.iterdir()):
    if not task_dir.is_dir():
        continue
    name = task_dir.name
    sol_dir = task_dir / "solution"

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        shutil.copytree(task_dir / "repo", repo)
        red_ok, red_msg = grade(task_dir, repo)
        red_ok = not red_ok  # 原题【必须失败】

    if not sol_dir.is_dir():
        print(f"{name:24s} 红:{'✅' if red_ok else '❌'}  绿:⚠️ 无 solution/,验不了")
        if not red_ok:
            failures.append(f"{name}: 原题本来就是绿的 —— {red_msg}")
        continue

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        shutil.copytree(task_dir / "repo", repo)
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
