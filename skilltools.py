# -*- coding: utf-8 -*-
"""
skilltools.py — 技能工具管理器（Windows 注册表风格 · 与主程序同款 HTML5 响应式界面）

功能：
- 可视化管理 skills/ 目录的技能数据：
    skills/tools_list.json              → 技能目录（name / trigger / aliases / category / enabled）
    skills/skilltools_information.json  → 技能详情（description / prompt_template / parameters）
- 仿注册表编辑器布局：左侧键树（技能库） + 右侧值属性面板
- 支持新建 / 查看 / 修改 / 删除技能，保存后自动维护上述两个 JSON 文件
- 无边框圆角半透明窗口 + 自定义标题栏（拖动/最小化/最大化/关闭）+ 四边缩放

技能字段（适配主程序 \\@技能 唤醒体系）：
    name             名称（唯一；目录条目 + 详情键名）
    trigger          触发词（\\@trigger 唤醒，唯一，[A-Za-z0-9_中文-]+，不含空格）
    kind             类型：skill=普通技能/标签；switch=开关工具（可同时开启 0-2 个）
    aliases          别名（字符串列表，逗号分隔录入）
    category         分类（如 工具 / 记忆 / 创作 / 开关）
    enabled          是否启用（False 时主程序 \\@唤醒 列表中不显示）
    description      技能说明（注入 LLM 的技能上下文）
    prompt_template  执行要求（可含 {参数名} 占位符）
    parameters       参数列表 [{"name", "default", "required"}]

运行方式：python skilltools.py   （仅依赖 PySide6，与主程序一致）

数据一致性：
- 所有写入使用原子替换（先写 .tmp 再 os.replace），避免 JSON 写一半损坏；
- 全部 UTF-8 编码；
- 读取时缺失字段自动回填默认值，保证结构完整；
- 保存 / 删除后自动重建两个 JSON 文件（去重 / 补漏 / 排序）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 与主程序一致的依赖：PySide6
try:
    from PySide6.QtCore import (
        Qt, QEvent, QPoint, QRect, QSize,
    )
    from PySide6.QtGui import (
        QColor, QCursor, QMouseEvent, QPainter, QPainterPath, QPixmap,
    )
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
        QGraphicsDropShadowEffect, QHBoxLayout, QHeaderView, QInputDialog,
        QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
        QScrollArea, QStyle, QTableWidget, QTableWidgetItem, QTextEdit,
        QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError:  # pragma: no cover
    print("=" * 60)
    print("[启动失败] 依赖 PySide6 未安装。")
    print("请先安装依赖：pip install PySide6")
    print("=" * 60)
    raise SystemExit(1)

from utils.styled_msg import (  # noqa: E402
    styled_box, styled_info, styled_question, styled_warning)

# ================================================================
# HTML5 风格常量（与主程序 ui_manager.py 完全一致）
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

# ================================================================
# 路径与字段定义
# ================================================================
ROOT = Path(__file__).resolve().parent
CATALOG_FILE = "tools_list.json"                 # 技能目录（列表）
INFO_FILE = "skilltools_information.json"        # 技能详情（键名 = 技能名称）

def _skills_dir() -> Path:
    """技能数据目录：优先读取主程序配置（paths.skills），失败时回退 ./skills。"""
    try:
        from config_loader import ConfigLoader
        return ConfigLoader.instance().skills_dir
    except Exception:  # noqa: BLE001 - 配置损坏 / 未初始化时回退
        return ROOT / "skills"

SKILLS_DIR = _skills_dir()

# @技能 触发词合法字符（与 skill_manager._TAG_TOKEN 保持一致）
_TRIGGER_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5-]+$")
# Windows 文件名非法字符（技能名不允许，保证安全）
_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 字段：键 → (中文标签, 说明/占位提示)
FIELDS: Dict[str, Tuple[str, str]] = {
    "name":     ("名称", "技能名称（唯一）"),
    "trigger":  ("触发词", "\\@触发词 唤醒技能，例如 translate"),
    "kind":     ("类型", "普通技能 / 开关工具（思维链、联网搜索等可同时开启）"),
    "aliases":  ("别名", "逗号分隔的别名，例如 翻译,fanyi,译一下"),
    "category": ("分类", "技能分类，例如 工具 / 记忆 / 创作 / 开关"),
    "description": ("技能说明", "技能说明（注入 LLM 的技能上下文）"),
    "prompt_template": ("执行要求", "执行要求（可含 {参数名} 占位符，注入 LLM 的上下文）"),
}

_DEFAULT_CATEGORY = "工具"

# ================================================================
# 颜色 / 样式工具函数（与主程序 ui_manager.py 一致）
# ================================================================
def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"

def _lighten(hex_color: str, amt: int) -> str:
    return QColor(hex_color).lighter(100 + amt).name()

def _darken(hex_color: str, amt: int) -> str:
    return QColor(hex_color).darker(100 + amt).name()

def btn_qss(base: str = ACCENT, hover: str = ACCENT_HOVER,
            pressed: str = ACCENT_PRESSED, radius: int = 12,
            text_color: str = "#ffffff", font_size: int = 14,
            bold: bool = True, padding: str = "8px 20px") -> str:
    """HTML5 渐变主按钮样式（仿 CSS :hover/:active）。"""
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
    """幽灵按钮（白底描边，仿 HTML 次按钮）。"""
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
    """危险操作按钮（白底红字描边，删除等）。"""
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

def apply_shadow(widget: QWidget, blur: int = 14, y: int = 3,
                 alpha: int = 45) -> QGraphicsDropShadowEffect:
    """为控件添加柔和投影（Material 层级感）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect

def root_qss(accent: str = ACCENT, scale: float = 1.0) -> str:
    """生成全局 QSS（与主程序 _root_qss_base 完全一致，并补充注册表键树/表格样式）。

    accent 为主题色（跟随主界面设置，实时同步）；scale 为界面缩放系数。
    """
    sel_rgba = _hex_to_rgba(accent, 0.15)
    qss = f"""
QWidget#root {{ background: transparent; }}
QFrame#sidePanel, QFrame#mainPanel {{
    background: {CARD_BG}; border-radius: {RADIUS}px;
}}
QFrame#settingsCard {{
    background: {CARD_BG}; border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#sectionTitle {{
    color: {TEXT_DARK}; font-size: 15px; font-weight: 600;
    padding-left: 10px; border-left: 4px solid {accent};
}}
QLabel#appTitle {{ color: {TEXT_DARK}; font-size: 14px; font-weight: 600; }}
QLabel#toolName {{ color: {TEXT_DARK}; font-size: 16px; font-weight: 600; }}
QLabel#commandTag {{ color: {accent}; font-size: 13px; font-weight: 600; }}
QLabel#statusText {{ color: {TEXT_LIGHT}; font-size: 11px; }}
QLabel#hintText {{ color: {TEXT_LIGHT}; font-size: 12px; }}
QLabel#countText {{ color: {TEXT_MID}; font-size: 12px; }}
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
QLineEdit {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    font-size: 13px; background: white; color: {TEXT_DARK};
}}
QLineEdit:focus {{ border: 2px solid {accent}; padding: 5px 7px; }}
QComboBox {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 6px 10px; font-size: 13px; color: {TEXT_DARK};
}}
QComboBox:hover {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: white; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {sel_rgba};
    selection-color: {accent};
}}
QCheckBox {{ color: {TEXT_DARK}; font-size: 13px; spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: white;
}}
QCheckBox::indicator:checked {{
    background: {accent}; border-color: {accent};
}}
QSpinBox {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 4px 6px;
    font-size: 13px; background: white;
}}
QTextEdit {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    font-size: 13px; background: white; color: {TEXT_DARK};
}}
QTextEdit:focus {{ border: 2px solid {accent}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px; border: none;
}}
QScrollBar::handle:vertical {{
    background: rgba(120,130,160,0.35); border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(108,142,245,0.6); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{
    background: rgba(120,130,160,0.35); border-radius: 4px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: rgba(108,142,245,0.6); }}
QListWidget {{
    background: white; border: 1px solid {BORDER}; border-radius: 8px;
    font-size: 13px; outline: none; color: {TEXT_DARK};
}}
QListWidget::item {{ padding: 8px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {sel_rgba}; color: {accent}; }}
QTreeWidget {{
    background: transparent; border: none; outline: none;
    font-size: 13px; color: {TEXT_DARK};
}}
QTreeWidget::item {{
    padding: 5px 6px; border-radius: 8px; margin: 1px 2px;
}}
QTreeWidget::item:hover {{ background: rgba(108,142,245,0.08); }}
QTreeWidget::item:selected {{
    background: {sel_rgba}; color: {accent}; border-radius: 8px;
}}
QTableWidget {{
    background: white; border: 1px solid {BORDER}; border-radius: 8px;
    font-size: 13px; color: {TEXT_DARK}; gridline-color: #eef0f6;
}}
QHeaderView::section {{
    background: #f4f5fa; color: {TEXT_MID}; border: none;
    padding: 6px 8px; font-size: 12px; font-weight: 600;
}}
QDialog {{
    background: rgba(244,245,250,0.98); border-radius: 12px;
}}
QMessageBox, QDialog {{ font-size: 13px; }}
"""
    if abs(scale - 1.0) < 1e-6:
        return qss
    return _scale_qss(qss, scale)


def _scale_qss(qss: str, scale: float) -> str:
    """按 scale 缩放 QSS 中所有 px 数值。

    font-size 保持 ≥8px 下限（避免小窗口下文字过小不可读）；
    其余结构尺寸（margin/padding/border/width/height/radius 等）线性缩放，
    下限 1px，避免小窗口下滚动条 margin 被放大超过自身宽度导致滑块消失。
    """
    def _rep(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        start = m.start()
        seg_start = max(qss.rfind(";", 0, start), qss.rfind("{", 0, start)) + 1
        decl = qss[seg_start:start].lstrip()
        floor = 8 if decl.startswith("font-size") else 1
        return f"{max(floor, int(n * scale))}px"
    return re.sub(r"(\d+)px", _rep, qss)

# ================================================================
# 数据层：skills/tools_list.json + skills/skilltools_information.json
# ================================================================
def default_skill_data(name: str, trigger: Optional[str] = None) -> Dict[str, Any]:
    """返回技能默认数据模板（新建技能时使用）。"""
    return {
        "name": name,
        "trigger": trigger or name,
        "kind": "skill",
        "aliases": [],
        "category": _DEFAULT_CATEGORY,
        "enabled": True,
        "description": "",
        "prompt_template": "",
        "parameters": [],
        # 需求 v5/v6：生图技能标记 + 独立 API（留空复用主 API / 全局生图 API）
        "image_gen": False,
        # 需求：允许联网（勾选后该技能可调用搜索引擎 API）
        "network_enabled": False,
        "api_base": "",
        "api_key": "",
        "api_model": "",
    }

def _read_catalog_raw() -> List[Dict[str, Any]]:
    """读取 skills/tools_list.json 的 skills 列表；缺失/损坏返回空列表。"""
    path = SKILLS_DIR / CATALOG_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        skills = data.get("skills") if isinstance(data, dict) else data
        return [s for s in skills if isinstance(s, dict)] if isinstance(skills, list) else []
    except Exception:  # noqa: BLE001
        return []

def _read_info_raw() -> Dict[str, Dict[str, Any]]:
    """读取 skills/skilltools_information.json（键名 = 技能名称）；损坏返回空。"""
    path = SKILLS_DIR / INFO_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}

def list_skills() -> List[str]:
    """返回所有技能名称（升序；以目录为准，详情键名补漏）。"""
    names: set = set()
    for s in _read_catalog_raw():
        n = str(s.get("name") or "").strip()
        if n:
            names.add(n)
    names.update(str(k) for k in _read_info_raw() if k)
    return sorted(names)

def read_skill(name: str) -> Dict[str, Any]:
    """读取一个技能（目录 + 详情合并）；缺失字段回填默认值，保证 schema 完整。"""
    base = default_skill_data(name)
    for s in _read_catalog_raw():
        if str(s.get("name") or "") == name:
            for k in ("trigger", "kind", "aliases", "category", "enabled"):
                if k in s and s[k] is not None:
                    base[k] = s[k]
            break
    info = _read_info_raw().get(name)
    if isinstance(info, dict):
        for k in ("description", "prompt_template", "parameters",
                  "image_gen", "network_enabled", "api_base", "api_key",
                  "api_model"):
            if k in info and info[k] is not None:
                base[k] = info[k]
    base["name"] = name
    if not base["trigger"]:
        base["trigger"] = name
    if not isinstance(base["aliases"], list):
        base["aliases"] = []
    if not isinstance(base["parameters"], list):
        base["parameters"] = []
    base["enabled"] = bool(base["enabled"])
    base["kind"] = "switch" if base["kind"] == "switch" else "skill"
    base["image_gen"] = bool(base.get("image_gen", False))
    base["network_enabled"] = bool(base.get("network_enabled", False))
    base["api_base"] = str(base.get("api_base") or "").strip()
    base["api_key"] = str(base.get("api_key") or "").strip()
    base["api_model"] = str(base.get("api_model") or "").strip()
    return base

def write_json_atomic(path: Path, data: Any) -> None:
    """原子写入 JSON（先写临时文件再替换，避免写一半损坏）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8")
    os.replace(tmp, path)

def _parse_aliases(text: str) -> List[str]:
    """把用户录入的别名字符串解析为列表（支持逗号/顿号/空格/分号分隔，去空去重）。"""
    parts = re.split(r"[,\s;、/，；]+", text or "")
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out

def validate_skill(data: Dict[str, Any],
                   old_name: Optional[str] = None) -> str:
    """校验技能数据，返回错误信息（空串表示通过）。"""
    name = str(data.get("name", "")).strip()
    trigger = str(data.get("trigger", "")).strip()
    aliases = [str(a).strip() for a in (data.get("aliases") or []) if str(a).strip()]
    kind = str(data.get("kind") or "skill").strip().lower()
    if kind not in ("skill", "switch"):
        return "类型只能为：普通技能 / 开关工具。"
    if not name:
        return "名称不能为空。"
    if _INVALID_FS_CHARS.search(name):
        return "名称包含非法字符（不能包含 < > : \" / \\ | ? * 等）。"
    if name in (".", ".."):
        return "名称不能为 . 或 ..。"
    if not trigger:
        return "触发词不能为空（\\@触发词 用于在聊天中唤醒技能）。"
    if not _TRIGGER_RE.match(trigger):
        return "触发词只能包含英文/数字/下划线/连字符/中文，且不能包含空格。"
    for a in aliases:
        if not _TRIGGER_RE.match(a):
            return f"别名「{a}」只能包含英文/数字/下划线/连字符/中文，且不能包含空格。"
    # 冲突检查（同名 / 同触发词 / 别名与触发词/名称互撞）
    for other in list_skills():
        if other == old_name:
            continue
        od = read_skill(other)
        if od.get("name") == name:
            return f"已存在同名技能「{name}」，请更换名称。"
        if od.get("trigger") == trigger:
            return f"触发词 \\@{trigger} 已被技能「{od.get('name', other)}」占用。"
        other_aliases = [str(a) for a in (od.get("aliases") or [])]
        if trigger in other_aliases:
            return f"触发词 \\@{trigger} 与技能「{od.get('name', other)}」的别名冲突。"
        if name in other_aliases:
            return f"名称「{name}」与技能「{od.get('name', other)}」的别名冲突。"
        for a in aliases:
            if a == od.get("trigger"):
                return f"别名「{a}」与技能「{od.get('name', other)}」的触发词 \\@{a} 冲突。"
            if a == od.get("name"):
                return f"别名「{a}」与技能「{od.get('name', other)}」的名称冲突。"
            if a in other_aliases:
                return f"别名「{a}」与技能「{od.get('name', other)}」的别名重复。"
    # 参数名校验（仅允许安全字符，避免注入）
    params = data.get("parameters") or []
    seen_p: set = set()
    for p in params:
        if not isinstance(p, dict):
            continue
        pn = str(p.get("name") or "").strip()
        if not pn:
            return "参数列表中存在空参数名，请检查参数。"
        if pn in seen_p:
            return f"参数名「{pn}」重复。"
        seen_p.add(pn)
        if not _TRIGGER_RE.match(pn):
            return f"参数名「{pn}」只能包含英文/数字/下划线/连字符/中文。"
    return ""

def _catalog_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    """从合并数据中提取目录条目字段。"""
    return {
        "name": str(data.get("name", "")).strip(),
        "trigger": str(data.get("trigger", "")).strip(),
        "kind": "switch" if data.get("kind") == "switch" else "skill",
        "aliases": [str(a).strip() for a in (data.get("aliases") or [])
                    if str(a).strip()],
        "category": str(data.get("category") or _DEFAULT_CATEGORY).strip(),
        "enabled": bool(data.get("enabled", True)),
    }

def _info_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    """从合并数据中提取详情条目字段。"""
    params: List[Dict[str, Any]] = []
    for p in (data.get("parameters") or []):
        if not isinstance(p, dict):
            continue
        params.append({
            "name": str(p.get("name") or "").strip(),
            "default": p.get("default"),
            "required": bool(p.get("required", False)),
        })
    return {
        "description": str(data.get("description") or ""),
        "prompt_template": str(data.get("prompt_template") or ""),
        "parameters": params,
        # 需求 v5/v6：生图标记 + 独立 API（留空复用主 API / 全局生图 API）
        "image_gen": bool(data.get("image_gen", False)),
        "network_enabled": bool(data.get("network_enabled", False)),
        "api_base": str(data.get("api_base") or "").strip(),
        "api_key": str(data.get("api_key") or "").strip(),
        "api_model": str(data.get("api_model") or "").strip(),
    }

def save_skill(data: Dict[str, Any],
               old_name: Optional[str] = None) -> Tuple[bool, str]:
    """保存技能：新建（old_name=None）或修改/重命名。

    返回 (是否成功, 消息)。成功后自动重建两个 JSON 文件。
    """
    clean = dict(data)
    err = validate_skill(clean, old_name)
    if err:
        return False, err
    name = str(clean.get("name", "")).strip()
    try:
        catalog = _read_catalog_raw()
        catalog = [s for s in catalog
                   if not (str(s.get("name") or "") in (name, old_name))]
        catalog.append(_catalog_entry(clean))
        catalog.sort(key=lambda s: str(s.get("name") or ""))

        info = _read_info_raw()
        if old_name and old_name != name:
            info.pop(old_name, None)
        info[name] = _info_entry(clean)

        write_json_atomic(SKILLS_DIR / CATALOG_FILE,
                          {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "count": len(catalog), "skills": catalog})
        write_json_atomic(SKILLS_DIR / INFO_FILE, info)
        return True, f"已保存技能「{name}」（共 {len(list_skills())} 个）"
    except OSError as exc:
        return False, f"保存失败：{exc}"

def delete_skill(name: str) -> Tuple[bool, str]:
    """删除一个技能，并同步重建两个 JSON 文件。"""
    if name not in list_skills():
        return False, f"技能「{name}」不存在。"
    try:
        catalog = [s for s in _read_catalog_raw()
                   if str(s.get("name") or "") != name]
        info = _read_info_raw()
        info.pop(name, None)
        write_json_atomic(SKILLS_DIR / CATALOG_FILE,
                          {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "count": len(catalog), "skills": catalog})
        write_json_atomic(SKILLS_DIR / INFO_FILE, info)
        return True, f"已删除技能「{name}」（剩余 {len(list_skills())} 个）"
    except OSError as exc:
        return False, f"删除失败：{exc}"

def rebuild_tools_list() -> None:
    """重建 skills/tools_list.json 与 skilltools_information.json。

    自动去重、剔除孤儿详情、按名称排序、回填缺失字段，供 CLI 修复使用。
    """
    names = list_skills()
    info = _read_info_raw()
    info = {k: v for k, v in info.items() if k in names}
    catalog = []
    for n in names:
        d = read_skill(n)
        catalog.append(_catalog_entry(d))
    catalog.sort(key=lambda s: str(s.get("name") or ""))
    try:
        write_json_atomic(SKILLS_DIR / CATALOG_FILE,
                          {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "count": len(catalog), "skills": catalog})
        write_json_atomic(SKILLS_DIR / INFO_FILE, info)
    except OSError:  # pragma: no cover
        pass

# ================================================================
# 主窗口：技能管理器（Windows 注册表风格 · HTML5 响应式界面）
# ================================================================
class SkillManagerWindow(QMainWindow):
    """无边框圆角半透明窗口：左侧注册表键树 + 右侧值属性面板。"""

    def __init__(self) -> None:
        super().__init__(None)
        # 主题色跟随主界面配置（需求：主界面风格变化 skilltools 也遵守）
        try:
            from config_loader import ConfigLoader
            self._accent = ConfigLoader.instance().accent() or ACCENT
        except Exception:  # noqa: BLE001 - 配置未初始化时回退默认蓝
            self._accent = ACCENT
        self._scale = 1.0
        self._dragging = False
        self._drag_offset = QPoint()
        self._current_name: Optional[str] = None
        self._dirty = False
        self._parameters: List[Dict[str, Any]] = []

        # 无边框窗口
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("技能工具管理器")
        self.setMinimumSize(920, 640)
        self.resize(1080, 720)

        self._build_ui()
        self._init_resize()
        self._refresh_tree()
        names = list_skills()
        self._load_skill(names[0] if names else None)

    def apply_accent(self, color: str) -> None:
        """主界面主题色变化时同步生效（需求：主界面风格变化 skilltools 也遵守）。"""
        if not color or str(color).lower() == str(self._accent).lower():
            return
        self._accent = color
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet(root_qss(color, self._scale))
            self.update()

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        central.setStyleSheet(root_qss(self._accent, self._scale))
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar())

        body = QHBoxLayout()
        body.setContentsMargins(16, 8, 16, 14)
        body.setSpacing(14)
        body.addWidget(self._build_left_panel(), 1)
        body.addWidget(self._build_right_panel(), 3)
        root.addLayout(body, 1)

        status = QHBoxLayout()
        status.setContentsMargins(16, 0, 16, 8)
        self._status_path = QLabel()
        self._status_path.setObjectName("statusText")
        self._status_msg = QLabel()
        self._status_msg.setObjectName("statusText")
        self._status_count = QLabel()
        self._status_count.setObjectName("countText")
        status.addWidget(self._status_path)
        status.addStretch(1)
        status.addWidget(self._status_msg)
        status.addSpacing(10)
        status.addWidget(self._status_count)
        root.addLayout(status)

    # ------------------------------------------------------------ 标题栏
    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("技能工具管理器")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        sub = QLabel("注册表风格技能库 · Celestia AssistantAI")
        sub.setObjectName("statusText")
        layout.addWidget(sub)
        layout.addStretch(1)

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
        self.showMinimized()

    def _on_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_close(self) -> None:
        self.close()

    # ------------------------------------------------------------ 左栏（注册表键树）
    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("技能库")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(16)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.itemSelectionChanged.connect(self._on_tree_selected)
        layout.addWidget(self._tree, 1)

        hint = QLabel("提示：点选左侧键查看/编辑，\n双击字段可弹出注册表式编辑框。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        new_btn = QPushButton("＋ 新建技能")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setObjectName("ghostBtn")
        new_btn.clicked.connect(self._on_new_skill)
        btn_row.addWidget(new_btn, 1)
        del_btn = QPushButton("删除")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._on_delete_skill)
        btn_row.addWidget(del_btn)
        layout.addLayout(btn_row)
        return panel

    # ------------------------------------------------------------ 右栏（值属性面板）
    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("mainPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部：技能名 + 触发词标签 + 操作按钮
        top = QHBoxLayout()
        top.setSpacing(10)
        self._skill_name_label = QLabel("未选择技能")
        self._skill_name_label.setObjectName("toolName")
        self._trigger_tag = QLabel("")
        self._trigger_tag.setObjectName("commandTag")
        top.addWidget(self._skill_name_label)
        top.addWidget(self._trigger_tag)
        top.addStretch(1)
        save_btn = QPushButton("保存修改")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save_skill)
        top.addWidget(save_btn)
        layout.addLayout(top)

        # 可滚动属性编辑区
        scroll = QScrollArea()
        scroll.setObjectName("propScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        container = QWidget()
        container.setObjectName("root")
        body = QVBoxLayout(container)
        body.setContentsMargins(4, 4, 4, 4)
        body.setSpacing(10)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        def _section(title: str) -> "QFormLayout":
            """左对齐标题独立一行 + 下方卡片（与主程序设置面板一致）。"""
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            body.addWidget(title_label)
            box = QFrame()
            box.setObjectName("settingsCard")
            form = QFormLayout(box)
            form.setContentsMargins(14, 14, 14, 14)
            form.setSpacing(10)
            body.addWidget(box)
            return form

        # 1. 基本属性（注册表值）
        prop_form = _section("基本属性（注册表值）")
        self._name_box = QLineEdit()
        self._name_box.setPlaceholderText(FIELDS["name"][1])
        self._trigger_box = QLineEdit()
        self._trigger_box.setPlaceholderText(FIELDS["trigger"][1])
        self._kind_combo = QComboBox()
        self._kind_combo.addItems(["普通技能", "开关工具"])
        self._kind_combo.setToolTip("开关工具（如思维链、联网搜索）可与其他开关同时开启")
        self._aliases_box = QLineEdit()
        self._aliases_box.setPlaceholderText(FIELDS["aliases"][1])
        self._category_box = QLineEdit()
        self._category_box.setPlaceholderText(FIELDS["category"][1])
        self._enabled_box = QCheckBox("启用（未勾选时主程序 \\@唤醒 列表不显示）")
        self._enabled_box.setToolTip("关闭后技能从主程序 \\@唤醒 弹窗中隐藏")
        prop_form.addRow(FIELDS["name"][0], self._name_box)
        prop_form.addRow(FIELDS["trigger"][0], self._trigger_box)
        prop_form.addRow(FIELDS["kind"][0], self._kind_combo)
        prop_form.addRow(FIELDS["aliases"][0], self._aliases_box)
        prop_form.addRow(FIELDS["category"][0], self._category_box)
        prop_form.addRow("启用", self._enabled_box)

        # 2. 技能说明
        desc_form = _section("技能说明（注入 LLM 的技能上下文）")
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText(FIELDS["description"][1])
        self._desc_edit.setMinimumHeight(100)
        desc_form.addRow(self._desc_edit)

        # 3. 执行要求
        tpl_form = _section("执行要求（可含 {参数名} 占位符）")
        self._tpl_edit = QTextEdit()
        self._tpl_edit.setPlaceholderText(FIELDS["prompt_template"][1])
        self._tpl_edit.setMinimumHeight(110)
        tpl_form.addRow(self._tpl_edit)

        # 4. 参数
        param_form = _section("参数（调用时按默认值注入）")
        param_row = QHBoxLayout()
        param_row.setSpacing(8)
        self._param_summary = QLabel("（0 个参数）")
        self._param_summary.setObjectName("countText")
        param_row.addWidget(self._param_summary, 1)
        param_btn = QPushButton("编辑参数…")
        param_btn.setCursor(Qt.PointingHandCursor)
        param_btn.setObjectName("ghostBtn")
        param_btn.clicked.connect(self._edit_parameters_dialog)
        param_row.addWidget(param_btn)
        param_form.addRow(param_row)

        # 5. 独立 API（需求 v6：留空复用主 API）+ 生图标记（需求 v5）
        api_form = _section("独立 API（留空复用主 API；对话 / 生图按该技能独立调用）")
        self._image_gen_box = QCheckBox(
            "图像生成技能（调用 \\@触发词 时直接调用生图 API，不再走对话）")
        self._image_gen_box.setToolTip("勾选后（如「生成图片」\\@image）调用即强制生图")
        api_form.addRow(self._image_gen_box)
        # 需求：允许联网（勾选后该技能可调用搜索引擎 API）
        self._network_box = QCheckBox(
            "允许联网（调用该技能时可通过搜索引擎 API 获取实时信息）")
        self._network_box.setToolTip(
            "勾选后 \\@network 联网搜索技能在全局「允许联网」开启或本技能勾选时即可搜索")
        api_form.addRow(self._network_box)
        self._api_base_box = QLineEdit()
        self._api_base_box.setPlaceholderText("独立 API 地址（留空复用主 API）")
        api_form.addRow("API 地址", self._api_base_box)
        self._api_key_box = QLineEdit()
        self._api_key_box.setPlaceholderText("独立 API Key（留空复用主 API Key）")
        self._api_key_box.setEchoMode(QLineEdit.Password)
        api_form.addRow("API Key", self._api_key_box)
        self._api_model_box = QLineEdit()
        self._api_model_box.setPlaceholderText("独立模型名（留空复用主模型）")
        api_form.addRow("模型", self._api_model_box)

        # 字段变化标记 dirty（供切换/退出时提示保存）
        for widget in (self._name_box, self._trigger_box, self._aliases_box,
                       self._category_box, self._api_base_box, self._api_key_box,
                       self._api_model_box):
            widget.textChanged.connect(self._mark_dirty)
        self._image_gen_box.toggled.connect(self._mark_dirty)
        self._network_box.toggled.connect(self._mark_dirty)
        self._kind_combo.currentIndexChanged.connect(self._mark_dirty)
        self._enabled_box.toggled.connect(self._mark_dirty)
        for widget in (self._desc_edit, self._tpl_edit):
            widget.textChanged.connect(self._mark_dirty)

        # 双击字段 → 注册表式「编辑字符串」对话框
        self._name_box.mouseDoubleClickEvent = \
            lambda e: self._edit_field_dialog("name", e)
        self._trigger_box.mouseDoubleClickEvent = \
            lambda e: self._edit_field_dialog("trigger", e)
        self._aliases_box.mouseDoubleClickEvent = \
            lambda e: self._edit_field_dialog("aliases", e)
        self._category_box.mouseDoubleClickEvent = \
            lambda e: self._edit_field_dialog("category", e)

        # 底部提示条
        tip = QLabel("注册表式布局：左侧键 = 技能，右侧值 = 字段。"
                     "保存后自动写入 tools_list.json 与 "
                     "skilltools_information.json，主程序 \\@技能 唤醒立即生效。")
        tip.setObjectName("hintText")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return panel

    # ------------------------------------------------------------ 树与属性联动
    def _refresh_tree(self, select_name: Optional[str] = None) -> None:
        """重建左侧注册表键树；可选指定选中的技能。"""
        names = list_skills()
        self._tree.blockSignals(True)
        self._tree.clear()

        root_item = QTreeWidgetItem(["我的技能库"])
        root_item.setData(0, Qt.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(QStyle.SP_DirHomeIcon))
        root_item.setExpanded(True)
        self._tree.addTopLevelItem(root_item)

        for name in names:
            data = read_skill(name)
            item = QTreeWidgetItem(
                [f"{data.get('name', name)}  [\\@{data.get('trigger', '')}]"])
            item.setData(0, Qt.UserRole, name)
            item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
            item.setToolTip(0, data.get("description", "") or "（无技能说明）")
            root_item.addChild(item)

        self._tree.blockSignals(False)
        # 恢复 / 指定选中项
        if select_name:
            for item in self._walk_items(root_item):
                if item.data(0, Qt.UserRole) == select_name:
                    self._tree.setCurrentItem(item)
                    break
        elif names:
            first = root_item.child(0)
            if first is not None:
                self._tree.setCurrentItem(first)
        else:
            self._tree.setCurrentItem(root_item)
        self._update_count(len(names))

    @staticmethod
    def _walk_items(item: QTreeWidgetItem):
        """深度遍历树节点。"""
        stack = [item]
        while stack:
            cur = stack.pop()
            yield cur
            for i in range(cur.childCount()):
                stack.append(cur.child(i))

    def _on_tree_selected(self) -> None:
        """点击键树切换 → 提示保存未保存修改后加载新技能。"""
        item = self._tree.currentItem()
        if item is None:
            return
        name = item.data(0, Qt.UserRole) or ""
        if name == self._current_name and not self._dirty:
            return
        if not self._confirm_discard():
            return
        self._load_skill(name if name else None)

    def _load_skill(self, name: Optional[str]) -> None:
        """将技能数据载入编辑控件。name=None 表示空选择。"""
        self._current_name = name
        if name:
            data = read_skill(name)
        else:
            data = default_skill_data("")
            data["aliases"] = []
            data["parameters"] = []
        self._name_box.setText(str(data.get("name", "")))
        self._trigger_box.setText(str(data.get("trigger", "")))
        self._kind_combo.setCurrentIndex(1 if data.get("kind") == "switch" else 0)
        self._aliases_box.setText("，".join(
            str(a) for a in (data.get("aliases") or [])))
        self._category_box.setText(str(data.get("category", "")))
        self._enabled_box.setChecked(bool(data.get("enabled", True)))
        self._desc_edit.setPlainText(str(data.get("description", "")))
        self._tpl_edit.setPlainText(str(data.get("prompt_template", "")))
        self._parameters = list(data.get("parameters") or [])
        self._refresh_param_summary()
        # 独立 API / 生图标记 / 允许联网（需求 v5/v6/v7）
        self._image_gen_box.setChecked(bool(data.get("image_gen", False)))
        self._network_box.setChecked(bool(data.get("network_enabled", False)))
        self._api_base_box.setText(str(data.get("api_base") or ""))
        self._api_key_box.setText(str(data.get("api_key") or ""))
        self._api_model_box.setText(str(data.get("api_model") or ""))
        self._dirty = False
        self._refresh_prop()

    def _refresh_param_summary(self) -> None:
        params = self._parameters or []
        n = len(params)
        required = sum(1 for p in params
                       if isinstance(p, dict) and p.get("required"))
        text = f"（{n} 个参数，其中必填 {required} 个）" if n else "（0 个参数）"
        self._param_summary.setText(text)

    def _refresh_prop(self) -> None:
        """刷新右侧标题、触发词标签与状态栏。"""
        name = self._name_box.text().strip()
        trigger = self._trigger_box.text().strip()
        if name:
            self._skill_name_label.setText(name)
            self._trigger_tag.setText(f"\\@{trigger}" if trigger else "（未设置触发词）")
            self._status_path.setText(f"计算机\\技能库\\{name}")
        else:
            self._skill_name_label.setText("未选择技能")
            self._trigger_tag.setText("")
            self._status_path.setText("计算机\\技能库")
        self._status_msg.setText("已就绪" if not self._dirty else "有未保存修改")

    def _update_count(self, n: int) -> None:
        self._status_count.setText(f"共 {n} 个技能")

    def _mark_dirty(self, *_: Any) -> None:
        if not self._dirty:
            self._dirty = True
            self._refresh_prop()

    # ------------------------------------------------------------ 数据收集与操作
    def _collect_data(self) -> Dict[str, Any]:
        """从编辑控件收集技能数据。"""
        return {
            "name": self._name_box.text().strip(),
            "trigger": self._trigger_box.text().strip(),
            "kind": "switch" if self._kind_combo.currentIndex() == 1 else "skill",
            "aliases": _parse_aliases(self._aliases_box.text()),
            "category": self._category_box.text().strip() or _DEFAULT_CATEGORY,
            "enabled": self._enabled_box.isChecked(),
            "description": self._desc_edit.toPlainText().strip(),
            "prompt_template": self._tpl_edit.toPlainText().strip(),
            "parameters": list(self._parameters or []),
            # 需求 v5/v6/v7：生图标记 + 允许联网 + 独立 API
            "image_gen": self._image_gen_box.isChecked(),
            "network_enabled": self._network_box.isChecked(),
            "api_base": self._api_base_box.text().strip(),
            "api_key": self._api_key_box.text().strip(),
            "api_model": self._api_model_box.text().strip(),
        }

    def _on_save_skill(self) -> None:
        """保存当前编辑的技能（新建或修改，含重命名）。"""
        data = self._collect_data()
        if not data["name"]:
            styled_warning(self, "提示", "请先填写技能名称。")
            return
        ok, msg = save_skill(data, old_name=self._current_name)
        if not ok:
            styled_warning(self, "保存失败", msg)
            return
        self._status_msg.setText(msg)
        new_name = data["name"]
        self._current_name = new_name
        self._dirty = False
        self._refresh_tree(select_name=new_name)
        self._refresh_prop()

    def _on_new_skill(self) -> None:
        """新建技能：输入名称后载入默认模板（保存时才真正写入文件）。"""
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "新建技能", "请输入技能名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            styled_warning(self, "提示", "技能名称不能为空。")
            return
        if _INVALID_FS_CHARS.search(name) or name in (".", ".."):
            styled_warning(self, "提示",
                                "名称包含非法字符（不能包含 < > : \" / \\ | ? * 等）。")
            return
        if name in list_skills():
            styled_warning(self, "提示", f"已存在同名技能「{name}」，请更换名称。")
            return
        data = default_skill_data(name)
        self._current_name = None          # 尚未落盘 → 保存时按新建处理
        self._name_box.setText(data["name"])
        self._trigger_box.setText(data["trigger"])
        self._kind_combo.setCurrentIndex(0)   # 新建默认「普通技能」
        self._aliases_box.setText("")
        self._category_box.setText(data["category"])
        self._enabled_box.setChecked(True)
        self._desc_edit.setPlainText(data["description"])
        self._tpl_edit.setPlainText(data["prompt_template"])
        self._parameters = []
        self._refresh_param_summary()
        # 独立 API / 生图标记 / 允许联网（需求 v5/v6/v7）
        self._image_gen_box.setChecked(False)
        self._network_box.setChecked(False)
        self._api_base_box.setText("")
        self._api_key_box.setText("")
        self._api_model_box.setText("")
        self._dirty = True
        self._refresh_prop()
        self._status_msg.setText(f"正在新建技能「{name}」，点击「保存修改」创建。")

    def _on_delete_skill(self) -> None:
        """删除当前选中的技能（需二次确认）。"""
        if not self._current_name:
            styled_info(self, "提示", "请先在左侧选择一个技能。")
            return
        name = self._current_name
        if not styled_question(
                self, "确认删除",
                f"确定删除技能「{name}」吗？\n\n"
                f"将从 skills/tools_list.json 与 "
                "skills/skilltools_information.json 中移除，\n此操作不可撤销。",
                danger=True):
            return
        ok, msg = delete_skill(name)
        if not ok:
            styled_warning(self, "删除失败", msg)
            return
        self._current_name = None
        self._dirty = False
        self._refresh_tree()
        self._load_skill(None)
        self._status_msg.setText(msg)

    def _confirm_discard(self) -> bool:
        """有未保存修改时询问：保存 / 放弃 / 取消。返回 True 表示允许继续。"""
        if not self._dirty:
            return True
        box = styled_box(
            self, "未保存的修改",
            "当前技能有未保存的修改，是否保存？",
            icon="question",
            buttons=(QMessageBox.Save | QMessageBox.Discard
                     | QMessageBox.Cancel),
            default=QMessageBox.Save)
        ret = box.exec()
        if ret == QMessageBox.Cancel:
            return False
        if ret == QMessageBox.Save:
            data = self._collect_data()
            if data["name"]:
                ok, msg = save_skill(data, old_name=self._current_name)
                self._status_msg.setText(msg)
                if not ok:
                    styled_warning(self, "保存失败", msg)
                    return False
        return True

    # ------------------------------------------------------------ 注册表式字段编辑
    def _edit_field_dialog(self, key: str, _event: Any = None) -> None:
        """仿 regedit「编辑字符串」：双击字段弹窗编辑。"""
        if key not in FIELDS:
            return
        label, placeholder = FIELDS[key]
        current = {
            "name": self._name_box.text(),
            "trigger": self._trigger_box.text(),
            "aliases": self._aliases_box.text(),
            "category": self._category_box.text(),
            "description": self._desc_edit.toPlainText(),
            "prompt_template": self._tpl_edit.toPlainText(),
        }.get(key, "")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑 {label}")
        dlg.setStyleSheet(root_qss(self._accent, self._scale))
        dlg.resize(560, 320 if key in ("description", "prompt_template") else 180)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        hint = QLabel(f"值名称：{label}")
        hint.setObjectName("sectionTitle")
        lay.addWidget(hint)

        if key in ("description", "prompt_template"):
            editor = QTextEdit()
            editor.setPlainText(current)
            editor.setPlaceholderText(placeholder)
            lay.addWidget(editor, 1)
        else:
            editor = QLineEdit(current)
            editor.setPlaceholderText(placeholder)
            lay.addWidget(editor)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("primaryBtn")
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("ghostBtn")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        def _apply() -> None:
            value = (editor.toPlainText() if key in ("description", "prompt_template")
                     else editor.text())
            if key == "name":
                self._name_box.setText(value)
            elif key == "trigger":
                self._trigger_box.setText(value)
            elif key == "aliases":
                self._aliases_box.setText(value)
            elif key == "category":
                self._category_box.setText(value)
            elif key == "description":
                self._desc_edit.setPlainText(value)
            elif key == "prompt_template":
                self._tpl_edit.setPlainText(value)
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    # ------------------------------------------------------------ 参数编辑对话框
    def _edit_parameters_dialog(self) -> None:
        """编辑参数列表：名称 / 默认值 / 必填（表格 + 增删行）。"""
        params = list(getattr(self, "_parameters", []) or [])
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑参数")
        dlg.setStyleSheet(root_qss(self._accent, self._scale))
        dlg.resize(560, 360)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        hint = QLabel("参数名将作为 {参数名} 占位符注入执行要求；"
                      "必填参数在注入时优先使用用户输入。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["参数名", "默认值", "必填"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)

        for p in params:
            if not isinstance(p, dict):
                continue
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(str(p.get("name") or "")))
            table.setItem(row, 1, QTableWidgetItem(str(p.get("default") or "")))
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked if p.get("required") else Qt.Unchecked)
            table.setItem(row, 2, chk)
        lay.addWidget(table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("＋ 添加参数")
        add_btn.setObjectName("ghostBtn")
        del_btn = QPushButton("删除选中行")
        del_btn.setObjectName("dangerBtn")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("primaryBtn")
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("ghostBtn")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        def _add_row() -> None:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(""))
            table.setItem(row, 1, QTableWidgetItem(""))
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Unchecked)
            table.setItem(row, 2, chk)
            table.setCurrentCell(row, 0)

        def _remove_row() -> None:
            row = table.currentRow()
            if row >= 0:
                table.removeRow(row)

        def _apply() -> None:
            out: List[Dict[str, Any]] = []
            for r in range(table.rowCount()):
                pn = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                if not pn:
                    continue
                dv = table.item(r, 1).text() if table.item(r, 1) else ""
                req_item = table.item(r, 2)
                required = (req_item is not None
                            and req_item.checkState() == Qt.Checked)
                out.append({"name": pn, "default": dv, "required": required})
            self._parameters = out
            self._refresh_param_summary()
            self._mark_dirty()
            dlg.accept()

        add_btn.clicked.connect(_add_row)
        del_btn.clicked.connect(_remove_row)
        ok_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def closeEvent(self, event: Any) -> None:  # noqa: D102
        """关闭窗口前提示未保存修改。"""
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ 无边框窗口缩放
    def _init_resize(self) -> None:
        """启用无边框窗口的四边/四角拖拽缩放与悬停光标反馈（与主程序一致）。"""
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
        """无边框窗口四边/四角缩放（与主程序 ui_manager 一致的实现）。"""
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

    # ------------------------------------------------------------ 圆角半透明背景
    def paintEvent(self, event: Any) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setBrush(QColor(244, 245, 250, 210))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 16, 16)
        painter.end()
        super().paintEvent(event)

# ================================================================
# 入口
# ================================================================
def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SkillTools")
    app.setApplicationDisplayName("技能工具管理器")
    window = SkillManagerWindow()
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())

