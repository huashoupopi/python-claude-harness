"""t17 判分必须认违规类型，不能只认英文单词或变量名。

2026-08-21 实跑：中文写对的 AUDIT.md 被 "default" / "is_live" / "float" 误杀。
"""

import subprocess
import tempfile
from pathlib import Path

GRADE = (
    Path(__file__).resolve().parent.parent
    / "bench"
    / "tasks"
    / "t17_divide_audit"
    / "grade.sh"
)

# 实跑 off t2 原文（被旧尺子误杀、内容是对的）
CHINESE_LIVE = """\
pkg_alpha: 使用 eval 执行表达式字符串
pkg_beta: 可变对象 list 作为函数默认参数
pkg_gamma: 用 is 比较字符串内容
pkg_delta: 用 == 比较浮点计算结果
pkg_epsilon: 使用裸 except:
pkg_zeta: 用 items or [] 对假值不友好的默认
"""

# 实跑 on t1 原文（同样被 "default" 误杀）
CHINESE_ON_T1 = """\
pkg_alpha: 使用 eval 直接执行表达式字符串，存在任意代码执行风险
pkg_beta: add_tag 使用可变列表作为默认参数，多次调用会共享并污染同一列表
pkg_gamma: 使用 is 比较字符串内容而非比较值
pkg_delta: 对浮点计算结果使用 == 直接比较
pkg_epsilon: 使用裸 except 吞掉所有异常且未限定异常类型
pkg_zeta: 使用 opts or {} 作为默认值，会把空字典等假值误判为未传参
"""


def _grade(audit: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        Path(td, "AUDIT.md").write_text(audit, encoding="utf-8")
        return subprocess.run(
            ["bash", str(GRADE), td], capture_output=True, text=True, timeout=30
        )


def test_chinese_live_report_passes():
    r = _grade(CHINESE_LIVE)
    assert r.returncode == 0, r.stdout + r.stderr


def test_chinese_on_t1_report_passes():
    r = _grade(CHINESE_ON_T1)
    assert r.returncode == 0, r.stdout + r.stderr


def test_english_solution_still_passes():
    sol = (
        Path(__file__).resolve().parent.parent
        / "bench"
        / "tasks"
        / "t17_divide_audit"
        / "solution"
        / "AUDIT.md"
    )
    r = _grade(sol.read_text(encoding="utf-8"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_type_still_fails():
    r = _grade(
        "pkg_alpha: eval\n"
        "pkg_beta: looks fine\n"
        "pkg_gamma: 用 is 比较字符串内容\n"
        "pkg_delta: 浮点\n"
        "pkg_epsilon: except\n"
        "pkg_zeta: or []\n"
    )
    assert r.returncode != 0
    assert "pkg_beta" in (r.stdout + r.stderr)


def test_no_audit_file_fails():
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            ["bash", str(GRADE), td], capture_output=True, text=True, timeout=30
        )
    assert r.returncode != 0
