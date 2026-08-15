"""The checks that ship with the pipeline."""

from registry import register


@register("not_empty", code="E101")
def not_empty(value):
    return value != ""


@register("is_ascii", code="E102")
def is_ascii(value):
    return all(ord(char) < 128 for char in value)
