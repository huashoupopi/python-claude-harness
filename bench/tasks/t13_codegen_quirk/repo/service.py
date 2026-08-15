"""Signup validation, built on top of the generated field checks."""

import validators


def validate_signup(form):
    problems = []
    if not validators.check_username(form["username"]):
        problems.append("username")
    if not validators.check_email(form["email"]):
        problems.append("email")
    if not validators.check_age(form["age"]):
        problems.append("age")
    if not validators.check_zipcode(form["zipcode"]):
        problems.append("zipcode")
    return problems


def is_valid(form):
    return validate_signup(form) == []
