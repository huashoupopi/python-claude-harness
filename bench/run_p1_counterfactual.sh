#!/bin/bash
# P1 反事实实跑。四题共用：同一 commit / TIMEOUT / max_turns=25 / SANDBOX=1 / mem_self / 3 trial。
# t17/t18/t19：on + off。t20：只跑 on（强制 compact 单臂，不构造 off）。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
MANIFEST=bench/runs/_p1_manifest.txt
mkdir -p bench/runs
{
  echo "commit=$(git rev-parse HEAD)"
  echo "commit_short=$(git rev-parse --short HEAD)"
  echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "TIMEOUT=${BENCH_TIMEOUT:-900}"
  echo "WORKERS=${BENCH_WORKERS:-4}"
  echo "TRIALS=3"
  echo "CONFIG=mem_self"
  echo "SANDBOX=1"
  echo "max_turns=25"
} | tee "$MANIFEST"

run_batch() {
  local name=$1
  shift
  local log="bench/runs/_p1_${name}.log"
  echo "===== START $name $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" | tee -a "$MANIFEST"
  echo "cmd: $*" | tee -a "$MANIFEST"
  # 不把 disable 开关留在环境里串批
  env -u BENCH_DISABLE_TOOLS -u BENCH_DISABLE_MCP "$@" \
    uv run python bench/run_bench.py 2>&1 | tee "$log"
  grep "输出 " "$log" | tail -1 | tee -a "$MANIFEST"
  echo "===== END $name $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" | tee -a "$MANIFEST"
}

export BENCH_TRIALS=3
export BENCH_ONLY_CONFIGS=mem_self
export BENCH_WORKERS="${BENCH_WORKERS:-4}"
export BENCH_SANDBOX=1
export BENCH_TIMEOUT="${BENCH_TIMEOUT:-900}"

# ON：四题能力都开（t18/t20 的题目级开关走 bench.env）
run_batch on \
  env BENCH_ONLY_TASKS=t17_divide_audit,t18_house_style,t19_mcp_lookup,t20_two_phase

# OFF t17：摘掉 spawn_subagent
run_batch t17_off \
  env BENCH_ONLY_TASKS=t17_divide_audit BENCH_DISABLE_TOOLS=spawn_subagent

# OFF t18：摘掉 load_skill（bench.env 仍不拷 skills/）
run_batch t18_off \
  env BENCH_ONLY_TASKS=t18_house_style BENCH_DISABLE_TOOLS=load_skill

# OFF t19：MCP 连不上
run_batch t19_off \
  env BENCH_ONLY_TASKS=t19_mcp_lookup BENCH_DISABLE_MCP=1

echo "finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$MANIFEST"
