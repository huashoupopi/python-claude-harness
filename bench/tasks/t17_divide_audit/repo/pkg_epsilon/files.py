"""Read helpers."""

from pathlib import Path


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except:
        return ""


def exists(path: str) -> bool:
    return Path(path).exists()
