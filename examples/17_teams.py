import json
import os
import random
import subprocess
import threading
import time
from datetime import datetime
from enum import StrEnum
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
TASKS_DIR = WORKDIR / ".tasks"
TASKS_DIR.mkdir(exist_ok=True)
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
MAILBOX_DIR = WORKDIR / ".mailboxes"
MAILBOX_DIR.mkdir(exist_ok=True)
model = os.getenv("NVIDIA_MODEL")


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
    if not can_start(task_id):
        deps = [
            d
            for d in task.blockedBy
            if not _task_path(d).exists() or load_task(d).status != TaskStatus.COMPLETED
        ]
        return f"Blocked by: {deps}"
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


# Load durable jobs on startup, then start scheduler thread
load_durable_jobs()
threading.Thread(target=cron_scheduler_loop, daemon=True).start()
print("  \033[35m[cron] scheduler thread started\033[0m")


class MessageBus:
    def send(
        self, from_agent: str, to_agent: str, content: str, msg_type: str = "message"
    ):
        msg = {
            "from": from_agent,
            "to": to_agent,
            "content": content,
            "type": msg_type,
            "ts": time.time(),
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


class SendMessageArgs(BaseModel):
    to: str = Field(..., description="the recipient teammate's name")
    content: str = Field(..., description="the message content to send")


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
        f"Send results via send_message to 'lead'."
    )

    def run():
        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": prompt})
        sub_tool_registry = {
            "bash": ("Run a shell command.", BashArgs, run_bash),
            "send_message": (
                "Send a message to another agent.",
                SendMessageArgs,
                lambda to, content: (BUS.send(name, to, content), "Sent")[1],
            ),
        }
        sub_tools = [
            {
                "type": "function",
                "function": {
                    "name": t_name,
                    "description": desc,
                    "parameters": args_model.model_json_schema(),
                },
            }
            for t_name, (desc, args_model, _) in sub_tool_registry.items()
        ]

        for _ in range(10):
            inbox = BUS.read_inbox(name)
            if inbox:
                messages.append(
                    {"role": "user", "content": f"<inbox>{json.dumps(inbox)}</inbox>"}
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
            for tc in msg.tool_calls:
                entry = sub_tool_registry.get(tc.function.name)
                if not entry:
                    output = f"Error: unknown tool '{tc.function.name}'"
                else:
                    desc, ArgsModel, handler = entry
                    try:
                        args = ArgsModel.model_validate_json(
                            tc.function.arguments or "{}"
                        )
                    except Exception as e:
                        output = f"Error: invalid arguments for {tc.function.name}: {e}"
                    else:
                        output = handler(**args.model_dump())
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": output}
                )

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
    return f"Teammate '{name}' spawned as {role}"


# ── Team Tool Handlers (s15 new) ──


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"


def run_check_inbox() -> str:
    msgs = BUS.read_inbox("lead")
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        lines.append(f"  [{m['from']}] {m['content'][:200]}")
    return "\n".join(lines)


class BashArgs(BaseModel):
    command: str = Field(..., description="the shell command to run")
    run_in_background: bool = Field(
        False, description="whether to run the command in the background"
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


class SpawnTeammateArgs(BaseModel):
    name: str = Field(..., description="the name of the teammate agent")
    role: str = Field(..., description="the role or persona of the teammate agent")
    prompt: str = Field(..., description="the initial prompt for the teammate agent")


def run_bash(command: str, run_in_background: bool = False) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: dangerous command blocked"
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


TOOL_REGISTRY = {
    "bash": ("Run a shell command.", BashArgs, run_bash),
    "create_task": (
        "Create a new task with subject, description, and optional blockedBy list.",
        CreateTaskArgs,
        run_create_task,
    ),
    "list_tasks": (
        "List all tasks with their status and dependencies.",
        NoneArgs,
        run_list_tasks,
    ),
    "get_task": (
        "Get full details of a task by its ID.",
        TaskIdArgs,
        run_get_task,
    ),
    "claim_task": (
        "Claim a task by its ID, marking it as in_progress if possible.",
        TaskIdArgs,
        run_claim_task,
    ),
    "complete_task": (
        "Mark a task as completed by its ID, unblocking dependent tasks.",
        TaskIdArgs,
        run_complete_task,
    ),
    "schedule_cron": (
        "Schedule a cron job with a cron expression and prompt.",
        ScheduleCronArgs,
        run_schedule_cron,
    ),
    "list_crons": (
        "List all scheduled cron jobs with their details.",
        NoneArgs,
        run_list_crons,
    ),
    "cancel_cron": (
        "Cancel a scheduled cron job by its ID.",
        CancelCronArgs,
        run_cancel_cron,
    ),
    "spawn_teammate": (
        "Spawn a teammate agent with a name, role, and initial prompt.",
        SpawnTeammateArgs,
        run_spawn_teammate,
    ),
    "send_message": (
        "Send a message to another teammate agent.",
        SendMessageArgs,
        run_send_message,
    ),
    "check_inbox": (
        "Check the inbox for messages sent to this agent.",
        NoneArgs,
        run_check_inbox,
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
    for name, (desc, args_model, _) in TOOL_REGISTRY.items()
]

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": f"Available tools: {', '.join(TOOL_REGISTRY.keys())}.",
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


def update_context(context: dict, messages: list) -> dict:
    """Derive context from real state: which tools exist, whether memory files exist."""
    memories = ""
    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_text().strip()
        if content:
            memories = content
    return {
        "enabled_tools": list(TOOL_REGISTRY.keys()),  # list(TOOL_HANDLER.keys())
        "workspace": str(WORKDIR),
        "memories": memories,
    }


_bg_counter = 0
background_tasks: dict[str, dict] = {}  # bg_id → {tool_use_id, command, status}
background_results: dict[str, str] = {}  # bg_id → output
background_lock = threading.Lock()


def is_slow_operation(tc_name: str, tc_args: dict) -> bool:
    """Fallback heuristic: commands likely to take > 30s."""
    if tc_name != "bash":
        return False
    cmd = tc_args.get("command", "").lower()
    slow_keywords = [
        "install",
        "build",
        "test",
        "deploy",
        "compile",
        "docker build",
        "pip install",
        "npm install",
        "cargo build",
        "pytest",
        "make",
    ]
    return any(kw in cmd for kw in slow_keywords)


def should_run_background(tc_name: str, tc_args: dict) -> bool:
    """Model explicit request takes priority; fallback to heuristic."""
    if tc_args.get("run_in_background"):
        return True
    return is_slow_operation(tc_name, tc_args)


def execute_tool(tc) -> str:
    """Execute a tool call block, return output."""
    entry = TOOL_REGISTRY.get(tc.function.name)
    if not entry:
        return f"Error: unknown tool '{tc.function.name}'"
    else:
        desc, ArgsModel, handler = entry
        try:
            args = ArgsModel.model_validate_json(tc.function.arguments or "{}")
        except Exception as e:
            return f"Error: invalid arguments for tool '{tc.function.name}' - {str(e)}"
        else:
            print(f"[tool call] {tc.function.name} with args: {args}")
            return handler(**args.model_dump())


def start_background_task(tc, args) -> str:
    """Run tool in a daemon thread. Returns background task ID."""
    global _bg_counter
    _bg_counter += 1
    bg_id = f"bg_{_bg_counter:04d}"
    cmd = args.get("command", tc.function.name)

    def worker():
        result = execute_tool(tc)
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


session_context = update_context({}, [])
session_history = [{"role": "system", "content": get_system_prompt(session_context)}]


def agent_loop(messages: list, context: dict):
    max_turns = 25
    system = get_system_prompt(context)
    messages[0] = {**messages[0], "content": system}
    # fired = consume_cron_queue()
    # for job in fired:
    #     messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
    #     print(f"  \033[35m[inject cron] {job.prompt[:50]}\033[0m")
    for turn in range(max_turns):
        try:
            reps = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            messages.append(
                {"role": "assistant", "content": f"[Error] {type(e).__name__}: {e}"}
            )
            return context
        msg = reps.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            # print(f"[assistant] {msg.content}")
            return context
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            if should_run_background(tc.function.name, args):
                bg_id = start_background_task(tc, args)
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

            result = execute_tool(tc)
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

        context = update_context(context, messages)
        system = get_system_prompt(context)
        messages[0] = {**messages[0], "content": system}
    print("达到最大轮次")
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
    if user_query is not None:
        session_history.append({"role": "user", "content": user_query})
    if cron:
        fired = consume_cron_queue()
        for job in fired:
            session_history.append(
                {"role": "user", "content": f"[Scheduled] {job.prompt}"}
            )
            print(f"  \033[35m[inject cron] {job.prompt[:50]}\033[0m")
    context = agent_loop(session_history, session_context)
    if context:
        session_context = context
    session_context = update_context(session_context, session_history)
    # Check inbox for teammate results → inject into history
    inbox = BUS.read_inbox("lead")
    if inbox:
        inbox_text = "\n".join(f"From {m['from']}: {m['content'][:200]}" for m in inbox)
        session_history.append({"role": "user", "content": f"[Inbox]\n{inbox_text}"})
        print(f"\n\033[33m[Inbox: {len(inbox)} messages injected]\033[0m")
    print_latest_assistant_text(session_history)
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


if __name__ == "__main__":
    print("输入一个问题，回车发送。输入q退出。\n")
    # context = update_context({}, [])
    # system = get_system_prompt(context)
    # messages = [{"role": "system", "content": system}]
    threading.Thread(target=queue_processor_loop, daemon=True).start()
    print("  \033[35m[queue processor] started\033[0m")
    while True:
        try:
            user_input = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ["q", "quit", "exit"]:
            break
        # messages.append({"role": "user", "content": user_input})
        # agent_loop(messages, context)
        # context = update_context(context, messages)
        with agent_lock:
            run_agent_turn_locked(user_input)
