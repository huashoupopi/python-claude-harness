def count_vowels(s):
    """Count vowels (aeiou), case-insensitive."""
    return sum(1 for c in s.lower() if c in "aeiou")
