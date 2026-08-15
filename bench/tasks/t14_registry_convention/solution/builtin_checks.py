"""The checks that ship with the pipeline."""

from registry import register


@register("not_empty", code="E101")
def not_empty(value):
    return value != ""


@register("is_ascii", code="E102")
def is_ascii(value):
    return all(ord(char) < 128 for char in value)


@register("min_length", code="E103")
def min_length(value):
    return len(value) >= 3


@register("no_whitespace", code="E104")
def no_whitespace(value):
    return not any(char.isspace() for char in value)
