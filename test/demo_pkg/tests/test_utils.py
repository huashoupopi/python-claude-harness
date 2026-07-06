"""Tests for the utils module.

This module contains unit tests for the utility functions
in the demo_pkg.utils module.
"""

import unittest
from demo_pkg.utils import add, multiply, greet


class TestUtils(unittest.TestCase):
    """Test cases for utility functions."""

    def test_add(self):
        """Test the add function with various inputs."""
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add(-1, 1), 0)
        self.assertEqual(add(0, 0), 0)

    def test_multiply(self):
        """Test the multiply function with various inputs."""
        self.assertEqual(multiply(2, 3), 6)
        self.assertEqual(multiply(-2, 3), -6)
        self.assertEqual(multiply(0, 5), 0)

    def test_greet(self):
        """Test the greet function with different names."""
        self.assertEqual(greet("Alice"), "Hello, Alice!")
        self.assertEqual(greet("Bob"), "Hello, Bob!")
        self.assertEqual(greet(), "Hello, World!")


if __name__ == "__main__":
    unittest.main()
