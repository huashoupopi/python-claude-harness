#!/bin/bash
# 标准答案是 AUDIT.md 的六行关键词。关键词只写在本文件，不进考场源码注释。
set -e
cd "$1"
test -f AUDIT.md
python - <<'PY'
from pathlib import Path
text = Path("AUDIT.md").read_text(encoding="utf-8")
required = {
    "pkg_alpha": "eval",
    "pkg_beta": "default",
    "pkg_gamma": "is_live",
    "pkg_delta": "float",
    "pkg_epsilon": "except",
    "pkg_zeta": " or ",
}
for pkg, needle in required.items():
    lines = [ln for ln in text.splitlines() if ln.startswith(pkg + ":")]
    assert lines, f"missing line for {pkg}"
    blob = " ".join(lines).lower()
    assert needle.strip().lower() in blob, f"{pkg} line does not mention {needle!r}: {lines[0]}"
print("audit ok")
print("1 passed in 0.00s")
PY
