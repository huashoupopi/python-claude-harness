"""题目级标记。独立模块，避免 import run_bench（它会建 runs/ 目录）。"""

from pathlib import Path

DIAGNOSTIC_MARKER = "diagnostic_only"


def is_diagnostic(task_dir: Path) -> bool:
    return (Path(task_dir) / DIAGNOSTIC_MARKER).is_file()
