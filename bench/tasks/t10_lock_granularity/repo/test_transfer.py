import threading

from rates import RateProvider
from store import BalanceStore
from transfer import TransferService


class ObservableRates:
    """A rate provider that reports whether two transfers were inside it at once.

    convert() parks on a barrier until a second caller arrives. If a second
    caller can never arrive, the barrier gives up after `timeout` and convert()
    still returns a correct rate -- so this class never changes the arithmetic,
    it only observes whether the two calls overlapped.
    """

    def __init__(self, parties=2, timeout=1.0):
        self._barrier = threading.Barrier(parties)
        self._timeout = timeout
        self.overlapped = False

    def convert(self, amount):
        try:
            self._barrier.wait(timeout=self._timeout)
            self.overlapped = True
        except threading.BrokenBarrierError:
            pass
        return amount


def _two_concurrent_transfers(service):
    threads = [
        threading.Thread(target=service.transfer, args=("alice", "bob", 100), daemon=True)
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "transfer 线程没有结束"


def test_single_transfer_moves_money():
    store = BalanceStore({"alice": 1000, "bob": 0})
    service = TransferService(store, RateProvider())
    service.transfer("alice", "bob", 100)
    assert store.get("alice") == 900
    assert store.get("bob") == 100


def test_total_assets_is_conserved_by_a_transfer():
    store = BalanceStore({"alice": 1000, "bob": 0})
    service = TransferService(store, RateProvider())
    before = service.total_assets()
    service.transfer("alice", "bob", 100)
    assert service.total_assets() == before


def test_concurrent_transfers_do_not_lose_updates():
    store = BalanceStore({"alice": 1000, "bob": 0})
    service = TransferService(store, ObservableRates())
    _two_concurrent_transfers(service)
    assert store.get("alice") == 800
    assert store.get("bob") == 200


def test_rate_lookup_is_not_serialized():
    store = BalanceStore({"alice": 1000, "bob": 0})
    rates = ObservableRates()
    service = TransferService(store, rates)
    _two_concurrent_transfers(service)
    assert rates.overlapped, "两次转账没能同时进入汇率查询"
