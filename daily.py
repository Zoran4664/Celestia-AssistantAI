# -*- coding: utf-8 -*-
"""daily.py — 日记本子项目（Celestia AssistantAI · HTML5 响应式风格）

独立运行的日记程序，界面风格与主程序完全一致：
- HTML5 白色圆角卡片 / 主题色 #6c8ef5 / 投影 / 渐变与幽灵按钮；
- 无边框圆角半透明窗口 + 自定义标题栏（拖动 / 最小化 / 最大化 / 关闭）+ 四边缩放。

功能：
- 一级界面 DailyMainWindow：日记卡片列表（标题 / 建立日期 / 上次修改日期），
  每篇带删除按钮（img/dailydel.png）；右上角「新建日记」与「删除文件」。
- 二级界面 DailyEditWindow：标题、正文（富文本）、插入图片、插入文件、
  角色专属多选（主项目 roles/ 角色 + group.json 群组）、保存。
- 数据全部位于主目录 /dailydata：dailytext/ 存日记 JSON，dailyfile/ 存附件副本。
- 日记自动记录首次保存时间 created_at 与最近修改时间 updated_at。
- 全 UTF-8 编码，界面不含任何 emoji。

运行方式：python daily.py   （仅依赖 PySide6，与主程序一致）
规划文档：dailymanger.md（路径 / 变量 / 数据结构说明与调整示例）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

# ================================================================
# HTML5 风格常量（与主程序 ui_manager.py / skilltools.py 完全一致）
# ================================================================
ACCENT = "#6c8ef5"
ACCENT_HOVER = "#8fb0ff"
ACCENT_PRESSED = "#4a6fd4"
TEXT_DARK = "#2b2b33"
TEXT_MID = "#6b7280"
TEXT_LIGHT = "#9aa0ac"
CARD_BG = "rgba(255,255,255,0.92)"
CARD_BG_ALT = "rgba(246,247,252,0.9)"
BORDER = "#e5e7f0"
RADIUS = 14
DANGER = "#d64545"
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif'

# 时间戳格式（created_at / updated_at 共用）
_TS_FMT = "%Y-%m-%d %H:%M:%S"

# ================================================================
# 路径定义（以主目录为基准，与主程序 config_loader 一致）
# ================================================================
ROOT = Path(__file__).resolve().parent
DAILY_DIR = ROOT / "dailydata"            # 数据根目录
DAILY_TEXT_DIR = DAILY_DIR / "dailytext"  # 日记 JSON 目录
DAILY_FILE_DIR = DAILY_DIR / "dailyfile"  # 附件目录（图片 / 文件副本）
IMG_DELETE = ROOT / "img" / "dailydel.png"  # 删除按钮图标


# ================================================================
# 基础工具函数
# ================================================================
def _now_str() -> str:
    """当前时间字符串（按 _TS_FMT 格式）。"""
    return datetime.now().strftime(_TS_FMT)


def _unique_id() -> str:
    """生成日记唯一 id：YYYYMMDD_HHMMSS_<6位hex>。"""
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff\-.]", flags=re.UNICODE)


def _safe_name(name: str) -> str:
    """文件名消毒：仅保留中文 / 字母 / 数字 / -_.，防止路径穿越。"""
    cleaned = _SAFE_RE.sub("_", str(name)).strip("._")
    return cleaned or "unnamed"


def ensure_dirs() -> None:
    """确保日记 / 附件目录存在。"""
    DAILY_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_FILE_DIR.mkdir(parents=True, exist_ok=True)


def _hex_to_rgba(color: str, alpha: float) -> str:
    """#RRGGBB -> rgba(r,g,b,a)。"""
    from PySide6.QtGui import QColor
    c = QColor(color)
    if not c.isValid():
        c = QColor(ACCENT)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _lighten(color: str, amount: int) -> str:
    from PySide6.QtGui import QColor
    c = QColor(color)
    if c.isValid():
        c = c.lighter(100 + amount)
        return c.name()
    return color


def _darken(color: str, amount: int) -> str:
    from PySide6.QtGui import QColor
    c = QColor(color)
    if c.isValid():
        c = c.darker(100 + amount)
        return c.name()
    return color



# ================================================================
# QSS 生成（与主程序 ui_manager._root_qss_base 风格一致）
# ================================================================
def _root_qss(accent: str, scale: float = 1.0) -> str:
    """生成全局 QSS（复刻主程序 HTML5 响应式风格）。

    视觉基调：主界面 theme 透底 + 主题色淡底面板 + 白色块状卡片。
    - 卡片 / 面板：白色块 + 主题色左侧色条 / 边框（HTML5 块状显示）；
    - 输入框 / 列表：纯白底（需求：文本框一直白色），聚焦时主题色描边；
    - 管理界面（文件管理等）：同主题色 + 白色搭配，与主页统一。
    """
    sel_rgba = _hex_to_rgba(accent, 0.15)
    card_tint = _hex_to_rgba(accent, 0.05)      # 卡片淡主题色底
    card_tint_hover = _hex_to_rgba(accent, 0.10)
    input_bg = "white"                            # 输入框纯白底
    panel_bg = _hex_to_rgba(accent, 0.06)       # 管理面板淡主题色底
    qss = f"""
QWidget#root {{ background: transparent; }}
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QFrame#mainPanel {{
    background: {panel_bg}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QFrame#cardWidget {{
    background: {CARD_BG}; border: 1px solid {BORDER};
    border-left: 4px solid {accent};
    border-radius: 10px;
}}
QFrame#cardWidget:hover {{
    background: {CARD_BG}; border-color: {accent};
    border-left: 4px solid {accent};
}}
QLabel#sectionTitle {{
    color: {TEXT_DARK}; font-size: 15px; font-weight: 600;
    padding-left: 10px; border-left: 4px solid {accent};
}}
QLabel#appTitle {{ color: {TEXT_DARK}; font-size: 14px; font-weight: 600; }}
QLabel#statusText {{ color: {TEXT_LIGHT}; font-size: 11px; }}
QLabel#cardTitle {{ color: {TEXT_DARK}; font-size: 16px; font-weight: 600; }}
QLabel#cardDate {{ color: {TEXT_MID}; font-size: 12px; }}
QLabel#cardPreview {{ color: {TEXT_MID}; font-size: 12px; }}
QLabel#emptyHint {{ color: {TEXT_LIGHT}; font-size: 14px; }}
QPushButton#titleBtn {{
    background: transparent; border: none; color: {TEXT_MID};
    font-size: 13px; border-radius: 6px;
}}
QPushButton#titleBtn:hover {{ background: #e8e9f2; color: {TEXT_DARK}; }}
QPushButton#primaryBtn {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {accent}, stop:1 {accent});
    color: white; border: none; border-radius: 12px;
    padding: 8px 20px; font-size: 14px; font-weight: 600;
}}
QPushButton#primaryBtn:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_lighten(accent, 18)}, stop:1 {accent});
}}
QPushButton#primaryBtn:pressed {{ background: {_darken(accent, 12)}; }}
QPushButton#ghostBtn {{
    background: rgba(255,255,255,0.85); color: {TEXT_DARK};
    border: 1px solid {BORDER}; border-radius: 12px;
    padding: 7px 16px; font-size: 13px;
}}
QPushButton#ghostBtn:hover {{
    border-color: {accent}; color: {accent};
    background: {_hex_to_rgba(accent, 0.08)};
}}
QPushButton#ghostBtn:pressed {{ background: {_hex_to_rgba(accent, 0.16)}; }}
QPushButton#dangerBtn {{
    background: rgba(255,255,255,0.85); color: {DANGER};
    border: 1px solid #f0d2d2; border-radius: 12px;
    padding: 7px 16px; font-size: 13px;
}}
QPushButton#dangerBtn:hover {{
    background: #fdeaea; border-color: {DANGER}; color: {DANGER};
}}
QPushButton#dangerBtn:pressed {{ background: #fbdcdc; }}
QPushButton#delIconBtn {{
    background: transparent; border: none;
}}
QPushButton#delIconBtn:hover {{ background: #fdeaea; border-radius: 8px; }}
QLineEdit {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    font-size: 13px; background: {input_bg}; color: {TEXT_DARK};
}}
QLineEdit:focus {{ border: 2px solid {accent}; background: white; }}
QTextEdit {{
    border: 1px solid {BORDER}; border-radius: 10px; padding: 6px;
    font-size: 14px; background: {input_bg}; color: {TEXT_DARK};
}}
QTextEdit:focus {{ border: 2px solid {accent}; background: white; }}
QListWidget {{
    background: {input_bg}; border: 1px solid {BORDER}; border-radius: 8px;
    font-size: 13px; outline: none;
    font-family: {FONT_FAMILY};
}}
QListWidget::item {{ padding: 8px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {sel_rgba}; color: {accent}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: #eef0f6; width: 12px; margin: 2px; border-radius: 6px;
}}
QScrollBar::handle:vertical {{
    background: {_hex_to_rgba(accent, 0.55)}; border-radius: 6px; min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: {_hex_to_rgba(accent, 0.8)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: #eef0f6; height: 12px; }}
QScrollBar::handle:horizontal {{
    background: {_hex_to_rgba(accent, 0.55)}; border-radius: 6px; min-width: 40px;
}}
QGroupBox#roleGroup {{
    background: {card_tint}; border: 1px solid {BORDER};
    border-radius: 12px; margin-top: 4px; font-size: 13px;
}}
QGroupBox#roleGroup::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px;
    color: {TEXT_DARK}; font-weight: 600; font-size: 13px;
}}
QMessageBox, QDialog {{ font-size: 13px; }}
"""
    if abs(scale - 1.0) < 1e-6:
        return qss
    return _scale_qss(qss, scale)


def _scale_qss(qss: str, scale: float) -> str:
    """按 scale 缩放 QSS 中所有 px 数值（font-size 保底 8px，其它保底 1px）。"""
    def _rep(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        start = m.start()
        seg_start = max(qss.rfind(";", 0, start), qss.rfind("{", 0, start)) + 1
        decl = qss[seg_start:start].lstrip()
        floor = 8 if decl.startswith("font-size") else 1
        return f"{max(floor, int(n * scale))}px"
    return re.sub(r"(\d+)px", _rep, qss)


def btn_qss(base: str = ACCENT, hover: str = ACCENT_HOVER,
            pressed: str = ACCENT_PRESSED, radius: int = 12,
            text_color: str = "#ffffff", font_size: int = 14,
            bold: bool = True, padding: str = "8px 20px") -> str:
    """HTML5 渐变主按钮样式（仿 CSS :hover/:active，与主界面一致）。"""
    weight = "600" if bold else "400"
    return f"""
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {base}, stop:1 {pressed});
    color: {text_color}; border: none; border-radius: {radius}px;
    padding: {padding}; font-size: {font_size}px; font-weight: {weight};
}}
QPushButton:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {hover}, stop:1 {base});
}}
QPushButton:pressed {{
    background: {pressed};
}}
QPushButton:disabled {{
    background: #b9c4ea; color: rgba(255,255,255,0.8);
}}
"""


def ghost_btn_qss(radius: int = 12, font_size: int = 13,
                  padding: str = "7px 16px") -> str:
    """幽灵按钮（白底描边，仿 HTML 次按钮，与主界面一致）。"""
    return f"""
QPushButton {{
    background: rgba(255,255,255,0.85); color: {TEXT_DARK};
    border: 1px solid {BORDER}; border-radius: {radius}px;
    padding: {padding}; font-size: {font_size}px;
}}
QPushButton:hover {{
    border-color: {ACCENT}; color: {ACCENT};
    background: rgba(108,142,245,0.08);
}}
QPushButton:pressed {{
    background: rgba(108,142,245,0.16);
}}
"""


def danger_btn_qss(radius: int = 12, font_size: int = 13,
                   padding: str = "7px 16px") -> str:
    """危险操作按钮（白底红字描边，删除等，与主界面一致）。"""
    return f"""
QPushButton {{
    background: rgba(255,255,255,0.85); color: {DANGER};
    border: 1px solid #f0d2d2; border-radius: {radius}px;
    padding: {padding}; font-size: {font_size}px;
}}
QPushButton:hover {{
    background: #fdeaea; border-color: {DANGER}; color: {DANGER};
}}
QPushButton:pressed {{
    background: #fbdcdc;
}}
"""


def apply_shadow(widget: Any, blur: int = 14, y: int = 3,
                 alpha: int = 45) -> Any:
    """为控件添加柔和投影（Material 层级感）。"""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def _confirm_dialog(parent: QWidget, title: str, text: str,
                    danger: bool = False) -> bool:
    """HTML5 风格确认对话框（与主界面视觉统一，替代系统原生 QMessageBox）。

    返回 True 表示用户确认；False 表示取消。
    - danger=True 时主按钮为危险红（删除类操作）；
    - danger=False 时主按钮为主题色渐变。
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dlg.setAttribute(Qt.WA_TranslucentBackground)
    dlg.setFixedSize(460, 210)
    dlg.setModal(True)

    # 白色圆角卡片容器（与主界面 HTML5 卡片一致）
    card = QFrame(dlg)
    card.setObjectName("cardWidget")
    card.setStyleSheet(
        "QFrame#cardWidget { background: rgba(255,255,255,0.97);"
        " border: 1px solid #e5e7f0; border-radius: 14px; }")
    apply_shadow(card, blur=18, y=4, alpha=55)
    card.setGeometry(10, 10, dlg.width() - 20, dlg.height() - 20)

    outer = QVBoxLayout(card)
    outer.setContentsMargins(22, 16, 22, 14)
    outer.setSpacing(10)

    head = QLabel(title)
    head.setObjectName("sectionTitle")
    outer.addWidget(head)

    msg = QLabel(text)
    msg.setObjectName("cardDate")
    msg.setWordWrap(True)
    outer.addWidget(msg, 1)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)
    btn_row.addStretch(1)
    ok_btn = QPushButton("确认")
    ok_btn.setCursor(Qt.PointingHandCursor)
    ok_btn.setStyleSheet(btn_qss() if not danger else danger_btn_qss())
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)
    cancel_btn = QPushButton("取消")
    cancel_btn.setCursor(Qt.PointingHandCursor)
    cancel_btn.setStyleSheet(ghost_btn_qss())
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel_btn)
    outer.addLayout(btn_row)

    return dlg.exec() == QDialog.Accepted


# ================================================================
# 数据读写（角色 / 群组 / 日记 / 附件）
# ================================================================
def _load_json(path: Path, default: Any) -> Any:
    """安全读取 JSON 文件，失败返回 default。"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 文件损坏 / 编码异常时回退默认值
        pass
    return default


def list_roles() -> List[str]:
    """扫描主项目 roles/ 下含 roles.json 的角色目录名（升序）。"""
    d = ROOT / "roles"
    if not d.is_dir():
        return []
    out = []
    for sub in sorted(p.name for p in d.iterdir() if p.is_dir()):
        if (d / sub / "roles.json").exists():
            out.append(sub)
    return out


def role_display(name: str) -> str:
    """角色显示名：优先 roles.json 的 display_name / name，缺省回退目录名。"""
    card = _load_json(ROOT / "roles" / name / "roles.json", {})
    if isinstance(card, dict):
        return str(card.get("display_name") or card.get("name") or name)
    return name


def list_groups() -> List[str]:
    """读取主项目 roles/group.json 的群组名列表。"""
    data = _load_json(ROOT / "roles" / "group.json", {})
    groups = data.get("groups") if isinstance(data, dict) else None
    return list(groups.keys()) if isinstance(groups, dict) else []


def list_diaries() -> List[Dict[str, Any]]:
    """扫描 dailytext 下所有日记 json，按 created_at 倒序返回。"""
    ensure_dirs()
    out: List[Dict[str, Any]] = []
    for p in DAILY_TEXT_DIR.glob("*.json"):
        data = _load_json(p, {})
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    out.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return out


def read_diary(did: str) -> Optional[Dict[str, Any]]:
    """按 id 读取单篇日记（损坏 / 不存在返回 None）。"""
    data = _load_json(DAILY_TEXT_DIR / f"{_safe_name(did)}.json", {})
    return data if isinstance(data, dict) and data.get("id") else None


def save_diary(data: Dict[str, Any]) -> bool:
    """保存日记：首次写 created_at，刷新 updated_at，原子写 JSON。"""
    ensure_dirs()
    did = str(data.get("id") or _unique_id())
    data["id"] = did
    if not data.get("created_at"):
        data["created_at"] = _now_str()
    data["updated_at"] = _now_str()
    path = DAILY_TEXT_DIR / f"{_safe_name(did)}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def delete_diary(did: str) -> bool:
    """删除单篇日记 json，返回是否成功。"""
    path = DAILY_TEXT_DIR / f"{_safe_name(did)}.json"
    try:
        if path.exists():
            path.unlink()
        return True
    except OSError:
        return False


def copy_into_dailyfile(src: str) -> Optional[str]:
    """把外部文件复制进 dailyfile，返回相对路径 dailyfile/<新名>。

    失败（源不存在 / 复制出错）返回 None。
    """
    src_path = Path(src)
    if not src_path.is_file():
        return None
    ensure_dirs()
    new_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}_" \
               f"{_safe_name(src_path.name)}"
    dst = DAILY_FILE_DIR / new_name
    try:
        shutil.copy2(src_path, dst)
        return f"dailyfile/{new_name}"
    except OSError:
        return None


def list_dailyfiles() -> List[Path]:
    """列出 dailyfile 下所有文件（按名称升序）。"""
    ensure_dirs()
    return sorted((p for p in DAILY_FILE_DIR.iterdir() if p.is_file()),
                  key=lambda p: p.name.lower())


def delete_dailyfile(name: str) -> bool:
    """删除 dailyfile 中单个文件（名称经 _safe_name 防穿越）。"""
    safe = _safe_name(name)
    if safe in ("", ".", ".."):
        return False
    path = DAILY_FILE_DIR / safe
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _dailyfile_origin_map() -> Dict[str, str]:
    """构建 dailyfile 文件名 -> 来源日记标题 映射（删除文件界面 From: 显示）。

    从全部日记的 attachments 中反向索引：附件相对路径 dailyfile/<名> 归属哪篇日记。
    一个文件可能被多篇日记引用，取第一篇（按 created_at 倒序的最新日记）。
    """
    origin: Dict[str, str] = {}
    for data in list_diaries():
        title = str(data.get("title") or "无标题")
        files = data.get("attachments") or []
        if not isinstance(files, list):
            continue
        for rel in files:
            rel = str(rel)
            if rel.startswith("dailyfile/"):
                name = Path(rel).name
                origin.setdefault(name, title)
    return origin


def _file_url(path: Path) -> str:
    """本地绝对路径 -> file:/// URL（用于富文本显示，中文路径安全）。"""
    return path.resolve().as_uri()


def _rel_to_abs(html: str) -> str:
    """将正文 HTML 中 dailyfile/... 相对路径（src/href）转绝对 file:/// 路径（显示用）。

    需求修复（图片二次打开不显示）：Qt toHtml 会把中文文件名百分号编码，
    旧日记里存的是 src="dailyfile/<百分号编码名>"，读取时必须 unquote 才能
    匹配到真实文件；兼容 src（图片）与 href（附件/音乐链接）。
    """
    def _rep(m: "re.Match[str]") -> str:
        attr = m.group(1)
        rel = m.group(2)
        # 兼容百分号编码与明文文件名，取最后一段文件名
        name = Path(unquote(rel)).name
        return f'{attr}="{_file_url(DAILY_FILE_DIR / name)}"'
    return re.sub(r'(src|href)="dailyfile/([^"]+)"', _rep, html)


def _abs_to_rel(html: str) -> str:
    """将正文 HTML 中绝对 file:/// 路径（src/href）转回 dailyfile/... 相对路径（存储用）。

    需求修复：写入时 unquote 归一化为明文文件名，二次打开不再出现路径编码不一致。
    """
    prefix = DAILY_FILE_DIR.resolve().as_uri().rstrip("/")

    def _rep(m: "re.Match[str]") -> str:
        attr = m.group(1)
        url = m.group(2)
        if url.startswith(prefix):
            rel = unquote(url[len(prefix) + 1:])
            return f'{attr}="dailyfile/{rel}"'
        return m.group(0)
    return re.sub(r'(src|href)="(file:///[^"]+)"', _rep, html)


# ================================================================
# PySide6 导入
# ================================================================
try:
    from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize
    from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPainterPath, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
        QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    print("=" * 60)
    print("[启动失败] 依赖 PySide6 未安装。")
    print("请先安装依赖：pip install PySide6")
    print("=" * 60)
    raise SystemExit(1)

from utils.styled_msg import (  # noqa: E402
    styled_confirm, styled_info, styled_question, styled_warning)


def _accent_from_config() -> str:
    """读取主项目主题色（失败回退默认蓝 #6c8ef5）。"""
    cfg = _load_json(ROOT / "data" / "config.json", {})
    if isinstance(cfg, dict):
        try:
            accent = (cfg.get("ui") or {}).get("accent")
            if accent:
                return str(accent)
        except Exception:  # noqa: BLE001
            pass
    return ACCENT


def _theme_from_config() -> Tuple[str, bool]:
    """读取主项目主题背景配置：返回 (theme_file 路径, blur_background 开关)。"""
    cfg = _load_json(ROOT / "data" / "config.json", {})
    if isinstance(cfg, dict):
        try:
            ui = cfg.get("ui") or {}
            theme_file = str(ui.get("theme_file") or "")
            blur = bool(ui.get("blur_background", False))
            return theme_file, blur
        except Exception:  # noqa: BLE001
            pass
    return "", False


def _blur_image(path: str, radius: int = 18) -> Any:
    """用 Pillow 预模糊主题背景（与主程序 ui_manager 一致）。失败时回退原图。"""
    try:
        from PIL import Image, ImageFilter
        from PySide6.QtGui import QImage
        img = Image.open(path).convert("RGB")
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)
    except Exception:  # noqa: BLE001
        return QPixmap(path)


class _FramelessMixin:
    """无边框圆角半透明窗口通用骨架（拖动 / 最小化 / 最大化 / 关闭 / 四边缩放）。

    与主程序 ui_manager / skilltools 的实现保持一致。
    """

    def _setup_frameless(self, title: str) -> None:
        self._accent = _accent_from_config()
        self._scale = 1.0
        self._dragging = False
        self._drag_offset = QPoint()
        self._background_pm: Optional[Any] = None
        self._load_theme()
        flags = (
            Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        # QDialog 默认是 Qt.Dialog 窗口类型，最小化/最大化行为无效；
        # 追加 Qt.Window 使其成为可最小化/最大化的普通顶级窗口（需求：按钮可用）
        if isinstance(self, QDialog):
            flags |= Qt.Window
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self._init_resize()

    # ---------------------------------------------------------- 主题背景
    def _load_theme(self) -> None:
        """加载主界面主题背景图（继承 data/config.json 的 ui.theme_file）。

        无主题配置或文件不存在时保持默认半透明底色（与主界面一致）。
        """
        self._background_pm = None
        theme_file, blur = _theme_from_config()
        path = Path(theme_file)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            try:
                self._background_pm = (
                    _blur_image(str(path), 24) if blur else QPixmap(str(path)))
            except Exception:  # noqa: BLE001
                self._background_pm = None

    def _wrap_central(self, central: Any) -> None:
        """把中心容器铺满整个窗口（QDialog 必须显式设置外层布局）。

        QMainWindow 用 setCentralWidget 自动拉伸；QDialog 需要将 central
        放入 self 的外层布局，否则内容只占左侧最小宽度。
        """
        from PySide6.QtWidgets import QSizePolicy
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if isinstance(self, QDialog):
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(central)

    # ---------------------------------------------------------- 标题栏拖动
    def _title_press(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()

    def _title_move(self, event: Any) -> None:
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _title_release(self, event: Any) -> None:
        self._dragging = False
        event.accept()

    def _on_min(self) -> None:
        """最小化窗口（兼容无边框 QMainWindow 与 QDialog）。

        仅 showMinimized() 对无边框窗口可能不生效，追加 setWindowState 兜底。
        """
        self.showMinimized()
        try:
            self.setWindowState(self.windowState() | Qt.WindowMinimized)
        except Exception:  # noqa: BLE001
            pass

    def _on_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_close(self) -> None:
        self.close()

    # ---------------------------------------------------------- 四边缩放
    def _init_resize(self) -> None:
        self._edge = 8
        self._resize_dir = 0
        self._resize_start_global = QPoint()
        self._resize_start_geom = QRect()
        self._resize_start_minw = 0
        self._resize_start_minh = 0
        self._enable_mouse_tracking(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    @staticmethod
    def _enable_mouse_tracking(widget: QWidget) -> None:
        widget.setMouseTracking(True)
        for child in widget.findChildren(QWidget):
            try:
                child.setMouseTracking(True)
            except Exception:  # noqa: BLE001
                pass

    def _resize_edge(self, gpos: QPoint) -> int:
        g = self.geometry()
        e = self._edge
        d = 0
        if g.left() <= gpos.x() <= g.left() + e:
            d |= 1
        if g.right() - e <= gpos.x() <= g.right():
            d |= 2
        if g.top() <= gpos.y() <= g.top() + e:
            d |= 4
        if g.bottom() - e <= gpos.y() <= g.bottom():
            d |= 8
        return d

    def _apply_resize(self, gpos: QPoint) -> None:
        dx = gpos.x() - self._resize_start_global.x()
        dy = gpos.y() - self._resize_start_global.y()
        sg = self._resize_start_geom
        x, y, w, h = sg.x(), sg.y(), sg.width(), sg.height()
        if self._resize_dir & 1:
            x = sg.x() + dx
            w = sg.width() - dx
        if self._resize_dir & 2:
            w = sg.width() + dx
        if self._resize_dir & 4:
            y = sg.y() + dy
            h = sg.height() - dy
        if self._resize_dir & 8:
            h = sg.height() + dy
        if w < self._resize_start_minw:
            if self._resize_dir & 1:
                x -= self._resize_start_minw - w
            w = self._resize_start_minw
        if h < self._resize_start_minh:
            if self._resize_dir & 4:
                y -= self._resize_start_minh - h
            h = self._resize_start_minh
        self.setGeometry(x, y, w, h)

    def _update_resize_cursor(self, gpos: QPoint) -> None:
        d = self._resize_edge(gpos)
        if d == 0:
            self.unsetCursor()
        elif d in (1, 2):
            self.setCursor(Qt.SizeHorCursor)
        elif d in (4, 8):
            self.setCursor(Qt.SizeVerCursor)
        elif d in (5, 10):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.SizeBDiagCursor)

    def _resize_event(self, obj: Any, event: Any) -> bool:
        if not isinstance(obj, QWidget) or not (obj is self or self.isAncestorOf(obj)):
            return False
        if not isinstance(event, QMouseEvent):
            return False
        gpos = event.globalPosition().toPoint()
        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if not (self.isMaximized() or self.isFullScreen()):
                d = self._resize_edge(gpos)
                if d:
                    self._resize_dir = d
                    self._resize_start_global = gpos
                    self._resize_start_geom = QRect(self.geometry())
                    self._resize_start_minw = self.minimumWidth()
                    self._resize_start_minh = self.minimumHeight()
                    return True
        elif etype == QEvent.MouseMove:
            if self._resize_dir:
                self._apply_resize(gpos)
                return True
            if not (self.isMaximized() or self.isFullScreen()):
                self._update_resize_cursor(gpos)
        elif etype == QEvent.MouseButtonRelease:
            if self._resize_dir:
                self._resize_dir = 0
                self.unsetCursor()
                return True
        elif etype == QEvent.Leave and obj is self and not self._resize_dir:
            self.unsetCursor()
        return False

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if self._resize_event(obj, event):
            return True
        return super().eventFilter(obj, event)

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setBrush(QColor(244, 245, 250, 210))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 16, 16)
        if getattr(self, "_background_pm", None) and not self._background_pm.isNull():
            scaled = self._background_pm.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(rect, 16, 16)
            painter.setClipPath(path)
            painter.setOpacity(0.55)
            # 居中裁剪绘制，避免把 theme 图拉伸变形（需求：不压缩拉伸）
            src_rect = QRect(
                (scaled.width() - self.width()) // 2,
                (scaled.height() - self.height()) // 2,
                self.width(), self.height())
            painter.drawPixmap(rect, scaled, src_rect)
            painter.restore()
        painter.end()
        super().paintEvent(event)



# ================================================================
# 一级界面：日记列表主页
# ================================================================
class DailyMainWindow(_FramelessMixin, QMainWindow):
    """日记列表主页：展示全部日记卡片（标题 / 建立日期 / 上次修改日期）。

    右上角提供「新建日记」与「删除文件」按钮；每篇卡片带删除按钮（img/dailydel.png）。
    """

    def __init__(self) -> None:
        super().__init__(None)
        self._setup_frameless("日记本")
        # 窗口比例与主界面一致（主界面 920x640 最小 / 1200x780 默认）
        self.setMinimumSize(920, 640)
        self.resize(1200, 780)
        self._press_did = ""
        self._build_ui()
        self._refresh_cards()

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        self._wrap_central(central)
        central.setStyleSheet(_root_qss(self._accent, self._scale))
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar())

        body = QVBoxLayout()
        body.setContentsMargins(16, 8, 16, 14)
        body.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("我的日记")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._count_label = QLabel()
        self._count_label.setObjectName("statusText")
        head.addWidget(self._count_label)
        body.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("mainPanel")
        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(10, 10, 10, 10)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_host)
        apply_shadow(self._scroll)
        body.addWidget(self._scroll, 1)
        root.addLayout(body)

    # ------------------------------------------------------------ 标题栏
    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("日记本")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        sub = QLabel("Celestia AssistantAI · 子项目")
        sub.setObjectName("statusText")
        layout.addWidget(sub)
        layout.addStretch(1)

        new_btn = QPushButton("新建日记")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setObjectName("primaryBtn")
        new_btn.clicked.connect(self._on_new)
        layout.addWidget(new_btn)

        file_btn = QPushButton("删除文件")
        file_btn.setCursor(Qt.PointingHandCursor)
        file_btn.setObjectName("ghostBtn")
        file_btn.clicked.connect(self._on_manage_files)
        layout.addWidget(file_btn)

        for text, tip, slot in (
            ("─", "最小化", self._on_min), ("□", "最大化/还原", self._on_max),
            ("×", "关闭", self._on_close),
        ):
            btn = QPushButton(text)
            btn.setFixedSize(34, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            btn.setObjectName("titleBtn")
            layout.addWidget(btn)

        bar.mousePressEvent = self._title_press
        bar.mouseMoveEvent = self._title_move
        bar.mouseReleaseEvent = self._title_release
        return bar

    # ------------------------------------------------------------ 卡片列表
    def _refresh_cards(self) -> None:
        """重建日记卡片列表。"""
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        diaries = list_diaries()
        if not diaries:
            hint = QLabel("还没有日记。点击右上角「新建日记」开始记录。")
            hint.setObjectName("emptyHint")
            hint.setAlignment(Qt.AlignCenter)
            self._cards_layout.insertWidget(0, hint)
        else:
            for data in diaries:
                self._cards_layout.insertWidget(
                    0, self._build_card(data))
        self._count_label.setText(f"共 {len(diaries)} 篇")

    def _build_card(self, data: Dict[str, Any]) -> QWidget:
        """构建单篇日记卡片（标题 / 建立日期 / 上次修改日期 / 删除按钮）。

        卡片水平方向占满整个界面宽度（需求：内容完整布局整个界面）。
        """
        did = str(data.get("id") or "")
        card = QFrame()
        card.setObjectName("cardWidget")
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(86)
        # 水平方向尽量扩展，完整占满界面宽度
        from PySide6.QtWidgets import QSizePolicy
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.setMinimumWidth(720)
        apply_shadow(card, blur=10, y=2, alpha=30)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 10, 10)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(4)

        title_lbl = QLabel(str(data.get("title") or "无标题"))
        title_lbl.setObjectName("cardTitle")
        info.addWidget(title_lbl)

        created = str(data.get("created_at") or "-")
        updated = str(data.get("updated_at") or "-")
        date_lbl = QLabel(f"建立：{created}    上次修改：{updated}")
        date_lbl.setObjectName("cardDate")
        info.addWidget(date_lbl)

        layout.addLayout(info, 1)

        del_btn = QPushButton()
        del_btn.setObjectName("delIconBtn")
        del_btn.setFixedSize(32, 32)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("删除这篇日记")
        icon_path = IMG_DELETE
        if icon_path.exists():
            del_btn.setIcon(QPixmap(str(icon_path)).scaled(
                20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            del_btn.setText("删除")
        del_btn.clicked.connect(lambda _=False, d=did: self._on_delete_diary(d))
        layout.addWidget(del_btn)

        card.mousePressEvent = lambda e, d=did: self._card_press(e, d)
        card.mouseReleaseEvent = self._card_release
        return card

    def _card_press(self, event: Any, did: str) -> None:
        if event.button() == Qt.LeftButton:
            self._press_did = did
            event.accept()

    def _card_release(self, event: Any) -> None:
        did = self._press_did
        self._press_did = ""
        if event.button() == Qt.LeftButton and did:
            self._open_edit(did)
        event.accept()

    # ------------------------------------------------------------ 操作
    def _on_new(self) -> None:
        """新建日记：打开空白编辑窗口。"""
        self._open_edit(None)

    def _open_edit(self, did: Optional[str]) -> None:
        """打开二级编辑界面（did 为空 = 新建）。

        保存后刷新卡片列表，保证新建/编辑的日记立即出现在主页。
        """
        win = DailyEditWindow(self, did=did)
        win.exec()
        self._refresh_cards()

    def _on_delete_diary(self, did: str) -> None:
        """删除单篇日记（带 HTML5 风格确认）。"""
        if not did:
            return
        title = read_diary(did).get("title", "") if read_diary(did) else ""
        if not styled_confirm(
                self, "删除日记",
                f"确定删除日记「{title}」吗？此操作不可撤销。",
                danger=True):
            return
        if delete_diary(did):
            self._refresh_cards()
        else:
            styled_warning(self, "删除失败", "日记文件删除失败，请检查权限。")

    def _on_manage_files(self) -> None:
        """打开附件文件管理器（删除 dailydata/dailyfile 中的文件）。"""
        dlg = FileManageDialog(self)
        dlg.exec()


class _LinkTextEdit(QTextEdit):
    """可编辑 QTextEdit：点击正文中的 <a href> 链接时用系统默认程序打开。

    PySide6 6.11 移除了 QTextEdit 的 anchorClicked / linkActivated 信号与
    setOpenExternalLinks 方法，改用 anchorAt(pos) + 鼠标事件自行处理。
    """

    def mouseReleaseEvent(self, event: Any) -> None:
        anchor = self.anchorAt(event.position().toPoint())
        if anchor and event.button() == Qt.LeftButton:
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(anchor))
            except Exception:  # noqa: BLE001
                pass
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ================================================================
# 二级界面：日记编辑界面
# ================================================================
class DailyEditWindow(_FramelessMixin, QDialog):
    """日记编辑界面：标题 / 正文 / 插入图片 / 插入文件 / 角色专属多选 / 保存。

    保存时自动记录首次保存时间 created_at 与最近修改时间 updated_at。
    """

    def __init__(self, parent: Optional[QWidget], did: Optional[str] = None) -> None:
        super().__init__(parent)
        self._setup_frameless("编辑日记")
        self._diary_id = did
        self._data: Dict[str, Any] = read_diary(did) if did else {}
        self._attachments: List[str] = []
        # 音乐附件播放器（rel -> QMediaPlayer）与循环开关（rel -> bool）
        self._music_players: Dict[str, Any] = {}
        self._music_loops: Dict[str, bool] = {}
        # 正文图片显示比例（1.0=完整 / 0.5 / 0.25 / 0.15）
        self._image_scale = 1.0
        # 窗口比例与主界面一致
        self.setMinimumSize(920, 640)
        self.resize(1200, 780)
        self._build_ui()
        self._load_data()

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        self._wrap_central(central)
        central.setStyleSheet(_root_qss(self._accent, self._scale))
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_title_bar())

        body = QVBoxLayout()
        body.setContentsMargins(16, 8, 16, 14)
        body.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_lbl = QLabel("标题")
        title_lbl.setObjectName("sectionTitle")
        title_row.addWidget(title_lbl)
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("请输入日记标题")
        title_row.addWidget(self._title_edit, 1)
        body.addLayout(title_row)

        self._editor = _LinkTextEdit()
        self._editor.setPlaceholderText("记录今天的内容……可插入图片与文件。")
        # 链接点击由 _LinkTextEdit.mouseReleaseEvent 用系统默认程序打开
        body.addWidget(self._editor, 3)

        # 需求：附件在编辑区下方管理（图标+文件名+日期+删除键，每文件一行，
        # 文件名超宽自动换行增加行高而非拉宽编辑框；多文件时滚动显示）
        self._attach_title = QLabel("附件管理（文件与音乐）")
        self._attach_title.setObjectName("sectionTitle")
        body.addWidget(self._attach_title)
        self._attach_scroll = QScrollArea()
        self._attach_scroll.setWidgetResizable(True)
        self._attach_scroll.setFrameShape(QFrame.NoFrame)
        self._attach_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._attach_scroll.setMaximumHeight(150)
        self._attach_container = QWidget()
        self._attach_rows = QVBoxLayout(self._attach_container)
        self._attach_rows.setContentsMargins(0, 0, 0, 0)
        self._attach_rows.setSpacing(4)
        self._attach_rows.addStretch(1)
        self._attach_scroll.setWidget(self._attach_container)
        body.addWidget(self._attach_scroll)
        self._attach_title.setVisible(False)
        self._attach_scroll.setVisible(False)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        img_btn = QPushButton("插入图片")
        img_btn.setCursor(Qt.PointingHandCursor)
        img_btn.setObjectName("ghostBtn")
        img_btn.clicked.connect(self._on_insert_image)
        tool_row.addWidget(img_btn)
        file_btn = QPushButton("插入文件")
        file_btn.setCursor(Qt.PointingHandCursor)
        file_btn.setObjectName("ghostBtn")
        file_btn.clicked.connect(self._on_insert_file)
        tool_row.addWidget(file_btn)
        # 需求：日记可插入音乐（mp3/wav/flac 等，复制进 dailyfile 存为链接）
        music_btn = QPushButton("插入音乐")
        music_btn.setCursor(Qt.PointingHandCursor)
        music_btn.setObjectName("ghostBtn")
        music_btn.clicked.connect(self._on_insert_music)
        tool_row.addWidget(music_btn)

        # 图片缩放大小（需求：完整 / 页面50% / 页面25% / 页面15%）
        img_scale_lbl = QLabel("图片大小")
        img_scale_lbl.setObjectName("statusText")
        tool_row.addWidget(img_scale_lbl)
        self._scale_combo = QComboBox()
        self._scale_combo.addItems(
            ["完整大小", "页面的50%大", "页面的25%中", "页面的15%小"])
        self._scale_combo.setCurrentIndex(0)
        self._scale_combo.currentIndexChanged.connect(
            self._on_image_scale_changed)
        tool_row.addWidget(self._scale_combo)

        self._attach_label = QLabel("已插入附件：无")
        self._attach_label.setObjectName("statusText")
        tool_row.addWidget(self._attach_label, 1)
        body.addLayout(tool_row)

        self._build_role_area(body)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setObjectName("ghostBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        body.addLayout(btn_row)

        layout.addLayout(body, 1)

    def _build_title_bar(self) -> QWidget:
        """编辑界面标题栏（含最小化 / 最大化 / 关闭）。"""
        bar = QWidget()
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("编辑日记" if self._diary_id else "新建日记")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        for text, tip, slot in (
            ("─", "最小化", self._on_min), ("□", "最大化/还原", self._on_max),
            ("×", "关闭", self.reject),
        ):
            btn = QPushButton(text)
            btn.setFixedSize(34, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            btn.setObjectName("titleBtn")
            layout.addWidget(btn)

        bar.mousePressEvent = self._title_press
        bar.mouseMoveEvent = self._title_move
        bar.mouseReleaseEvent = self._title_release
        return bar

    # ------------------------------------------------------------ 角色专属
    def _build_role_area(self, body: QVBoxLayout) -> None:
        """角色专属多选区：角色（roles/ 含 roles.json）+ 群组（group.json）。"""
        from PySide6.QtWidgets import QGroupBox
        group = QGroupBox("角色专属（选填，可多选：仅这些角色可了解该日记）")
        group.setObjectName("roleGroup")
        glayout = QVBoxLayout(group)
        glayout.setContentsMargins(12, 18, 12, 10)
        glayout.setSpacing(4)

        self._role_list = QListWidget()
        self._role_list.setMaximumHeight(120)
        roles = list_roles()
        groups = list_groups()
        if not roles and not groups:
            hint = QLabel("未发现角色 / 群组（roles/ 目录为空时可不选）。")
            hint.setObjectName("statusText")
            glayout.addWidget(hint)
        else:
            for name in roles:
                item = QListWidgetItem(f"角色：{role_display(name)}（{name}）")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, name)
                self._role_list.addItem(item)
            for gname in groups:
                item = QListWidgetItem(f"群组：{gname}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, gname)
                self._role_list.addItem(item)
            glayout.addWidget(self._role_list)
        body.addWidget(group)

    def _selected_roles(self) -> List[str]:
        """读取勾选的角色 / 群组名列表。"""
        out: List[str] = []
        if not hasattr(self, "_role_list"):
            return out
        for i in range(self._role_list.count()):
            item = self._role_list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(str(item.data(Qt.UserRole) or ""))
        return [x for x in out if x]

    # ------------------------------------------------------------ 数据加载 / 保存
    def _load_data(self) -> None:
        """载入已有日记内容（新建为空）。"""
        if not self._data:
            return
        self._title_edit.setText(str(self._data.get("title") or ""))
        html = str(self._data.get("content_html") or "")
        if html:
            self._editor.setHtml(_rel_to_abs(html))
        allowed = self._data.get("allowed_roles") or []
        if isinstance(allowed, list) and hasattr(self, "_role_list"):
            for i in range(self._role_list.count()):
                item = self._role_list.item(i)
                ud = str(item.data(Qt.UserRole) or "")
                if ud in allowed:
                    item.setCheckState(Qt.Checked)
        files = self._data.get("attachments") or []
        if isinstance(files, list) and files:
            self._attachments = [str(x) for x in files]
            self._update_attach_label()
            self._refresh_attach_list()

    def _on_save(self) -> None:
        """保存日记：首次记录 created_at，刷新 updated_at。"""
        title = self._title_edit.text().strip()
        if not title:
            styled_info(self, "提示", "请先填写日记标题。")
            self._title_edit.setFocus()
            return
        html = _abs_to_rel(self._editor.toHtml())
        data = dict(self._data)
        data.update({
            "id": self._diary_id or _unique_id(),
            "title": title,
            "content_html": html,
            "allowed_roles": self._selected_roles(),
            "attachments": self._attachments,
        })
        if save_diary(data):
            self._data = data
            self._diary_id = data.get("id")
            self.accept()
        else:
            styled_warning(self, "保存失败", "写入日记文件失败，请检查磁盘与权限。")

    # ------------------------------------------------------------ 附件管理（底部列表）
    @staticmethod
    def _is_image_rel(rel: str) -> bool:
        """图片附件（正文内联显示，不列入底部附件列表）。"""
        return Path(rel).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

    @staticmethod
    def _is_music_rel(rel: str) -> bool:
        """音乐附件（底部以简易播放器形式显示：播放/暂停 + 循环 + 删除）。"""
        return Path(rel).suffix.lower() in {
            ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}

    @staticmethod
    def _file_date(rel: str) -> str:
        """附件复制日期（取自 dailyfile 中文件的 mtime）。"""
        try:
            st = (DAILY_FILE_DIR / Path(rel).name).stat()
            return datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""

    def _update_attach_label(self) -> None:
        """刷新已插入附件提示文本（工具栏计数）。"""
        if self._attachments:
            self._attach_label.setText(f"已插入附件：{len(self._attachments)} 个")
        else:
            self._attach_label.setText("已插入附件：无")

    def _make_attach_row(self, rel: str) -> QWidget:
        """构建单条附件行：音乐附件 -> 简易播放器（播放/暂停+循环+删除）；
        普通文件 -> 图标+文件名（可换行）+日期+删除键。"""
        if self._is_music_rel(rel):
            return self._make_music_row(rel)
        row = QFrame()
        row.setObjectName("attachRow")
        row.setStyleSheet(
            "QFrame#attachRow{background:rgba(108,142,245,0.06);"
            "border:1px solid #e5e7f0;border-radius:8px;}"
            "QLabel{background:transparent;border:none;}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        icon = QLabel()
        pm = QPixmap(str(ROOT / "img" / "file.png"))
        icon.setPixmap(pm.scaled(18, 18, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation))
        icon.setFixedSize(18, 18)
        lay.addWidget(icon)

        name = QLabel(Path(rel).name)
        name.setWordWrap(True)
        name.setToolTip(str(DAILY_FILE_DIR / Path(rel).name))
        name.setStyleSheet("color:#2b2b33;font-size:13px;")
        lay.addWidget(name, 1)

        date_lbl = QLabel(self._file_date(rel))
        date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        date_lbl.setStyleSheet("color:#9aa0ac;font-size:12px;")
        lay.addWidget(date_lbl)

        del_btn = QPushButton()
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFixedSize(24, 24)
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setIcon(QIcon(str(ROOT / "img" / "notedel.png")))
        del_btn.setToolTip("删除该附件")
        del_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:12px;}")
        del_btn.clicked.connect(
            lambda _=False, r=rel: self._remove_attachment(r))
        lay.addWidget(del_btn)
        return row

    def _make_music_row(self, rel: str) -> QWidget:
        """构建音乐附件简易播放器行：
        file.png 图标 + 文件名 + 日期 + 播放/暂停 + 循环 + 删除。

        使用 QMediaPlayer 本地播放，播放/暂停按钮随状态切换系统图标，
        循环按钮为可勾选开关，删除后播放器自动停止释放。
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtWidgets import QStyle

        row = QFrame()
        row.setObjectName("attachRow")
        row.setStyleSheet(
            "QFrame#attachRow{background:rgba(108,142,245,0.06);"
            "border:1px solid #e5e7f0;border-radius:8px;}"
            "QLabel{background:transparent;border:none;}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        icon = QLabel()
        pm = QPixmap(str(ROOT / "img" / "file.png"))
        icon.setPixmap(pm.scaled(18, 18, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation))
        icon.setFixedSize(18, 18)
        lay.addWidget(icon)

        name = QLabel(Path(rel).name)
        name.setWordWrap(True)
        name.setToolTip(str(DAILY_FILE_DIR / Path(rel).name))
        name.setStyleSheet("color:#2b2b33;font-size:13px;")
        lay.addWidget(name, 1)

        date_lbl = QLabel(self._file_date(rel))
        date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        date_lbl.setStyleSheet("color:#9aa0ac;font-size:12px;")
        lay.addWidget(date_lbl)

        # 播放器（每次构建行时创建，_refresh_attach_list 会先清理旧的）
        player = QMediaPlayer(self)
        audio = QAudioOutput(self)
        audio.setVolume(0.8)
        player.setAudioOutput(audio)
        src = DAILY_FILE_DIR / Path(rel).name
        if src.exists():
            player.setSource(QUrl.fromLocalFile(str(src)))
        else:
            player.setSource(QUrl(""))
        self._music_players[rel] = player
        self._music_loops.setdefault(rel, False)

        btn_ss = (
            "QPushButton{background:rgba(108,142,245,0.10);border:none;"
            "border-radius:13px;}"
            "QPushButton:hover{background:rgba(108,142,245,0.22);}"
            "QPushButton:pressed{background:rgba(108,142,245,0.30);}")

        play_btn = QPushButton()
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setFixedSize(26, 26)
        play_btn.setIconSize(QSize(16, 16))
        play_btn.setStyleSheet(btn_ss)
        play_btn.setToolTip("播放/暂停")

        def _sync_play_icon(state: Any) -> None:
            if state == QMediaPlayer.PlaybackState.PlayingState:
                play_btn.setIcon(self.style().standardIcon(
                    QStyle.StandardPixmap.SP_MediaPause))
            else:
                play_btn.setIcon(self.style().standardIcon(
                    QStyle.StandardPixmap.SP_MediaPlay))

        def _toggle_play() -> None:
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                player.pause()
            else:
                player.play()

        play_btn.clicked.connect(_toggle_play)
        player.playbackStateChanged.connect(_sync_play_icon)
        _sync_play_icon(player.playbackState())
        lay.addWidget(play_btn)

        loop_btn = QPushButton("循环")
        loop_btn.setCursor(Qt.PointingHandCursor)
        loop_btn.setCheckable(True)
        loop_btn.setChecked(self._music_loops.get(rel, False))
        loop_btn.setToolTip("循环播放")
        loop_btn.setStyleSheet(
            "QPushButton{background:rgba(108,142,245,0.10);border:none;"
            "border-radius:12px;padding:2px 10px;font-size:12px;color:#4a5a8a;}"
            "QPushButton:hover{background:rgba(108,142,245,0.22);}"
            "QPushButton:checked{background:#6c8ef5;color:white;}"
            "QPushButton:checked:hover{background:#8fb0ff;}")

        def _on_loop(checked: bool) -> None:
            self._music_loops[rel] = checked
            player.setLoops(QMediaPlayer.Loops.Infinite
                            if checked else QMediaPlayer.Loops.Once)

        loop_btn.toggled.connect(_on_loop)
        _on_loop(loop_btn.isChecked())
        lay.addWidget(loop_btn)

        del_btn = QPushButton()
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFixedSize(24, 24)
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setIcon(QIcon(str(ROOT / "img" / "notedel.png")))
        del_btn.setToolTip("删除该音乐附件")
        del_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:12px;}")
        del_btn.clicked.connect(
            lambda _=False, r=rel: self._remove_attachment(r))
        lay.addWidget(del_btn)
        return row

    def _refresh_attach_list(self) -> None:
        """重建底部附件列表（每文件一行，图片附件不重复列入）。
        重建前停止并释放全部音乐播放器，随后按需重建。"""
        if not hasattr(self, "_attach_rows"):
            return
        # 停止并释放旧播放器，避免刷新重建行时资源泄漏
        for p in self._music_players.values():
            try:
                p.stop()
                p.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._music_players.clear()
        while self._attach_rows.count() > 1:
            item = self._attach_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        shown = False
        for rel in list(self._attachments):
            if self._is_image_rel(rel):
                continue
            self._attach_rows.insertWidget(
                self._attach_rows.count() - 1, self._make_attach_row(rel))
            shown = True
        self._attach_title.setVisible(shown)
        self._attach_scroll.setVisible(shown)

    def _remove_attachment(self, rel: str) -> None:
        """删除日记中的一个附件引用（保留 dailyfile 中的物理文件）。
        若是音乐附件，先停止并释放其播放器。"""
        name = Path(rel).name
        if not styled_question(self, "删除附件",
                               f"确定从这篇日记中移除附件「{name}」吗？",
                               danger=True):
            return
        p = self._music_players.pop(rel, None)
        if p is not None:
            try:
                p.stop()
                p.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._music_loops.pop(rel, None)
        if rel in self._attachments:
            self._attachments.remove(rel)
        self._update_attach_label()
        self._refresh_attach_list()

    def _on_insert_image(self) -> None:
        """选择图片 -> 复制进 dailyfile -> 光标处插入正文（按当前缩放比例显示）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not path:
            return
        rel = copy_into_dailyfile(path)
        if rel is None:
            styled_warning(self, "插入失败", "图片复制进 dailyfile 失败。")
            return
        self._attachments.append(rel)
        self._update_attach_label()
        url = _file_url(DAILY_FILE_DIR / Path(rel).name)
        # 完整大小插入（原始像素），缩放由 _on_image_scale_changed 统一控制
        self._editor.textCursor().insertHtml(
            f'<img src="{url}" style="display:block;">')
        self._apply_image_scale()

    def _on_image_scale_changed(self, _index: int) -> None:
        """图片缩放比例切换：重设正文中所有图片的显示宽度。"""
        self._apply_image_scale()

    def _apply_image_scale(self) -> None:
        """按当前缩放比例（完整 / 50% / 25% / 15%）调整正文所有图片宽度。

        基准：编辑区可视宽度 × 比例；完整大小 = 图片原始像素（不超过可视宽度）。
        """
        if not hasattr(self, "_scale_combo"):
            return
        ratios = {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.15}
        ratio = ratios.get(self._scale_combo.currentIndex(), 1.0)
        self._image_scale = ratio
        doc = self._editor.document()
        view_w = max(self._editor.viewport().width() - 20, 200)
        from PySide6.QtGui import QTextImageFormat, QTextCursor

        def _blocks() -> list:
            out = []
            block = doc.begin()
            while block.isValid():
                out.append(block)
                block = block.next()
            return out

        # 遍历所有文本块中的图片 fragment
        for block in _blocks():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isImageFormat():
                    fmt = frag.charFormat().toImageFormat()
                    # 原始像素（完整大小）
                    pix = QPixmap(fmt.name())
                    orig_w = pix.width() if not pix.isNull() else view_w
                    new_w = int(orig_w * ratio) if ratio < 1.0 else orig_w
                    new_w = max(1, min(new_w, view_w))
                    new_fmt = QTextImageFormat(fmt)
                    new_fmt.setWidth(new_w)
                    # 等比高度
                    if not pix.isNull() and pix.height() > 0:
                        new_fmt.setHeight(int(new_w * pix.height() / pix.width()))
                    cursor = QTextCursor(doc)
                    cursor.setPosition(frag.position())
                    cursor.setPosition(frag.position() + frag.length(),
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(new_fmt)
                it += 1
        self._editor.document().setDocumentMargin(
            self._editor.document().documentMargin())
        self._editor.viewport().update()

    def _on_insert_music(self) -> None:
        """插入音乐文件 -> 复制进 dailyfile -> 加入底部附件管理列表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音乐", "",
            "音乐文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma)")
        if not path:
            return
        rel = copy_into_dailyfile(path)
        if rel is None:
            styled_warning(self, "插入失败", "音乐复制进 dailyfile 失败。")
            return
        self._attachments.append(rel)
        self._update_attach_label()
        self._refresh_attach_list()

    def _on_insert_file(self) -> None:
        """选择任意文件 -> 复制进 dailyfile -> 加入底部附件管理列表。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "所有文件 (*)")
        if not path:
            return
        rel = copy_into_dailyfile(path)
        if rel is None:
            styled_warning(self, "插入失败", "文件复制进 dailyfile 失败。")
            return
        self._attachments.append(rel)
        self._update_attach_label()
        self._refresh_attach_list()

    def _stop_all_players(self) -> None:
        """停止并释放所有音乐播放器（无论是否循环，关闭即停止）。"""
        for p in self._music_players.values():
            try:
                p.stop()
                p.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._music_players.clear()
        self._music_loops.clear()

    def done(self, result: int) -> None:  # noqa: D102
        """对话框关闭统一出口（accept/reject/done）：先停止所有音乐播放。"""
        self._stop_all_players()
        super().done(result)

    def closeEvent(self, event: Any) -> None:  # noqa: D102
        """关闭前停止所有音乐播放器并释放资源（不强制保存，用户可取消）。"""
        self._stop_all_players()
        event.accept()


# ================================================================
# 附件文件管理器（删除 dailydata/dailyfile 中的文件）
# ================================================================
class FileManageDialog(_FramelessMixin, QDialog):
    """列出 dailydata/dailyfile 下所有附件文件，可勾选后删除。"""

    def __init__(self, parent: Optional[QWidget]) -> None:
        super().__init__(parent)
        self._setup_frameless("删除文件")
        # 窗口比例与主界面一致（文件管理为较小的子窗口，按主界面比例缩放）
        self.setMinimumSize(760, 540)
        self.resize(900, 660)
        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        self._wrap_central(central)
        central.setStyleSheet(_root_qss(self._accent, self._scale))
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_title_bar())

        body = QVBoxLayout()
        body.setContentsMargins(16, 10, 16, 14)
        body.setSpacing(10)

        hint = QLabel("勾选要删除的附件文件（位于 dailydata/dailyfile/）。")
        hint.setObjectName("statusText")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self._file_list = QListWidget()
        self._file_list.setSelectionMode(QListWidget.NoSelection)
        body.addWidget(self._file_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        del_btn = QPushButton("删除选中")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(del_btn)
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setObjectName("ghostBtn")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        body.addLayout(btn_row)

        layout.addLayout(body, 1)

    def _build_title_bar(self) -> QWidget:
        """文件管理器标题栏。"""
        bar = QWidget()
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("删除文件")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        for text, tip, slot in (
            ("─", "最小化", self._on_min), ("□", "最大化/还原", self._on_max),
            ("×", "关闭", self.accept),
        ):
            btn = QPushButton(text)
            btn.setFixedSize(34, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            btn.setObjectName("titleBtn")
            layout.addWidget(btn)

        bar.mousePressEvent = self._title_press
        bar.mouseMoveEvent = self._title_move
        bar.mouseReleaseEvent = self._title_release
        return bar

    # ------------------------------------------------------------ 列表
    def _refresh_list(self) -> None:
        """重建附件文件列表（文件名 + 大小 + 修改时间 + From:来源日记 + 勾选框）。"""
        self._file_list.clear()
        files = list_dailyfiles()
        if not files:
            item = QListWidgetItem("附件目录为空，没有可删除的文件。")
            item.setFlags(Qt.NoItemFlags)
            self._file_list.addItem(item)
            return
        origin_map = _dailyfile_origin_map()
        for p in files:
            try:
                size = p.stat().st_size
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                size = 0
                mtime = datetime.now()
            origin_title = origin_map.get(p.name, "")
            if origin_title:
                origin_text = f"    From:{origin_title}"
            else:
                origin_text = ""
            label = (f"{p.name}    ({size} 字节)    "
                     f"{mtime.strftime(_TS_FMT)}{origin_text}")
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, p.name)
            self._file_list.addItem(item)

    def _on_delete_selected(self) -> None:
        """删除勾选的附件文件。"""
        names = []
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.checkState() == Qt.Checked:
                names.append(str(item.data(Qt.UserRole) or ""))
        if not names:
            styled_info(self, "提示", "请先勾选要删除的文件。")
            return
        if not styled_question(
                self, "删除文件",
                f"确定删除选中的 {len(names)} 个附件文件吗？此操作不可撤销。",
                danger=True):
            return
        ok_count = 0
        for name in names:
            if delete_dailyfile(name):
                ok_count += 1
        self._refresh_list()
        styled_info(
            self, "完成", f"已删除 {ok_count} 个文件（共选择 {len(names)} 个）。")


# ================================================================
# 入口
# ================================================================
def main() -> int:
    """日记本主入口：启动一级界面。"""
    app = QApplication(sys.argv)
    app.setApplicationName("DailyJournal")
    app.setApplicationDisplayName("日记本")
    window = DailyMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())






