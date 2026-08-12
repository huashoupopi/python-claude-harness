"""启动方式(必须 uv run,否则 grade.sh 里的 python 不在 PATH 上):
cd python-claude-harness && uv run python bench/run_bench.py"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()  # bench/ 自身,绝对路径地图的锚点
TASKS_DIR = HERE / "tasks"
RUNNER = HERE / "agent_runner.py"

run_dir = HERE / "runs" / time.strftime("run_%Y%m%d_%H%M%S")

# 配置名 -> 被测文件。短名进目录与 JSONL,文件名传给 runner
# （2026-08-11 由 AI 代改:纯机械映射,不涉及判分/消融逻辑）
CONFIGS = {
    "baseline": "03_multi_tool_loop.py",
    "compact": "09_compact_loop.py",
    "self_memory": "10_memory_loop.py",
    "official_memory": "11_memory_tool.py",
}

for name, config in CONFIGS.items():
    print(f"=== Running config: {name} ({config}) ===")
    for trial in range(1, 4):  # 每题跑三次
        print(f"=== Trial {trial} ===")
        results = []
        run_dir.mkdir(parents=True, exist_ok=True)
        for task_dir in sorted(TASKS_DIR.iterdir()):
            if not task_dir.is_dir():
                continue
            # ① 发卷: 把 task_dir/repo 拷到 run_dir/题名/repo
            # ② 监考: 子进程跑 RUNNER,cwd=考场副本,超时 300;记开始结束时间
            # ③ 收卷: 子进程跑 grade.sh,第一个参数=考场副本路径
            # ④ 登分: 拼一条 record 字典,append 进 results,并追加写进 run_dir/results.jsonl
            #    record 至少有: task / success / steps / duration_s
            shutil.copytree(
                task_dir / "repo", run_dir / name / task_dir.name / f"repo{trial}"
            )
            (run_dir / name / task_dir.name / f"logs{trial}").mkdir(
                parents=True, exist_ok=True
            )
            success = False
            try:
                start_time = time.time()
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        str(task_dir.resolve() / "task.md"),
                        config,
                    ],
                    cwd=run_dir / name / task_dir.name / f"repo{trial}",
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                duration = time.time() - start_time
                steps = proc.stdout.count("[tool call]")
                with open(
                    run_dir / name / task_dir.name / f"logs{trial}" / "agent.log",
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(proc.stdout)
                    f.write("\n\n=== STDERR ===\n\n")
                    f.write(proc.stderr)
            except subprocess.TimeoutExpired:
                print(f"Task {task_dir.name} timed out.")
                success = False
                duration = 300
                steps = 0
            proc2 = subprocess.run(
                [
                    "bash",
                    str(task_dir / "grade.sh"),
                    str(run_dir / name / task_dir.name / f"repo{trial}"),
                ],
                capture_output=True,
                text=True,
            )
            success = proc2.returncode == 0
            record = {
                "config": name,
                "trial": trial,
                "task": task_dir.name,
                "success": success,
                "steps": steps,
                "duration_s": duration,
            }
            results.append(record)
            with open(run_dir / "results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f" Trial {trial} - Task {task_dir.name}: {'PASSED' if success else 'FAILED'}, steps: {steps}, duration: {duration:.2f}s"
            )
        M = len(results)
        N = sum(1 for r in results if r["success"])
        print(f"{N}/{M} passed")

# 成绩单: 打印每题一行(题名 过没过 步数 耗时),最后一行 "N/M passed"
