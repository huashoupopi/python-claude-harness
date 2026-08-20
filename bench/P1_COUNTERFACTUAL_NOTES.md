# P1 反事实实跑（2026-08-21）

> 设计：`docs/2026-08-21_题库拓宽与幻觉专项设计.md` §1.2。
> **本文件只记实测。** t20 与 t18/t19 **不同类**，见 §四。

## 实验配置（五项全同，只切能力）

| 项 | 值 |
|---|---|
| commit | `24698f1` (`24698f10d9de2e1b8ee4a6e1b3aabe26cbfb6df2`) |
| 模型 | `grok-composer-2.5-fast`（每条 `results.jsonl` 的 `model` 字段） |
| `TIMEOUT_S` | 900 |
| `max_turns` | 25（`agent_loop` 写死） |
| sandbox | `BENCH_SANDBOX=1`（镜像 `harness-sandbox:1`） |
| 配置臂 | 只跑 `mem_self`（`MEMORY_MODE=self` `TODO_MODE=nudge`） |
| trial | 每臂 3 |
| workers | 4 |
| 墙钟 | 2026-08-20T18:09:47Z → 18:24:55Z（约 15 min） |

批次目录（`bench/runs/`，gitignore，不入库）：

| 臂 | 目录 |
|---|---|
| on（四题能力开） | `run_20260821_020947` |
| t17 off（`BENCH_DISABLE_TOOLS=spawn_subagent`） | `run_20260821_021344` |
| t18 off（`BENCH_DISABLE_TOOLS=load_skill`，bench.env 仍不拷 skills/） | `run_20260821_021524` |
| t19 off（`BENCH_DISABLE_MCP=1`） | `run_20260821_021820` |
| t17 on（判分修复后 `996f6a1`） | `run_20260821_023920` |
| t17 off（判分修复后） | `run_20260821_024128` |

t20 **没有 off 臂**。

复现：`bash bench/run_p1_counterfactual.sh`

---

## 一、总表（判分出口码）

| 题 | 臂 | t1 | t2 | t3 | 通过 | 目标能力调用 |
|---|---|---|---|---|---:|---|
| t18_house_style | on | PASS 24步 157s | PASS 12步 74s | FAIL 10步 55s | **2/3** | load_skill 2, 2, 1 次（**3/3 都调了**） |
| t18_house_style | off | FAIL 29步 | FAIL 26步 | FAIL 29步 | **0/3** | load_skill 0；widget.py 与原卷逐字节相同 |
| t19_mcp_lookup | on | PASS 8步 39s | PASS 8步 30s | PASS 8步 30s | **3/3** | connect_mcp + `mcp__quota__get_limit` 各 1 次；`WIDGET_LIMIT=18427` |
| t19_mcp_lookup | off | FAIL 32步 338s | FAIL 47步 395s | FAIL 55步 390s | **0/3** | connect_mcp 拿到 `Error: MCP disabled`；`get_limit` 0 次；`WIDGET_LIMIT` 仍是 100 |
| t17_divide_audit | on（第一轮，判分坏了，**无效**） | FAIL | PASS | PASS | 2/3 | 见 §五；不能当结论 |
| t17_divide_audit | off（第一轮，判分坏了，**无效**） | FAIL | FAIL | FAIL | 0/3 | 见 §五 |
| t17_divide_audit | on（判分修复后 `996f6a1`） | PASS 23步 | PASS 23步 | PASS 23步 | **3/3** | spawn_subagent 6/6/6；`tokens_subagent` 52723 / 51472 / 51764 |
| t17_divide_audit | off（判分修复后） | PASS 13步 | PASS 13步 | PASS 13步 | **3/3** | spawn_subagent 0；比 on 更少步 |
| t20_two_phase | on（单臂） | PASS 8步 | PASS 6步 | PASS 7步 | **3/3** | `kind=compact reason=force` 各 1 次；终态 FINDINGS.md=`adapter_e20d` |

无超时、无 `tests_tampered`。

---

## 二、t18：不可替代成立（通过率有方差）

**off 臂必须失败：成立。** 三次 widget.py 都没改。模型会调 `bash load_skill house-style`（shell 报 not found），然后 `find /` 找 SKILL.md，没拿到 `hs_` / `HouseError` / `# house-import-fence`。

**on 臂必须调用且通过：调用 3/3，通过 2/3。** t3 调了 `load_skill` 一次，但写回去的仍是 `def normalize` + `TypeError`，没落 house-style 标记。触发有了，应用不稳。

按设计「3/3 都出现目标能力调用」——调用这条过了；「on 必须通过」——没到 3/3。方差记在这里，不改题。

---

## 三、t19：不可替代成立

**on 3/3**：`connect_mcp('quota')` → `mcp__quota__get_limit` → 把 `WIDGET_LIMIT` 改成 18427。数字不在考场里。

**off 0/3**：工具返回 `Error: MCP disabled (BENCH_DISABLE_MCP=1)`。随后长时间搜文件系统 / 反汇编 `__pycache__`，没有写出 18427。`quota.py` 三次都停在 100。

这是四道题里对照最干净的一道。

---

## 四、t20：单臂，与 t18/t19 不同类

**不跑 off 臂。** 「不 compact」对话还在，天然更容易，构造不出必败对照。它测的不是「某工具不可替代」，是：

> 强制 compact 之后，agent 还能否靠事先落盘的 FINDINGS.md 续上第二阶段。

判据是 **compact 之前有没有落盘**，不是 off 臂失败。

实测 3/3 都过了 grade.sh（终态有 FINDINGS.md 且 `scale(n)==n*2`），`[auto-compact]` / `reason=force` 三次都响了。但时序并不都符合「先落盘再压缩」：

| trial | compact 之前写了 FINDINGS.md？ | 实际顺序 |
|---|---|---|
| t1 | **否** | `python probe.py` → `[auto-compact]` → 再 `write_file FINDINGS.md` → 修 adapter |
| t2 | **是** | probe → `echo > FINDINGS.md` → `[auto-compact]` → `edit_file` |
| t3 | **否** | probe → `[auto-compact]` → 再写 FINDINGS → 修 adapter |

t1/t3 是靠 compact 摘要或残余上下文记住了 `CHOSEN=adapter_e20d`，**事后补写** FINDINGS.md 来过判分。grade.sh 只看终态，看不出这一点。

**这两次比「3/3 通过」有价值：** agent 没有主动外化，是 compact 摘要碰巧保住了名字。那是运气，不是设计。压缩层比预想的更「救命」，但不能当成外化机制成立。

结论：强制 compact 注入点工作正常；「外化时序」只稳了 1/3。本题留作单臂诊断，**不与 t18/t19 的 on/off 表混列成同一条不可替代证据**。

---

## 五、t17：第一轮测不出来；判分修复后 off 也能过

预判（架构台）：六包合计约 2130 字符，装得下。**不放大模块**——放大测的是压缩层。

### 5.1 第一轮（`24698f1`）无效，不是结案

旧 `grade.sh` 要英文 `"default"` / `"is_live"`（变量名）/ `"float"`。task.md 只要求「一句话」。

第一轮记分板 on 2/3、off 0/3。**结论只能是「这次测不出来」**，不能是「证明了 subagent 可有可无」。六条违规模型全审对，中文被误杀。同族：rework R01/R02，尺子在测自己。

用修好的尺子回放第一轮六份 AUDIT.md：**on 3 份 + off 3 份全部转绿**。

### 5.2 判分修复后重跑（`996f6a1`，同一模型 / timeout / max_turns / sandbox / mem_self / 3 trial）

| 臂 | 目录 | t1 | t2 | t3 | 通过 | spawn_subagent | 中位步数 |
|---|---|---|---|---|---:|---:|---:|
| on | `run_20260821_023920` | PASS | PASS | PASS | **3/3** | 6 / 6 / 6 | 23 |
| off | `run_20260821_024128` | PASS | PASS | PASS | **3/3** | 0 / 0 / 0 | 13 |

on 的 `tokens_subagent`：52723 / 51472 / 51764（有数，不是伪造 0）。off 为 `null`。

off 三次 AUDIT.md 六条类型都写到了。t1/t2 有函数名对不上源码的情况（如 `run()` / `same_label()`），类型仍对，按「违规类型」尺子过。t3 中文，文件名也对。

off 比 on **更少步、更快**（13 步 / ~52s vs 23 步 / ~117s）：有 subagent 时模型会去调，但对本题不是刚需。

### 5.3 结论（写明：判分修复后测出来的）

**在判分修复之后的这次实测里，subagent 在这类任务里不是不可替代。** on 3/3 会调它，off 3/3 不用也能过。本题不进入「不可替代」列。模块体量未改。

---

## 六、还不构成「进正式题库」的

设计要求 on 臂 3/3 触发且通过、off 臂必须失败。按这个尺子：

| 题 | 进「不可替代」列？ |
|---|---|
| t19 | **是**（3/3 vs 0/3，数字对得上 MCP） |
| t18 | **是**，注明 on 有 1/3 方差（模型不服从，非信息缺失） |
| t17 | **否**。第一轮因判分无效；**判分修复后** off 3/3 也能过（见 §五） |
| t20 | **不适用该尺子**（见 §四）。另记外化时序仅稳 1/3，两次靠摘要兜底 |

---

## 七、原跑批字段摘录

on `run_20260821_020947`（节选）：

```
t19 t1/t2/t3  ok steps=8,8,8   tokens_loop≈15.5k
t20 t1/t2/t3  ok steps=8,6,7   tokens_loop=23.0k/15.2k/19.2k
t18 t1/t2 ok, t3 fail  steps=24/12/10
t17 t1 fail, t2/t3 ok  spawn_subagent=6  tokens_subagent=35621/51691/51057
```

无超时。
