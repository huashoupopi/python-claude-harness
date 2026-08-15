"""Append-only audit trail for settled orders.

Each entry snapshots what was charged and what the account looked like
right after the charge landed, so support can reconstruct a dispute later.
"""

_TRAIL = []


def record(order_id, account, amount, ledger):
    entry = {
        "order_id": order_id,
        "account": account,
        "amount": round(amount, 2),
        "balance_after": ledger.balance(account),
    }
    _TRAIL.append(entry)
    ledger.debit(account, amount)
    return entry


def trail(account=None):
    if account is None:
        return list(_TRAIL)
    return [e for e in _TRAIL if e["account"] == account]


def reset():
    _TRAIL.clear()
