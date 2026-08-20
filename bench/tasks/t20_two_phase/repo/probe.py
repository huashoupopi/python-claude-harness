"""One-shot probe: print the chosen adapter, then consume entropy.bin."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

NAMES = ("adapter_7f3a", "adapter_b91c", "adapter_e20d")
ENTROPY = Path("entropy.bin")


def main() -> int:
    if not ENTROPY.exists():
        print("ALREADY_PROBED", file=sys.stderr)
        return 1
    data = ENTROPY.read_bytes()
    ENTROPY.unlink()
    idx = int(hashlib.sha256(data).hexdigest()[:8], 16) % len(NAMES)
    print(f"CHOSEN={NAMES[idx]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
