#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IN_Score 参考实现。

把 24 维隐式评分压缩成干预迫切度 IN，并给出温暖度、篇幅上限、问句上限与策略组合。

三系数 = (1.0000, 0.40126, 0.24850) / 1.64976，分别对应症状本身、倦怠直接效应 c′、
主观支持保护路径 |b|。所有阈值、权重、拐点从 config.json 读取，脚本内不重复定义。

用法
----
    python in_score.py --demo
    python in_score.py --state '{"D":[3,4,2,3,4,3,2,3,1],"A":[3,3,2,2,3,2,1],"F":[5,7,8,4,6],"SS":[3,4,2]}'
    python in_score.py --state-file state.json --prev-band L2 --indent
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"

BAND_IDS = ("L0", "L1", "L2", "L3", "L4")

# 维度 → 策略标签（1-based 序号，与 dimensions.json 的 id 顺序一致）
DEP_STRATEGY = {
    1: "S5_dep_energy",
    2: "S5_dep_mood",
    3: "S5_dep_sleep",
    4: "S5_dep_fatigue",
    5: "S5_dep_appetite",
    6: "S5_dep_selfworth",
    7: "S5_dep_attention",
    8: "S5_dep_motor",
    9: "S5_dep_suicide",
}
ANX_STRATEGY = {
    1: "S5_anx_tension",
    2: "S5_anx_worry",
    3: "S5_anx_trivia",
    4: "S5_anx_relax",
    5: "S5_anx_restless",
    6: "S5_anx_irritable",
    7: "S5_anx_fear",
}
FAT_STRATEGY = {
    1: "S4_anxiety_feedback",
    2: "S4_lowvalue",
    3: "S4_invasion",
    4: "S4_escape",
    5: "S4_avoidance",
}


class StateError(ValueError):
    """输入状态不合法。"""


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _apply_config(cfg: Dict[str, Any]) -> None:
    """把配置写进模块级常量，保证『配置是唯一真源』。"""
    global W_SEV, W_F, W_SS, KNOTS, PHI_MID, PHI_KNEE, PSI_GAMMA
    global K_TREND_S, K_TREND_F, TREND_CAP, BANDS, HYST, SCALES, CFG

    math = cfg["math"]
    W_SEV = float(math["weights"]["W_SEV"])
    W_F = float(math["weights"]["W_F"])
    W_SS = float(math["weights"]["W_SS"])
    KNOTS = tuple(float(x) for x in math["phi"]["knots"])
    PHI_MID = float(math["phi"]["v1"])
    PHI_KNEE = float(math["phi"]["v2"])
    PSI_GAMMA = float(math["psi"]["gamma"])
    K_TREND_S = float(math["trend"]["k_s"])
    K_TREND_F = float(math["trend"]["k_f"])
    TREND_CAP = float(math["trend"]["cap"])
    BANDS = tuple(float(x) for x in math["bands"])
    HYST = float(math["hysteresis"])
    SCALES = cfg["scales"]
    CFG = cfg


CFG: Dict[str, Any] = {}
TOTALS = {k: (v["min_total"], v["max_total"]) for k, v in load_config()["scales"].items()}
_apply_config(load_config())


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
def _norm_total(total: float, key: str) -> float:
    lo, hi = TOTALS[key]
    if total < lo or total > hi:
        raise StateError(f"{key} 总分 {total} 越界，合法区间 [{lo}, {hi}]")
    return (total - lo) / (hi - lo)


def norm_depression(dims: Sequence[float]) -> float:
    return _norm_total(sum(dims), "D")


def norm_anxiety(dims: Sequence[float]) -> float:
    return _norm_total(sum(dims), "A")


def norm_fatigue(dims: Sequence[float]) -> float:
    return _norm_total(sum(dims), "F")


def norm_subjective_support(score: float) -> float:
    """主观社会支持 1-10，越高越好。"""
    lo, hi = 1.0, 10.0
    if score < lo or score > hi:
        raise StateError(f"主观社会支持 {score} 越界，合法区间 [{lo}, {hi}]")
    return (score - lo) / (hi - lo)


# --------------------------------------------------------------------------- #
# 非线性
# --------------------------------------------------------------------------- #
def phi_fatigue(x: float) -> float:
    """倦怠拐点函数，分段连续、单调不减。

    x ≤ 0.40            → 0
    0.40 < x ≤ 0.60     → 二次升到 0.45（第一拐点）
    0.60 < x ≤ 0.75     → 线性升到 0.55（平台段）
    0.75 < x ≤ 1.00     → 幂 1.5 升到 1.00（第二拐点）
    """
    k1, k2, k3 = KNOTS
    x = min(max(x, 0.0), 1.0)
    if x <= k1:
        return 0.0
    if x <= k2:
        return PHI_MID * ((x - k1) / (k2 - k1)) ** 2
    if x <= k3:
        return PHI_MID + (PHI_KNEE - PHI_MID) * ((x - k2) / (k3 - k2))
    return PHI_KNEE + (1.0 - PHI_KNEE) * ((x - k3) / (1.0 - k3)) ** 1.5


def psi_support(x: float) -> float:
    """主观支持缓冲函数，幂次由配置给出。"""
    x = min(max(x, 0.0), 1.0)
    return x ** PSI_GAMMA


# --------------------------------------------------------------------------- #
# 分档
# --------------------------------------------------------------------------- #
def _guard_floor(ctx: Dict[str, Any]) -> int:
    """工程化分诊底线：只抬档位，不改 IN 数值。"""
    floor = 0
    for guard in CFG["math"].get("guards", []):
        rules = guard.get("all") or guard.get("any")
        if not rules:
            continue
        ops = {
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            "<": lambda a, b: a < b,
        }
        results = []
        for rule in rules:
            field, op, value = rule["field"], rule["op"], rule["value"]
            actual = ctx.get(field)
            if actual is None:
                results.append(False)
                continue
            results.append(ops[op](actual, value))
        hit = any(results) if "any" in guard else all(results)
        if hit:
            floor = max(floor, BAND_IDS.index(guard["min_band"]))
    return floor


def band_index(score: float) -> int:
    idx = 0
    for edge in BANDS:
        if score >= edge:
            idx += 1
    return idx


def resolve_band(score: float, prev_band: Optional[str] = None) -> str:
    """带 ±0.02 回滞的分档，避免多轮对话在边界抖动。"""
    raw = band_index(score)
    if prev_band not in BAND_IDS[:4]:
        return BAND_IDS[raw]
    p = BAND_IDS.index(prev_band)
    if raw > p:
        return BAND_IDS[raw] if score >= BANDS[p] + HYST else BAND_IDS[p]
    if raw < p:
        return BAND_IDS[raw] if score < BANDS[p - 1] - HYST else BAND_IDS[p]
    return BAND_IDS[p]


# --------------------------------------------------------------------------- #
# 状态解析与校验
# --------------------------------------------------------------------------- #
def _as_dims(values: Any, n: int, lo: float, hi: float, label: str) -> List[float]:
    if not isinstance(values, (list, tuple)) or len(values) != n:
        raise StateError(f"{label} 必须是长度 {n} 的数组")
    out: List[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise StateError(f"{label} 含非数值元素：{v!r}")
        if v < lo or v > hi:
            raise StateError(f"{label} 元素 {v} 越界，合法区间 [{lo}, {hi}]")
        out.append(float(v))
    return out


def normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """接受 v1 状态或旧版 {"Eval":{...}} 单值状态，统一成内部结构。"""
    if not isinstance(state, dict):
        raise StateError("state 必须是 JSON 对象")

    # 兼容旧格式：{"Eval": {"D":4,"A":2,"F":9,"SS_sub":2,...}}
    payload = state.get("Eval", state)
    legacy = "Eval" in state or not all(k in payload for k in ("D", "A", "F", "SS"))

    if legacy:
        d = float(payload.get("D", 1))
        a = float(payload.get("A", 1))
        f = float(payload.get("F", 1))
        ss_sub = float(payload.get("SS_sub", payload.get("SS", 1)))
        s_obj = float(payload.get("SS_obj", ss_sub))
        s_uti = float(payload.get("SS_uti", ss_sub))
        for name, val, lo, hi in (
            ("D", d, 1, 5), ("A", a, 1, 5), ("F", f, 1, 10),
            ("SS_sub", ss_sub, 1, 10), ("SS_obj", s_obj, 1, 10), ("SS_uti", s_uti, 1, 10),
        ):
            if val < lo or val > hi:
                raise StateError(f"旧格式 {name}={val} 越界，合法区间 [{lo}, {hi}]")
        s_d = (d - 1) / 4.0
        s_a = (a - 1) / 4.0
        f_hat = (f - 1) / 9.0
        return {
            "legacy": True,
            "s_d": s_d, "s_a": s_a, "f": f_hat,
            "ss_sub": ss_sub, "ss_obj": s_obj, "ss_uti": s_uti,
            "ss_hat": (ss_sub - 1) / 9.0,
            "dims": {"D": None, "A": None, "F": None, "SS": None},
            "totals": {"D": None, "A": None, "F": None, "SS_sub": ss_sub},
            "flags": int(payload.get("f", 0) or 0),
        }

    dims_d = _as_dims(payload["D"], SCALES["D"]["dims"], 1, 5, "D")
    dims_a = _as_dims(payload["A"], SCALES["A"]["dims"], 1, 5, "A")
    dims_f = _as_dims(payload["F"], SCALES["F"]["dims"], 1, 10, "F")
    dims_s = _as_dims(payload["SS"], SCALES["SS"]["dims"], 1, 10, "SS")

    flags = payload.get("f", 0)
    if isinstance(flags, dict):
        flags = (1 if flags.get("crisis") else 0) | (2 if flags.get("od") else 0)
    if not isinstance(flags, int) or isinstance(flags, bool):
        raise StateError("f 必须是整数位掩码（1=危机，2=过量用药）")

    return {
        "legacy": False,
        "s_d": norm_depression(dims_d),
        "s_a": norm_anxiety(dims_a),
        "f": norm_fatigue(dims_f),
        "ss_sub": dims_s[0], "ss_obj": dims_s[1], "ss_uti": dims_s[2],
        "ss_hat": norm_subjective_support(dims_s[0]),
        "dims": {"D": dims_d, "A": dims_a, "F": dims_f, "SS": dims_s},
        "totals": {
            "D": sum(dims_d), "A": sum(dims_a),
            "F": sum(dims_f), "SS_sub": dims_s[0],
        },
        "flags": flags,
    }


# --------------------------------------------------------------------------- #
# 主计算
# --------------------------------------------------------------------------- #
def _trend(ns: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> float:
    if not prev:
        return 0.0
    p = normalize_state(prev)
    s_now = 0.5 * (ns["s_d"] + ns["s_a"])
    s_prev = 0.5 * (p["s_d"] + p["s_a"])
    raw = K_TREND_S * max(0.0, s_now - s_prev) + K_TREND_F * max(0.0, ns["f"] - p["f"])
    return min(TREND_CAP, raw)


def _limits(band: str, ns: Dict[str, Any], crisis: bool) -> Tuple[int, int]:
    spec = CFG["bands"][band]
    max_chars = int(spec["max_chars"])
    max_q = int(spec["max_questions"])
    mods = CFG["modifiers"]
    f_dims = ns["dims"]["F"]
    if not crisis and f_dims:
        m = mods["F_invasion_ge"]
        if f_dims[int(m["dim"]) - 1] >= m["threshold"]:
            max_chars = min(max_chars, int(m["max_chars"]))
        m = mods["F_avoidance_ge"]
        if f_dims[int(m["dim"]) - 1] >= m["threshold"]:
            max_chars = min(max_chars, int(m["max_chars"]))
            max_q = min(max_q, int(m["max_questions"]))
        m = mods["F_escape_ge"]
        if f_dims[int(m["dim"]) - 1] >= m["threshold"]:
            max_chars = min(max_chars, int(m["max_chars"]))
    if crisis:
        max_q = max(max_q, 1)
    d_dims = ns["dims"]["D"]
    if d_dims:
        m = mods["D_low_mood_ge"]
        if d_dims[int(m["dim"]) - 1] >= m["threshold"]:
            max_q = min(max_q, int(m["max_questions"]))
    return max_chars, max_q


def _strategies(ns: Dict[str, Any], band: str, crisis: bool, od: bool) -> List[str]:
    out: List[str] = []
    if crisis:
        out.append("S1_crisis")
    if od:
        out.append("S1_od")
    if crisis:
        return out

    f_dims, d_dims, a_dims = ns["dims"]["F"], ns["dims"]["D"], ns["dims"]["A"]
    if f_dims:
        if f_dims[2] >= 7:
            out.append("S2_short")
        if f_dims[4] >= 7:
            out.append("S2_listen")

    ss_sub, ss_obj, ss_uti = ns["ss_sub"], ns["ss_obj"], ns["ss_uti"]
    if ss_sub <= 3:
        out.append("S3_sssub_accept")
    elif ss_sub <= 5:
        out.append("S3_sssub_accept")
    if ss_uti <= 3:
        out.append("S3_ssuti_model")
    if ss_obj <= 3:
        out.append("S3_sssub_only")

    if f_dims:
        idx = _argmax(f_dims)
        if f_dims[idx] >= 7:
            out.append(FAT_STRATEGY[idx + 1])

    if d_dims:
        idx = _argmax(d_dims)
        if d_dims[idx] >= 3:
            out.append(DEP_STRATEGY[idx + 1])
    if a_dims:
        idx = _argmax(a_dims)
        if a_dims[idx] >= 3:
            out.append(ANX_STRATEGY[idx + 1])

    seen, ordered = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered[:6]


def _argmax(values: Sequence[float]) -> int:
    best, best_i = -1e9, 0
    for i, v in enumerate(values):
        if v > best:
            best, best_i = v, i
    return best_i


def compute(state: Dict[str, Any], prev: Optional[Dict[str, Any]] = None,
            prev_band: Optional[str] = None) -> Dict[str, Any]:
    """返回完整评估结果。prev 用于趋势项，prev_band 用于分档回滞。"""
    ns = normalize_state(state)
    s_hat = 0.5 * (ns["s_d"] + ns["s_a"])
    phi_v = phi_fatigue(ns["f"])
    psi_v = psi_support(ns["ss_hat"])
    trend = _trend(ns, prev)

    raw = W_SEV * s_hat + W_F * phi_v - W_SS * psi_v + trend
    score = min(max(raw, 0.0), 1.0)

    d_dims = ns["dims"]["D"]
    crisis_dim = CFG["safety"]["crisis_dim_index"]
    suicidal = d_dims[crisis_dim - 1] if d_dims else 0
    od = bool(ns["flags"] & 2)
    crisis = bool(ns["flags"] & 1) or (suicidal >= CFG["safety"]["crisis_dim_threshold"])
    elevated = suicidal >= CFG["safety"]["elevated_dim_threshold"]

    if crisis:
        score, band = 1.0, "L4"
    else:
        band = resolve_band(score, prev_band)
        ctx = {
            "D_total": ns["totals"]["D"], "A_total": ns["totals"]["A"],
            "F_total": ns["totals"]["F"], "SS_sub": ns["totals"]["SS_sub"],
            "F_hat": ns["f"],
        }
        floor = _guard_floor(ctx)
        if floor > BAND_IDS.index(band):
            band = BAND_IDS[floor]
        if elevated and BAND_IDS.index(band) < 3:
            band = "L3"

    max_chars, max_q = _limits(band, ns, crisis)
    warmth = int(CFG["bands"][band]["warmth"])
    if band == "L0" and score < 0.15:
        warmth = 1

    referral = crisis or bool(ns["flags"] & 4) or (
        ns["totals"]["D"] is not None
        and ns["totals"]["D"] >= CFG["safety"]["referral_total_D"]
    )

    return {
        "in": round(score, 4),
        "band": band,
        "band_label": CFG["bands"][band]["label"],
        "warmth": warmth,
        "max_chars": max_chars,
        "max_questions": max_q,
        "flags": {
            "crisis": crisis,
            "od": od,
            "referral": referral,
            "suicidal_elevated": elevated,
            "legacy_input": ns["legacy"],
        },
        "components": {
            "s_hat": round(s_hat, 4),
            "phi_fatigue": round(phi_v, 4),
            "psi_support": round(psi_v, 4),
            "trend": round(trend, 4),
            "in_raw": round(raw, 4),
        },
        "normalized": {
            "D": round(ns["s_d"], 4),
            "A": round(ns["s_a"], 4),
            "F": round(ns["f"], 4),
            "SS_sub": round(ns["ss_hat"], 4),
        },
        "totals": ns["totals"],
        "strategies": _strategies(ns, band, crisis, od),
    }


# --------------------------------------------------------------------------- #
# 分箱（速查表的唯一来源）
# --------------------------------------------------------------------------- #
BIN_BOUNDS = {
    "D": (9, 17, 24, 31, 38, 45),
    "A": (7, 13, 19, 25, 30, 35),
    "F": (5, 14, 23, 32, 41, 50),
    "SS_sub": (1, 3, 5, 7, 9, 10),
}
BIN_LABELS = {
    "D": ("9-16", "17-23", "24-30", "31-37", "38-45"),
    "A": ("7-12", "13-18", "19-24", "25-29", "30-35"),
    "F": ("5-13", "14-22", "23-31", "32-40", "41-50"),
    "SS_sub": ("1-2", "3-4", "5-6", "7-8", "9-10"),
}


def bin_total_mid(key: str, i: int) -> float:
    """第 i 箱（0..4）的代表总分。"""
    b = BIN_BOUNDS[key]
    return round((b[i] + b[i + 1] - 1) / 2.0, 2)


def compute_from_bins(d_bin: int, a_bin: int, f_bin: int, ss_bin: int,
                      prev_band: Optional[str] = None) -> Dict[str, Any]:
    """用总分分箱代表值计算，速查表与一致性测试的唯一入口。"""
    d_tot = bin_total_mid("D", d_bin)
    a_tot = bin_total_mid("A", a_bin)
    f_tot = bin_total_mid("F", f_bin)
    ss = bin_total_mid("SS_sub", ss_bin)

    s_d = _norm_total(d_tot, "D")
    s_a = _norm_total(a_tot, "A")
    f_hat = _norm_total(f_tot, "F")
    ss_hat = norm_subjective_support(ss)

    s_hat = 0.5 * (s_d + s_a)
    raw = (W_SEV * s_hat + W_F * phi_fatigue(f_hat) - W_SS * psi_support(ss_hat))
    score = min(max(raw, 0.0), 1.0)
    band = resolve_band(score, prev_band)
    ctx = {"D_total": d_tot, "A_total": a_tot, "F_total": f_tot,
           "SS_sub": ss, "F_hat": f_hat}
    floor = _guard_floor(ctx)
    if floor > BAND_IDS.index(band):
        band = BAND_IDS[floor]
    return {"in": round(score, 4), "band": band, "s_hat": round(s_hat, 4),
            "phi": round(phi_fatigue(f_hat), 4), "psi": round(psi_support(ss_hat), 4)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEMO_STATE = {
    "D": [4, 4, 3, 3, 3, 5, 3, 3, 1],
    "A": [3, 3, 2, 2, 3, 2, 2],
    "F": [6, 8, 9, 7, 6],
    "SS": [2, 3, 2],
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="emotional-mediation-support IN_Score 计算器")
    ap.add_argument("--state", help="v1 或旧版状态 JSON 字符串")
    ap.add_argument("--state-file", help="状态 JSON 文件路径")
    ap.add_argument("--prev", help="上一轮状态 JSON（用于趋势项）")
    ap.add_argument("--prev-band", choices=BAND_IDS[:4], help="上一轮档位（用于回滞）")
    ap.add_argument("--indent", action="store_true", help="美化输出")
    ap.add_argument("--demo", action="store_true", help="跑内置示例")
    ap.add_argument("--check-weights", action="store_true", help="自检权重与配置一致性")
    args = ap.parse_args(argv)

    if args.check_weights:
        total = W_SEV + W_F + W_SS
        print(json.dumps({
            "W_SEV": W_SEV, "W_F": W_F, "W_SS": W_SS, "sum": round(total, 6),
            "reachable_max": round(W_SEV + W_F, 4),
            "share_depression": 0.0429 / 0.3067,
            "share_anxiety": 0.0306 / 0.2970,
        }, ensure_ascii=False, indent=2))
        return 0 if abs(total - 1.0) < 1e-9 else 3

    payload: Optional[Dict[str, Any]] = None
    if args.demo:
        payload = DEMO_STATE
    elif args.state_file:
        with open(args.state_file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    elif args.state:
        payload = json.loads(args.state)
    else:
        ap.print_help()
        return 2

    prev = json.loads(args.prev) if args.prev else None
    try:
        result = compute(payload, prev=prev, prev_band=args.prev_band)
    except (StateError, KeyError) as exc:
        print(f"[state error] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.indent else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
