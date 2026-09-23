"""
media_viewer.py — 全屏媒体查看器（V2-F1）

参考 HanaAgent（openhanako）desktop MediaViewer 交互模型，PySide6 实现：
- 全屏暗色遮罩，无边框；
- 图片：滚轮缩放（以光标为中心）、按住拖拽平移、双击还原；
- 导航：左右方向键 / 界面按钮在同目录或同会话相邻媒体间切换；
- 键盘：+ / - / 0 缩放，Esc / 关闭按钮退出；
- 支持图片（png/jpg/jpeg/webp/gif/bmp）。

线程纪律：本类仅在主线程创建与操作（QWidget）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QKeyEvent, QPixmap, QWheelEvent, QPainter, QColor
from PySide6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from utils.utf8 import force_utf8_stdio  # noqa: F401 - 风格一致

logger = logging.getLogger("AI_DeskMate.MediaViewer")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# 主项目风格常量（GUI 统一）
_ACCENT = "#6c8ef5"
_TEXT_LIGHT = "#9aa0ac"
_DANGER = "#d64545"


def is_image_path(path: str) -> bool:
    """判断路径是否为支持的图片文件。"""
    return Path(str(path)).suffix.lower() in _IMAGE_EXTS


def _btn_qss(base: str = _ACCENT) -> str:
    return (f"QPushButton {{ background: rgba(255,255,255,0.12); color: #fff;"
            f" border: none; border-radius: 8px; padding: 6px 14px;"
            f" font-size: 13px; }}"
            f"QPushButton:hover {{ background: {base}; }}"
            f"QPushButton:pressed {{ background: {base}; }}")


class _ImageView(QGraphicsView):
    """可缩放平移的图片视图。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: Optional[QGraphicsPixmapItem] = None
        self._zoom = 1.0
        self._dragging = False
        self._drag_last = QPointF()
        self.setBackgroundBrush(QColor(18, 18, 22))
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setDragMode(QGraphicsView.NoDrag)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._scene.clear()
        self._item = QGraphicsPixmapItem(pixmap)
        self._scene.addItem(self._item)
        self._zoom = 1.0
        self.fit_to_window()

    def fit_to_window(self) -> None:
        if self._item is not None:
            self.fitInView(self._item, Qt.KeepAspectRatio)
            self._zoom = 1.0

    def zoom_by(self, factor: float) -> None:
        if self._item is None:
            return
        self._zoom = max(0.1, min(10.0, self._zoom * factor))
        self.resetTransform()
        self.scale(self._zoom, self._zoom)
        # 以视口中心为锚点缩放
        c = self.mapToScene(self.viewport().rect().center())
        self.centerOn(c)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: D102
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_by(1.15)
        elif delta < 0:
            self.zoom_by(1 / 1.15)

    def mousePressEvent(self, event) -> None:  # noqa: D102
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: D102
        if self._dragging:
            delta = event.position() - self._drag_last
            self._drag_last = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: D102
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: D102
        self.fit_to_window()


class MediaViewer(QWidget):
    """全屏媒体查看器。"""

    closed = Signal()

    def __init__(self, paths: Optional[List[str]] = None,
                 index: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._paths: List[str] = [str(p) for p in (paths or []) if is_image_path(str(p))]
        self._index = max(0, min(index, len(self._paths) - 1)) if self._paths else 0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        # 顶部：文件名 + 操作按钮
        top = QHBoxLayout()
        top.setSpacing(6)
        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet(
            f"color:white; font-size:13px; background:transparent;")
        top.addWidget(self._title_lbl, 1)

        def _mk_btn(text: str, tip: str, fn) -> QPushButton:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setStyleSheet(_btn_qss())
            b.clicked.connect(fn)
            return b

        top.addWidget(_mk_btn("−", "缩小", lambda: self._view.zoom_by(1 / 1.15)))
        top.addWidget(_mk_btn("+", "放大", lambda: self._view.zoom_by(1.15)))
        top.addWidget(_mk_btn("0", "还原", lambda: self._view.fit_to_window()))
        top.addWidget(_mk_btn("‹", "上一个", lambda: self._navigate(-1)))
        top.addWidget(_mk_btn("›", "下一个", lambda: self._navigate(1)))
        close_btn = _mk_btn("×", "关闭（Esc）", self._close_viewer)
        close_btn.setStyleSheet(_btn_qss(_DANGER))
        top.addWidget(close_btn)
        layout.addLayout(top)

        # 图片视图
        self._view = _ImageView(self)
        layout.addWidget(self._view, 1)

        # 底部提示
        hint = QLabel("滚轮缩放 · 拖拽平移 · ←→ 切换 · Esc 关闭")
        hint.setStyleSheet(
            f"color:{_TEXT_LIGHT}; font-size:11px; background:transparent;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self._load_current()

    # ------------------------------------------------------------ 导航
    def _load_current(self) -> None:
        if not self._paths:
            self._title_lbl.setText("（无可查看的图片）")
            return
        path = self._paths[self._index]
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._title_lbl.setText(f"无法加载：{Path(path).name}")
            return
        self._view.set_pixmap(pixmap)
        self._title_lbl.setText(
            f"{Path(path).name}  （{self._index + 1}/{len(self._paths)}）")

    def _navigate(self, delta: int) -> None:
        if not self._paths:
            return
        self._index = (self._index + delta) % len(self._paths)
        self._load_current()

    def _close_viewer(self) -> None:
        self.closed.emit()
        self.close()

    # ------------------------------------------------------------ 键盘
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D102
        if event.key() == Qt.Key_Escape:
            self._close_viewer()
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self._navigate(1)
        elif event.key() in (Qt.Key_Left, Qt.Key_Up):
            self._navigate(-1)
        elif event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self._view.zoom_by(1.15)
        elif event.key() == Qt.Key_Minus:
            self._view.zoom_by(1 / 1.15)
        elif event.key() == Qt.Key_0:
            self._view.fit_to_window()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(16, 16, 20, 245))
        painter.end()
        super().paintEvent(event)

    # ------------------------------------------------------------ 静态入口
    @staticmethod
    def open(paths: Optional[List[str]] = None, index: int = 0) -> Optional["MediaViewer"]:
        """打开全屏查看器（模态）。paths 为图片路径列表。"""
        viewer = MediaViewer(paths, index)
        viewer.showFullScreen()
        viewer.raise_()
        viewer.activateWindow()
        return viewer
