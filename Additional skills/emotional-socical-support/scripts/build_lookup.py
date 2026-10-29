#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 assets/in_lookup.csv 与参考文档里的速查表。

用法：
    python build_lookup.py                # 写 CSV
    python build_lookup.py --markdown     # 打印可直接粘进 references/03-quick-lookup.md 的表格
    python build_lookup.py --stats        # 打印状态空间统计（用于说明分档为何这样切）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import in_score as m  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "assets" / "in_lookup.csv"

HEADER = [
    "d_bin", "a_bin", "f_bin", "ss_bin",
    "D_total", "A_total", "F_total", "SS_sub",
    "D_range", "A_range", "F_range", "SS_range",
    "s_hat", "phi_f", "psi_ss", "in",
    "band_score", "band_final",
]


def row(d: int, a: int, f: int, ss: int) -> list:
    r = m.compute_from_bins(d, a, f, ss)
    return [
        d, a, f, ss,
        m.bin_total_mid("D", d), m.bin_total_mid("A", a),
        m.bin_total_mid("F", f), m.bin_total_mid("SS_sub", ss),
        m.BIN_LABELS["D"][d], m.BIN_LABELS["A"][a],
        m.BIN_LABELS["F"][f], m.BIN_LABELS["SS_sub"][ss],
        r["s_hat"], r["phi"], r["psi"], r["in"],
        m.BAND_IDS[m.band_index(r["in"])], r["band"],
    ]


def write_csv() -> int:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = [row(d, a, f, ss) for d in range(5) for a in range(5)
            for f in range(5) for ss in range(5)]
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {CSV_PATH}")
    return 0


def _s_hat_bin(s_hat: float) -> int:
    return min(4, int(s_hat * 5.0))


def _s_bin_from_da(d_bin: int, a_bin: int) -> int:
    r = m.compute_from_bins(d_bin, a_bin, 2, 2)
    return _s_hat_bin(r["s_hat"])


def _s_hat_bin_text() -> str:
    lines = []
    for d in range(5):
        cells = [f"S{_s_bin_from_da(d, a)}" for a in range(5)]
        lines.append("| D" + str(d) + " | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _cell_band(f: int, s: int, ss: int) -> str:
    """纯分数档（不含护栏），按症状档中心值计算。"""
    s_hat = 0.1 + 0.2 * s
    f_hat = (m.bin_total_mid("F", f) - 5) / 45.0
    ss_hat = max(0.0, (m.bin_total_mid("SS_sub", ss) - 1) / 9.0)
    raw = (m.W_SEV * s_hat + m.W_F * m.phi_fatigue(f_hat) - m.W_SS * m.psi_support(ss_hat))
    return m.BAND_IDS[m.band_index(min(max(raw, 0.0), 1.0))]


def print_markdown() -> int:
    print("### 第一步：(ΣD 档, ΣA 档) → 症状档 S\n")
    print("| ΣD \\ ΣA | A0 | A1 | A2 | A3 | A4 |")
    print("|---|---|---|---|---|---|")
    print(_s_hat_bin_text())
    print()
    prev = None
    for f in range(5):
        rows = [_cell_band(f, s, ss) for s in range(5) for ss in range(5)]
        if rows == prev:
            print(f"### 第二步 · 倦怠档 F{f}（ΣF {m.BIN_LABELS['F'][f]}）\n")
            print("> 与上一档完全相同（Φ 仍为 0），直接沿用。\n")
            continue
        prev = rows
        print(f"### 第二步 · 倦怠档 F{f}（ΣF {m.BIN_LABELS['F'][f]}）\n")
        print("| S \\ SS | SS0 | SS1 | SS2 | SS3 | SS4 |")
        print("|---|---|---|---|---|---|")
        for s in range(5):
            cells = [_cell_band(f, s, ss) for ss in range(5)]
            print(f"| S{s} | " + " | ".join(cells) + " |")
        print()
    return 0


def print_stats() -> int:
    vals = [m.compute_from_bins(d, a, f, ss)["in"]
            for d in range(5) for a in range(5) for f in range(5) for ss in range(5)]
    vals.sort()
    n = len(vals)

    def q(p: float) -> float:
        return round(vals[int(p * (n - 1))], 4)

    print(json.dumps({
        "cells": n,
        "min": vals[0],
        "max": vals[-1],
        "median": q(0.5),
        "quantiles": {"25%": q(0.25), "37%": q(0.37), "63%": q(0.63),
                      "75%": q(0.75), "87%": q(0.87)},
        "band_edges": list(m.BANDS),
        "share_below_edge": [round(sum(v < e for v in vals) / n, 3) for e in m.BANDS],
        "reachable_max": round(m.W_SEV + m.W_F, 4),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.markdown:
        return print_markdown()
    if args.stats:
        return print_stats()
    return write_csv()


if __name__ == "__main__":
    raise SystemExit(main())
