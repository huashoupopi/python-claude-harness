"""P2 diagnostic_only 不得污染双头验和 pass_rate 分母。"""

import json
from pathlib import Path

from bench.analyze import load
from bench.taskmeta import is_diagnostic


def test_t21_and_t22_are_marked_diagnostic():
    root = Path(__file__).resolve().parent.parent / "bench" / "tasks"
    assert is_diagnostic(root / "t21_order_paradox")
    assert is_diagnostic(root / "t22_true_and_false")
    assert not is_diagnostic(root / "t01_fix_failing_test")


def test_analyze_load_drops_diagnostic_from_scored_rows(tmp_path, capsys):
    p = tmp_path / "results.jsonl"
    p.write_text(
        json.dumps(
            {
                "task": "t01_fix_failing_test",
                "success": True,
                "pass_rate": 1.0,
                "timed_out": False,
                "config": "mem_self",
                "trial": 1,
            }
        )
        + "\n"
        + json.dumps(
            {
                "task": "t21_order_paradox",
                "success": False,
                "pass_rate": 0.0,
                "timed_out": False,
                "diagnostic_only": True,
                "config": "mem_self",
                "trial": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load(p)
    assert [r["task"] for r in rows] == ["t01_fix_failing_test"]
    err = capsys.readouterr().out
    assert "diagnostic_only" in err
    assert "t21_order_paradox" in err
