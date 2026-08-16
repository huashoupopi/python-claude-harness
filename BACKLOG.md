# 待办

想到的功能扔这儿，别丢。做了就划掉并注明 commit。

---

## 交互体验（2026-08-16 提出）

对标 Claude Code 的手感。目前主干的 REPL 是最朴素的 `input()`。

- [ ] **输错了能改** —— 打错一个字符，按 ESC 退出当前输入重来，不用把整行删掉
      现状：`main()` 里就是裸 `input()`，没有行编辑
      方向：`readline` 模块（标准库，能给方向键/历史）或 `prompt_toolkit`（要装包）

- [ ] **接着上一轮聊** —— 恢复上一次会话继续
      📌 **难的那半已经有了**：`write_transcript()` 已在把完整对话存成
      `.transcripts/transcript_*.jsonl`，现在磁盘上就躺着几份
      缺的是：读回来塞进 `messages`、一个 `--resume` 参数、列出可选会话
      ⚠️ 要考虑：恢复之后 `.memory`、`.tasks` 这些状态对不对得上

- [ ] **界面美化** —— 现在全靠 ANSI 颜色码手拼
      可参考 `bench/trace_view.py` 的做法（分层：概览 → 逐行 → 展开）

- [ ] **其他亮点功能** —— 待定

---

## 评测（bench）

- [x] ~~🔴 **让记忆轴真的能说话**~~ —— **已做并收口**（t16 + `reset_between_rounds`，`3bc9b39`/`649f944`）
      结论是**「测不出」不是「没用」**：机制完全正常（记忆抓到了那三条规矩、注入 100%），
      但实测差 +2.0 步，而这个实验只能分辨 >10 步。
      要 settle 需要每臂 72 次 ≈ 12 小时 → 裁定不跑。见 `STAGE3_NOTES.md` §2.2
- [ ] 🟡 **幻觉螺旋专项题** —— t08 那次 48 个虚构文件只被采样到一次，
      而堵掉逃生口（藏答案）之后，尾部行为变成什么样**从未观察过**
- [ ] 🟡 子 agent / teammate 的 token 未计入
      （📌 2026-08-16 实测：225 次跑批里 `spawn_subagent`/`spawn_teammate` **零调用**，
      所以当前数据没受影响；等有题目真的用到再说）
- [ ] 🟢 `reasoning_tokens` / `cached_tokens` 未单独分析

---

## CI（2026-08-16 从化石里挖出来的）

- [x] ~~CI 跑的是 07-06 的代码~~ —— **已修**（`31c0790` + `75f3887`）
      根因：模块级 `OpenAI(api_key=None)` 当场抛 → 没有 `.env` 的环境 import 就炸，
      100 条塌 88 条，活下来的 12 条正是 07-06 那次绿灯的 12 条。
      顺带修了沙箱守卫（只查 daemon 没查镜像）。现在 CI 真跑当前代码 = **100 passed**。

- [x] ~~**`verify_tasks.py` 进 CI**~~ —— **已做**（`a064d7c`+`3940a99`）
      补的空白：**`pytest` 完全不看 `bench/tasks/`**，16 道题被改坏没有任何东西会发现
      连判次数做成 `VERIFY_REPEATS`，**CI 里设 1**：默认的 5 次是抓 flaky 的，
      而 flaky 靠「每进程哈希种子不同」，本地跑 5 次和 CI 跑 5 次是同一件事 ——
      CI 多跑那 4 次纯属重复本地做过的事。**实测 CI 整轮 48s**（原估 2-3 分钟）

- [x] ~~🟢 Node.js 20 deprecated 警告~~ —— **已消**：`checkout` v4→**v7**、`setup-uv` v3→**v10.0.1**
      🪝 踩到一个坑值得记：**major 浮动 tag 是惯例，不是保证**。
      查 latest release 拿到 `v10.0.1` 就写 `@v10` → `Unable to resolve action`。
      实际 tag 列表里 `checkout` 维护了 `v7`，而 `setup-uv` **从 v8 起就不维护** `v8`/`v9`/`v10`。
      → **写 action 版本前先看它到底有哪些 tag，别从 release 号推。**

---

## 已知的债

- [ ] `harness/` 是**死包**，`main.py` 还在 import 它 —— 真正在跑的是 `examples/22_trunk.py`
- [ ] 两份黑名单（`DENY_LIST` 与 `run_bash` 里的 `dangerous`）
- [ ] `teammate` 那条线仍是非流式（主 agent 已流式，行为不一致）
