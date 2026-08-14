from counting import count_vowels
from display import truncate
from names import initials
from words import reverse_words


def test_reverse_words():
    assert reverse_words("a b c") == "c b a"


def test_count_vowels():
    assert count_vowels("AEIou xyz") == 5


def test_truncate():
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 5) == "hello..."


def test_initials():
    assert initials("ada lovelace") == "A.L."
