#!/bin/bash
# 限额真值只写在本文件与主干 mock 里,不进考场。
set -e
cd "$1"
"${BENCH_PYTHON:-python}" - <<'PY'
from quota import allowed

assert allowed(18427) is True, "limit too low: 18427 must be allowed"
assert allowed(18428) is False, "limit too high: 18428 must be rejected"
assert allowed(0) is True
print("quota ok")
print("1 passed in 0.00s")
PY
