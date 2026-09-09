# -*- coding: utf-8 -*-
"""logintime.py — 主界面打开时间记录（登陆时间）

需求：data/logintime.json 存储每次打开主应用主界面的时间，
logintime_history.json 记录长期最常见的打开时间（每小时一个时间段，共 24 个），
两者均可用于对话调用（对话、问候等 API 调用时注入上下文）。

功能：
- record_login()        : 打开主界面时记录（受配置开关 ui.login_time_enabled 控制）
- login_time_summary()  : 最近若干次打开时间摘要（供对话注入）
- login_time_stats()    : 24 小时段长期分布统计（供对话注入）
- forget_short()        : 遗忘短期记录（清空 logintime.json）
- forget_long()         : 遗忘长期记录（重置 logintime_history.json）
- 关闭功能时不再记录，但保留已有数据（仅开关停用）
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.LoginTime")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOGIN_TIME_FILE = DATA_DIR / "logintime.json"
LOGIN_HISTORY_FILE = DATA_DIR / "logintime_history.json"

# 每个小时一个时间段，一天 24 个
HOURS = 24

# 短期记录保留条数上限（防止无限膨胀）
_MAX_SHORT = 200

_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _load_json(path: Path, default: Any) -> Any:
    """安全读取 JSON，失败返回 default。"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return default


def _save_json(path: Path, data: Any) -> None:
    """原子写入 JSON（先写临时文件再替换）。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 %s 失败: %s", path.name, exc)


def _login_enabled() -> bool:
    """读取配置开关：关闭后不再记录（但保留已有数据）。"""
    try:
        from config_loader import ConfigLoader
        return bool(ConfigLoader.instance().get(
            "ui", "login_time_enabled", default=True))
    except Exception:  # noqa: BLE001
        return True


def record_login(now: Optional[datetime] = None) -> bool:
    """记录一次主界面打开时间。

    向 logintime.json 追加一条（含时间与所在小时段），并累加
    logintime_history.json 中对应小时段计数。功能关闭时不记录。

    返回是否已记录。
    """
    if not _login_enabled():
        return False
    ts = now or datetime.now()
    hour = ts.hour
    rec = {
        "time": ts.strftime(_TS_FMT),
        "hour": hour,
        "timestamp": int(time.time()),
    }
    # 短期记录
    data = _load_json(LOGIN_TIME_FILE, {})
    if not isinstance(data, dict):
        data = {}
    logins = data.get("logins")
    if not isinstance(logins, list):
        logins = []
    logins.append(rec)
    # 保留最近 _MAX_SHORT 条
    data["logins"] = logins[-_MAX_SHORT:]
    data["total"] = len(logins)
    data["last_login"] = rec["time"]
    _save_json(LOGIN_TIME_FILE, data)

    # 长期分布
    hist = _load_json(LOGIN_HISTORY_FILE, {})
    if not isinstance(hist, dict):
        hist = {}
    buckets = hist.get("buckets")
    if not isinstance(buckets, dict):
        buckets = {str(i): 0 for i in range(HOURS)}
    # 兼容缺失键
    for i in range(HOURS):
        buckets.setdefault(str(i), 0)
    try:
        buckets[str(hour)] = int(buckets.get(str(hour), 0)) + 1
    except (TypeError, ValueError):
        buckets[str(hour)] = 1
    hist["buckets"] = buckets
    hist["total"] = sum(int(v) for v in buckets.values())
    hist["last_update"] = rec["time"]
    _save_json(LOGIN_HISTORY_FILE, hist)
    return True


def login_time_summary(limit: int = 8) -> str:
    """最近若干次打开时间摘要（供对话/问候 API 注入上下文）。

    返回示例：「用户最近打开本应用的时间：2026-08-08 10:30:00（10 点）、
    2026-08-07 22:10:00（22 点）…」
    无记录时返回空串。
    """
    data = _load_json(LOGIN_TIME_FILE, {})
    logins = data.get("logins") if isinstance(data, dict) else None
    if not isinstance(logins, list) or not logins:
        return ""
    recent = list(reversed(logins))[:limit]
    parts = [f"{r.get('time')}（{r.get('hour')} 点）" for r in recent
             if isinstance(r, dict) and r.get("time")]
    if not parts:
        return ""
    return "最近打开本应用的时间：" + "、".join(parts) + "。"


def login_time_stats() -> str:
    """24 小时段长期打开分布统计（供对话/问候 API 注入上下文）。

    返回最常见的 3 个时间段及占比；无记录时返回空串。
    """
    hist = _load_json(LOGIN_HISTORY_FILE, {})
    buckets = hist.get("buckets") if isinstance(hist, dict) else None
    if not isinstance(buckets, dict):
        return ""
    total = sum(int(v) for v in buckets.values())
    if total <= 0:
        return ""
    ranked = sorted(
        ((int(v), int(k)) for k, v in buckets.items()
         if isinstance(v, int) or (isinstance(v, str) and v.isdigit())),
        reverse=True)[:3]
    if not ranked:
        return ""
    parts = [f"{h} 点（{c} 次，占 {c * 100 // total}%）" for c, h in ranked]
    return "用户长期最常见的打开时段：" + "、".join(parts) + "。"


def login_time_context() -> str:
    """组合短期摘要 + 长期分布（供对话系统提示注入）。"""
    parts = []
    s = login_time_summary()
    if s:
        parts.append(s)
    st = login_time_stats()
    if st:
        parts.append(st)
    return " ".join(parts) if parts else ""


def forget_short() -> int:
    """遗忘短期登陆时间：清空 logintime.json 全部记录。返回删除条数。"""
    data = _load_json(LOGIN_TIME_FILE, {})
    logins = data.get("logins") if isinstance(data, dict) else None
    n = len(logins) if isinstance(logins, list) else 0
    try:
        LOGIN_TIME_FILE.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("删除 %s 失败: %s", LOGIN_TIME_FILE.name, exc)
    return n


def forget_long() -> int:
    """遗忘长期登陆时间：重置 logintime_history.json 分布。返回累计记录数。"""
    hist = _load_json(LOGIN_HISTORY_FILE, {})
    buckets = hist.get("buckets") if isinstance(hist, dict) else None
    total = 0
    if isinstance(buckets, dict):
        total = sum(int(v) for v in buckets.values())
    try:
        LOGIN_HISTORY_FILE.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("删除 %s 失败: %s", LOGIN_HISTORY_FILE.name, exc)
    return total


def clear_all() -> None:
    """清除全部登陆时间数据（短期 + 长期）。"""
    forget_short()
    forget_long()


if __name__ == "__main__":
    # 自测：记录 3 次并输出摘要
    from datetime import timedelta
    base = datetime.now().replace(minute=0, second=0, microsecond=0)
    for i in range(3):
        record_login(base - timedelta(hours=i))
    print("summary:", login_time_summary())
    print("stats:", login_time_stats())
    print("context:", login_time_context())

