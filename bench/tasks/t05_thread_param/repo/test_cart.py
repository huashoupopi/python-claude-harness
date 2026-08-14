from cart import checkout


def test_default_rate():
    assert checkout([("a", 100.0)]) == 110.0


def test_custom_rate():
    assert checkout([("a", 100.0)], tax_rate=0.08) == 108.0


def test_zero_rate():
    assert checkout([("a", 100.0), ("b", 50.0)], tax_rate=0.0) == 150.0
