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

## 已知的债

- [ ] `harness/` 是**死包**，`main.py` 还在 import 它 —— 真正在跑的是 `examples/22_trunk.py`
- [ ] 两份黑名单（`DENY_LIST` 与 `run_bash` 里的 `dangerous`）
- [ ] `teammate` 那条线仍是非流式（主 agent 已流式，行为不一致）
