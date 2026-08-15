"""Turns schema.def into validators.py.

schema.def is the source of truth. validators.py is a build product: whatever
is in it gets thrown away and rewritten every time this runs.

    python codegen.py
"""

import pathlib

HEADER = '''"""GENERATED FILE -- do not edit by hand.

Source of truth: schema.def
Regenerate with: python codegen.py
"""
'''

TEMPLATES = {
    "min_length": "def check_{field}(value):\n    return len(value) >= {arg}\n",
    "exact_length": "def check_{field}(value):\n    return len(value) == {arg}\n",
    "max_value": "def check_{field}(value):\n    return value <= {arg}\n",
    "contains": "def check_{field}(value):\n    return {arg!r} in value\n",
}

NUMERIC = {"min_length", "exact_length", "max_value"}


def render(schema_text):
    parts = [HEADER]
    for line in schema_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        field, rule, arg = (piece.strip() for piece in line.split("|"))
        if rule not in TEMPLATES:
            raise ValueError(f"unknown rule {rule!r} for field {field!r}")
        value = int(arg) if rule in NUMERIC else arg
        parts.append(TEMPLATES[rule].format(field=field, arg=value))
    return "\n".join(parts)


def main():
    here = pathlib.Path(__file__).parent
    schema = (here / "schema.def").read_text(encoding="utf-8")
    (here / "validators.py").write_text(render(schema), encoding="utf-8")
    print("validators.py regenerated from schema.def")


if __name__ == "__main__":
    main()
