#!/bin/bash
# 判的是「是否提到该包的违规类型」，中英都认。
# 2026-08-21 实跑事故：task.md 只要求「一句话」，旧版却要英文 "default" /
# "is_live"（变量名）/ "float"，把中文写对的报告全误杀。
# 同族：rework R01/R02 —— 判分词太窄，尺子在测自己。
set -e
cd "$1"
test -f AUDIT.md
"${BENCH_PYTHON:-python}" - <<'PY'
import re
from pathlib import Path

text = Path("AUDIT.md").read_text(encoding="utf-8")

# 每条 = 该包对应的违规类型。任一词命中即可。不要钉变量名、不要只认英文。
TYPES = {
    "pkg_alpha": [
        r"eval",
        r"exec",
    ],
    "pkg_beta": [
        r"default",
        r"默认参数",
        r"可变默认",
        r"mutable\s+default",
    ],
    "pkg_gamma": [
        r"(?<![a-z_])is(?![a-z_])",  # 运算符 is，不是 is_live 这种名字
        r"用\s*is",
        r"使用\s*is",
        r"identity",
    ],
    "pkg_delta": [
        r"float",
        r"浮点",
        r"floating",
    ],
    "pkg_epsilon": [
        r"except",
        r"裸\s*except",
        r"bare\s+except",
    ],
    "pkg_zeta": [
        r"\bor\b",
        r"假值",
        r"fals[ey]",
        r"falsey",
        r"or\s*\{\}",
        r"or\s*\[\]",
    ],
}

failures = []
for pkg, patterns in TYPES.items():
    lines = [ln for ln in text.splitlines() if ln.startswith(pkg + ":")]
    if not lines:
        failures.append(f"missing line for {pkg}")
        continue
    blob = " ".join(lines)
    if not any(re.search(p, blob, flags=re.IGNORECASE) for p in patterns):
        failures.append(f"{pkg} line does not mention the violation type: {lines[0]}")
assert not failures, "; ".join(failures)
print("audit ok")
print("1 passed in 0.00s")
PY
