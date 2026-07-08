---
name: python-style
description: "Python coding conventions for this project. Use when writing or editing Python code — naming, imports, error handling, type hints."
---
# Python Style Guide

Follow these conventions when writing Python in this project:

## Naming
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private helpers: prefix with a single underscore `_helper`

## Imports
- Group in three blocks: standard library, third-party, local — separated by blank lines.
- No wildcard imports (`from x import *`).

## Type hints
- All public functions must have parameter and return type annotations.

## Error handling
- Never use a bare `except:`. Catch specific exceptions.
- Prefer returning an error string over crashing when the caller is an LLM tool.
