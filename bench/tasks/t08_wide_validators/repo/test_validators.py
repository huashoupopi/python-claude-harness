from check_age import is_age
from check_email import is_email
from check_hex import is_hex
from check_name import is_name
from check_phone import is_phone
from check_slug import is_slug
from check_url import is_url
from check_zip import is_zip


def test_is_email():
    assert is_email("a@b.com") is True
    assert is_email("nope") is False

def test_is_age():
    assert is_age(0) is True
    assert is_age(151) is False

def test_is_zip():
    assert is_zip("12345") is True
    assert is_zip("123") is False

def test_is_phone():
    assert is_phone("13800138000") is True
    assert is_phone("1380013800") is False

def test_is_name():
    assert is_name("ada") is True
    assert is_name("   ") is False

def test_is_url():
    assert is_url("https://x.com") is True
    assert is_url("ftp://x") is False

def test_is_hex():
    assert is_hex("#a1b2c3") is True
    assert is_hex("#xyz") is False

def test_is_slug():
    assert is_slug("hello-world") is True
    assert is_slug("hello world") is False
