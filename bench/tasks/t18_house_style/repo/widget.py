"""String helpers used by the rest of the service."""

import re
from typing import Any


def normalize(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed.lower()


def clip(text: str, width: int) -> str:
    if width < 1:
        raise ValueError("width must be >= 1")
    text = normalize(text)
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"
