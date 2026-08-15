"""Double-entry-ish account ledger.

Every movement of money goes through debit()/credit() so that the entry log
stays authoritative: balance() is derived state, entries are the source of truth.
"""


class InsufficientDetail(ValueError):
    """Raised when an amount is not a usable monetary value."""


class Ledger:
    def __init__(self):
        self._balances = {}
        self._entries = []

    def debit(self, account, amount):
        """Take `amount` out of `account`."""
        if amount < 0:
            raise InsufficientDetail("debit amount must be non-negative")
        self._balances[account] = self._balances.get(account, 0.0) - amount
        self._entries.append(("debit", account, round(amount, 2)))

    def credit(self, account, amount):
        """Put `amount` back into `account`."""
        if amount < 0:
            raise InsufficientDetail("credit amount must be non-negative")
        self._balances[account] = self._balances.get(account, 0.0) + amount
        self._entries.append(("credit", account, round(amount, 2)))

    def balance(self, account):
        return round(self._balances.get(account, 0.0), 2)

    def entries(self, account=None):
        if account is None:
            return list(self._entries)
        return [e for e in self._entries if e[1] == account]
