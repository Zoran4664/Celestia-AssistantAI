# -*- coding: utf-8 -*-
"""
calorie_tracker.py — \\@calorie 饮食热量工具

做什么：
    1. 解析「吃了什么」，估算热量（优先联网查中国食物成分表
       https://nlc.chinanutri.cn/fq/ 作参考，查不到时用模型常识估算）；
    2. 把「时间 + 饮食 + 热量」写入 data/dailylifedata.json（按日汇总）；
    3. 按《中国居民膳食指南》给出简单的饮食指导建议。

不含任何 QWidget：纯数据/IO，可在子线程执行。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import project_root

DATA_FILE = project_root() / "data" / "dailylifedata.json"
NLC_URL = "https://nlc.chinanutri.cn/fq/"

# 参考《中国居民膳食指南（2022）》的成人每日能量需要量（kcal，轻体力活动）
DAILY_REFERENCE = {"女": 1800, "男": 2250, "默认": 2000}

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    m = _JSON_RE.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _meal_of(ts: datetime) -> str:
    h = ts.hour
    if h < 4 or h >= 21:        # 深夜（0-4 点、21 点以后）算夜宵
        return "夜宵"
    if h < 10:
        return "早餐"
    if h < 15:
        return "午餐"
    return "晚餐"


class CalorieTracker:
    """饮食热量记录（data/dailylifedata.json）。"""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool

    # ------------------------------------------------------------ 存档
    @staticmethod
    def load() -> Dict[str, Any]:
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"updated": "", "records": [], "daily": {}}
        if not isinstance(data, dict):
            return {"updated": "", "records": [], "daily": {}}
        data.setdefault("records", [])
        data.setdefault("daily", {})
        return data

    @staticmethod
    def save(data: Dict[str, Any]) -> None:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, DATA_FILE)

    @staticmethod
    def day_total(date: str = "") -> int:
        data = CalorieTracker.load()
        date = date or datetime.now().strftime("%Y-%m-%d")
        return int((data.get("daily") or {}).get(date, {}).get("total_kcal", 0) or 0)

    @classmethod
    def append_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        """写入一条记录并刷新当日汇总，返回 {record, day_total, date}。"""
        data = cls.load()
        ts = datetime.now()
        rec = dict(record)
        rec.setdefault("ts", ts.strftime("%Y-%m-%d %H:%M:%S"))
        rec.setdefault("date", ts.strftime("%Y-%m-%d"))
        rec.setdefault("meal", _meal_of(ts))
        try:
            rec["total_kcal"] = int(rec.get("total_kcal") or 0)
        except Exception:  # noqa: BLE001
            rec["total_kcal"] = 0
        data["records"].append(rec)
        day = data["daily"].setdefault(
            rec["date"], {"total_kcal": 0, "count": 0, "meals": {}})
        day["total_kcal"] = int(day.get("total_kcal") or 0) + rec["total_kcal"]
        day["count"] = int(day.get("count") or 0) + 1
        meals = day.setdefault("meals", {})
        meals[rec["meal"]] = int(meals.get(rec["meal"]) or 0) + rec["total_kcal"]
        cls.save(data)
        return {"record": rec, "day_total": day["total_kcal"], "date": rec["date"]}

    # ------------------------------------------------------------ 估算
    def online_reference(self, food_text: str) -> str:
        """联网参考：优先用已配置的联网搜索查中国食物成分表数据（失败返回空串）。"""
        if not food_text or self._pool is None:
            return ""
        fn = getattr(self._pool, "web_search", None)
        if fn is None:
            return ""
        try:
            return fn(f"{food_text} 热量 千卡 每100克 中国食物成分表", top_n=5) or ""
        except Exception:  # noqa: BLE001 - 未配置搜索 / 网络异常时静默降级
            return ""

    def estimate(self, food_text: str) -> Dict[str, Any]:
        """估算热量：返回 {items, total_kcal, advice, reference, online}。"""
        if self._pool is None:
            raise RuntimeError("模型客户端不可用")
        ref = self.online_reference(food_text)
        system = (
            "你是营养师。按《中国居民膳食指南（2022）》估算用户描述的饮食热量。"
            "只输出 JSON（不要 Markdown 代码块、不要解释），结构：\n"
            "{\n"
            '  "items":[{"name":"食物","amount":"分量","kcal":热量整数,'
            '"note":"估算依据（每100克约多少千卡）"}],\n'
            '  "total_kcal":总热量整数,\n'
            '  "advice":"80-150 字的简单饮食指导建议：包含三餐搭配、油盐、'
            '蔬果/奶类/全谷物的摄入提醒，符合中国居民膳食指南"\n'
            "}\n"
            "分量不明时按常见一份估算并注明；总热量四舍五入到 10 kcal。"
        )
        user = f"用户说：{food_text}\n"
        if ref:
            user += f"\n可参考的联网资料（如与常识冲突以权威数据为准）：\n{ref[:2000]}"
        fn = getattr(self._pool, "chat_complete_custom", None)
        if fn is None:
            raise RuntimeError("模型客户端不支持 chat_complete_custom")
        raw = fn([{"role": "system", "content": system},
                  {"role": "user", "content": user}],
                 temperature=0.3, max_tokens=1200)
        data = _extract_json(raw)
        items = data.get("items") or []
        if not isinstance(items, list):
            items = []
        try:
            total = int(data.get("total_kcal") or 0)
        except Exception:  # noqa: BLE001
            total = sum(int(i.get("kcal") or 0) for i in items
                        if isinstance(i, dict))
        if not total:
            total = sum(int(i.get("kcal") or 0) for i in items
                        if isinstance(i, dict))
        return {"items": items, "total_kcal": total,
                "advice": str(data.get("advice") or ""),
                "reference": NLC_URL, "online": bool(ref), "raw": raw}

    # ------------------------------------------------------------ 展示
    @staticmethod
    def format_report(result: Dict[str, Any], day_total: int,
                      date: str = "", meal: str = "") -> str:
        items = result.get("items") or []
        lines = ["**饮食热量记录**"
                 + (f"（{date} {meal}）" if date or meal else ""), ""]
        if items:
            lines.append("| 食物 | 分量 | 热量 |")
            lines.append("| --- | --- | --- |")
            for it in items[:20]:
                if isinstance(it, dict):
                    lines.append(f"| {it.get('name', '')} | {it.get('amount', '')} "
                                 f"| {it.get('kcal', 0)} kcal |")
            lines.append("")
        lines.append(f"**本餐合计：约 {result.get('total_kcal', 0)} kcal**")
        lines.append(f"**今日累计：约 {day_total} kcal**"
                     f"（参考：成人每日约 {DAILY_REFERENCE['默认']} kcal，"
                     f"女 {DAILY_REFERENCE['女']} / 男 {DAILY_REFERENCE['男']}）")
        left = DAILY_REFERENCE["默认"] - day_total
        if left > 0:
            lines.append(f"剩余额度约 {left} kcal。")
        else:
            lines.append(f"已超出参考额度约 {abs(left)} kcal，建议后续清淡、增加活动量。")
        if result.get("advice"):
            lines.append("")
            lines.append(f"**饮食建议（参考《中国居民膳食指南》）**：{result['advice']}")
        lines.append("")
        src = f"（已联网查证）" if result.get("online") else "（未配置联网搜索，按常见食物成分估算）"
        lines.append(f"数据来源：模型估算{src} · 可核对 中国食物成分表 {NLC_URL}")
        lines.append("已记录到 `data/dailylifedata.json`")
        return "\n".join(lines)


def recent_days(limit: int = 7) -> List[Dict[str, Any]]:
    """最近若干天的汇总（供展示）。"""
    data = CalorieTracker.load()
    days = sorted((data.get("daily") or {}).items(), reverse=True)[:limit]
    return [{"date": d, "total_kcal": int((v or {}).get("total_kcal") or 0),
             "count": int((v or {}).get("count") or 0)} for d, v in days]
