"""Utility functions for the demo package.

This module contains simple utility functions like arithmetic operations
and greeting messages for demonstration and testing purposes.
"""


def add(a: int, b: int) -> int:
    """Add two integers and return the result.

    Args:
        a: First integer
        b: Second integer

    Returns:
        Sum of a and b
    """
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the result.

    Args:
        a: First integer
        b: Second integer

    Returns:
        Product of a and b
    """
    return a * b


def greet(name: str = "World") -> str:
    """Return a greeting message for the given name.

    Args:
        name: Name to greet (default: "World")

    Returns:
        Greeting message
    """
    return f"Hello, {name}!"
