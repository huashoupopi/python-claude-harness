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

# 🔴 2026-08-15 第一次正式跑的教训:原本 300s,实测超时 34/120 = 28%。
# 根因不是并行拖慢(实测并行下反而更快:t08 单跑 290s vs 并行中位 247s),
# 而是 timeout 【正好切在耗时分布的中间偏右】:未超时记录中位 181s、最大 297s ——
# 只留了 3s 余量。我明明单跑实测过 t08 要 290s,却把 timeout 留在 300s。
# 🪝 timeout 不是「防死循环的保险丝」那么简单 —— 它同时是一把【采样刀】:
#    切在分布内部,就会按「跑得快」这个与结果相关的规则筛掉一部分样本,
#    制造幸存者偏差。它必须设在分布【之外】,而不是分布【之内】。
TIMEOUT_S = int(os.getenv("BENCH_TIMEOUT", "900"))  # 3 倍中位数,留足尾部
WORKERS = int(os.getenv("BENCH_WORKERS", "4"))
TRIALS = int(os.getenv("BENCH_TRIALS", "3"))
DRY_RUN = os.getenv("BENCH_DRY_RUN") == "1"

# 只跑指定的题/臂(逗号分隔的【子串】匹配),留空 = 全跑。
# 用途是冒烟:新改了跑批逻辑,先花 2 分钟跑【一个】单元验代码路径,
# 而不是拿 225 个单元去赌一条从没执行过的分支。
# 📌 留空时行为与加这两个开关之前完全一致。
ONLY_TASKS = [s for s in os.getenv("BENCH_ONLY_TASKS", "").split(",") if s]
ONLY_CONFIGS = [s for s in os.getenv("BENCH_ONLY_CONFIGS", "").split(",") if s]

# 🔴 2026-08-15:演习和实弹的目录名要分开。
# 踩过的坑:两者混在 runs/ 里,而 analyze.py 默认取【最新那批】——
# 跑一次 dry-run 再跑分析,它就会去分析那批「完美考生」的假数据(全 8/8、steps 全 0)。
# 我自己就这么扫错过一次:ls -td 拿到最新目录,那是演习,还以为是全部。
# 🪝 名字取对了,逻辑就不用写过滤 —— analyze.py 的 glob("run_*") 自动跳过 dryrun_*。
run_dir = (
    HERE / "runs" / time.strftime(("dryrun_" if DRY_RUN else "run_") + "%Y%m%d_%H%M%S")
)
# 沙箱:默认【开】—— 与主干相反,这是有意的。
# 主干默认 off 是因为「加功能不能改变原有行为」;而 bench 是【无人看守的批量跑】,
# 2026-08-15 正是在这里被泄漏了 2/120(模型跑出考场 cat 了 solution/)。
# 🪝 同一个开关的默认值,在不同场合可以不同 —— 取决于「出事时有没有人在看」。
SANDBOX = os.getenv("BENCH_SANDBOX", "1") == "1"
DELETIONS = "_deletions.txt"  # solution/ 里的特殊文件,见 verify_tasks.py

# ✅ 2026-08-15 已修(实现在文件末尾「🔒 藏答案」那段):跑之前把 solution/ 移出项目树,
#    try/finally 还回去。以下是案发记录,保留。
# 2026-08-15 实测泄漏 2/120:模型做不出题(48 次 write_file 仍不过),掉头去查评测系统,
# 找到 bench/tasks/<题>/solution/ 直接 cat 了标准答案,日志里自己写着「已对齐 solution」。
# ⚠️ 建 solution/ 时明确想过「它不会进考场」—— 但那只防住了 copytree,没防住 bash。
# ⚠️ tests_tampered 那道闸完全没反应:它防的是【改测试】,模型走的是【抄答案】。
# 🪝 防作弊闸只挡住了你想到的那条路。黑名单挡不住越界,只有隔离能挡(→ T21 沙箱的
#    第二个实证动机;第一个是 T19 自测发现 bash 绕过 write_file 的路径检查)。
# 检测(扫日志里的路径字符串)保留作兜底,但它是下界不是准确值 —— 模型若用变量拼
# 路径就检测不到。

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


TRACE_NAME = ".bench_trace.json"  # agent_runner 写在 cwd(=考场)下的那份


def task_rounds(task_dir: Path) -> list[Path]:
    """这道题分几轮:task.md 是第一轮,round2.md / round3.md 依次往后。

    📌 没有 round2.md 的题就只有一轮 —— 也就是【加多轮之前代码本来的样子】。
       老 12 题一个字不用改,行为完全不变。
    """
    rounds = [task_dir / "task.md"]
    n = 2
    while (task_dir / f"round{n}.md").exists():
        rounds.append(task_dir / f"round{n}.md")
        n += 1
    return rounds


def read_trace(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"parse_error": True}


def merge_traces(traces: list[dict]) -> dict:
    """把多轮的轨迹并成一份。

    轮次之间是【独立进程】,每一轮的计数器都从 0 重新开始,所以合并规则按量的性质分:
        累积量(reminders / todo_calls / token)  取每轮末值再【相加】
        规模量(ctx_chars_max / sys_prompt_chars) 取【最大】—— 相加没有意义
        turns                                    首尾相接
    🪝 合成一个数就再也拆不开了 —— 和 tokens_loop/tokens_aux 分两本账是同一条道理。
    """
    traces = [t for t in traces if t]
    if not traces:
        return {}
    if len(traces) == 1:
        return traces[0]

    merged = {"turns": [], "rounds": len(traces)}
    # 被测对象:各轮当然是同一个,取第一份即可(拿不到就 None,不编)
    merged["model"] = next((t.get("model") for t in traces if t.get("model")), None)
    for key in ("tokens_loop", "tokens_aux"):
        merged[key] = {}
        for field in ("total", "prompt", "calls"):
            merged[key][field] = sum(t.get(key, {}).get(field, 0) for t in traces)
    merged["system_prompt_chars"] = max(
        (t.get("system_prompt_chars", 0) for t in traces), default=0
    )
    # reminders / todo_calls 记在每一轮【最后一个 turn】上,是那一轮的累计值。
    # 直接把各轮的 turns 接起来会让计数看着"倒退",所以这里把末值加总后重新钉在最后一格。
    reminders = sum((t["turns"][-1]["reminders"]) for t in traces if t.get("turns"))
    todo_calls = sum((t["turns"][-1]["todo_calls"]) for t in traces if t.get("turns"))
    for t in traces:
        merged["turns"].extend(t.get("turns", []))
    if merged["turns"]:
        merged["turns"][-1] = dict(merged["turns"][-1])
        merged["turns"][-1]["reminders"] = reminders
        merged["turns"][-1]["todo_calls"] = todo_calls
    return merged


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
        if not (repo / p.name).exists()
        or (repo / p.name).read_bytes() != p.read_bytes()
    )
    extra = sorted(
        p.name
        for p in repo.glob("test_*.py")
        if p.name not in {o.name for o in originals}
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
    # ⚠️ 定义在分支【外面】:dry-run 走的是另一条路,但下面的清理是两条路共用的。
    #    (第一版把它写在 else 里,dry-run 当场 UnboundLocalError —— 冒烟闸抓到的。)
    # 容器名由【这一方】决定 —— 子进程超时会被 kill,它的 finally 跑不到,
    # 收尸只能靠起它的人。名字带上臂/题/轮次,并行 4 路也不会撞。
    sbx_tag = f"{name}-{task_dir.name}-t{trial}".replace("_", "-")

    rounds = task_rounds(task_dir)
    out = err = ""

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
        if SANDBOX:
            env["SANDBOX_MODE"] = "docker"
            env["SANDBOX_NETWORK"] = "none"  # 无人看守的批量跑:断网防外泄
            env["SANDBOX_TAG"] = sbx_tag
        # 多轮题:每一轮 = 一个【独立进程】,messages 从零开始,
        # 靠留在考场里的 .memory 把上一轮的东西带过来 —— 这正是记忆轴要测的东西。
        # ⚠️ 每轮跑完立刻把轨迹改名存下来,否则下一轮会把它【覆盖掉】。
        for index, prompt_file in enumerate(rounds, start=1):
            try:
                proc = subprocess.run(
                    [sys.executable, str(RUNNER), str(prompt_file.resolve())],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_S,  # 每轮各自计时,不是全程共用一份
                )
                out += proc.stdout
                err += proc.stderr
            except subprocess.TimeoutExpired as e:
                # 超时的轨迹恰恰是最该看的那一份 —— 它记着模型卡在哪里循环。
                # (2026-08-14 教训:success 会骗人、steps 会骗人,只有轨迹说实话。
                #  空转 15 步那次如果日志被丢了,这个 bug 至今还在。)
                timed_out = True
                out += _as_text(e.stdout)
                err += _as_text(e.stderr)
            finally:
                live = repo / TRACE_NAME
                if live.exists():
                    live.rename(repo / f".bench_trace_r{index}.json")
            if timed_out:
                break  # 这一轮就没跑完,下一轮的起点已经不可信了
        duration = time.time() - start_time

    if SANDBOX:
        # 兜底收尸:正常退出时 agent_runner 已经拆过,这里是幂等的二次确认;
        # 超时被 kill 那次,这里是【唯一】会执行的清理。
        subprocess.run(
            ["docker", "rm", "-f", f"harness-sbx-{sbx_tag}"],
            capture_output=True,
            timeout=60,
        )

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
    # 多轮题:每轮的轨迹已经在跑完时改名存开了,这里按轮次读回来再合并。
    # 单轮题只会命中 .bench_trace_r1.json 一份,合并是恒等操作。
    # (TRACE_NAME 那份是没被改名的残留 —— 兜底也读一下,免得静默丢掉整条记录。)
    trace = merge_traces(
        [read_trace(repo / f".bench_trace_r{i}.json") for i in range(1, len(rounds) + 1)]
        or [read_trace(repo / TRACE_NAME)]
    ) or read_trace(repo / TRACE_NAME)
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
        # 🔴 被测对象记进每一条:换过端点的批次之间,数据要能自己说清是谁跑的
        "model": trace.get("model"),
        "steps": steps,
        "rounds": len(rounds),  # 多轮题的 steps/token 是全程累计,不跟单轮题直接比
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


def _wanted(text: str, patterns: list[str]) -> bool:
    return not patterns or any(p in text for p in patterns)


all_tasks = [td for td in sorted(TASKS_DIR.iterdir()) if td.is_dir()]
tasks = [td for td in all_tasks if _wanted(td.name, ONLY_TASKS)]
configs = {k: v for k, v in CONFIGS.items() if _wanted(k, ONLY_CONFIGS)}

units = [
    (name, env_patch, trial, task_dir)
    for name, env_patch in configs.items()
    for trial in range(1, TRIALS + 1)
    for task_dir in tasks
]
if not units:
    raise SystemExit(
        f"过滤之后一个单元都不剩 —— BENCH_ONLY_TASKS={ONLY_TASKS} "
        f"BENCH_ONLY_CONFIGS={ONLY_CONFIGS}\n"
        f"可选题目: {[t.name for t in all_tasks]}\n可选臂: {list(CONFIGS)}"
    )

run_dir.mkdir(parents=True, exist_ok=True)
# 每题满分几条 —— 由标准答案实跑派生,不写常量(题目加测试时自动跟上)
# ⚠️ 只给【这一轮真的要跑的】题算,否则冒烟一道题也要陪跑 15 次 pytest。
EXPECTED = {td.name: expected_total(td) for td in tasks}
print(
    f"{'[DRY RUN] ' if DRY_RUN else ''}"
    f"{len(units)} 个单元 = {len(configs)} 臂 × {TRIALS} trial × {len(tasks)} 题,"
    f"并发 {WORKERS},输出 {run_dir}"
    + (f"\n⚠️ 已过滤:题={ONLY_TASKS or '全部'} 臂={ONLY_CONFIGS or '全部'}"
       if ONLY_TASKS or ONLY_CONFIGS else "")
)

# 🔒 藏答案:跑之前把 solution/ 整个移出项目树,跑完还回去(泄漏的第二道锁,见 §61)。
# ⚠️ 必须在 EXPECTED 算完【之后】—— expected_total() 要跑一遍标准答案才知道满分几条。
# ⚠️ dry-run 不藏 —— 演习模式拿 solution 当"完美考生",藏了冒烟闸当场报废。
# ⚠️ 只在这里藏【一次】,不能写进 run_one:那是 4 路并发,会互相抢同一个目录。
# 📌 移到项目【外面】(/tmp)而不是换个角落:这道锁只在 BENCH_SANDBOX=0 时才起作用,
#    而那时模型的 bash 裸跑在宿主机上,cd .. 哪都能去 —— 藏在项目里挡不住会 find 的它。
#    丢了也不怕:21 个答案文件都在 git 里,git checkout 就回来。
# 📌 try 从【藏之前】就开始:搬到第 5 个出错的话,前 4 个也得还回去。
#    finally 照着 hidden 走(而不是照着"应该有 8 个"),所以搬了一半也能正确还原。
# 🔴 不写 except —— 跑批崩了数据就是残的,没有别的应对方式。
#    吞掉异常的后果是脚本继续往下跑,拿一批残缺 results 打出一张看起来正常的成绩单。
#    🪝 同族:keep_worktree 返回「已保留」而其实没有(2026-08-15 扫描)。工具返回
#       【假成功】比返回错误更糟 —— 它会被当成事实继续用下去。
hidden = []
try:
    if not DRY_RUN:
        stash = Path(tempfile.mkdtemp(prefix="bench-sol_"))
        for td in sorted(TASKS_DIR.iterdir()):
            sol = td / "solution"
            if sol.is_dir():
                dest = stash / td.name
                shutil.move(sol, dest)
                hidden.append([sol, dest])

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(run_one, *u) for u in units]
        for fut in as_completed(futures):
            results.append(fut.result())
finally:
    for sol, dest in hidden:
        shutil.move(dest, sol)

# 成绩单:每个臂一行 N/M,外加需要人看一眼的异常
print(f"\n=== 成绩单 ({time.time() - t0:.0f}s) ===")
for name in CONFIGS:
    rows = [r for r in results if r["config"] == name]
    if not rows:
        continue
    n = len(rows)
    full = sum(1 for r in rows if r["success"])
    avg = lambda k: sum(r[k] for r in rows) / n
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
