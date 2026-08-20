"""Feature flag bag."""


def with_defaults(opts: dict | None) -> dict:
    incoming = opts or {}
    incoming.setdefault("retries", 3)
    return incoming


def enabled(opts: dict, name: str) -> bool:
    return bool(opts.get(name))
