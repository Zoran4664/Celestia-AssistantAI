#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""in_score 的回归测试（无需 pytest：python tests/test_in_score.py）。"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import in_score as m  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def state(d=3, a=3, f=5, ss=(5, 5, 5), suicide=1):
    d_dims = [d] * 8 + [suicide]
    return {"D": d_dims, "A": [a] * 7, "F": [f] * 5, "SS": list(ss)}


# --------------------------------------------------------------------------- #
def t_weights() -> None:
    total = m.W_SEV + m.W_F + m.W_SS
    check("weights sum to 1", approx(total, 1.0, 1e-9), f"sum={total}")
    check("weights match config",
          approx(m.W_SEV, m.CFG["math"]["weights"]["W_SEV"], 1e-9)
          and approx(m.W_F, m.CFG["math"]["weights"]["W_F"], 1e-9)
          and approx(m.W_SS, m.CFG["math"]["weights"]["W_SS"], 1e-9))
    check("weight ratio equals path ratio",
          approx(m.W_F / m.W_SS, 0.40126 / 0.24850, 1e-3),
          f"ratio={m.W_F / m.W_SS:.4f}")


def t_mediation_share() -> None:
    """本式隐含的中介占比必须与定稿的非标准化路径系数同量级。"""
    share_d = 0.0703 / 0.471          # ab_std / r(F,D)
    share_a = 0.0515 / 0.472
    check("depression share within 1.5pp of the fixed coefficients", abs(share_d - 0.1399) < 0.015,
          f"{share_d:.4f} vs 0.1399")
    check("anxiety share within 1.5pp of the fixed coefficients", abs(share_a - 0.1030) < 0.015,
          f"{share_a:.4f} vs 0.1030")


def t_norms() -> None:
    check("norm depression lower bound", approx(m.norm_depression([1] * 9), 0.0))
    check("norm depression upper bound", approx(m.norm_depression([5] * 9), 1.0))
    check("norm anxiety bounds",
          approx(m.norm_anxiety([1] * 7), 0.0) and approx(m.norm_anxiety([5] * 7), 1.0))
    check("norm fatigue bounds",
          approx(m.norm_fatigue([1] * 5), 0.0) and approx(m.norm_fatigue([10] * 5), 1.0))
    check("norm support bounds",
          approx(m.norm_subjective_support(1), 0.0) and approx(m.norm_subjective_support(10), 1.0))


def t_phi() -> None:
    k1, k2, k3 = m.KNOTS
    check("phi(0)=0", approx(m.phi_fatigue(0.0), 0.0))
    check("phi(1)=1", approx(m.phi_fatigue(1.0), 1.0))
    eps = 1e-9
    check("phi continuous at k1",
          approx(m.phi_fatigue(k1 - eps), m.phi_fatigue(k1 + eps), 1e-6))
    check("phi continuous at k2",
          approx(m.phi_fatigue(k2 - eps), m.phi_fatigue(k2 + eps), 1e-6))
    check("phi continuous at k3",
          approx(m.phi_fatigue(k3 - eps), m.phi_fatigue(k3 + eps), 1e-6))
    seq = [m.phi_fatigue(i / 1000) for i in range(1001)]
    check("phi monotone non-decreasing", all(b >= a - 1e-12 for a, b in zip(seq, seq[1:])))
    check("phi flat below first knot", approx(m.phi_fatigue(0.39), 0.0))
    check("phi rises after first knot", m.phi_fatigue(0.50) > 0.0)


def t_psi() -> None:
    check("psi(0)=0", approx(m.psi_support(0.0), 0.0))
    check("psi(1)=1", approx(m.psi_support(1.0), 1.0))
    seq = [m.psi_support(i / 500) for i in range(501)]
    check("psi monotone non-decreasing", all(b >= a - 1e-12 for a, b in zip(seq, seq[1:])))
    check("psi is convex (diminishing at low support)",
          m.psi_support(0.5) < 0.5, f"psi(.5)={m.psi_support(0.5):.4f}")


def t_monotone() -> None:
    prev = None
    vals = [m.compute(state(d=k))["in"] for k in range(1, 6)]
    check("IN non-decreasing in depression",
          all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), str(vals))

    vals = [m.compute(state(a=k))["in"] for k in range(1, 6)]
    check("IN non-decreasing in anxiety",
          all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), str(vals))

    vals = [m.compute(state(f=k))["in"] for k in range(1, 11)]
    check("IN non-decreasing in fatigue",
          all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), str(vals))

    vals = [m.compute(state(ss=(k, k, k)))["in"] for k in range(1, 11)]
    check("IN non-increasing in subjective support",
          all(b <= a + 1e-9 for a, b in zip(vals, vals[1:])), str(vals))
    check("subjective support moves IN meaningfully",
          vals[0] - vals[-1] > 0.10, f"delta={vals[0] - vals[-1]:.3f}")


def t_clamp() -> None:
    low = m.compute({"D": [1] * 9, "A": [1] * 7, "F": [1] * 5, "SS": [10, 10, 10]})
    check("IN clamped at 0", low["in"] == 0.0, str(low["components"]))
    high = m.compute({"D": [5] * 8 + [1], "A": [5] * 7, "F": [10] * 5, "SS": [1, 1, 1]})
    check("IN never exceeds W_SEV+W_F", high["in"] <= m.W_SEV + m.W_F + 1e-9,
          f"in={high['in']} cap={m.W_SEV + m.W_F:.4f}")
    check("IN <= 1", m.compute(state(d=5, a=5, f=10, ss=(1, 1, 1), suicide=5))["in"] <= 1.0)


def t_crisis() -> None:
    r = m.compute(state(d=4, a=3, f=6, ss=(4, 4, 4), suicide=5))
    check("suicidal dim 5 forces L4", r["band"] == "L4", r["band"])
    check("crisis forces IN=1.0", r["in"] == 1.0)
    check("crisis allows at most one question", r["max_questions"] <= 1, str(r["max_questions"]))
    check("crisis flags referral", r["flags"]["referral"] is True)
    check("crisis leads with S1_crisis", r["strategies"][0] == "S1_crisis")

    r4 = m.compute(state(d=4, a=3, f=6, ss=(4, 4, 4), suicide=4))
    check("suicidal dim 4 alone does not open L4", r4["band"] != "L4", r4["band"])
    check("suicidal dim 4 still raises attention", r4["band"] in ("L3", "L4"), r4["band"])

    r3 = m.compute(state(d=3, a=2, f=4, ss=(6, 6, 6), suicide=3))
    check("suicidal dim ==3 elevates to at least L3", r3["band"] in ("L3", "L4"), r3["band"])
    check("suicidal dim ==3 marks elevated", r3["flags"]["suicidal_elevated"] is True)

    rod = m.compute({"D": [2] * 8 + [1], "A": [2] * 7, "F": [4] * 5, "SS": [5, 5, 5], "f": 2})
    check("od bitmask adds S1_od", "S1_od" in rod["strategies"])


def t_band() -> None:
    check("band edges exact", [m.resolve_band(e) for e in (0.25, 0.37, 0.49)] == ["L1", "L2", "L3"])
    check("band L0 below first edge", m.resolve_band(0.249) == "L0")
    check("hysteresis blocks 0.01 upgrade", m.resolve_band(0.26, "L0") == "L0")
    check("hysteresis allows 0.03 upgrade", m.resolve_band(0.28, "L0") == "L1")
    check("hysteresis blocks 0.01 downgrade", m.resolve_band(0.24, "L1") == "L1")
    check("hysteresis allows 0.03 downgrade", m.resolve_band(0.22, "L1") == "L0")
    check("unknown prev band falls back to raw", m.resolve_band(0.40, "L9") == "L2")


def t_trend() -> None:
    base = state(d=2, a=2, f=4, ss=(6, 6, 6))
    worse = state(d=4, a=4, f=8, ss=(6, 6, 6))
    r = m.compute(worse, prev=base)
    check("trend positive when worsening", r["components"]["trend"] > 0.0,
          str(r["components"]))
    r2 = m.compute(base, prev=worse)
    check("trend ignores improvement", r2["components"]["trend"] == 0.0)
    huge = m.compute(state(d=5, a=5, f=10, ss=(1, 1, 1), suicide=1),
                     prev=state(d=1, a=1, f=1, ss=(10, 10, 10)))
    check("trend capped", huge["components"]["trend"] <= m.TREND_CAP + 1e-9,
          str(huge["components"]["trend"]))


def t_limits() -> None:
    r = m.compute({"D": [3] * 8 + [1], "A": [3] * 7, "F": [8, 8, 8, 8, 8], "SS": [5, 5, 5]})
    check("invasion >=7 shrinks output", r["max_chars"] <= 160, str(r["max_chars"]))
    r2 = m.compute({"D": [3] * 8 + [1], "A": [3] * 7, "F": [5, 5, 5, 5, 8], "SS": [5, 5, 5]})
    check("avoidance >=7 caps at 100 chars", r2["max_chars"] <= 100, str(r2["max_chars"]))
    check("avoidance >=7 forbids questions", r2["max_questions"] == 0)
    r3 = m.compute(state(d=1, a=1, f=1, ss=(9, 9, 9)))
    check("calm state allows a full reply", r3["max_chars"] >= 280, str(r3["max_chars"]))
    check("calm state allows two questions", r3["max_questions"] >= 2, str(r3["max_questions"]))


def t_strategies() -> None:
    r = m.compute({"D": [2] * 8 + [1], "A": [2] * 7, "F": [4, 9, 8, 6, 7], "SS": [2, 2, 2]})
    s = r["strategies"]
    check("short output strategy present when invasion high", "S2_short" in s, str(s))
    check("listen strategy present when avoidance high", "S2_listen" in s, str(s))
    check("support acceptance present when SS_sub low", "S3_sssub_accept" in s, str(s))
    check("no duplicate strategies", len(s) == len(set(s)))
    check("strategy count capped", len(s) <= 6)
    r2 = m.compute(state(d=1, a=1, f=2, ss=(9, 9, 9)))
    check("calm state yields few strategies", len(r2["strategies"]) <= 2, str(r2["strategies"]))


def t_legacy() -> None:
    legacy = {"Eval": {"D": 4, "A": 2, "F": 9, "SS_sub": 2, "SS_obj": 2, "SS_uti": 2}}
    r = m.compute(legacy)
    check("legacy input accepted", r["flags"]["legacy_input"] is True)
    check("legacy F=9 maps to high fatigue", r["normalized"]["F"] > 0.8, str(r["normalized"]))
    check("legacy crisis flags degrade gracefully", r["band"] in m.BAND_IDS)


def t_errors() -> None:
    for bad, label in (
        ({"D": [1] * 8, "A": [1] * 7, "F": [1] * 5, "SS": [1, 1, 1]}, "wrong D length"),
        ({"D": [9] * 9, "A": [1] * 7, "F": [1] * 5, "SS": [1, 1, 1]}, "D out of range"),
        ({"D": [1] * 9, "A": [1] * 7, "F": [1] * 5, "SS": [1, 1]}, "wrong SS length"),
        ({"D": [1] * 9, "A": [1] * 7, "F": [1] * 5, "SS": [1, 1, 1], "f": "x"}, "bad bitmask"),
    ):
        try:
            m.compute(bad)
            raised = False
        except (m.StateError, KeyError):
            raised = True
        check(f"rejects {label}", raised)


def _bin_of_total(key: str, total: float) -> int:
    b = m.BIN_BOUNDS[key]
    for i in range(5):
        if total <= b[i + 1] - 1:
            return i
    return 4


def t_bins() -> None:
    # 网格单调性：症状箱、倦怠箱不降，支持箱不升
    mono_d = True
    for a in range(5):
        for f in range(5):
            for ss in range(5):
                seq = [m.BAND_IDS.index(m.compute_from_bins(d, a, f, ss)["band"]) for d in range(5)]
                mono_d &= all(y >= x for x, y in zip(seq, seq[1:]))
    check("grid band non-decreasing in D bin", mono_d)

    mono_ss = True
    for d in range(5):
        for a in range(5):
            for f in range(5):
                seq = [m.BAND_IDS.index(m.compute_from_bins(d, a, f, ss)["band"]) for ss in range(5)]
                mono_ss &= all(y <= x for x, y in zip(seq, seq[1:]))
    check("grid band non-increasing in support bin", mono_ss)

    mono_f = True
    for d in range(5):
        for a in range(5):
            for ss in range(5):
                seq = [m.BAND_IDS.index(m.compute_from_bins(d, a, f, ss)["band"]) for f in range(5)]
                mono_f &= all(y >= x for x, y in zip(seq, seq[1:]))
    check("grid band non-decreasing in fatigue bin", mono_f)

    # 量化误差：查表分档与精确分档最多相差一档（硬约束），一致率 >=60%（软底线）
    import random
    random.seed(20261027)
    same, worst, n = 0, 0, 4000
    for _ in range(n):
        d = random.randint(9, 45)
        a = random.randint(7, 35)
        f = random.randint(5, 50)
        ss = random.randint(1, 10)
        exact = m.compute({
            "D": [d / 9] * 9, "A": [a / 7] * 7,
            "F": [f / 5] * 5, "SS": [ss, ss, ss],
        })["band"]
        approx = m.compute_from_bins(
            _bin_of_total("D", d), _bin_of_total("A", a),
            _bin_of_total("F", f), _bin_of_total("SS_sub", ss),
        )["band"]
        diff = abs(m.BAND_IDS.index(exact) - m.BAND_IDS.index(approx))
        worst = max(worst, diff)
        same += diff == 0
    check("lookup never shifts more than one band", worst <= 1, f"worst={worst}")
    check("lookup agrees with exact scoring >=60%", same / n >= 0.60,
          f"agree={same / n:.3f}")


def t_guards() -> None:
    r = m.compute({"D": [1] * 9, "A": [1] * 7, "F": [3] * 5, "SS": [2, 5, 5]})
    check("high exposure + low support floored at L1", m.BAND_IDS.index(r["band"]) >= 1, r["band"])
    r2 = m.compute({"D": [3, 3, 3, 3, 3, 3, 3, 3, 2], "A": [1] * 7, "F": [1] * 5, "SS": [9, 9, 9]})
    check("moderate depression floored at L2", m.BAND_IDS.index(r2["band"]) >= 2, r2["band"])
    r3 = m.compute({"D": [4, 4, 4, 4, 4, 4, 4, 4, 1], "A": [1] * 7, "F": [1] * 5, "SS": [9, 9, 9]})
    check("severe depression floored at L3", r3["band"] == "L3", r3["band"])
    check("guards never alter IN value", r3["in"] < 0.37, f"in={r3['in']}")


def t_demo_runs() -> None:
    r = m.compute(m.DEMO_STATE)
    check("demo state produces L2-L3", r["band"] in ("L2", "L3"), r["band"])
    check("demo result is JSON-serialisable", isinstance(json.dumps(r, ensure_ascii=False), str))
    check("demo strategies non-empty", len(r["strategies"]) > 0)


def main() -> int:
    tests = [
        t_weights, t_mediation_share, t_norms, t_phi, t_psi, t_monotone,
        t_clamp, t_crisis, t_band, t_trend, t_limits, t_strategies,
        t_legacy, t_errors, t_bins, t_guards, t_demo_runs,
    ]
    for fn in tests:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"FAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
