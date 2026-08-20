"""行为测试。故意不提任何编码规范 —— 规范只在技能里。"""

import widget


def test_normalize_collapses_and_lowers():
    assert widget.normalize("  Hello   World  ") == "hello world"


def test_clip_short_unchanged():
    assert widget.clip("ab", 5) == "ab"


def test_clip_long_adds_ellipsis():
    assert widget.clip("abcdef", 4) == "abc…"
