import pytest

import orders
import payments
from gateway import PaymentGateway

ACCOUNT = "cust-3097"
BASKET = [(25.0, 2), (10.0, 5)]  # 100.0


def test_a_clean_payment_charges_once():
    gateway = PaymentGateway()
    receipt = payments.pay(gateway, ACCOUNT, 100.0)
    assert receipt["amount"] == 100.0
    assert len(gateway.charges) == 1


def test_payment_survives_a_blip_after_the_write():
    gateway = PaymentGateway(fail_after_write=1)
    receipt = payments.pay(gateway, ACCOUNT, 100.0)
    assert receipt["amount"] == 100.0


def test_a_blip_after_the_write_does_not_charge_twice():
    gateway = PaymentGateway(fail_after_write=1)
    payments.pay(gateway, ACCOUNT, 100.0)
    assert len(gateway.charges) == 1, (
        f"客户被扣了 {len(gateway.charges)} 次: "
        f"{[c['amount'] for c in gateway.charges]}"
    )


def test_two_blips_in_a_row_still_charge_once():
    gateway = PaymentGateway(fail_after_write=2)
    payments.pay(gateway, ACCOUNT, 100.0)
    assert len(gateway.charges) == 1


def test_payment_survives_a_blip_before_the_write():
    gateway = PaymentGateway(fail_before_write=1)
    receipt = payments.pay(gateway, ACCOUNT, 100.0)
    assert receipt["amount"] == 100.0
    assert len(gateway.charges) == 1


def test_it_gives_up_once_the_attempts_run_out():
    gateway = PaymentGateway(fail_before_write=99)
    with pytest.raises(ConnectionError):
        payments.pay(gateway, ACCOUNT, 100.0)
    assert gateway.charges == []


def test_placing_an_order_through_a_blip_charges_once():
    gateway = PaymentGateway(fail_after_write=1)
    result = orders.place(gateway, ACCOUNT, BASKET)
    assert result["total"] == 100.0
    assert len(gateway.charges) == 1
