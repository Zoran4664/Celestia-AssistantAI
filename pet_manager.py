"""
pet_manager.py — 桌面宠物引擎（DesktopPet · 渐隐过渡）

- 透明置顶无边框窗口 + GIF 动图状态机（QMovie）
  动作：standing(静止) / run(拖动) / say1(输入) / say2(输出) / hello(问候)
        / sleep(休息) / work(专注)
- 所有动作切换均通过 QPropertyAnimation 实现 opacity 渐隐过渡（300ms crossfade）
- 右键菜单 11 项：设置日程 / 设置提醒 / 定时问候 / 小窗对话 / 悬浮对话 /
  提醒休息 / 专注模式 / 专注助手 / 返回主页 / 隐藏桌宠 / 退出
- 拖拽移动（run.gif）、静止 standing.gif；气泡对话框形式输出
- 200 字极简对话小窗（MiniChatWindow）
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, QPoint, QSize, QRect, QRectF, QDateTime, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QCursor, QIcon, QMovie, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from config_loader import ConfigLoader
from signal_bus import SignalBus
from role_manager import RoleManager
from memory_pipeline import MemoryPipeline
from llm_client import LLMClientPool, LLMWorker

_PET_ACTIONS = ("standing", "run", "say1", "say2", "hello", "sleep", "work", "angry")
_ACTION_EMOJI = {
    "standing": "AI", "run": "AI", "say1": "说", "say2": "说",
    "hello": "嗨", "sleep": "睡", "work": "专注",
}
_GREETINGS = [
    "嗨，我在这儿哦~", "需要帮忙吗？", "记得喝水呀！",
    "要不要休息一下眼睛？", "我一直陪着你~",
]

_DIALOG_QSS = """
QDialog { background:#ffffff; }
QLabel { color:#2b2b33; font-size:13px; }
QListWidget {
    background:white; border:1px solid #e5e7f0; border-radius:8px;
    font-size:13px; outline:none;
}
QPushButton {
    background:#6c8ef5; color:white; border:none; border-radius:8px;
    padding:6px 16px; font-size:13px; font-weight:600;
}
QPushButton:hover { background:#8fb0ff; }
"""

# 小窗界面底框图片目录（img/）
_IMG_DIR = Path(__file__).resolve().parent / "img"


def _img(name: str) -> Path:
    """返回 img/ 下的图片路径；兼容 answer.png / answear.png 拼写差异。"""
    p = _IMG_DIR / name
    if name == "answer.png" and not p.exists():
        alt = _IMG_DIR / "answear.png"
        if alt.exists():
            return alt
    return p


def _qss_url(path: Path) -> str:
    """将 Windows 路径转换为 QSS url() 可用的正斜杠形式。"""
    return str(path).replace("\\", "/")

# 与主界面 root_qss 一致的 HTML5 风格右键菜单样式（问题：风格统一）
_MENU_QSS = """
QMenu {
    background: rgba(255,255,255,0.97);
    border: 1px solid #e5e7f0;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    color: #2b2b33;
}
QMenu::item {
    padding: 7px 26px;
    border-radius: 7px;
    margin: 2px 6px;
}
QMenu::item:selected {
    background: rgba(108,142,245,0.12);
    color: #6c8ef5;
}
QMenu::item:disabled { color: #9aa0ac; }
QMenu::separator {
    height: 1px;
    background: #e5e7f0;
    margin: 4px 10px;
}
"""

# 悬浮提醒小窗样式（与主界面卡片风格一致；卡片白色圆角底由 AlertPopup.paintEvent 手工绘制）
_ALERT_QSS = """
QLabel#alertTitle { color:#2b2b33; font-size:14px; font-weight:600; }
QLabel#alertBody {
    color:#2b2b33; font-size:13px; background:transparent;
    border:none; padding:4px;
}
QPushButton#alertClose {
    background:#ffffff; color:#2b2b33; border:1px solid #e5e7f0;
    border-radius:6px; padding:3px 12px; font-size:12px;
}
QPushButton#alertClose:hover {
    background:#fff1f1; color:#e11d48; border-color:#e11d48;
}
"""


class DesktopPet(QWidget):
    """透明置顶桌宠窗口（GIF 渐隐过渡状态机）。"""

    def __init__(self, main_window: Optional[QWidget] = None,
                 initial_role: str = "默认助手") -> None:
        super().__init__(None)
        self._cfg = ConfigLoader.instance()
        self._roles = RoleManager.instance()
        self._memory = MemoryPipeline.instance()
        self._pool = LLMClientPool.instance()
        self._bus = SignalBus.instance()
        self._main_window = main_window
        self._role = initial_role
        self._current_action = ""   # 初始为空，确保首个 set_state 真正换图
        self._current_movie: Optional[QMovie] = None
        self._dragging = False
        self._drag_offset = QPoint()
        self._reminders: List[Dict[str, Any]] = []
        self._greeting_on = False
        self._rest_on = False
        self._sleep_reminded = False
        self._focus_last_warn = 0.0
        self._focus_last_active = 0.0
        self._focus_target_title = ""
        self._floating_on = bool(self._cfg.get("pet", "floating_chat", default=False))
        self._mini_chat: Optional[MiniChatWindow] = None
        self._mini_chat_closed = False   # 用户关闭小窗后不再自动弹出，直到鼠标离开再进入

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._base_w, self._base_h = 300, 400   # 桌宠默认尺寸（300x400 基准）
        self._zoom = 1.0
        self.resize(self._base_w, self._base_h)
        # 默认显示在屏幕右下角（需求：桌宠开启默认在右下角）
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self._base_w, area.bottom() - self._base_h)

        self._build_ui()
        self._build_menu()
        self._start_timers()
        self._bus.pet_say.connect(self._on_pet_say)
        self._bus.role_switched.connect(self._on_role_switched)
        # 群聊：最后发言角色切换 → 桌宠显示为发送最后一条消息的角色（需求 2）
        self._bus.speaker_switched.connect(self._on_role_switched)
        self.set_state("standing")

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._movie_label = QLabel(self)
        self._movie_label.setAlignment(Qt.AlignCenter)
        self._movie_label.setFixedSize(self._base_w, self._base_h)
        self._movie_label.setStyleSheet("font-size:64px;")
        layout.addWidget(self._movie_label)

        # 桌宠提示内容统一走 AlertPopup（白色圆角卡片，paintEvent 手工绘制底框），
        # 不再创建「只有文字没有底框」的独立气泡窗口（需求：不要显示该弹窗）。

    # ------------------------------------------------------------ 渐隐过渡
    def _pet_gif(self, action: str) -> str:
        """获取动作 GIF；缺失或为极小的占位文件时回退到 standing（②）。"""
        gif = self._roles.pet_gif_path(self._role, action)
        if gif:
            try:
                if Path(gif).stat().st_size < 4000:
                    gif = ""          # 极小占位图（图标）→ 视为缺失
            except OSError:
                gif = ""
        if not gif and action != "standing":
            gif = self._roles.pet_gif_path(self._role, "standing")
        return gif

    def _set_movie(self, gif: str) -> None:
        """将 GIF 动图加载到标签（无淡入；缩放时用于强制刷新尺寸）。"""
        if gif:
            movie = QMovie(gif)
            movie.setScaledSize(QSize(self._pet_w(), self._pet_h()))
            self._movie_label.setMovie(movie)
            movie.setCacheMode(QMovie.CacheAll)
            movie.start()
            self._current_movie = movie
        else:
            self._movie_label.setMovie(None)
            self._movie_label.setText("")   # 无 GIF 时不显示文字，直接空白
            self._current_movie = None

    def _crossfade(self, action: str) -> None:
        """GIF 动作切换：立即换图 + 淡入（保证图片始终显示）。"""
        self._set_movie(self._pet_gif(action))

        # 淡入过渡（QGraphicsOpacityEffect；保留引用避免被回收）
        self._movie_label.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(self._movie_label)
        self._movie_label.setGraphicsEffect(effect)
        self._fade_effect = effect
        anim = QPropertyAnimation(effect, b"opacity", self)
        self._fade_anim = anim
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(300)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: self._movie_label.setGraphicsEffect(None))
        anim.start()
        # 保险：无论动画是否完成，400ms 后移除特效，保证图片可见
        QTimer.singleShot(400, lambda: self._movie_label.setGraphicsEffect(None))

    def set_state(self, action: str, duration_ms: int = 0) -> None:
        """状态机：切换到指定动作；非基态动作可自动回退 standing。"""
        if action not in _PET_ACTIONS:
            action = "standing"
        if action == self._current_action:
            return
        self._current_action = action
        self._crossfade(action)
        self._bus.pet_state.emit(action)
        if duration_ms > 0 and action in ("say1", "say2", "hello"):
            QTimer.singleShot(duration_ms, lambda: self.set_state("standing"))

    def _on_role_switched(self, role: str) -> None:
        self._role = role
        self._current_action = ""
        self.set_state("standing")

    def _on_pet_say(self, text: str) -> None:
        if not text or not str(text).strip():
            return
        self.set_state("say2", duration_ms=random.randint(2000, 4000))
        self._show_alert("桌宠", text[:120])

    def _show_bubble(self, text: str) -> None:
        """桌宠提示内容统一走 AlertPopup（白色圆角卡片，有底框）。

        不再使用「只有文字没有底框」的独立气泡窗口（需求：不要显示该弹窗）。
        """
        self._show_alert("桌宠", text)

    def _hide_bubble(self) -> None:
        """兼容旧引用：直接关闭当前提醒弹窗。"""
        if getattr(self, "_alert_popup", None) is not None:
            self._alert_popup.close()

    def _fade_bubble(self) -> None:
        """兼容旧引用：无独立气泡窗口，改为关闭当前提醒弹窗。"""
        self._hide_bubble()

    # ------------------------------------------------------------ 滚轮缩放
    def _pet_w(self) -> int:
        return int(self._base_w * self._zoom)

    def _pet_h(self) -> int:
        return int(self._base_h * self._zoom)

    def _apply_size(self) -> None:
        """按缩放系数调整桌宠窗口与动图尺寸（立即生效，无需点击/拖动）。"""
        w, h = self._pet_w(), self._pet_h()
        self.resize(w, h)
        self._movie_label.setFixedSize(w, h)
        # 重新加载动图，强制 QMovie 立即应用新的 scaledSize（直接渲染新尺寸）
        self._set_movie(self._pet_gif(self._current_action or "standing"))
        self.update()
        self._movie_label.update()

    def wheelEvent(self, event: Any) -> None:  # noqa: D102
        """鼠标滚轮放大/缩小桌宠（可在设置中关闭）。"""
        if not self._cfg.pet_zoom_enabled():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(3.0, self._zoom * 1.1)
        else:
            self._zoom = max(0.5, self._zoom * 0.9)
        self._apply_size()
        event.accept()

    # ------------------------------------------------------------ 右键菜单
    def _build_menu(self) -> None:
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, _pos: Any) -> None:
        menu = QMenu(self)
        # 与主界面 HTML5 风格统一的菜单样式
        menu.setStyleSheet(_MENU_QSS)

        menu.addAction("设置日程", self._add_schedule)
        menu.addAction("设置提醒", self._add_reminder)

        act_greet = menu.addAction("定时问候")
        act_greet.setCheckable(True)
        act_greet.setChecked(self._greeting_on)
        act_greet.toggled.connect(self._toggle_greeting)

        menu.addAction("小窗对话", self._open_mini_chat)
        act_floating = menu.addAction("悬浮对话")
        act_floating.setCheckable(True)
        act_floating.setChecked(self._floating_on)
        act_floating.toggled.connect(self._toggle_floating)

        act_rest = menu.addAction("提醒休息")
        act_rest.setCheckable(True)
        act_rest.setChecked(self._rest_on)
        act_rest.toggled.connect(self._toggle_rest)

        act_focus = menu.addAction(
            "关闭专注模式" if self._focus_target_title else "专注模式（选择窗口）")
        act_focus.triggered.connect(
            self._disable_focus_mode if self._focus_target_title
            else self._setup_focus_mode)

        menu.addAction("专注助手", self._open_focus_assistant)

        menu.addSeparator()
        menu.addAction("返回主页", self._back_to_main)
        menu.addAction("隐藏桌宠", self.hide)
        menu.addAction("退出桌宠", self._quit_pet)
        # 菜单弹出前：临时隐藏小窗，避免遮挡菜单
        had_mini = self._mini_chat is not None and self._mini_chat.isVisible()
        if had_mini:
            self._mini_chat.hide()
        menu.exec(QCursor.pos())
        # 菜单关闭后：
        # - 用户通过「小窗对话」刚打开的小窗（可见）→ 保持显示（需求：点击即弹出）
        # - 悬浮对话勾选且小窗不可见 → 恢复常驻显示
        if self._mini_chat is not None and not self._mini_chat.isVisible():
            if self._floating_on:
                self._mini_chat.show()
                self._mini_chat.raise_()

    def _back_to_main(self) -> None:
        self._bus.show_main_requested.emit()

    def _open_focus_assistant(self) -> None:
        """专注助手：先设置专注时间 → 置顶倒计时窗 → 角色语气鼓励。

        功能由主界面承载（MainWindow._open_focus_assistant），
        桌宠右键菜单「专注助手」作为入口。
        """
        mw = self._main_window
        if mw is not None and hasattr(mw, "_open_focus_assistant"):
            mw._open_focus_assistant()

    def _quit_pet(self) -> None:
        # 退出桌宠时一并关闭小窗（需求：退出后小窗一起关闭）
        self._close_mini_chat()
        self.close()

    # ------------------------------------------------------------ 日程/提醒
    def _ask_task(self, title: str, label: str,
                  max_len: int = 120) -> Optional[Dict[str, Any]]:
        """一页式填写：时间 + 内容 + 每日循环（⑫，不分三次弹窗）。"""
        from PySide6.QtWidgets import (QDialog, QDateTimeEdit, QCheckBox,
                                       QLineEdit, QPushButton, QVBoxLayout,
                                       QHBoxLayout, QLabel)
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(_DIALOG_QSS)
        dialog.resize(460, 320)
        lay = QVBoxLayout(dialog)
        lay.addWidget(QLabel("选择时间（年月日 时:分:秒）："))
        editor = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        lay.addWidget(editor)
        lay.addWidget(QLabel(label))
        text_edit = QLineEdit()
        text_edit.setMaxLength(max_len)
        text_edit.setPlaceholderText("在此输入内容…")
        lay.addWidget(text_edit)
        loop_check = QCheckBox("每日循环")
        lay.addWidget(loop_check)
        row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        lay.addLayout(row)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return None
        content = text_edit.text().strip()
        if not content:
            return None
        return {
            "time_str": editor.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "text": content[:max_len],
            "loop": loop_check.isChecked(),
        }

    def _add_schedule(self) -> None:
        """设置日程：一页填写时间 + 日程工作 + 每日循环（⑫）。"""
        data = self._ask_task(
            "设置日程", "日程工作（例如：打开计算器 / 提醒我喝水）：")
        if not data:
            return
        self._reminders.append({"kind": "schedule", **data})
        self._show_alert("日程", f"已设置日程：{data['time_str']} · {data['text'][:30]}")

    def _add_reminder(self) -> None:
        """设置提醒：一页填写时间 + 内容（120 字内）+ 每日循环（⑫）。"""
        data = self._ask_task("设置提醒", "提醒内容（120 字内）：")
        if not data:
            return
        self._reminders.append({"kind": "reminder", **data})
        self._show_alert("提醒", f"已添加提醒：{data['time_str']} · {data['text'][:30]}")


    # ------------------------------------------------------------ 定时器
    def _start_timers(self) -> None:
        self._greet_timer = QTimer(self)
        self._greet_timer.timeout.connect(self._on_greeting_tick)
        self._greet_timer.start(30 * 60 * 1000)      # 30 分钟问候

        self._sleep_timer = QTimer(self)
        self._sleep_timer.timeout.connect(self._check_sleep)
        self._sleep_timer.start(60_000)              # 1 小时休息检查

        self._focus_timer = QTimer(self)
        self._focus_timer.timeout.connect(self._check_focus)
        self._focus_timer.start(10_000)          # 10 秒检查一次专注目标

        self._reminder_timer = QTimer(self)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start(20_000)

    def _toggle_greeting(self, on: bool) -> None:
        self._greeting_on = bool(on)
        if on:
            # 开启后 3 秒先问候一次，便于即时看到效果
            QTimer.singleShot(3000, self._on_greeting_tick)

    def _generate_greeting(self) -> None:
        """按角色性格 + 记忆内容生成报时问候（③）。"""
        sys_p = self._roles.role_system_prompt(self._role)
        user_name = (self._cfg.user_name() or "用户").strip() or "用户"
        sys_p += (f" 你的用户名字是「{user_name}」，"
                  f"请在问候时直接称呼这个名字。")
        ctx = self._memory.retrieve_before("主动问候", self._role, None)
        messages = [{
            "role": "system",
            "content": sys_p + " 现在是整点报时问候。请以你的性格对用户说一句"
                       "问候（不超过 30 字），并结合对用户的记忆，标注【情感】。",
        }]
        if ctx:
            messages.append({"role": "system", "content": "相关记忆：\n" + ctx})
        messages.append({"role": "user", "content": "请报时问候。"})
        worker = LLMWorker(messages, kind="main", stream=False, max_tokens=80)
        worker.request_finished.connect(lambda t: self._show_greeting(t))
        worker.request_error.connect(lambda _e: self._show_greeting(""))
        self._keep_worker(worker)
        worker.start()

    def _show_greeting(self, text: str) -> None:
        now = QDateTime.currentDateTime().toString("HH:mm")
        if not text or not text.strip():
            text = f"{now}，{random.choice(_GREETINGS)}"
        clean = self._roles.strip_emotion_tags(text)
        self.set_state("hello", duration_ms=4000)
        self._show_alert("定时问候", f"{now}，{clean[:120]}")

    def _toggle_rest(self, on: bool) -> None:
        self._rest_on = bool(on)
        self._sleep_reminded = False
        if on:
            self._check_sleep()

    def _toggle_floating(self, on: bool) -> None:
        self._floating_on = bool(on)
        self._cfg.set(on, "pet", "floating_chat")
        self._cfg.save()
        if on:
            # 勾选悬浮对话：打开前强制关闭旧的，再显示全新小窗（常驻）
            self._close_mini_chat()
            win = self._ensure_mini_chat()
            win.show()
            win.raise_()
            win.activateWindow()
            self._place_window(win)
            # 开启悬浮窗时：say1.gif（需求）
            self.set_state("say1", duration_ms=0)
        else:
            # 取消勾选：关闭小窗，不再一直显示
            self._close_mini_chat()

    def _ensure_mini_chat(self) -> "MiniChatWindow":
        """获取（必要时创建）唯一的小窗对话实例，并定位在屏幕内、避开角色图片。"""
        if self._mini_chat is None:
            self._mini_chat = MiniChatWindow(self, self._role)
        self._place_window(self._mini_chat)
        return self._mini_chat

    def _place_window(self, win: QWidget, margin: int = 10) -> None:
        """将 win 放置在桌宠附近且**永远在屏幕可用区域内**。

        小窗（MiniChatWindow）**大小固定**（不随位置缩放，需求）；
        默认放桌宠**正上方**（居中贴近角色图片），放不下才放侧面
        （先右侧、后左侧）。最后双向钳制在屏幕内。
        """
        if win is None:
            return
        screen = QApplication.primaryScreen()
        avail = (screen.availableGeometry()
                 if screen is not None else QRect(0, 0, 1920, 1080))
        w, h = win.width(), win.height()
        pet_x, pet_y = self.x(), self.y()
        pet_w, pet_h = self._pet_w(), self._pet_h()

        # 默认放桌宠上方（居中贴近角色图片）
        x = pet_x + (pet_w - w) // 2
        y = pet_y - h - margin
        # 上方空间不够 → 侧面（小窗大小固定，不缩放）
        if y < avail.top() + margin:
            x = pet_x + pet_w + margin
            y = pet_y
            if x + w > avail.right() - margin:
                x = pet_x - w - margin
        # 右侧放不下 → 放左侧
        if x + w > avail.right() - margin:
            x = pet_x - w - margin

        # 水平钳制（永远在屏幕内）
        x = min(max(x, avail.left() + 4), avail.right() - w - 4)
        # 垂直钳制
        y = min(max(y, avail.top() + 4), avail.bottom() - h - 4)
        win.move(int(x), int(y))

    def _close_mini_chat(self) -> None:
        """强制关闭小窗（销毁实例，保证下次打开是全新状态）。"""
        if self._mini_chat is not None:
            self._mini_chat.close()
            self._mini_chat = None

    # ------------------------------------------------------------ 专注模式（⑭）
    def _list_windows(self) -> list:
        """枚举可见顶层窗口（Windows 用 ctypes，其他平台返回空列表）。"""
        results = []
        if os.name == "nt":
            import ctypes
            user32 = ctypes.windll.user32

            def _cb(hwnd, _):
                if user32.IsWindowVisible(hwnd) \
                        and user32.GetWindowTextLengthW(hwnd) > 0:
                    buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, buf, 512)
                    title = buf.value.strip()
                    if title:
                        results.append((hwnd, title))
                return True

            ProcType = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                          ctypes.c_void_p)
            user32.EnumWindows(ProcType(_cb), 0)
        return results

    def _target_minimized(self) -> bool:
        """按标题查找目标窗口是否最小化/不可见。"""
        if os.name != "nt":
            return False
        import ctypes
        user32 = ctypes.windll.user32
        for hwnd, title in self._list_windows():
            if title == self._focus_target_title:
                return bool(user32.IsIconic(hwnd)) \
                    or not bool(user32.IsWindowVisible(hwnd))
        return True          # 目标窗口已关闭，视为离开

    def _setup_focus_mode(self) -> None:
        """弹窗选择当前任务窗口，确定后启用专注模式。"""
        from PySide6.QtWidgets import (QDialog, QListWidget, QPushButton,
                                       QVBoxLayout, QHBoxLayout, QLabel)
        wins = self._list_windows()
        dialog = QDialog(self)
        dialog.setWindowTitle("专注模式 - 选择任务窗口")
        dialog.setStyleSheet(_DIALOG_QSS)
        dialog.resize(420, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("选择当前要专注的任务窗口："))
        lst = QListWidget()
        titles = [t for _, t in wins] or ["（未检测到其他窗口）"]
        lst.addItems(titles)
        layout.addWidget(lst, 1)
        row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        layout.addLayout(row)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return
        sel = lst.currentItem()
        if sel is None or not sel.text().strip():
            return
        self._focus_target_title = sel.text().strip()
        # 立即生效：首次最小化就提示（不等 5 分钟节流）
        self._focus_last_warn = time.time() - 301
        self._focus_last_active = 0.0
        self._show_alert("专注模式", f"专注模式已开启：目标「{self._focus_target_title[:20]}」")
        # 同样的功能：专注助手倒计时窗也一并启动（先设置时间 → 悬浮倒计时）
        self._open_focus_assistant()

    def _disable_focus_mode(self) -> None:
        self._focus_target_title = ""
        self._show_alert("专注模式", "专注模式已关闭，放心休息~")
        # 一并关闭专注助手倒计时窗
        mw = self._main_window
        win = getattr(mw, "_focus_window", None)
        if win is not None:
            try:
                win.close()
            except Exception:  # noqa: BLE001
                pass

    def _generate_focus_encouragement(self) -> None:
        """按角色性格调用 API 生成鼓励专注的话，并使用 angry 图。"""
        sys_p = self._roles.role_system_prompt(self._role)
        user_name = (self._cfg.user_name() or "用户").strip() or "用户"
        sys_p += (f" 你的用户名字是「{user_name}」，"
                  f"督促时请直接称呼这个名字。")
        messages = [
            {"role": "system",
             "content": sys_p + " 用户偷偷切走了专注窗口。请以你的性格用简短（≤40字）"
                        "的话督促/鼓励用户回到任务上，并用【】标注情感。"},
            {"role": "user", "content": "请督促我回到任务上。"},
        ]
        worker = LLMWorker(messages, kind="main", stream=False, max_tokens=80)
        worker.request_finished.connect(
            lambda text: self._show_focus_encouragement(text))
        worker.request_error.connect(
            lambda _e: self._show_focus_encouragement(""))
        self._keep_worker(worker)
        worker.start()

    def _keep_worker(self, worker: Any) -> None:
        """保存 LLMWorker 引用，防止被 GC 中断线程；线程结束后自动清理。

        QThread 对象若无 Python 引用，可能在线程运行期间被垃圾回收，
        导致「QThread: Destroyed while thread is still running」且回调不触发。
        """
        if not hasattr(self, "_role_workers"):
            self._role_workers: List[Any] = []
        self._role_workers.append(worker)
        try:
            worker.finished.connect(lambda w=worker: self._drop_worker(w))
        except Exception:  # noqa: BLE001
            pass

    def _drop_worker(self, worker: Any) -> None:
        workers = getattr(self, "_role_workers", None)
        if workers and worker in workers:
            try:
                workers.remove(worker)
            except ValueError:
                pass

    def _show_focus_encouragement(self, text: str) -> None:
        clean = self._roles.strip_emotion_tags(text) if text else "回到任务上吧，我陪着你！"
        self.set_state("angry", duration_ms=4000)
        # 需求：专注模式弹白色圆角卡片（AlertPopup）展示提醒，不再用无底框气泡
        self._show_alert("专注提醒", clean)

    def _on_greeting_tick(self) -> None:
        if not self._greeting_on:
            return
        hour = QDateTime.currentDateTime().time().hour()
        if 22 <= hour or hour < 6:
            return
        if self._current_action == "sleep":
            return
        self.set_state("hello", duration_ms=4000)
        self._generate_greeting()

    def _check_sleep(self) -> None:
        """提醒休息：夜间 22 点 - 次日 6 点，每小时提醒一次（sleep.gif）。

        到时间后按角色性格生成提示词，并弹出悬浮小窗显示。
        """
        if not self._rest_on:
            return
        hour = QDateTime.currentDateTime().time().hour()
        sleeping = (hour >= 22) or (hour < 6)
        if not sleeping:
            self._sleep_reminded = False
            if self._current_action == "sleep":
                self.set_state("standing")
            return
        self.set_state("sleep")
        if not self._sleep_reminded:
            self._sleep_reminded = True
            fallback = f"已经 {hour} 点了，该休息啦~ 早点睡吧！"
            self._generate_role_message(
                "休息提醒",
                f"现在是夜间 {hour} 点，该休息了。请以你的性格催促我早点睡觉，"
                "语气要符合角色设定，不超过 60 字。",
                on_done=lambda t: self._show_alert("休息提醒", t or fallback))

    def _check_focus(self) -> None:
        """专注模式：目标任务窗口被最小化/关闭时，按角色性格输出鼓励（⑭）。"""
        if not getattr(self, "_focus_target_title", ""):
            return
        if not self._target_minimized():
            return
        now = time.time()
        if now - self._focus_last_warn > 300:
            self._focus_last_warn = now
            self._generate_focus_encouragement()

    def _check_reminders(self) -> None:
        """按时间到点触发日程/提醒（时间戳比较，避免秒级匹配遗漏）。

        到点后生成符合角色性格的提示词并弹出悬浮小窗。
        """
        if not self._reminders:
            return
        now = QDateTime.currentDateTime()
        now_ts = now.toSecsSinceEpoch()
        due = []
        for r in self._reminders:
            t = QDateTime.fromString(r["time_str"], "yyyy-MM-dd HH:mm:ss")
            if not t.isValid():
                continue
            ts = t.toSecsSinceEpoch()
            if now_ts >= ts and r.get("_fired_at") != ts:
                due.append(r)
                r["_fired_at"] = ts
        for r in due:
            if r["kind"] == "schedule":
                self._trigger_schedule(r["text"])
            else:
                self._trigger_reminder(r["text"])
            if r["loop"]:
                # 每日循环：顺延到明天同一时刻
                t = QDateTime.fromString(r["time_str"], "yyyy-MM-dd HH:mm:ss")
                r["time_str"] = t.addDays(1).toString("yyyy-MM-dd HH:mm:ss")
                r.pop("_fired_at", None)
            else:
                self._reminders.remove(r)

    def _trigger_reminder(self, text: str) -> None:
        """提醒到点：生成角色风格提示词并弹出悬浮小窗。"""
        self.set_state("say2", duration_ms=4000)
        self._generate_role_message(
            "提醒",
            f"提醒内容：{text}。请以你的性格提醒我这件事，语气符合角色设定，"
            "不超过 60 字。",
            on_done=lambda t: self._show_alert("提醒", t or f"提醒：{text}"))

    def _trigger_schedule(self, task: str) -> None:
        """日程到点：执行内置命令映射 + 生成角色风格提示词弹悬浮小窗。"""
        self.set_state("work", duration_ms=4000)
        cmd_map = {
            "计算器": "calc", "记事本": "notepad", "画图": "mspaint",
            "浏览器": "start https://www.baidu.com", "资源管理器": "explorer",
        }
        import shlex
        import subprocess
        executed = False
        for key, cmd in cmd_map.items():
            if key in task:
                try:
                    subprocess.Popen(shlex.split(cmd))
                except Exception:  # noqa: BLE001
                    pass
                executed = True
                break
        self._generate_role_message(
            "日程",
            f"日程安排：{task}。请以你的性格提醒我去执行这项日程，"
            "语气符合角色设定，不超过 60 字。",
            on_done=lambda t: self._show_alert(
                "日程" + ("（已执行）" if executed else ""),
                t or f"日程提醒：{task}"))

    # ------------------------------------------------------------ 角色风格提示
    def _generate_role_message(self, scene: str, hint: str,
                               on_done, fallback: str = "") -> None:
        """按角色性格 + 记忆生成一段符合角色风格的提示词（异步 LLM）。

        :param scene:   触发场景描述（传给模型的任务）
        :param hint:    提示语（拼入 system prompt 约束角色语气）
        :param on_done: 收到文本后的回调（text 可能为空，需自行兜底）
        """
        sys_p = self._roles.role_system_prompt(self._role)
        user_name = (self._cfg.user_name() or "用户").strip() or "用户"
        sys_p += (f" 你的用户名字是「{user_name}」，"
                  f"请直接称呼这个名字。")
        ctx = self._memory.retrieve_before(hint, self._role, None)
        messages = [{
            "role": "system",
            "content": sys_p + f" {hint} 请保持角色性格，用【】标注情感。",
        }]
        if ctx:
            messages.append({"role": "system", "content": "相关记忆：\n" + ctx})
        messages.append({"role": "user", "content": scene})
        worker = LLMWorker(messages, kind="main", stream=False, max_tokens=120)
        worker.request_finished.connect(lambda t: on_done(t))
        worker.request_error.connect(lambda _e: on_done(""))
        self._keep_worker(worker)
        worker.start()

    def _show_alert(self, title: str, text: str) -> None:
        """弹出悬浮提醒小窗（桌宠上方，跟随桌宠移动）。"""
        if not text or not text.strip():
            return
        clean = self._roles.strip_emotion_tags(text)
        popup = AlertPopup(self, self._role, title, clean)
        popup.move(self.x() + 10, max(0, self.y() - popup.height() - 8))
        popup.show()
        self._alert_popup = popup


    # ------------------------------------------------------------ 拖拽/悬浮
    def mousePressEvent(self, event: Any) -> None:  # noqa: D102
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._prev_action = self._current_action   # 记录拖放前状态
            self.set_state("run")
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()

    def _clamp_pet_pos(self, pos: QPoint) -> QPoint:
        """将桌宠位置钳制在屏幕内（可遮盖底部任务栏 → 用 screenGeometry）。"""
        screen = QApplication.primaryScreen()
        sg = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        w, h = self.width(), self.height()
        pos.setX(min(max(pos.x(), sg.left()), sg.right() - w))
        pos.setY(min(max(pos.y(), sg.top()), sg.bottom() - h))
        return pos

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: D102
        if self._dragging:
            pos = event.globalPosition().toPoint() - self._drag_offset
            # 角色图片不超出屏幕（可遮盖底部任务栏 → 用 screenGeometry）
            self.move(self._clamp_pet_pos(pos))
            # 拖动时悬浮对话小窗 / 提醒窗 / 气泡跟随桌宠一起移动
            self._sync_follow_windows()
            event.accept()

    def _sync_follow_windows(self) -> None:
        """拖动桌宠时，悬浮对话小窗 / 提醒窗跟随并保持在屏幕内。"""
        for win in (getattr(self, "_mini_chat", None),
                    getattr(self, "_alert_popup", None)):
            if win is not None and win.isVisible():
                self._place_window(win)

    def closeEvent(self, event: Any) -> None:  # noqa: D102
        """桌宠关闭时一并关闭小窗 / 提醒窗（需求：退出后小窗一起关闭）。"""
        self._close_mini_chat()
        if getattr(self, "_alert_popup", None) is not None:
            self._alert_popup.close()
        super().closeEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: D102
        if self._dragging:
            self._dragging = False
            # 拖放结束后恢复拖放前的状态（⑪ 不切回默认图）
            self.set_state(getattr(self, "_prev_action", "standing"))
        event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: D102
        self._open_mini_chat()

    def enterEvent(self, event: Any) -> None:  # noqa: D102
        # 悬浮对话已改为「勾选即常驻显示」，不再由鼠标悬停控制弹出
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: D102
        super().leaveEvent(event)

    def _open_mini_chat(self, _checked: bool = False) -> None:
        """小窗对话：每次打开前强制关闭旧的（确保干净），再显示新实例。"""
        # 打开前强制关闭旧的（需求：悬浮窗口默认不打开、每次打开前强制关闭）
        self._close_mini_chat()
        win = self._ensure_mini_chat()
        win.show()
        win.raise_()
        win.activateWindow()
        self._place_window(win)

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.transparent)
        painter.drawRect(self.rect())
        painter.end()


class _FrameWidget(QWidget):
    """底框图片控件：图片按比例压缩至**完全显示**（KeepAspectRatio 居中，
    不截断不变形）；content_rect() 返回图片中心指定比例的区域供子控件布局。"""

    def __init__(self, img_path: "Path", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pix = QPixmap(str(img_path)) if img_path.exists() else QPixmap()
        self._img_rect = QRect()
        self._layout_cb: Optional[Any] = None   # paintEvent 后回调（子控件重定位）

    def set_layout_callback(self, cb: Any) -> None:
        self._layout_cb = cb

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._pix.isNull() or self._pix.width() <= 0 or self._pix.height() <= 0:
            painter.end()
            return
        r = self.rect()
        w, h = self._pix.width(), self._pix.height()
        scale = min(r.width() / w, r.height() / h)
        dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
        x, y = (r.width() - dw) // 2, (r.height() - dh) // 2
        self._img_rect = QRect(x, y, dw, dh)
        painter.drawPixmap(self._img_rect, self._pix)
        painter.end()
        # 图片区域计算完成后，重定位子控件（保证文字/输入框精确在图片中心比例内）
        if self._layout_cb is not None:
            self._layout_cb()

    def content_rect(self, ratio: float = 0.8) -> QRect:
        """图片中心 ratio 区域（相对本控件坐标），子控件布局用。"""
        r = self._img_rect
        if r.isEmpty() or r.width() <= 0 or r.height() <= 0:
            r = self.rect()
        w, h = int(r.width() * ratio), int(r.height() * ratio)
        return QRect(r.x() + (r.width() - w) // 2,
                     r.y() + (r.height() - h) // 2, w, h)


class MiniChatWindow(QWidget):
    """200 字极简对话小窗（输入 say1.gif / 输出 say2.gif）。"""

    def __init__(self, pet: "DesktopPet", role: str) -> None:
        super().__init__(None)
        self._pet = pet
        self._role = role
        self._pool = LLMClientPool.instance()
        self._memory = MemoryPipeline.instance()
        self._roles = RoleManager.instance()
        self._bus = SignalBus.instance()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 高度只含两图高度 + 中间留空（无标题栏）
        aP, tP = QPixmap(str(_img("answer.png"))), QPixmap(str(_img("tell.png")))
        aW = aP.width() if not aP.isNull() else 450
        aH = aP.height() if not aP.isNull() else 338
        tW = tP.width() if not tP.isNull() else 413
        tH = tP.height() if not tP.isNull() else 103
        # 保存图片原始尺寸，供等比缩放（需求：窗口过大时调整扩缩）
        self._img_a_w, self._img_a_h = aW, aH
        self._img_t_w, self._img_t_h = tW, tH
        self._win_w = 360
        self._answer_h = max(1, int(self._win_w * aH / aW))   # answer 图按宽度比例高度
        self._tell_h = max(1, int(self._win_w * tH / tW))     # tell 图按宽度比例高度
        self._gap = 8                                         # 两图中间留空
        self.setFixedSize(self._win_w,
                          self._answer_h + self._gap + self._tell_h)
        self._build_ui()

    def resize_by_width(self, new_w: int) -> None:
        """按宽度等比调整窗口尺寸与图片布局（需求：窗口过大时缩放适配）。"""
        new_w = max(200, int(new_w))
        self._win_w = new_w
        self._answer_h = max(1, int(new_w * self._img_a_h / self._img_a_w))
        self._tell_h = max(1, int(new_w * self._img_t_h / self._img_t_w))
        self.setFixedSize(new_w, self._answer_h + self._gap + self._tell_h)
        if hasattr(self, "_answer_frame"):
            self._answer_frame.setFixedHeight(self._answer_h)
            self._input_frame.setFixedHeight(self._tell_h)
            self._layout_mini()

    def resize_by_height(self, new_h: int) -> None:
        """按高度等比调整窗口尺寸（等比缩放宽度）。"""
        total = self._answer_h + self._gap + self._tell_h
        if total <= 0:
            return
        self.resize_by_width(max(200, int(self._win_w * new_h / total)))


    def closeEvent(self, event: Any) -> None:  # noqa: D102
        """用户点击 X 关闭：记录标志；等待并停止 LLM 工作线程（防闪退）。"""
        self._pet._mini_chat_closed = True
        worker = getattr(self, "_worker", None)
        if worker is not None:
            try:
                worker.wait(1200)   # 等待 LLM 线程结束，避免 QThread 被销毁时崩溃
            except Exception:  # noqa: BLE001
                pass
            self._worker = None
        # 关闭悬浮窗 → 切回默认图（standing.gif，需求：只有关闭才切回）
        try:
            self._pet.set_state("standing")
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._gap)

        # 关闭按钮：用 /img/close.png 图标，悬浮在右上角（不占窗口高度）
        close_btn = QPushButton(self)
        close_btn.setIcon(QIcon(str(_img("close.png"))))
        close_btn.setIconSize(QSize(30, 30))
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent; border:none;}"
            "QPushButton:hover{background:rgba(225,29,72,0.12);"
            " border-radius:8px;}")
        close_btn.clicked.connect(self.close)
        self._close_btn = close_btn

        # 输出区：answer.png 底框 **完整显示**（KeepAspectRatio 压缩不截断），
        # 文字子控件由 _layout_mini 定位在图片中心 80% 内
        self._answer_frame = _FrameWidget(_img("answer.png"))
        self._answer_frame.setFixedHeight(self._answer_h)
        self._bubble = QLabel("你好，我是你的桌面助手~", self._answer_frame)
        self._bubble.setWordWrap(True)
        self._bubble.setAlignment(Qt.AlignCenter)
        self._bubble.setStyleSheet(
            "QLabel{color:#2b2b33; font-size:13px; background:transparent;}")
        layout.addWidget(self._answer_frame, 1)

        # 输入区：tell.png 底框 **完整显示**，输入框与发送按钮在图片中心区域
        self._input_frame = _FrameWidget(_img("tell.png"))
        self._input_frame.setFixedHeight(self._tell_h)
        self._input_container = QWidget(self._input_frame)
        self._input_container.setStyleSheet("background:transparent;")
        input_lay = QHBoxLayout(self._input_container)
        input_lay.setContentsMargins(8, 4, 8, 4)
        input_lay.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("说点什么…")
        self._input.setMaxLength(100)
        self._input.returnPressed.connect(self._send)
        # 打字时：say1.gif（需求）
        self._input.textChanged.connect(
            lambda _t: self._pet.set_state("say1", duration_ms=0))
        self._input.setStyleSheet(
            "QLineEdit{background:transparent; border:none; font-size:13px;"
            " color:#2b2b33;}")
        # 发送按钮：白色文字 + 系统主色（#6c8ef5），与主界面 HTML5 风格一致
        send_btn = QPushButton("发送")
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet(
            "QPushButton{background:#6c8ef5; color:white; border:none;"
            " border-radius:7px; padding:5px 16px; font-size:13px; font-weight:600;}"
            "QPushButton:hover{background:#8fb0ff;}"
            "QPushButton:pressed{background:#4a6fd4;}")
        send_btn.clicked.connect(self._send)
        input_lay.addWidget(self._input, 1)
        input_lay.addWidget(send_btn)
        layout.addWidget(self._input_frame)

        # 图片绘制完成后重定位子控件（保证内容精确在图片中心比例内）
        self._answer_frame.set_layout_callback(self._layout_mini)
        self._input_frame.set_layout_callback(self._layout_mini)
        self._layout_mini()

    def _layout_mini(self) -> None:
        """定位子控件：输出文字在 answer 中心 80%、输入框/按钮在 tell 中心区域，
        关闭按钮悬浮在右上角（raise_ 防止被底框图片覆盖，修复点击失效）。"""
        self._bubble.setGeometry(self._answer_frame.content_rect(0.8))
        self._input_container.setGeometry(self._input_frame.content_rect(0.95))
        if hasattr(self, "_close_btn"):
            # 关闭按钮上移 10px（需求：y 从 6 → -4）
            self._close_btn.move(self.width() - self._close_btn.width() - 6, -4)
            self._close_btn.raise_()


    def resizeEvent(self, event: Any) -> None:  # noqa: D102
        super().resizeEvent(event)
        self._layout_mini()

    def _send(self) -> None:
        text = self._input.text().strip()[:100]
        if not text:
            return
        self._input.clear()
        # 发送后强制去除光标（需求：再次点击对话框才能输入）
        self._input.clearFocus()
        self._bubble.setText("…")
        # 发起提问等待文字输出：say2.gif（需求：打字时 say1、提问等待输出时 say2）
        self._pet.set_state("say2", duration_ms=0)
        self._done = False   # 开始新一轮对话
        self._stream_buffer: List[str] = []
        self._stream_timer: Optional[QTimer] = None
        # 主线程快速构建 messages（读角色卡缓存，无 ChromaDB 检索 → 不阻塞不假死），
        # LLMWorker 在后台 QThread 流式请求（即时输出）
        try:
            messages = self._build_messages(text)
        except Exception as exc:  # noqa: BLE001
            self._bubble.setText(f"出错：{exc}")
            return
        if not messages:
            self._bubble.setText("出错：无法构建对话消息")
            return
        self._worker = LLMWorker(messages, kind="main", stream=True, max_tokens=200)
        self._worker.stream_chunk.connect(self._on_stream)
        self._worker.request_finished.connect(self._on_done)
        self._worker.request_error.connect(
            lambda e: self._bubble.setText(f"出错：{e[:80]}"))
        self._worker.start()

    def _on_stream(self, chunk: str) -> None:
        """流式输出：累积后节流刷新（50ms），避免每 token 重排卡顿（需求 2）。"""
        if getattr(self, "_done", False):
            return   # 已完成，忽略迟到片段
        if not hasattr(self, "_stream_buffer"):
            self._stream_buffer = []
        self._stream_buffer.append(chunk)
        if not hasattr(self, "_stream_timer") or self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setSingleShot(True)
            self._stream_timer.timeout.connect(self._flush_stream)
            self._stream_timer.start(50)
        else:
            self._stream_timer.start(50)   # 重置计时

    def _flush_stream(self) -> None:
        """节流刷新气泡；输出中保持 say2.gif（需求）。"""
        if getattr(self, "_done", False):
            return   # 已完成，不再刷新/清空气泡
        if not hasattr(self, "_stream_buffer"):
            return
        text = "".join(self._stream_buffer)
        # 不截断：API 已用提示词限制 100 字（需求 3），仅移除可能的情绪标签
        clean = self._roles.strip_emotion_tags(text)
        self._bubble.setText(clean)
        # 显示正在输出内容时：say2.gif（需求）
        self._pet.set_state("say2", duration_ms=0)
        self._stream_timer = None

    def _build_messages(self, text: str) -> List[Dict[str, Any]]:
        # 主线程快速构建（读角色卡缓存），不做 ChromaDB 记忆检索，
        # 避免阻塞 UI / 检索异常导致假死与无输出（需求修复）
        sys_p = self._roles.role_system_prompt(self._role)
        user_name = (self._pet._cfg.user_name() or "用户").strip() or "用户"
        sys_p += (f" 你的用户名字是「{user_name}」，"
                  f"请在对话中直接称呼这个名字。")
        # 需求 3：不输出情绪标签；用 API 提示词要求总结在 100 字以内（不截断）
        messages: List[Dict[str, Any]] = [{
            "role": "system",
            "content": sys_p + " 请用简洁的中文回答，把回复总结控制在 100 字以内，"
                       "不要输出任何情绪标签。",
        }]
        messages.append({"role": "user", "content": text})
        return messages

    def _on_done(self, reply: str) -> None:
        # 不截断：API 已限制 100 字，仅移除可能的情绪标签（需求 3）
        clean = self._roles.strip_emotion_tags(reply)
        self._bubble.setText(clean)
        self._memory.archive_after("", clean, self._role, None)
        self._done = True   # 完成标记：停止后续流式刷新（避免清空气泡/切回 say2）
        self._stream_buffer = []
        if hasattr(self, "_stream_timer") and self._stream_timer is not None:
            try:
                self._stream_timer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._stream_timer = None
        if getattr(self, "_builder", None) is not None:
            self._builder = None
        # 全部输出完成：保持 say1.gif（不自动回退 standing，需求：只有关闭悬浮窗才切回）
        self._pet.set_state("say1", duration_ms=0)


class AlertPopup(QWidget):
    """悬浮提醒小窗：显示角色风格的事件提示（日程/提醒/休息/专注）。

    - 无边框置顶半透明小窗；白色圆角卡片底框由 paintEvent 手工绘制，
      不依赖 QSS 背景渲染（避免半透明窗口在部分环境下不显示底框）；
    - 右上角可手动关闭；默认 20 秒后自动渐隐消失；
    - 显示角色立绘小头像 + 标题 + 内容。
    """

    def __init__(self, pet: "DesktopPet", role: str, title: str, text: str,
                 auto_close_ms: int = 20000) -> None:
        super().__init__(None)
        self._pet = pet
        self._role = role
        self._roles = RoleManager.instance()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(300, 200)
        self._build_ui(title, text)
        if auto_close_ms > 0:
            QTimer.singleShot(auto_close_ms, self._fade_close)

    def _build_ui(self, title: str, text: str) -> None:
        # 卡片白色圆角底由 paintEvent 绘制，这里只负责内容布局与文字/按钮样式
        self.setStyleSheet(_ALERT_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        avatar = QLabel()
        avatar.setFixedSize(40, 40)
        avatar.setScaledContents(True)
        pm = QPixmap()
        portrait = self._roles.portrait_path(self._role)
        if portrait and Path(portrait).exists():
            pm.load(portrait)
        if not pm.isNull():
            avatar.setPixmap(pm)
        else:
            avatar.setText("AI")
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet("color:#6c8ef5; font-weight:600; font-size:13px;")
        head.addWidget(avatar)

        name = QLabel(f"{self._roles.display_name(self._role)} · {title}")
        name.setObjectName("alertTitle")
        head.addWidget(name, 1)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("alertClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn)
        layout.addLayout(head)

        body = QLabel(text)
        body.setObjectName("alertBody")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(body, 1)

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        """手工绘制白色圆角卡片底，保证半透明窗口也能稳定显示底框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        painter.setPen(QPen(QColor("#e5e7f0"), 1))
        painter.setBrush(QColor(255, 255, 255, 247))
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)

    def _fade_close(self) -> None:
        """自动关闭前渐隐。"""
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(600)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self.close)
        anim.start()
        self._fade_anim = anim


