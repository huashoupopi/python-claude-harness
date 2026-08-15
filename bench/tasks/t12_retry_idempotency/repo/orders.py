"""Order placement: price the basket, then take the money."""

import payments


def basket_total(items):
    return sum(price * quantity for price, quantity in items)


def place(gateway, account, items):
    total = basket_total(items)
    receipt = payments.pay(gateway, account, total)
    return {"account": account, "total": total, "receipt": receipt}
