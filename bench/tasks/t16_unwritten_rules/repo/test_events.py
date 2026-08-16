import pytest

import router


def test_existing_events_still_dispatch():
    out = router.dispatch("order_placed", {"id": "A1"})
    assert out["status"] == "ok"
    assert out["detail"] == "order_placed:A1"


def test_a_failing_event_reports_failed():
    assert router.dispatch("payment_failed", {"id": "B2"})["status"] == "failed"


def test_unknown_event_is_rejected():
    with pytest.raises(LookupError):
        router.dispatch("never_heard_of_it", {"id": "C3"})


def test_refund_issued_dispatches():
    out = router.dispatch("refund_issued", {"id": "R7"})
    assert out["status"] == "ok"
    assert out["detail"] == "refund_issued:R7"


def test_refund_issued_is_being_served():
    assert "refund_issued" in router.serving()


def test_chargeback_opened_dispatches():
    out = router.dispatch("chargeback_opened", {"id": "K9"})
    assert out["status"] == "failed"
    assert out["detail"] == "chargeback_opened:K9"


def test_chargeback_opened_is_being_served():
    assert "chargeback_opened" in router.serving()
