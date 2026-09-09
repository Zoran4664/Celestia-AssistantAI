"""
signal_bus.py — 全局信号总线（SignalBus）

观察者模式：所有跨模块事件（LLM 回复流、情感切换、桌宠指令、记忆更新、
会话保存、设置变更等）统一经由 SignalBus 发布/订阅，模块之间零直接耦合。

使用方式：
    bus = SignalBus.instance()
    bus.reply_stream.connect(self._on_stream)     # 订阅
    bus.reply_finished.emit(text, role)           # 发布
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SignalBus(QObject):
    """全局信号总线单例。所有信号均为跨线程安全的队列连接。"""

    _instance = None

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

    @classmethod
    def instance(cls) -> "SignalBus":
        """获取全局信号总线单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
