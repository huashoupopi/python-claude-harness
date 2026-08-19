import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

# WORKDIR = Path(__file__).parent.resolve()
WORKDIR = Path.cwd()  # 改为当前工作目录，更通用
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_DIR.mkdir(exist_ok=True)
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"

load_dotenv(override=True)

MEMORY_TYPES = ["user", "feedback", "project", "reference"]


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
        pass


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


_scan_skills()


def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- **{s['name']}**: {s['description']}" for s in SKILL_REGISTRY.values()
    )


def build_system() -> str:
    """Build SYSTEM prompt with skill catalog injected at startup."""
    index = read_memory_index()
    memories_section = f"\n\nMemories available:\n{index}" if index else ""
    catalog = list_skills()
    return (
        f"You are a coding agent at {WORKDIR}. "
        f"{memories_section}\n"
        "Relevant memories are injected below. Respect user preferences from memory.\n"
        "When the user says 'remember' or expresses a clear preference, extract it as a memory.\n"
        f"Skills available:\n{catalog}\n"
        "Use load_skill to get full details when needed."
    )


SYSTEM = build_system()

SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)

client = OpenAI(
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY"),
    timeout=60.0,
    default_headers={"User-Agent": "curl/8.7.1"},
)

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


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


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


class TodoItem(BaseModel):
    content: str = Field(..., description="the content of the todo item")
    status: Literal["pending", "in_progress", "completed"] = Field(
        ..., description="the status of the todo item"
    )


class TodoWriteArgs(BaseModel):
    todos: list[TodoItem] = Field(
        ..., description="list of todo items with content and status"
    )


class TaskArgs(BaseModel):
    description: str = Field(..., description="description of the task")


class SkillsArgs(BaseModel):
    name: str = Field(..., description="name of the skill to load")


class CompactArgs(BaseModel):
    focus: str = Field(
        ..., description="Summarize earlier conversation to free context space."
    )


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    # dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    # if any(d in command for d in dangerous):
    #     return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
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
        return f"Error: {str(e)}"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        file_path = safe_path(path)
        lines = file_path.read_text().splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def run_glob(pattern: str) -> str:
    import glob as g

    try:
        results = []
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {str(e)}"


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


def spawn_subagent(description: str) -> str:
    SUB_TOOL_REGISTRY = {
        k: v
        for k, v in TOOL_REGISTRY.items()
        if k != "todo_write" and k != "spawn_subagent"
    }
    SUB_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": args_model.model_json_schema(),
            },
        }
        for name, (desc, args_model, func) in SUB_TOOL_REGISTRY.items()
    ]
    messages = [{"role": "system", "content": SUB_SYSTEM}]
    messages.append({"role": "user", "content": description})
    for turns in range(30):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=SUB_TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"[subagent] {msg.content}")
            return msg.content

        for tc in msg.tool_calls:
            entry = SUB_TOOL_REGISTRY.get(tc.function.name)
            if not entry:
                result = f"Error: unknown tool '{tc.function.name}'"
            else:
                desc, ArgsModel, handler = entry
                try:
                    args = ArgsModel.model_validate_json(tc.function.arguments)
                except Exception as e:
                    result = f"Error: invalid arguments for tool '{tc.function.name}' - {str(e)}"
                else:
                    print(f"[subagent tool call] {tc.function.name} with args: {args}")
                    arg_dict = args.model_dump()
                    blocked = trigger_hook("PreToolUse", tc.function.name, arg_dict)
                    if blocked is not None:
                        result = blocked
                    else:
                        result = handler(**arg_dict)
                        trigger_hook("PostToolUse", tc.function.name, result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(
                f"[subagent tool result] {str(result[:200] + '...') if len(result) > 200 else result}"
            )  # 只打印前200字符
    return "[subagent] reached max turns without conclusion"


def load_skill(name: str) -> str:
    """Load full skill content. Lookup via registry — no path traversal."""
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Skill not found: {name}"
    return skill["content"]


model = os.getenv("MODEL")
# 单位是字符,非 token;主干 22_trunk.py 已改为 token 语义,此处保留课程原码形态用于溯源对照
CONTEXT_LIMIT = 500000
KEEP_RECENT = 3
PERSIST_THRESHOLD = 30000


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


def write_transcript(msgs, filename=None):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    if filename is not None:
        name = filename
    else:
        name = f"transcript_{int(time.time())}"
    path = TRANSCRIPT_DIR / f"{name}.jsonl"
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


TOOL_REGISTRY = {
    "bash": ("Run a shell command.", BashArgs, run_bash),
    "read_file": ("Read file contents.", ReadFileArgs, run_read),
    "write_file": ("Write content to a file.", WriteFileArgs, run_write),
    "edit_file": ("Replace exact text in a file once.", EditFileArgs, run_edit),
    "glob": ("Find files matching a glob pattern.", GlobArgs, run_glob),
    "todo_write": (
        "Create and manage a task list for your current coding session. "
        "IMPORTANT: this tool REPLACES the entire task list on every call. "
        "Always pass the COMPLETE list of ALL tasks (including unchanged ones) "
        "with their current status — never send only the tasks you just updated.",
        TodoWriteArgs,
        run_todo_write,
    ),
    "spawn_subagent": (
        "Launch a subagent to handle a complex subtask. Returns only the final conclusion.",
        TaskArgs,
        spawn_subagent,
    ),
    "load_skill": (
        "Load the full content of a skill by name.",
        SkillsArgs,
        load_skill,
    ),
    "compact": (
        "Summarize earlier conversation to free context space.",
        CompactArgs,
        compact_history,
    ),
}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": args_model.model_json_schema(),
        },
    }
    for name, (desc, args_model, func) in TOOL_REGISTRY.items()
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


def permission_hook(name, args):
    if name == "bash":
        for pattern in DENY_LIST:
            if pattern in args.get("command", ""):
                print(f"\n\033[31m⛔ Blocked: '{pattern}'\033[0m")
                return "Permission denied by deny list"
        for kw in DESTRUCTIVE:
            if kw in args.get("command", ""):
                print("\n\033[33m⚠  Potentially destructive command\033[0m")
                print(f"   Tool: {name}({args})")
                choice = input("   Allow? [y/N] ").strip().lower()
                if choice not in ("y", "yes"):
                    return "Permission denied by user"
    if name in ("write_file", "edit_file"):
        path = args.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            print("\n\033[33m⚠  Writing outside workspace\033[0m")
            print(f"   Tool: {name}({args})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    return None


def log_hook(name, args):
    """PreToolUse: log every tool call."""
    args_preview = str(list(args.values())[:2])[:60]
    print(f"\033[90m[HOOK] {name}({args_preview})\033[0m")
    return None


def large_output_hook(name, output):
    """PostToolUse: warn on large output."""
    if len(str(output)) > 100000:
        print(
            f"\033[33m[HOOK] ⚠ Large output from {name}: {len(str(output))} chars\033[0m"
        )
    return None


# UserPromptSubmit hook: log user input before it reaches the LLM
def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None
    # return f"工作目录: {WORKDIR}\n用户输入: {query}"


# Stop hook: print summary when loop is about to exit
def summary_hook(messages: list):
    tool_count = sum(1 for m in messages if m.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None


register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", log_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


rounds_since_todo = 0
SESSION_ID = int(time.time())
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


def agent_loop(messages):
    reactive_retries = 0
    global compact_failures
    global rounds_since_todo
    max_turns = 25
    memories_content = load_memories(messages)
    memory_turn = (
        len(messages) - 1
        if messages and isinstance(messages[-1].get("content"), str)
        else None
    )
    for trun in range(max_turns):
        pre_compress = [m for m in messages]
        messages[:] = tool_result_budget(messages)  # L3: persist large results first
        messages[:] = snip_compact(messages)  # L1: trim middle
        messages[:] = micro_compact(messages)  # L2: old result placeholders
        try_compact(messages)  # L4: summarize if too large
        if rounds_since_todo >= 3 and messages:
            messages.append(
                {"role": "user", "content": "<reminder>Update your todos.</reminder>"}
            )
            rounds_since_todo = 0
        try:
            request_messages = messages
            if (
                memories_content
                and memory_turn is not None
                and memory_turn < len(messages)
                and messages[memory_turn]["role"] == "user"
            ):
                request_messages = messages.copy()
                request_messages[memory_turn] = {
                    **messages[memory_turn],
                    "content": memories_content
                    + "\n\n"
                    + messages[memory_turn]["content"],
                }
            resp = client.chat.completions.create(
                model=model,
                messages=request_messages,
                tools=TOOLS,
                tool_choice="auto",
            )
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
            raise
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            force = trigger_hook("Stop", messages)
            if force is not None:
                messages.append({"role": "user", "content": force})
                continue
            extract_memories(pre_compress)
            write_transcript(messages, filename=f"trajectory_{SESSION_ID}")
            consolidate_memories()
            print(f"[assistant] {msg.content}")
            return msg.content

        rounds_since_todo += 1
        for tc in msg.tool_calls:
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
            entry = TOOL_REGISTRY.get(tc.function.name)
            if not entry:
                result = f"Error: unknown tool '{tc.function.name}'"
            else:
                desc, ArgsModel, handler = entry
                try:
                    args = ArgsModel.model_validate_json(tc.function.arguments)
                except Exception as e:
                    result = f"Error: invalid arguments for tool '{tc.function.name}' - {str(e)}"
                else:
                    print(f"[tool call] {tc.function.name} with args: {args}")
                    arg_dict = args.model_dump()
                    blocked = trigger_hook("PreToolUse", tc.function.name, arg_dict)
                    if blocked is not None:
                        result = blocked
                    else:
                        result = handler(**arg_dict)
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
            print(
                f"[tool result] {str(result[:200] + '...') if len(result) > 200 else result}"
            )  # 只打印前200字符
    print("[已达最大轮次,中止]")
    write_transcript(messages, filename=f"trajectory_{SESSION_ID}")


if __name__ == "__main__":
    print("输入问题，回车发送。输入 q 退出。\n")

    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        injected = trigger_hook("UserPromptSubmit", user_input)
        messages.append(
            {
                "role": "user",
                "content": injected if injected is not None else user_input,
            }
        )
        messages[0] = {"role": "system", "content": build_system()}
        agent_loop(messages)
