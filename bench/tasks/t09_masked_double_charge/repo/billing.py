"""Settlement flow: price the order, move the money, leave a trail."""

import audit
import pricing


def settle(order_id, account, items, ledger, audit_enabled=True):
    """Charge `account` for `items` and return the amount charged."""
    charge = pricing.order_total(items)
    ledger.debit(account, charge)
    if not audit_enabled:
        audit.record(order_id, account, charge, ledger)
    return charge


def refund(order_id, account, amount, ledger):
    """Give `amount` back to `account` and return it."""
    ledger.credit(account, amount)
    return amount
