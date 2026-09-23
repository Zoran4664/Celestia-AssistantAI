"""
signal_bus.py — 全局信号总线（SignalBus）

观察者模式：所有跨模块事件（LLM 回复流、情感切换、桌宠指令、记忆更新、
会话保存、设置变更等）统一经由 SignalBus 发布/订阅，模块之间零直接耦合。

使用方式：
    bus = SignalBus.instance()
    bus.reply_stream.connect(self._on_stream)     # 订阅
    bus.reply_finished.emit(text, role)           # 发布

V2 扩展（goat_V2_list.md B6）：
- request/handle 请求-响应模式 + 能力注册表：模块间互相查询状态 / 调用能力
    bus.handle("memory:today_summary", fn)        # 注册能力处理器
    bus.request("memory:today_summary", role)     # 请求，返回第一个非 None 结果
- 新增 V2 信号：memory_compiled / notify_emitted / cron_task_done / file_updated / heartbeat_tick
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("AI_DeskMate.Bus")

# request/handle 约定：handler 返回 None 表示「不处理此请求」（SKIP），
# 返回其它值表示「已处理」，request 立即返回该值。
_SKIP = None


class SignalBus(QObject):
    """全局信号总线单例。所有信号均为跨线程安全的队列连接。"""

    _instance: Optional["SignalBus"] = None

    # ------------------------------ 主对话流 ------------------------------
    request_started = Signal(str)           # 请求开始（携带用户输入）
    reply_stream = Signal(str)              # 流式回复片段
    reply_finished = Signal(str, str)       # (完整回复文本, 发言角色)
    reply_error = Signal(str)               # 异常信息

    # --------------------------- 角色 / 情感 / 立绘 ------------------------
    emotion_changed = Signal(str)           # 归一化情感标签
    portrait_switched = Signal(str, str)    # (角色, 情感) -> 切换立绘
    role_switched = Signal(str)             # 单聊角色切换
    group_changed = Signal(str)             # 群聊组切换（空串 = 单聊）
    speaker_switched = Signal(str)          # 群聊最后发言角色切换（桌宠跟随显示）

    # ------------------------------ 记忆管线 ------------------------------
    memory_notice = Signal(str)             # 记忆状态提示（归档/遗忘/训练）
    memory_updated = Signal()               # 记忆已更新（刷新界面提示等）

    # -------------------------------- 桌宠 --------------------------------
    pet_say = Signal(str)                   # 桌宠气泡文本
    pet_state = Signal(str)                 # 桌宠动作状态（stand/run/say1/...）
    pet_proactive = Signal()                # 触发随机主动问候
    show_main_requested = Signal()          # 请求显示主窗口（桌宠右键）

    # -------------------------------- 会话 --------------------------------
    conversation_saved = Signal(str)        # 会话文件已保存（路径）

    # -------------------------------- 技能 --------------------------------
    skill_invoked = Signal(str)             # 技能被调用（技能名）

    # -------------------------------- 生图 --------------------------------
    image_generated = Signal(str)           # 生图完成（本地图片路径）

    # -------------------------------- 系统 --------------------------------
    settings_updated = Signal()             # 设置已保存（各模块需重建配置）
    app_quit = Signal()                     # 请求退出应用

    # ------------------------------ V2 新信号 ------------------------------
    memory_compiled = Signal(str)           # 记忆传送带编译完成（携带摘要说明）
    notify_emitted = Signal(str, str)       # 统一通知投递 (标题, 正文)
    cron_task_done = Signal(str, str)       # Cron 任务完成 (job_id, 状态)
    file_updated = Signal(str)              # 文件工具写入文件（路径）
    heartbeat_tick = Signal()               # 心跳巡检触发（可手动触发）
    heartbeat_care_requested = Signal(str)  # 心跳发现变化 → 请求角色生成关怀（携带 hint）

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # 能力注册表（capability -> [handler, ...]），线程安全
        self._cap_handlers: Dict[str, List[Callable[..., Any]]] = {}
        self._cap_lock = threading.RLock()

    @classmethod
    def instance(cls) -> "SignalBus":
        """获取全局信号总线单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ V2: request/handle
    def handle(self, capability: str, handler: Callable[..., Any]) -> None:
        """注册能力处理器：capability 为能力名（如 "memory:today_summary"）。

        handler 返回 None 表示「不处理」（SKIP），返回其它值表示已处理。
        同一能力可注册多个 handler，按注册顺序尝试。
        """
        with self._cap_lock:
            self._cap_handlers.setdefault(capability, []).append(handler)

    def unhandle(self, capability: str, handler: Optional[Callable[..., Any]] = None) -> None:
        """注销能力处理器；handler 为空时注销该能力全部处理器。"""
        with self._cap_lock:
            if capability not in self._cap_handlers:
                return
            if handler is None:
                self._cap_handlers.pop(capability, None)
            else:
                hs = self._cap_handlers[capability]
                try:
                    hs.remove(handler)
                except ValueError:
                    pass
                if not hs:
                    self._cap_handlers.pop(capability, None)

    def request(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        """请求-响应调用：依次调用该能力所有 handler，返回第一个非 None 结果。

        约定：
        - handler 返回 None 表示「不处理此请求」，继续尝试下一个；
        - 全部 handler 返回 None 时返回 None；
        - handler 内禁止做耗时操作（应内部开 AsyncWorker / LLMWorker），
          避免阻塞调用方线程。
        """
        with self._cap_lock:
            handlers = list(self._cap_handlers.get(capability, []))
        for h in handlers:
            try:
                result = h(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("能力请求 %s 的处理器异常: %s", capability, exc)
                continue
            if result is not None:
                return result
        return None

    def capabilities(self) -> List[str]:
        """列出当前已注册的全部能力名。"""
        with self._cap_lock:
            return list(self._cap_handlers.keys())
