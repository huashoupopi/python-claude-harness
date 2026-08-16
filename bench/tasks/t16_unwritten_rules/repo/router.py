"""Event dispatch.

Loads whatever bootstrap says is enabled and hands each event to its handler.
"""

import importlib

import bootstrap

MODULE_PREFIX = "handler_"


def _load(event):
    if event not in bootstrap.ENABLED:
        raise LookupError(f"no handler serving {event!r}")
    try:
        return importlib.import_module(f"{MODULE_PREFIX}{event}")
    except ModuleNotFoundError as exc:
        raise LookupError(f"no handler serving {event!r}") from exc


def dispatch(event, payload):
    module = _load(event)
    result = module.handle(payload)
    # 下游的告警、重试、审计都读这两个字段,缺一个整条链路就断。
    return {"event": event, "status": result["status"], "detail": result["detail"]}


def serving():
    return sorted(bootstrap.ENABLED)
