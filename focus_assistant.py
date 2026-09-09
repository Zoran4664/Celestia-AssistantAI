"""focus_assistant.py — 专注助手（新增模块）

专注助手：先设置专注时间 → 弹出置顶悬浮倒计时窗（img/time.png 上叠加倒计时文字）
→ 未到设定时间（提前结束）时调用 API 以当前角色语气输出「坚持」鼓励；
   到达设定时间时调用 API 以当前角色语气输出「完成」祝贺。

界面风格与主程序保持一致（HTML5 白色圆角卡片 / 主题色 #6c8ef5 / 投影 / 渐变与幽灵按钮）。

说明：
- 本模块为纯新增，不修改 / 删除任何既有代码；
- 鼓励文案通过 LLMWorker（QThread）异步生成，绝不阻塞主线程；
- encouragement_ready(role, text) 信号供主界面把角色鼓励同步追加到聊天区。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import (
    Qt, QTimer, Signal, QRect, QRectF, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from config_loader import ConfigLoader, project_root
from role_manager import RoleManager
from llm_client import LLMWorker

logger = logging.getLogger("AI_DeskMate.Focus")

# ================================================================
# HTML5 风格常量（与主界面一致）
# ================================================================
_ACCENT = "#6c8ef5"
_ACCENT_HOVER = "#8fb0ff"
_ACCENT_PRESSED = "#4a6fd4"
_TEXT_DARK = "#2b2b33"
_TEXT_MID = "#6b7280"
_BORDER = "#e5e7f0"

_DIALOG_QSS = f"""
QDialog {{
    background: #ffffff;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QLabel {{ color: {_TEXT_DARK}; font-size: 13px; background: transparent; }}
QLabel#focusTitle {{ font-size: 17px; font-weight: 600; color: {_TEXT_DARK}; }}
QLabel#focusHint {{ color: {_TEXT_MID}; }}
QSpinBox {{
    border: 1px solid {_BORDER}; border-radius: 8px; padding: 6px 10px;
    font-size: 15px; color: {_TEXT_DARK}; background: #ffffff;
}}
QSpinBox:focus {{ border: 2px solid {_ACCENT}; }}
QPushButton#focusStart {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_ACCENT}, stop:1 {_ACCENT_PRESSED});
    color: #ffffff; border: none; border-radius: 10px;
    padding: 8px 22px; font-size: 14px; font-weight: 600;
}}
QPushButton#focusStart:hover {{ background: {_ACCENT_HOVER}; }}
QPushButton#focusCancel {{
    background: rgba(255,255,255,0.85); color: {_TEXT_DARK};
    border: 1px solid {_BORDER}; border-radius: 10px;
    padding: 8px 22px; font-size: 14px;
}}
QPushButton#focusCancel:hover {{ border-color: {_ACCENT}; color: {_ACCENT}; }}
"""

_WINDOW_QSS = f"""
QPushButton#focusWinClose {{
    background: transparent; border: none;
}}
QPushButton#focusWinClose:hover {{
    background: rgba(255, 255, 255, 0.55); border-radius: 6px;
}}
"""

_ALERT_QSS = f"""
QLabel {{ background: transparent; }}
QLabel#focusAlertTitle {{ color: {_TEXT_DARK}; font-size: 13px; font-weight: 600; }}
QLabel#focusAlertBody {{
    color: {_TEXT_DARK}; font-size: 13px; background: transparent;
    border: none; padding: 4px;
}}
QPushButton#focusAlertClose {{
    background: #ffffff; color: {_TEXT_DARK}; border: 1px solid {_BORDER};
    border-radius: 6px; padding: 3px 12px; font-size: 12px;
}}
QPushButton#focusAlertClose:hover {{ background: #fff1f1; color: #e11d48; border-color: #e11d48; }}
"""


def _time_pixmap() -> QPixmap:
    """加载主目录 img/time.png（失败时返回主题色占位图）。"""
    pm = QPixmap()
    try:
        p = project_root() / "img" / "time.png"
        if p.exists():
            pm.load(str(p))
    except Exception:  # noqa: BLE001
        pm = QPixmap()
    if pm.isNull():
        pm = QPixmap(300, 128)
        pm.fill(QColor(_ACCENT))
    return pm


def _fmt_time(seconds: int) -> str:
    """倒计时格式：MM:SS，超过 1 小时为 H:MM:SS。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ================================================================
# 专注记录：data/focus_history.json（最近 30 条）+ data/focus_summary.json（摘要）
# ================================================================
_HISTORY_LIMIT = 30


def _focus_history_path() -> Path:
    return project_root() / "data" / "focus_history.json"


def _focus_summary_path() -> Path:
    return project_root() / "data" / "focus_summary.json"


def _fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(max(0, float(ts))).strftime("%Y-%m-%d %H:%M:%S")


def _load_focus_history() -> list:
    try:
        p = _focus_history_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取专注历史失败: %s", exc)
    return []


def _refresh_focus_summary(hist: list) -> dict:
    total = len(hist)
    succ = sum(1 for h in hist if h.get("success"))
    total_min = sum(int(h.get("actual_minutes") or 0) for h in hist)
    summary = {
        "total_sessions": total,
        "success": succ,
        "fail": max(0, total - succ),
        "total_minutes": total_min,
        "avg_minutes": round(total_min / total, 1) if total else 0,
        "last_session": hist[-1].get("end") if hist else "",
        "last_success": bool(hist[-1].get("success")) if hist else None,
        "last_role": hist[-1].get("role") if hist else "",
    }
    try:
        _focus_summary_path().parent.mkdir(parents=True, exist_ok=True)
        _focus_summary_path().write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("刷新专注摘要失败: %s", exc)
    return summary


def record_focus(role: str, planned_seconds: int, actual_seconds: int,
                 success: bool) -> None:
    """记录一次专注会话：写入历史（仅保留最近 30 条）并刷新摘要 json。"""
    try:
        end_ts = time.time()
        start_ts = end_ts - max(0, int(actual_seconds))
        hist = _load_focus_history()
        hist.append({
            "start": _fmt_dt(start_ts),
            "end": _fmt_dt(end_ts),
            "planned_minutes": max(1, int(round(int(planned_seconds) / 60))),
            "actual_minutes": max(0, int(round(max(0, int(actual_seconds)) / 60))),
            "success": bool(success),
            "role": role,
        })
        hist = hist[-_HISTORY_LIMIT:]
        _focus_history_path().parent.mkdir(parents=True, exist_ok=True)
        _focus_history_path().write_text(
            json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
        _refresh_focus_summary(hist)
    except Exception as exc:  # noqa: BLE001
        logger.warning("记录专注会话失败: %s", exc)


def focus_summary_text() -> str:
    """专注摘要一行文本（只读摘要 json，供提示词要求时调阅 / 投入短期记忆）。"""
    try:
        p = _focus_summary_path()
        if not p.exists():
            return ""
        s = json.loads(p.read_text(encoding="utf-8"))
        total = int(s.get("total_sessions") or 0)
        if total <= 0:
            return ""
        last_role = str(s.get("last_role") or "")
        tail = ("，角色 " + last_role) if last_role else ""
        return (f"专注记录：共 {total} 次，成功 {s.get('success', 0)} 次，"
                f"失败 {s.get('fail', 0)} 次，累计 {s.get('total_minutes', 0)} 分钟，"
                f"最近一次 {s.get('last_session', '')} 结束，"
                f"{'已按时完成' if s.get('last_success') else '提前结束'}" + tail + "。")
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成专注摘要文本失败: %s", exc)
        return ""


def clear_focus_records() -> None:
    """清空专注历史与摘要（遗忘「历史长期摘要记忆」用）。"""
    for _p in (_focus_history_path(), _focus_summary_path()):
        try:
            if _p.exists():
                _p.unlink()
        except Exception as exc:  # noqa: BLE001
            logger.warning("清除专注记录失败: %s", exc)

# ================================================================
# FocusDurationDialog — 开始前设置专注时间
# ================================================================
class FocusDurationDialog(QDialog):
    """专注助手：开始前先设置专注时间（分钟，1–180，默认 25）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("专注助手")
        self.setModal(True)
        self.setStyleSheet(_DIALOG_QSS)
        self.setMinimumWidth(380)
        self.setMinimumHeight(210)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(10)

        title = QLabel("专注助手")
        title.setObjectName("focusTitle")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        hint = QLabel("开始前请先设置本次专注的时间：")
        hint.setObjectName("focusHint")
        hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(hint)

        self._spin = QSpinBox()
        self._spin.setRange(1, 180)
        self._spin.setValue(25)
        self._spin.setSuffix(" 分钟")
        self._spin.setAlignment(Qt.AlignCenter)
        self._spin.setFixedWidth(170)
        spin_row = QHBoxLayout()
        spin_row.addStretch(1)
        spin_row.addWidget(self._spin)
        spin_row.addStretch(1)
        outer.addLayout(spin_row)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        start_btn = QPushButton("开始专注")
        start_btn.setObjectName("focusStart")
        start_btn.setCursor(Qt.PointingHandCursor)
        start_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("focusCancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(start_btn)
        btns.addWidget(cancel_btn)
        btns.addStretch(1)
        outer.addLayout(btns)

    def minutes(self) -> int:
        """用户设置的专注分钟数。"""
        return max(1, int(self._spin.value() or 25))

# ================================================================
# _CountdownPanel — 在 img/time.png 上叠加倒计时文字
# ================================================================
class _CountdownPanel(QWidget):
    """显示 img/time.png，并在图片上方叠加居中倒计时文字（半透明圆角底保证可读）。

    面板填满父窗口；字号随面板宽度缩放，适配滚轮调整大小。
    """

    def __init__(self, pixmap: QPixmap, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._text = "00:00"

    def set_countdown(self, text: str) -> None:
        self._text = text or "00:00"
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        # 1) 画 time.png（保持比例完整显示）
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        # 2) 倒计时文字（半透明圆角胶囊底，字号随宽度缩放）
        size = max(14, min(72, self.width() // 10))
        font = QFont("Microsoft YaHei UI", size)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        text = self._text or "00:00"
        tw = fm.horizontalAdvance(text)
        bw, bh = tw + int(size * 1.6), int(size * 1.8)
        rect = QRect((self.width() - bw) // 2, (self.height() - bh) // 2, bw, bh)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRoundedRect(rect, bh // 2, bh // 2)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignCenter, text)
        p.end()
        super().paintEvent(event)


# ================================================================
# _FocusAlert — 角色鼓励悬浮卡片（仿主程序 AlertPopup 风格）
# ================================================================
class _FocusAlert(QWidget):
    """角色鼓励悬浮卡片：白色圆角底 + 角色头像 + 标题 + 内容 + 关闭，20 秒自动渐隐。"""

    def __init__(self, role: str, title: str, text: str,
                 auto_close_ms: int = 20000) -> None:
        super().__init__(None)
        self._role = role
        self._roles = RoleManager.instance()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(320, 180)
        self._build_ui(role, title, text)
        if auto_close_ms > 0:
            QTimer.singleShot(auto_close_ms, self._fade_close)

    def _build_ui(self, role: str, title: str, text: str) -> None:
        self.setStyleSheet(_ALERT_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        avatar = QLabel()
        avatar.setFixedSize(40, 40)
        pm = QPixmap()
        portrait = self._roles.portrait_path(role)
        if portrait and Path(portrait).exists():
            pm.load(portrait)
        if pm.isNull():
            pm = QPixmap(40, 40)
            pm.fill(QColor(_ACCENT))
        avatar.setPixmap(pm.scaled(
            40, 40, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        head.addWidget(avatar)

        name = QLabel(f"{self._roles.display_name(role)} · {title}")
        name.setObjectName("focusAlertTitle")
        head.addWidget(name, 1)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("focusAlertClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn)
        layout.addLayout(head)

        body = QLabel(text)
        body.setObjectName("focusAlertBody")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(body, 1)

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        """手工绘制白色圆角卡片底，保证半透明窗口稳定显示底框。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.setPen(QPen(QColor(_BORDER), 1))
        p.setBrush(QColor(255, 255, 255, 247))
        p.drawPath(path)
        p.end()
        super().paintEvent(event)

    def _fade_close(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(600)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self.close)
        anim.start()
        self._fade_anim = anim

# ================================================================
# FocusCountdownWindow — 倒计时悬浮窗（置顶 / 可拖动 / 无边框）
# ================================================================
class FocusCountdownWindow(QWidget):
    """专注倒计时悬浮窗：置顶无边框可拖动，img/time.png 上实时显示倒计时文字。

    - 未到设定时间点击「结束专注」/「×」/系统关闭 → 角色 API 输出「坚持」鼓励后关闭；
    - 倒计时归零（达到设定时间）→ 角色 API 输出「完成」祝贺；
    - 鼓励完成后发出 encouragement_ready(role, text)，供主界面同步追加聊天气泡。
    """

    encouragement_ready = Signal(str, str)   # (role, clean_text)

    def __init__(self, role: str, minutes: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._role = role
        self._total = max(1, int(minutes or 1)) * 60
        self._remaining = self._total
        self._finished = False
        self._ending = False
        self._worker: Optional[Any] = None
        self._alert: Optional[QWidget] = None
        self._started_at = time.time()
        self._session_recorded = False

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 只保留图片：初始尺寸按 img/time.png 比例（宽 360）
        pm = _time_pixmap()
        self._aspect = (pm.height() / max(1, pm.width())) if not pm.isNull() else (128 / 300)
        self.resize(360, max(1, int(round(360 * self._aspect))))
        self.setWindowTitle("专注助手")
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._update_display()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.setStyleSheet(_WINDOW_QSS)
        # 仅保留图片：img/time.png 上叠加倒计时文字
        self._panel = _CountdownPanel(_time_pixmap(), self)
        self._panel.setGeometry(self.rect())

        # 关闭按钮使用 img/close.png（透明底，位于图片右上角）
        close_pm = QPixmap(str(project_root() / "img" / "close.png"))
        if close_pm.isNull():
            close_pm = QPixmap(18, 18)
            close_pm.fill(QColor("#e11d48"))
        self._close_btn = QPushButton(self)
        self._close_btn.setObjectName("focusWinClose")
        self._close_btn.setIcon(QIcon(close_pm))
        self._close_btn.setIconSize(close_pm.size())
        self._close_btn.setFixedSize(close_pm.width() + 8, close_pm.height() + 8)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip("关闭（提前结束 → 角色鼓励）")
        self._close_btn.clicked.connect(self.close)
        self._place_close_btn()

    def _place_close_btn(self) -> None:
        margin = 6
        self._close_btn.move(
            self.width() - self._close_btn.width() - margin, margin)

    def resizeEvent(self, event: Any) -> None:  # noqa: D102
        self._panel.setGeometry(self.rect())
        self._place_close_btn()
        super().resizeEvent(event)

    def wheelEvent(self, event: Any) -> None:  # noqa: D102
        """滚轮放在悬浮窗上可调整大小（保持 img/time.png 比例）。"""
        delta = event.angleDelta().y()
        step = 24 if delta > 0 else -24
        new_w = max(180, min(720, self.width() + step))
        new_h = max(1, int(round(new_w * self._aspect)))
        self.resize(new_w, new_h)
        event.accept()

    # ------------------------------------------------------------ 拖动
    def mousePressEvent(self, event: Any) -> None:  # noqa: D102
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: D102
        if getattr(self, "_dragging", False):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: D102
        self._dragging = False
        event.accept()

    # ------------------------------------------------------------ 倒计时
    def _update_display(self) -> None:
        self._panel.set_countdown(_fmt_time(self._remaining))

    def _tick(self) -> None:
        if self._finished or self._ending:
            return
        self._remaining = max(0, self._remaining - 1)
        if self._remaining <= 0:
            self._remaining = 0
            self._timer.stop()
            self._finished = True
            self._update_display()
            self._on_time_up()
            return
        self._update_display()

    # ------------------------------------------------------------ 结束 / 完成
    def _on_end_clicked(self) -> None:
        """未到达 → 请角色鼓励后自动关闭；已到达 → 直接关闭。"""
        if self._ending:
            return
        if self._finished:
            self.close()
            return
        self._ending = True
        self._timer.stop()
        if self._remaining > 0:
            # 未达到专注时间：角色「坚持」鼓励
            self._generate_encouragement(
                early=True,
                fallback="还没到设定的专注时间哦，再坚持一下，我陪着你！",
                on_done=self._on_early_done)
        else:
            self._on_time_up()

    def _on_time_up(self) -> None:
        """到达设定专注时间：角色「完成」祝贺。"""
        self._finished = True
        self._timer.stop()
        self._record_session(success=True)
        self._generate_encouragement(
            early=False,
            fallback="专注时间到啦，你做得真棒！好好休息一下~",
            on_done=self._on_success_done)

    def closeEvent(self, event: Any) -> None:  # noqa: D102
        """未到设定时间关闭（× / 系统关闭）→ 先请角色鼓励，稍后自动关闭。"""
        if not self._finished and not self._ending and self._remaining > 0:
            event.ignore()
            self._on_end_clicked()
            return
        try:
            if getattr(self, "_timer", None) is not None:
                self._timer.stop()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------ 鼓励生成
    def _generate_encouragement(self, early: bool, fallback: str,
                                on_done: Callable[[str], None]) -> None:
        """按当前角色性格调用 API 生成鼓励 / 祝贺文案（LLMWorker 异步，不卡界面）。"""
        try:
            roles = RoleManager.instance()
            cfg = ConfigLoader.instance()
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取角色/配置单例失败，使用回退文案: %s", exc)
            on_done(fallback)
            return
        sys_p = roles.role_system_prompt(self._role)
        user_name = (cfg.user_name() or "用户").strip() or "用户"
        sys_p += f"\n\n用户称呼：{user_name}（请在合适的时机直接称呼这个名字）。"
        if early:
            task = ("用户提前结束了专注（距设定时间还剩 %s）。"
                    "请以你的性格，用简短（不超过40字）的话鼓励用户再坚持一下、回到专注上，"
                    "末尾用〖〗标注当前情感。") % _fmt_time(self._remaining)
        else:
            task = ("用户刚刚完成了设定的专注时间。"
                    "请以你的性格，用简短（不超过40字）的话夸奖并祝贺用户完成专注，"
                    "末尾用〖〗标注当前情感。")
        messages = [
            {"role": "system",
             "content": sys_p + "\n\n【要求】回复必须简短（≤40字），情感标注用〖〗。"},
            {"role": "user", "content": task},
        ]
        try:
            worker = LLMWorker(messages, kind="main", stream=False, max_tokens=80)
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建 LLMWorker 失败，使用回退文案: %s", exc)
            on_done(fallback)
            return
        worker.request_finished.connect(
            lambda text: self._on_generated(text, fallback, on_done))
        worker.request_error.connect(
            lambda _e: self._on_generated("", fallback, on_done))
        self._worker = worker          # 防止被 GC 回收导致线程中断
        try:
            worker.finished.connect(lambda w=worker: self._drop_worker(w))
        except Exception:  # noqa: BLE001
            pass
        worker.start()

    def _on_generated(self, text: str, fallback: str,
                      on_done: Callable[[str], None]) -> None:
        roles = RoleManager.instance()
        clean = roles.strip_emotion_tags(text or "").strip() if text else ""
        if not clean:
            clean = fallback
        on_done(clean)

    def _record_session(self, success: bool) -> None:
        """记录本次专注：写历史/摘要 json，并把摘要投入短期记忆供聊天调用。"""
        if getattr(self, "_session_recorded", False):
            return
        self._session_recorded = True
        try:
            actual = max(0, int(time.time() - getattr(self, "_started_at", time.time())))
            record_focus(self._role, self._total, actual, success)
            summary = focus_summary_text()
            if summary:
                from memory_pipeline import MemoryPipeline
                MemoryPipeline.instance().add_short_note(self._role, "[专注记录] " + summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记录专注会话/写入短期记忆失败: %s", exc)

    def _on_early_done(self, text: str) -> None:
        self._record_session(success=False)
        self._show_alert("专注提醒", text)
        self.encouragement_ready.emit(self._role, text)
        QTimer.singleShot(500, self.close)

    def _on_success_done(self, text: str) -> None:
        self._show_alert("专注完成", text)
        self.encouragement_ready.emit(self._role, text)
        QTimer.singleShot(1200, self.close)

    def _drop_worker(self, worker: Any) -> None:
        if getattr(self, "_worker", None) is worker:
            self._worker = None

    # ------------------------------------------------------------ 提醒卡片
    def _show_alert(self, title: str, text: str) -> None:
        popup = _FocusAlert(self._role, title, text)
        popup.move(max(0, self.x() + (self.width() - popup.width()) // 2),
                   max(0, self.y() - popup.height() - 8))
        popup.show()
        self._alert = popup





