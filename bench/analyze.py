"""读 results.jsonl，出归因用的四块表。

    uv run python bench/analyze.py [runs/run_YYYYmmdd_HHMMSS/results.jsonl]
    不给路径就取 runs/ 下最新的一次。

【口径继承自 stage-1，故意不重新发明】—— 两阶段的数字要能对话:
  噪声底线   同配置内部 min-max 波动。stage-1 实测 0-5 步,上界 5
             → 「配置间小于 5 步的差异,在 n=3 下不可分辨」
  配对比较   同题同 trial 跨臂比,数「谁更低」+ 符号检验 p = (1/2)^n

🔴 为什么两种算法都要报:stage-1 最值钱的方法论产出,正是【同一批数据、
   两种算法给出相反结论】—— 比均值说「测不出」(差 4.7 < 噪声 5),
   配对说「有差别」(9/9 同向, p≈0.002)。只报一种,那个产出就废了。
   🪝 配对设计 = 把已知的干扰源(题目难度)当区组消掉,而不是让它变成噪声。

⚠️ 一条要说出口的口径限制:配对单位是 (题, trial),但同一题的 3 个 trial
   【并不独立】(题目难度相同),这让符号检验的 p 偏乐观。沿用是为了跟 stage-1 可比,
   但不能假装它不存在 —— 结论里要带着这句话讲。
"""

import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).parent.resolve()

# 两个消融轴。同轴内部才配对比较 —— 跨轴比没有意义(动了两个变量)
AXES = {
    "memory 轴": ["mem_none", "mem_self", "mem_official"],
    "todo 轴": ["todo_none", "todo_tool", "mem_self"],  # mem_self 即 todo_nudge,两轴共用原点
}


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def fmt(x, w=7, p=1):
    return f"{x:>{w}.{p}f}"


def block_noise(rows):
    """① 噪声底线:同一个(臂,题)内部 3 次 trial 的波动。这是尺子的分辨率。"""
    print("\n" + "=" * 78)
    print("① 噪声底线 —— 同配置同题内部的波动(这是本次数据的分辨率下限)")
    print("=" * 78)
    spans = []
    for metric in ("steps", "tokens_total"):
        worst = []
        for (cfg, task), g in group(rows, lambda r: (r["config"], r["task"])).items():
            vals = [r[metric] for r in g]
            if len(vals) > 1:
                worst.append((max(vals) - min(vals), cfg, task, vals))
        worst.sort(reverse=True)
        if not worst:
            continue
        span = worst[0][0]
        spans.append((metric, span))
        print(f"\n  {metric}: 最大内部波动 {span}   (最抖的三组)")
        for s, cfg, task, vals in worst[:3]:
            print(f"      {cfg:14s} {task:22s} {vals}  波动 {s}")
    print("\n  📏 判读:配置之间【小于上述波动】的差异,在 n=3 下不可分辨。")
    return dict(spans)


def group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def block_summary(rows):
    """② 每臂汇总。注意 token 分两本账 —— 合起来就再也拆不开了。"""
    print("\n" + "=" * 78)
    print("② 每臂汇总")
    print("=" * 78)
    hdr = (
        f"  {'配置':14s}{'全过':>7s}{'通过率':>8s}{'steps':>8s}"
        f"{'主token':>9s}{'附加层':>8s}{'附加次':>7s}{'改动行':>7s}{'催':>6s}{'todo':>6s}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for cfg, g in group(rows, lambda r: r["config"]).items():
        n = len(g)
        a = lambda k: sum(r[k] for r in g) / n  # noqa: E731
        print(
            f"  {cfg:14s}{sum(1 for r in g if r['success']):>4d}/{n:<2d}"
            f"{fmt(a('pass_rate'), 8, 2)}{fmt(a('steps'), 8)}"
            f"{fmt(a('tokens_loop') / 1000, 8)}k{fmt(a('tokens_aux') / 1000, 7, 2)}k"
            f"{fmt(a('aux_calls'), 7, 1)}"
            f"{fmt(a('lines_added') + a('lines_removed'), 7)}"
            f"{fmt(a('reminders'), 6)}{fmt(a('todo_calls'), 6)}"
        )


def block_paired(rows, metric="steps"):
    """③ 比均值 vs 配对比较 —— 两种算法都报,因为它们可能给出相反结论。"""
    print("\n" + "=" * 78)
    print(f"③ 配对比较({metric}) —— 同题同 trial,只剩「配置」一个变量在动")
    print("=" * 78)
    by_key = {(r["config"], r["task"], r["trial"]): r[metric] for r in rows}
    for axis, cfgs in AXES.items():
        print(f"\n  【{axis}】")
        for a, b in combinations(cfgs, 2):
            pairs = [
                (by_key[(a, t, i)], by_key[(b, t, i)])
                for (c, t, i) in by_key
                if c == a and (b, t, i) in by_key
            ]
            if not pairs:
                continue
            n = len(pairs)
            a_lower = sum(1 for x, y in pairs if x < y)
            b_lower = sum(1 for x, y in pairs if x > y)
            tie = n - a_lower - b_lower
            mean_a = sum(x for x, _ in pairs) / n
            mean_b = sum(y for _, y in pairs) / n
            k = max(a_lower, b_lower)
            # 单尾符号检验(忽略平局),与 stage-1 口径一致
            eff = a_lower + b_lower
            p = (0.5**eff) * sum(_c(eff, i) for i in range(k, eff + 1)) if eff else 1.0
            verdict = "有差别" if p < 0.05 else "测不出"
            print(
                f"    {a:14s} vs {b:14s}  均值 {mean_a:6.1f} vs {mean_b:6.1f}"
                f"(差 {abs(mean_a - mean_b):5.1f})   "
                f"配对 {a_lower}:{b_lower}(平 {tie})  p={p:.4f}  → {verdict}"
            )


def _c(n, k):
    from math import comb

    return comb(n, k)


def block_anomalies(rows):
    """④ 异常清单 —— 这些轨迹必须人看,不能只看汇总数字。"""
    print("\n" + "=" * 78)
    print("④ 需要人看一眼的轨迹")
    print("=" * 78)
    buckets = {
        "考生动过测试文件(成绩已按原卷判,但行为要看)": [r for r in rows if r["tests_tampered"]],
        "超时": [r for r in rows if r["timed_out"]],
        "pytest 收集就挂了(≠ 断言失败)": [r for r in rows if r.get("collect_error")],
        "零步数(一次工具都没调)": [r for r in rows if r["steps"] == 0],
        "改动异常大(>60 行)": [r for r in rows if r["lines_added"] + r["lines_removed"] > 60],
    }
    clean = True
    for label, rs in buckets.items():
        if not rs:
            continue
        clean = False
        print(f"\n  ⚠️ {label}: {len(rs)} 条")
        for r in rs[:6]:
            print(f"      {r['config']}/{r['task']}/t{r['trial']}  {r.get('tests_tampered') or ''}")
    if clean:
        print("\n  ✅ 无异常")
    print(
        "\n  🪝 ㉚ 的教训:success 会骗人、steps 会骗人,只有轨迹说实话。"
        "\n     汇总数字看着正常不代表没事 —— 空转 15 步那次 success=True。"
    )


def main():
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else max((HERE / "runs").glob("run_*/results.jsonl"), key=lambda p: p.stat().st_mtime)
    )
    rows = load(path)
    print(f"\n数据源: {path}   共 {len(rows)} 条记录")
    block_noise(rows)
    block_summary(rows)
    for metric in ("steps", "tokens_total"):
        block_paired(rows, metric)
    block_anomalies(rows)
    print(
        "\n" + "=" * 78
        + "\n⚠️ 口径限制(讲结论时要带上):配对单位是(题, trial),但同一题的 3 个 trial"
        "\n   并不独立(题目难度相同),符号检验的 p 因此偏乐观。沿用此口径是为了与"
        "\n   stage-1 可比,不是因为它无懈可击。"
        "\n⚠️ bench 测的是【一次性任务里各层的净成本】。记忆层在真实使用中跨会话累积,"
        "\n   这里的考场每次全新、.memory/ 是空的 —— 收益结构性地为零,别把话说成"
        "\n   「memory 没用」。"
    )


if __name__ == "__main__":
    main()
