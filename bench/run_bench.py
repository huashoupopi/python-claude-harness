"""启动方式(必须 uv run,否则 grade.sh 与 agent 手里的 python 都不在 PATH 上):

    cd python-claude-harness && uv run python bench/run_bench.py

⚠️ 「必须 uv run」不是客套话 —— 2026-08-14 实测:改用 .venv/bin/python 直接起,
   agent 的 bash 里 `python` 变成系统 Python(没装 pytest),模型在 pytest 上空转 12 轮。
   同一份 harness、同一道题,换个启动方式就从 4 轮做完变成 16 轮挣扎。

环境变量:
    BENCH_WORKERS=4    并发度。steps 对并发免疫,duration 会受影响(见 record 里的 workers 字段)
    BENCH_TRIALS=3     每个(臂,题)重复次数
    BENCH_DRY_RUN=1    冒烟模式:不起 agent、不花钱,直接把 solution 拷进考场当"完美考生",
                       几秒钟跑完全链路 —— 用来验发卷/判分/登分/并发本身有没有 bug
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent.resolve()  # bench/ 自身,绝对路径地图的锚点
TASKS_DIR = HERE / "tasks"
RUNNER = HERE / "agent_runner.py"
SKILLS_SRC = HERE.parent / "skills"  # 技能库真身,发卷时要跟着走(见 ① 处)

run_dir = HERE / "runs" / time.strftime("run_%Y%m%d_%H%M%S")

TIMEOUT_S = 300
WORKERS = int(os.getenv("BENCH_WORKERS", "4"))
TRIALS = int(os.getenv("BENCH_TRIALS", "3"))
DRY_RUN = os.getenv("BENCH_DRY_RUN") == "1"
DELETIONS = "_deletions.txt"  # solution/ 里的特殊文件,见 verify_tasks.py

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
#
# 【轴式消融】一次只动一个轴,别的固定在主干默认值。
# 两个轴都动的话 3×3=9 臂,乘上题数与重复次数根本跑不完;而且成绩变了也分不清
# 是谁的功劳(混淆变量)。两轴共用「全默认」那一格(mem_self == todo_nudge),
# 所以 3+3-1 = 5 个配置。
#
# 📌 每个臂都【显式写全两个变量】,一个都不靠默认值兜底。
#    理由是 2026-08-14 那个默认值 bug 的延伸:靠默认的话,读 CONFIGS 的人看不出
#    另一个轴处在什么状态;更糟的是哪天默认值一改,历史数据的含义就跟着变了,
#    而 results.jsonl 里只记了臂名。—— 实验配置必须自我说明。
CONFIGS = {
    # memory 轴(todo 固定在默认 nudge)
    "mem_none": {"MEMORY_MODE": "none", "TODO_MODE": "nudge"},
    "mem_self": {"MEMORY_MODE": "self", "TODO_MODE": "nudge"},  # ← 两轴共用的原点
    "mem_official": {"MEMORY_MODE": "official", "TODO_MODE": "nudge"},
    # todo 轴(memory 固定在默认 self);todo_nudge 就是上面的 mem_self,不重复跑
    "todo_none": {"MEMORY_MODE": "self", "TODO_MODE": "none"},
    "todo_tool": {"MEMORY_MODE": "self", "TODO_MODE": "tool"},
}

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


_io_lock = threading.Lock()  # 保护 results.jsonl 与 print —— 并发下两者都会交错


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


def apply_solution(sol_dir: Path, repo: Path):
    """把标准答案覆盖到考场上 —— 只在 DRY_RUN 里用,模拟一个"完美考生"。"""
    for f in sol_dir.iterdir():
        if f.name == DELETIONS:
            for line in f.read_text(encoding="utf-8").splitlines():
                target = repo / line.strip()
                if line.strip() and target.exists():
                    target.unlink()
        else:
            shutil.copy(f, repo / f.name)


def restore_tests(task_dir: Path, repo: Path) -> list[str]:
    """判分前把测试文件恢复成【原卷】,并报告考生动过哪些。

    🔴 为什么必须做(2026-08-14):
        run_bench 把 tasks/<题>/repo/ 整个 copytree 到考场,测试文件【也在里面】;
        agent 的 cwd 就是考场,它对考场有完整写权限;
        grade.sh 是 `cd 考场 && pytest`,收集的正是考场里那份测试。
        → 判卷用的卷子和考生手里的卷子是同一份。
        agent 一个 edit_file 把 assert 改成 assert True,什么都不干也 PASS。

    task.md 里写着「不许修改 test_ 开头的文件」—— 那是【说给模型听的一句请求】,
    没有任何代码在检查它。规则写在 prompt 里 ≠ 规则被执行
    (同族:write_file 有路径检查,但 bash echo > 越界路径 就绕过去了)。
    而且 agent 不需要有恶意 —— 「我觉得这测试写错了,改一下」是真实开发里的常见行为。

    做法 = 收卷时用监考手里那份判:
        ① 先【记录】动过哪些(作弊要可见,不能记成一次成功)
        ② 删掉考场里所有 test_*.py(含 agent 自己新建的 —— 它们不该参与判分,
           否则 agent 写个失败的辅助测试就会冤枉一个正确的实现)
        ③ 把原卷的 test_*.py 拷回去
    """
    originals = list((task_dir / "repo").glob("test_*.py"))
    tampered = sorted(
        p.name
        for p in originals
        if not (repo / p.name).exists() or (repo / p.name).read_bytes() != p.read_bytes()
    )
    extra = sorted(
        p.name for p in repo.glob("test_*.py") if p.name not in {o.name for o in originals}
    )
    for stale in repo.glob("test_*.py"):
        stale.unlink()
    for orig in originals:
        shutil.copy(orig, repo / orig.name)
    return tampered + [f"+{name}" for name in extra]  # "+" 前缀 = agent 新建的


PYTEST_TAIL = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)")


def parse_pytest(text: str) -> dict:
    """从 pytest -q 的最后一行抠出条数。

    🔴 为什么要这个:原来 success 只是 `returncode == 0`,一个布尔。
    但题目有 4-8 条测试,「过了 7/8」和「过了 2/8」被记成同一个 False ——
    成倍的信息量白白丢掉,而这两种失败在归因上完全是两回事
    (差一点做完 vs 根本没做对)。
    """
    out = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    lines = text.strip().splitlines()
    if not lines:
        return out
    for n, word in PYTEST_TAIL.findall(lines[-1]):
        out["error" if word.startswith("error") else word] = int(n)
    return out


IGNORE_PARTS = {"skills", "__pycache__"}


def _repo_files(root: Path) -> dict:
    """考场里属于「题目」的文件。排除 harness 自己拉的屎(.memory/.tasks/…)与技能库。"""
    files = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(x.startswith(".") or x in IGNORE_PARTS for x in rel.parts):
            continue
        try:
            files[str(rel)] = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            files[str(rel)] = ["<binary>"]
    return files


def diff_stats(task_dir: Path, repo: Path) -> dict:
    """考场 vs 原卷改了多少 —— 两个都 PASS 的臂,改 3 行和改 80 行不是一回事。

    在 restore_tests 之后算,所以这里只反映【源码】改动;
    对测试文件动手脚单独记在 tests_tampered,两件事不要混。
    """
    before, after = _repo_files(task_dir / "repo"), _repo_files(repo)
    added_lines = removed_lines = 0
    for rel in set(before) | set(after):
        for line in difflib.unified_diff(before.get(rel, []), after.get(rel, []), n=0):
            if line.startswith("+") and not line.startswith("+++"):
                added_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed_lines += 1
    return {
        "files_touched": sum(
            1 for rel in set(before) | set(after) if before.get(rel) != after.get(rel)
        ),
        "files_created": len(set(after) - set(before)),
        "files_deleted": len(set(before) - set(after)),
        "lines_added": added_lines,
        "lines_removed": removed_lines,
    }


def expected_total(task_dir: Path) -> int:
    """这道题满分是几条 —— 拿标准答案实跑一次得到,不靠手写常量。

    🪝 手写常量会腐烂:题目加一条测试而常量没跟着改,通过率就悄悄失真。
    让它从「答案跑一遍」派生,加测试时自动跟上。
    """
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        shutil.copytree(task_dir / "repo", repo, ignore=IGNORE_CACHES)
        sol = task_dir / "solution"
        if sol.is_dir():
            apply_solution(sol, repo)
        p = subprocess.run(
            ["bash", str(task_dir / "grade.sh"), str(repo)],
            capture_output=True,
            text=True,
        )
        return parse_pytest(p.stdout + p.stderr)["passed"]


def run_one(name: str, env_patch: dict, trial: int, task_dir: Path) -> dict:
    """一个考试单元:发卷 → 监考 → 收卷 → 登分。并发安全:只在末尾用锁写共享资源。"""
    repo = run_dir / name / task_dir.name / f"repo{trial}"
    logs = run_dir / name / task_dir.name / f"logs{trial}"
    shutil.copytree(task_dir / "repo", repo, ignore=IGNORE_CACHES)
    logs.mkdir(parents=True, exist_ok=True)

    # ① 技能库要跟着考场走 —— 主干里 SKILLS_DIR = Path.cwd() / "skills",
    #    而子进程的 cwd 是这份考场副本。不拷的话 _scan_skills() 一进门就
    #    return,SKILL_REGISTRY 空,system prompt 里那段技能清单变成
    #    "(no skills found)" —— 「技能」这一层在 bench 里等于没装。
    #    (2026-08-14 发现的第三个 bug:凡是跟着 cwd 走的东西,换考场就漂移)
    if SKILLS_SRC.exists():
        shutil.copytree(SKILLS_SRC, repo / "skills")

    timed_out = False
    start_time = time.time()

    if DRY_RUN:
        # 冒烟模式:跳过 agent,直接上标准答案。验的是 bench 自己,不是模型。
        apply_solution(task_dir / "solution", repo)
        out, err = "[dry-run] solution applied\n", ""
        duration = time.time() - start_time
    else:
        # ⚠️ env 必须【基于 os.environ 复制】再打补丁。
        #    只传 {"MEMORY_MODE": ...} 会把 PATH / HOME 整个抹掉,
        #    子进程连解释器和 .venv 都找不到。
        env = os.environ.copy()
        env.update(env_patch)
        try:
            proc = subprocess.run(
                [sys.executable, str(RUNNER), str(task_dir.resolve() / "task.md")],
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
            timed_out = True
            duration = time.time() - start_time
            out, err = _as_text(e.stdout), _as_text(e.stderr)

    steps = out.count("[tool call]")
    (logs / "agent.log").write_text(
        out + "\n\n=== STDERR ===\n\n" + err, encoding="utf-8"
    )

    tampered = restore_tests(task_dir, repo)

    # 超时的也照样判分:被 kill 之前可能已经把代码改对了,
    # 「没跑完」和「没做对」是两件事,分开记(timed_out 字段)。
    proc2 = subprocess.run(
        ["bash", str(task_dir / "grade.sh"), str(repo)], capture_output=True, text=True
    )
    counts = parse_pytest(proc2.stdout + proc2.stderr)
    total = EXPECTED[task_dir.name]

    # agent_runner 在【发请求那一刻】记下的轨迹(reminder 注入次数、上下文规模…);
    # 超时被 kill 的那次可能没写成,所以要容错 —— 不能因为拿不到轨迹就丢掉整条记录。
    trace = {}
    trace_path = repo / ".bench_trace.json"
    if trace_path.exists():
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            trace = {"parse_error": True}
    turns = trace.get("turns", [])

    record = {
        "config": name,
        "trial": trial,
        "task": task_dir.name,
        "success": proc2.returncode == 0,
        # 🔴 不再只有布尔:「过了 7/8」和「过了 2/8」在归因上是两回事
        "passed": counts["passed"],
        "total": total,
        "pass_rate": round(counts["passed"] / total, 3) if total else 0.0,
        "collect_error": counts["error"] > 0,  # 收集就挂了 ≠ 断言失败
        "steps": steps,
        "duration_s": duration,
        "timed_out": timed_out,
        "tests_tampered": tampered,  # 非空 = 考生动过卷子(已按原卷判,但行为要留痕)
        "workers": WORKERS,  # duration 的解释需要它:并发下这个数不可跨批比较
        # ── 改动范围:两个都 PASS 的臂,改 3 行和改 80 行不是一回事 ──
        **diff_stats(task_dir, repo),
        # ── 看不见的注入:stdout 里没有 reminder / system prompt / 记忆 ──
        "turns": len(turns),
        "sys_prompt_chars": trace.get("system_prompt_chars", 0),
        "reminders": turns[-1]["reminders"] if turns else 0,
        "todo_calls": turns[-1]["todo_calls"] if turns else 0,
        "memory_injected": any(t.get("memory_injected") for t in turns),
        "ctx_chars_max": max((t["chars"] for t in turns), default=0),
        # ── 真实成本:消融的分母。之前只能拿 steps 当近似 ──
        # 两本账分开:loop=主循环(流式),aux=附加层(记忆挑/提取/合并、compact 摘要,全是非流式)。
        # 🪝 合成一个总数就再也拆不开了,而「这一层自己烧了多少」正是消融要回答的问题。
        "tokens_loop": trace.get("tokens_loop", {}).get("total", 0),
        "tokens_loop_prompt": trace.get("tokens_loop", {}).get("prompt", 0),
        "tokens_aux": trace.get("tokens_aux", {}).get("total", 0),
        "aux_calls": trace.get("tokens_aux", {}).get("calls", 0),
        "tokens_total": (
            trace.get("tokens_loop", {}).get("total", 0)
            + trace.get("tokens_aux", {}).get("total", 0)
        ),
    }

    with _io_lock:
        with open(run_dir / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"  [{name}/{task_dir.name}/t{trial}] "
            f"{'PASSED' if record['success'] else 'FAILED'}"
            f"{' (TIMEOUT)' if timed_out else ''}"
            f"{' ⚠️ TAMPERED: ' + ','.join(tampered) if tampered else ''}"
            f"  {counts['passed']}/{total}  steps={steps} {duration:.0f}s",
            flush=True,
        )
    return record


units = [
    (name, env_patch, trial, task_dir)
    for name, env_patch in CONFIGS.items()
    for trial in range(1, TRIALS + 1)
    for task_dir in sorted(TASKS_DIR.iterdir())
    if task_dir.is_dir()
]

run_dir.mkdir(parents=True, exist_ok=True)
# 每题满分几条 —— 由标准答案实跑派生,不写常量(题目加测试时自动跟上)
EXPECTED = {
    td.name: expected_total(td) for td in sorted(TASKS_DIR.iterdir()) if td.is_dir()
}
print(
    f"{'[DRY RUN] ' if DRY_RUN else ''}"
    f"{len(units)} 个单元 = {len(CONFIGS)} 臂 × {TRIALS} trial × "
    f"{len(units) // (len(CONFIGS) * TRIALS)} 题,并发 {WORKERS},输出 {run_dir}"
)

results = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futures = [ex.submit(run_one, *u) for u in units]
    for fut in as_completed(futures):
        results.append(fut.result())

# 成绩单:每个臂一行 N/M,外加需要人看一眼的异常
print(f"\n=== 成绩单 ({time.time() - t0:.0f}s) ===")
for name in CONFIGS:
    rows = [r for r in results if r["config"] == name]
    if not rows:
        continue
    n = len(rows)
    full = sum(1 for r in rows if r["success"])
    avg = lambda k: sum(r[k] for r in rows) / n  # noqa: E731
    print(
        f"  {name:14s} 全过 {full}/{n}   "
        f"平均通过率 {avg('pass_rate'):.2f}   "
        f"steps {avg('steps'):.1f}   "
        f"改动 {avg('lines_added') + avg('lines_removed'):.0f} 行/"
        f"{avg('files_touched'):.1f} 文件   "
        f"催 {avg('reminders'):.1f} 次 → todo {avg('todo_calls'):.1f} 次   "
        f"token {avg('tokens_total') / 1000:.1f}k"
        f"(主 {avg('tokens_loop') / 1000:.1f}k + 附加层 {avg('tokens_aux') / 1000:.1f}k"
        f"/{avg('aux_calls'):.1f} 次调用)"
    )

bad = [r for r in results if r["tests_tampered"]]
slow = [r for r in results if r["timed_out"]]
if bad:
    print(f"\n⚠️ {len(bad)} 次考生动过测试文件(已按原卷判分,但这些轨迹要人看):")
    for r in bad:
        print(f"   {r['config']}/{r['task']}/t{r['trial']}: {r['tests_tampered']}")
if slow:
    print(f"\n⚠️ {len(slow)} 次超时:")
    for r in slow:
        print(f"   {r['config']}/{r['task']}/t{r['trial']}")
