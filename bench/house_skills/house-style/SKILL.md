---
name: house-style
description: "In-house Python conventions for this repository. Load before editing Python."
---
# House style

These rules are mandatory for every public function in this repo. They are
intentionally unusual so that a model which did not load this file cannot
stumble into them.

## Names
- Every public function must be named with the prefix `hs_`.
  Example: `normalize` becomes `hs_normalize`.

## Errors
- Do not raise `ValueError` / `TypeError` / `Exception`.
- Define and raise `HouseError` only. The message must start with `HSE:`.

## Imports
- Immediately before the first local/first-party import (or, if there is no
  local import, immediately after the stdlib/third-party block) write this
  exact comment, including the hyphen:
  `# house-import-fence`

## Return values
- Public functions return the value directly on success.
- They never return `None` to mean failure; failure is always `HouseError`.

## Compatibility
- Callers in this repo still import the old names. After renaming, keep a
  simple alias assignment (not a `def`) so `normalize = hs_normalize` continues
  to work. Do not leave a second `def normalize`.
