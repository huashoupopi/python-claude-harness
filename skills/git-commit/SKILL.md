---
name: git-commit
description: "How to write commit messages in this project. Use when creating git commits or when the user asks for a commit message."
---
# Git Commit Convention

## Format
```
<type>: <short summary under 60 chars>

<optional body: what and why, not how>
```

## Types
- `feat`: a new feature
- `fix`: a bug fix
- `refactor`: code change that neither fixes a bug nor adds a feature
- `test`: adding or fixing tests
- `docs`: documentation only

## Rules
- Summary in imperative mood: "add", not "added".
- Do not commit `.env` or any file containing secrets.
