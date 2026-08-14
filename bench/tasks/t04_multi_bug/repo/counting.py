def count_vowels(s):
    """Count vowels (aeiou), case-insensitive."""
    return sum(1 for c in s if c in "aeiou")
