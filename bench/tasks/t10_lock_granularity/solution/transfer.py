"""Money transfers between accounts.

Several worker threads call transfer() against the same store, so anything
that reads a balance and then writes it back has to be protected.
"""

import threading


class TransferService:
    def __init__(self, store, rates):
        self._store = store
        self._rates = rates
        self._lock = threading.Lock()

    def transfer(self, src, dst, amount):
        """Move `amount` out of `src` and the converted amount into `dst`."""
        converted = self._rates.convert(amount)
        with self._lock:
            self._store.set(src, self._store.get(src) - amount)
            self._store.set(dst, self._store.get(dst) + converted)

    def total_assets(self):
        """Sum of every balance, as one consistent snapshot."""
        with self._lock:
            return sum(self._store.get(a) for a in self._store.accounts())
