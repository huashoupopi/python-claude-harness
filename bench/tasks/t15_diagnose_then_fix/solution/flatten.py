"""Flatten one level of nesting."""


def flatten(rows):
    out = []
    for row in rows:
        out.extend(row)
    return out
