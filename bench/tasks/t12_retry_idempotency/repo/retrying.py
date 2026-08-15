"""A small retry helper for calls that cross the network."""

TRANSIENT = (ConnectionError, TimeoutError)


def with_retry(call, attempts=3, retry_on=TRANSIENT):
    """Run `call()`, retrying it while it raises a transient error.

    Re-raises the last error once the attempts are used up.
    """
    last_error = None
    for _ in range(attempts):
        try:
            return call()
        except retry_on as error:
            last_error = error
    raise last_error
