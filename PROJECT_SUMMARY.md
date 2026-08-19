# Project Summary: python-claude-harness

## Overview
The `python-claude-harness` project is a specialized learning repository designed to build a Claude Code-style Agent Harness from scratch using Python. The primary goal is to independently re-implement the mechanisms described in the `learn-claude-code` curriculum (20 chapters) to deeply understand agentic loops, tool calling, and context management.

## Key Technical Goals
- **Independent Implementation**: Creating a standalone harness without relying on historical projects.
- **Provider Adapter Design**: A core architectural focus to allow seamless switching between different LLM backends:
  - **Anthropic-compatible**: Using DeepSeek endpoints for low-cost, high-performance tool calling.
  - **OpenAI-compatible**: Supporting NVIDIA NIM (leveraging free endpoints).
  - **Other providers**: Future support for official Claude and local models.
- **Educational Output**: The project will culminate in a working harness, architecture documentation, and detailed learning notes.

## Project Specifications
- **Python Version**: `>=3.13`
- **Key Dependencies**:
  - `anthropic` & `openai`: For LLM API interactions.
  - `pydantic`: For data validation and settings.
  - `rich` & `loguru`: For enhanced terminal output and logging.
  - `typer`: For creating the command-line interface.
  - `python-dotenv`: For environment variable management.

## Planned Architecture
- `examples/22_trunk.py`: 真正在跑的主干（单文件，13 层）。
- `notes/`: Learning reflections per chapter.
- `comparisons/`: Benchmarking against Aider and the original Claude Code.

## Current Status
The project is in the initial phase (Phase 0), focusing on establishing a minimal working agent loop and the foundation for the Provider Adapter interface.
