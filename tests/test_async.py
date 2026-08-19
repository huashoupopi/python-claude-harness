"""run_bash 可丢线程。被测:examples/22_trunk.py。"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_async_example(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", tmp_path)
    monkeypatch.setattr(sandbox, "SANDBOX_MODE", "off")
    result = await asyncio.to_thread(sandbox.run_bash, "echo Hello, World!")
    assert "Hello, World!" in result
