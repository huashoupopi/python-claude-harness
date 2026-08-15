"""Split a sequence into fixed-size chunks."""


def chunk(items, size):
    return [items[i : i + size] for i in range(0, len(items), size + 1)]
