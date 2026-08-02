from greeting import greet


def test_greet():
    assert greet("World") == "Hello, World!"
    assert greet("Alex") == "Hello, Alex!"
