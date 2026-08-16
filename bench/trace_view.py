"""看一条 agent 轨迹:这一跑到底干了什么、时间花在哪、哪一步不对劲。

    uv run python bench/trace_view.py                    # 最新一条(从 .traces/ 找)
    uv run python bench/trace_view.py <trace.json>       # 指定一条
    uv run python bench/trace_view.py <目录>             # 目录下最新一条
    uv run python bench/trace_view.py <trace.json> --full  # 不截断参数与结果

它解决的问题不是「发现异常」——那是 analyze.py 的活,它读 results.jsonl 就能报出
「这一跑不对劲,去看看」。缺口在【从发现到看懂】:被点名之后,你还得打开 agent.log
肉眼读几十次工具调用。本工具把那一步变成一屏。

📌 数据来源是主干 hook 落的结构化轨迹(.traces/*.json),【不解析日志文本】。
   🪝 从日志里反解结构,是在为「当初没记下来」还债。

设计参考 DeepSeek harness 的轨迹模式(2026-08-16 当事人给的参考):
   顶部一条时间带看整体形状 → 逐行看发生了什么 → 需要时再展开细节。
   分层的意义:一上来糊一脸 JSON 等于没有视图。
"""

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

# 颜色按【语义】分,不是按好看分:红色永远意味着「这里要看」。
C = {
    "dim": "\033[90m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "off": "\033[0m",
}

# 工具按职能分色:读=蓝、写=黄、执行=洋红、组织=青。
# 一屏扫下去,颜色的分布本身就是行为画像 —— 满屏黄色 = 一直在写,基本没读。
TOOL_COLOR = {
    "read_file": "blue", "glob": "blue", "grep": "blue", "load_skill": "blue",
    "write_file": "yellow", "edit_file": "yellow",
    "bash": "magenta",
    "todo_write": "cyan", "memory": "cyan", "compact": "cyan",
}
BAND = {"blue": "▄", "yellow": "█", "magenta": "▓", "cyan": "░"}


def paint(text, color):
    return f"{C.get(color, '')}{text}{C['off']}"


def _arg(key, value, true_len=None) -> str:
    """一个参数怎么显示才有信息量。

    ⚠️ 两类值直接原样打会把行淹掉,而且淹掉的正是【别的参数】:
        path     考场是临时目录,完整路径能占满一整行,有信息量的只有末尾那截
        content  write_file 的正文是整个文件,几千字符
    对后者报【长度】比报前 80 个字符有用得多 —— 「写了 1.2k」是个可比较的量,
    而一段被拦腰截断的代码既读不懂也没法比。
    """
    text = str(value)
    if key in ("path", "file_path") and "/" in text:
        return f"{key}={Path(text).name}"
    # 🔴 长度必须用【记录时的真实长度】,不能用手里这份(它已经被截断过了)。
    #    否则会安静地说假话:实际写了 3000 字符,显示成「500 字符」。
    n = true_len if true_len is not None else len(text)
    if n > 90:
        return f"{key}=<{n} 字符>"
    return f"{key}={text}"


def find_trace(arg: str | None) -> Path:
    """找一条轨迹。给目录就取里面最新的,什么都不给就从 .traces/ 取最新的。"""
    if arg:
        p = Path(arg)
        if p.is_file():
            return p
        if p.is_dir():
            files = sorted(p.rglob("trace_*.json"), key=lambda f: f.stat().st_mtime)
            if files:
                return files[-1]
            raise SystemExit(f"⛔ {p} 下面没有 trace_*.json")
        raise SystemExit(f"⛔ 找不到 {p}")
    for root in (Path.cwd() / ".traces", HERE.parent / ".traces"):
        files = sorted(root.glob("trace_*.json")) if root.is_dir() else []
        if files:
            return files[-1]
    raise SystemExit(
        "⛔ 没找到轨迹。跑一次 agent 会在工作区落 .traces/;"
        "\n   或者直接指定:trace_view.py <路径>"
    )


def pair_events(events: list[dict]) -> list[dict]:
    """把 tool_call 与它的 tool_result 配成一条。

    ⚠️ 【被权限挡下】的调用在主干里也会发 PostToolUse,所以正常情况下配得上。
       配不上的那种(有 call 没 result)恰恰是最该显眼的 —— 说明这一跑【没有正常结束】
       (超时被 kill / 崩了),那一条会标成 no-result 而不是被悄悄丢掉。
       🪝 数据对不齐的时候,把对不齐这件事显示出来,而不是抹平它。
    """
    rows, pending = [], None
    for e in events:
        if e["kind"] == "tool_call":
            if pending:
                rows.append({**pending, "ms": None, "ok": None, "result": "(无结果)"})
            pending = e
        elif e["kind"] == "tool_result":
            base = pending or {"tool": e["tool"], "args": {}}
            # t_call 单独留着 —— 合并时 result 的 t 会盖掉 call 的 t,
            # 而「这个工具是什么时候【开始】的」正是算思考时间要用的量。
            rows.append({**base, **e, "kind": "pair", "t_call": base.get("t")})
            pending = None
        else:
            rows.append(e)
    if pending:
        rows.append({**pending, "ms": None, "ok": None, "result": "(无结果)"})
    return rows


def add_thinking(rows: list[dict]) -> list[dict]:
    """把「模型在想」这段时间算出来,插进时间线。

    hook 只看得见工具的进和出,看不见 LLM 调用 —— 但【上一个工具结束到下一个工具开始】
    中间那段,harness 就是在等模型。每个事件都记了时间戳 t,一减就有,不用加任何探针。

    🪝 缺的量不一定要新加测点:先看现有的量之间能不能【减出来】。

    🔴 2026-08-16 修:开头那段【原来漏了】。prev_end 初值是 None,于是
       「用户提问 → 第一个工具调用」整段被跳过 —— 实测一条普通对话漏掉 8.8s / 37.1s,
       接近四分之一,而视图还报得像是算全了。
       更糟的是这个函数的注释当时写着「开头那段同样算进去」——【注释说了,代码没做】。
       🪝 注释写了 ≠ 代码做了,而注释比代码更容易被人当成事实
          (同族:「规则写在 prompt 里 ≠ 规则被执行」)。
       现在拿 user 事件的时间戳当起点。没有 user 事件时(比如从中间截取的轨迹)
       仍从第一个工具开始算 —— 拿不到就不编。

    ⚠️ 仍未计入的一段:最后一个工具结束 → 模型给出最终回答。
       hook 里没有「本轮结束」的时刻(Stop 是整个会话结束),这段拿不到,
       所以【报出来的思考时间是下界,不是全部】。宁可报少并说明,不要凑一个数。
    """
    out, prev_end = [], None
    for r in rows:
        if r.get("kind") == "user":
            prev_end = r.get("t", prev_end)  # 开头那段的起点
        elif r.get("kind") == "pair":
            start = r.get("t_call")
            if prev_end is not None and start is not None and start > prev_end:
                out.append({"kind": "think", "ms": round((start - prev_end) * 1000)})
            prev_end = r.get("t", prev_end)
        out.append(r)
    return out


def timeline(rows: list[dict], width: int = 72) -> str:
    """顶部时间带:每段按【耗时】占宽,颜色按类型。

    看的是形状不是数值 —— 哪一段特别宽 = 时间黑洞;某种颜色连成一片 = 卡在一种动作上。
    ⚠️ 拿不到耗时(老轨迹/没结果)时退化成等宽 —— 宁可等宽,不要凭空编一个时长。
    """
    segs = [r for r in rows if r.get("kind") in ("pair", "think")]
    if not segs:
        return "(无工具调用)"
    total = sum(r.get("ms") or 0 for r in segs)
    out = []
    for r in segs:
        ms = r.get("ms") or 0
        if r["kind"] == "think":
            n = max(1, round(width * ms / total)) if total else 1
            out.append(paint("·" * n, "dim"))
            continue
        color = TOOL_COLOR.get(r["tool"], "dim")
        n = max(1, round(width * ms / total)) if total else max(1, width // len(segs))
        ch = "!" if r.get("blocked") else ("×" if r.get("ok") is False else BAND.get(color, "─"))
        out.append(paint(ch * n, "red" if (r.get("blocked") or r.get("ok") is False) else color))
    return "".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv
    path = find_trace(args[0] if args else None)
    t = json.loads(path.read_text(encoding="utf-8"))
    rows = add_thinking(pair_events(t["events"]))
    pairs = [r for r in rows if r.get("kind") == "pair"]
    think_ms = sum(r["ms"] for r in rows if r.get("kind") == "think")

    # ── 出身:这条轨迹是谁、拿什么跑的 ──
    # 🪝 一份数据离开当时的对话之后,还能不能说清自己是怎么产生的?
    print(f"\n{C['bold']}{path.name}{C['off']}")
    prov = "  ".join(
        f"{k}={paint(t.get(k) or '?', 'cyan')}"
        for k in ("harness", "model", "memory_mode", "todo_mode", "sandbox_mode")
    )
    print(f"  {prov}\n")

    # ── 时间带 ──
    total_ms = sum(r.get("ms") or 0 for r in pairs)
    wall = total_ms + think_ms
    print(f"  {timeline(rows)}")
    # 🔴 两个时间要分开报:工具跑了多久 vs 模型想了多久。
    #    合成一个「总耗时」就再也看不出「这一跑到底慢在哪」—— 而这正是要看的东西。
    print(
        paint(f"  {len(pairs)} 次工具调用", "dim")
        + paint(f"   工具 {total_ms / 1000:.1f}s", "magenta")
        + paint(f" ({total_ms / wall * 100 if wall else 0:.0f}%)", "dim")
        + paint(f"   模型思考 {think_ms / 1000:.1f}s", "dim")
        + paint(f" ({think_ms / wall * 100 if wall else 0:.0f}%)", "dim")
    )
    legend = "  ".join(
        paint(f"{BAND[c]} {n}", c)
        for c, n in (("blue", "读"), ("yellow", "写"), ("magenta", "执行"), ("cyan", "组织"))
    )
    print(
        paint("  图例: ", "dim") + legend + paint("  · 思考", "dim")
        + paint("   × 出错   ! 被拦", "red") + "\n"
    )

    # ── 逐行 ──
    limit = 10**9 if full else 200
    step = 0
    for r in rows:
        if r.get("kind") == "user":
            print(f"  {paint('USER ', 'bold')} {r['text'][:limit]}\n")
            continue
        if r.get("kind") == "think":
            # 思考时间只在【值得注意】时才占一行 —— 每一步都印会把工具淹掉
            if r["ms"] >= 1000:
                print(paint(f"      ⋯ 模型思考 {r['ms'] / 1000:.1f}s", "dim"))
            continue
        step += 1
        i = step
        color = TOOL_COLOR.get(r["tool"], "dim")
        ms = f"{r['ms']:>5}ms" if r.get("ms") is not None else "    —"
        if r.get("blocked"):
            mark = paint("!", "red")
        elif r.get("ok") is False:
            mark = paint("×", "red")
        elif r.get("ok") is None:
            mark = paint("?", "yellow")
        else:
            mark = paint("·", "dim")
        lens = r.get("arg_lens", {})
        arg_preview = ", ".join(
            _arg(k, v, lens.get(k)) for k, v in list(r.get("args", {}).items())[:2]
        )
        r["_step"] = i  # 底部「要看的」要用同一套编号,不能用含思考事件的原始下标
        print(f"  {i:>3} {mark} {paint(r['tool'][:14].ljust(14), color)} {paint(ms, 'dim')}  "
              f"{arg_preview[:limit if full else 78]}")
        if full or r.get("blocked") or r.get("ok") is False:
            body = str(r.get("result", ""))[:limit if full else 300].replace("\n", "\n        ")
            print(paint(f"        → {body}", "dim"))

    # ── 汇总:行为画像 ──
    print(f"\n  {C['bold']}这一跑的形状{C['off']}")
    counts = Counter(r["tool"] for r in pairs)
    spent = Counter()
    for r in pairs:
        spent[r["tool"]] += r.get("ms") or 0
    for tool, n in counts.most_common():
        share = spent[tool] / total_ms * 100 if total_ms else 0
        bar = "█" * max(1, round(share / 4))
        print(f"    {paint(tool[:14].ljust(14), TOOL_COLOR.get(tool, 'dim'))} "
              f"{n:>3} 次  {spent[tool] / 1000:>6.1f}s {paint(bar, 'dim')} {share:.0f}%")

    bad = [r for r in pairs if r.get("ok") is False or r.get("blocked")]
    if bad:
        print(f"\n  {paint('要看的:', 'red')}")
        for r in bad:
            why = "被拦" if r.get("blocked") else "出错"
            print(f"    #{r.get('_step', '?'):<3} {r['tool']:<14} {why}  "
                  f"{str(r.get('result', ''))[:60]}")
    else:
        print(paint("\n  没有出错或被拦的调用。", "dim"))
    print()


if __name__ == "__main__":
    main()
