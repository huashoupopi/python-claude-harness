"""T19 组装的起点:21_mcp_real.py 的可 import 副本。

与 21 的唯一区别是**把模块级副作用收进函数**,其余逐字未动:
  - 四个 mkdir              -> ensure_dirs()
  - load_durable_jobs + cron 线程 + print -> start_cron_scheduler()
  - session_context/history 初始化        -> init_session()
  - __main__ 块             -> main()

为什么要做:原版 `import` 一次 = 在 cwd 里造四个目录、起一条 cron 线程、建一份会话状态,
既没法写测试,也没法被别的模块复用。可 import 是打包的前提。

21 保持课程原状不动,当基准;本文件是主干,后续 T19 在这上面合并。
运行方式与 21 相同:uv run python examples/22_trunk.py
"""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv(override=True)

client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL"),
    api_key=os.getenv("NVIDIA_API_KEY"),
    timeout=120.0,
    default_headers={"User-Agent": "curl/8.7.1"},
)

WORKDIR = Path.cwd()
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
TASKS_DIR = WORKDIR / ".tasks"
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
MAILBOX_DIR = WORKDIR / ".mailboxes"
WORKTREES_DIR = WORKDIR / ".worktrees"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
SKILLS_DIR = WORKDIR / "skills"
model = os.getenv("NVIDIA_MODEL")

CURRENT_TODOS: list = []
MAX_GLOB_RESULTS = 200
CONTEXT_LIMIT = 500000
KEEP_RECENT = 3
PERSIST_THRESHOLD = 30000
MEMORY_TYPES = ["user", "feedback", "project", "reference"]


SKILL_REGISTRY: dict[str, dict] = {}


def _scan_skills():
    if not SKILLS_DIR.exists():
        return
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_file = d / "SKILL.md"
        if skill_file.exists():
            raw = skill_file.read_text()
            meta, body = _parse_frontmatter(raw)
            name = meta.get("name", d.name)
            description = meta.get(
                "description", raw.split("\n")[0].lstrip("#").strip()
            )
            SKILL_REGISTRY[name] = {
                "name": name,
                "description": description,
                "content": raw,
            }


def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- **{s['name']}**: {s['description']}" for s in SKILL_REGISTRY.values()
    )


# ---------------------------------------------------------------------------
# 记忆层开关(T22 消融轴:stage-1 测的三个臂就是这三种模式)
#   none      不注入、不提取、不注册 memory 工具        —— baseline
#   self      系统自动提取 + 相关性注入,模型无感        —— self_memory
#   official  注册 memory 工具,模型自己 view/create     —— official_memory
#
# 优先级:CLI 参数 --memory > 环境变量 MEMORY_MODE > 默认值
#   环境变量这条是给 bench 用的:run_bench.py 起子进程跑,env 传得下去,
#   而 CLI 参数走不到(agent_runner.py 不经过 main())。
# 【2026-08-13 AI 代写:开关机制,待盲讲验收】
# ---------------------------------------------------------------------------
# self 模式下,select_relevant_memories 会【额外调一次 LLM】来挑相关记忆。
# 而 update_context 有三个调用点,其中一个在 agent_loop 的循环内 ——
# 不缓存的话一轮对话最多触发 26 次额外请求(10_memory_loop.py 是进 loop 算一次)。
# 这里按「一次用户输入 = 选一次记忆」缓存,语义与 10 对齐,
# stage-1 的 self_memory 那组数据才可比。None = 本轮尚未选过。
_memories_cache: str | None = None

MEMORY_MODES = ("none", "self", "official")
MEMORY_MODE = os.getenv("MEMORY_MODE", "self")
if MEMORY_MODE not in MEMORY_MODES:
    # fail loud:配置错了当场炸,不要静默退回默认值 ——
    # 否则 bench 跑完一整轮才发现「消融臂根本没生效」,数据全废。
    raise ValueError(f"MEMORY_MODE={MEMORY_MODE!r} 不合法,只能是 {MEMORY_MODES} 之一")

TODO_MODES = ("none", "tool", "nudge")
TODO_MODE = os.getenv("TODO_MODE", "nudge")
if TODO_MODE not in TODO_MODES:
    # fail loud:配置错了当场炸,不要静默退回默认值 ——
    # 否则 bench 跑完一整轮才发现「消融臂根本没生效」,数据全废。
    raise ValueError(f"TODO_MODE={TODO_MODE!r} 不合法,只能是 {TODO_MODES} 之一")

# 【2026-08-14 删除 ESCALATED_MAX_TOKENS = 64000】
# 13_error_recovery.py 的截断处置是两级:①升档重来(8000→64000,丢掉半截重新生成)
# ②保留 + 续写。本主干是【流式】的,半截输出已经一个字一个字打到用户屏幕上了,
# 「重来」= 用户看到同一段话来两遍 —— 直接否定了流式的价值。
# 故只保留②,升档这一级整个去掉,该常量与 RecoveryState.has_escalated 一并删除。
# ⚠️ 附带后果(值得记):13 的①【顺带】挡住了「残缺工具调用进历史」——
#    它丢的是整个 msg。去掉①之后这层保护也没了,所以②里必须显式
#    build_message(text, {}) 把可能残缺的 tool_calls 丢掉,否则孤儿 → API 400。
DEFAULT_MAX_TOKENS = 8000
MAX_RECOVERY_RETRIES = 3
MAX_RETRIES = 10
BASE_DELAY_MS = 500
MAX_CONSECUTIVE_529 = 3
# ⚠️ 当前 FALLBACK_MODEL 与主 model 读的是【同一个环境变量】,即「切换到自己」。
# 这是【有意保留的现状】(2026-08-13 当事人决定):手头没有第二个可用模型,
# 目的只是先把 529 → 切换 这条路径跑通。真要有容灾,改读独立的 FALLBACK_MODEL 变量即可
# (with_retry 里已有「没配就只打日志继续重试」的分支,值为 None 是安全的)。
# → 切换是否真的生效,靠单元测试 monkeypatch 一个假模型名来验,不靠线上行为。
FALLBACK_MODEL = os.getenv("NVIDIA_MODEL")
CONTINUATION_PROMPT = "Output token limit hit. Resume directly — no apology, no recap. Pick up mid-thought."

# 子代理(s06/s07):一次性、只回结论。"Do not delegate further" 是防套娃的第一道,
# 第二道是 spawn_subagent 里从工具池摘掉 spawn_subagent / spawn_teammate。
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)
MAX_SUBAGENT_TURNS = 30


class RecoveryState:
    """Track recovery attempts across the loop."""

    def __init__(self):
        # 【2026-08-14 删掉两个字段】
        #   has_escalated —— 流式下不做「升档重来」,见 ESCALATED_MAX_TOKENS 处的说明
        #   has_attempted_reactive_compact —— 主干用的是 agent_loop 里的局部变量
        #       reactive_retries,这个字段定义了从来没人读没人写
        self.recovery_count = 0  # 本轮对话已续写几次(max_tokens 截断)
        self.consecutive_529 = 0  # 连续 529 次数,成功一次即清零
        self.current_model = model  # 529 连败达阈值时会被换成 FALLBACK_MODEL


def retry_delay(attempt, retry_after=None):
    """Exponential backoff with jitter. Retry-After takes priority."""
    if retry_after:
        return retry_after
    base = min(BASE_DELAY_MS * (2**attempt), 32000) / 1000
    jitter = random.uniform(0, base * 0.25)
    return base + jitter


def with_retry(fn, state: RecoveryState):
    """Exponential backoff for transient errors (429/529).
    Non-transient errors are re-raised for the outer handler."""
    for attempt in range(MAX_RETRIES):
        try:
            result = fn()
            state.consecutive_529 = 0
            return result
        except Exception as e:
            name = type(e).__name__
            msg = str(e).lower()

            # 429 rate limit -> exponential backoff
            if "ratelimit" in name.lower() or "429" in msg:
                delay = retry_delay(attempt)
                print(
                    f"  \033[33m[429 rate limit] retry {attempt + 1}/{MAX_RETRIES},"
                    f" wait {delay:.1f}s\033[0m"
                )
                time.sleep(delay)
                continue

            # 529 overloaded -> exponential backoff + fallback model
            if "overloaded" in name.lower() or "529" in msg or "overloaded" in msg:
                state.consecutive_529 += 1
                if state.consecutive_529 >= MAX_CONSECUTIVE_529:
                    if FALLBACK_MODEL:
                        state.current_model = FALLBACK_MODEL
                        state.consecutive_529 = 0
                        print(
                            f"  \033[31m[529 x{MAX_CONSECUTIVE_529}]"
                            f" switching to {FALLBACK_MODEL}\033[0m"
                        )
                    else:
                        state.consecutive_529 = 0
                        print(
                            f"  \033[31m[529 x{MAX_CONSECUTIVE_529}]"
                            f" no FALLBACK_MODEL_ID configured, continuing retry\033[0m"
                        )
                delay = retry_delay(attempt)
                print(
                    f"  \033[33m[529 overloaded] retry {attempt + 1}/{MAX_RETRIES},"
                    f" wait {delay:.1f}s\033[0m"
                )
                time.sleep(delay)
                continue

            # Not transient -> re-raise for outer try/except
            raise
    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    """Check whether an API error indicates prompt/context too long."""
    msg = str(e).lower()
    return (
        ("prompt" in msg and "long" in msg)
        or "prompt_is_too_long" in msg
        or "context_length_exceeded" in msg
        or "max_context_window" in msg
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return meta, parts[2].strip()


def _rebuild_index():
    """Rebuild MEMORY.md index from all memory files."""
    lines = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text()
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description", body.split("\n")[0][:80])
        lines.append(f"- [{name}]({f.name}) — {desc}")
    MEMORY_INDEX.write_text("\n".join(lines) + "\n" if lines else "")


def write_memory_file(name: str, mem_type: str, description: str, body: str):
    """Write a single memory file with YAML frontmatter."""
    slug = name.lower().replace(" ", "-").replace("/", "-")
    filename = f"{slug}.md"
    filepath = MEMORY_DIR / filename
    filepath.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
    )
    _rebuild_index()
    return filepath


def read_memory_index() -> str:
    """Read MEMORY.md index (injected into SYSTEM every turn)."""
    if not MEMORY_INDEX.exists():
        return ""
    text = MEMORY_INDEX.read_text().strip()
    return text if text else ""


def read_memory_file(filename: str) -> str | None:
    """Read a single memory file's full content."""
    path = MEMORY_DIR / filename
    if not path.exists():
        return None
    return path.read_text()


def list_memory_files() -> list[dict]:
    """List all memory files with metadata."""
    result = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text()
        meta, body = _parse_frontmatter(raw)
        result.append(
            {
                "filename": f.name,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "type": meta.get("type", "user"),
                "body": body,
            }
        )
    return result


def select_relevant_memories(messages: list, max_items: int = 5) -> list[str]:
    files = list_memory_files()
    if not files:
        return []

    recent_texts = []
    for msg in reversed(messages):
        if msg["role"] == "user":
            recent_texts.append(msg["content"])
        if len(recent_texts) >= 3:
            break
    recent = " ".join(reversed(recent_texts))[:2000]

    if not recent.strip():
        return []
    catalog_lines = []
    for i, f in enumerate(files):
        catalog_lines.append(f"{i}: {f['name']} - {f['description']}")
    catalog = "\n".join(catalog_lines)
    prompt = (
        "Given the recent conversation and the memory catalog below, "
        "select the indices of memories that are clearly relevant. "
        "Return ONLY a JSON array of integers, e.g. [0, 3]. "
        "If none are relevant, return [].\n\n"
        f"Recent conversation:\n{recent}\n\n"
        f"Memory catalog:\n{catalog}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        content = response.choices[0].message.content
        content = str(content)
        match = re.search(r"\[.*?\]", content, re.DOTALL)
        if match:
            indices = json.loads(match.group())
            selected = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(files):
                    selected.append(files[idx]["filename"])
                    if len(selected) >= max_items:
                        break
            return selected
    except Exception:
        pass

    # Fallback: keyword matching on name + description
    keywords = [w.lower() for w in recent.split() if len(w) > 3]
    selected = []
    for f in files:
        text = (f["name"] + " " + f["description"]).lower()
        if any(kw in text for kw in keywords):
            selected.append(f["filename"])
            if len(selected) >= max_items:
                break
    return selected


def load_memories(messages: list) -> str:
    """Load relevant memory content for injection into context."""
    selected_files = select_relevant_memories(messages)
    if not selected_files:
        return ""

    parts = ["<relevant_memories>"]
    for filename in selected_files:
        content = read_memory_file(filename)
        if content:
            parts.append(content)
    parts.append("</relevant_memories>")
    return "\n\n".join(parts)


def extract_memories(messages: list):
    dialogue_parts = []
    for msg in messages[-10:]:
        dialogue_parts.append(f"{msg['role']}: {msg.get('content', '')}")
    dialogue = "\n".join(dialogue_parts)

    if not dialogue.strip():
        return []
    # Check existing memories to avoid duplicates
    existing = list_memory_files()
    existing_desc = (
        "\n".join(f"- {m['name']}: {m['description']}" for m in existing)
        if existing
        else "(none)"
    )

    prompt = (
        "Extract user preferences, constraints, or project facts from this dialogue.\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n"
        "- name: short kebab-case identifier (e.g. 'user-preference-tabs')\n"
        "- type: one of 'user' (user preference), 'feedback' (guidance), "
        "'project' (project fact), 'reference' (external pointer)\n"
        "- description: one-line summary for index lookup\n"
        "- body: full detail in markdown\n"
        "If nothing new or already covered by existing memories, return [].\n\n"
        f"Existing memories:\n{existing_desc}\n\n"
        f"Dialogue:\n{dialogue[:4000]}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        content = response.choices[0].message.content
        content = str(content)
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return
        items = json.loads(match.group())
        if not items:
            return
        count = 0
        for mem in items:
            name = mem.get("name", f"memory_{int(time.time())}")
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                write_memory_file(name, mem_type, desc, body)
                count += 1
        if count:
            print(f"\n\033[33m[Memory: extracted {count} new memories]\033[0m")
    except Exception:
        pass


CONSOLIDATE_THRESHOLD = 10


def consolidate_memories():
    files = list_memory_files()
    if len(files) < CONSOLIDATE_THRESHOLD:
        return
    files = sorted(files, key=lambda f: (MEMORY_DIR / f["filename"]).stat().st_mtime)
    selected = []
    now_length = 0
    budget = 16000
    for f in files:
        length = len(
            f"## {f['filename']}\nname: {f['name']}\ndescription: {f['description']}\n{f['body']}"
        )
        if now_length + length > budget:
            break
        selected.append(f)
        now_length += length

    catalog = "\n\n".join(
        f"## {f['filename']}\nname: {f['name']}\ndescription: {f['description']}\n{f['body']}"
        for f in selected
    )
    prompt = (
        "Consolidate the following memory files. Rules:\n"
        "1. Merge duplicates into one\n"
        "2. Remove outdated/contradicted memories\n"
        "3. Keep the total under 30 memories\n"
        "4. Preserve important user preferences above all\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n\n"
        f"{catalog}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        content = response.choices[0].message.content
        content = str(content)
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return
        items = json.loads(match.group())

        new_names = []
        for mem in items:
            name = mem.get("name", f"memory_{int(time.time())}")
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                filepath = write_memory_file(name, mem_type, desc, body)
                new_names.append(filepath.name)
        # Remove old memory files (keep MEMORY.md)
        for f in selected:
            # if f.name != "MEMORY.md":
            if f["filename"] not in new_names:
                (MEMORY_DIR / f["filename"]).unlink()
        _rebuild_index()

        print(
            f"\n\033[33m[Memory: consolidated {len(selected)} → {len(items)} memories]\033[0m"
        )
    except Exception as e:
        print(f"[Memory: consolidation failed: {e}]")


def _message_has_tool_use(msg):
    if not msg.get("tool_calls"):
        return False
    return True


def _is_tool_result_message(msg):
    if msg.get("role") != "tool":
        return False
    if not msg.get("tool_call_id"):
        return False
    return True


def ensure_dirs():
    """建运行期目录。只在启动时调用——import 本模块不该在 cwd 里造目录。"""
    for d in (
        MEMORY_DIR,
        TASKS_DIR,
        MAILBOX_DIR,
        WORKTREES_DIR,
        TRANSCRIPT_DIR,
        TOOL_RESULTS_DIR,
        SKILLS_DIR,
    ):
        # parents=True 不能省:TOOL_RESULTS_DIR 是 .task_outputs/tool-results 【两层】,
        # 不建父目录会 FileNotFoundError。在项目根目录跑时一直没炸,是因为
        # .task_outputs/ 早被 persist_large_output 里的 mkdir(parents=True) 建好了 ——
        # 换到 bench 的全新考场副本(2026-08-14)才暴露:
        # 【代码依赖了「当前环境恰好已经有某个东西」】。
        d.mkdir(parents=True, exist_ok=True)


def snip_compact(msgs, max_msgs=50):
    if len(msgs) <= max_msgs:
        return msgs
    keep_head, keep_tail = 3, max_msgs - 3
    head_end, tail_start = keep_head, len(msgs) - keep_tail
    if head_end > 0 and _message_has_tool_use(msgs[head_end - 1]):
        while head_end < len(msgs) and _is_tool_result_message(msgs[head_end]):
            head_end += 1
    if tail_start > 0 and _is_tool_result_message(msgs[tail_start]):
        while tail_start > 0 and not _message_has_tool_use(msgs[tail_start]):
            tail_start -= 1
    if head_end >= tail_start:
        return msgs
    snipped = tail_start - head_end
    return (
        msgs[:head_end]
        + [{"role": "user", "content": f"[snipped {snipped} messages]"}]
        + msgs[tail_start:]
    )


def collect_tool_results(msgs):
    results = []
    for i, msg in enumerate(msgs):
        if _is_tool_result_message(msg):
            results.append(
                {
                    "index": i,
                    "tool_call_id": msg.get("tool_call_id"),
                    "msg": msg,
                }
            )
    return results


def micro_compact(msgs):
    tool_results = collect_tool_results(msgs)
    if len(tool_results) <= KEEP_RECENT:
        return msgs
    # for _, _, msg in tool_results[:-KEEP_RECENT]:
    for dic in tool_results[:-KEEP_RECENT]:
        if len(dic["msg"].get("content", "")) > 120:
            dic["msg"]["content"] = "[tool result snipped]"
    return msgs


def persist_large_output(tc_id, output):
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tc_id}.txt"
    if not path.exists():
        path.write_text(output)
    return f"<persisted-output>\nFull output: {path}\nPreview:\n{output[:2000]}\n</persisted-output>"


def tool_result_budget(msgs, max_bytes=200_000):
    last = msgs[-1] if msgs else None
    if not last or not _is_tool_result_message(last):
        return msgs
    batch = []
    for i in range(len(msgs) - 1, -1, -1):
        msg = msgs[i]
        if not _is_tool_result_message(msg):
            continue
        batch.append(msg)
    total_bytes = sum(len(m.get("content", "")) for m in batch)
    if total_bytes <= max_bytes:
        return msgs
    ranked = sorted(batch, key=lambda m: len(m.get("content", "")), reverse=True)
    for m in ranked:
        if total_bytes <= max_bytes:
            break
        content = m.get("content", "")
        if len(content) <= PERSIST_THRESHOLD:
            continue
        tc_id = m.get("tool_call_id")
        m["content"] = persist_large_output(tc_id, content)
        total_bytes = sum(len(m.get("content", "")) for m in batch)
    return msgs


def write_transcript(msgs):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w") as f:
        for msg in msgs:
            f.write(json.dumps(msg, default=str) + "\n")
    return path


def summarize_history(msgs):
    conversation = json.dumps(msgs, default=str)[:80000]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
        "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n"
        + conversation
    )
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], max_tokens=2000
    )
    return response.choices[0].message.content.strip() or "(empty summary)"


def compact_history(msgs):
    transcript_path = write_transcript(msgs)
    print(f"[transcript saved: {transcript_path}]")
    summary = summarize_history(msgs)
    return [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]


# Emergency: reactiveCompact — on API error
def reactive_compact(msgs):
    transcript = write_transcript(msgs)
    summary = summarize_history(msgs)
    tail_start = max(1, len(msgs) - 5)
    if tail_start > 1 and _is_tool_result_message(msgs[tail_start]):
        while tail_start > 1 and not _message_has_tool_use(msgs[tail_start]):
            tail_start -= 1
    return [
        {"role": "user", "content": f"[Reactive compact]\n\n{summary}"},
        *msgs[tail_start:],
    ]


HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hook(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


def log_hook(name, args):
    """PreToolUse: log every tool call."""
    args_preview = str(list(args.values())[:2])[:60]
    print(f"\033[90m[HOOK] {name}({args_preview})\033[0m")


# 【2026-08-13 删除 large_output_hook】它警告「输出 > 100000 字符」,但那道线
# 永远碰不到:persist_large_output 在 30000 就把大输出落盘换成占位符了,
# 而 run_bash 自己还有 [:50000] 截断 —— 上游三道闸全在它前面。
# 留一个永不触发的 hook 就是又一个僵尸。大输出的处置归 compact L3 管。
# ⚠️ PostToolUse 这个【时机】保留(agent_loop 里仍在 trigger),只是当前无订阅者。


# UserPromptSubmit hook: log user input before it reaches the LLM
def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    # return f"工作目录: {WORKDIR}\n用户输入: {query}"


# Stop hook: print summary when loop is about to exit
def summary_hook(messages: list):
    tool_count = sum(1 for m in messages if m.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")


DENY_LIST = [
    "rm -rf /",
    "sudo",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "> /dev/sda",
]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def require_approval(name: str, args: dict, reason: str) -> str | None:
    """要求人工批准。返回 None=放行,字符串=拒绝原因(与 hook 契约一致)。

    安全约定(fail closed):【问不到人 = 没得到许可 = 拒绝】。

    两道防线缺一不可:
      isatty()  挡住「读到了但那不是人的回答」—— 管道/重定向会读到数据流里的下一行
      try/except 挡住「压根读不到」—— /dev/null 抛 EOFError、Ctrl-C 抛 KeyboardInterrupt
    """
    if not sys.stdin.isatty():
        return (
            f"Permission denied: non-interactive environment, cannot confirm {reason}"
        )

    # 确定要问人了,才打印给人看的东西
    print(f"\n\033[33m⚠  {reason}\033[0m")
    print(f"   Tool: {name}({args})")
    try:
        choice = input("   Allow? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "Permission denied: no input available (EOF/interrupt)"
    # [y/N] 大写 N = 直接回车等于拒绝
    return None if choice in ("y", "yes") else "Permission denied by user"


def permission_hook(name, args):
    if name == "bash":
        for pattern in DENY_LIST:
            if pattern in args.get("command", ""):
                print(f"\n\033[31m⛔ Blocked: '{pattern}'\033[0m")
                return "Permission denied by deny list"
        for kw in DESTRUCTIVE:
            if kw in args.get("command", ""):
                denied = require_approval(name, args, "potentially destructive command")
                if denied:
                    return denied
    if name in ("write_file", "edit_file"):
        path = args.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            denied = require_approval(name, args, "writing outside workspace")
            if denied:
                return denied
    return None


register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", log_hook)
register_hook("PreToolUse", permission_hook)
register_hook("Stop", summary_hook)


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Task(BaseModel):
    id: str = Field(..., description="Unique identifier for the task")
    subject: str = Field(..., description="Subject or title of the task")
    description: str = Field(..., description="Description of the task")
    status: TaskStatus = Field(TaskStatus.PENDING, description="Status of the task")
    owner: str | None = Field(None, description="Owner of the task")
    blockedBy: list[str] = Field(
        default_factory=list, description="List of task IDs that block this task"
    )
    worktree: str | None = Field(default=None, description="Associated worktree name")


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def create_task(
    subject: str, description: str = "", blockedBy: list[str] | None = None
) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject,
        description=description,
        status=TaskStatus.PENDING,
        owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def save_task(task: Task):
    _task_path(task.id).write_text(task.model_dump_json(indent=2))


def list_tasks() -> list[Task]:
    return [
        Task(**json.loads(p.read_text())) for p in sorted(TASKS_DIR.glob("task_*.json"))
    ]


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def get_task(task_id: str) -> str:
    """Return full task details as JSON."""
    task = load_task(task_id)
    return task.model_dump_json(indent=2)


def can_start(task_id: str) -> bool:
    """Check if all blockedBy dependencies are completed.
    Missing dependencies are treated as blocked."""
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != TaskStatus.COMPLETED:
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.PENDING:
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    if not can_start(task_id):
        deps = [
            d
            for d in task.blockedBy
            if _task_path(d).exists() and load_task(d).status != "completed"
        ]
        missing = [d for d in task.blockedBy if not _task_path(d).exists()]
        parts = []
        if deps:
            parts.append(f"blocked by: {deps}")
        if missing:
            parts.append(f"missing deps: {missing}")
        return "Cannot start — " + ", ".join(parts)
    task.owner = owner
    task.status = TaskStatus.IN_PROGRESS
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress (owner: {owner})\033[0m")
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        return f"Task {task_id} is {task.status}, cannot complete"
    task.status = TaskStatus.COMPLETED
    save_task(task)
    unblocked = [
        t.subject
        for t in list_tasks()
        if t.status == TaskStatus.PENDING and t.blockedBy and can_start(t.id)
    ]
    print(f"  \033[32m[complete] {task.subject} ✓\033[0m")
    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
        print(f"  \033[33m[unblocked] {', '.join(unblocked)}\033[0m")
    return msg


VALID_WT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def validate_worktree_name(name: str) -> str | None:
    """Return error message if invalid, None if valid."""
    if not name:
        return "Worktree name cannot be empty"
    if name == "." or name == "..":
        return f"'{name}' is not a valid worktree name"
    if not VALID_WT_NAME.match(name):
        return (
            f"Invalid worktree name '{name}': "
            "only letters, digits, dots, underscores, dashes (1-64 chars)"
        )
    return None


def run_git(args: list[str]) -> tuple[bool, str]:
    """Run git command. Return (ok, output)."""
    try:
        r = subprocess.run(
            ["git"] + args, cwd=WORKDIR, capture_output=True, text=True, timeout=30
        )
        out = (r.stdout + r.stderr).strip()
        out = out[:5000] if out else "(no output)"
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    """Append a lifecycle event to events.jsonl."""
    event = {
        "type": event_type,
        "worktree": worktree_name,
        "task_id": task_id,
        "ts": time.time(),
    }
    events_file = WORKTREES_DIR / "events.jsonl"
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


def create_worktree(name: str, task_id: str = "") -> str:
    """Create a git worktree with a dedicated branch. Optionally bind to a task."""
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    path = WORKTREES_DIR / name
    if path.exists():
        return f"Worktree '{name}' already exists at {path}"
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        return f"Git error: {result}"
    if task_id:
        bind_task_to_worktree(task_id, name)
    log_event("create", name, task_id)
    print(f"  \033[33m[worktree] created: {name} at {path}\033[0m")
    return f"Worktree '{name}' created at {path}"


def bind_task_to_worktree(task_id: str, worktree_name: str):
    """Write worktree field to task. Keep status as pending for auto-claim."""
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)
    print(f"  \033[33m[bind] {task.subject} → worktree:{worktree_name}\033[0m")


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    """Count uncommitted files and commits in a worktree."""
    try:
        r1 = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = len([l for l in r1.stdout.strip().splitlines() if l.strip()])
        r2 = subprocess.run(
            ["git", "log", "@{push}..HEAD", "--oneline"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        commits = len([l for l in r2.stdout.strip().splitlines() if l.strip()])
        return files, commits
    except Exception:
        return -1, -1


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    """Remove worktree. Refuses if uncommitted changes unless discard_changes."""
    err = validate_worktree_name(name)
    if err:
        return err
    path = WORKTREES_DIR / name
    if not path.exists():
        return f"Worktree '{name}' not found"
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return (
                f"Cannot verify worktree '{name}' status. "
                "Use discard_changes=true to force removal."
            )
        if files > 0 or commits > 0:
            return (
                f"Worktree '{name}' has {files} uncommitted file(s) "
                f"and {commits} unpushed commit(s). "
                "Use discard_changes=true to force removal, "
                "or keep_worktree to preserve for review."
            )
    ok1, _ = run_git(["worktree", "remove", str(path), "--force"])
    if not ok1:
        return f"Failed to remove worktree directory for '{name}'"
    run_git(["branch", "-D", f"wt/{name}"])
    log_event("remove", name)
    print(f"  \033[33m[worktree] removed: {name}\033[0m")
    return f"Worktree '{name}' removed"


def keep_worktree(name: str) -> str:
    """Keep worktree for manual review. Branch preserved."""
    err = validate_worktree_name(name)
    if err:
        return err
    log_event("keep", name)
    print(f"  \033[36m[worktree] kept: {name}\033[0m")
    return f"Worktree '{name}' kept for review (branch: wt/{name})"


class CronJob(BaseModel):
    id: str = Field(..., description="Unique identifier for the cron job")
    cron: str = Field(..., description="Cron expression for scheduling")  # "0 9 * * *"
    prompt: str = Field(..., description="Prompt to send to the agent")
    recurring: bool = Field(default=True, description="Whether the job is recurring")
    durable: bool = Field(default=True, description="Whether the job is saved to disk")


scheduled_jobs: dict[str, CronJob] = {}
cron_queue: list[CronJob] = []
cron_lock = threading.Lock()
agent_lock = threading.Lock()
_last_fired: dict[str, str] = {}  # job_id → "YYYY-MM-DD HH:MM"


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    if "," in field:
        return any(_cron_field_matches(f.strip(), value) for f in field.split(","))
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    if "-" in field:
        lo, hi = field.split("-", 1)
        return int(lo) <= value <= int(hi)
    return int(field) == value


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Check if a cron expression matches the given datetime."""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    dow_val = (dt.weekday() + 1) % 7  # Python: Mon=0, Sun=6; Cron: Sun=0, Sat=6

    m = _cron_field_matches(minute, dt.minute)
    h = _cron_field_matches(hour, dt.hour)
    d = _cron_field_matches(dom, dt.day)
    mo = _cron_field_matches(month, dt.month)
    w = _cron_field_matches(dow, dow_val)
    if not (m and h and mo):
        return False
    dom_uncertain = dom == "*"
    dow_uncertain = dow == "*"
    if dom_uncertain and dow_uncertain:
        return True
    if dom_uncertain:
        return w
    if dow_uncertain:
        return d
    return d or w


def _validate_cron_field(field: str, lo: int, hi: int) -> str | None:
    """Validate a single cron field value is within [lo, hi]."""
    if field == "*":
        return None
    if field.startswith("*/"):
        step_str = field[2:]
        if not step_str.isdigit():
            return f"Invalid step: {field}"
        step = int(step_str)
        if step <= 0:
            return f"Step must be > 0: {field}"
        return None
    if "," in field:
        for part in field.split(","):
            err = _validate_cron_field(part.strip(), lo, hi)
            if err:
                return err
        return None
    if "-" in field:
        parts = field.split("-", 1)
        if not parts[0].isdigit() or not parts[1].isdigit():
            return f"Invalid range: {field}"
        a, b = int(parts[0]), int(parts[1])
        if a < lo or a > hi or b < lo or b > hi:
            return f"Range {field} out of bounds [{lo}-{hi}]"
        if a > b:
            return f"Range start > end: {field}"
        return None
    if not field.isdigit():
        return f"Invalid field: {field}"
    val = int(field)
    if val < lo or val > hi:
        return f"Value {val} out of bounds [{lo}-{hi}]"
    return None


def validate_cron(cron_expr: str) -> str | None:
    """Validate a cron expression. Returns error message or None."""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields, got {len(fields)}"
    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
    for i, (field, (lo, hi), name) in enumerate(zip(fields, bounds, names)):
        err = _validate_cron_field(field, lo, hi)
        if err:
            return f"{name}: {err}"
    return None


def save_durable_jobs():
    """Save durable scheduled jobs to disk."""
    DURABLE_PATH.write_text(
        json.dumps(
            [j.model_dump() for j in scheduled_jobs.values() if j.durable], indent=2
        )
    )


def load_durable_jobs():
    """Load durable jobs from disk on startup."""
    if not DURABLE_PATH.exists():
        return
    try:
        jobs = json.loads(DURABLE_PATH.read_text())
        for j in jobs:
            job = CronJob(**j)
            err = validate_cron(job.cron)
            if err:
                print(f"  \033[31m[cron] skipping invalid job {job.id}: {err}\033[0m")
                continue
            scheduled_jobs[job.id] = job
        valid = [j for j in jobs if j["id"] in scheduled_jobs]
        if valid:
            print(f"  \033[35m[cron] loaded {len(valid)} durable job(s)\033[0m")
    except Exception:
        pass


def schedule_job(
    cron: str, prompt: str, recurring: bool = True, durable: bool = True
) -> CronJob | str:
    """Register a new cron job. Returns CronJob or error string."""
    err = validate_cron(cron)
    if err:
        return err
    job = CronJob(
        id=f"cron_{random.randint(0, 999999):06d}",
        cron=cron,
        prompt=prompt,
        recurring=recurring,
        durable=durable,
    )
    with cron_lock:
        scheduled_jobs[job.id] = job
    if durable:
        save_durable_jobs()
    print(f"  \033[35m[cron register] {job.id} '{cron}' → {prompt[:40]}\033[0m")
    return job


def cancel_job(job_id: str) -> str:
    """Cancel a cron job."""
    with cron_lock:
        job = scheduled_jobs.pop(job_id, None)
    if not job:
        return f"Job {job_id} not found"
    if job.durable:
        save_durable_jobs()
    print(f"  \033[31m[cron cancel] {job_id}\033[0m")
    return f"Cancelled {job_id}"


def cron_scheduler_loop():
    """Independent daemon thread: poll every 1s, fire matching jobs.
    Individual job errors are caught to prevent one bad job from
    killing the entire scheduler thread."""
    while True:
        time.sleep(1)
        now = datetime.now()
        # Date-aware marker prevents daily jobs from skipping on day 2+
        minute_marker = now.strftime("%Y-%m-%d %H:%M")
        with cron_lock:
            for job in list(scheduled_jobs.values()):  # 注意这个list
                try:
                    if cron_matches(job.cron, now):
                        if _last_fired.get(job.id) != minute_marker:
                            cron_queue.append(job)
                            _last_fired[job.id] = minute_marker
                            print(
                                f"  \033[35m[cron fire] {job.id} → "
                                f"{job.prompt[:40]}\033[0m"
                            )
                        if not job.recurring:
                            scheduled_jobs.pop(job.id, None)
                            if job.durable:
                                save_durable_jobs()
                except Exception as e:
                    print(f"  \033[31m[cron error] {job.id}: {e}\033[0m")


def consume_cron_queue() -> list[CronJob]:
    """Consume fired jobs from cron_queue (called by agent_loop)."""
    with cron_lock:
        fired = list(cron_queue)
        cron_queue.clear()
    return fired


def has_cron_queue() -> bool:
    """Return whether fired cron jobs are waiting to be delivered."""
    with cron_lock:
        return bool(cron_queue)


def start_cron_scheduler():
    """载入持久化 job 并起调度线程。只在启动时调用——import 不该起后台线程。"""
    load_durable_jobs()
    threading.Thread(target=cron_scheduler_loop, daemon=True).start()
    print("  \033[35m[cron] scheduler thread started\033[0m")


class MessageBus:
    def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "message",
        metadata: dict | None = None,
    ):
        msg = {
            "from": from_agent,
            "to": to_agent,
            "content": content,
            "type": msg_type,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg) + "\n")
        print(f"  \033[33m[bus] {from_agent} → {to_agent}: {content[:50]}\033[0m")

    def read_inbox(self, agent_name: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent_name}.jsonl"
        if not inbox.exists():
            return []
        msgs = [
            json.loads(line)
            for line in inbox.read_text().strip().splitlines()
            if line.strip()
        ]
        inbox.unlink()
        return msgs


BUS = MessageBus()

# Track spawned teammates
active_teammates: dict[str, bool] = {}


class ProtocolStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProtocolState(BaseModel):
    request_id: str = Field(
        ..., description="Unique identifier for the protocol request"
    )
    type: str = Field(..., description="Type of the protocol request")
    sender: str = Field(..., description="Name of the sender agent")
    target: str = Field(..., description="Name of the target agent")
    status: ProtocolStatus = Field(
        ProtocolStatus.PENDING, description="Current status of the protocol request"
    )
    payload: str = Field(
        ...,
        description="Additional data or message associated with the protocol request",
    )
    created_at: float = Field(
        default_factory=time.time,
        description="Timestamp when the protocol request was created",
    )


pending_requests: dict[str, ProtocolState] = {}


def match_response(response_type: str, request_id: str, approve: bool):
    state = pending_requests.get(request_id)
    if not state:
        print(f"  \033[31m[protocol] unknown request_id: {request_id}\033[0m")
        return
    # Validate response type matches request type
    if state.type == "shutdown" and response_type != "shutdown_response":
        print(
            f"  \033[31m[protocol] type mismatch: expected shutdown_response, "
            f"got {response_type}\033[0m"
        )
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        print(
            f"  \033[31m[protocol] type mismatch: expected plan_approval_response, "
            f"got {response_type}\033[0m"
        )
        return
    if state.status != ProtocolStatus.PENDING:
        print(
            f"  \033[33m[protocol] {request_id} already {state.status}, "
            f"ignoring duplicate\033[0m"
        )
        return
    state.status = ProtocolStatus.APPROVED if approve else ProtocolStatus.REJECTED
    icon = "✓" if approve else "✗"
    color = "32" if approve else "31"
    print(
        f"  \033[{color}m[protocol] {state.type} {icon} "
        f"({request_id}: {state.status})\033[0m"
    )


def consume_lead_inbox(route_protocol: bool = True) -> list[dict]:
    """Read Lead's inbox. Route protocol responses, return all messages.
    Called by both run_check_inbox() and main loop to avoid
    messages being consumed without protocol routing."""
    msgs = BUS.read_inbox("lead")
    if not msgs:
        return []
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                approve = meta.get("approve", False)
                match_response(msg_type, req_id, approve)
    return msgs


def new_request_id() -> str:
    return f"req_{random.randint(0, 999999):06d}"


class SendMessageArgs(BaseModel):
    to: str = Field(..., description="the recipient teammate's name")
    content: str = Field(..., description="the message content to send")


class SubmitPlanArgs(BaseModel):
    plan: str = Field(..., description="the plan content to submit to Lead")


IDLE_POLL_INTERVAL = 5  # seconds
IDLE_TIMEOUT = 60  # seconds


class IdleResult(StrEnum):
    WORK = "work"
    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


def scan_unclaimed_tasks() -> list[dict]:
    unclaimed = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text())
        if (
            task.get("status") == "pending"
            and not task.get("owner")
            and can_start(task["id"])
        ):
            unclaimed.append(task)
    return unclaimed


def idle_poll(agent_name: str, messages: list, name: str, role: str) -> IdleResult:
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        inbox = BUS.read_inbox(agent_name)
        if inbox:
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(
                        agent_name,
                        "lead",
                        "Shutting down gracefully.",
                        "shutdown_response",
                        {"request_id": req_id, "approve": True},
                    )
                    print(
                        f"  \033[35m[protocol] {agent_name} approved shutdown ({req_id})\033[0m"
                    )
                    return IdleResult.SHUTDOWN
            # Non-protocol inbox: inject and resume work
            messages.append(
                {"role": "user", "content": "<inbox>" + json.dumps(inbox) + "</inbox>"}
            )
            print(f"  \033[36m[idle] {name} found inbox messages\033[0m")
            return IdleResult.WORK
        unclaimed_tasks = scan_unclaimed_tasks()
        if unclaimed_tasks:
            task_data = unclaimed_tasks[0]
            result = claim_task(task_data["id"], owner=agent_name)
            if "Claimed" in result:
                wt_info = ""
                if task_data.get("worktree"):
                    wt_path = WORKTREES_DIR / task_data["worktree"]
                    wt_info = f"\nWork directory: {wt_path}"
                messages.append(
                    {
                        "role": "user",
                        "content": f"<auto-claimed>Task {task_data['id']}: "
                        f"{task_data['subject']}{wt_info}</auto-claimed>",
                    }
                )
                print(
                    f"  \033[32m[idle] {name} auto-claimed: "
                    f"{task_data['subject']}\033[0m"
                )
                return IdleResult.WORK
            print(f"  \033[33m[idle] {name} claim failed: {result}\033[0m")

    print(f"  \033[31m[idle] {name} timeout ({IDLE_TIMEOUT}s)\033[0m")
    return IdleResult.TIMEOUT


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    """Spawn a teammate agent in a background thread.
    Teaching version: max 10 rounds per teammate.
    Real CC: teammates use idle loop (wait for inbox, work, repeat)
    until shutdown_request."""
    if name in active_teammates:
        return f"Teammate '{name}' already exists"

    system = (
        f"You are '{name}', a {role}. "
        f"Use tools to complete tasks. "
        f"You can list and claim tasks from the board. "
        f"If a task has a worktree, work in that directory."
    )

    def handle_inbox_message(name: str, msg: dict, messages: list) -> bool:
        """Dispatch incoming protocol messages by type.
        Returns True if teammate should stop."""
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")

        if msg_type == "shutdown_request":
            BUS.send(
                name,
                "lead",
                "Shutting down gracefully.",
                "shutdown_response",
                {"request_id": req_id, "approve": True},
            )
            print(f"  \033[35m[protocol] {name} approved shutdown ({req_id})\033[0m")
            return True  # stop the loop

        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            if approve:
                messages.append(
                    {
                        "role": "user",
                        "content": "[Plan approved] Proceed with the task.",
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[Plan rejected] Feedback: {msg['content']}",
                    }
                )

        return False  # continue

    def run():
        wt_ctx = {"path": None}

        def _wt_cwd() -> Path | None:
            p = wt_ctx["path"]
            return Path(p) if p else None

        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": prompt})
        sub_tool_registry = {
            "bash": builtin("Run a shell command.", BashArgs, _run_bash),
            "send_message": builtin(
                "Send a message to another agent.",
                SendMessageArgs,
                lambda to, content: (BUS.send(name, to, content), "Sent")[1],
            ),
            "submit_plan": builtin(
                "Submit a plan to Lead for approval.",
                SubmitPlanArgs,
                lambda plan: _teammate_submit_plan(name, plan),
            ),
            "list_tasks": builtin(
                "List all tasks.",
                NoneArgs,
                lambda: _run_list_tasks(),
            ),
            "claim_task": builtin(
                "Claim a task.",
                TaskIdArgs,
                lambda task_id: _run_claim_task(task_id),
            ),
            "complete_task": builtin(
                "Complete a task.",
                TaskIdArgs,
                lambda task_id: _run_complete_task(task_id),
            ),
        }

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return "No tasks."
            return "\n".join(
                f"  {t.id}: {t.subject} [{t.status}]"
                + (f" (wt:{t.worktree})" if t.worktree else "")
                for t in tasks
            )

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "Claimed" in result:
                # Set worktree cwd if task has one
                task = load_task(task_id)
                if task.worktree:
                    wt_ctx["path"] = str(WORKTREES_DIR / task.worktree)
                else:
                    wt_ctx["path"] = None
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            wt_ctx["path"] = None
            return result

        sub_tools = [
            {
                "type": "function",
                "function": {
                    "name": t_name,
                    "description": entry.description,
                    "parameters": entry.schema,
                },
            }
            for t_name, entry in sub_tool_registry.items()
        ]

        while True:
            if len(messages) <= 3:
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"<identity>You are '{name}', role: {role}. "
                        f"Continue your work.</identity>",
                    },
                )
            should_shutdown = False
            # inbox = BUS.read_inbox(name)
            # should_stop = False
            # non_protocol = []
            # for msg in inbox:
            #     if msg.get("type") in ("shutdown_request", "plan_approval_response"):
            #         should_stop = handle_inbox_message(name, msg, messages)
            #         if should_stop:
            #             break
            #     else:
            #         non_protocol.append(msg)
            # if should_stop:
            #     shutdown_requested = True
            #     break
            # if non_protocol:
            #     inbox_json = json.dumps(non_protocol)
            #     messages.append(
            #         {"role": "user", "content": "<inbox>" + inbox_json + "</inbox>"}
            #     )
            for _ in range(10):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break
                if should_shutdown:
                    break
                if inbox and not should_shutdown:
                    non_protocol = [m for m in inbox if m.get("type") == "message"]
                    if non_protocol:
                        messages.append(
                            {
                                "role": "user",
                                "content": f"<inbox>{json.dumps(non_protocol)}</inbox>",
                            }
                        )
                if len(messages) > 20:
                    messages = [messages[0]] + messages[-20:]
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,  # 第一条要保留
                        tools=sub_tools,
                        tool_choice="auto",
                        max_tokens=8000,
                    )
                except Exception as e:
                    print(f"  \033[31m[teammate] {name} error: {e}\033[0m")
                    break
                msg = response.choices[0].message
                messages.append(msg.model_dump(exclude_none=True))
                if not msg.tool_calls:
                    break
                    # # Idle: wait for inbox messages instead of exiting
                    # # Real CC sends idle_notification to Lead here
                    # while not shutdown_requested:
                    #     time.sleep(1)
                    #     inbox = BUS.read_inbox(name)
                    #     if not inbox:
                    #         continue
                    #     for msg in inbox:
                    #         if msg.get("type") in (
                    #             "shutdown_request",
                    #             "plan_approval_response",
                    #         ):
                    #             should_stop = handle_inbox_message(name, msg, messages)
                    #             if should_stop:
                    #                 shutdown_requested = True
                    #                 break
                    #         else:
                    #             non_protocol.append(msg)
                    #     if shutdown_requested:
                    #         break
                    #     if non_protocol:
                    #         inbox_json = json.dumps(non_protocol)
                    #         messages.append(
                    #             {
                    #                 "role": "user",
                    #                 "content": "<inbox>" + inbox_json + "</inbox>",
                    #             }
                    #         )
                    #         break  # back to LLM turn with new messages
                for tc in msg.tool_calls:
                    entry = sub_tool_registry.get(tc.function.name)
                    if not entry:
                        output = f"Error: unknown tool '{tc.function.name}'"
                    else:
                        handler = entry.handler
                        if entry.validator is not None:
                            try:
                                args = entry.validator.model_validate_json(
                                    tc.function.arguments or "{}"
                                )
                            except Exception as e:
                                output = f"Error: invalid arguments for {tc.function.name}: {e}"
                            else:
                                output = handler(**args.model_dump())
                        else:
                            args = tc.function.arguments or "{}"
                            output = handler(**json.loads(args))
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": output}
                    )
            if should_shutdown:
                break

            # IDLE phase (s17 new)
            idle_result = idle_poll(name, messages, name, role)
            if idle_result == IdleResult.SHUTDOWN:
                break
            if idle_result == IdleResult.TIMEOUT:
                break

        # Send final summary to Lead
        summary = "Done."
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                summary = msg.get("content", "Done.")
                break
        BUS.send(name, "lead", summary, "result")
        active_teammates.pop(name, None)
        print(f"  \033[32m[teammate] {name} finished\033[0m")

    active_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    print(f"  \033[36m[teammate] {name} spawned as {role}\033[0m")
    return f"Teammate '{name}' spawned as {role}(autonomous)"


def _teammate_submit_plan(from_name: str, plan: str) -> str:
    """Teammate submits a plan to Lead for approval.

    Note: This is a protocol-level request, not a code-level gate.
    After submitting, the teammate's thread continues running — it can
    still call bash/write/etc. Real enforcement relies on the model
    waiting for the approval response before acting. Code-level tool
    gating would require blocking the teammate's tool dispatch until
    approval arrives.
    """
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="plan_approval",
        sender=from_name,
        target="lead",
        status=ProtocolStatus.PENDING,
        payload=plan,
    )
    BUS.send(from_name, "lead", plan, "plan_approval_request", {"request_id": req_id})
    return f"Plan submitted ({req_id}). Waiting for approval..."


# ── Team Tool Handlers (s15 new) ──
def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="shutdown",
        sender="lead",
        target=teammate,
        status=ProtocolStatus.PENDING,
        payload="",
    )
    BUS.send(
        "lead",
        teammate,
        "Please shut down gracefully.",
        "shutdown_request",
        {"request_id": req_id},
    )
    print(f"  \033[35m[protocol] shutdown_request → {teammate} ({req_id})\033[0m")
    return f"Shutdown request sent to {teammate} (req: {req_id})"


def run_request_plan(teammate: str, task: str) -> str:
    """Lead asks a teammate to submit a plan for a task."""
    BUS.send("lead", teammate, f"Please submit a plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"


def run_review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    if state.status != ProtocolStatus.PENDING:
        return f"Request {request_id} already {state.status}"
    state.status = ProtocolStatus.APPROVED if approve else ProtocolStatus.REJECTED
    BUS.send(
        "lead",
        state.sender,
        feedback or ("Approved" if approve else "Rejected"),
        "plan_approval_response",
        {"request_id": request_id, "approve": approve},
    )
    icon = "✓" if approve else "✗"
    print(f"  \033[32m[protocol] plan {icon} ({request_id})\033[0m")
    return f"Plan {'approved' if approve else 'rejected'} ({request_id})"


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(f"  [{m['from']}]{tag} {m['content'][:200]}")
    return "\n".join(lines)


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")
    run_in_background: bool = Field(
        False, description="whether to run the command in the background"
    )
    cwd: Path | None = Field(
        default=None, description="the working directory for the command"
    )


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="the path of the file to read")
    limit: int | None = Field(None, description="maximum number of lines to read")


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="path to write the file to")
    content: str = Field(..., description="content to write into the file")


class EditFileArgs(BaseModel):
    path: str = Field(..., description="path to the file to edit")
    old_text: str = Field(..., description="exact text to find and replace")
    new_text: str = Field(..., description="text to replace the old_text with")


class GlobArgs(BaseModel):
    pattern: str = Field(..., description="glob pattern to search for files")
    limit: int | None = Field(None, description="maximum number of files to return")


class TodoItem(BaseModel):
    content: str = Field(..., description="the content of the todo item")
    status: Literal["pending", "in_progress", "completed"] = Field(
        ..., description="the status of the todo item"
    )


class TodoWriteArgs(BaseModel):
    todos: list[TodoItem] = Field(
        ..., description="list of todo items with content and status"
    )


class CompactArgs(BaseModel):
    focus: str = Field(
        ..., description="Summarize earlier conversation to free context space."
    )


class CreateTaskArgs(BaseModel):
    subject: str = Field(..., description="the subject of the task")
    description: str = Field("", description="the description of the task")
    blockedBy: list[str] | None = Field(
        None, description="list of task IDs that block this task"
    )


class TaskIdArgs(BaseModel):
    task_id: str = Field(..., description="the ID of the task to operate on")


class NoneArgs(BaseModel):
    pass


class ScheduleCronArgs(BaseModel):
    cron: str = Field(..., description="the cron expression for scheduling")
    prompt: str = Field(..., description="the prompt to send to the agent")
    recurring: bool = Field(default=True, description="whether the job is recurring")
    durable: bool = Field(default=True, description="whether the job is saved to disk")


class CancelCronArgs(BaseModel):
    job_id: str = Field(..., description="the ID of the cron job to cancel")


class RequestShutdownArgs(BaseModel):
    teammate: str = Field(
        ..., description="the name of the teammate to request shutdown"
    )


class RequestPlanArgs(BaseModel):
    teammate: str = Field(
        ..., description="the name of the teammate to request a plan from"
    )
    task: str = Field(..., description="the task for which to request a plan")


class ReviewPlanArgs(BaseModel):
    request_id: str = Field(..., description="the ID of the plan approval request")
    approve: bool = Field(..., description="whether to approve or reject the plan")
    feedback: str = Field("", description="optional feedback for the teammate")


class SpawnTeammateArgs(BaseModel):
    name: str = Field(..., description="the name of the teammate agent")
    role: str = Field(..., description="the role or persona of the teammate agent")
    prompt: str = Field(..., description="the initial prompt for the teammate agent")


class MemoryArgs(BaseModel):
    command: Literal["view", "create", "str_replace", "insert", "delete", "rename"] = (
        Field(..., description="The memory operation to perform")
    )
    path: str | None = Field(
        None,
        description="Virtual path starting with /memories (for view/create/str_replace/insert/delete) ... e.g. /memories/preferences.md",
    )
    view_range: list[int] | None = Field(
        None,
        description="view only: [start_line, end_line]; end -1 means to end of file",
    )
    file_text: str | None = Field(
        None, description="create only: full file content to write"
    )
    old_str: str | None = Field(
        None,
        description="str_replace only: exact text to find, must appear exactly once",
    )
    new_str: str | None = Field(
        None, description="str_replace only: replacement; omit to delete old_str"
    )
    insert_line: int | None = Field(
        None, description="insert only: insert after this line number; 0 = top of file"
    )
    insert_text: str | None = Field(None, description="insert only: the text to insert")
    old_path: str | None = Field(None, description="rename only: source path")
    new_path: str | None = Field(None, description="rename only: destination path")


class SkillsArgs(BaseModel):
    name: str = Field(..., description="name of the skill to load")


class CreateWorktreeArgs(BaseModel):
    name: str = Field(..., description="the name of the worktree")
    task_id: str = Field(
        ..., description="the ID of the task associated with the worktree"
    )


class RemoveWorktreeArgs(BaseModel):
    name: str = Field(..., description="the name of the worktree to remove")
    discard_changes: bool = Field(
        default=False, description="whether to discard changes in the worktree"
    )


class KeepWorktreeArgs(BaseModel):
    name: str = Field(..., description="the name of the worktree to keep")


def run_bash(
    command: str, run_in_background: bool = False, cwd: Path | None = None
) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            output = "(no output)"
        return output[:50000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {e!s}"


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int | None = None) -> str:
    try:
        file_path = safe_path(path)
        lines = file_path.read_text().splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e!s}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e!s}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e!s}"


def run_glob(pattern: str, limit: int | None = None) -> str:
    import glob as g

    try:
        results = []
        for match in sorted(g.glob(pattern, root_dir=WORKDIR, recursive=True)):
            resolved = (WORKDIR / match).resolve()
            # is_relative_to 把「等于」也算「在里面」,所以自引用路径(../<工作目录名>)
            # 会漏网;显式排除 WORKDIR 自身。
            if resolved == WORKDIR or not resolved.is_relative_to(WORKDIR):
                continue
            # 输出统一成相对 WORKDIR 的干净路径,避免 ../ 开头的形式流到下游
            results.append(str(resolved.relative_to(WORKDIR)))
        if limit is not None and limit < len(results):
            results = results[:limit] + [f"... ({len(results) - limit} more matches)"]
            return "\n".join(results)
        if len(results) > MAX_GLOB_RESULTS:
            results = results[:MAX_GLOB_RESULTS] + [
                f"... ({len(results) - MAX_GLOB_RESULTS} more matches)"
            ]
            return "\n".join(results)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e!s}"


def run_todo_write(todos: list) -> str:
    global CURRENT_TODOS
    CURRENT_TODOS = todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for t in CURRENT_TODOS:
        icon = {
            "pending": " ",
            "in_progress": "\033[36m▸\033[0m",
            "completed": "\033[32m✓\033[0m",
        }[t["status"]]
        lines.append(f"  [{icon}] {t['content']}")
    print("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"


def resolve_memory_path(path: str) -> Path:
    first_path = Path("/memories")
    p = Path(path)
    if not p.is_relative_to(first_path):
        raise ValueError(
            f"Error: Invalid path '{path}'. All paths must start with /memories — for example /memories/notes.md"
        )
    relative_path = p.relative_to(first_path)
    real_path = (MEMORY_DIR / relative_path).resolve()
    if not real_path.is_relative_to(MEMORY_DIR.resolve()):
        raise ValueError(
            f"Error: Invalid path '{path}'. All paths must start with /memories — for example /memories/notes.md"
        )
    return real_path


def run_memory_view(path: str, view_range: list[int] | None = None) -> str:
    real = resolve_memory_path(path)
    if not real.exists():
        return f"The path {path} does not exist. Please provide a valid path."
    if real.is_dir():
        return _format_dir(real, path)
    else:
        return _format_file(real, path, view_range)


def _human_size(size):
    return f"{max(size, 1) / 1024:.1f}K"


def _format_file(real: Path, virtual: str, view_range: list[int] | None = None) -> str:
    lines = real.read_text().splitlines()
    start, end = 1, len(lines)
    if view_range is not None:
        start = view_range[0] if view_range[0] else 1
        end = view_range[1] if view_range[1] != -1 else len(lines)
    out = [f"Here's the content of {virtual} with line numbers:"]
    for i in range(start, end + 1):
        out.append(f"{i:>6}\t{lines[i - 1]}")

    return "\n".join(out)


def _format_dir(real: Path, virtual: str) -> str:
    out = [
        f"Here're the files and directories up to 2 levels deep in {virtual}, excluding hidden items and node_modules:"
    ]
    out.append(f"{_human_size(real.stat().st_size)}\t{virtual}")
    for entry in sorted(real.glob("*")) + sorted(real.glob("*/*")):
        if any(
            en.startswith(".") or en == "node_modules" or en == "MEMORY.md"
            for en in entry.relative_to(real).parts
        ):
            continue
        temp = virtual / entry.relative_to(real)
        out.append(f"{_human_size(entry.stat().st_size)}\t{temp}")
    return "\n".join(out)


def run_memory_create(path: str, file_text: str) -> str:
    real = resolve_memory_path(path)
    if real.name == "MEMORY.md":
        return f"Error: The path {path} is reserved"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(file_text)
    _rebuild_index()
    return f"File created successfully at: {path}"


def run_memory_str_replace(path: str, old_str: str, new_str: str | None = None) -> str:
    real = resolve_memory_path(path)
    if not real.is_file():
        return f"Error: The path {path} does not exist. Please provide a valid path."
    if real.name == "MEMORY.md":
        return f"Error: The path {path} is reserved"
    text = real.read_text()
    if old_str not in text:
        return f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
    counts = text.count(old_str)
    if counts == 1:
        if new_str is None:
            new_str = ""
        text = text.replace(old_str, new_str)
        real.write_text(text)
        _rebuild_index()
        return "The memory file has been edited."
    else:
        lines = text.splitlines()
        line_numbers = []
        for i, line in enumerate(lines):
            if old_str in line:
                line_numbers.append(i + 1)
        return f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: {line_numbers}. Please ensure it is unique"


def run_memory_insert(path: str, insert_line: int, insert_text: str) -> str:
    real = resolve_memory_path(path)
    if not real.is_file():
        return f"Error: The path {path} does not exist"
    if real.name == "MEMORY.md":
        return f"Error: The path {path} is reserved"
    lines = real.read_text().splitlines()
    if insert_line < 0 or insert_line > len(lines):
        return f"Error: Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the file: [0, {len(lines)}]"
    lines.insert(insert_line, insert_text)
    real.write_text("\n".join(lines))
    _rebuild_index()
    return f"The file {path} has been edited."


def run_memory_delete(path: str) -> str:
    if path == "/memories":
        return "Error: Cannot delete the root memory directory."
    real = resolve_memory_path(path)
    if not real.exists():
        return f"Error: The path {path} does not exist"
    if real.name == "MEMORY.md":
        return f"Error: The path {path} is reserved"
    if real.is_dir():
        shutil.rmtree(real)
    else:
        real.unlink()
    _rebuild_index()
    return f"Successfully deleted {path}"


def run_memory_rename(old_path: str, new_path: str) -> str:
    if old_path == "/memories" or new_path == "/memories":
        return "Error: Cannot move the root memory directory."
    old_real = resolve_memory_path(old_path)
    new_real = resolve_memory_path(new_path)
    if old_real.name == "MEMORY.md" or new_real.name == "MEMORY.md":
        return "Error: Cannot move MEMORY.md"
    if not old_real.exists():
        return f"Error: The path {old_path} does not exist"
    if new_real.exists():
        return f"Error: The destination {new_path} already exists"
    new_real.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_real), str(new_real))
    _rebuild_index()
    return f"Successfully renamed {old_path} to {new_path}"


def run_memory(command, **kwargs) -> str:
    try:
        if command == "view":
            return run_memory_view(kwargs["path"], kwargs.get("view_range"))
        elif command == "create":
            return run_memory_create(kwargs["path"], kwargs["file_text"])
        elif command == "str_replace":
            return run_memory_str_replace(
                kwargs["path"], kwargs["old_str"], kwargs.get("new_str")
            )
        elif command == "insert":
            return run_memory_insert(
                kwargs["path"], kwargs["insert_line"], kwargs["insert_text"]
            )
        elif command == "delete":
            return run_memory_delete(kwargs["path"])
        elif command == "rename":
            return run_memory_rename(kwargs["old_path"], kwargs["new_path"])
        else:
            return f"Error: unknown memory command {command}"
    except (ValueError, TypeError, KeyError) as e:
        return f"Error: {e!s}"


class CompactArgs(BaseModel):
    focus: str = Field(
        "", description="What to keep in focus while summarizing (optional)."
    )


def run_compact(focus: str = "") -> str:
    """占位 handler:compact 实际由 agent_loop 的特判分支处理。

    为什么不能走普通 handler:压缩要【改写 messages 本身】,而 handler 的签名是
    handler(**args) —— 它只拿得到自己的参数,拿不到会话历史。真正干活的是
    agent_loop 里 `if tc.function.name == "compact"` 那一支(调 try_compact(force=True),
    链路 try_compact → compact_history → write_transcript + summarize_history)。

    ⚠️ 为什么不像 09 那样直接把 compact_history 填在这儿:
        compact_history(msgs) 收的是位置参数 msgs,而这里会以 compact_history(focus=...)
        被调用 → TypeError。09 里没炸只因为特判在前面拦住了,永远走不到。
        【填一个"看起来能用其实不能"的函数,比填一个明确的占位更危险】——
        读代码的人会以为调 compact 就等于执行 compact_history。
    """
    return (
        "[compact] Compaction is handled by the agent loop, not by this tool call. "
        "If you still see a long history in the next turn, the compaction did not "
        "trigger — do not call compact again, continue with the task instead."
    )


class SubagentArgs(BaseModel):
    description: str = Field(
        ..., description="The self-contained subtask for the subagent to complete."
    )


def spawn_subagent(description: str) -> str:
    """一次性子代理:全新上下文进去,只带一句总结回来。

    与主干已有的 spawn_teammate 是两种东西,别混:
        subagent  一次性 / 同步 / 不留状态 / 只回结论      —— s06 的双向保护
        teammate  长期存活 / 异步线程 / 有 inbox 和协议往返 —— s17 的团队协作

    「双向保护」是它的全部意义:
        全新 messages  → 保【子】:它的注意力全在这个子任务上,不被主线历史干扰
        只回结论       → 保【主】:子任务的一堆中间步骤不会灌进主上下文

    ⚠️ 安全:子代理执行工具时【同样要过 PreToolUse】。
       否则模型被 permission 拦下后,只要 spawn 一个子代理去干同一件事就绕过去了 ——
       跟「用 bash 绕过 write_file 的路径检查」是同一类漏洞。
    """
    # 排除三个:todo_write(主的待办与子无关)、spawn_subagent 与 spawn_teammate
    # (防止无限套娃 —— SUB_SYSTEM 里也明说了 "Do not delegate further")
    sub_registry = {
        name: entry
        for name, entry in assemble_tool_pool().items()
        if name not in ("todo_write", "spawn_subagent", "spawn_teammate")
    }
    sub_tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": entry.description,
                "parameters": entry.schema,
            },
        }
        for name, entry in sub_registry.items()
    ]

    messages = [
        {"role": "system", "content": SUB_SYSTEM},
        {"role": "user", "content": description},
    ]
    print(f"  \033[35m[subagent] start: {description[:60]}\033[0m")

    for _ in range(MAX_SUBAGENT_TURNS):
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=sub_tools,
            tool_choice="auto",
            stream=True,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        text, tool_calls, _finish = accumulate_stream(stream)
        messages.append(build_message(text, tool_calls))
        calls = [tc for _, tc in sorted(tool_calls.items())]
        if not calls:
            print("  \033[35m[subagent] done\033[0m")
            return text  # ← 只把结论交回主 agent
        for tc in calls:
            args = json.loads(tc.function.arguments or "{}")
            blocked = trigger_hook("PreToolUse", tc.function.name, args)
            result = (
                f"[Tool '{tc.function.name}' blocked by hook: {blocked}]"
                if blocked is not None
                else execute_tool(tc, sub_registry)
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "[subagent] 达到最大轮次,子任务未完成"


def load_skill(name: str) -> str:
    """Load full skill content. Lookup via registry — no path traversal."""
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY) or "(none)"
        return f"Skill not found: {name!r}, available: {available}"
    return skill["content"]


def run_create_task(
    subject: str, description: str = "", blockedBy: list[str] | None = None
) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks. Use create_task to add some."
    lines = []
    for t in tasks:
        icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
        deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
        owner = f" [{t.owner}]" if t.owner else ""
        lines.append(f"  {icon} {t.id}: {t.subject} [{t.status}]{owner}{deps}")
    return "\n".join(lines)


def run_get_task(task_id: str) -> str:
    try:
        return get_task(task_id)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"


def run_claim_task(task_id: str) -> str:
    return claim_task(task_id, owner="agent")


def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)


def run_schedule_cron(
    cron: str, prompt: str, recurring: bool = True, durable: bool = True
) -> str:
    result = schedule_job(cron, prompt, recurring, durable)
    if isinstance(result, str):
        return f"Error: {result}"
    return f"Scheduled {result.id}: '{cron}' → {prompt}"


def run_list_crons() -> str:
    with cron_lock:
        jobs = list(scheduled_jobs.values())
    if not jobs:
        return "No cron jobs. Use schedule_cron to add one."
    lines = []
    for j in jobs:
        tag = "recurring" if j.recurring else "one-shot"
        dur = "durable" if j.durable else "session"
        lines.append(f"  {j.id}: '{j.cron}' → {j.prompt[:40]} [{tag}, {dur}]")
    return "\n".join(lines)


def run_cancel_cron(job_id: str) -> str:
    return cancel_job(job_id)


def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)


def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)


def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


class MCPClient:
    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, Callable] = {}

    def register(self, tool_defs: list[dict], handlers: dict[str, Callable]):
        self.tools = tool_defs
        self._handlers = handlers

    def call_tool(self, tool_name: str, args: dict) -> str:
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"Error: {e!s}"


_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_mcp_name(name: str) -> str:
    """Normalize MCP client name to a safe format."""
    return _DISALLOWED_CHARS.sub("_", name)


class SearchDocsArgs(BaseModel):
    query: str = Field(..., description="the search query for documentation")


def _mock_server_docs():
    client = MCPClient("docs")
    client.register(
        tool_defs=[
            {
                "name": "search",
                "description": "Search documentation. (readOnly)",
                "inputSchema": SearchDocsArgs.model_json_schema(),
            },
            {
                "name": "get_version",
                "description": "Get API version. (readOnly)",
                "inputSchema": NoneArgs.model_json_schema(),
            },
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        },
    )
    return client


class DeployArgs(BaseModel):
    service: str = Field(..., description="the name of the service to deploy")


def _mock_server_deploy():
    client = MCPClient("deploy")
    client.register(
        tool_defs=[
            {
                "name": "trigger",
                "description": "Trigger a deployment. (destructive — requires approval in real CC)",
                "inputSchema": DeployArgs.model_json_schema(),
            },
            {
                "name": "status",
                "description": "Check deployment status. (readOnly)",
                "inputSchema": DeployArgs.model_json_schema(),
            },
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        },
    )
    return client


class MCPStdioClient:
    """通过 stdio 连接一个真实 MCP server 的客户端。"""

    def __init__(self, name: str, command: list[str]):
        """
        name    —— 本地注册名（做工具前缀用，不要用 server 自报的 serverInfo.name）
        command —— 启动命令，例如 ["python", "toy_mcp_server.py"]
        """
        self.name = name
        self.tools: list[dict] = []  # tools/list 拿回来的工具定义存这里
        self._next_id = 0  # 自增请求号

        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,  # 我往它输入里写
            stdout=subprocess.PIPE,  # 我读它的输出
            stderr=None,  # 调试期：让 server 日志直接打到终端
            text=True,  # 收发 str 而不是 bytes
            bufsize=1,  # 行缓冲
        )

    # ────────────────────────────────────────────────────────────
    # 心脏：发一条请求，等一条回应
    # ────────────────────────────────────────────────────────────
    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        req_id = self._next_id

        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "my-harness",
                        "version": "0.1.0",
                    },
                },
            },
        }

        # 发
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

        # 收
        line = self.proc.stdout.readline()
        if not line:  # 空字符串 = 对面已经关了（进程死了）
            raise RuntimeError(f"MCP server '{self.name}' 断开了（stdout 读到 EOF）")
        resp = json.loads(line)

        # ── TODO ① 收到的 id 和发出去的对得上吗？──────────────────
        # 想清楚：如果对不上，说明发生了什么？该怎么处理？
        # （提示：这是你第三次见 id 配对——s07 tool_call_id、s16 request_id、现在）
        #
        # 你的代码写这里
        if resp.get("id") != req_id:
            raise RuntimeError(
                f"MCP server '{self.name}' 返回的 id={resp.get('id')} 和请求 id={req_id} 不匹配"
            )

        # ── TODO ② 错误分流 ──────────────────────────────────────
        # resp 里可能是 "result"，也可能是 "error"。
        # 想清楚：
        #   - JSON-RPC 层的 error（-32601 方法不存在 / -32602 参数非法）
        #     ——这是【你的代码写错了】，该让它安静地返回，还是大声地炸？
        #   - 注意：工具执行失败【不在这里】，它在 result 里带 isError=true
        #
        # 你的代码写这里
        if "error" in resp:
            error = resp["error"]
            raise RuntimeError(
                f"MCP server '{self.name}' 返回错误: code={error.get('code')}, message={error.get('message')}"
            )

        return resp["result"]

    # ────────────────────────────────────────────────────────────
    # 发现：问 server 有哪些工具
    # ────────────────────────────────────────────────────────────
    def register(self) -> list[dict]:
        # ── TODO ③ 发 tools/list，把结果存进 self.tools ────────────
        # 想清楚：
        #   - 存原样的 list[dict]，还是转成别的形状？
        #   - 21_mcp.py 里 assemble_tool_pool 会怎么用它？
        #     （它现在读的是 tool_def["name"] 和 tool_def["inputSchema"]）
        #   - ⚠️ 真 server 给的 inputSchema 是 dict，而 21_mcp.py 里
        #     assemble_tools() 写的是 args_model.model_json_schema()
        #     —— 那笔债在这里撞上，但【先别改 21_mcp.py】，本文件跑通再说
        #
        # 你的代码写这里
        result = self._rpc("tools/list")
        self.tools = result.get("tools", [])
        return self.tools

    # ────────────────────────────────────────────────────────────
    # 调用：让 server 执行一个工具
    # ────────────────────────────────────────────────────────────
    def call_tool(self, tool_name: str, args: dict) -> str:
        # ── TODO ④ 发 tools/call，把返回的 content 转成【字符串】────
        # 想清楚：
        #   - server 回的 content 是【数组】，每项形如 {"type":"text","text":"..."}
        #     可能有多项，也可能有非 text 类型（image/audio/resource_link）
        #   - 你的 handler 契约是「返回给模型看的字符串」——薄壳层翻译官（s12）
        #   - result 里还有 isError 字段。为 true 时怎么办？
        #     （提示：spec 说 client SHOULD 把它给模型，让模型自我纠正）
        #
        # 你的代码写这里
        result = self._rpc("tools/call", {"name": tool_name, "arguments": args})
        results = []
        content = result.get("content", [])
        is_error = result.get("isError", False)
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                results.append(text)
            else:
                results.append(f"[非文本内容，type={item.get('type')}]")
        output = "\n".join(results)
        if is_error:
            output = f"[工具执行失败]\n{output}"
        return output

    # ────────────────────────────────────────────────────────────
    # 关闭：spec 的三步 —— 关 stdin → 等退出 → 超时强杀
    # ────────────────────────────────────────────────────────────
    def close(self):
        if self.proc.poll() is not None:  # 已经死了
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


REAL_SERVERS = {"weather": [sys.executable, "toy_mcp_server.py"]}

MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}

mcp_clients: dict[str, MCPClient | MCPStdioClient] = {}


def connect_mcp_name(name: str) -> str:
    if name in mcp_clients:
        return f"MCP server '{name}' already exists"
    factory = MOCK_SERVERS.get(name)
    if not factory:
        command = REAL_SERVERS.get(name)
        if not command:
            available = [*MOCK_SERVERS.keys(), *REAL_SERVERS.keys()]
            return f"Error: unknown MCP server '{name}'. Available: {available}"
        else:
            mcp_client = MCPStdioClient(name, command)
            mcp_client.register()
    else:
        mcp_client = factory()
    mcp_clients[name] = mcp_client
    tool_names = [t["name"] for t in mcp_client.tools]
    print(
        f"  \033[36m[mcp] connected to '{name}' with tools: {', '.join(tool_names)}\033[0m"
    )
    return f"Connected to MCP server '{name}' with tools: {', '.join(tool_names)}"


def _make_mcp_handler(client, tool_name):
    def handler(**kwargs):
        return client.call_tool(tool_name, kwargs)

    return handler


def assemble_tool_pool() -> dict:
    tools = dict(TOOL_REGISTRY)
    if MEMORY_MODE != "official":
        tools.pop("memory", None)
    if TODO_MODE == "none":
        tools.pop("todo_write", None)
    for server_name, mcp_client in mcp_clients.items():
        safe_server = normalize_mcp_name(server_name)
        for tool_def in mcp_client.tools:
            safe_tool = normalize_mcp_name(tool_def["name"])
            prefixed = f"mcp__{safe_server}__{safe_tool}"
            handler = _make_mcp_handler(mcp_client, tool_def["name"])
            tools[prefixed] = ToolEntry(
                tool_def["description"],
                tool_def["inputSchema"],
                None,
                handler,
            )

    return tools


class ConnectMCPArgs(BaseModel):
    name: str = Field(..., description="the name of the MCP server to connect to")


def run_connect_mcp(name: str) -> str:
    return connect_mcp_name(name)


class ToolEntry(NamedTuple):
    description: str
    schema: dict
    validator: type[BaseModel] | None
    handler: Callable


def builtin(desc: str, args_model: type[BaseModel], handler: Callable) -> ToolEntry:
    return ToolEntry(desc, args_model.model_json_schema(), args_model, handler)


TOOL_REGISTRY = {
    "bash": builtin("Run a shell command.", BashArgs, run_bash),
    "read_file": builtin("Read file contents.", ReadFileArgs, run_read),
    "write_file": builtin("Write content to a file.", WriteFileArgs, run_write),
    "edit_file": builtin("Replace exact text in a file once.", EditFileArgs, run_edit),
    "glob": builtin("Find files matching a glob pattern.", GlobArgs, run_glob),
    "todo_write": builtin(
        "Create and manage a task list for your current coding session. "
        "IMPORTANT: this tool REPLACES the entire task list on every call. "
        "Always pass the COMPLETE list of ALL tasks (including unchanged ones) "
        "with their current status — never send only the tasks you just updated.",
        TodoWriteArgs,
        run_todo_write,
    ),
    "create_task": builtin(
        "Create a new task with subject, description, and optional blockedBy list.",
        CreateTaskArgs,
        run_create_task,
    ),
    "list_tasks": builtin(
        "List all tasks with their status and dependencies.",
        NoneArgs,
        run_list_tasks,
    ),
    "get_task": builtin(
        "Get full details of a task by its ID.",
        TaskIdArgs,
        run_get_task,
    ),
    "claim_task": builtin(
        "Claim a task by its ID, marking it as in_progress if possible.",
        TaskIdArgs,
        run_claim_task,
    ),
    "complete_task": builtin(
        "Mark a task as completed by its ID, unblocking dependent tasks.",
        TaskIdArgs,
        run_complete_task,
    ),
    "schedule_cron": builtin(
        "Schedule a cron job with a cron expression and prompt.",
        ScheduleCronArgs,
        run_schedule_cron,
    ),
    "list_crons": builtin(
        "List all scheduled cron jobs with their details.",
        NoneArgs,
        run_list_crons,
    ),
    "cancel_cron": builtin(
        "Cancel a scheduled cron job by its ID.",
        CancelCronArgs,
        run_cancel_cron,
    ),
    "spawn_teammate": builtin(
        "Spawn a teammate agent with a name, role, and initial prompt.",
        SpawnTeammateArgs,
        run_spawn_teammate,
    ),
    "send_message": builtin(
        "Send a message to another teammate agent.",
        SendMessageArgs,
        run_send_message,
    ),
    "check_inbox": builtin(
        "Check the inbox for messages sent to this agent.",
        NoneArgs,
        run_check_inbox,
    ),
    "request_shutdown": builtin(
        "Request a teammate agent to shut down gracefully.",
        RequestShutdownArgs,
        run_request_shutdown,
    ),
    "request_plan": builtin(
        "Request a teammate agent to submit a plan for a specific task.",
        RequestPlanArgs,
        run_request_plan,
    ),
    "review_plan": builtin(
        "Review a plan submitted by a teammate agent, approving or rejecting it.",
        ReviewPlanArgs,
        run_review_plan,
    ),
    "create_worktree": builtin(
        "Create an isolated git worktree with its own branch.",
        CreateTaskArgs,
        run_create_worktree,
    ),
    "remove_worktree": builtin(
        "Remove a worktree. Refuses if uncommitted changes unless discard_changes=true.",
        RemoveWorktreeArgs,
        run_remove_worktree,
    ),
    "keep_worktree": builtin(
        "Keep a worktree for manual review.",
        KeepWorktreeArgs,
        run_keep_worktree,
    ),
    "connect_mcp": builtin(
        "Connect to a mock MCP server by name.",
        ConnectMCPArgs,
        run_connect_mcp,
    ),
    "memory": builtin(
        "All paths MUST start with /memories. To read your memory root, call {'command': 'view', 'path': '/memories'}. Never use read_file for memory paths. "
        "Your persistent memory directory at /memories. It survives across sessions. "
        "ALWAYS view /memories before starting a task to check for earlier notes. "
        "Record important user preferences, facts and progress as you learn them. "
        "Commands: view (list directory or read file), create (write/overwrite a file), "
        "str_replace, insert, delete, rename.",
        MemoryArgs,
        run_memory,
    ),
    "load_skill": builtin(
        "Load the full content of a skill by name.",
        SkillsArgs,
        load_skill,
    ),
    "compact": builtin(
        "Summarize earlier conversation to free context space.",
        CompactArgs,
        run_compact,
    ),
    "spawn_subagent": builtin(
        "Delegate a self-contained subtask to a one-shot subagent. "
        "It starts with a fresh context, does the work, and returns only a summary — "
        "use it when the subtask would flood your own context with details you don't need.",
        SubagentArgs,
        spawn_subagent,
    ),
}

# TOOLS = [
#     {
#         "type": "function",
#         "function": {
#             "name": name,
#             "description": desc,
#             "parameters": args_model.model_json_schema(),
#         },
#     }
#     for name, (desc, args_model, _) in TOOL_REGISTRY.items()
# ]

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    """Select and join prompt sections based on current context."""
    sections = []

    # Always loaded — identity, tools, workspace
    sections.append(PROMPT_SECTIONS["identity"])
    # tools 这一节【不能】住在 PROMPT_SECTIONS 里:那是模块级字典,f-string 在 import
    # 那一刻就求值成死字符串,之后工具池怎么变都不跟着变(MCP 是运行时 connect 进来的)。
    # 而且必须报【当轮工具池】而不是 TOOL_REGISTRY 全集 —— 否则 TODO_MODE=none 那一臂
    # 的 prompt 里还写着 todo_write,消融臂的痕迹擦不干净。
    # 顺序保持 identity → tools → workspace:section 顺序是 prompt 缓存的一部分,
    # 前缀一变缓存全失效(get_system_prompt 的 docstring 提到的 stable section ordering)。
    sections.append(f"Available tools: {', '.join(assemble_tool_pool().keys())}.")
    sections.append(PROMPT_SECTIONS["workspace"])

    # Conditional — memory loaded when MEMORY.md exists and has content
    memories = context.get("memories", "")
    if memories:
        sections.append(f"Relevant memories:\n{memories}")
    if context.get("skills"):
        sections.append(
            f"Available skills: {context['skills']}. Use load_skill to read their content."
        )

    return "\n\n".join(sections)


def get_system_prompt(context: dict) -> str:
    """Assemble the system prompt and report which sections went in.

    【2026-08-14 删掉了这里的进程内缓存】原本拿 json.dumps(context) 当 key,
    缓存上一次组装的结果。删掉的理由是一道算术:

        被缓存的开销 = assemble_tool_pool() + 几次 append + join
        算 key 的开销 = assemble_tool_pool() + json.dumps
                        ^^^^^^^^^^^^^^^^^^^ 一模一样,一份都省不掉
                        (工具池也是 prompt 的输入,正确的 key 必须覆盖它)

    为了判断「能不能用缓存」,得先把被缓存的事做掉大半;省下的只是 join 与
    json.dumps 的差额,大概率还是负的。
    🪝 当「验证缓存是否有效」的成本 ≈「重新算一遍」的成本时,这个缓存就不该存在。

    ⚠️ 它原本还是错的:key 只由 context 决定,而 prompt 的真实输入是 context
       【和当轮工具池】两个 —— 连上 MCP 后工具池变了、context 没变,会返回过期
       的 prompt(tests/test_prompt_assembly.py 里那条 staleness 测试钉的就是这个)。
       🪝 缓存 key 必须覆盖它实际依赖的全部输入。

    📌 别把这个进程内小缓存跟【API 级 prompt 缓存】混为一谈 —— 名字一样,完全两回事:
       真实 Claude Code 靠 stable section ordering + SYSTEM_PROMPT_DYNAMIC_BOUNDARY
       保证【前缀稳定】,在服务端拿命中率,那才是 prompt 缓存该发生的层次。
       (所以 assemble_system_prompt 里的 section 顺序是契约,不能随手调。)
    """
    prompt = assemble_system_prompt(context)

    loaded = ["identity", "tools", "workspace"]
    if context.get("memories"):
        loaded.append("memory")
    if context.get("skills"):
        loaded.append("skills")
    print(f"  \033[32m[assembled] sections: {', '.join(loaded)}\033[0m")
    return prompt


def update_context(context: dict, messages: list) -> dict:
    """Derive context from real state: which tools exist, whether memory files exist.

    分叉点①(记忆层三模式):
      none      不注入 —— 模型完全不知道有记忆这回事
      self      系统注入 —— 目前是【全量灌 MEMORY.md】,待升级成相关性筛选
                (10_memory_loop.py 的 select_relevant_memories / load_memories)
      official  不注入 —— 模型自己调 memory 工具 view,系统不越俎代庖
    """
    global _memories_cache
    memories = ""
    if MEMORY_MODE == "self":
        if _memories_cache is None:
            # 本轮第一次:调 LLM 挑相关记忆,读出【正文】(不是索引摘要)
            _memories_cache = load_memories(messages)
        memories = _memories_cache
    return {
        "enabled_tools": list(TOOL_REGISTRY.keys()),  # list(TOOL_HANDLER.keys())
        "workspace": str(WORKDIR),
        "memories": memories,
        "skills": list_skills(),
    }


_bg_counter = 0
background_tasks: dict[str, dict] = {}  # bg_id → {tool_use_id, command, status}
background_results: dict[str, str] = {}  # bg_id → output
background_lock = threading.Lock()


def should_run_background(tc_name: str, tc_args: dict) -> bool:
    """只认模型的显式意图 —— 后台与否由它决定,系统不猜。

    【2026-08-14 删掉启发式 is_slow_operation】
    原实现按关键词猜「这条命令是不是慢操作」:
        slow_keywords = ["install","build","test","deploy","compile","pytest","make",...]
    两个问题:
      ① 过宽:`cat test.py` 命中 "test",`cat build.md` 命中 "build"(2026-08-11 已记录)
      ② 🔴 致命:mini-bench 的题目全是「跑测试 → 看结果 → 改代码」,
         而 pytest 被自动丢后台 → 模型拿到的是占位符不是结果 → 再跑一次 → 又进后台
         → 实测空转 15 步。【散件时代测不出来:03 根本没有后台任务机制】

    为什么删而不是调关键词:关键词猜测是【设计缺陷】,不是调参问题。
    docstring 原本就写着 "Model explicit request takes priority" ——
    模型的显式意图本就该是唯一依据,启发式是多余的兜底。
    🪝 同族教训:stage-1 归因证明过「系统自动替模型做的决定常常是负担」(todo_write)。

    代价:模型忘了标记 run_in_background 时,慢命令会占住主循环
        (run_bash 有 timeout=120 兜底,不会永久卡死)。

    📌 TODO(有余力时做,当事人 2026-08-14 点名保留):补一个 wait_for_background 工具,
       让模型能主动等某个 bg_id 出结果 —— 那才是后台机制的完整形态。
       现在的形态是「派遣出去就不管了,靠下一轮的 [inject] 撞见」。
    """
    return bool(tc_args.get("run_in_background"))


def execute_tool(tc, TR) -> str:
    """Execute a tool call block, return output."""
    entry = TR.get(tc.function.name)
    if not entry:
        return f"Error: unknown tool '{tc.function.name}'"
    else:
        if entry.validator is not None:
            try:
                args = entry.validator.model_validate_json(
                    tc.function.arguments or "{}"
                ).model_dump()
            except Exception as e:
                return f"Error: invalid arguments for tool '{tc.function.name}' - {e!s}"
        else:
            args = tc.function.arguments or "{}"
            args = json.loads(args)
        handler = entry.handler
        print(f"[tool call] {tc.function.name} with args: {args}")
        return handler(**args)


def start_background_task(tc, TR, args) -> str:
    """Run tool in a daemon thread. Returns background task ID."""
    global _bg_counter
    _bg_counter += 1
    bg_id = f"bg_{_bg_counter:04d}"
    cmd = args.get("command", tc.function.name)

    def worker():
        result = execute_tool(tc, TR)
        with background_lock:
            background_tasks[bg_id]["status"] = "completed"
            background_results[bg_id] = result

    with background_lock:
        background_tasks[bg_id] = {
            "tool_use_id": tc.id,
            "command": cmd,
            "status": "running",
        }
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(f"  \033[33m[background] dispatched {bg_id}: {cmd[:40]}\033[0m")
    return bg_id


def collect_background_results() -> list[str]:
    """Collect completed background results as task_notification messages."""
    with background_lock:
        ready_ids = [
            bid
            for bid, task in background_tasks.items()
            if task["status"] == "completed"
        ]
    notifications = []
    for bg_id in ready_ids:
        with background_lock:
            task = background_tasks.pop(bg_id)
            output = background_results.pop(bg_id, "")
        summary = output[:200] if len(output) > 200 else output
        notifications.append(
            f"<task_notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>completed</status>\n"
            f"  <command>{task['command']}</command>\n"
            f"  <summary>{summary}</summary>\n"
            f"</task_notification>"
        )
        print(
            f"  \033[32m[background done] {bg_id}: "
            f"{task['command'][:40]} ({len(output)} chars)\033[0m"
        )
    return notifications


session_context: dict | None = None
session_history: list | None = None


def init_session():
    """建立会话初态。只在启动时调用——import 不该建立会话。"""
    global session_context, session_history
    _scan_skills()
    session_context = update_context({}, [])
    session_history = [
        {"role": "system", "content": get_system_prompt(session_context)}
    ]


def assemble_tools():
    TOOLS_Registry = assemble_tool_pool()
    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": entry.description,
                "parameters": entry.schema,
            },
        }
        for name, entry in TOOLS_Registry.items()
    ]
    return TOOLS_Registry, TOOLS


def accumulate_stream(chunks):
    final_tool_calls = {}
    final_text = ""
    finish_reason = None
    for chunk in chunks:
        delta = chunk.choices[0].delta
        if delta.content:
            final_text += delta.content
            print(delta.content, end="", flush=True)
        for tool_call in delta.tool_calls or []:
            index = tool_call.index
            if index not in final_tool_calls:
                final_tool_calls[index] = tool_call
            else:
                final_tool_calls[
                    index
                ].function.arguments += tool_call.function.arguments
        if chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

    return final_text, final_tool_calls, finish_reason


def build_message(text, tool_calls):
    """把累积结果拼成一条 assistant 消息。

    形状必须与非流式的 msg.model_dump(exclude_none=True) 一致,
    否则塞回 messages 后下一轮请求会 400。
    """
    msg = {"role": "assistant", "content": text}

    # 如果 tool_calls 非空:
    #   ① 把字典的值【按 key 排序】取出来
    #   ② 每个都 .model_dump(exclude={"index"})
    #   ③ 结果塞进 msg["tool_calls"]
    if tool_calls:
        msg["tool_calls"] = [
            call.model_dump(exclude={"index"}) for _, call in sorted(tool_calls.items())
        ]

    return msg


rounds_since_todo = 0
MAX_REACTIVE_RETRIES = 1  # retry limit for reactive compact
MAX_COMPACT_RETRIES = 3  # retry limit for compact_history
compact_failures = 0


def try_compact(msgs, force=False):
    global compact_failures
    if estimate_size(msgs) > CONTEXT_LIMIT or force:
        if compact_failures < MAX_COMPACT_RETRIES:
            print("[auto-compact]")
            assert msgs[0]["role"] == "system", "First message must be system prompt"
            try:
                msgs[1:] = compact_history(msgs)
            except Exception as e:
                print(f"[auto-compact failed: {e}]")
                compact_failures += 1
            else:
                compact_failures = 0
    return msgs


def estimate_size(msgs):
    return len(str(msgs))


def agent_loop(messages: list, context: dict):
    max_turns = 25
    reactive_retries = 0
    global compact_failures
    global rounds_since_todo
    # 进门先按【当前 messages】重算一次 context:传进来的那份是上一轮末尾算的,
    # 还不含本轮的 user 消息 —— self 模式下会因此选不出相关记忆(第一轮尤其明显)。
    # 有 _memories_cache 兜着,这一次不会额外多调 LLM。
    context = update_context(context, messages)
    # system = get_system_prompt(context)
    system = assemble_system_prompt(context)
    messages[0] = {**messages[0], "content": system}
    # fired = consume_cron_queue()
    # for job in fired:
    #     messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
    #     print(f"  \033[35m[inject cron] {job.prompt[:50]}\033[0m")
    TOOLS_Registry, TOOLS = assemble_tools()
    state = RecoveryState()
    for turn in range(max_turns):
        pre_compress = [dict(m) for m in messages]
        messages[:] = tool_result_budget(messages)  # L3: persist large results first
        messages[:] = snip_compact(messages)  # L1: trim middle
        messages[:] = micro_compact(messages)  # L2: old result placeholders
        try_compact(messages)  # L4: summarize if too large
        if TODO_MODE == "nudge" and rounds_since_todo >= 3 and messages:
            messages.append(
                {"role": "user", "content": "<reminder>Update your todos.</reminder>"}
            )
            rounds_since_todo = 0
        try:
            attempt = 0

            def do_request():
                nonlocal attempt
                if attempt > 0:
                    print("\n\033[33m[连接中断,重新生成 —— 上面这段作废]\033[0m\n")
                attempt += 1
                stream = client.chat.completions.create(
                    # 用 state 里的,不是全局 model —— 529 连续三次时 with_retry
                    # 会把 state.current_model 换成 FALLBACK_MODEL,这里必须跟着走,
                    # 否则「切换模型」只改了个字段、请求照旧,日志会说谎。
                    model=state.current_model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    stream=True,
                    max_tokens=DEFAULT_MAX_TOKENS,
                )
                return accumulate_stream(stream)

            text, tool_calls, finish_reason = with_retry(do_request, state)
            reactive_retries = 0
        except Exception as e:
            if (
                "prompt_too_long" in str(e).lower()
                or "too many tokens" in str(e).lower()
            ) and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[reactive compact]")
                assert messages[0]["role"] == "system", (
                    "First message must be system prompt"
                )
                messages[1:] = reactive_compact(messages)
                reactive_retries += 1
                continue
            messages.append(
                {"role": "assistant", "content": f"[Error] {type(e).__name__}: {e}"}
            )
            return context
        # msg = reps.choices[0].message
        # messages.append(msg.model_dump(exclude_none=True))
        # text, tool_calls, finish_reason = accumulate_stream(reps)
        if finish_reason == "length":
            messages.append(build_message(text, {}))
            if state.recovery_count < MAX_RECOVERY_RETRIES:
                messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                state.recovery_count += 1
                print(
                    f"  \033[33m[max_tokens] continuation"
                    f" {state.recovery_count}/{MAX_RECOVERY_RETRIES}\033[0m"
                )
                continue
            print("  \033[31m[max_tokens] recovery limit reached\033[0m")
            return context
        msg = build_message(text, tool_calls)
        messages.append(msg)
        calls = [tc for _, tc in sorted(tool_calls.items())]
        if not calls:
            trigger_hook("Stop", messages)
            if MEMORY_MODE == "self":
                extract_memories(pre_compress)
                consolidate_memories()
            return context
        rounds_since_todo += 1
        for tc in calls:
            if tc.function.name == "compact":
                result = "[auto-compact]"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
                try_compact(messages, force=True)
                break
            args = json.loads(tc.function.arguments or "{}")
            blocked = trigger_hook("PreToolUse", tc.function.name, args)
            if blocked is not None:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[Tool '{tc.function.name}' blocked by hook: {blocked}]",
                    }
                )
                print(f"[tool blocked] {tc.function.name}: {blocked}")
                continue
            if should_run_background(tc.function.name, args):
                bg_id = start_background_task(tc, TOOLS_Registry, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[Background task {bg_id} started] "
                        f"Command: {args.get('command', tc.function.name)}. "
                        f"Result will be available when complete.",
                    }
                )
                print(f"[background] {bg_id} started for tool '{tc.function.name}'")
                continue

            result = execute_tool(tc, TOOLS_Registry)
            trigger_hook("PostToolUse", tc.function.name, result)
            if tc.function.name == "todo_write":
                rounds_since_todo = 0
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result[:200]}")

        bg_notifications = collect_background_results()
        if bg_notifications:
            for notif in bg_notifications:
                messages.append({"role": "user", "content": notif})
            print(
                f"  \033[32m[inject] {len(bg_notifications)} background "
                f"notification(s)\033[0m"
            )
        if any(tc.function.name == "connect_mcp" for tc in calls):
            TOOLS_Registry, TOOLS = assemble_tools()
        context = update_context(context, messages)
        system = assemble_system_prompt(context)
        messages[0] = {**messages[0], "content": system}
    print("达到最大轮次")
    trigger_hook("Stop", messages)
    # 【有意】不在这个出口提取记忆(对照上面 if not calls 那个出口),三条理由:
    #   ① 跑满 max_turns 通常意味着任务卡住了(模型打转/工具一直报错),
    #      后半段是挣扎,不是用户在表达偏好 —— 提取出来的多半是噪音
    #   ② 代价不对称:一次失败的工具调用这轮就结束了,但一条【错记忆】会留在
    #      .memory/ 里影响后续所有对话,而且看起来像条正常记忆,很难发现
    #   ③ 这里的 pre_compress 是【最后一轮】的快照,早期对话早被 snip_compact
    #      裁掉了 —— 连完整对话都拿不到,提取的基础本身就是残缺的
    return context


def print_latest_assistant_text(messages: list):
    if not messages:
        return
    msg = messages[-1]
    if msg.get("role") != "assistant":
        return
    content = msg.get("content", "")
    if content:
        print(f"[assistant] {content}")


def run_agent_turn_locked(user_query: str | None = None, cron: bool = False):
    global session_context
    global _memories_cache
    _memories_cache = None  # 新一轮用户输入 → 重新挑一次相关记忆
    if user_query is not None:
        trigger_hook("UserPromptSubmit", user_query)
        session_history.append({"role": "user", "content": user_query})
    if cron:
        fired = consume_cron_queue()
        for job in fired:
            trigger_hook("UserPromptSubmit", job.prompt)
            session_history.append(
                {"role": "user", "content": f"[Scheduled] {job.prompt}"}
            )
            print(f"  \033[35m[inject cron] {job.prompt[:50]}\033[0m")
    context = agent_loop(session_history, session_context)
    if context:
        session_context = context
    session_context = update_context(session_context, session_history)
    # Check inbox for teammate results → inject into history
    inbox_msgs = consume_lead_inbox(route_protocol=True)
    if inbox_msgs:
        inbox_text = "\n".join(
            f"From {m['from']}: {m['content'][:200]}" for m in inbox_msgs
        )
        session_history.append({"role": "user", "content": f"[Inbox]\n{inbox_text}"})
        print(f"\n\033[33m[Inbox: {len(inbox_msgs)} messages injected]\033[0m")
    # print_latest_assistant_text(session_history)
    print()


def queue_processor_loop():
    global session_context
    while True:
        time.sleep(0.2)
        if not has_cron_queue():
            continue
        if not agent_lock.acquire(blocking=False):  # 如果无法获取锁，则跳过 不等
            continue
        try:
            if not has_cron_queue():
                continue
            print("\n  \033[35m[queue processor] delivering scheduled work\033[0m")
            run_agent_turn_locked(cron=True)
        finally:
            agent_lock.release()


def main():
    """启动 agent。原先散在模块顶层的四类副作用全部收在这里。"""
    global MEMORY_MODE
    parser = argparse.ArgumentParser(description="Run the coding agent.")
    parser.add_argument(
        "task", nargs="?", help="Optional initial task to give the agent."
    )
    parser.add_argument(
        "--memory",
        choices=MEMORY_MODES,
        default=None,
        help=f"记忆层模式(默认取环境变量 MEMORY_MODE,当前 {MEMORY_MODE})",
    )
    args = parser.parse_args()
    if args.memory:  # CLI 参数优先级最高
        MEMORY_MODE = args.memory
    print(f"  \033[36m[memory] mode = {MEMORY_MODE}\033[0m")
    ensure_dirs()
    init_session()
    if args.task:
        with agent_lock:
            run_agent_turn_locked(args.task)
        return
    start_cron_scheduler()
    print("输入一个问题，回车发送。输入q退出。\n")
    threading.Thread(target=queue_processor_loop, daemon=True).start()
    print("  \033[35m[queue processor] started\033[0m")
    while True:
        try:
            user_input = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        with agent_lock:
            run_agent_turn_locked(user_input)


if __name__ == "__main__":
    main()
