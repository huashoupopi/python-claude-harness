import asyncio

import pytest


@pytest.mark.asyncio
async def test_async_example():
    from harness.tools import run_bash

    result = await asyncio.to_thread(run_bash, "echo Hello, World!")
    assert result.strip() == "Hello, World!"
