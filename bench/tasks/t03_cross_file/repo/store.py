def total_value(items):
    return sum((item["价格"] + item["数量"]) for item in items)