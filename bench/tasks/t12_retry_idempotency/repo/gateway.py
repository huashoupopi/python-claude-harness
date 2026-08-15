"""Stand-in for the external payment provider.

The provider deduplicates on `idempotency_key`: replaying a key it has already
settled returns the original receipt instead of moving money a second time.

Two very different blips can be simulated:

    fail_after_write=N   the connection drops *after* the charge was written,
                         so the caller never learns that the money moved
    fail_before_write=N  the request never reached the provider at all
"""


class PaymentGateway:
    def __init__(self, fail_after_write=0, fail_before_write=0):
        self._settled = {}
        self.charges = []
        self._fail_after = fail_after_write
        self._fail_before = fail_before_write

    def charge(self, idempotency_key, account, amount):
        if idempotency_key in self._settled:
            return self._settled[idempotency_key]

        if self._fail_before > 0:
            self._fail_before -= 1
            raise ConnectionError("connection refused before the request landed")

        receipt = {
            "key": idempotency_key,
            "account": account,
            "amount": round(amount, 2),
        }
        self._settled[idempotency_key] = receipt
        self.charges.append(receipt)

        if self._fail_after > 0:
            self._fail_after -= 1
            raise ConnectionError("connection reset after the charge was written")

        return receipt
