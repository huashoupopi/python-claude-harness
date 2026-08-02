import json
import os
import random
import subprocess
import time
from pathlib import Path

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
MEMORY_DIR.mkdir(exist_ok=True)
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
model = os.getenv("NVIDIA_MODEL")
FALLBACK_MODEL = os.getenv("NVIDIA_MODEL")

ESCALATED_MAX_TOKENS = 64000
DEFAULT_MAX_TOKENS = 8000
MAX_RECOVERY_RETRIES = 3
MAX_RETRIES = 10
BASE_DELAY_MS = 500
MAX_CONSECUTIVE_529 = 3
CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly — "
    "no apology, no recap. Pick up mid-thought."
)

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    """Select and join prompt sections based on current context."""
    sections = []

    # Always loaded — identity, tools, workspace
    sections.append(PROMPT_SECTIONS["identity"])
    sections.append(PROMPT_SECTIONS["tools"])
    sections.append(PROMPT_SECTIONS["workspace"])

    # Conditional — memory loaded when MEMORY.md exists and has content
    memories = context.get("memories", "")
    if memories:
        sections.append(f"Relevant memories:\n{memories}")

    return "\n\n".join(sections)


_last_context_key = None
_last_prompt = None


def get_system_prompt(context: dict) -> str:
    """Cache wrapper — reassemble only when context changes.

    Uses json.dumps for deterministic serialization, not Python's hash()
    which has process randomization and fails on nested dicts/lists.
    This cache only avoids redundant string assembly within a process.
    Real Claude Code additionally protects API-level prompt cache via
    stable section ordering and SYSTEM_PROMPT_DYNAMIC_BOUNDARY.
    """
    global _last_context_key, _last_prompt
    key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)
    if key == _last_context_key and _last_prompt:
        print("  \033[90m[cache hit] system prompt unchanged\033[0m")
        return _last_prompt
    _last_context_key = key
    _last_prompt = assemble_system_prompt(context)

    loaded = ["identity", "tools", "workspace"]
    if context.get("memories"):
        loaded.append("memory")
    print(f"  \033[32m[assembled] sections: {', '.join(loaded)}\033[0m")
    return _last_prompt


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": BashArgs.model_json_schema(),
        },
    }
]


class RecoveryState:
    """Track recovery attempts across the loop."""

    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_529 = 0
        self.has_attempted_reactive_compact = False
        self.current_model = model


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


def reactive_compact(messages: list) -> list:
    """Emergency compact — teaching version keeps last N messages.
    Real CC generates a compact summary via LLM, then retries with
    the compacted message list. Teaching version simplifies to tail
    retention since s08/s09 already cover LLM-based compact."""
    print("  \033[31m[reactive compact] trimming to last 5 messages\033[0m")
    tail = messages[-5:]
    return [
        {
            "role": "user",
            "content": "[Reactive compact] Earlier conversation trimmed. "
            "Continue from where you left off.",
        },
        *tail,
    ]


def update_context(context: dict, messages: list) -> dict:
    """Derive context from real state: which tools exist, whether memory files exist."""
    memories = ""
    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_text().strip()
        if content:
            memories = content
    return {
        "enabled_tools": "bash",  # list(TOOL_HANDLER.keys())
        "workspace": str(WORKDIR),
        "memories": memories,
    }


def agent_loop(messages: list, context: dict):
    max_turns = 25
    system = get_system_prompt(context)
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS
    messages[0] = {**messages[0], "content": system}
    for turn in range(max_turns):
        try:
            reps = with_retry(
                lambda mt=max_tokens: client.chat.completions.create(
                    model=state.current_model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_tokens=mt,
                ),
                state,
            )
        except Exception as e:
            if is_prompt_too_long_error(e):
                if not state.has_attempted_reactive_compact:
                    messages[1:] = reactive_compact(messages)
                    state.has_attempted_reactive_compact = True
                    continue
                print("  \033[31m[unrecoverable] still too long after compact\033[0m")
                messages.append(
                    {
                        "role": "assistant",
                        "content": "[Error] Context too large, cannot continue.",
                    }
                )
                return

            name = type(e).__name__
            print(f"  \033[31m[unrecoverable] {name}: {str(e)[:100]}\033[0m")
            messages.append(
                {"role": "assistant", "content": f"[Error] {name}: {str(e)[:200]}"}
            )
            return

        choice = reps.choices[0]
        msg = choice.message
        # ── Path 1: max_tokens -> escalate or continue ──
        if choice.finish_reason == "length":
            # First escalation: don't append truncated output, retry same request
            if not state.has_escalated:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                print(
                    f"  \033[33m[max_tokens] escalating"
                    f" {DEFAULT_MAX_TOKENS} -> {ESCALATED_MAX_TOKENS}\033[0m"
                )
                continue
            # 64K still truncated: save truncated output + continuation prompt
            messages.append(msg.model_dump(exclude_none=True))
            if state.recovery_count < MAX_RECOVERY_RETRIES:
                messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                state.recovery_count += 1
                print(
                    f"  \033[33m[max_tokens] continuation"
                    f" {state.recovery_count}/{MAX_RECOVERY_RETRIES}\033[0m"
                )
                continue
            print("  \033[31m[max_tokens] recovery limit reached\033[0m")
            return

        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            return msg.content
        for tc in msg.tool_calls:
            if tc.function.name != "bash":
                result = f"Error: unknown tool {tc.function.name}"
            else:
                try:
                    args = BashArgs.model_validate_json(tc.function.arguments)
                except Exception as e:
                    print(f"Error: failed to parse tool arguments: {str(e)}")
                    result = f"Error: failed to parse tool arguments: {str(e)}"
                else:
                    result = run_bash(args.command)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
            print(f"[tool result] {result[:200]}")
        context = update_context(context, messages)
        system = get_system_prompt(context)
        messages[0] = {**messages[0], "content": system}
    print("达到最大轮次")


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    context = update_context({}, [])
    system = get_system_prompt(context)
    messages = [{"role": "system", "content": system}]
    while True:
        user_input = input(">>> ")
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages, context)
        context = update_context(context, messages)
