"""Cached access to the effective configuration.

loader.load() walks the whole layered config, so the result is held here and
handed out to every caller instead of being rebuilt on each lookup.
"""

import loader

_CACHE = None


def get(name):
    global _CACHE
    if _CACHE is None:
        _CACHE = loader.load()
    return _CACHE[name]
