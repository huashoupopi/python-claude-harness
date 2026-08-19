"""Skills 渐进式披露的可判定测试。被测:examples/22_trunk.py 的扫描 / load_skill / trace。

口径(2026-08-17 设计 v4):load_skill 返回完整 SKILL.md(含 frontmatter),不改实现。
本文件只钉四件事,全部不依赖模型行为:
  ① 未加载前 system prompt 只有名称与描述,没有正文
  ② load_skill 返回预期全文
  ③ 未知 skill 安全报错
  ④ trace 记下加载次数、内容长度、字符/4 估算 token
"""

import json

BODY_MARKER = "UNIQUE_SKILL_BODY_NEVER_IN_PROMPT_xyz"


def _write_skill(root, name, description, body):
    d = root / name
    d.mkdir(parents=True)
    text = (
        f"---\nname: {name}\ndescription: {description}\n---\n"
        f"# {name}\n\n{body}\n"
    )
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return text


def _isolated_skills(sandbox, tmp_path, monkeypatch):
    """把扫描目录和轨迹指到 tmp,并清空注册表。测完由调用方 restore。"""
    original = dict(sandbox.SKILL_REGISTRY)
    monkeypatch.setattr(sandbox, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(sandbox, "TRACE_DIR", tmp_path / ".traces")
    monkeypatch.setattr(sandbox, "_trace_events", [])
    monkeypatch.setattr(sandbox, "_trace_pending", {})
    sandbox.SKILL_REGISTRY.clear()
    return original


def test_system_prompt_has_catalog_not_body(sandbox, tmp_path, monkeypatch):
    """未加载前 prompt 只含名称与描述,不含 SKILL.md 正文。"""
    original = _isolated_skills(sandbox, tmp_path, monkeypatch)
    try:
        _write_skill(
            tmp_path / "skills",
            "demo-skill",
            "short catalog line",
            BODY_MARKER,
        )
        sandbox._scan_skills()
        prompt = sandbox.assemble_system_prompt({"skills": sandbox.list_skills()})
        assert "demo-skill" in prompt
        assert "short catalog line" in prompt
        assert BODY_MARKER not in prompt
        assert "---" not in prompt
    finally:
        sandbox.SKILL_REGISTRY.clear()
        sandbox.SKILL_REGISTRY.update(original)


def test_load_skill_returns_full_markdown_including_frontmatter(
    sandbox, tmp_path, monkeypatch
):
    """load_skill 返回完整 SKILL.md,含 frontmatter,不是解析后的 body。"""
    original = _isolated_skills(sandbox, tmp_path, monkeypatch)
    try:
        raw = _write_skill(
            tmp_path / "skills",
            "demo-skill",
            "short catalog line",
            BODY_MARKER,
        )
        sandbox._scan_skills()
        got = sandbox.load_skill("demo-skill")
        assert got == raw
        assert got.startswith("---")
        assert "name: demo-skill" in got
        assert BODY_MARKER in got
    finally:
        sandbox.SKILL_REGISTRY.clear()
        sandbox.SKILL_REGISTRY.update(original)


def test_unknown_skill_reports_available_names(sandbox, tmp_path, monkeypatch):
    """未知 skill 返回错误字符串,列出可用名字,不抛异常、不读路径。"""
    original = _isolated_skills(sandbox, tmp_path, monkeypatch)
    try:
        _write_skill(tmp_path / "skills", "demo-skill", "d", "body")
        sandbox._scan_skills()
        out = sandbox.load_skill("nope")
        assert out.startswith("Skill not found:")
        assert "nope" in out
        assert "demo-skill" in out
    finally:
        sandbox.SKILL_REGISTRY.clear()
        sandbox.SKILL_REGISTRY.update(original)


def test_trace_records_skill_load_count_and_length(sandbox, tmp_path, monkeypatch):
    """trace 可见加载事件:次数、字符数、字符/4 估算 token。"""
    original = _isolated_skills(sandbox, tmp_path, monkeypatch)
    try:
        raw = _write_skill(
            tmp_path / "skills",
            "demo-skill",
            "short catalog line",
            BODY_MARKER,
        )
        sandbox._scan_skills()
        sandbox.trigger_hook("PreToolUse", "load_skill", {"name": "demo-skill"})
        result = sandbox.load_skill("demo-skill")
        sandbox.trigger_hook("PostToolUse", "load_skill", result)
        sandbox.trigger_hook("Stop", [])

        calls = [
            e
            for e in sandbox._trace_events
            if e.get("kind") == "tool_call" and e.get("tool") == "load_skill"
        ]
        assert len(calls) == 1
        ev = calls[0]
        assert ev["skill_name"] == "demo-skill"
        assert ev["skill_found"] is True
        assert ev["skill_chars"] == len(raw)
        assert ev["skill_est_tokens"] == len(raw) // 4

        files = list((tmp_path / ".traces").glob("*.json"))
        assert files, "Stop 之后轨迹没落盘"
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["skills"]["load_count"] == 1
        load = payload["skills"]["loads"][0]
        assert load["name"] == "demo-skill"
        assert load["chars"] == len(raw)
        assert load["est_tokens"] == len(raw) // 4
        assert load["found"] is True
    finally:
        sandbox.SKILL_REGISTRY.clear()
        sandbox.SKILL_REGISTRY.update(original)


def test_trace_unknown_skill_is_found_false(sandbox, tmp_path, monkeypatch):
    """加载失败也进轨迹,found=False,长度 0 —— 次数仍要能数到。"""
    original = _isolated_skills(sandbox, tmp_path, monkeypatch)
    try:
        sandbox._scan_skills()
        sandbox.trigger_hook("PreToolUse", "load_skill", {"name": "ghost"})
        calls = [
            e
            for e in sandbox._trace_events
            if e.get("kind") == "tool_call" and e.get("tool") == "load_skill"
        ]
        assert len(calls) == 1
        assert calls[0]["skill_found"] is False
        assert calls[0]["skill_chars"] == 0
        assert calls[0]["skill_est_tokens"] == 0
    finally:
        sandbox.SKILL_REGISTRY.clear()
        sandbox.SKILL_REGISTRY.update(original)
