"""
skill_popup.py — @技能 唤醒弹窗（SkillPopup）

无边框 + 投影 + 圆角列表的顶层弹窗。焦点始终留在输入框，
键盘导航（上下 / 回车 / Esc）由 MainWindow.eventFilter 统一劫持，
本控件只负责列表展示与鼠标点击。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QGraphicsDropShadowEffect,
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
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

_ITEM_H = 36          # 兜底行高（实际以 sizeHintForRow 的真实渲染行高为准）
_MAX_VISIBLE = 8      # 最多同时可见条目数
_POPUP_MIN_W = 260    # 弹窗最小宽度
_POPUP_MAX_W = 520    # 弹窗最大宽度（内容更宽时出横向滚动条）


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
        # 文本不用省略号截断：宽度足够就完整显示，超出上限则横向滚动查看
        self._list.setTextElideMode(Qt.ElideNone)
        self._list.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self._skills: List[Dict[str, Any]] = []
        self._labels: List[str] = []      # 各条目展示文本（计算内容宽度用）
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
            self._labels.append(label)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, skill)
            it.setToolTip(desc)
            self._list.addItem(it)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """按内容计算弹窗尺寸（修复重叠错乱 / 显示不全 / 点错行）。

        旧实现的问题：
          * 行高写死 36px，与真实渲染行高（约 31px，随 DPI/字体变化）不符
            → 列表底部空白、最后一行被裁，视觉与命中区域错位；
          * 宽度交给 adjustSize()，与最长条目无关（内容 488px 弹窗只有 274px）
            → 文字被截断显示不全；
          * 半透明无边框窗口**边显示边改大小**会留下残影（旧一帧的列表叠在新一帧上）
            → 看起来行与行重叠、点到的行和看到的行不是同一个。
        """
        row_h = self._list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = _ITEM_H
        rows = max(1, min(self._list.count(), _MAX_VISIBLE))

        # 内容宽度用字体度量现算：QListView.sizeHintForColumn 在内容远超视口时
        # 只度量可见行，会得到被截断的假宽度（导致超长条目既不换行也不可滚动）
        fm = self._list.fontMetrics()
        content_w = 0
        for label in self._labels:
            content_w = max(content_w, fm.horizontalAdvance(label))
        content_w += 40          # ::item 左右 padding 14×2 + margin 6×2
        lm = self.layout().contentsMargins()
        frame_w = lm.left() + lm.right() + 2      # + 左右边框
        frame_h = lm.top() + lm.bottom() + 2      # + 上下边框

        w = max(_POPUP_MIN_W, min(_POPUP_MAX_W, content_w + frame_w))
        h = rows * row_h + frame_h
        # 内容超宽/超行数时滚动条会占掉一点空间，提前补上避免最后一行被裁
        if content_w + frame_w > _POPUP_MAX_W:
            h += self._list.horizontalScrollBar().sizeHint().height()
        if self._list.count() > _MAX_VISIBLE:
            w += self._list.verticalScrollBar().sizeHint().width()
            w = min(w, _POPUP_MAX_W + 24)

        size = QSize(w, h)
        if self.isVisible() and size != self.size():
            # 关键：改大小前先隐藏一拍（同一事件循环内马上会重新 show），
            # 避免半透明窗口拉伸残影导致的「重叠 / 点错行」
            self.hide()
        self.setFixedSize(size)

    def show_at(self, pos: Any) -> None:
        """移动到全局坐标 pos 显示（自动防越界、盖住输入框时上移）。"""
        if self._list.count() == 0:
            self.hide()
            return
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
