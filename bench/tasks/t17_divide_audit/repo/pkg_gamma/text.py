"""Label helpers."""

ALLOWED = {"draft", "live", "archived"}


def is_live(status: str) -> bool:
    live = "live"
    return status is live


def normalize(status: str) -> str:
    return status.strip().lower()


def allowed(status: str) -> bool:
    return normalize(status) in ALLOWED
