"""Slices result sets into pages, using whatever page size is configured."""

import settings


def page(items, index):
    size = settings.get("page_size")
    start = index * size
    return items[start : start + size]


def page_count(items):
    size = settings.get("page_size")
    return (len(items) + size - 1) // size
