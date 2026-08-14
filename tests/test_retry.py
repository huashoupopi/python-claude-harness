"""错误恢复(with_retry)的单元测试。被测:examples/22_trunk.py

【为什么必须造假失败】
正常对话里 API 不会返回 429/529 —— with_retry 第一次调用就成功返回,
里面的退避、连败计数、模型切换【一次都不会执行】。
等线上真限流了才第一次运行,那时错了没人知道。

【最该守的是那条分工线】
with_retry 只吃【暂时性】故障(429/529),其他错误原样 raise 给外层。
守不住这条,prompt_too_long 这种非暂时性错误会被白白重试 10 次,
既拖慢又掩盖真正的问题。
"""

import pytest


class Boom(Exception):
    """非暂时性错误的替身。"""


def make_flaky(errors: list, result="OK"):
    """造一个「前 N 次抛指定异常,之后返回 result」的假函数。

    errors: 依次抛出的异常列表;抛完就返回 result。
    返回 (fn, calls) —— calls 记录每次调用,用来断言重试次数。
    """
    calls = []

    def fn():
        calls.append(len(calls))
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return result

    return fn, calls


# ---------------------------------------------------------------------------


def test_succeeds_without_retry(trunk):
    """一次就成功时,不该有任何重试。"""
    fn, calls = make_flaky([])
    state = trunk.RecoveryState()
    assert trunk.with_retry(fn, state) == "OK"
    assert len(calls) == 1


def test_retries_on_429(trunk, monkeypatch):
    """429 是暂时性故障 —— 退避后重试,最终成功。"""
    monkeypatch.setattr(trunk, "BASE_DELAY_MS", 1)  # 别让测试真睡几秒
    fn, calls = make_flaky([Exception("429 rate limit"), Exception("429 rate limit")])
    state = trunk.RecoveryState()
    assert trunk.with_retry(fn, state) == "OK"
    assert len(calls) == 3, "两次失败 + 一次成功"


def test_non_transient_error_is_not_retried(trunk):
    """🔴 分工线:非暂时性错误【原样抛出】,不重试。

    prompt_too_long / 参数错 / 鉴权失败 这类错,重试一百次也没用。
    硬重试只会拖慢,还掩盖真正的问题 —— 它们该交给外层的 except 处理。
    """
    fn, calls = make_flaky([Boom("something structurally wrong")] * 5)
    state = trunk.RecoveryState()
    with pytest.raises(Boom):
        trunk.with_retry(fn, state)
    assert len(calls) == 1, "只调了一次就抛出,没有重试"


def test_529_switches_to_fallback_model_after_three(trunk, monkeypatch):
    """🔴 529 连续三次 → 切换到 FALLBACK_MODEL。

    ⚠️ 生产配置里 FALLBACK_MODEL 与主 model 是同一个值(有意保留的现状),
    线上看不出切没切 —— 所以这条测试把它 monkeypatch 成一个假名字,
    这是【唯一】能验证切换路径通不通的办法。
    """
    monkeypatch.setattr(trunk, "BASE_DELAY_MS", 1)
    monkeypatch.setattr(trunk, "FALLBACK_MODEL", "FAKE-BACKUP-MODEL")

    state = trunk.RecoveryState()
    before = state.current_model

    fn, calls = make_flaky([Exception("529 overloaded")] * 3)
    assert trunk.with_retry(fn, state) == "OK"

    assert before != "FAKE-BACKUP-MODEL", "前置条件:一开始不是 fallback"
    assert state.current_model == "FAKE-BACKUP-MODEL", "连续 3 次 529 后该切过去"
    assert len(calls) == 4, "三次失败 + 一次成功"


def test_consecutive_529_resets_on_success(trunk, monkeypatch):
    """连败计数在成功后清零 —— 否则偶发的 529 会累积成误判。"""
    monkeypatch.setattr(trunk, "BASE_DELAY_MS", 1)
    state = trunk.RecoveryState()

    fn, _ = make_flaky([Exception("529 overloaded")])
    trunk.with_retry(fn, state)
    assert state.consecutive_529 == 0, "成功一次就该清零"


def test_retry_delay_grows_and_has_jitter(trunk):
    """退避是指数增长 + 随机抖动,且封顶。

    抖动是为了防【惊群】:一百个客户端同时被限流,
    如果都精确等 1/2/4 秒,它们会同步重试,把服务再打挂一次。
    """
    d0 = trunk.retry_delay(0)
    d3 = trunk.retry_delay(3)
    assert d3 > d0, "指数增长"
    assert trunk.retry_delay(99) <= 32 * 1.25 + 0.01, "封顶 32 秒(+ 最多 25% 抖动)"
    # 同一个 attempt 连算两次,结果不该完全一样(有随机成分)
    samples = {trunk.retry_delay(3) for _ in range(20)}
    assert len(samples) > 1, "没有抖动的话 20 次会得到同一个值"


def test_retry_after_takes_priority(trunk):
    """服务端给了 Retry-After 就听它的,不用自己算。"""
    assert trunk.retry_delay(5, retry_after=7) == 7
