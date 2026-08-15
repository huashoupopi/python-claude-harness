"""Currency conversion.

In production this reaches the FX pricing service over the network, so a call
can take a while. Nothing here touches account state.
"""


class RateProvider:
    def __init__(self, rate=1.0):
        self._rate = rate

    def convert(self, amount):
        return amount * self._rate
