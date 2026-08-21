# python-claude-harness

A coding agent that reads code, edits it, and runs tests — plus an evaluation harness built to measure whether its layers actually earn their keep.

[中文说明](README.md) · Written from scratch following [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code); the evaluation harness is not part of that course.

---

## What it does

Give it one sentence. It breaks the task down, reads files, writes code, runs tests, and loops until they pass:

```bash
uv run python examples/22_trunk.py "fix the failing test"
```

With no argument it drops into a REPL. It ships 28 tools — file I/O, shell, a task list, subagents, cross-session memory, cron, git worktrees, MCP plugins.

Skills load **on demand**. `skills/*/SKILL.md` is scanned once at startup, but the system prompt only carries the index (name + one line). When the model wants the details it calls `load_skill` and gets the **full SKILL.md, frontmatter included**. That's progressive disclosure: the index is present every turn and costs little; the body is paid for once, and only if it's used. Two working examples live in the repo: `git-commit` and `python-style`.

---

## What this project is actually about

Writing an agent that runs isn't the hard part. The hard part is answering: **do all these layers actually do anything?**

So the repo carries an evaluation harness — **22 tasks** (20 graded by exit code, 2 diagnostic-only and excluded from the pass rate). Switch one layer off, run the same tasks, compare.

### What came out

**5 configurations × 15 tasks × 3 runs each = 225 runs. Every one solved the task.**

> That data was collected on the 15-task bank. The bank has since grown to 22 (t17–t20 graded, t21/t22 diagnostic). **The numbers below are never backfilled** — a different task bank is a different ruler, and reporting them together would be fabricating a comparison.

| Layer switched off | Effect | Reading |
|---|---|---|
| **Task list** (todo tool) | Steps **cut in half**<br>14.6 → 7.1 | **The most expensive layer** — and it scales with difficulty: +5 steps on easy tasks, +12 on hard ones |
| **Memory** (recall across turns) | Barely moves<br>0.3 steps | **On this task bank, it paid for nothing** |

For the todo result there was **not a single counterexample** across 225 runs — 42 paired comparisons, all 42 in the same direction.

Two follow-ups worth stating:

- The cost of the todo layer comes from **the tool existing**, not from the system nagging the model to use it. Keep the tool, drop the reminders, and step count barely changes.
- All 15 tasks were solved under every configuration, which means pass/fail had stopped discriminating. **The only thing left to measure was effort.**

### Task difficulty

| | Avg steps | Avg cost |
|---|---|---|
| Basic (8) | 9.3 | 25k tokens |
| Advanced (4) | 14.1 | 45k tokens |
| Two-round (3) | 22.9 | 67k tokens |

The advanced ones are bugs you actually hit in backend work: two bugs masking each other, a thread race, a config cache that never invalidates, a network retry that double-charges the customer.

---

## Seeing what the agent did

Every run writes a trace. Replay it any time:

```bash
uv run python bench/trace_view.py
```

```
21 tool calls   tools 1.4s (1%)   model thinking 131.2s (99%)
legend: ▄ read  █ write  ▓ exec  ░ organize  · think   × error  ! blocked

  2 · bash        348ms  command=python -m pytest -v
      ⋯ thinking 11.3s
  4 · read_file     1ms  path=billing.py
```

That first line was the surprise: **tool execution is 1% of wall-clock, the model thinking is 99%**. Which means making an agent faster is not about making tools faster — it's about making it think fewer times.

---

## Keeping it contained

An agent's `bash` is universal, and in-process permission checks cannot fence it in. This is not hypothetical — it happened here: a file-tool write outside the workspace was blocked, so the model **switched to `bash` and wrote the file anyway**, announcing "I'll use bash instead" in its reply. The file landed outside the workspace.

So the whole thing goes into a Docker container with only the working directory mounted. Inside, paths outside the workspace **do not exist**.

```bash
uv run python bench/sandbox_demo.py
```

```
[sandbox off]  write outside → succeeded       read outside → leaked
[sandbox on]   write outside → blocked         read outside → blocked
[both]         normal task   → works
```

That last row is not padding. **Proving bad things are blocked is only half of it — you also have to prove good things still get through.** A sandbox that permits nothing at all would pass the first half perfectly.

---

## Getting started

```bash
uv sync                                          # install
cp .env.example .env                             # add your API key
uv run python examples/22_trunk.py "your task"   # run the agent
uv run pytest -q                                 # tests
```

Evaluation harness (needs Docker):

```bash
uv run python bench/verify_tasks.py    # check the 22 tasks themselves are sound
uv run python bench/run_bench.py       # full sweep (~80 min)
uv run python bench/analyze.py         # read the results
```

`verify_tasks.py` runs a **two-sided check** on every task: the original repo must fail, and the reference solution must pass. One side alone is not enough — check only that it fails and the task may be unsolvable, or the grader may be broken; check only that the solution passes and the task may have been green all along, so the agent scores a point for doing nothing.

---

## Layout

```
examples/22_trunk.py   the agent itself
bench/                 evaluation: task bank, sweep runner, analysis, trace viewer
tests/                 tests
sandbox/               Docker sandbox image
```

### What's inside the trunk file

`22_trunk.py` is a single file of roughly 3,900 lines, arranged in 13 layers. Each layer opens with a `【第 N 层】` marker — **search for that string to jump**:

| | Layer | Responsibility | Line |
|---|---|---|---|
| 1 | Config · constants · switches | client, working dirs, mode flags | 38 |
| 2 | Error recovery | retry, backoff, rate limits, context overflow | 217 |
| 3 | Memory | store, retrieve, auto-select what to inject | 310 |
| 4 | Context management | compaction strategies, transcript persistence | 603 |
| 5 | Hooks · permissions · tracing | interception points around every tool call | 774 |
| 6 | State | tasks, git worktrees, cron | 1009 |
| 7 | Teams | inter-agent messaging and protocol | 1493 |
| 8 | Tool schemas | input schemas for 28 tools | 2085 |
| 9 | Tool implementations | sandbox, file I/O, subagents, MCP | 2248 |
| 10 | Tool registry | assembling the tool pool | 3090 |
| 11 | Prompt assembly | what the model actually sees each turn | 3273 |
| 12 | Dispatch | which tool runs, and whether it goes to the background | 3385 |
| 13 | Session · streaming · main loop | `agent_loop` and the entrypoint | 3513 |

> Line numbers are from 2026-08-16 and drift as the code changes — **search the `【第 N 层】` markers instead.**
>
> Each layer's comment block records not just its responsibility but the mistakes made in it — why registration order matters in layer 5, why the sandbox check has to come first in layer 9.
>
> **The single file is deliberate for now.** Splitting it into a package is in `BACKLOG.md`, currently deferred: the test suite's sandbox isolation depends on `monkeypatch.setattr` against module globals, and a naive package facade would silently break it for 45 tests — green tests writing to real directories. The fixture gets fixed first, then the file structure.

---

## About

Built alongside an open course ([learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)), 20 chapters on agent internals — but not in the usual way:

- **Every line hand-written.** The course targets Anthropic's API format; this repo uses OpenAI's, so every chapter had to be translated rather than copied.
- **Closed-book exams.** After the key chapters: clear the editor, rewrite the whole agent from an empty file, no reference material, no AI. Done twice.
- **Defects found in the course material** and fixed — alongside a fair number written and then caught here.

The evaluation harness is not from the course. That part is the point of the repo.
