"""Drop a leading prefix if it is present."""


def strip_prefix(value, prefix):
    if prefix in value:
        return value[len(prefix):]
    return value
