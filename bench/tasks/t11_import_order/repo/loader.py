"""Builds the effective configuration: defaults <- config file <- environment.

Reading the file and re-parsing every key is not free, which is why callers
are expected to go through settings.get() rather than calling load() directly.
"""

import os

DEFAULTS = {
    "page_size": 20,
    "retry_limit": 3,
    "timeout_s": 30,
}

ENV_PREFIX = "APP_"

load_count = 0


def load():
    global load_count
    load_count += 1

    values = dict(DEFAULTS)

    path = os.environ.get("APP_CONFIG_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, raw = line.split("=", 1)
                name = name.strip()
                if name in values:
                    values[name] = int(raw.strip())

    for name in DEFAULTS:
        env_name = ENV_PREFIX + name.upper()
        if env_name in os.environ:
            values[name] = int(os.environ[env_name])

    return values
