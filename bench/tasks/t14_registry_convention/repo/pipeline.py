"""Runs every registered check against a value and reports the failures."""

import builtin_checks  # noqa: F401  -- the import is what fills the registry
from registry import REGISTRY


def run(value):
    return sorted(
        meta["code"] for meta in REGISTRY.values() if not meta["fn"](value)
    )


def describe():
    return {name: meta["code"] for name, meta in sorted(REGISTRY.items())}
