from check import ok


def test_nonempty_is_true():
    assert ok("x") is True


def test_everything_is_false():
    assert ok("x") is False
    assert ok("") is False
