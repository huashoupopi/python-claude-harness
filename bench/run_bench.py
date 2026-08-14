"""启动方式(必须 uv run,否则 grade.sh 里的 python 不在 PATH 上):
cd python-claude-harness && uv run python bench/run_bench.py"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()  # bench/ 自身,绝对路径地图的锚点
TASKS_DIR = HERE / "tasks"
RUNNER = HERE / "agent_runner.py"
SKILLS_SRC = HERE.parent / "skills"  # 技能库真身,发卷时要跟着走(见下面 ① 处)

run_dir = HERE / "runs" / time.strftime("run_%Y%m%d_%H%M%S")

TIMEOUT_S = 300

# 配置名 -> 这个臂要注入的环境变量
#
# 【2026-08-14 stage-2 改造】
# stage-1:一个臂 = 一个【散件文件】(03/09/10/11),靠给 runner 传文件名切换。
# stage-2:只有一个被测对象(22_trunk.py),臂靠【环境变量】切换 ——
#         主干里 MEMORY_MODE = os.getenv("MEMORY_MODE", "self"),模块级读一次。
#
# ⚠️ 值必须是 str:subprocess 的 env 只收字符串,传 True/None 会 TypeError。
# ⚠️ 主干对非法值是 fail loud(当场 raise ValueError),所以拼错臂名不会静默
#    退回默认值跑完一整轮才发现数据全废 —— 这就是那个 fail loud 在买的保险。
CONFIGS = {
    "mem_none": {"MEMORY_MODE": "none"},
    "mem_self": {"MEMORY_MODE": "self"},
    "mem_official": {"MEMORY_MODE": "official"},
}


def _as_text(x) -> str:
    """TimeoutExpired 上的 stdout/stderr 有三种形态:None / bytes / str。

    text=True 只保证【正常返回】那条路解码;超时是走异常抛出的,
    POSIX 上 _check_timeout 把还没解码的 bytes 直接塞进异常对象。
    """
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return x


for name, env_patch in CONFIGS.items():
    print(f"=== Running config: {name} ({env_patch}) ===")
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
            repo = run_dir / name / task_dir.name / f"repo{trial}"
            logs = run_dir / name / task_dir.name / f"logs{trial}"
            shutil.copytree(task_dir / "repo", repo)
            logs.mkdir(parents=True, exist_ok=True)

            # ① 技能库要跟着考场走 —— 主干里 SKILLS_DIR = Path.cwd() / "skills",
            #    而子进程的 cwd 是这份考场副本。不拷的话 _scan_skills() 一进门就
            #    return,SKILL_REGISTRY 空,system prompt 里那段技能清单变成
            #    "(no skills found)" —— 「技能」这一层在 bench 里等于没装。
            #    (2026-08-14 发现的第三个 bug:凡是跟着 cwd 走的东西,换考场就漂移)
            if SKILLS_SRC.exists():
                shutil.copytree(SKILLS_SRC, repo / "skills")

            # ⚠️ env 必须【基于 os.environ 复制】再打补丁。
            #    只传 {"MEMORY_MODE": ...} 会把 PATH / HOME 整个抹掉,
            #    子进程连解释器和 .venv 都找不到。
            env = os.environ.copy()
            env.update(env_patch)

            timed_out = False
            start_time = time.time()
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        str(task_dir.resolve() / "task.md"),
                    ],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_S,
                )
                duration = time.time() - start_time
                out, err = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as e:
                # 超时的轨迹恰恰是最该看的那一份 —— 它记着模型卡在哪里循环。
                # (2026-08-14 教训:success 会骗人、steps 会骗人,只有轨迹说实话。
                #  空转 15 步那次如果日志被丢了,这个 bug 至今还在。)
                print(f"Task {task_dir.name} timed out.")
                timed_out = True
                duration = time.time() - start_time
                out, err = _as_text(e.stdout), _as_text(e.stderr)

            steps = out.count("[tool call]")
            with open(logs / "agent.log", "w", encoding="utf-8") as f:
                f.write(out)
                f.write("\n\n=== STDERR ===\n\n")
                f.write(err)

            # 超时的也照样判分:被 kill 之前可能已经把代码改对了,
            # 「没跑完」和「没做对」是两件事,分开记(timed_out 字段)。
            proc2 = subprocess.run(
                ["bash", str(task_dir / "grade.sh"), str(repo)],
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
                "timed_out": timed_out,
            }
            results.append(record)
            with open(run_dir / "results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f" Trial {trial} - Task {task_dir.name}: "
                f"{'PASSED' if success else 'FAILED'}"
                f"{' (TIMEOUT)' if timed_out else ''}, "
                f"steps: {steps}, duration: {duration:.2f}s"
            )
        M = len(results)
        N = sum(1 for r in results if r["success"])
        print(f"{N}/{M} passed")

# 成绩单: 打印每题一行(题名 过没过 步数 耗时),最后一行 "N/M passed"
