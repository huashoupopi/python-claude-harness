from store import total_value

items = [
    {"名字": "洋葱", "价格": 10, "数量": 5},
    {"名字": "大蒜", "价格": 20, "数量": 3},
]

total = total_value(items)
summary = f"TOTAL: {total}"
