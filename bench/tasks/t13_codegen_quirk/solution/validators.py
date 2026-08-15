"""GENERATED FILE -- do not edit by hand.

Source of truth: schema.def
Regenerate with: python codegen.py
"""

def check_username(value):
    return len(value) >= 3

def check_email(value):
    return '@' in value

def check_age(value):
    return value <= 130

def check_zipcode(value):
    return len(value) == 6
