"""Summarize P1 on/off live runs. Read-only over bench/runs/."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
TOOL_RE = re.compile(r"\[tool call\] (\S+)")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def tool_calls_from_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return TOOL_RE.findall(log_path.read_text(encoding="utf-8", errors="replace"))


def compact_events(repo: Path) -> list[dict]:
    events = []
    traces = repo / ".traces"
    if traces.is_dir():
        for p in traces.glob("trace_*.json"):
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for e in payload.get("events") or []:
                if isinstance(e, dict) and e.get("kind") == "compact":
                    events.append(e)
    bench = repo / ".bench_trace.json"
    if not bench.exists():
        for p in repo.glob(".bench_trace_r*.json"):
            bench = p
            break
    return events


def findings(repo: Path) -> str | None:
    p = repo / "FINDINGS.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None


def unit_dir(run_dir: Path, row: dict) -> Path:
    return run_dir / row["config"] / row["task"] / f"repo{row['trial']}"


def logs_dir(run_dir: Path, row: dict) -> Path:
    return run_dir / row["config"] / row["task"] / f"logs{row['trial']}"


def summarize_row(run_dir: Path, row: dict, arm: str) -> dict:
    repo = unit_dir(run_dir, row)
    log = logs_dir(run_dir, row) / "agent.log"
    tools = tool_calls_from_log(log)
    compact = compact_events(repo)
    force = [e for e in compact if e.get("reason") == "force"]
    return {
        "arm": arm,
        "task": row["task"],
        "trial": row["trial"],
        "success": row.get("success"),
        "timed_out": row.get("timed_out"),
        "passed": row.get("passed"),
        "total": row.get("total"),
        "steps": row.get("steps"),
        "duration_s": row.get("duration_s"),
        "model": row.get("model"),
        "tokens_loop": row.get("tokens_loop"),
        "tokens_subagent": row.get("tokens_subagent"),
        "tools": tools,
        "spawn_subagent": tools.count("spawn_subagent"),
        "load_skill": tools.count("load_skill"),
        "connect_mcp": tools.count("connect_mcp"),
        "mcp_quota": sum(1 for t in tools if t.startswith("mcp__quota")),
        "force_compact": len(force),
        "findings": findings(repo),
    }


def main() -> None:
    manifest = RUNS / "_p1_manifest.txt"
    mapping: dict[str, Path] = {}
    current = None
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("===== START "):
                current = line.split()[2]
            if "输出 " in line and current:
                mapping[current] = Path(line.split("输出 ", 1)[1].strip())
    if not mapping:
        print("no run dirs in manifest", file=sys.stderr)
        sys.exit(1)

    by_task: dict[str, list[dict]] = defaultdict(list)
    for arm, run_dir in mapping.items():
        for row in load_jsonl(run_dir / "results.jsonl"):
            by_task[row["task"]].append(summarize_row(run_dir, row, arm))

    print(json.dumps({k: v for k, v in sorted(by_task.items())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
