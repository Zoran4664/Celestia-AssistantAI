#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验状态向量（v1 或旧版 Eval 格式），并打印归一化摘要。

用法：
    python validate_state.py --state-file state.json
    python validate_state.py --state '{"v":1,"t":3,"D":[...],"A":[...],"F":[...],"SS":[...]}'
    python validate_state.py --demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import in_score as m  # noqa: E402

ALLOWED_TOP_KEYS = {"v", "t", "D", "A", "F", "SS", "x", "f", "Eval"}
DEMO = {
    "v": 1, "t": 3,
    "D": [3, 4, 3, 3, 3, 4, 3, 3, 1],
    "A": [3, 3, 2, 2, 3, 2, 2],
    "F": [5, 7, 8, 4, 6],
    "SS": [3, 4, 2],
    "x": {"IN": 0.31, "b": "L2", "w": 4, "q": 1},
    "f": 0,
}


def validate(payload: dict) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    data = payload
    if not isinstance(payload, dict):
        return False, ["状态必须是 JSON 对象"], {}

    unknown = set(data) - ALLOWED_TOP_KEYS
    if unknown:
        problems.append(f"未知字段：{sorted(unknown)}")
    if "v" in data and data["v"] != 1:
        problems.append(f"不支持的 schema 版本：{data['v']}")
    if "t" in data and (not isinstance(data["t"], int) or data["t"] < 1):
        problems.append("t 必须是 >=1 的整数轮次")

    try:
        result = m.compute(data)
    except (m.StateError, KeyError) as exc:
        return False, problems + [f"评分失败：{exc}"], {}

    x = data.get("x")
    if isinstance(x, dict):
        if "IN" in x and abs(float(x["IN"]) - result["in"]) > 0.11:
            problems.append(f"x.IN={x['IN']} 与重算值 {result['in']} 相差过大")
        if "b" in x and x["b"] not in m.BAND_IDS:
            problems.append(f"x.b={x['b']} 不是合法档位")
        if "w" in x and not 1 <= int(x["w"]) <= 5:
            problems.append(f"x.w={x['w']} 必须在 1-5")
        if "q" in x and not 0 <= int(x["q"]) <= 2:
            problems.append(f"x.q={x['q']} 必须在 0-2")
    return not problems, problems, result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state")
    ap.add_argument("--state-file")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo:
        payload = DEMO
    elif args.state_file:
        payload = json.loads(Path(args.state_file).read_text(encoding="utf-8"))
    elif args.state:
        payload = json.loads(args.state)
    else:
        ap.print_help()
        return 2

    ok, problems, result = validate(payload)
    print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False, indent=2))
    if result:
        print(json.dumps({
            "in": result["in"], "band": result["band"],
            "warmth": result["warmth"], "max_chars": result["max_chars"],
            "max_questions": result["max_questions"],
            "strategies": result["strategies"],
        }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
