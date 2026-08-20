"""反事实对照开关:off 臂用来关能力,不是用来出题。

判定标准在设计文档 §1.2:off 臂必须失败。本文件只钉【关法本身】——
关了真的从池子里消失 / MCP 连不上 / 强制 compact 真的 force=True。
题目合不合格是后面跑批的事。
"""

import importlib.util

from tests.test_guards import TRUNK_PATH


def test_disable_tools_pops_copy_not_registry(trunk, monkeypatch):
    """摘工具只动 assemble_tool_pool 的浅拷贝,TOOL_REGISTRY 全集不许被改。"""
    monkeypatch.setattr(trunk, "DISABLE_TOOLS", ("spawn_subagent", "load_skill"))
    monkeypatch.setattr(trunk, "mcp_clients", {})
    monkeypatch.setattr(trunk, "MEMORY_MODE", "official")
    pool = trunk.assemble_tool_pool()
    assert "spawn_subagent" not in pool
    assert "load_skill" not in pool
    assert "bash" in pool
    assert "spawn_subagent" in trunk.TOOL_REGISTRY
    assert "load_skill" in trunk.TOOL_REGISTRY


def test_disable_tools_empty_leaves_pool_alone(trunk, monkeypatch):
    monkeypatch.setattr(trunk, "DISABLE_TOOLS", ())
    monkeypatch.setattr(trunk, "mcp_clients", {})
    monkeypatch.setattr(trunk, "MEMORY_MODE", "official")
    assert trunk.assemble_tool_pool().keys() == trunk.TOOL_REGISTRY.keys()


def test_disable_mcp_blocks_connect_without_registering(trunk, monkeypatch):
    monkeypatch.setattr(trunk, "BENCH_DISABLE_MCP", True)
    before = dict(trunk.mcp_clients)
    out = trunk.connect_mcp_name("docs")
    assert out.startswith("Error: MCP disabled")
    assert trunk.mcp_clients == before


def test_disable_mcp_off_still_connects_mock(trunk, monkeypatch):
    monkeypatch.setattr(trunk, "BENCH_DISABLE_MCP", False)
    monkeypatch.setattr(trunk, "mcp_clients", {})
    out = trunk.connect_mcp_name("docs")
    assert out.startswith("Connected")
    assert "docs" in trunk.mcp_clients


def test_skills_dir_override_on_fresh_import(tmp_path, monkeypatch):
    """BENCH_SKILLS_DIR 指到考场外时,扫描读的是那份,不是 cwd/skills。"""
    outside = tmp_path / "host-skills"
    skill = outside / "house-style"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: house-style\ndescription: only via load_skill\n---\nSECRET_MARKER_QX\n",
        encoding="utf-8",
    )
    exam = tmp_path / "exam"
    exam.mkdir()
    monkeypatch.chdir(exam)
    monkeypatch.setenv("BENCH_SKILLS_DIR", str(outside))
    monkeypatch.delenv("TODO_MODE", raising=False)
    monkeypatch.delenv("MEMORY_MODE", raising=False)
    monkeypatch.delenv("BENCH_DISABLE_TOOLS", raising=False)
    spec = importlib.util.spec_from_file_location("trunk_skills_override", TRUNK_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.SKILLS_DIR == outside
    m._scan_skills()
    assert "house-style" in m.SKILL_REGISTRY
    assert "SECRET_MARKER_QX" in m.load_skill("house-style")
    assert not (exam / "skills").exists()


def test_should_copy_skills_default_on_without_importing_run_bench(monkeypatch):
    """老题依赖 skills 进考场。默认必须是拷。

    不 import run_bench:它在模块级会建 runs/ 目录。把函数源码 exec 出来测谓词。
    """
    import ast
    from pathlib import Path

    monkeypatch.delenv("BENCH_COPY_SKILLS", raising=False)
    src = Path(__file__).parent.parent / "bench" / "run_bench.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "should_copy_skills"
    )
    ns: dict = {"os": __import__("os")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(src), "exec"), ns)
    should_copy_skills = ns["should_copy_skills"]
    assert should_copy_skills({}) is True
    assert should_copy_skills({"BENCH_COPY_SKILLS": "0"}) is False
    assert should_copy_skills({"BENCH_COPY_SKILLS": "1"}) is True
    monkeypatch.setenv("BENCH_COPY_SKILLS", "0")
    assert should_copy_skills({"BENCH_COPY_SKILLS": "1"}) is False  # 操作员优先
