# -*- coding: utf-8 -*-
"""统一风格的提示/确认对话框（与主界面 HTML5 风格一致）。

背景：主程序各模块原先直接调用 QMessageBox.warning / information / question，
弹窗样式与主界面不一致，且字体随 QSS 缩放可能产生放大/缩小/锯齿。
本模块提供统一入口：字体固定为整数像素（13px）不缩放无锯齿；
样式复刻主界面白卡 + 主题色主按钮 + 幽灵按钮。

用法：
    from utils.styled_msg import styled_confirm, styled_info, styled_warning
    if styled_confirm(self, "删除日记", "确定删除《xxx》吗？", danger=True):
        ...
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCENT_DEFAULT = "#6c8ef5"
FONT_NAME = "Microsoft YaHei UI"

_THEME = {
    "accent": ACCENT_DEFAULT,
    "text_dark": "#2b2b33",
    "text_mid": "#6b7280",
    "border": "#e5e7f0",
    "danger": "#d64545",
}


def _load_theme() -> dict:
    theme = dict(_THEME)
    try:
        cfg = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
        accent = str((cfg.get("ui") or {}).get("accent") or ACCENT_DEFAULT)
        theme["accent"] = accent
    except Exception:  # noqa: BLE001
        pass
    return theme


def _lighten(hex_color: str, amt: int) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    def _ch(n: int) -> int:
        return max(0, min(255, n + amt))
    try:
        return "#{:02x}{:02x}{:02x}".format(
            _ch(int(h[0:2], 16)), _ch(int(h[2:4], 16)), _ch(int(h[4:6], 16)))
    except ValueError:
        return hex_color


def _font() -> "QFont":
    """固定整数像素字体（13px，不缩放、无锯齿）。"""
    from PySide6.QtGui import QFont
    f = QFont(FONT_NAME)
    f.setPixelSize(13)
    return f


def _msgbox_qss(theme: dict) -> str:
    """QMessageBox 风格：白卡 + 主题色默认按钮 + 幽灵次按钮，固定字体。"""
    a = theme["accent"]
    return f"""
QMessageBox {{
    background: rgba(255,255,255,0.97);
    border: 1px solid {theme['border']};
    border-radius: 14px;
}}
QMessageBox QLabel {{
    color: {theme['text_dark']};
    font-size: 13px;
    font-family: "{FONT_NAME}", "Microsoft YaHei", sans-serif;
}}
QMessageBox QPushButton {{
    background: rgba(255,255,255,0.85);
    color: {theme['text_dark']};
    border: 1px solid {theme['border']};
    border-radius: 12px;
    padding: 7px 18px;
    font-size: 13px;
    font-family: "{FONT_NAME}", "Microsoft YaHei", sans-serif;
}}
QMessageBox QPushButton:hover {{
    border-color: {a}; color: {a};
    background: rgba(108,142,245,0.08);
}}
QMessageBox QPushButton:default {{
    background: {a}; color: white; border: none; font-weight: 600;
}}
QMessageBox QPushButton:default:hover {{
    background: {_lighten(a, 18)};
}}
QMessageBox QPushButton#dangerBtn {{
    background: {theme['danger']}; color: white; border: none; font-weight: 600;
}}
QMessageBox QPushButton#dangerBtn:hover {{ background: #ef6a6a; }}
"""


def styled_box(parent, title: str, text: str, icon: str = "information",
               buttons=None, default=None, danger_buttons=()) -> "QMessageBox":
    """构建已套用统一风格与固定字体的 QMessageBox，返回后调用 exec()。"""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setFont(_font())
    icon_map = {
        "information": QMessageBox.Information,
        "warning": QMessageBox.Warning,
        "critical": QMessageBox.Critical,
        "question": QMessageBox.Question,
    }
    box.setIcon(icon_map.get(icon, QMessageBox.Information))
    if buttons is not None:
        box.setStandardButtons(buttons)
        if default is not None:
            box.setDefaultButton(default)
    theme = _load_theme()
    box.setStyleSheet(_msgbox_qss(theme))
    for btn_id in danger_buttons:
        btn = box.button(btn_id)
        if btn is not None:
            btn.setObjectName("dangerBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
    return box


def styled_info(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    box = styled_box(parent, title, text, icon="information",
                     buttons=QMessageBox.Ok, default=QMessageBox.Ok)
    box.exec()


def styled_warning(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    box = styled_box(parent, title, text, icon="warning",
                     buttons=QMessageBox.Ok, default=QMessageBox.Ok)
    box.exec()


def styled_critical(parent, title: str, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    box = styled_box(parent, title, text, icon="critical",
                     buttons=QMessageBox.Ok, default=QMessageBox.Ok)
    box.exec()


def styled_question(parent, title: str, text: str,
                    default_no: bool = True, danger: bool = False) -> bool:
    """是/否确认（删除类 danger=True 使确认按钮红色）。"""
    from PySide6.QtWidgets import QMessageBox

    yes = QMessageBox.Yes
    no = QMessageBox.No
    box = styled_box(
        parent, title, text, icon="question",
        buttons=yes | no,
        default=no if default_no else yes,
        danger_buttons=(yes,) if danger else ())
    yb = box.button(yes)
    nb = box.button(no)
    if yb is not None:
        yb.setText("确认" if not danger else "删除")
    if nb is not None:
        nb.setText("取消")
    return box.exec() == QMessageBox.Yes


def styled_confirm(parent, title: str, text: str, danger: bool = False,
                   ok_text: str = "确认", cancel_text: str = "取消") -> bool:
    """HTML5 卡片式确认对话框（无边框白卡 + 投影，与主界面卡片一致）。

    danger=True 时主按钮为危险红（删除类操作）。
    返回 True 表示用户确认。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
        QPushButton, QVBoxLayout,
    )

    theme = _load_theme()
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dlg.setAttribute(Qt.WA_TranslucentBackground)
    dlg.setFixedSize(460, 210)
    dlg.setModal(True)

    card = QFrame(dlg)
    card.setObjectName("cardWidget")
    card.setStyleSheet(
        "QFrame#cardWidget { background: rgba(255,255,255,0.97);"
        " border: 1px solid #e5e7f0; border-radius: 14px; }")
    eff = QGraphicsDropShadowEffect(card)
    eff.setBlurRadius(18)
    eff.setOffset(0, 4)
    eff.setColor(QColor(0, 0, 0, 55))
    card.setGraphicsEffect(eff)
    card.setGeometry(10, 10, dlg.width() - 20, dlg.height() - 20)

    outer = QVBoxLayout(card)
    outer.setContentsMargins(22, 16, 22, 14)
    outer.setSpacing(10)

    head = QLabel(title)
    head.setObjectName("sectionTitle")
    head.setFont(_font())
    outer.addWidget(head)

    msg = QLabel(text)
    msg.setWordWrap(True)
    msg.setFont(_font())
    msg.setStyleSheet(f"color:{theme['text_mid']}; background:transparent;")
    outer.addWidget(msg, 1)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)
    btn_row.addStretch(1)

    ok = QPushButton(ok_text)
    ok.setCursor(Qt.PointingHandCursor)
    ok.setFont(_font())
    if danger:
        ok.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.85);color:#d64545;"
            "border:1px solid #f0d2d2;border-radius:12px;padding:7px 18px;"
            "font-size:13px;}"
            "QPushButton:hover{background:#fdeaea;border-color:#d64545;color:#d64545;}")
    else:
        ok.setStyleSheet(
            f"QPushButton{{background:{theme['accent']};color:white;border:none;"
            f"border-radius:12px;padding:7px 18px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{_lighten(theme['accent'], 18)};}}")
    ok.clicked.connect(dlg.accept)
    btn_row.addWidget(ok)

    cancel = QPushButton(cancel_text)
    cancel.setCursor(Qt.PointingHandCursor)
    cancel.setFont(_font())
    cancel.setStyleSheet(
        "QPushButton{background:rgba(255,255,255,0.85);color:#2b2b33;"
        "border:1px solid #e5e7f0;border-radius:12px;padding:7px 18px;"
        "font-size:13px;}"
        f"QPushButton:hover{{border-color:{theme['accent']};color:{theme['accent']};"
        "background:rgba(108,142,245,0.08);}")
    cancel.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel)

    outer.addLayout(btn_row)
    return dlg.exec() == QDialog.Accepted

