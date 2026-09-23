"""
notify_service.py — 统一通知服务（V2-B5）

参考 HanaAgent（openhanako）lib/notifications/notification-service.ts。

职责（只做「分发与幂等」，不负责具体展示）：
- 统一入口 notify(title, body, ...)，由 GUI 层（MainWindow / DesktopPet）连接
  SignalBus.notify_emitted 决定展示方式（弹窗 / 气泡 / 托盘）；
- 幂等去重：同 idempotency_key 在 TTL 内重复投递返回 skipped（防重复打扰）；
- 聚焦策略：desktop_focus = when_unfocused 时，通过 SignalBus.request("ui:window_active")
  查询主窗口是否活跃，活跃则跳过（避免打断用户）；
- 返回每通道投递明细 dict（如 {"desktop": "sent" / "skipped" / "skipped_focus"}）。

线程纪律：本类可在任意线程调用；display 槽（notify_emitted 连接者）在接收线程执行，
GUI 槽由 Qt 自动切到主线程（信号队列连接）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject

from config_loader import ConfigLoader
from signal_bus import SignalBus

logger = logging.getLogger("AI_DeskMate.Notify")

_CHANNEL_DESKTOP = "desktop"


class NotifyService(QObject):
    """统一通知服务（单例）。"""

    _instance: Optional["NotifyService"] = None

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cfg = ConfigLoader.instance()
        self._bus = SignalBus.instance()
        self._seen: Dict[str, float] = {}   # idempotency_key -> 过期时间戳
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "NotifyService":
        """获取全局通知服务单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 投递
    def notify(self, title: str, body: str = "",
               channel: str = _CHANNEL_DESKTOP,
               idempotency_key: Optional[str] = None,
               focus_policy: Optional[str] = None) -> Dict[str, str]:
        """投递一条通知。

        :param title: 标题
        :param body: 正文（可空）
        :param channel: 通道（当前支持 desktop；后续可扩展 bridge 等）
        :param idempotency_key: 幂等键（TTL 内重复投递返回 skipped）
        :param focus_policy: 覆盖配置的聚焦策略（always / when_unfocused）
        :return: {通道: 状态}，状态 ∈ sent / skipped / skipped_focus
        """
        title = str(title or "").strip()
        body = str(body or "").strip()
        if not title and not body:
            return {channel: "skipped"}
        ttl_sec = max(1, int(self._cfg.notify_idempotency_ttl_min() or 10) * 60)

        # 幂等去重
        if idempotency_key:
            now = time.time()
            with self._lock:
                expire = self._seen.get(idempotency_key)
                if expire is not None and expire > now:
                    return {channel: "skipped"}
                self._seen[idempotency_key] = now + ttl_sec

        # 聚焦策略：when_unfocused 且主窗口活跃时跳过
        fp = (focus_policy or self._cfg.notify_desktop_focus() or "always").strip()
        if fp == "when_unfocused":
            try:
                active = self._bus.request("ui:window_active")
                if active is True:
                    return {channel: "skipped_focus"}
            except Exception as exc:  # noqa: BLE001
                logger.warning("通知聚焦查询失败（按 always 投递）: %s", exc)

        self._bus.notify_emitted.emit(title, body)
        return {channel: "sent"}

    def notify_pet(self, body: str) -> Dict[str, str]:
        """便捷：桌宠气泡通知（复用 pet_say 信号，由桌宠展示）。"""
        if body:
            self._bus.pet_say.emit(str(body))
            return {"pet": "sent"}
        return {"pet": "skipped"}

    # ------------------------------------------------------------ 维护
    def cleanup_expired(self) -> int:
        """清理过期幂等键，返回清理条数。"""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._seen.items() if v <= now]
            for k in expired:
                self._seen.pop(k, None)
        return len(expired)

    def pending_keys(self) -> int:
        """当前未过期的幂等键数量（测试/诊断用）。"""
        now = time.time()
        with self._lock:
            return sum(1 for v in self._seen.values() if v > now)
