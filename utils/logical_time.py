"""
logical_time.py — 逻辑日与时间工具（V2-A4）

参考 HanaAgent（openhanako）lib/time-utils.ts：
- 逻辑日（logical day）：以凌晨 4 点为日界线。凌晨 00:00-03:59 归属前一天，
  保证「熬夜到凌晨」的对话仍算在同一个自然日里。
- 提供日期格式化 / 相对日期描述，供记忆传送带（memory_compile）、日记等使用。
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional


def logical_day(now: Optional[_dt.datetime] = None) -> _dt.date:
    """返回当前（或指定时刻）所属的逻辑日：凌晨 4 点前归属前一天。"""
    dt = now or _dt.datetime.now()
    if dt.hour < 4:
        dt = dt - _dt.timedelta(days=1)
    return dt.date()


def logical_day_str(now: Optional[_dt.datetime] = None) -> str:
    """逻辑日字符串（YYYY-MM-DD）。"""
    return logical_day(now).strftime("%Y-%m-%d")


def is_new_logical_day(prev_date: Optional[str], now: Optional[_dt.datetime] = None) -> bool:
    """判断是否跨入新的逻辑日（prev_date 为空时视为 True）。"""
    cur = logical_day_str(now)
    return bool(prev_date) and prev_date != cur


def date_label(date_obj: _dt.date, now: Optional[_dt.datetime] = None) -> str:
    """把日期转成人类可读标签：今天 / 昨天 / 前天 / YYYY-MM-DD。"""
    cur = logical_day(now)
    delta = (cur - date_obj).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if delta == 2:
        return "前天"
    return date_obj.strftime("%Y-%m-%d")


def recent_logical_days(n: int, now: Optional[_dt.datetime] = None) -> list:
    """返回最近 n 个逻辑日（不含今天），日期升序（最远在前）。"""
    cur = logical_day(now)
    out = []
    for i in range(n, 0, -1):
        out.append(cur - _dt.timedelta(days=i))
    return out
