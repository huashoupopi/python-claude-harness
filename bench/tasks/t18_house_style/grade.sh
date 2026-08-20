#!/bin/bash
# 行为测在考场内的 pytest；风格约定只写在本文件，不进考场。
# 否则模型读 test_*.py 就能绕过 load_skill。
set -e
cd "$1"
python - <<'PY'
from pathlib import Path
src = Path("widget.py").read_text(encoding="utf-8")
assert "def hs_normalize" in src, "missing hs_normalize (house-style names)"
assert "def hs_clip" in src, "missing hs_clip (house-style names)"
assert "class HouseError" in src, "missing HouseError"
assert "HSE:" in src, "HouseError messages must start with HSE:"
assert "# house-import-fence" in src, "missing exact import fence comment"
assert "def normalize(" not in src, "old public name normalize still present"
assert "def clip(" not in src, "old public name clip still present"
print("style ok")
PY
python -m pytest -q
