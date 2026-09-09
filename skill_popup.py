"""
skill_popup.py — @技能 唤醒弹窗（SkillPopup）

无边框 + 投影 + 圆角列表的顶层弹窗。焦点始终留在输入框，
键盘导航（上下 / 回车 / Esc）由 MainWindow.eventFilter 统一劫持，
本控件只负责列表展示与鼠标点击。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

_ACCENT = "#6c8ef5"
_BORDER = "#e5e7f0"
_TEXT_DARK = "#2b2b33"

# 与主界面 HTML5 风格一致的弹窗样式
# 需求：弹窗外框与列表背景改为纯白不透明 #ffffff，
# 避免悬浮在聊天区上方时与聊天显示框文字透出叠加。
_POPUP_QSS = f"""
QFrame#skillPopup {{
    background: #ffffff;
    border: 1px solid {_BORDER};
    border-radius: 12px;
}}
QListWidget#skillList {{
    background: #ffffff;
    border: none;
    outline: none;
    font-size: 13px;
    color: {_TEXT_DARK};
}}
QListWidget#skillList::item {{
    padding: 7px 14px;
    border-radius: 8px;
    margin: 2px 6px;
}}
QListWidget#skillList::item:selected {{
    background: rgba(108,142,245,0.16);
    color: {_ACCENT};
}}
QListWidget#skillList::item:hover {{
    background: rgba(108,142,245,0.08);
}}
"""

_ITEM_H = 36          # 单条技能行高
_MAX_VISIBLE = 8      # 最多同时可见条目数


class SkillPopup(QFrame):
    """技能唤醒弹窗控件。"""

    activated = Signal(object)   # 鼠标点击选中技能（携带技能 dict）

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("skillPopup")
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_POPUP_QSS)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        self._list = QListWidget()
        self._list.setObjectName("skillList")
        self._list.setFocusPolicy(Qt.NoFocus)   # 焦点留在输入框
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self._skills: List[Dict[str, Any]] = []
        self.hide()

    # ------------------------------------------------------------ 数据
    def set_skills(self, items: List[Dict[str, Any]]) -> None:
        """刷新列表项。items 元素须含 name / trigger / description。

        开关型工具（kind=switch）在名称前加「[开关]」标记，提示可组合开启。
        """
        self._skills = items
        self._list.clear()
        for skill in items:
            name = str(skill.get("name") or skill.get("trigger") or "?")
            desc = str(skill.get("description") or "")
            badge = "[开关] " if skill.get("kind") == "switch" else ""
            label = badge + name + (f"　— {desc[:22]}" if desc else "")
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, skill)
            it.setToolTip(desc)
            self._list.addItem(it)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._list.setFixedHeight(
            min(self._list.count(), _MAX_VISIBLE) * _ITEM_H + 8)

    def show_at(self, pos: Any) -> None:
        """移动到全局坐标 pos 显示（自动防越界、盖住输入框时上移）。"""
        if self._list.count() == 0:
            self.hide()
            return
        self.adjustSize()
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is None:
            self.show()
            self.raise_()
            return
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = max(geo.left(), pos.x())
        y = pos.y()
        if x + w > geo.right():
            x = geo.right() - w
        if y + h > geo.bottom():
            y = max(geo.top(), pos.y() - h - 12)
        self.move(x, y)
        self.show()
        self.raise_()

    # ------------------------------------------------------------ 交互
    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if isinstance(data, dict):
            self.activated.emit(data)

    def move_selection(self, delta: int) -> None:
        """键盘上下移动选中项（delta 为 -1 / +1）。"""
        row = self._list.currentRow()
        new_row = row + delta
        if 0 <= new_row < self._list.count():
            self._list.setCurrentRow(new_row)

    def selected_skill(self) -> Optional[Dict[str, Any]]:
        """当前选中技能 dict；无选中返回 None。"""
        it = self._list.currentItem()
        if it is None:
            return None
        data = it.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def current_count(self) -> int:
        """当前列表条目数（供测试/状态判断）。"""
        return self._list.count()
