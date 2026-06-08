# python-claude-harness

**独立学习项目**：从零构建 Claude Code 风格的 Agent Harness（Python 实现）。

## 项目定位
- 完全独立的项目（不强制迁移/集成到 rework 等历史项目）。
- 先跟随官方 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 20章主线学习机制。
- 同时在自己的仓库里**独立重新实现**，加深理解。
- 强调 **Provider Adapter** 设计，从早期开始支持多后端（DeepSeek Anthropic 兼容、NVIDIA NIM OpenAI 兼容、未来 PydanticAI 等）。
- 最终产出：可运行的独立 harness + 详细笔记 + 架构设计文档 + 迁移经验（对就业有直接帮助）。

## 推荐学习方式
1. 先按官方课程走（s01 → s20），用参考仓库跑示例。
2. 每章学完后，在本项目里**自己重新实现**对应部分（参考 `../stages/` 的补充说明）。
3. 重点关注“为什么这么设计 + 如何做 Provider 抽象 + 生产级考虑”。

## 快速开始（参考官方课程）

### 1. 克隆参考仓库（只读，用于跑官方示例和对照源码）
```bash
# 在 claude-code-harness-study 目录下执行
cd /Users/liuchenxu/Documents/Documents/code/claude-code-harness-study
git clone https://github.com/shareAI-lab/learn-claude-code
cd learn-claude-code
pip install -r requirements.txt
cp .env.example .env
```

### 2. 配置 API（推荐先用 DeepSeek Anthropic 兼容端点）
编辑 `learn-claude-code/.env`：

```env
ANTHROPIC_API_KEY=sk-你的DeepSeek-key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL=deepseek-chat
```

- DeepSeek 的 Anthropic 兼容端点可以让官方示例代码**几乎不改**就能跑，tool calling 支持优秀，成本很低。
- 获取 DeepSeek key：https://platform.deepseek.com

### 3. 运行官方示例（跟随课程）
```bash
python s01_agent_loop/code.py          # 起点：一个循环 + bash
python s08_context_compact/code.py     # 上下文压缩（重要章）
python s20_comprehensive/code.py       # 终点章：全部机制归到一个循环
```

### 4. 在本项目（python-claude-harness）里开始独立实现
本目录就是你的**独立实现仓库**。

推荐结构（我们会逐步填充）：
```
python-claude-harness/
├── harness/                 # 核心实现（你自己写）
│   ├── core/
│   │   ├── loop.py
│   │   ├── provider_adapter.py   # 关键抽象，支持多 provider
│   │   ├── context.py
│   │   └── ...
│   ├── tools/
│   └── ...
├── examples/
├── notes/                   # 每章复盘笔记（强烈建议写）
├── comparisons/             # 与官方实现、Aider、真实 CC 的对比
└── pyproject.toml
```

**第一阶段（Phase 0）要做的事**（对应官方 s01 + 我们的补充）：
- 理解最小 agent loop（messages + tool_use/tool_result）。
- 在自己的项目里**手写**一个最小可工作的 loop（不要直接复制官方代码）。
- 开始考虑 ProviderAdapter 的接口设计（我们会在这个阶段引入）。
- 写复盘笔记（见 `../stages/01_agent_loop.md`）。

## API 策略（已确认）
- **跟随官方课程阶段**：优先用 **DeepSeek Anthropic 兼容端点**（格式最匹配，tool_use 块一致，官方示例基本零修改）。
- **NVIDIA NIM（你有免费额度）**：非常好的免费模型资源（很多 Free Endpoint）。它是 OpenAI 兼容格式。
  - 我们会在 Provider Adapter 阶段专门支持 OpenAI 格式 provider。
  - 届时你可以把 NVIDIA 的免费强模型（例如 nemotron 系列、cosmos 等）接进来测试。
  - 你的当前 rate limit（huashoupopi, 40 rpm）很合适学习使用。
- 后期可以轻松切换：DeepSeek → NVIDIA → 官方 Claude → 本地模型 等。

## 下一步建议
1. 先按上面步骤 clone 参考仓库 + 配置 DeepSeek .env + 跑通官方 s01。
2. 同时阅读 `../stages/01_agent_loop.md`（我们为独立项目调整后的版本，增加了 adapter 思考和独立项目说明）。
3. 当你跑通官方 s01 后，告诉我，我们立刻开始在 `python-claude-harness` 里写第一个可工作的 loop + 基础 adapter 骨架。

所有代码、笔记、对比都放在这个独立仓库里，最终会成为一个很好的学习 + 面试展示项目。

有任何问题（clone 报错、.env 配置、NVIDIA 如何在 adapter 里用、第一阶段具体代码结构等），随时问我。我们一步一步来。

加油！这个项目做扎实了，对理解真实 agent harness 帮助非常大。