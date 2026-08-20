#!/bin/bash
# 选中的适配器由 entropy.bin 的 sha256 决定，真值只写在本文件。
# FINDINGS.md 必须存在且等于这个名字 —— 没有外化即失败。
set -e
cd "$1"
test -f FINDINGS.md
python - <<'PY'
from pathlib import Path
import importlib.util

chosen = Path("FINDINGS.md").read_text(encoding="utf-8").strip()
assert chosen == "adapter_e20d", f"FINDINGS.md has {chosen!r}, expected adapter_e20d"

path = Path(f"{chosen}.py")
spec = importlib.util.spec_from_file_location("chosen", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert mod.scale(7) == 14, f"{chosen}.scale(7) == {mod.scale(7)}, want 14"
assert mod.scale(0) == 0
print("two-phase ok")
print("1 passed in 0.00s")
PY
