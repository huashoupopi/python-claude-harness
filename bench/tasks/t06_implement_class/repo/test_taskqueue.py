import pytest

from taskqueue import TaskQueue


def test_push_and_size():
    q = TaskQueue()
    q.push("a")
    q.push("b")
    assert q.size() == 2


def test_pop_is_fifo():
    q = TaskQueue()
    q.push("a")
    q.push("b")
    assert q.pop() == "a"
    assert q.pop() == "b"


def test_peek_does_not_remove():
    q = TaskQueue()
    q.push("a")
    assert q.peek() == "a"
    assert q.size() == 1


def test_is_empty():
    q = TaskQueue()
    assert q.is_empty() is True
    q.push("a")
    assert q.is_empty() is False


def test_pop_on_empty_raises():
    q = TaskQueue()
    with pytest.raises(IndexError):
        q.pop()
