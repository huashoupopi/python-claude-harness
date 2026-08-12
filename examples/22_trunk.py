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

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

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
model = os.getenv("NVIDIA_MODEL")


def ensure_dirs():
    """建运行期目录。只在启动时调用——import 本模块不该在 cwd 里造目录。"""
    for d in (MEMORY_DIR, TASKS_DIR, MAILBOX_DIR, WORKTREES_DIR):
        d.mkdir(exist_ok=True)


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


def agent_loop(messages: list, context: dict):
    max_turns = 25
    # system = get_system_prompt(context)
    system = assemble_system_prompt(context)
    messages[0] = {**messages[0], "content": system}
    # fired = consume_cron_queue()
    # for job in fired:
    #     messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
    #     print(f"  \033[35m[inject cron] {job.prompt[:50]}\033[0m")
    TOOLS_Registry, TOOLS = assemble_tools()
    for turn in range(max_turns):
        try:
            reps = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                stream=True,
            )
        except Exception as e:
            messages.append(
                {"role": "assistant", "content": f"[Error] {type(e).__name__}: {e}"}
            )
            return context
        # msg = reps.choices[0].message
        # messages.append(msg.model_dump(exclude_none=True))
        text, tool_calls, finish_reason = accumulate_stream(reps)
        msg = build_message(text, tool_calls)
        messages.append(msg)
        calls = [tc for _, tc in sorted(tool_calls.items())]
        if not calls:
            return context
        for tc in calls:
            args = json.loads(tc.function.arguments or "{}")
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
    ensure_dirs()
    start_cron_scheduler()
    init_session()
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
