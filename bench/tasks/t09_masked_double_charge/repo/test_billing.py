import pytest

import audit
import billing
import pricing
from ledger import Ledger

ACCOUNT = "cust-8841"
ONE_ITEM = [(100.0, 1)]


@pytest.fixture(autouse=True)
def _clean_trail():
    audit.reset()
    yield
    audit.reset()


def test_order_total_applies_tax():
    assert pricing.order_total(ONE_ITEM) == 108.0


def test_bulk_discount_kicks_in_at_threshold():
    assert pricing.order_total([(10.0, 10)]) == 102.6


def test_balance_after_single_order():
    ledger = Ledger()
    billing.settle("ord-1", ACCOUNT, ONE_ITEM, ledger)
    assert ledger.balance(ACCOUNT) == -108.0


def test_balance_after_two_orders():
    ledger = Ledger()
    billing.settle("ord-1", ACCOUNT, ONE_ITEM, ledger)
    billing.settle("ord-2", ACCOUNT, ONE_ITEM, ledger)
    assert ledger.balance(ACCOUNT) == -216.0


def test_ledger_records_one_entry_per_charge():
    ledger = Ledger()
    billing.settle("ord-1", ACCOUNT, ONE_ITEM, ledger)
    assert len(ledger.entries(ACCOUNT)) == 1


def test_audit_trail_records_settled_order():
    ledger = Ledger()
    billing.settle("ord-1", ACCOUNT, ONE_ITEM, ledger)
    entries = audit.trail(ACCOUNT)
    assert len(entries) == 1
    assert entries[0]["order_id"] == "ord-1"
    assert entries[0]["amount"] == 108.0
    assert entries[0]["balance_after"] == -108.0


def test_refund_credits_the_account_back():
    ledger = Ledger()
    ledger.debit(ACCOUNT, 108.0)
    billing.refund("ord-1", ACCOUNT, 108.0, ledger)
    assert ledger.balance(ACCOUNT) == 0.0
