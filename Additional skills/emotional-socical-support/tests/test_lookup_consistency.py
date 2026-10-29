#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 assets/in_lookup.csv 与参考实现逐行一致，并检查网格单调性。"""

from __future__ import annotations

import csv
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


def main() -> int:
    path = ROOT / "assets" / "in_lookup.csv"
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    check("table has 625 rows (5^4)", len(rows) == 625, str(len(rows)))
    check("header count", len(rows[0]) == 18, str(len(rows[0])))

    mismatch_in, mismatch_band, guard_violation = 0, 0, 0
    grid = {}
    for r in rows:
        d, a, f, ss = int(r["d_bin"]), int(r["a_bin"]), int(r["f_bin"]), int(r["ss_bin"])
        ref = m.compute_from_bins(d, a, f, ss)
        if abs(float(r["in"]) - ref["in"]) > 1e-9:
            mismatch_in += 1
        if r["band_final"] != ref["band"]:
            mismatch_band += 1
        if m.BAND_IDS.index(r["band_final"]) < m.BAND_IDS.index(r["band_score"]):
            guard_violation += 1
        grid[(d, a, f, ss)] = m.BAND_IDS.index(r["band_final"])

    check("every row matches reference implementation", mismatch_in == 0, f"{mismatch_in} rows")
    check("every band matches reference implementation", mismatch_band == 0, f"{mismatch_band} rows")
    check("guards only ever raise the band", guard_violation == 0, f"{guard_violation} rows")

    mono_d = all(grid[(d + 1, a, f, ss)] >= grid[(d, a, f, ss)]
                 for d in range(4) for a in range(5) for f in range(5) for ss in range(5))
    check("non-decreasing in D bin", mono_d)
    mono_f = all(grid[(d, a, f + 1, ss)] >= grid[(d, a, f, ss)]
                 for d in range(5) for a in range(5) for f in range(4) for ss in range(5))
    check("non-decreasing in F bin", mono_f)
    mono_ss = all(grid[(d, a, f, ss + 1)] <= grid[(d, a, f, ss)]
                  for d in range(5) for a in range(5) for f in range(5) for ss in range(4))
    check("non-increasing in support bin", mono_ss)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"FAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
