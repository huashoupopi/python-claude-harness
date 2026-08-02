from pathlib import Path

WORKDIR = Path.cwd()  # 改为当前工作目录，更通用
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_DIR.mkdir(exist_ok=True)


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
    real_path = resolve_memory_path(path)
    if not real_path.exists():
        return (
            f"Error: Memory file '{path}' does not exist. Please provide a valid path."
        )
    if real_path.is_dir():
        return _format_dir(real_path, path)
    else:
        return _format_file(real_path, path, view_range)


def _human_size(size):
    return f"{max(size, 1) / 1024:.1f}K"


def _format_file(real: Path, virtual: str, view_range: list[int] | None) -> str:
    lines = real.read_text().splitlines()
    start, end = 1, len(lines)
    if view_range:
        start = view_range[0] if view_range[0] else 1
        end = view_range[1] if view_range[1] != -1 else len(lines)
    out = [f"Here's the contene of {virtual} with line numbers:"]
    for i in range(start, end + 1):
        out.append(f"{i:>6}\t{lines[i - 1]}")

    return "\n".join(out)


def _format_dir(real: Path, virtual: str) -> str:
    out = [
        f"Here're the files and directories up to 2 levels deep in {virtual}, excluding hidden items and node_modules:"
    ]
    out.append(f"{_human_size(real.stat().st_size)}\t{virtual}/")
    for entry in sorted(real.glob("*")) + sorted(real.glob("*/*")):
        if any(
            en.startswith(".") or en == "node_modules" or en == "MEMORY.md"
            for en in entry.relative_to(real).parts
        ):
            continue
        temp = virtual / entry.relative_to(real)
        out.append(f"{_human_size(entry.stat().st_size)}\t{temp}")
    return "\n".join(out)


def run_memory_create(path: str, content: str) -> str:
    real_path = resolve_memory_path(path)
    if real_path.name == "MEMORY.md":
        return f"Error: Tje path {path} is reserved"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text(content)
    _rebuild_index()
    return f"File created successfully at: {path}"
