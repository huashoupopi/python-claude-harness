"""The check registry.

Checks put themselves in here at import time. A check that nothing imports
never runs its decorator, and so does not exist as far as the pipeline is
concerned -- no error, it is simply absent.
"""

REGISTRY = {}


def register(name, code):
    def wrap(fn):
        taken = {meta["code"] for meta in REGISTRY.values()}
        if code in taken:
            raise ValueError(f"duplicate check code {code!r}")
        REGISTRY[name] = {"code": code, "fn": fn}
        return fn

    return wrap


def registered_names():
    return sorted(REGISTRY)
