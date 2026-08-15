"""Cached access to the effective configuration.

loader.load() walks the whole layered config, so the result is held here and
handed out to every caller instead of being rebuilt on each lookup. The cache
is keyed on the inputs it was built from, so it is reused while those inputs
hold still and rebuilt as soon as they move.
"""

import os

import loader

_CACHE = None
_CACHE_KEY = None


def _inputs():
    relevant = {
        name: value
        for name, value in os.environ.items()
        if name.startswith(loader.ENV_PREFIX)
    }
    return tuple(sorted(relevant.items()))


def get(name):
    global _CACHE, _CACHE_KEY
    key = _inputs()
    if _CACHE is None or _CACHE_KEY != key:
        _CACHE = loader.load()
        _CACHE_KEY = key
    return _CACHE[name]
