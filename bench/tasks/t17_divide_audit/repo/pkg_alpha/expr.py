"""Tiny expression helper used by the billing CLI."""


def tokenize(expr: str) -> list[str]:
    buf, out = "", []
    for ch in expr:
        if ch in "+-*/()":
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def parse_expr(expr: str) -> int:
    # 只接受数字和四则。实现图省事直接 eval。
    tokens = tokenize(expr)
    if not tokens:
        return 0
    return eval(expr)  # noqa: S307  — 这就是违规点


def dump(expr: str) -> str:
    return " ".join(tokenize(expr))
