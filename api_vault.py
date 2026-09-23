# -*- coding: utf-8 -*-
"""
api_vault.py — 常用接口类 API 管理器（技能工具配套 · 纯存储）

定位（需求）：
    每个 API 接口的调用方法不一致（有的用 ?key=、有的用 Bearer、有的要额外参数），
    因此本管理器**只做存储**：不校验字段、不强制任何一项必填，任意调用方式都能记下来，
    供 \\@weather 等技能工具按「名称 / 简介 / 地址 / KEY / 自定义内容 / 其他规则」取用。

数据文件：
    data/api_vault.json
    {
      "updated": "2026-09-19 12:00:00",
      "profiles": [
        {"id": "...", "name": "和风天气", "desc": "...", "base": "https://devapi.qweather.com",
         "key": "...", "extra": "...", "rules": "...", "enabled": true}
      ]
    }

字段说明：
    name    名称（如 和风天气）
    desc    简介
    base    地址（API Host / Base URL）
    key     API KEY（也可能是 Token；掩码显示，可点「显示」查看）
    extra   自定义内容（额外请求头、额外参数、JSON 片段等，原样存储）
    rules   其他规则（配额、鉴权方式、注意事项等）
    enabled 是否启用（禁用后技能工具不再取用）

运行：python api_vault.py
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import project_root

# ================================================================ 数据层
DATA_FILE = project_root() / "data" / "api_vault.json"

FIELD_LABELS: Dict[str, str] = {
    "name": "名称",
    "desc": "简介",
    "base": "地址",
    "key": "API KEY",
    "extra": "自定义内容",
    "rules": "其他规则",
}


def _blank() -> Dict[str, Any]:
    return {"id": uuid.uuid4().hex[:12], "name": "", "desc": "", "base": "",
            "key": "", "extra": "", "rules": "", "enabled": True}


def _norm(p: Any) -> Dict[str, Any]:
    """字段缺失自动回填，保证结构完整。"""
    d = _blank()
    if isinstance(p, dict):
        for k in ("id", "name", "desc", "base", "key", "extra", "rules"):
            v = p.get(k)
            d[k] = str(v) if v is not None else ""
        d["enabled"] = bool(p.get("enabled", True))
    if not d["id"]:
        d["id"] = uuid.uuid4().hex[:12]
    return d


def load_profiles() -> List[Dict[str, Any]]:
    """读取全部接口配置（文件损坏 / 缺失时返回空列表）。"""
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 文件不存在或 JSON 损坏
        return []
    items = raw.get("profiles") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [_norm(x) for x in items]


def save_profiles(profiles: List[Dict[str, Any]]) -> None:
    """原子替换写入（先写 .tmp 再 os.replace），避免写一半损坏。"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(profiles),
        "profiles": [_norm(p) for p in profiles],
    }
    tmp = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def upsert_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """新增或更新（按 id；id 为空则新建）。"""
    profiles = load_profiles()
    item = _norm(profile)
    for i, p in enumerate(profiles):
        if p.get("id") == item["id"]:
            profiles[i] = item
            save_profiles(profiles)
            return item
    profiles.append(item)
    save_profiles(profiles)
    return item


def delete_profile(profile_id: str) -> None:
    profiles = [p for p in load_profiles() if p.get("id") != profile_id]
    save_profiles(profiles)


def find_profile(keyword: str, require_key: bool = False) -> Optional[Dict[str, Any]]:
    """按关键词查找接口配置：依次匹配 id / 名称 / 简介（不区分大小写）。

    require_key=True 时只返回已填写 KEY 的条目。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return None
    best: Optional[Dict[str, Any]] = None
    for p in load_profiles():
        if not p.get("enabled", True):
            continue
        if require_key and not str(p.get("key") or "").strip():
            continue
        hay = " ".join(str(p.get(k) or "") for k in ("id", "name", "desc")).lower()
        if kw not in hay:
            continue
        if str(p.get("name") or "").strip().lower() == kw:
            return p          # 名称完全命中 → 直接返回
        if best is None:
            best = p
    return best


def get_key(keyword: str, default: str = "") -> str:
    """取某个接口的 KEY（找不到返回 default）。"""
    p = find_profile(keyword, require_key=True)
    return str(p.get("key") or "").strip() if p else default


def get_base(keyword: str, default: str = "") -> str:
    """取某个接口的地址（找不到返回 default）。"""
    p = find_profile(keyword)
    return str(p.get("base") or "").strip() if p else default


def ensure_defaults() -> None:
    """首次使用时写入一条「和风天气」占位配置，方便用户直接填 KEY。"""
    if load_profiles():
        return
    save_profiles([_norm({
        "name": "和风天气",
        "desc": "和风天气 QWeather 开发者 API（v7）：天气预报 / 预警 / 空气 / 分钟降水 / 天文 等",
        "base": "https://devapi.qweather.com",
        "key": "",
        "extra": "geo=https://geoapi.qweather.com",
        "rules": "在 https://console.qweather.com 申请；地址填「控制台 → 设置」里分配的"
                 " 独立 API Host（形如 xxxx.xy.qweatherapi.com），公共地址 devapi / "
                 "geoapi 自 2026 年起逐步停服（会报 403 invalid-host）；"
                 "鉴权用请求标头 X-QW-Api-Key 或查询参数 key=。",
    })])


# ================================================================ GUI
try:
    from PySide6.QtCore import Qt, QPoint, QRect
    from PySide6.QtGui import QColor, QPainter, QPainterPath
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QPushButton, QTextEdit, QVBoxLayout,
        QWidget,
    )
    from PySide6.QtGui import QMouseEvent
except ImportError:  # pragma: no cover
    Qt = None        # type: ignore


ACCENT = "#6c8ef5"
TEXT_DARK = "#2b2b33"
TEXT_MID = "#6b7280"
TEXT_LIGHT = "#9aa0ac"
BORDER = "#e5e7f0"
DANGER = "#d64545"
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif'


def _btn_qss(accent: str) -> str:
    return (
        f"QPushButton{{background:{accent};color:#fff;border:none;border-radius:10px;"
        f"padding:8px 18px;font-family:{FONT_FAMILY};font-size:13px;}}"
        f"QPushButton:hover{{background:#8fb0ff;}}"
        f"QPushButton:pressed{{background:#4a6fd4;}}"
    )


def _ghost_qss(accent: str) -> str:
    return (
        f"QPushButton{{background:rgba(255,255,255,0.9);color:{accent};"
        f"border:1px solid {accent};border-radius:10px;padding:7px 16px;"
        f"font-family:{FONT_FAMILY};font-size:13px;}}"
        f"QPushButton:hover{{background:{accent};color:#fff;}}"
    )


def _danger_qss() -> str:
    return (
        f"QPushButton{{background:#fff;color:{DANGER};border:1px solid {DANGER};"
        f"border-radius:10px;padding:7px 16px;font-family:{FONT_FAMILY};font-size:13px;}}"
        f"QPushButton:hover{{background:{DANGER};color:#fff;}}"
    )


class ApiVaultWindow(QWidget):
    """常用接口类 API 管理器（无边框圆角 · 与主程序同款风格）。"""

    def __init__(self) -> None:
        super().__init__()
        self._accent = ACCENT
        self._drag_pos: Optional[QPoint] = None
        self._profiles: List[Dict[str, Any]] = []
        self._current_id = ""
        self._key_visible = False
        self.setWindowTitle("常用接口类 API 管理器")
        self.resize(880, 620)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 标题栏
        bar = QHBoxLayout()
        title = QLabel("常用接口类 API 管理器")
        title.setStyleSheet(
            f"color:{TEXT_DARK};font-family:{FONT_FAMILY};font-size:15px;font-weight:600;")
        sub = QLabel("接口信息仅本地存储，供技能工具按名称取用")
        sub.setStyleSheet(f"color:{TEXT_LIGHT};font-family:{FONT_FAMILY};font-size:11px;")
        bar.addWidget(title)
        bar.addSpacing(8)
        bar.addWidget(sub)
        bar.addStretch(1)
        for txt, slot in (("─", self.showMinimized), ("×", self.close)):
            b = QPushButton(txt)
            b.setFixedSize(30, 26)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;color:{TEXT_MID};border:none;"
                f"font-size:14px;}}QPushButton:hover{{background:rgba(0,0,0,0.06);"
                f"border-radius:8px;}}")
            b.clicked.connect(slot)
            bar.addWidget(b)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(12)

        # 左：接口列表
        left = QFrame()
        left.setObjectName("card")
        left.setFixedWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:#fff;border:1px solid {BORDER};border-radius:10px;"
            f"font-family:{FONT_FAMILY};font-size:13px;padding:4px;}}"
            f"QListWidget::item{{padding:8px 10px;border-radius:8px;}}"
            f"QListWidget::item:selected{{background:rgba(108,142,245,0.16);"
            f"color:{TEXT_DARK};}}")
        self._list.currentRowChanged.connect(self._on_select)
        ll.addWidget(self._list, 1)
        row = QHBoxLayout()
        new_btn = QPushButton("＋ 新建")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(_ghost_qss(self._accent))
        new_btn.clicked.connect(self._new_profile)
        del_btn = QPushButton("删除")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet(_danger_qss())
        del_btn.clicked.connect(self._delete_current)
        row.addWidget(new_btn)
        row.addWidget(del_btn)
        ll.addLayout(row)
        body.addWidget(left)

        # 右：属性表单
        right = QFrame()
        right.setObjectName("card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(8)

        self._edits: Dict[str, Any] = {}
        self._name = self._add_line(rl, "名称", "例如：和风天气")
        self._desc = self._add_text(rl, "简介", "这个接口是做什么的（选填）", 3)
        self._base = self._add_line(rl, "地址", "API Host / Base URL（选填）")

        # KEY 行（带显示/隐藏）
        key_title = QLabel(FIELD_LABELS["key"] + "（选填）")
        key_title.setStyleSheet(
            f"color:{TEXT_MID};font-family:{FONT_FAMILY};font-size:12px;")
        rl.addWidget(key_title)
        krow = QHBoxLayout()
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.Password)
        self._key.setPlaceholderText("Key / Token（选填，掩码显示）")
        self._key.setStyleSheet(self._input_qss())
        eye = QPushButton("显示")
        eye.setFixedWidth(60)
        eye.setCursor(Qt.PointingHandCursor)
        eye.setStyleSheet(_ghost_qss(self._accent))
        eye.clicked.connect(self._toggle_key)
        krow.addWidget(self._key, 1)
        krow.addWidget(eye)
        rl.addLayout(krow)

        self._extra = self._add_text(rl, "自定义内容",
                                     "额外请求头 / 额外参数 / JSON 片段等，原样保存（选填）", 4)
        self._rules = self._add_text(rl, "其他规则",
                                     "配额、鉴权方式、注意事项等（选填）", 4)
        self._enabled = QCheckBox("启用（禁用后技能工具不再取用该接口）")
        self._enabled.setChecked(True)
        self._enabled.setStyleSheet(
            f"QCheckBox{{color:{TEXT_DARK};font-family:{FONT_FAMILY};font-size:12px;}}")
        rl.addWidget(self._enabled)
        rl.addStretch(1)

        brow = QHBoxLayout()
        save_btn = QPushButton("保存修改")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(_btn_qss(self._accent))
        save_btn.clicked.connect(self._save_current)
        brow.addStretch(1)
        brow.addWidget(save_btn)
        rl.addLayout(brow)
        body.addWidget(right, 1)

        root.addLayout(body, 1)
        self._status = QLabel("就绪")
        self._status.setStyleSheet(
            f"color:{TEXT_LIGHT};font-family:{FONT_FAMILY};font-size:11px;")
        root.addWidget(self._status)
        self.setStyleSheet(
            f"QFrame#card{{background:rgba(255,255,255,0.94);border:1px solid {BORDER};"
            f"border-radius:14px;}}")

    def _input_qss(self) -> str:
        return (
            f"QLineEdit,QTextEdit{{background:#fff;border:1px solid {BORDER};"
            f"border-radius:8px;padding:6px 8px;color:{TEXT_DARK};"
            f"font-family:{FONT_FAMILY};font-size:13px;}}"
            f"QLineEdit:focus,QTextEdit:focus{{border:1px solid {self._accent};}}"
        )

    def _add_line(self, layout: QVBoxLayout, label: str,
                  placeholder: str) -> QLineEdit:
        t = QLabel(f"{label}（选填）")
        t.setStyleSheet(
            f"color:{TEXT_MID};font-family:{FONT_FAMILY};font-size:12px;")
        layout.addWidget(t)
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setStyleSheet(self._input_qss())
        layout.addWidget(e)
        return e

    def _add_text(self, layout: QVBoxLayout, label: str,
                  placeholder: str, rows: int) -> QTextEdit:
        t = QLabel(f"{label}（选填）")
        t.setStyleSheet(
            f"color:{TEXT_MID};font-family:{FONT_FAMILY};font-size:12px;")
        layout.addWidget(t)
        e = QTextEdit()
        e.setPlaceholderText(placeholder)
        e.setFixedHeight(28 * rows)
        e.setStyleSheet(self._input_qss())
        layout.addWidget(e)
        return e

    # ------------------------------------------------------------ 数据交互
    def _reload(self) -> None:
        self._profiles = load_profiles()
        self._list.blockSignals(True)
        self._list.clear()
        for p in self._profiles:
            name = str(p.get("name") or "").strip() or "（未命名接口）"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, p.get("id"))
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._load_blank()
        self._status.setText(f"共 {len(self._profiles)} 个接口 · {DATA_FILE}")

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._profiles):
            return
        p = self._profiles[row]
        self._current_id = str(p.get("id") or "")
        self._name.setText(str(p.get("name") or ""))
        self._desc.setPlainText(str(p.get("desc") or ""))
        self._base.setText(str(p.get("base") or ""))
        self._key.setText(str(p.get("key") or ""))
        self._extra.setPlainText(str(p.get("extra") or ""))
        self._rules.setPlainText(str(p.get("rules") or ""))
        self._enabled.setChecked(bool(p.get("enabled", True)))

    def _load_blank(self) -> None:
        self._current_id = ""
        for w in (self._name, self._base, self._key):
            w.clear()
        for w in (self._desc, self._extra, self._rules):
            w.clear()
        self._enabled.setChecked(True)

    def _new_profile(self) -> None:
        self._load_blank()
        self._list.setCurrentRow(-1)
        self._name.setFocus()
        self._status.setText("新建接口：填写后点「保存修改」")

    def _save_current(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._status.setText("请先填写接口名称")
            return
        profile = _blank()
        if self._current_id:
            profile["id"] = self._current_id
        profile.update({
            "name": name,
            "desc": self._desc.toPlainText().strip(),
            "base": self._base.text().strip(),
            "key": self._key.text().strip(),
            "extra": self._extra.toPlainText().strip(),
            "rules": self._rules.toPlainText().strip(),
            "enabled": self._enabled.isChecked(),
        })
        saved = upsert_profile(profile)
        self._current_id = saved["id"]
        self._reload()
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == self._current_id:
                self._list.setCurrentRow(i)
                break
        self._status.setText(f"已保存：{name}")

    def _delete_current(self) -> None:
        if not self._current_id:
            self._status.setText("请先选中要删除的接口")
            return
        try:
            from utils.styled_msg import styled_question
            ok = styled_question(self, "删除接口",
                                 "确定删除当前接口配置吗？此操作不可撤销。")
        except Exception:  # noqa: BLE001
            ok = True
        if not ok:
            return
        delete_profile(self._current_id)
        self._current_id = ""
        self._reload()
        self._status.setText("已删除")

    def _toggle_key(self) -> None:
        self._key_visible = not self._key_visible
        self._key.setEchoMode(
            QLineEdit.Normal if self._key_visible else QLineEdit.Password)
        self.sender().setText("隐藏" if self._key_visible else "显示")

    # ------------------------------------------------------------ 主题 / 外观
    def apply_accent(self, color: str) -> None:
        """主界面切换主题色时同步（与技能工具管理器一致）。"""
        if not color:
            return
        self._accent = color
        self._refresh_styles()

    def _refresh_styles(self) -> None:
        for w in self.findChildren(QPushButton):
            txt = w.text()
            if txt in ("保存修改",):
                w.setStyleSheet(_btn_qss(self._accent))
            elif txt in ("＋ 新建", "显示", "隐藏"):
                w.setStyleSheet(_ghost_qss(self._accent))
            elif txt == "删除":
                w.setStyleSheet(_danger_qss())
        for w in self.findChildren(QLineEdit):
            w.setStyleSheet(self._input_qss())
        for w in self.findChildren(QTextEdit):
            w.setStyleSheet(self._input_qss())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRect(0, 0, self.width(), self.height()), 16, 16)
        p.fillPath(path, QColor(244, 245, 250, 235))
        p.end()

    # 无边框拖动
    def mousePressEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        if event.position().y() < 56:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)


def main() -> None:
    import sys
    app = QApplication(sys.argv)
    ensure_defaults()
    w = ApiVaultWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
