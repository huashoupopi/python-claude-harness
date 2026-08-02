from report import items, summary


def test_summary():
    assert summary == f"TOTAL: {sum((item['价格'] * item['数量']) for item in items)}"
