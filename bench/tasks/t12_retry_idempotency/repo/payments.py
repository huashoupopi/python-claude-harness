"""Charging a customer, with the network flakiness handled for the caller."""

import uuid

from retrying import with_retry


def pay(gateway, account, amount):
    def attempt():
        idempotency_key = uuid.uuid4().hex
        return gateway.charge(idempotency_key, account, amount)

    return with_retry(attempt)
