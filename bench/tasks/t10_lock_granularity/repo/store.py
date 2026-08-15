"""In-memory balance store. Plain reads and writes, no locking of its own."""


class BalanceStore:
    def __init__(self, initial=None):
        self._balances = dict(initial or {})

    def get(self, account):
        return self._balances.get(account, 0)

    def set(self, account, value):
        self._balances[account] = value

    def accounts(self):
        return list(self._balances)
