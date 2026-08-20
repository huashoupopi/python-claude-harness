"""从一次 diagnostic 跑批生成 P2 报告。先证据后指标。只写「至少检测到」。

    uv run python bench/diagnose_hallucination.py bench/runs/run_YYYYmmdd_HHMMSS
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent.resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from hallucination import analyze_unit, load_events_from_repo  # noqa: E402


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main(run_dir: Path) -> str:
    rows = [
        json.loads(l)
        for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    diag = [r for r in rows if r.get("diagnostic_only")]
    if not diag:
        raise SystemExit(f"{run_dir} 里没有 diagnostic_only 记录")

    units = []
    for r in diag:
        repo = run_dir / r["config"] / r["task"] / f"repo{r['trial']}"
        events, final = load_events_from_repo(repo)
        if not final:
            final = ""
        analysis = analyze_unit(events, final, workdir=str(repo.resolve()))
        units.append({"row": r, "repo": str(repo), "analysis": analysis})

    by_task: dict[str, list] = {}
    for u in units:
        by_task.setdefault(u["row"]["task"], []).append(u)

    models = sorted({r["row"].get("model") for r in units})
    configs = sorted({r["row"].get("config") for r in units})
    timeouts = [u for u in units if u["row"].get("timed_out")]

    lines = []
    a = lines.append
    a("# P2 幻觉诊断报告")
    a("")
    a("这三个指标是**检测下界**，不是完整幻觉率。")
    a("结论只适用于：**当前模型、当前 harness、当前这两道题**的探索性分布。")
    a("⛔ 不得写成「本 agent 的幻觉率」。")
    a("")
    a("## 口径")
    a("")
    a(f"- 批次：`{run_dir.name}`")
    a(f"- commit：`{git_sha()}`")
    a(f"- 模型：{models}")
    a(f"- 配置（单一）：{configs}")
    a(f"- 每题次数：{ {t: len(v) for t, v in by_task.items()} }")
    a(f"- TIMEOUT 单列：{len(timeouts)} 条（不并入「未瞎编」）")
    a(f"- workers：见 results 的 `workers` 字段")
    a("")

    for task, group in sorted(by_task.items()):
        a(f"## {task}")
        a("")
        n = len(group)
        timed = [u for u in group if u["row"].get("timed_out")]
        live = [u for u in group if not u["row"].get("timed_out")]
        a(f"- 单元 {n}，其中超时 {len(timed)}（单列，下面计数只用未超时的 {len(live)}）")
        hall_n = sum(u["analysis"]["hallucinated_files"]["at_least"] for u in live)
        comp_n = sum(u["analysis"]["false_completion"]["at_least"] for u in live)
        try_n = sum(u["analysis"]["escape_attempts"]["at_least_tried"] for u in live)
        blk_n = sum(u["analysis"]["escape_attempts"]["at_least_blocked"] for u in live)
        suc_n = sum(
            u["analysis"]["escape_attempts"]["at_least_succeeded_looking"] for u in live
        )
        extra = Counter()
        for u in live:
            extra.update(u["analysis"]["extra_files"])
        a(f"- **至少检测到** hallucinated_files **{hall_n}** 次（条，跨 trial 合计）")
        a(f"- **至少检测到** false_completion **{comp_n}** 次")
        a(
            f"- **至少检测到** escape 尝试 **{try_n}** / 拦截 **{blk_n}** / 看起来成功 **{suc_n}**"
        )
        a(f"- 快照里多出来的文件（原始证据，不是三指标之一）：{dict(extra)}")
        a("")
        a("### 取证（每 trial 一条）")
        a("")
        for u in sorted(live + timed, key=lambda x: x["row"]["trial"]):
            r = u["row"]
            an = u["analysis"]
            tag = "TIMEOUT" if r.get("timed_out") else ("ok-fail" if not r.get("success") else "success")
            a(
                f"- t{r['trial']} {tag} steps={r.get('steps')} "
                f"hall={an['hallucinated_files']['hallucinated_files'][:8]} "
                f"complete={an['false_completion']['at_least']} "
                f"escape_try={an['escape_attempts']['at_least_tried']} "
                f"snaps={an['n_file_snapshots']}"
            )
            fa = (an["final_assistant"] or "").replace("\n", " ")[:240]
            a(f"  最终回答摘录：{fa!r}")
            if an["false_completion"]["matches"]:
                a(f"  完成声明命中：{an['false_completion']['matches'][:3]}")
            if an["escape_attempts"]["tried"]:
                a(f"  越界尝试：{an['escape_attempts']['tried'][:3]}")
            if an["escape_attempts"]["blocked"]:
                a(f"  拦截：{an['escape_attempts']['blocked'][:3]}")
        a("")

    a("## 已知缺陷")
    a("")
    a("- hallucinated_files：变量拼路径抓不到；创建后又删除的不算幻觉（快照序列已排除）")
    a("- false_completion：句式表要靠人扩充；否定词离得远会漏")
    a("- escape_attempts：不用 grep 日志；间接访问会漏；「看起来成功」≠ 证明读到了考场外")
    a("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        runs = sorted((HERE / "runs").glob("run_*"), reverse=True)
        if not runs:
            raise SystemExit("usage: diagnose_hallucination.py <run_dir>")
        target = runs[0]
    else:
        target = Path(sys.argv[1])
    text = main(target)
    out = HERE / "P2_HALLUCINATION_NOTES.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out}", file=sys.stderr)
