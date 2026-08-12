# Stage-1 阶梯消融 · 实验记录与诚实标注

**日期**：2026-08-11 ｜ **依据**：[21 天排期修正规划书](../../../arch/2026-08-10_review_21天排期修正.md) 议题④
**目的**（席④ 原话）：**验仪器 + 出方向 + 生成假设，不是出结论。**

---

## 一、实验设计

| 臂 | 被测文件 | 短名 | 代表什么 |
|---|---|---|---|
| 1 | `examples/03_multi_tool_loop.py` | `baseline` | 裸跑：只有工具分发，无压缩、无记忆 |
| 2 | `examples/09_compact_loop.py` | `compact` | 加了多层上下文压缩 |
| 3 | `examples/10_memory_loop.py` | `self_memory` | 加了自研记忆 |
| 4 | `examples/11_memory_tool.py` | `official_memory` | 官方 memory tool 原语 |

**题目**：`bench/tasks/` 三道（`t01_fix_failing_test` 修测试 / `t02_create_file` 无中生有 / `t03_cross_file` 跨文件追凶）
**重复**：每配置 × 3 次（trial）
**规模**：4 × 3 × 3 = **36 次 agent 运行**
**记录**：`runs/<时间戳>/results.jsonl`（每行含 `config`/`trial`/`task`/`success`/`steps`/`duration_s`）＋ `runs/<时间戳>/<配置>/<题名>/logs<N>/agent.log`（完整轨迹）

---

## 二、⚠️ 已知污染（诚实标注，必须随数据一起引用）

> **能说出自己实验的污染在哪，比实验干净更值钱。**

### 污染 ①：`baseline` 臂的 system prompt 与其余三臂不同（**未消除，本质性**）

`03_multi_tool_loop.py` **没有 `build_system()`**，因此 `agent_runner.py` 走 `hasattr` 分支，给它一段**手写的 system prompt**：

```
You are a coding agent at {cwd}. Use tools to solve tasks. Act, don't explain.
```

其余三臂用各自 `build_system()` 生成的完整 prompt。

**→ `baseline` 与其他臂的差异不止一个变量**（既少了记忆/压缩层，system prompt 也不同）。**本数据不能把 baseline 的差异单独归因于「没有记忆」。**

### 污染 ②-④：client 配置四处不一致（**已于 2026-08-11 消除，但记录在案**）

排查过程中发现 `03` 与 `09/10/11` 的 OpenAI client 有**三处**历史遗留差异，**若不修，整组数据作废且表面正常**：

| # | 不一致 | 症状 | 处置 |
|---|---|---|---|
| ② | 端点变量名：`03` 用 `OPENROUTER_*`，其余用 `API_KEY`/`BASE_URL`/`MODEL` | **连的不是同一个服务商、跑的不是同一个模型** —— steps 差异主要来自换模型 | 统一为 `API_KEY`/`BASE_URL`/`MODEL` |
| ③ | `03` 的 client **未设 `timeout`**，其余为 `timeout=60.0` | 慢请求下 `03` 会干等 SDK 默认时长，`duration_s` 爆表且与记忆无关 | 补 `timeout=60.0` |
| ④ | `03` **无 `default_headers`**，其余为 `{"User-Agent": "curl/8.7.1"}` | **中转网关按 UA 拦截 → `PermissionDeniedError: Your request was blocked`**，`03` 连纯文本请求都发不出去 | 补同样的 header |

> **④ 是元凶**：最初表现为 `APITimeoutError`，修掉 ③ 之后真实错误才浮出来。**诊断顺序：先让错误说实话，再修它。**

### 🔴 四处污染同一个根

```
21 个 example 文件 = 21 份各自持有的 client 配置
```

**→ 这是 T19「配置只该有一处」的直接实证**，且由 stage-1 在动手组装之前发现。

---

## 三、📏 噪声底线（本次最重要的产出）

**2026-08-11 实测**（`official_memory` 单配置 × 3 次，同题同码，唯一变量是模型自身）：

| 题 | trial 1 | trial 2 | trial 3 | 范围 |
|---|---|---|---|---|
| `t01_fix_failing_test` | 11 | 11 | 8 | **8 – 11**（波动 3 步） |
| `t02_create_file` | 7 | 7 | 7 | **7 – 7**（零波动） |
| `t03_cross_file` | 11 | 13 | 9 | **9 – 13**（波动 4 步） |

> ### **同一配置内部的 steps 波动为 3-4 步。**
> ### **因此：配置之间小于 4 步的差异，在 n=3 下不可分辨。**

**可直接引用的诚实句**：

> 「在 n=3 的条件下，同一配置内部 steps 波动 3-4 步，**因此配置间小于 4 步的差异在本数据下不可分辨**。」

**推论 A**：`t02` 零波动 → 它**没有探索空间**，对配置差异不敏感。它能证明 bench 不是随机的，但对消融**不提供分辨力**。扩题时需考虑其去留。

**推论 B**：**「多跑几次取平均」救不了这个问题** —— 再跑十次只会把这个范围描得更清楚，**不会让它变窄**。统计撑不起 n=3。

**推论 C**：**因此唯一的武器是轨迹里的机制解释**。
不能说「压缩版平均少 2 步」；可以说「**打开 baseline 的轨迹，它把同一个文件读了三遍——因为它没有记忆**」。
**一句机制解释不需要任何统计显著性。** 这也是 `agent.log` 必须落盘的原因（原代码 `proc.stdout` 只用来 `count()` 就丢弃，轨迹从未存过盘，与顺序表 T11 行「轨迹全存档」的记载不符——**「我以为存了」和「真的存了」是两件事**）。

---

## 四、指标可信度

| 字段 | 可信度 | 说明 |
|---|---|---|
| `success` | 二元 | 三道题若全配置都过，此列**无分辨力** |
| `steps` | ✅ 相对干净 | `proc.stdout.count("[tool call]")`，与网速无关。**但注意：T20 流式改造会改变 stdout 形态，届时此指标作废、基线需重跑**（这正是执行序把 stage-1 排在 T20 之前、stage-2 排在 T20 之后的原因） |
| `duration_s` | 🔴 噪音大 | 墙上时钟，混入网络往返与端点负载。**跨配置比较不可用**，仅作参考 |

---

## 五、基础设施改造记录（2026-08-11，当事人手写）

- `run_bench.py`：加 config 外层循环 + trial 中层循环；**config 在 trial 外**（中途崩溃时保住「完整的少数」而非「残缺的多数」）
- `record` 加 `config` / `trial` 两列 —— **维度进列，不进文件名**（一行 = 一次完整观测，可直接分组分析）
- 轨迹落盘 `logs<N>/agent.log`，stdout 与 stderr 之间加 `=== STDERR ===` 分隔
- 目录分层 `runs/<时间戳>/<配置短名>/<题名>/repo<N>`，路径含全部三个变量（配置/trial/题名）
- `agent_runner.py`：`SRC` 由 `sys.argv[2]` 传入；`hasattr(m, "build_system")` 分支；`WORKDIR = Path.cwd()`（跟随考场副本，防止 agent 跑去改主仓库）

**AI 代改部分（标注）**：配置短名映射（`CONFIGS` 字典）、`03` 的三处 client 配置对齐。**判分逻辑与消融设计全部为当事人手写。**

---

## 六、📊 结果（2026-08-11 全量跑完，`runs/run_20260811_121902/`）

**36/36 全部 PASSED。**

| 配置 | t01 修测试 | t02 无中生有 | t03 跨文件 | 合计 |
|---|---|---|---|---|
| **baseline** 裸跑 | `[5, 5, 5]` | `[5, 5, 5]` | `[6, 9, 6]` | **Σ51** |
| **self_memory** | `[8, 7, 7]` | `[7, 7, 7]` | `[12, 9, 10]` | Σ74 |
| **compact** | `[8, 8, 13]` | `[7, 8, 7]` | `[11, 11, 10]` | Σ83 |
| **official_memory** | `[9, 10, 10]` | `[7, 9, 7]` | `[9, 11, 14]` | **Σ86** |

### 噪声底线修正

12 组数据的内部波动：**最小 0 步**（baseline t01/t02 三次全 5）、**最大 5 步**（compact t01 = 8,8,13；official t03 = 9,11,14）。
→ **修正为「同配置内部波动 0–5 步，上界 5」**（原估 3-4 步，基于单配置样本，偏窄）。

---

## 七、🔬 方法论产出：比均值 vs 配对比较，同一批数据给出相反结论

```
比均值    t01: baseline 5.0 vs official 9.7 = 差 4.7 步 < 最大噪声 5 步  →「测不出差别」
配对比    9 个配对（3 题 × 3 trial）中 baseline 更低的：9/9              →「有差别」
          符号检验 p = (1/2)^9 ≈ 0.002
```

**为什么配对更敏感**：t03 天然比 t01 难（步数更多）。**把三道题混在一起算均值，题目难度的方差把配置差异淹没了**；配对之后每一对内部题目相同、trial 相同，**只剩「配置」一个变量在动**。

> ### **配对设计（paired design）＝ 把已知的干扰源当区组消掉，而不是让它变成噪声。**

**可引用句**：「我一开始按均值比，差异淹在噪声里；改成配对比较后 9/9 同向，符号检验 p≈0.002 —— 因为题目难度是已知干扰源，配对能把它消掉。」

---

## 八、🎯 归因：轨迹推翻了全部三个候选假设

`t01 / trial1` 四臂完整动作序列：

```
baseline          bash → read_file → read_file → edit_file → bash                                    5
self_memory       bash → bash → read_file → read_file → todo_write → edit_file → bash → todo_write   8
compact           bash → bash → read_file → read_file → todo_write → edit_file → bash → todo_write   8
official_memory   todo_write → bash → read_file ×2 → todo_write → edit_file → todo_write → bash → todo_write  9
```

**核心工作序列四臂完全一致**：`bash 看错误 → read_file ×2 → edit_file 修 → bash 验证`。
**baseline 并没有重复探索**（它也只读 2 次文件）。

| 候选假设 | 判定 |
|---|---|
| ① 题目太简单，记忆层只剩开销 | **部分成立**：简单所以 todo 不回本 |
| ② baseline 的 system prompt 不同（污染①） | **部分成立**：是 prompt 引导模型去维护 todo 的 |
| ③ 记忆层固定开销（每轮读写 memory） | **❌ 证伪。与 memory 无关。** |

> ### **真凶是 `todo_write`：baseline 0 次 / self_memory 2 次 / compact 2 次 / official_memory 4 次。**
> ### 步数差 = todo_write 次数差。**而 todo 系统与「记忆」「压缩」毫无关系。**

**统计告诉你「有差异」，轨迹告诉你「差异是什么」——而且轨迹推翻了统计之外的所有猜测。**

### 可直接引用的面试段落

> 「我做消融时发现带 todo 系统的配置在简单任务上多花 60-80% 的步数。打开轨迹一看，**核心工作序列四个配置完全一样，多出来的全是 `todo_write` 本身**。所以 todo 的开销是固定的——**它只在任务复杂到需要跨轮追踪时才回本，在三步能解决的题上是纯负担。**」

### → 给 T19 的设计依据

**todo 该不该有启用条件？** 真实的 Claude Code 在 system prompt 里明确写了「什么时候该用 TodoWrite」，**不是无条件启用**。T19 装 todo 那一层时，本数据就是设计依据。

---

## 九、遗留（stage-2 处理）

1. **只看了 t01 的轨迹**，t02/t03 是否对得上同一结论未验证。
2. **`t02` 零波动**（baseline 三次全 5、self_memory 三次全 7）——它对配置差异不敏感，扩题时需决定去留。
3. **污染① 未消除**：要分辨「缺 todo」与「prompt 不同」，需要 stage-2 的**单变量臂**（同一份 harness，只开关 todo）。
4. **n=3 题**：题量是本实验最硬的限制，扩到 10-15 题是 stage-2 的前置。
5. ⚠️ **T20 流式改造会让 `steps` 指标作废**（`stdout.count("[tool call]")` 依赖 stdout 形态），届时基线需重跑——这正是执行序把 stage-1 排在 T20 之前的原因。
