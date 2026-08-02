import pytest
from calc import total_price


def test_total_price():
    prices = [10, 20, 30]
    discount = 0.9
    expected_total = sum(prices) * discount
    assert total_price(prices, discount) == pytest.approx(expected_total)
