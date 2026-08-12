"""B 组:cron 校验器与匹配器。纯函数,不落盘,用 trunk 即可。

这组钉住的是「一个表达式能不能注册」和「到点了算不算命中」两件事。
T19 搬 cron 系统时这两条规则必须原样过来。
"""

from datetime import datetime


# ---------- validate_cron:能不能注册 ----------


def test_valid_expressions_pass(trunk):
    """合法表达式返回 None(None 表示无错误)。"""
    assert trunk.validate_cron("* * * * *") is None
    assert trunk.validate_cron("*/5 * * * *") is None
    assert trunk.validate_cron("30 9 1 6 1") is None
    assert trunk.validate_cron("0 9-17 * * *") is None
    assert trunk.validate_cron("0,15,30,45 * * * *") is None


def test_wrong_field_count_rejected(trunk):
    """cron 必须 5 段,多一段少一段都拒。"""
    assert "Expected 5 fields" in trunk.validate_cron("* * * *")
    assert "Expected 5 fields" in trunk.validate_cron("* * * * * *")


def test_out_of_bounds_rejected(trunk):
    """每段有自己的上下界:分 0-59 / 时 0-23 / 日 1-31 / 月 1-12 / 周 0-6。"""
    assert "minute" in trunk.validate_cron("60 * * * *")
    assert "hour" in trunk.validate_cron("* 24 * * *")
    assert "day-of-month" in trunk.validate_cron("* * 32 * *")
    assert "month" in trunk.validate_cron("* * * 13 *")
    assert "day-of-week" in trunk.validate_cron("* * * * 7")


def test_malformed_range_and_step_rejected(trunk):
    """范围起点不能大于终点;步长必须是正整数。"""
    assert "Range start > end" in trunk.validate_cron("10-5 * * * *")
    assert "Step must be > 0" in trunk.validate_cron("*/0 * * * *")
    assert "Invalid step" in trunk.validate_cron("*/abc * * * *")


# ---------- cron_matches:到点了算不算命中 ----------


def test_step_matches_on_multiples(trunk):
    """*/5 命中 0,5,10...,不命中 7。"""
    assert trunk.cron_matches("*/5 * * * *", datetime(2026, 8, 11, 10, 5))
    assert trunk.cron_matches("*/5 * * * *", datetime(2026, 8, 11, 10, 0))
    assert not trunk.cron_matches("*/5 * * * *", datetime(2026, 8, 11, 10, 7))


def test_dom_and_dow_are_or_not_and(trunk):
    """cron 的历史怪癖:日和周都指定时是 OR,不是 AND。

    "0 0 13 * 5" = 每月13号 或 每周五,不是「13号且是周五」。
    """
    friday = datetime(2026, 8, 14)  # 周五,不是13号
    the_13th = datetime(2026, 8, 13)  # 13号,周四
    assert trunk.cron_matches("0 0 13 * 5", friday.replace(hour=0, minute=0))
    assert trunk.cron_matches("0 0 13 * 5", the_13th.replace(hour=0, minute=0))


def test_field_count_mismatch_never_matches(trunk):
    """段数不对的表达式直接不命中,不抛异常。"""
    assert not trunk.cron_matches("* * *", datetime(2026, 8, 11, 10, 0))


# ---------- 🔴 已知不一致:校验器和匹配器意见相反 ----------


def test_known_inconsistency_step_with_comma(trunk):
    """`*/5,10` 匹配得上,却注册不进去——两个函数的分支顺序是反的。

    _cron_field_matches: 先查 ","  → split 成 ["*/5","10"] → 能匹配
    _validate_cron_field: 先查 "*/" → step_str="5,10" → isdigit() False → 拒

    现状如此,先钉住。T19 若统一了分支顺序,这条会红——那是提醒不是错误。
    """
    expr = "*/5,10 * * * *"
    assert trunk.cron_matches(expr, datetime(2026, 8, 11, 10, 10))  # 匹配器:能
    assert trunk.validate_cron(expr) is not None  # 校验器:不能
