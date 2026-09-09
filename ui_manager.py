"""
ui_manager.py — 主对话界面（MainWindow，Gemini 式 1:3 分栏 · HTML5 响应式风格）

功能：
- 无边框圆角半透明主窗口（白色半透明白底 + /theme 背景图，可配高斯模糊）
- 左栏：立绘随情感自动切换（QPropertyAnimation opacity 渐隐过渡）
  + 角色/群聊下拉 + 随机主动对话 + 主题切换
- 右栏：新会话/历史侧栏 + 流式气泡对话（圆形头像 name-.png）
  + 多模态附件 + 设置面板（11 项）
- /theme 命令切换背景；QThread 异步 LLM 调用（ChatWorker），GUI 永不卡死
- 系统托盘常驻

HTML5 响应式风格：
- QSS：渐变按钮 + 圆角 + :hover/:pressed 伪状态（仿 CSS）
- QGraphicsDropShadowEffect：卡片/气泡/按钮投影（Material 层级感）
- QPropertyAnimation：按钮悬停浮起、立绘渐隐、气泡淡入弹出
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AI_DeskMate.UI")

from PySide6.QtCore import (
    Qt, QThread, QObject, Signal, QTimer, QPoint, QSize, QPropertyAnimation,
    QEasingCurve, QParallelAnimationGroup, QEvent, QRect,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath,
    QPixmap, QLinearGradient,
    QMouseEvent, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLayout, QLayoutItem, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from config_loader import ConfigLoader, project_root
from signal_bus import SignalBus
from role_manager import RoleManager
from memory_pipeline import MemoryPipeline
from llm_client import LLMClientPool, LLMWorker
from utils.utf8 import utf8_env
from utils.styled_msg import (
    styled_confirm, styled_info, styled_question, styled_warning)
from skill_manager import SkillManager, _TAG_TOKEN
from skill_popup import SkillPopup


# ================================================================
# HTML5 风格常量
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

_EMOTION_COLORS = {
    "neutral": "#6b7280", "happy": "#e8862c", "sad": "#5b7ee6",
    "angry": "#d64545", "surprised": "#9b59b6",
}

# 群聊（全部角色）：选择后在所有角色中由 AI 判定谁最该回复
GROUP_ALL = "群聊（全部角色）"

# 系统托盘单例：整个进程只保留一个托盘图标（避免任务栏出现多个小图标）
_TRAY_SINGLETON = None

# 专业/场景标签（非开关标签，如 \@research、\@med、\@education）激活时注入的语境：
# 限制专业内容不要带角色（小马）的语气（需求 v3-2）
_PRO_MODE_INSTRUCTION = (
    "[专业模式] 当前启用了专业/场景标签（如 \\@research、\\@med、\\@education 等）。"
    "请以专业、客观、正式的语气直接作答，内容聚焦于解决问题本身；"
    "不要使用角色（小马）的口癖、卖萌语气、感叹词或角色扮演口吻。"
    "回复末尾仍需按〖〗情绪标注要求标注（通常为 neutral）。"
)

# \closed 关闭指令：独立成词（前后为空白或常见中英文标点）
_CLOSED_CMD_RE = re.compile(r"(?:^|\s)[\\/]closed(?=\s|[，。；、！？,.!?；]|$)")

# 长对话性能：聊天区最多保留的气泡数（超出自动裁掉最旧的界面气泡；
# 消息记录 self._messages 仍完整保留，用于存档与记忆）
_MAX_BUBBLES = 80

# 生图技能触发词（需求 v5：\@image 强制调用生图 API）
_IMAGE_GEN_TRIGGERS = {"image", "images", "draw"}
# 自然语言提示词生图关键词（未加 \@ 标签时，含这些关键词也可触发生图）
_IMAGE_GEN_KEYWORDS = ("生成图片", "生成一张", "生成一幅", "画一张", "画一幅",
                       "画个", "画图", "帮我画", "给我画", "帮我生成")


def _short_filename(path: str, limit: int = 15) -> str:
    """文件名截断：主名超过 limit 字（不含后缀）用 … 省略，后缀保留。

    需求：主界面对话中的附件文件名确保在 15 个字以内（不含后缀名），
    超长自动省略并配合标签换行显示，避免撑爆聊天框。
    """
    p = Path(str(path))
    stem = p.stem
    suffix = p.suffix
    if len(stem) > limit:
        return stem[:limit] + "…" + suffix
    return p.name


def _img_html(name: str, size: int = 16) -> str:
    """生成内联 GIF 图标 HTML（如 /img/thinking.gif），用于替换 emoji 图标。

    需求：思维链图标用 /img/thinking.gif，画图/生图用 /img/createimg.gif，
    且 UI 中不再使用 emoji 字符。
    """
    p = str(project_root() / "img" / name).replace("\\", "/")
    return (f'<img src="file:///{p}" width="{size}" height="{size}" '
            f'style="vertical-align:middle;margin-right:3px;">')


class _FlowLayout(QLayout):
    """流式布局：子控件先从左到右排成一行，宽度不足时自动换到下一行。

    需求：上传文件/附件链接先在一横行显示，只有放不下时才另起一行。
    参考 Qt FlowLayout 示例的简化实现（含 heightForWidth 保证高度自适应）。
    """

    def __init__(self, parent: Optional[QWidget] = None,
                 margin: int = 0, spacing: int = 6) -> None:
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: List[QLayoutItem] = []

    def addItem(self, item: QLayoutItem) -> None:  # noqa: D102
        self._items.append(item)

    def count(self) -> int:  # noqa: D102
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: D102
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: D102
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: D102
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: D102
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: D102
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: D102
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: D102
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: D102
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_h = 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()

# 与主界面 root_qss 一致的 HTML5 风格右键菜单样式（需求：风格统一）
_MENU_QSS = f"""
QMenu {{
    background: rgba(255,255,255,0.97);
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    color: {TEXT_DARK};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QMenu::item {{
    padding: 7px 26px;
    border-radius: 7px;
    margin: 2px 6px;
}}
QMenu::item:selected {{
    background: rgba(108,142,245,0.12);
    color: {ACCENT};
}}
QMenu::item:disabled {{ color: {TEXT_LIGHT}; }}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 10px;
}}
"""

# ================================================================
# HTML5 风格工具函数
# ================================================================
def btn_qss(base: str = ACCENT, hover: str = ACCENT_HOVER,
            pressed: str = ACCENT_PRESSED, radius: int = 12,
            text_color: str = "#ffffff", font_size: int = 14,
            bold: bool = True, padding: str = "8px 20px") -> str:
    """HTML5 渐变按钮样式（仿 CSS :hover/:active）。"""
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


def apply_shadow(widget: QWidget, blur: int = 14, y: int = 3,
                 alpha: int = 45) -> QGraphicsDropShadowEffect:
    """为控件添加柔和投影（Material 层级感）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def animate(widget: QWidget, prop: bytes, start: float, end: float,
            ms: int = 220, easing: "Any" = QEasingCurve.OutCubic) -> QPropertyAnimation:
    """通用属性动画（HTML5 transition 模拟）。"""
    anim = QPropertyAnimation(widget, prop, widget)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(easing)
    anim.start()
    return anim


def fade_pixmap(label: QLabel, pixmap: QPixmap, ms: int = 1200) -> None:
    """立绘/表情图切换：总共 1.2 秒渐隐过渡。

    流程：第一张先淡出消失（ms/2）→ 换图 → 第二张再淡入出现（ms/2）。
    用 QGraphicsOpacityEffect 实现子控件透明度动画（windowOpacity 只对顶层
    窗口生效）；动画对象保存在 label 上避免被 GC 回收导致闪退。
    """
    effect = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(effect)

    def _swap() -> None:
        label.setPixmap(pixmap)
        anim2 = animate(effect, b"opacity", 0.0, 1.0, ms // 2)   # 淡入出现第二张
        label._fade_anim2 = anim2   # 保留引用，防止动画对象被回收
        anim2.start()

    anim1 = animate(effect, b"opacity", 1.0, 0.0, ms // 2)   # 淡出消失第一张
    anim1.finished.connect(_swap)
    label._fade_anim1 = anim1   # 保留引用，防止动画对象被回收
    anim1.start()

def _load_pixmap(path: str, width: int, height: int,
                 circular: bool = False) -> QPixmap:
    """加载图片居中显示；circular 为圆形头像（图片填满圆形，白色圆环仅 1px 很窄）。"""
    if path and Path(path).exists():
        src = QPixmap(path)
    else:
        src = QPixmap(width, height)
        src.fill(QColor("#8e9aaf"))
    out = QPixmap(width, height)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    if circular:
        # 图片填满整个圆形（ByExpanding 居中裁剪），避免四周露出大片白色底
        scaled = src.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation)
        painter.setBrush(QColor("white"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, width, height)
        path_obj = QPainterPath()
        path_obj.addEllipse(1, 1, width - 2, height - 2)   # 白色圆环仅 1px
        painter.setClipPath(path_obj)
    else:
        scaled = src.scaled(width, height, Qt.KeepAspectRatio,
                            Qt.SmoothTransformation)
    x = (width - scaled.width()) // 2
    y = (height - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return out


def _blur_image(path: str, radius: int = 18) -> QPixmap:
    """用 Pillow 预模糊主题背景（仅一次，性能友好）。"""
    try:
        from PIL import Image, ImageFilter
        from PySide6.QtGui import QImage
        img = Image.open(path).convert("RGB")
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)
    except Exception:
        return QPixmap(path)


# ================================================================
# ChatWorker — 异步对话任务（QThread）
# ================================================================
class ChatWorker(QObject):
    """一次完整对话：附件视觉描述 → 记忆检索 → 流式生成 → 归档。

    运行于独立 QThread；通过信号回传主线程，绝不触碰任何 QWidget。
    """

    token = Signal(str)                 # 流式片段
    reasoning = Signal(str)             # 思维链片段（模型原生 reasoning_content）
    emotion = Signal(str)               # 检测到的情感
    finished = Signal(str, str, str)    # (clean_text, speaker, emotion)
    error = Signal(str)                 # 异常信息

    def __init__(self, prompt: str, role: str, group: str,
                 attachments: Optional[List[str]] = None,
                 members: Optional[List[str]] = None,
                 prev_speaker: str = "",
                 user_name: str = "用户",
                 skill_context: str = "",
                 api_override: Optional[Dict[str, Any]] = None,
                 thinking_mode: bool = False,
                 web_search_query: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._skill_context = skill_context   # @技能 注入的说明上下文（可为空）
        self._api_override = api_override or {}  # 需求 v6：技能独立 API（留空复用主 API）
        self._thinking_mode = thinking_mode   # 需求：\@thinking 思维链技能
        self._web_search_query = (web_search_query or "").strip()  # 需求：\@network 联网搜索
        self._reasoning_parts: List[str] = []
        self._role = role
        self._group = group
        self._attachments = attachments or []
        self._members = members or []
        self._prev_speaker = prev_speaker
        self._user_name = (user_name or "用户").strip() or "用户"
        self._pool = LLMClientPool.instance()
        self._memory = MemoryPipeline.instance()
        self._roles = RoleManager.instance()

    @staticmethod
    def _strip_role_prefix(text: str, role: str) -> str:
        """移除文本开头的「角色名:」前缀（含 [角色名]/【角色名】/角色名：/角色名 说：）。"""
        t = (text or "").strip()
        name = (role or "").strip()
        if not name:
            return t
        for pat in (
            rf"^\s*[\u3010\[]\s*{re.escape(name)}\s*[\u3011\]]\s*[:：]\s*",
            rf"^\s*{re.escape(name)}\s*[:：]\s*",
            rf"^\s*{re.escape(name)}\s*说\s*[:：]?\s*",
        ):
            m = re.match(pat, t, re.S)
            if m:
                return t[m.end():].strip()
        return t

    @staticmethod
    def _strip_group_prefix(text: str, members: List[str]) -> str:
        """剥离文本开头任意成员名前缀（[角色名]: / 角色名： / 角色名 说：）。

        需求：群聊回复正文不再显示 [角色名] 或 角色名： 前缀，发言者姓名
        已由气泡标题（name_label）展示。
        """
        t = (text or "").strip()
        if not t:
            return t
        for m in members:
            name = (m or "").strip()
            if not name:
                continue
            for pat in (
                rf"^\s*[\u3010\[]\s*{re.escape(name)}\s*[\u3011\]]\s*[:：]\s*",
                rf"^\s*{re.escape(name)}\s*[:：]\s*",
                rf"^\s*{re.escape(name)}\s*说\s*[:：]?\s*",
            ):
                m2 = re.match(pat, t, re.S)
                if m2:
                    t = t[m2.end():].strip()
                    break
        return t

    @staticmethod
    def _normalize_group_reply(text: str, speaker: str, members: List[str]) -> str:
        """清理群聊回复：剥掉模型可能自带的任意成员前缀，只保留正文。

        需求：界面不显示 [角色名]: / 角色名： 前缀（姓名由气泡标题展示）。
        """
        return ChatWorker._strip_group_prefix(text, members)

    def _judge_group_speaker(self, prompt: str, members: List[str]) -> str:
        """调用 API 内部判定最该发言的成员（判定结果仅内部使用，绝不输出到对话）。

        判定失败 / 返回非法名字时返回空串，由上层回退到现有解析逻辑。
        """
        if not prompt or not members:
            return ""
        intro = self._roles.member_intro(members)
        sys_p = (
            "你是一个群聊发言判定器。下面是当前群聊的成员及性格：\n"
            f"{intro}\n\n"
            "用户的最新消息：\n"
            f"{prompt}\n\n"
            "请判断这条消息最应该由哪位成员回复。"
            "只输出该成员的名字（必须完全等于上面列出的成员名之一），"
            "不要输出任何其他文字、标点或解释。"
        )
        try:
            raw = self._pool.chat_complete(
                [{"role": "system", "content": sys_p}],
                kind="small", temperature=0.1, max_tokens=16, timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("群聊发言判定调用失败（回退解析）: %s", exc)
            return ""
        name = (raw or "").strip().strip("[]【】:「」：:。，,、 \t\n")
        for m in members:
            if name == m or (name and (name in m or m in name)):
                logger.info("群聊发言判定结果: %s（依据：%r）", m, raw)
                return m
        logger.warning("群聊发言判定结果非法（%r），回退解析", raw)
        return ""

    def _build_system_prompt(self, chosen_speaker: str = "") -> str:
        card = self._roles.role_card(self._role)
        sp = self._roles.role_system_prompt(self._role)
        emo_list = card.get("emotion_list") or ["neutral", "happy", "sad", "angry", "surprised"]
        # 需求：每一句回复末尾都必须用〖〗标注情绪（从情绪列表选择），
        # 〖〗与其中文字仅供界面识别切换立绘，绝不显示在对话正文中。
        sp += (
            f"\n\n【情绪标注要求】每一句回复末尾都必须用〖〗标注当前情绪，"
            f"必须且只能从以下情绪中选择一个："
            f"{'、'.join(str(e) for e in emo_list)}。"
            f"〖〗内的文字仅供程序识别，不要在对话正文中显示〖〗及其内容。"
        )
        # 告知角色用户昵称，让角色知道并称呼对方的名字
        user_name = (self._user_name or "").strip() or "用户"
        sp += f"\n\n用户称呼：{user_name}（请在合适的时机直接称呼这个名字）。"
        # 需求：注入用户打开本应用的常用时间（data/logintime.json + 长期分布），
        # 让对话/问候可以感知用户作息；失败或开关关闭时忽略
        try:
            from logintime import login_time_context
            lt_ctx = login_time_context()
            if lt_ctx:
                sp += "\n\n【用户的打开时间习惯】" + lt_ctx
        except Exception:  # noqa: BLE001
            pass
        if self._group:
            hint = self._roles.speaker_hint(self._group)
            sp += f"\n\n当前处于群聊（成员：{'、'.join(self._members) or '未知'}）。{hint}"
            sp += (
                "\n\n【群聊发言规则】请根据本条问题内容判断当前最应该由哪一位成员发言，"
                "然后直接以 [成员名]: 回复内容 的格式输出该成员的回复。"
                "不要输出\"谁最应该发言\"之类的任何判断过程或说明文字，"
                "〖〗情绪标签仍放在该回复末尾。"
            )
            if chosen_speaker:
                sp += (
                    f"\n【发言者指定】本条消息已确定由 {chosen_speaker} 发言，"
                    f"请以 [{chosen_speaker}]: 开头直接输出 {chosen_speaker} 的回复，"
                    "不要改为其他成员。"
                )
        return sp

    def run(self) -> None:
        try:
            extra = ""
            for path in self._attachments:
                try:
                    desc = self._pool.document_describe(
                        "请用中文简洁地总结这份文件/图片的关键内容。", path
                    )
                    extra += f"\n[用户上传文件: {Path(path).name}]\n内容摘要: {desc}"
                except Exception as exc:  # noqa: BLE001
                    extra += f"\n[用户上传文件: {Path(path).name}（解析失败）]"
            full_prompt = self._prompt + extra

            # 群聊发言者确定（需求：点名触发 > API 内部判定 > 兜底解析）：
            # ① 用户消息点名（角色名/显示名/别名）→ 直接由该角色发言（多名字取第一个）；
            # ② 未点名 → 调用 API 内部判定谁最该发言，判定结果仅内部使用、绝不输出；
            # ③ 判定失败 → 生成回复后由现有 resolve_speaker 兜底。
            chosen_speaker = ""
            if self._group and self._members:
                aliases = self._roles.group_aliases(self._group)
                chosen_speaker = self._roles.mentioned_member(
                    self._prompt, self._members, aliases)
                if not chosen_speaker:
                    chosen_speaker = self._judge_group_speaker(
                        full_prompt, self._members)

            ctx = ""
            try:
                ctx = self._memory.retrieve_before(full_prompt, self._role,
                                                   self._group)
            except Exception:  # noqa: BLE001
                ctx = ""   # 记忆检索异常不阻塞对话
            messages: List[Dict[str, Any]] = [
                {"role": "system",
                 "content": self._build_system_prompt(chosen_speaker)}
            ]
            if ctx:
                messages.append({"role": "system", "content": "相关记忆：\n" + ctx})
            # 技能上下文注入（@技能 唤醒：技能说明以 system 级上下文进入 LLM）
            if self._skill_context:
                messages.append({"role": "system", "content": self._skill_context})
            # 需求：\@thinking 思维链技能——要求模型按【思维链】/【回答】格式输出
            if self._thinking_mode:
                messages.append({"role": "system", "content": (
                    "用户要求你展示思考过程。请严格按以下格式输出：\n"
                    "【思维链】\n（先完整输出你的推理过程）\n【回答】\n（再输出最终回答）")})
            # 需求：\@network 联网搜索——把实时搜索结果注入 system 上下文
            if self._web_search_query:
                try:
                    results = self._pool.web_search(self._web_search_query)
                    if results:
                        messages.append({"role": "system",
                                         "content": "以下是实时网络搜索结果（请基于这些信息回答，并附上来源）：\n"
                                                    + results})
                except Exception as exc:  # noqa: BLE001
                    self.error.emit(f"联网搜索失败：{exc}")
                    return
            try:
                recent = self._memory.recent_turns(self._role, self._group,
                                                   limit=5)
            except Exception:  # noqa: BLE001
                recent = []
            for turn in recent:
                tname = self._user_name if turn["role"] == "user" else turn["name"]
                messages.append({"role": turn["role"],
                                 "content": f"{tname}: {turn['content']}"})
            messages.append({"role": "user",
                             "content": f"{self._user_name}: {full_prompt}"})

            parts: List[str] = []

            def _reasoning_cb(piece: str) -> None:
                """收集模型原生思维链（reasoning_content）并回传主线程。"""
                self._reasoning_parts.append(piece)
                self.reasoning.emit(piece)

            api = self._api_override or {}
            custom_base = str(api.get("api_base") or "").strip()
            if custom_base:
                # 需求 v6：每个技能可独立调用不同 API（留空则走主 API）
                for piece in self._pool.chat_stream_custom(
                        messages,
                        base_url=custom_base,
                        api_key=str(api.get("api_key") or ""),
                        model=str(api.get("api_model") or ""),
                        reasoning_cb=_reasoning_cb):
                    parts.append(piece)
                    self.token.emit(piece)
            else:
                for piece in self._pool.chat_stream(messages, kind="main",
                                                    reasoning_cb=_reasoning_cb):
                    parts.append(piece)
                    self.token.emit(piece)
            text = "".join(parts)

            emo = self._roles.detect_emotion(text, self._role)
            self.emotion.emit(emo)
            clean = self._roles.strip_emotion_tags(text)
            speaker = self._role
            if self._group and self._members:
                if chosen_speaker in self._members:
                    # 点名/判定场景：强制该成员发言，并统一正文为 [成员名]: 内容
                    speaker = chosen_speaker
                    clean = self._normalize_group_reply(
                        clean, speaker, self._members)
                else:
                    # 兜底：解析模型输出中的发言者前缀；失败回退上一发言者/随机
                    speaker = self._roles.resolve_speaker(text, self._members,
                                                          self._prev_speaker)
                    # 需求：群聊正文不显示 [角色名]: / 角色名： 前缀
                    clean = self._normalize_group_reply(
                        clean, speaker, self._members)
            else:
                # 单聊时移除模型自行添加的「角色名:」前缀（名字已显示在气泡标题，
                # 避免出现 "Rainbow_Dash: Rainbow_Dash: 内容" 的重复）
                clean = self._strip_role_prefix(clean, self._role)

            # 记忆归档（archive_after，含小模型提炼 LLM 调用）移到主线程
            # _on_finished 中通过 AsyncWorker 异步执行，避免阻塞本线程与
            # 主线程的事件循环（GUI 卡死）。
            self.finished.emit(clean, speaker, emo)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"{type(exc).__name__}: {exc}")


# ================================================================
# AboutDialog — 「关于系统」介绍窗口（读取 data/about.json）
# ================================================================
class AboutDialog(QDialog):
    """关于系统：读取 data/about.json，标题居中、正文左对齐、超长滚动。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        cfg = ConfigLoader.instance()
        self.setWindowTitle("关于系统")
        self.setMinimumSize(480, 420)
        self.resize(540, 480)
        self.setStyleSheet(root_qss(
            cfg.accent(), 1.0,
            bool(cfg.get("ui", "transparent_chat", default=False))))

        title, text = "Celestia AssistantAI", ""
        about_path = project_root() / "data" / "about.json"
        try:
            data = json.loads(about_path.read_text(encoding="utf-8"))
            title = (data.get("title") or title).strip()
            text = data.get("text") or ""
        except Exception:  # noqa: BLE001
            text = "（未找到 data/about.json 介绍内容，请检查项目文件。）"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(8)

        # 标题（居中）
        title_label = QLabel(title)
        title_label.setObjectName("aboutTitle")
        title_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(title_label)

        # 正文（左对齐，超长时右侧滑块下拉）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent; border:none;}")
        body = QLabel(text)
        body.setObjectName("aboutText")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 关闭按钮
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("关闭")
        ok_btn.setObjectName("ghostBtn")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)


class _ElidedLabel(QLabel):
    """单行自动省略号标签：文字超出当前宽度时强制用 … 省略，绝不换行 / 叠字。

    历史侧边栏每个条目（第一行浓缩总结 + 第二行名字/群组·条数）都用本类，
    两行字体、颜色、行距完全一致；窗口宽度变化时 resizeEvent 自动重新省略。
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._full_text = text or ""
        self._updating = False
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        self.setFont(f)
        self.setObjectName("historyItem")
        self.setWordWrap(False)          # 仅按宽度省略，绝不自动换行
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setStyleSheet(
            f"color:{TEXT_DARK}; background:transparent;")
        self.setText(self._elided(self.width()))

    def setFullText(self, text: str) -> None:
        self._full_text = text or ""
        self.setText(self._elided(self.width()))

    def fullText(self) -> str:
        return self._full_text

    def _elided(self, width: int) -> str:
        fm = QFontMetrics(self.font())
        return fm.elidedText(self._full_text, Qt.ElideRight, max(width - 2, 1))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        # 重入保护：setText 会触发 updateGeometry→可能再次 resize→setText，
        # 若不加保护可能死循环导致 Qt 崩溃（闪退）。
        if self._updating:
            return
        self._updating = True
        try:
            new = self._elided(self.width())
            if new != self.text():
                self.setText(new)
        finally:
            self._updating = False


class _HistoryRow(QWidget):
    """历史会话单个条目：固定两行，行高 60px，超长强制省略。

    第一行 = 第一次对话的浓缩总结（不带角色名）；
    第二行 = 单聊名字 / 群聊群组名 + 对话条数，右侧 del.png 删除按钮。
    点击整行加载会话；右键弹出「修改标题/删除」菜单。
    """

    clicked = Signal(str)                      # 左键点击：path
    delete_requested = Signal(str)             # 删除按钮：path
    context_menu_requested = Signal(str, QPoint)  # 右键：(path, 全局坐标)

    def __init__(self, path: str, title: str, info: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("historyRow")
        self.setFixedHeight(60)
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 7, 6, 7)
        v.setSpacing(3)

        # 第一行：浓缩总结
        self._title_lbl = _ElidedLabel(title)
        line1 = QHBoxLayout()
        line1.setContentsMargins(0, 0, 0, 0)
        line1.setSpacing(4)
        line1.addWidget(self._title_lbl, 1)
        v.addLayout(line1)

        # 第二行：名字/群组 · 共X条 + del.png 删除按钮
        self._info_lbl = _ElidedLabel(info)
        del_btn = QPushButton()
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("删除此会话")
        del_btn.setFixedSize(18, 18)
        del_btn.setIconSize(QSize(14, 14))
        del_btn.setIcon(QIcon(str(project_root() / "img" / "del.png")))
        del_btn.setFlat(True)
        del_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; }"
            "QPushButton:hover { background: rgba(108,142,245,0.18);"
            " border-radius: 4px; }")
        del_btn.clicked.connect(lambda _=False: self.delete_requested.emit(path))
        line2 = QHBoxLayout()
        line2.setContentsMargins(0, 0, 0, 0)
        line2.setSpacing(4)
        line2.addWidget(self._info_lbl, 1)
        line2.addWidget(del_btn, 0, Qt.AlignVCenter)
        v.addLayout(line2)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.path)
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.context_menu_requested.emit(
                self.path, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)


# ================================================================
# MainWindow — 主窗口（HTML5 响应式风格）
# ================================================================
class MainWindow(QMainWindow):
    """Gemini 式 1:3 分栏主对话窗口。"""

    def __init__(self, initial_role: Optional[str] = None) -> None:
        super().__init__(None)
        self._cfg = ConfigLoader.instance()
        self._roles = RoleManager.instance()
        self._memory = MemoryPipeline.instance()
        self._pool = LLMClientPool.instance()
        self._bus = SignalBus.instance()
        self._pet: Optional[QWidget] = None
        self._worker_thread: Optional[QThread] = None
        self._chat_worker: Optional[ChatWorker] = None
        self._current_bubble: Optional[QLabel] = None
        self._busy = False
        self._prev_speaker = ""
        self._messages: List[Dict[str, Any]] = []
        self._session_path: Optional[Path] = None
        # 需求：重新打开软件时停留在上次退出时的角色/模式（单聊或群聊）
        self._current_role, self._current_group, self._current_mode = \
            self._restore_ui_state(initial_role)
        self._blur_bg = bool(self._cfg.get("ui", "blur_background", default=False))
        self._proactive_on = bool(self._cfg.get("ui", "random_proactive", default=False))
        self._proactive_remaining = 0
        # 待上传附件列表（Gemini 风格：点击「附件」后先挂起，点「发送」才随消息发出）
        self._pending_attachments: List[str] = []
        self._dragging = False
        self._drag_offset = QPoint()
        self._background_pm: Optional[QPixmap] = None
        self._accent = self._cfg.accent()
        # 透明聊天窗口（需求 v4）：开启后主聊天界面半透明，按钮与 PNG 图保持不透明
        self._transparent_chat = bool(
            self._cfg.get("ui", "transparent_chat", default=False))
        self._scale = 1.0
        self._design_width = 1200
        # \@技能 唤醒菜单（SkillManager 懒加载，GUI 不阻塞）
        self._skill_mgr = SkillManager.instance()
        self._skill_popup = None
        self._skill_menu_open = False
        # 技能工具管理器窗口实例（从设置面板打开后复用，主题色实时同步）
        self._skilltools_window: Optional[QWidget] = None
        # 日记记录窗口实例（左栏「日记记录」入口，复用避免重复创建）
        self._daily_window: Optional[QWidget] = None
        # 专业模式 / \closed 状态（需求 v3-2/3/4）：
        # - _pro_skill_active：非开关标签激活 → 立绘切 <角色名>-work.png + 注入专业语气约束
        # - _skill_closed：输入 \closed 后关闭技能语境（后续消息不再套用，除非重新输入标签）
        self._pro_skill_active = False
        self._skill_closed = False
        self._last_stream_render = 0.0

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(920, 640)
        self.resize(1200, 780)
        # 窗口整体不设透明度；由 paintEvent 半透明底色 + 面板不透明实现
        self.setWindowOpacity(1.0)

        self._build_ui()
        # 恢复上次退出时的模式：群聊则把「单聊/群聊」下拉切到群聊并刷新顶部选择框
        if self._current_mode == "群聊":
            self._mode_combo.setCurrentText("群聊")
        else:
            self._refresh_select_combo()
        self._init_resize()
        self._connect_bus()
        self._load_theme()
        self._apply_role(self._current_role, init=True)
        self._build_tray()
        self._apply_icon()
        self._refresh_history_list()
        # 需求：记录本次主界面打开时间（data/logintime.json + logintime_history.json），
        # 供对话/问候 API 调用感知用户常用打开时段；受设置开关控制，失败不阻塞启动
        try:
            from logintime import record_login
            record_login()
        except Exception as exc:  # noqa: BLE001
            logger.warning("记录登陆时间失败: %s", exc)

    # ------------------------------------------------------------ 默认角色
    def _default_role(self) -> str:
        roles = self._roles.list_roles()
        return roles[0] if roles else "默认助手"

    def _restore_ui_state(self, initial_role: Optional[str]) -> Tuple[str, str, str]:
        """恢复上次退出时的（角色, 群组, 模式）。

        优先级：配置保存的角色 > 启动参数/默认角色；保存的群组需存在于
        当前群聊组中，否则回退到第一个群聊组。返回 (role, group, mode)。
        """
        roles = self._roles.list_roles() or ["默认助手"]
        groups = self._roles.list_groups()
        saved_role = (self._cfg.get("ui", "last_role", default="") or "").strip()
        saved_group = (self._cfg.get("ui", "last_group", default="") or "").strip()
        saved_mode = (self._cfg.get("ui", "last_mode", default="单聊") or "").strip()

        role = saved_role if saved_role in roles else (initial_role or self._default_role())
        if role not in roles:
            role = roles[0]
        mode = saved_mode if saved_mode in ("单聊", "群聊") else "单聊"
        if mode == "群聊":
            group = saved_group if saved_group in groups else (
                next(iter(groups)) if groups else "")
        else:
            group = ""
        return role, group, mode

    def _persist_ui_state(self) -> None:
        """保存当前（模式, 角色, 群组）到配置，供下次启动恢复。"""
        try:
            self._cfg.set(self._current_mode, "ui", "last_mode")
            self._cfg.set(self._current_role, "ui", "last_role")
            self._cfg.set(self._current_group, "ui", "last_group")
            self._cfg.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存界面状态失败: %s", exc)

    def _user_name(self) -> str:
        """用户称呼（聊天中用户显示的名字）。"""
        return self._cfg.user_name()

    def set_pet(self, pet: Optional[QWidget]) -> None:
        self._pet = pet
        if pet is not None and hasattr(self, "_pet_btn"):
            self._pet_btn.setText("桌宠：关闭" if pet.isVisible() else "桌宠：开启")

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        central.setStyleSheet(root_qss(self._accent, self._scale,
                                       self._transparent_chat))
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
        self._status_label = QLabel(self._status_text())
        self._status_label.setObjectName("statusText")
        status.addWidget(self._status_label)
        status.addStretch(1)
        root.addLayout(status)


    # ------------------------------------------------------------ 标题栏
    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("Celestia AssistantAI")
        title.setObjectName("appTitle")
        layout.addWidget(title)
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
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
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
        # 需求：关闭按钮缩回托盘前先保存当前界面状态，
        # 保证重启后停留在上次退出时的角色/模式（单聊或群聊）
        self._persist_ui_state()
        # 缩回任务栏：静默隐藏到系统托盘，不弹系统通知、不播放提示音
        self.hide()

    # ------------------------------------------------------------ 无边框窗口缩放
    def _init_resize(self) -> None:
        """启用无边框窗口的四边/四角拖拽缩放与悬停光标反馈。"""
        self._edge = 8                      # 边缘/角落识别宽度（px）
        self._resize_dir = 0                # 位掩码：1左 2右 4上 8下
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
        """递归开启鼠标跟踪，使未按下按键时的 MouseMove 也能产生光标反馈。"""
        widget.setMouseTracking(True)
        for child in widget.findChildren(QWidget):
            try:
                child.setMouseTracking(True)
            except Exception:  # noqa: BLE001
                pass

    def _resize_edge(self, gpos: QPoint) -> int:
        """返回全局坐标 gpos 命中的缩放方向位掩码（0=未命中边缘）。"""
        g = self.geometry()
        e = self._edge
        d = 0
        if g.left() <= gpos.x() <= g.left() + e:
            d |= 1     # 左
        if g.right() - e <= gpos.x() <= g.right():
            d |= 2     # 右
        if g.top() <= gpos.y() <= g.top() + e:
            d |= 4     # 上
        if g.bottom() - e <= gpos.y() <= g.bottom():
            d |= 8     # 下
        return d

    def _apply_resize(self, gpos: QPoint) -> None:
        """根据拖拽起点与当前全局坐标计算并设置新窗口几何。"""
        dx = gpos.x() - self._resize_start_global.x()
        dy = gpos.y() - self._resize_start_global.y()
        sg = self._resize_start_geom
        x, y, w, h = sg.x(), sg.y(), sg.width(), sg.height()
        if self._resize_dir & 1:            # 左
            x = sg.x() + dx
            w = sg.width() - dx
        if self._resize_dir & 2:            # 右
            w = sg.width() + dx
        if self._resize_dir & 4:            # 上
            y = sg.y() + dy
            h = sg.height() - dy
        if self._resize_dir & 8:            # 下
            h = sg.height() + dy
        # 最小尺寸约束
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
        """按命中的边缘/角落切换缩放手型光标。"""
        d = self._resize_edge(gpos)
        if d == 0:
            self.unsetCursor()
        elif d in (1, 2):
            self.setCursor(Qt.SizeHorCursor)
        elif d in (4, 8):
            self.setCursor(Qt.SizeVerCursor)
        elif d in (5, 10):                  # 左上 / 右下
            self.setCursor(Qt.SizeFDiagCursor)
        else:                               # 右上 / 左下
            self.setCursor(Qt.SizeBDiagCursor)

    def _resize_event(self, obj: Any, event: Any) -> bool:
        """无边框窗口四边/四角缩放：仅处理本窗口内鼠标事件。

        由统一入口 eventFilter（输入框回车发送所在的同一个方法）调用。
        """
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




    # ------------------------------------------------------------ 左栏
    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 立绘区（占左侧 4/5，强制 300x400 基准）
        self._portrait_label = QLabel()
        self._portrait_label.setObjectName("portrait")
        self._portrait_label.setAlignment(Qt.AlignCenter)
        self._portrait_label.setMinimumSize(300, 400)
        layout.addWidget(self._portrait_label, 4)

        self._emotion_label = QLabel("平静")
        self._emotion_label.setObjectName("emotionTag")
        self._emotion_label.setAlignment(Qt.AlignCenter)
        self._emotion_label.hide()          # 不在主界面用文字显示情绪（需求）
        layout.addWidget(self._emotion_label)

        # 1. 角色/群组选择（单聊 → 角色缩写；群聊 → 群聊组名）
        self._select_combo = QComboBox()
        # 按当前模式填充：单聊=角色缩写（userData 保存真实目录名）；群聊=群组名
        self._refresh_select_combo()
        self._select_combo.currentTextChanged.connect(self._on_select_changed)
        layout.addWidget(self._select_combo)
        # 兼容旧引用（tools 测试等）
        self._role_combo = self._select_combo

        # 2. 单聊 / 群聊切换
        self._mode_combo = QComboBox()
        # 构建期间 blockSignals，避免 addItems 触发 currentTextChanged 提前执行
        # _on_mode_changed（此时右侧面板尚未构建，_chat_layout 不存在）
        self._mode_combo.blockSignals(True)
        self._mode_combo.addItems(["单聊", "群聊"])
        self._mode_combo.blockSignals(False)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        layout.addWidget(self._mode_combo)
        # 兼容旧引用（tools 测试等）
        self._group_combo = self._mode_combo

        # 3. 随机主动对话开关
        self._proactive_check = QCheckBox("随机主动对话")
        self._proactive_check.setChecked(self._proactive_on)
        self._proactive_check.toggled.connect(self._on_proactive_toggled)
        layout.addWidget(self._proactive_check)

        # 4. 设置
        settings_btn = QPushButton("设置")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self._open_settings)
        settings_btn.setStyleSheet(ghost_btn_qss())
        layout.addWidget(settings_btn)

        # 5. 桌宠开关
        self._pet_btn = QPushButton("桌宠：开启")
        self._pet_btn.setCursor(Qt.PointingHandCursor)
        self._pet_btn.clicked.connect(self._toggle_pet)
        self._pet_btn.setStyleSheet(ghost_btn_qss())
        layout.addWidget(self._pet_btn)

        # 6. 日记记录入口（子项目 daily.py 的日记主页面）
        daily_btn = QPushButton("日记记录")
        daily_btn.setCursor(Qt.PointingHandCursor)
        daily_btn.clicked.connect(self._open_daily)
        daily_btn.setStyleSheet(ghost_btn_qss())
        layout.addWidget(daily_btn)


        layout.addStretch(1)

        return panel

    def _set_portrait(self, role: str, emotion: str) -> None:
        # 与上一次情绪/角色相同 → 不切换图片（需求：一样才切换），
        # 但 portrait_switched 信号仍发出（供其他组件广播情绪）
        # 需求 v3-3：专业模式（非开关标签激活）时无论情绪一律显示 <角色名>-work.png
        pro = bool(getattr(self, "_pro_skill_active", False))
        show_key = "work" if pro else emotion
        key = (role, show_key, pro)
        changed = getattr(self, "_last_portrait_key", None) != key
        self._last_portrait_key = key
        if changed:
            if pro:
                path = self._roles.portrait_path(role, "work")
            else:
                path = self._roles.portrait_path(role, emotion)
            if path:
                pm = _load_pixmap(path, 300, 400, circular=False)
            else:
                pm = QPixmap(300, 400)
                pm.fill(QColor("#cfd8ea"))
            fade_pixmap(self._portrait_label, pm)   # 情绪切换 1.2 秒渐隐过渡
        # 不在主界面用文字显示当前情绪（需求：隐藏文字标签）
        self._emotion_label.hide()
        self._bus.portrait_switched.emit(role, emotion)

    def _avatar_path(self, role: str) -> str:
        """角色头像路径：专业模式下优先使用 <角色名>-work.png（需求 v3-3）。"""
        if self._pro_skill_active:
            p = self._roles.portrait_path(role, "work")
            if p:
                return p
        return self._roles.portrait_path(role)


    # ------------------------------------------------------------ 右栏
    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("mainPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(14, 12, 14, 6)
        self._session_title = QLabel("新会话")
        self._session_title.setObjectName("sessionTitle")
        top.addWidget(self._session_title)
        top.addStretch(1)

        new_btn = QPushButton("新会话")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._new_session)
        new_btn.setStyleSheet(ghost_btn_qss())
        top.addWidget(new_btn)

        self._history_btn = QPushButton("历史")
        self._history_btn.setCheckable(True)
        self._history_btn.setCursor(Qt.PointingHandCursor)
        self._history_btn.toggled.connect(self._toggle_history)
        self._history_btn.setStyleSheet(ghost_btn_qss())
        top.addWidget(self._history_btn)
        layout.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_chat_area(), 3)
        self._history_widget = self._build_history_widget()
        self._history_widget.hide()
        body.addWidget(self._history_widget, 1)
        layout.addLayout(body, 1)

        layout.addWidget(self._build_input_area())
        return panel

    def _build_chat_area(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(6, 2, 6, 2)
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setFrameShape(QFrame.NoFrame)
        self._chat_scroll.setObjectName("chatScroll")
        self._chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 需求 2：禁止横向滚动条（对话宽度随窗口等比缩放，绝不出现左右滑块）
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(8, 6, 8, 6)
        self._chat_layout.setSpacing(10)
        self._chat_layout.addStretch(1)
        self._chat_scroll.setWidget(self._chat_container)
        v.addWidget(self._chat_scroll)
        return wrap

    def _build_history_widget(self) -> QWidget:
        w = QFrame()
        w.setObjectName("historyPanel")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(6, 6, 6, 6)

        # 头部：标题（删除按钮在每一轮会话行内）
        head = QHBoxLayout()
        title = QLabel("历史会话")
        title.setStyleSheet(
            f"color:{TEXT_DARK}; font-size:13px; font-weight:600;")
        head.addWidget(title, 1)
        layout.addLayout(head)

        # 历史列表改用 QScrollArea + 垂直布局（不用 QListWidget+setItemWidget，
        # 彻底避免 itemWidget 行高坍缩导致的重叠，以及 item 内重排导致的 Qt 闪退）
        scroll = QScrollArea()
        scroll.setObjectName("historyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        container.setObjectName("historyListContainer")
        self._history_layout = QVBoxLayout(container)
        self._history_layout.setContentsMargins(2, 2, 2, 2)
        self._history_layout.setSpacing(6)
        self._history_layout.addStretch(1)      # 条目从顶部排，底部留白
        scroll.setWidget(container)

        self._history_list = scroll             # 兼容旧引用/测试（findChildren 等）
        self._history_container = container
        layout.addWidget(scroll, 1)
        return w

    def _show_history_menu(self, path: str, gpos: QPoint) -> None:
        """历史会话右键菜单：修改标题 / 删除。"""
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        act_rename = menu.addAction("修改标题")
        act_del = menu.addAction("删除此会话")
        act = menu.exec(gpos)
        if act == act_rename:
            self._rename_history_path(path)
        elif act == act_del:
            self._delete_history_path(path)

    def _rename_history_path(self, path: str) -> None:
        """修改历史会话标题（写入 session json 的 title 字段）。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "读取失败", str(exc))
            return
        current = data.get("title") or data.get("role") or "会话"
        new_title, ok = QInputDialog.getText(
            self, "修改标题", "请输入新的会话标题：", text=current)
        if not ok or not new_title.strip():
            return
        data["title"] = new_title.strip()[:40]
        try:
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "保存失败", str(exc))
            return
        self._refresh_history_list()

    def _toggle_history(self, checked: bool) -> None:
        self._history_widget.setVisible(checked)

    def _build_input_area(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("inputArea")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        # 待上传附件条（Gemini 风格：选择后先挂起显示，编辑完文字再点「发送」发出）
        self._attach_bar = QFrame()
        self._attach_bar.setObjectName("attachBar")
        # 需求：附件先一横行显示，放不下时自动换行（流式布局）
        self._attach_layout = _FlowLayout(self._attach_bar, margin=2, spacing=6)
        self._attach_bar.hide()
        v.addWidget(self._attach_bar)

        layout = QHBoxLayout()
        layout.setSpacing(8)

        attach_btn = QPushButton("附件")
        attach_btn.setFixedSize(64, 36)
        attach_btn.setToolTip("添加附件（点击后待上传，编辑完文字点「发送」一起发出）")
        attach_btn.setCursor(Qt.PointingHandCursor)
        attach_btn.clicked.connect(self._on_attach)
        attach_btn.setStyleSheet(ghost_btn_qss(font_size=15, padding="4px"))
        layout.addWidget(attach_btn)

        self._input = QTextEdit()
        self._input.setPlaceholderText("输入消息（\\@ 调用技能工具）")
        self._input.setFixedHeight(64)
        self._input.setObjectName("chatInput")
        self._input.installEventFilter(self)   # 回车发送
        # \@技能 唤醒弹窗（焦点留在输入框，键盘由 eventFilter 接管）
        self._skill_popup = SkillPopup(self)
        self._skill_popup.activated.connect(self._on_skill_selected)
        self._input.textChanged.connect(self._on_input_changed)
        self._input.cursorPositionChanged.connect(self._on_input_changed)
        layout.addWidget(self._input, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setFixedSize(76, 40)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send)
        layout.addWidget(self._send_btn)
        v.addLayout(layout)

        # 重建附件条（初始化时隐藏）
        self._refresh_attach_bar()
        return wrap


    # ------------------------------------------------------------ 托盘
    def _build_tray(self) -> None:
        global _TRAY_SINGLETON
        if _TRAY_SINGLETON is not None:
            # 复用已有托盘图标，确保任务栏只保留一个小图标
            self._tray = _TRAY_SINGLETON
            return
        pm = QPixmap(64, 64)
        pm.fill(QColor(ACCENT))
        self._tray = QSystemTrayIcon(QIcon(pm), self)
        self._tray.setToolTip("Celestia AssistantAI")
        menu = QMenu()
        menu.setStyleSheet(_MENU_QSS)
        act_show = menu.addAction("显示/隐藏主窗口")
        act_show.triggered.connect(self._toggle_visible)
        if self._pet is not None:
            act_pet = menu.addAction("显示/隐藏桌宠")
            act_pet.triggered.connect(self._toggle_pet)
        menu.addSeparator()
        act_settings = menu.addAction("设置")
        act_settings.triggered.connect(self._open_settings)
        act_quit = menu.addAction("退出")
        # 需求：托盘「退出」走 app_quit 信号（先持久化当前界面状态再退出），
        # 保证重启后停留在上次退出时的角色/模式（单聊或群聊）
        act_quit.triggered.connect(
            lambda _=False: self._bus.app_quit.emit())
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        _TRAY_SINGLETON = self._tray

    def _on_tray_activated(self, reason: Any) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _toggle_pet(self, _checked: bool = False) -> None:
        """切换桌宠显示/隐藏；桌宠未创建时动态创建（兼容配置关闭的情况）。"""
        existed = self._pet is not None
        if not self._ensure_pet():
            return
        if not existed:
            # 刚动态创建：保持显示（_ensure_pet 已 show()，不再触发隐藏）
            self._pet.show()
            self._pet.raise_()
            self._pet.activateWindow()
        elif self._pet.isVisible():
            self._pet.hide()
        else:
            self._pet.show()
            self._pet.raise_()
            self._pet.activateWindow()
        if hasattr(self, "_pet_btn"):
            self._pet_btn.setText(
                "桌宠：关闭" if self._pet.isVisible() else "桌宠：开启")

    def _ensure_pet(self) -> bool:
        """确保桌宠实例存在（配置禁用或未创建时动态创建）。"""
        if self._pet is not None:
            return True
        try:
            from pet_manager import DesktopPet
            pet = DesktopPet(main_window=self,
                             initial_role=self._current_role)
            pet.show()
            self._pet = pet
            logger.info("桌面宠物已动态创建（%s）", self._current_role)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建桌宠失败: %s", exc)
            return False

    # ------------------------------------------------------------ 日记记录
    def _open_daily(self, _checked: bool = False) -> None:
        """打开日记本子项目（daily.py）的日记主页面。

        日记数据位于 主目录/dailydata（dailytext 日记 JSON / dailyfile 附件）。
        已打开时前置到最上面，避免重复创建窗口。
        """
        win = getattr(self, "_daily_window", None)
        if win is not None:
            win.show()
            win.raise_()
            win.activateWindow()
            return
        try:
            from daily import DailyMainWindow as DailyWindow
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "日记记录", f"日记模块加载失败：{exc}")
            return
        try:
            win = DailyWindow()
            self._daily_window = win
            win.show()
            win.raise_()
            win.activateWindow()
        except Exception as exc:  # noqa: BLE001
            logger.warning("日记主页面打开失败: %s", exc)
            styled_warning(self, "日记记录", f"日记主页面打开失败：{exc}")

    # ------------------------------------------------------------ 专注助手
    def _open_focus_assistant(self, _checked: bool = False) -> None:
        """专注助手：先设置专注时间，再弹出倒计时悬浮窗；角色按角色风格鼓励。

        需求：专注助手在执行前需要先设置专注的时间；时间倒计时以文字形式
        显示在 主目录/img/time.png 上；未达到专注时间 / 达到专注时间时，
        均调用 API 以当前角色语气输出鼓励（坚持 / 完成祝贺）。
        """
        try:
            from focus_assistant import FocusDurationDialog, FocusCountdownWindow
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "专注助手", f"专注助手加载失败：{exc}")
            return
        dlg = FocusDurationDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        win = FocusCountdownWindow(role=self._current_role,
                                   minutes=dlg.minutes())
        # 专注鼓励只在桌宠模式下弹窗输出，不写入主界面聊天区
        self._focus_window = win
        win.show()
        win.raise_()
        win.activateWindow()

    # ------------------------------------------------------------ 信号订阅
    def _connect_bus(self) -> None:
        # 注意：不能将 reply_stream 连接到 _on_stream —— _on_stream 内部会
        # emit reply_stream，自我订阅会造成无限递归，卡死主线程。
        # 流式片段已由 ChatWorker.token 直接连接到 _on_stream。
        self._bus.pet_proactive.connect(self._on_pet_proactive)
        self._bus.show_main_requested.connect(self._toggle_visible)
        self._bus.settings_updated.connect(self._on_settings_updated)
        # 需求：退出（含系统托盘「退出」路径）前保存当前界面状态，
        # 保证重启后停留在上次退出时的角色/模式（单聊或群聊）。
        # 注意：托盘退出走 QApplication.quit()，不会触发 closeEvent，
        # 因此必须在此处先持久化再退出。
        self._bus.app_quit.connect(self._persist_ui_state)
        self._bus.app_quit.connect(QApplication.instance().quit)

    def _proactive_history(self, role: str) -> List[Dict[str, str]]:
        """收集最近对话作为随机主动对话的「上文」（当前会话优先，缺省回退短期记忆）。

        与主对话流程（ChatWorker.run）保持一致：每条消息以「名字: 内容」形式
        注入 API 上下文，让随机主动问候与刚才聊的话题自然衔接。
        """
        hist: List[Dict[str, str]] = []
        msgs = list(getattr(self, "_messages", None) or [])
        user_name = self._user_name()
        for it in msgs[-10:]:
            m_role = "user" if it.get("role") == "user" else "assistant"
            m_name = (it.get("name")
                      or (user_name if m_role == "user" else role)).strip()
            content = (it.get("content") or "").strip()
            if not content:
                continue
            hist.append({"role": m_role, "name": m_name, "content": content})
        if hist:
            return hist[-8:]
        # 当前会话为空（如桌宠定时问候）时，回退到已持久化的短期记忆
        try:
            return self._memory.recent_turns(role, self._current_group or None,
                                             limit=4)
        except Exception:  # noqa: BLE001
            return []

    def _on_pet_proactive(self) -> None:
        """随机主动对话：参考上文（注入最近对话，API 生成）+ 角色特征 + 记忆内容（⑳）。"""
        if self._busy:
            return
        members = self._current_members()
        if self._current_group and members:
            # 群聊主动问候：由上一位发言角色发起（无则随机一名群成员），
            # 头像/名字与该角色一一对应（需求 1）
            role = (self._prev_speaker if self._prev_speaker in members
                    else random.choice(members))
        else:
            role = self._current_role
        name = self._roles.display_name(role)
        sys_p = self._roles.role_system_prompt(role)
        user_name = self._user_name()
        sys_p += (f" 和你对话的用户名字是「{user_name}」，"
                  f"请在合适时机直接称呼这个名字。")
        ctx = self._memory.retrieve_before("主动问候", role, self._current_group or None)
        messages = [{"role": "system",
                     "content": sys_p + " 请务必结合上面注入的最近对话（上文）内容，"
                     "对用户说一句与刚才话题自然衔接、符合你性格的主动问候，"
                     "不超过 30 字，并标注【情感】。"}]
        if ctx:
            messages.append({"role": "system", "content": "相关记忆：\n" + ctx})
        # 参考上文：把当前会话最近的对话作为 API 上下文注入，
        # 让随机主动对话内容与刚才聊的话题衔接（需求：随机主动对话必须参考上文）
        for turn in self._proactive_history(role):
            messages.append({"role": turn["role"],
                             "content": f"{turn['name']}: {turn['content']}"})
        messages.append({"role": "user", "content": "请参考上面的最近对话内容，主动打招呼。"})
        worker = LLMWorker(messages, kind="main", stream=False, max_tokens=80)
        worker.request_finished.connect(
            lambda text: self._show_proactive(name, role, text))
        worker.request_error.connect(
            lambda e: self._show_proactive(name, role, ""))
        # 保存 worker 引用，防止被 GC 回收导致 QThread 销毁崩溃（闪退修复）
        self._proactive_worker = worker
        try:
            worker.finished.connect(
                lambda w=worker: self._clear_proactive_worker(w))
        except Exception:  # noqa: BLE001
            pass
        worker.start()

    def _clear_proactive_worker(self, worker: Any) -> None:
        """主动对话线程结束后清理引用。"""
        if getattr(self, "_proactive_worker", None) is worker:
            self._proactive_worker = None

    def _show_proactive(self, name: str, role: str, text: str) -> None:
        if not text or not text.strip():
            text = random.choice(["在忙吗？我来陪你聊聊天~", "今天过得怎么样？",
                                  "有什么我可以帮忙的吗？", "休息一下，看看窗外吧~"])
        clean = self._roles.strip_emotion_tags(text)
        # 需求：主动问候正文也不显示 [角色名]: / 角色名： 前缀
        if self._current_group:
            clean = ChatWorker._strip_group_prefix(clean, self._current_members())
        else:
            clean = ChatWorker._strip_role_prefix(clean, self._current_role)
        # 主动问候：按发起问候的角色显示对应头像/名字（需求 1）
        pro_bubble = self._append_bubble(name, clean, role=role)
        self._messages.append({"role": "assistant", "name": role,
                               "content": clean, "ts": time.time()})
        pb_wrap = getattr(pro_bubble, "_wrap", None)
        if pb_wrap is not None:
            pb_wrap._msg = self._messages[-1]
        emo = self._roles.detect_emotion(text, role)
        self._set_portrait(role, emo)
        # 需求：主界面回复/主动问候不再向桌宠广播 pet_say，
        # 避免桌宠旁弹出「只有文字没有底图」的重复气泡（错误弹窗）
        # 群聊主动问候：桌宠跟随当前角色（与最后发言角色保持一致）
        if self._current_group and role in self._current_members():
            self._bus.speaker_switched.emit(role)
        self._save_session()

    # ------------------------------------------------------------ 角色/群聊
    def _on_role_changed(self, name: str) -> None:
        if not name:
            return
        self._current_role = name
        self._current_group = ""
        # 切回单聊模式（最下面的下拉框 → 单聊）
        if self._mode_combo.currentText() != "单聊":
            self._mode_combo.setCurrentText("单聊")
        self._refresh_select_combo()
        self._new_session(quiet=True)
        self._apply_role(name)
        self._bus.role_switched.emit(name)
        self._bus.group_changed.emit("")
        self._persist_ui_state()

    def _on_select_changed(self, name: str) -> None:
        """顶部选择框：单聊模式 → 角色缩写；群聊模式 → 群聊组名。

        单聊时下拉项显示缩写（如 RD），真实角色目录名通过 itemData
        保存，此处还原后再交给 _on_role_changed，避免缩写污染
        _current_role 等内部逻辑（文件路径/角色卡均需完整目录名）。
        """
        if not name:
            return
        if self._current_mode == "群聊":
            self._current_group = name
            self._new_session(quiet=True)
            self._apply_role(self._current_role)
            self._bus.group_changed.emit(name)
            # 进入群聊：桌宠显示为该群最近一次会话最后一条消息的发言角色
            self._sync_pet_to_last_speaker()
        else:
            role = self._select_combo.currentData() or self._roles.full_name(name)
            self._on_role_changed(role)
        self._persist_ui_state()

    def _on_mode_changed(self, mode: str) -> None:
        """最下面的下拉框：单聊 / 群聊。切换后顶部选择框内容随之切换。"""
        self._current_mode = mode
        if mode == "群聊":
            groups = self._roles.list_groups()
            if self._current_group not in groups:
                self._current_group = next(iter(groups)) if groups else ""
        else:
            self._current_group = ""
        self._refresh_select_combo()
        self._new_session(quiet=True)
        self._apply_role(self._current_role)
        self._bus.group_changed.emit(self._current_group)
        if not self._current_group:
            self._bus.role_switched.emit(self._current_role)
        else:
            self._sync_pet_to_last_speaker()
        self._persist_ui_state()

    def _refresh_select_combo(self) -> None:
        """按当前模式刷新顶部选择框内容（单聊=角色缩写；群聊=群组名）。"""
        self._select_combo.blockSignals(True)
        self._select_combo.clear()
        if self._current_mode == "群聊":
            groups = self._roles.list_groups()
            self._select_combo.addItems(list(groups) if groups else [])
            if self._current_group in groups:
                self._select_combo.setCurrentText(self._current_group)
        else:
            roles = self._roles.list_roles() or ["默认助手"]
            for role in roles:
                # 显示缩写（限制字数防错乱），真实目录名存入 userData 供还原
                self._select_combo.addItem(self._roles.sidebar_label(role), role)
            idx = self._select_combo.findData(self._current_role)
            if idx < 0:
                idx = self._select_combo.findText(
                    self._roles.sidebar_label(self._current_role))
            self._select_combo.setCurrentIndex(max(idx, 0))
        self._select_combo.blockSignals(False)

    def _sync_pet_to_last_speaker(self) -> None:
        """进入群聊时，桌宠切换为该群最近一次会话最后一条消息的发言角色。"""
        if not self._current_group:
            return
        members = self._current_members()
        if not members:
            return
        conv_dir = self._cfg.conversations_dir
        if not conv_dir.is_dir():
            return
        prefix = f"session_{self._session_key()}_"
        paths = sorted(conv_dir.glob(prefix + "*.json"), reverse=True)
        if not paths:
            return
        try:
            data = json.loads(paths[0].read_text(encoding="utf-8"))
            for msg in reversed(data.get("messages") or []):
                if msg.get("role") == "assistant" and msg.get("name") in members:
                    self._bus.speaker_switched.emit(msg["name"])
                    return
        except Exception:  # noqa: BLE001
            pass

    def _current_members(self) -> List[str]:
        """当前会话成员：单聊=[]；群聊（全部角色）=所有角色；自定义群=group.json。"""
        if not self._current_group:
            return []
        if self._current_group == GROUP_ALL:
            return self._roles.list_roles()
        return self._roles.group_members(self._current_group)

    def _apply_role(self, role: str, init: bool = False) -> None:
        emo = self._roles.default_emotion(role)
        self._set_portrait(role, emo)
        if not init:
            # 标题同样使用缩写/短名，避免角色目录名（如 Twilight_Sparkle）
            # 在顶部标题栏撑开布局
            self._session_title.setText(
                f"{self._roles.sidebar_label(role)}"
                f"{' · ' + self._current_group if self._current_group else ''}"
            )

    def _on_proactive_toggled(self, checked: bool) -> None:
        self._proactive_on = bool(checked)
        self._cfg.set(self._proactive_on, "ui", "random_proactive")
        self._cfg.save()

    # ------------------------------------------------------------ 主题
    def _theme_candidates(self) -> List[Path]:
        theme_dir = self._cfg.theme_dir
        return sorted(
            [p for p in theme_dir.glob("*")
             if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        ) if theme_dir.is_dir() else []

    def _load_theme(self) -> None:
        candidates = self._theme_candidates()
        if not candidates:
            self._background_pm = None
            return
        # 优先加载设置面板中选择的主题
        saved = self._cfg.get("ui", "theme_file", default="") or ""
        chosen = Path(saved) if saved else None
        if chosen is None or not chosen.exists():
            chosen = candidates[0]
        self._apply_theme(str(chosen))

    def _apply_theme(self, path: str) -> None:
        self._background_pm = (
            _blur_image(path, 24) if self._blur_bg else QPixmap(path))
        self._cfg.set(path, "ui", "theme_file")
        self._cfg.save()
        self.update()

    def _toggle_blur(self, on: bool) -> None:
        self._blur_bg = on
        self._cfg.set(on, "ui", "blur_background")
        self._cfg.save()
        self._load_theme()

    def _apply_accent(self, color: str) -> None:
        """修改软件主题色并实时生效（整个界面重建 QSS）。"""
        self._accent = color
        self._cfg.set(color, "ui", "accent")
        self._cfg.save()
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet(root_qss(color, self._scale,
                                           self._transparent_chat))
        # 技能工具管理器跟随主界面主题色（需求：主界面风格变化 skilltools 也遵守）
        if self._skilltools_window is not None:
            try:
                self._skilltools_window.apply_accent(color)
            except Exception:  # noqa: BLE001
                pass

    def _apply_scale(self) -> None:
        """非全屏窗口缩放：内容等比例缩放（字体/圆角/间距随窗口宽度变化）。"""
        if self.width() <= 0:
            return
        scale = max(0.7, min(1.5, self.width() / self._design_width))
        if abs(scale - self._scale) < 1e-3:
            return
        self._scale = scale
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet(root_qss(self._accent, scale,
                                           self._transparent_chat))
        # 立绘区等比例缩放（基准 300x400；直接换图避免缩放时渐隐闪烁）
        self._portrait_label.setMinimumSize(int(300 * scale), int(400 * scale))
        pm = _load_pixmap(
            self._roles.portrait_path(
                self._current_role,
                self._roles.default_emotion(self._current_role)),
            int(300 * scale), int(400 * scale), circular=False)
        self._portrait_label.setPixmap(pm)
        # 发送按钮尺寸随缩放同步放大，避免全屏时按钮文字/本体被裁切（需求 4）
        if hasattr(self, "_send_btn"):
            self._send_btn.setFixedSize(
                max(76, int(76 * scale)), max(40, int(40 * scale)))
        # 非全屏缩放后重新适配已渲染气泡宽度，避免旧宽度撑出横向滚动条
        self._resize_bubbles()

    def _bubble_max_width(self) -> int:
        """聊天气泡最大宽度：按设计基准 700px × 当前缩放系数封顶，且不超视口。

        需求 1/2：主界面宽度视为固定设计宽（1200），窗口缩放只等比缩放，
        气泡在缩放后也按比例变小，绝不拉满整个视口、绝不出现横向滑块。
        预留右侧头像（64px）+ 间距（8px）+ 边距（24px），保证角色在左、
        用户居右时头像与对话框都能完整显示，不溢出视口。
        """
        vw = self._chat_scroll.viewport().width()
        if vw <= 0:
            vw = 800
        cap = int(700 * self._scale)
        return max(120, min(vw - 96, cap))

    def _resize_bubbles(self) -> None:
        """按当前缩放系数重算所有已渲染气泡的 max/min 宽度（等比缩放）。

        气泡宽度在 _append_bubble 创建时按当时的 viewport 宽度一次性设置，
        窗口非全屏缩放后必须重算，否则气泡保持旧宽度超出可视区，出现横向滑块条。
        """
        if not hasattr(self, "_chat_container") or not hasattr(self, "_chat_scroll"):
            return
        vw = self._chat_scroll.viewport().width()
        if vw <= 0:
            return
        max_w = self._bubble_max_width()
        min_w = min(240, max(160, max_w - 180))
        for bubble in self._chat_container.findChildren(QLabel):
            obj = bubble.objectName()
            if obj in ("bubbleUser", "bubbleAI"):
                bubble.setMaximumWidth(max_w)
                bubble.setMinimumWidth(min_w)

    def resizeEvent(self, event: Any) -> None:  # noqa: D102
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_icon(self) -> None:
        """应用 ICO 小图标（窗口 + 托盘）。配置为空时使用内置默认图标。"""
        icon_path = self._cfg.get("ui", "icon_path", default="") or ""
        p = Path(icon_path)
        icon = QIcon(str(p)) if p.exists() else self._default_icon()
        self.setWindowIcon(icon)
        if hasattr(self, "_tray"):
            self._tray.setIcon(icon)

    @staticmethod
    def _default_icon() -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(QColor(ACCENT))
        return QIcon(pm)

    def paintEvent(self, event: Any) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        # 半透明底色（透明仅作用于窗口背景；图片栏与对话框栏面板本身不透明）。
        # 透明聊天窗口开启时窗口底色更透，让桌面透出（需求 v4）
        _win_alpha = 120 if self._transparent_chat else 210
        painter.setBrush(QColor(244, 245, 250, _win_alpha))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 16, 16)
        if self._background_pm and not self._background_pm.isNull():
            scaled = self._background_pm.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(rect, 16, 16)
            painter.setClipPath(path)
            painter.setOpacity(0.55)
            painter.drawPixmap(0, 0, self.width(), self.height(), scaled)
            painter.restore()
        painter.end()
        super().paintEvent(event)

    def _status_text(self) -> str:
        base = self._cfg.api_base()
        model = self._cfg.main_model()
        return f"模型: {model}  ·  服务: {base}"


    # ------------------------------------------------------------ 发送流程
    def eventFilter(self, obj: Any, event: Any) -> bool:
        """统一事件过滤器：技能弹窗键盘 + 输入框回车发送 + 无边框窗口四边/四角缩放。"""
        # ① 技能弹窗打开时的键盘劫持（优先级最高：回车=选中技能，不触发发送）
        if (obj is self._input and event.type() == event.Type.KeyPress
                and self._skill_menu_open and self._skill_popup is not None
                and self._skill_popup.isVisible()):
            if event.key() == Qt.Key_Up:
                self._skill_popup.move_selection(-1)
                return True
            if event.key() == Qt.Key_Down:
                self._skill_popup.move_selection(1)
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and not (event.modifiers() & Qt.ShiftModifier):
                self._on_skill_selected()
                return True
            if event.key() == Qt.Key_Escape:
                self._close_skill_menu()
                return True
            # 其余按键放行：由 textChanged / cursorPositionChanged 实时刷新过滤
            return False
        # ② 输入框按键
        if obj is self._input and event.type() == event.Type.KeyPress:
            # 输入框按回车（不带 Shift）发送
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and not (event.modifiers() & Qt.ShiftModifier):
                self._close_skill_menu()
                self._on_send()
                return True
            # 输入 \@ 唤醒技能/工具菜单（由 _skill_should_trigger 判定前一个字符为 \）
            if event.text() == "@" and not (
                    event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
                if self._skill_should_trigger():
                    self._skill_menu_open = True
                    QTimer.singleShot(0, self._refresh_skill_popup)
                return False
            # 光标位于技能标签右侧按退格 → 整块删除标签
            if event.key() == Qt.Key_Backspace and self._skill_tag_backspace():
                return True
        # 输入框失焦关闭技能弹窗
        if obj is self._input and event.type() == event.Type.FocusOut:
            self._close_skill_menu()
        # ③ 无边框窗口四边/四角缩放
        if self._resize_event(obj, event):
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ 技能菜单
    def _skill_should_trigger(self) -> bool:
        """判断刚输入的 @ 前是否恰好是一个反斜杠 \\（即输入 \\@ 唤出菜单）。

        仅在单个反斜杠后触发；\\\\@（双反斜杠）与无前缀 @ 均不误触发。
        """
        cursor = self._input.textCursor()
        pos = cursor.position()
        if pos < 1:
            return False
        ch = self._input.document().characterAt(pos - 1)
        if ch != "\\":
            return False
        if pos >= 2 and self._input.document().characterAt(pos - 2) == "\\":
            return False
        return True

    def _on_input_changed(self) -> None:
        """输入框文本/光标变化时实时刷新技能弹窗（仅当菜单处于打开状态）。"""
        if not self._skill_menu_open:
            return
        self._refresh_skill_popup()

    def _refresh_skill_popup(self) -> None:
        """按光标前文本实时计算技能过滤结果并刷新/隐藏弹窗。"""
        if not self._skill_menu_open or self._skill_popup is None:
            return
        cursor = self._input.textCursor()
        doc = self._input.document()
        before = (doc.toPlainText()[:cursor.position()]
                  .replace("\u2029", "\n").replace("\u2028", "\n"))
        m = re.search(r"\\@([A-Za-z0-9_\u4e00-\u9fa5-]*)$", before)
        if m is None:
            self._close_skill_menu()
            return
        skills = self._skill_mgr.match(m.group(1))
        if not skills:
            self._close_skill_menu()
            return
        cr = self._input.cursorRect()
        global_pos = self._input.mapToGlobal(cr.bottomLeft())
        self._skill_popup.set_skills(skills)
        self._skill_popup.show_at(global_pos)

    def _close_skill_menu(self) -> None:
        """关闭技能弹窗并复位状态。"""
        self._skill_menu_open = False
        if self._skill_popup is not None:
            self._skill_popup.hide()

    def _on_skill_selected(self, skill: Optional[Dict[str, Any]] = None) -> None:
        """选中技能：以 UI Tag 标签无损插入输入框。"""
        if skill is None:
            skill = (self._skill_popup.selected_skill()
                     if self._skill_popup is not None else None)
        if not skill:
            self._close_skill_menu()
            return
        trigger = str(skill.get("trigger") or "").strip()
        if not trigger:
            self._close_skill_menu()
            return
        cursor = self._input.textCursor()
        doc = self._input.document()
        before = (doc.toPlainText()[:cursor.position()]
                  .replace("\u2029", "\n").replace("\u2028", "\n"))
        m = re.search(r"\\@([A-Za-z0-9_\u4e00-\u9fa5-]*)$", before)
        start = cursor.position()
        if m is not None:
            start -= len(m.group(0))
        self._close_skill_menu()   # 先关闭，避免插入触发 textChanged 重新弹出
        self._insert_skill_tag(start, cursor.position(), trigger)
        name = skill.get("name") or trigger
        self._bus.skill_invoked.emit(str(name))

    def _insert_skill_tag(self, start: int, end: int, trigger: str) -> None:
        """以高亮字符格式无损插入 \\@技能 标签，并复位后续字符格式。"""
        cursor = self._input.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#eef2ff"))
        fmt.setForeground(QColor(ACCENT))
        fmt.setFontWeight(QFont.Bold)
        cursor.insertText("\\@" + trigger, fmt)
        # 复位默认格式：防止后续输入继承标签背景/前景色。
        # 注意：setTextCursor() 会从文档中光标位置的字符格式重新推导
        # 「当前字符格式」（即标签末字符的格式），因此 setCurrentCharFormat
        # 必须在 setTextCursor 之后调用，否则会被再次覆盖、格式仍会泄漏。
        cursor.clearSelection()
        self._input.setTextCursor(cursor)
        self._input.setCurrentCharFormat(QTextCharFormat())

    def _skill_tag_backspace(self) -> bool:
        """退格键：光标位于技能标签右侧时整块删除标签。返回 True 表示已消费。"""
        cursor = self._input.textCursor()
        if cursor.hasSelection():
            return False
        pos = cursor.position()
        if pos <= 0:
            return False
        doc = self._input.document()
        before = (doc.toPlainText()[:pos]
                  .replace("\u2029", "\n").replace("\u2028", "\n"))
        m = re.search(r"\\@([A-Za-z0-9_\u4e00-\u9fa5-]+)$", before)
        if m is None:
            return False
        tag = m.group(0)
        if self._skill_mgr.resolve(tag[2:]) is None:
            return False
        c2 = self._input.textCursor()
        c2.setPosition(pos - len(tag))
        c2.setPosition(pos, QTextCursor.KeepAnchor)
        c2.removeSelectedText()
        self._input.setTextCursor(c2)
        return True

    def _parse_skill_tags(self, raw: str) -> List[Dict[str, Any]]:
        """解析文本中的技能标签，返回技能列表（按出现顺序、去重）。"""
        return self._skill_mgr.parse_tags(raw)

    def _expand_skill_prompt(self, raw: str) -> str:
        """构建技能说明注入文本；原文原样保留（无损），无技能时返回空串。

        非开关标签（专业/场景标签）存在时追加 [专业模式] 语境，
        限制专业内容不带角色（小马）语气（需求 v3-2）。
        """
        return self._build_skill_context(self._parse_skill_tags(raw))

    def _build_skill_context(self, tags: List[Dict[str, Any]]) -> str:
        """根据已解析的技能列表构建注入 LLM 的上下文（含专业模式语气约束）。"""
        parts = [self._skill_mgr.build_context(s) for s in tags]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        ctx = "\n---\n".join(parts)
        if any(s.get("kind") != "switch" for s in tags):
            ctx += "\n\n" + _PRO_MODE_INSTRUCTION
        return ctx

    def _process_skill_commands(self, text: str) -> Tuple[str, str, bool]:
        """处理 \\closed 指令与技能标签，返回 (clean_text, skill_context, pro_active)。

        - \\closed：本次发送忽略全部技能标签并关闭技能语境（后续消息不再套用），
          直到用户再次显式输入 \\@触发词 才重新启用（需求 v3-4）；
        - 已关闭状态：后续消息不注入任何技能上下文，\\@触发词 也一并剥离；
        - clean_text 已剥离 \\closed 指令；正常（未关闭）时技能标签保留供 LLM 解析。
        """
        raw = text or ""
        closed_now = _CLOSED_CMD_RE.search(raw) is not None
        clean = _CLOSED_CMD_RE.sub(" ", raw).strip()
        if closed_now:
            self._skill_closed = True
            # 本次回复不套用任何标签：连 \@触发词 也一并剥离，避免以纯文本发给 LLM
            return self._strip_skill_display(clean), "", False
        tags = self._parse_skill_tags(clean)
        if self._skill_closed and not tags:
            return self._strip_skill_display(clean), "", False
        if self._skill_closed and tags:
            self._skill_closed = False   # 用户显式再次输入标签 → 重新启用
        pro_active = any(s.get("kind") != "switch" for s in tags)
        return clean, self._build_skill_context(tags), pro_active

    def _reset_pro_mode(self) -> None:
        """退出专业/技能模式并恢复立绘（\\closed 纯指令时调用）。

        需求：输入 \\closed 提交后退出任何 skill、切回日常聊天模式，
        左侧立绘按原要求显示普通情绪（不再显示专业模式的 work.png）。
        仅当处于专业模式时才真正切换，避免无谓重绘。
        """
        if not self._pro_skill_active:
            return
        self._pro_skill_active = False
        try:
            self._set_portrait(
                self._current_role,
                self._roles.default_emotion(self._current_role))
        except Exception:  # noqa: BLE001
            logger.warning("\\closed 恢复立绘失败", exc_info=True)

    def _retain_skill_tags(self, tags: List[Dict[str, Any]]) -> None:
        """发送后把本次使用的技能标签重新插入输入框（保留高亮格式）。

        这样下次继续输入内容时无需重复输入 \\@触发词；手动删除标签
        （退格整块删除）或输入 \\closed 仍可正常退出技能模式。
        """
        for t in tags:
            trigger = str(t.get("trigger") or "").strip()
            if not trigger:
                continue
            cursor = self._input.textCursor()
            cursor.movePosition(QTextCursor.End)
            pos = cursor.position()
            self._insert_skill_tag(pos, pos, trigger)

    def _on_send(self) -> None:
        if self._busy:
            return
        text = (self._input.toPlainText()
                .replace("\u2029", "\n").replace("\u2028", "\n").strip())
        # 待上传附件：点「附件」后先挂起，随本条消息一起发送（Gemini 风格）
        pending = list(getattr(self, "_pending_attachments", []))
        if not text and not pending:
            return
        if text.startswith("/theme"):
            self._open_settings()
            self._input.clear()
            return
        closed_now = _CLOSED_CMD_RE.search(text) is not None
        # 技能指令处理：\closed 关闭技能语境 / 标签上下文注入 / 专业模式标记
        clean, skill_ctx, pro_active = self._process_skill_commands(text)
        if not clean and not pending:
            # 纯指令（如单独输入 \closed）且无附件：给出系统提示，不调用 LLM
            self._input.clear()
            self._reset_pro_mode()
            self._append_notice(
                "已关闭技能/专业模式：后续回复不再套用任何技能语境。"
                "重新输入 \\@触发词（如 \\@med）可再次启用。")
            return
        # 需求 v5：技能 \@image / 生图技能 / 提示词生图 路由
        tags = self._parse_skill_tags(text)
        image_skill = next(
            (s for s in tags
             if bool(s.get("image_gen"))
             or str(s.get("trigger") or "").lower() in _IMAGE_GEN_TRIGGERS),
            None)
        if image_skill is not None or (
                not tags and any(kw in clean for kw in _IMAGE_GEN_KEYWORDS)):
            self._input.clear()
            if not closed_now and tags:
                self._retain_skill_tags(tags)
            # 发送后清空待上传附件
            self._clear_pending_attachments()
            prompt = self._strip_skill_display(clean)
            self._route_image_gen(prompt or "", image_skill, pending)
            return
        # 保留技能标签：发送后把本次 \@触发词 留在输入框，
        # 下次继续输入内容即可再次使用，无需重复选择标签。
        retain = tags
        self._input.clear()
        if not closed_now and retain:
            self._retain_skill_tags(retain)
        # 发送后清空待上传附件
        self._clear_pending_attachments()
        # 需求 v6：该消息优先使用第一个配置了独立 API 的技能的端点
        api_override = next(
            (s for s in tags if str(s.get("api_base") or "").strip()), None)
        # 需求：\@thinking 思维链技能（要求模型按【思维链】/【回答】格式输出）
        thinking_mode = any(
            str(t.get("trigger") or "").strip().lower()
            in ("thinking", "推理", "思考") for t in tags)
        # 需求：\@network 联网搜索——全局「允许联网」开启或技能勾选「允许联网」时执行搜索
        net_skill = next(
            (t for t in tags
             if str(t.get("trigger") or "").strip().lower()
             in ("network", "联网", "搜索")
             or bool(t.get("network_enabled"))), None)
        web_search_query = ""
        if net_skill is not None and (
                bool(self._cfg.get("web", "enabled", default=False))
                or bool(net_skill.get("network_enabled"))):
            web_search_query = self._strip_skill_display(clean)
        try:
            self._start_chat(clean, pending, skill_context=skill_ctx,
                             pro_active=pro_active, api_override=api_override,
                             thinking_mode=thinking_mode,
                             web_search_query=web_search_query)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"发送失败：{exc}")

    def _on_attach(self) -> None:
        """添加附件：仅加入待上传列表（不立即发送），编辑完文字点「发送」一起发出。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件（可多选，点「发送」时一起上传）", "",
            "文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.txt *.md *.json "
            "*.csv *.pdf *.docx *.xlsx *.pptx)")
        if not paths:
            return
        for p in paths:
            if p and p not in self._pending_attachments:
                self._pending_attachments.append(p)
        self._refresh_attach_bar()
        self._input.setFocus()

    # ------------------------------------------------------------ 待上传附件
    def _refresh_attach_bar(self) -> None:
        """重建待上传附件条（Gemini 风格：显示文件名 + 单个移除按钮）。"""
        if not hasattr(self, "_attach_layout"):
            return
        while self._attach_layout.count() > 0:
            item = self._attach_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for path in self._pending_attachments:
            card = QFrame()
            card.setObjectName("attachChip")
            card.setMaximumWidth(240)
            row = QHBoxLayout(card)
            row.setContentsMargins(8, 3, 4, 3)
            row.setSpacing(6)
            # 需求 1：文件名 ≤15 字（不含后缀），超长省略 + 自动换行，不撑爆聊天框
            icon = QLabel()
            icon.setPixmap(QPixmap(str(project_root() / "img" / "file.png"))
                           .scaled(18, 18, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
            icon.setFixedSize(18, 18)
            row.addWidget(icon)
            name = QLabel(_short_filename(path))
            name.setToolTip(path)
            name.setWordWrap(True)
            name.setMaximumWidth(170)
            row.addWidget(name, 1)
            rm = QPushButton("移除")
            rm.setCursor(Qt.PointingHandCursor)
            rm.setStyleSheet(
                "QPushButton{background:transparent; color:#8b93a7; border:none;"
                " font-size:11px; padding:2px 8px;}"
                "QPushButton:hover{color:#e11d48;}")
            rm.clicked.connect(
                lambda _=False, p=path: self._remove_pending_attachment(p))
            row.addWidget(rm)
            card.setStyleSheet(
                "QFrame#attachChip{background:rgba(108,142,245,0.10);"
                " border:1px solid #d5ddf7; border-radius:8px;}"
                "QLabel{color:#3a4a80; font-size:12px; background:transparent;"
                " border:none;}")
            self._attach_layout.addWidget(card)
        self._attach_bar.setVisible(bool(self._pending_attachments))

    def _remove_pending_attachment(self, path: str) -> None:
        """从待上传列表中移除单个附件。"""
        if path in self._pending_attachments:
            self._pending_attachments.remove(path)
        self._refresh_attach_bar()

    def _clear_pending_attachments(self) -> None:
        """清空待上传附件（发送 / 切换会话时调用）。"""
        self._pending_attachments = []
        self._refresh_attach_bar()

    # ------------------------------------------------------------ 生图（需求 v5）
    def _route_image_gen(self, prompt: str,
                         skill: Optional[Dict[str, Any]],
                         pending: Optional[List[str]] = None) -> None:
        """路由到生图：校验配置 -> 用户气泡 -> 后台线程调用生图 API。"""
        skill = skill or {}
        image_api_ok = bool(
            str(skill.get("api_base") or "").strip()
            or str(self._cfg.get("api", "image_base", default="") or "").strip())
        if not image_api_ok:
            self._append_notice(
                "未配置生图 API：请在「设置 → 生图 API」填写生图 API 地址，"
                "或在技能管理器中为该技能填写独立 API。之后用 \\@image 触发词"
                "或输入提示词（如「画一张星空下的小马」）即可生成图片。")
            return
        if not (prompt or "").strip() and not pending:
            self._append_notice(
                "请输入图片画面描述后再生成，例如：\\@image 一只在星空下散步的小马。")
            return
        self._busy = True
        self._send_btn.setEnabled(False)
        display = (prompt or "").strip()
        if not display and pending:
            display = f"生成图片（附件 {len(pending)} 个）"
        user_bubble = self._append_bubble(
            self._user_name(), display, is_user=True)
        self._messages.append({"role": "user", "name": self._user_name(),
                               "content": prompt or "", "ts": time.time()})
        if pending and user_bubble is not None:
            ub_wrap = getattr(user_bubble, "_wrap", None)
            if ub_wrap is not None:
                self._messages[-1]["attachments"] = list(pending)
                self._append_attachment_links(user_bubble, pending)
        self._current_bubble = self._append_bubble(
            self._roles.display_name(self._current_role),
            _img_html("createimg.gif") + "正在生成图片，请稍候…",
            is_stream=True, role=self._current_role)
        self._stream_buffer = []
        try:
            from utils.async_worker import AsyncWorker
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"生图模块加载失败：{exc}")
            return
        worker = AsyncWorker(self._gen_image_task, prompt or display, skill)
        worker.succeeded.connect(self._on_image_generated)
        worker.failed.connect(self._on_error)
        self._image_worker = worker
        worker.start()

    def _gen_image_task(self, prompt: str,
                        skill: Optional[Dict[str, Any]]) -> str:
        """子线程内执行生图（不触碰任何 QWidget）。"""
        skill = skill or {}
        base = (str(skill.get("api_base") or "").strip()
                or self._cfg.get("api", "image_base", default="") or "")
        key = (str(skill.get("api_key") or "").strip()
               or self._cfg.get("api", "image_key", default="") or "")
        model = (str(skill.get("api_model") or "").strip()
                 or self._cfg.get("api", "image_model", default="") or "")
        return self._pool.image_generate(
            prompt, base_url=base, api_key=key, model=model)

    def _image_html(self, path: str) -> str:
        """构建聊天气泡内显示生图结果的 HTML（图片 + 打开原图链接）。"""
        max_w = max(self._bubble_max_width() - 24, 120)
        pm = QPixmap(path)
        w, h = pm.width(), pm.height()
        if w > max_w:
            h = int(h * max_w / w) if w else max_w
            w = max_w
        href = "file:///" + str(path).replace("\\", "/")
        return (
            f'<div><a href="{href}"><img src="{href}" '
            f'width="{max(w, 1)}" height="{max(h, 1)}" style="border-radius:8px;"></a></div>'
            f'<div style="color:#6b7280;font-size:12px;margin-top:4px;">已生成图片 · '
            f'<a href="{href}" style="color:{self._accent};text-decoration:none;">打开原图</a></div>')

    def _on_image_generated(self, result_path: str) -> None:
        """生图完成：当前气泡显示图片，恢复发送按钮。"""
        self._busy = False
        self._send_btn.setEnabled(True)
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.stop()
        bubble = getattr(self, "_current_bubble", None)
        if bubble is None:
            bubble = self._append_bubble(
                self._roles.display_name(self._current_role), "",
                is_stream=True, role=self._current_role)
            self._current_bubble = bubble
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
        except Exception:  # pragma: no cover
            QUrl = QDesktopServices = None  # type: ignore
        bubble.setTextFormat(Qt.RichText)
        bubble.setText(self._image_html(str(result_path)))
        bubble.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        if QDesktopServices is not None:
            bubble.linkActivated.connect(
                lambda url: QDesktopServices.openUrl(QUrl(url)))
        bubble.setProperty("raw", f"[生成图片] {result_path}")
        wrap = getattr(bubble, "_wrap", None)
        if wrap is not None:
            del_btn = getattr(wrap, "_del_btn", None)
            if del_btn is not None:
                del_btn.setEnabled(True)
        self._messages.append({"role": "assistant",
                               "name": self._roles.display_name(self._current_role),
                               "content": f"[生成图片] {result_path}",
                               "ts": time.time()})
        self._bus.image_generated.emit(str(result_path))
        self._cleanup_worker()
        self._scroll_to_bottom(force=True)

    def _start_chat(self, prompt: str, attachments: List[str],
                    skill_context: str = "", pro_active: bool = False,
                    api_override: Optional[Dict[str, Any]] = None,
                    thinking_mode: bool = False,
                    web_search_query: str = "") -> None:
        self._busy = True
        self._send_btn.setEnabled(False)
        self._reasoning_text: List[str] = []   # 本轮模型原生思维链累积
        # 纯附件消息（无文字）时用附件名占位，供记忆归档与气泡显示使用
        attach_display = ""
        if attachments:
            names = "、".join(Path(p).name for p in attachments[:3])
            attach_display = f"[附件] {names}"
            if len(attachments) > 3:
                attach_display += f" 等 {len(attachments)} 个"
        self._last_prompt = prompt or attach_display   # 供异步记忆归档使用
        self._bus.request_started.emit(prompt or attach_display)
        self._close_skill_menu()

        # 专业模式状态与立绘切换（需求 v3-3：非开关标签启动时立绘切 <角色名>-work.png）
        pro_changed = self._pro_skill_active != pro_active
        self._pro_skill_active = pro_active
        if pro_changed:
            self._set_portrait(
                self._current_role,
                self._roles.default_emotion(self._current_role))
        self._last_stream_render = 0.0

        # 若上一轮仍有残留超时计时器，先停止，避免其误触发错误回调
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.stop()

        # 随机主动：1-5 轮内触发
        if self._proactive_on:
            if self._proactive_remaining <= 0:
                self._proactive_remaining = random.randint(1, 5)
            self._proactive_remaining -= 1

        members = self._current_members()
        uname = self._user_name()
        # 用户气泡显示剥离 \@触发词 指令（消息记录保留完整内容，供 LLM 解析技能）；
        # 纯附件消息（无文字）时显示附件名占位，便于确认已发送内容
        display_text = self._strip_skill_display(prompt)
        if not display_text:
            display_text = attach_display
        user_bubble = self._append_bubble(uname, display_text, is_user=True)
        self._messages.append({"role": "user", "name": uname,
                               "content": prompt, "ts": time.time()})
        ub_wrap = getattr(user_bubble, "_wrap", None)
        if ub_wrap is not None:
            ub_wrap._msg = self._messages[-1]
        # 需求：插入文件并发送后，发送文字下方显示对应发送文件的超链接
        if attachments and ub_wrap is not None:
            self._messages[-1]["attachments"] = list(attachments)
            self._append_attachment_links(user_bubble, attachments)

        # 群聊流式占位气泡：优先取上一位发言角色（无则随机一名群成员）头像/名字，
        # 发言者确定后再按实际发言角色刷新（需求 1：头像/名字与角色一一对应）
        reply_role = self._current_role
        if self._current_group and members:
            reply_role = (self._prev_speaker if self._prev_speaker in members
                          else random.choice(members))
        reply_name = self._roles.display_name(reply_role)
        self._current_bubble = self._append_bubble(
            reply_name, "", is_stream=True, role=reply_role)
        self._stream_buffer: List[str] = []
        # 需求：角色开始输出时主聊天界面立即滚动到最下面
        self._scroll_to_bottom(force=True)

        self._worker_thread = QThread(self)
        self._chat_worker = ChatWorker(
            prompt=prompt, role=self._current_role, group=self._current_group,
            attachments=attachments, members=members, prev_speaker=self._prev_speaker,
            user_name=uname, skill_context=skill_context,
            api_override=api_override,
            thinking_mode=thinking_mode, web_search_query=web_search_query,
        )
        self._chat_worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._chat_worker.run)
        self._chat_worker.token.connect(self._on_stream)
        self._chat_worker.reasoning.connect(self._on_reasoning)
        self._chat_worker.emotion.connect(self._on_emotion)
        self._chat_worker.finished.connect(self._on_finished)
        self._chat_worker.error.connect(self._on_error)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.finished.connect(self._chat_worker.deleteLater)
        # 60 秒无任何输出才提示（LLM 客户端自身默认超时 60 秒，此处仅兜底防御；
        # 之前 10 秒对首 token 较慢的服务商过于苛刻，会被误判为请求失败）
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(
            lambda: self._on_error("请求超时（60 秒未收到回复），请检查网络后重试"))
        self._timeout_timer.start(60000)
        self._worker_thread.start()

    @staticmethod
    def _split_thinking_text(text: str) -> Tuple[str, str]:
        """拆分「【思维链】…【回答】…」格式文本，返回 (思维链, 正文)。

        \\@thinking 技能会让模型按该格式输出；未命中标记则正文为全文。
        """
        t = text or ""
        m = re.search(r"【思维链】(.*?)【回答】", t, re.S)
        if m:
            return m.group(1).strip(), (t[:m.start()] + t[m.end():]).strip()
        if t.strip().startswith("【思维链】"):
            return t[len("【思维链】"):].strip(), ""
        return "", t

    def _on_reasoning(self, chunk: str) -> None:
        """累积模型原生思维链（reasoning_content）并刷新气泡显示。"""
        self._reasoning_text.append(chunk)
        if self._current_bubble is not None:
            raw = self._current_bubble.property("raw") or ""
            self._update_bubble_text(self._display_text(raw))
            self._scroll_to_bottom()

    def _render_thinking_body(self, body: str, reasoning: str) -> str:
        """渲染「思维链灰色块 + 正文」的 HTML。"""
        import html as _html
        parts = []
        if reasoning:
            r = _html.escape(reasoning)
            parts.append(
                f'<div style="color:#8b93a7;font-size:12px;'
                f'background:rgba(108,142,245,0.07);border-left:3px solid '
                f'rgba(108,142,245,0.45);padding:6px 10px;margin:2px 0 8px 0;">'
                f'{_img_html("thinking.gif")}思考过程<br>{r}</div>')
        parts.append(self._render_body_html(body))
        return "".join(parts)

    def _render_body_html(self, body: str) -> str:
        """正文 -> 聊天 HTML：markdown 图片 / 图片 URL 转为「下载后显示」链接。

        需求：模型在主聊天界面可以输出 API 生成的图片——回复中带图片链接时
        先显示可点击占位，后台下载到生图保存目录后替换为本地 <img>。
        """
        import html as _html
        placeholders: List[tuple] = []

        def _md_rep(m: "re.Match[str]") -> str:
            alt = (m.group(1) or "图片").strip() or "图片"
            url = m.group(2)
            token = f"\x00IMG{len(placeholders)}\x00"
            placeholders.append((token, alt, url))
            return token

        def _url_rep(m: "re.Match[str]") -> str:
            url = m.group(0)
            token = f"\x00IMG{len(placeholders)}\x00"
            placeholders.append((token, "图片", url))
            return token

        t = body or ""
        # 1) 用占位符替换 markdown 图片 / 裸图片 URL（避免后续转义破坏标签）
        t = re.sub(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)", _md_rep, t)
        t = re.sub(
            r"(?<![\"=])(https?://[^\s<>\"'()]+\.(?:png|jpe?g|webp|gif))(?![)\"])",
            _url_rep, t, flags=re.I)
        esc = _html.escape(t).replace("&quot;", '"')
        # 2) 恢复图片占位符为可点击链接
        for token, alt, url in placeholders:
            u = _html.escape(url, quote=True)
            tag = (f'<a href="{u}" data-img-url="{u}" '
                   f'style="color:{self._accent};text-decoration:none;">'
                   f'{_img_html("createimg.gif")}{alt}</a>')
            esc = esc.replace(token, tag)
        return f'<div style="white-space:pre-wrap;">{esc}</div>'

    def _update_bubble_text(self, display: str) -> None:
        """按思维链显示开关渲染当前气泡：原生推理/【思维链】块 + 正文。"""
        if self._current_bubble is None:
            return
        native = "".join(getattr(self, "_reasoning_text", []))
        thinking_part, body_part = self._split_thinking_text(display)
        show_th = bool(self._cfg.get("ui", "show_thinking", default=False))
        self._current_bubble.setTextFormat(Qt.RichText)
        if show_th and (native.strip() or thinking_part.strip()):
            self._current_bubble.setText(self._render_thinking_body(
                body_part, reasoning=native or thinking_part))
        else:
            self._current_bubble.setText(self._render_body_html(
                body_part or display))

    @staticmethod
    def _extract_image_urls(text: str) -> List[str]:
        """提取文本中的图片 URL（markdown 图片语法或裸图片链接）。"""
        urls: List[str] = []
        for m in re.finditer(
                r"!\[[^\]]*\]\((https?://[^\s)]+)\)|"
                r"(https?://[^\s<>\"'()]+\.(?:png|jpe?g|webp|gif))",
                text or "", re.I):
            u = m.group(1) or m.group(2)
            if u and u not in urls:
                urls.append(u)
        return urls

    def _download_bubble_images(self, text: str) -> None:
        """扫描模型回复中的图片 URL，后台下载到生图保存目录后显示在气泡中。"""
        urls = self._extract_image_urls(text)
        if not urls:
            return
        try:
            from utils.async_worker import AsyncWorker
        except Exception as exc:  # noqa: BLE001
            logger.warning("图片下载模块加载失败: %s", exc)
            return
        for url in urls:
            try:
                w = AsyncWorker(self._download_image_worker,
                                url, str(self._cfg.image_dir()))
                w.succeeded.connect(
                    lambda path, u=url: self._on_image_downloaded(u, str(path)))
                w.failed.connect(
                    lambda msg: logger.warning("模型图片下载失败: %s", msg))
                w.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("模型图片下载启动失败: %s", exc)

    @staticmethod
    def _download_image_worker(url: str, out_dir: str) -> str:
        """子线程内下载图片到指定目录，返回本地路径。"""
        import httpx
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=60.0, follow_redirects=True)
        r.raise_for_status()
        fname = (f"model_{time.strftime('%Y%m%d_%H%M%S')}_"
                 f"{uuid.uuid4().hex[:6]}.png")
        dest = d / fname
        dest.write_bytes(r.content)
        return str(dest)

    def _on_image_downloaded(self, url: str, local_path: str) -> None:
        """图片下载完成：把气泡中对应链接替换为本地图片。"""
        bubble = getattr(self, "_current_bubble", None)
        if bubble is None:
            return
        import html as _html
        try:
            pm = QPixmap(local_path)
            max_w = max(self._bubble_max_width() - 24, 120)
            w, h = pm.width(), pm.height()
            if w > max_w:
                h = int(h * max_w / w) if w else max_w
                w = max_w
            href = "file:///" + str(local_path).replace("\\", "/")
            img_tag = (f'<a href="{href}"><img src="{href}" '
                       f'width="{max(w, 1)}" height="{max(h, 1)}" '
                       f'style="border-radius:8px;"></a>')
            u_esc = _html.escape(url, quote=True)
            pattern = rf'<a[^>]*data-img-url="{re.escape(u_esc)}"[^>]*>.*?</a>'
            new_html = re.sub(pattern, img_tag, bubble.text(), flags=re.S)
            if new_html:
                bubble.setText(new_html)
            self._scroll_to_bottom(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("模型图片显示失败 %s: %s", url, exc)

    def _on_stream(self, chunk: str) -> None:
        self._stream_buffer.append(chunk)
        # 有流式输出则重置无响应计时器（避免慢回复被误判超时）
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.start(60000)
        if self._current_bubble is not None:
            text = self._current_bubble.property("raw") + chunk
            self._current_bubble.setProperty("raw", text)
            # 流式渲染节流：长回复时避免每个 token 都全文重排（长对话卡死修复），
            # 至少间隔 40ms 刷新一次气泡文本，且每 40 个片段强制刷新兜底。
            now = time.monotonic()
            if now - getattr(self, "_last_stream_render", 0.0) >= 0.04 \
                    or len(self._stream_buffer) % 40 == 0:
                self._last_stream_render = now
                # 流式显示时隐藏【】情绪标签（只在左侧立绘/情绪标签显示）
                self._update_bubble_text(self._display_text(text))
                self._scroll_to_bottom()
        self._bus.reply_stream.emit(chunk)

    def _on_emotion(self, emotion: str) -> None:
        self._set_portrait(self._current_role, emotion)
        self._bus.emotion_changed.emit(emotion)

    def _on_finished(self, clean: str, speaker: str, emotion: str) -> None:
        self._busy = False
        self._send_btn.setEnabled(True)
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.stop()
        # 思维链/正文拆分：消息记录只存正文（不含【思维链】部分）
        _, body_part = self._split_thinking_text(clean)
        msg = {"role": "assistant", "name": speaker,
               "content": body_part if body_part else clean, "ts": time.time()}
        if self._current_bubble is not None:
            # 需求：最终输出隐藏 [角色名] / 角色名： 前缀（姓名由气泡标题展示）
            self._current_bubble.setProperty("raw", clean)
            self._update_bubble_text(self._display_text(clean))
            wrap = getattr(self._current_bubble, "_wrap", None)
            if wrap is not None:
                wrap._msg = msg
                btn = getattr(wrap, "_del_btn", None)
                if btn is not None:
                    btn.setEnabled(True)
        self._messages.append(msg)
        self._prev_speaker = speaker
        if self._current_group:
            self._set_portrait(speaker, emotion)
            # 群聊：气泡头像/名字与发言角色一一对应（需求 3）
            if self._current_bubble is not None:
                avatar_label = getattr(self._current_bubble, "_avatar_label", None)
                if avatar_label is not None:
                    avatar_label.setPixmap(_load_pixmap(
                        self._avatar_path(speaker), 64, 64,
                        circular=True))
                name_label = getattr(self._current_bubble, "_name_label", None)
                if name_label is not None:
                    name_label.setText(self._roles.display_name(speaker))
            # 群聊：桌宠显示为发送最后一条消息的角色（需求 2）
            if speaker in self._current_members():
                self._bus.speaker_switched.emit(speaker)
        self._bus.reply_finished.emit(clean, speaker)
        # 需求：模型回复中的图片 URL（markdown 图片/图片链接）自动下载并显示
        self._download_bubble_images(clean)
        # 主界面对话完成：不向桌宠广播 pet_say，
        # 避免桌宠气泡重复显示输出、悬浮小窗再次弹窗（需求）
        self._save_session()
        self._cleanup_worker()
        # 异步记忆归档：archive_after 内含小模型提炼 LLM 调用（可能较慢），
        # 放在后台 AsyncWorker 线程执行，避免阻塞主线程事件循环导致界面卡死。
        self._archive_async(clean, speaker)
        # 随机主动：触发一次（与上一输出停顿 1.5 秒）
        if self._proactive_on and self._proactive_remaining <= 0:
            self._proactive_remaining = random.randint(1, 5)
            QTimer.singleShot(1500, self._on_pet_proactive)

    def _archive_async(self, clean: str, speaker: str) -> None:
        """后台线程异步执行记忆归档（小模型提炼/防重入库）。"""
        try:
            from utils.async_worker import AsyncWorker
            prompt = getattr(self, "_last_prompt", "") or ""
            worker = AsyncWorker(
                self._memory.archive_after,
                prompt, clean, speaker, self._current_group or None,
            )
            worker.failed.connect(
                lambda msg: logger.warning("记忆归档失败: %s", msg))
            worker.start()
            self._archive_worker = worker
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动记忆归档失败: %s", exc)

    def _log_error(self, msg: str) -> None:
        """将错误记录到 log/error.log（需求：log 文件夹存储报错文件）。"""
        try:
            log_dir = project_root() / "log"
            log_dir.mkdir(exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_dir / "error.log", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:  # noqa: BLE001
            pass

    def _on_error(self, msg: str) -> None:
        self._busy = False
        self._send_btn.setEnabled(True)
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.stop()
        # 记录错误到 log 文件夹（需求 5）
        self._log_error(msg)
        # 防御：若错误发生在气泡创建前（如 _append_bubble 阶段），
        # _current_bubble 可能尚未初始化
        bubble = getattr(self, "_current_bubble", None)
        if bubble is not None:
            bubble.setText(f"输出失败：{msg}")
        # 10 秒后提示恢复，强制开始下一轮对话（需求 4：不弹模态窗打断）
        QTimer.singleShot(10000, lambda: self._on_error_recovered(msg))
        self._bus.reply_error.emit(msg)
        self._cleanup_worker()

    def _on_error_recovered(self, msg: str) -> None:
        """输出失败 10 秒后：提示已恢复，可继续下一轮对话。"""
        bubble = getattr(self, "_current_bubble", None)
        if bubble is not None:
            bubble.setText(f"输出失败：{msg}\n（10 秒已过，已恢复，可以继续对话）")

    def _cleanup_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            # 子线程可能仍在执行 LLM 调用，避免长时间阻塞主线程
            self._worker_thread.wait(500)
        self._worker_thread = None
        self._chat_worker = None
        # 生图后台线程兜底退出（避免窗口关闭时 QThread 仍运行警告）
        img_worker = getattr(self, "_image_worker", None)
        if img_worker is not None:
            try:
                img_worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._image_worker = None

    def _scroll_to_bottom(self, force: bool = False) -> None:
        """自动跟随最新对话：立即滚动到底，并在布局完成后再次修正。

        需求：角色开始输出时聊天区就应滚到最下面（而不是输出完才滚动）。
        先同步 adjustSize + 滚到底（立即反馈），再在事件循环末尾延迟一次
        修正（气泡刚插入时布局尚未重算，max 会在下次布局后才更新）。
        调用频率已被 _on_stream 的 40ms 渲染节流控制，不会频繁重排卡顿。
        """
        bar = self._chat_scroll.verticalScrollBar()
        try:
            self._chat_container.adjustSize()
        except Exception:  # noqa: BLE001
            pass
        bar.setValue(bar.maximum())
        if not getattr(self, "_scroll_pending", False):
            self._scroll_pending = True
            QTimer.singleShot(0, self._flush_scroll)

    def _flush_scroll(self) -> None:
        """延迟一次 adjustSize + 滚到底（合并同一事件循环内的多次请求）。"""
        self._scroll_pending = False
        try:
            self._chat_container.adjustSize()
        except Exception:  # noqa: BLE001
            pass
        bar = self._chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _display_text(self, text: str) -> str:
        """显示文本时隐藏情绪标签（【】/〖〗/emotion）与 [角色名]/角色名： 前缀。

        需求：单聊/群聊回复正文都不出现 [角色名] 或 角色名： 前缀（姓名由气泡标题展示）。
        """
        t = self._roles.strip_emotion_tags(text)
        if self._current_group:
            t = ChatWorker._strip_group_prefix(t, self._current_members())
        else:
            t = ChatWorker._strip_role_prefix(t, self._current_role)
        return t

    def _strip_skill_display(self, text: str) -> str:
        """用户气泡显示时剥离 \\@触发词 指令标签与 \\closed 指令（仅用于显示）。

        消息记录保留完整内容；例如
        `\\@thinking \\@network \\@research 帮我查一下` → `帮我查一下`。

        注意：不能对 `\\@[触发词字符]+` 做贪婪正则删除——标签后紧跟的
        中文正文会连标签一起被吞掉（如 `\\@translate你好` → 空）。必须按
        已注册技能精确匹配标签本身，只删标签、保留正文。
        """
        t = text or ""
        if t:
            # 精确剥离已注册的 \\@触发词 标签：对每个 \\@ 命中从最长到最短截断，
            # 取能解析为已注册技能的最长前缀（与 skill_manager.parse_tags 一致），
            # 只删标签本身、保留紧随其后的正文。
            for m in re.finditer(rf"\\@{_TAG_TOKEN}", t):
                full = m.group(0)
                token = full[2:]
                matched = None
                for end in range(len(token), 0, -1):
                    cand = self._skill_mgr.resolve(token[:end])
                    if cand is not None:
                        matched = cand
                        break
                if matched is not None:
                    t = t.replace(full[:2 + end], "", 1)
        t = _CLOSED_CMD_RE.sub(" ", t)
        return re.sub(r"\s{2,}", " ", t).strip()


    # ------------------------------------------------------------ 气泡渲染
    def _append_bubble(self, name: str, text: str, is_user: bool = False,
                       is_stream: bool = False, role: Optional[str] = None,
                       msg: Optional[Dict[str, Any]] = None) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        avatar: Optional[QLabel] = None
        if is_user:
            # 用户消息：右侧对齐——col 拉伸填满整行（气泡随行宽扩展至 max_w，
            # 与角色气泡一样填充满整行再换行），头像固定在最右侧。
            pass
        else:
            avatar = QLabel()
            avatar.setFixedSize(64, 64)
            avatar.setPixmap(_load_pixmap(
                self._avatar_path(role or self._current_role), 64, 64,
                circular=True))
            row.addWidget(avatar, 0, Qt.AlignTop)

        bubble = QLabel()
        bubble.setObjectName("bubbleUser" if is_user else "bubbleAI")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 需求：强制重新应用 QSS，保证气泡始终带白色底图（避免偶发“只有文字无底图”）
        _st = QApplication.style()
        if _st is not None:
            _st.unpolish(bubble)
            _st.polish(bubble)
        # 宽度随聊天区宽度自适应（⑦ 全屏/缩放不裁剪；需求 1/2：封顶 700×scale 等比）
        max_w = self._bubble_max_width()
        bubble.setMaximumWidth(max_w)
        # 用户/角色气泡统一最小宽度：避免小窗口时气泡强制等宽（=最大宽）导致
        # 头像+气泡总宽超出视口、左右内容显示不全（需求：角色在左/用户在右完整显示）
        bubble.setMinimumWidth(min(240, max(160, max_w - 180)))
        bubble.setMargin(10)
        # 气泡样式统一由 root_qss 的 QLabel#bubbleUser / QLabel#bubbleAI 管理，
        # 窗口缩放时字体随 scale 自动调整，避免非全屏文字显示不全
        bubble.setText(text)
        bubble.setProperty("raw", text)

        col = QVBoxLayout()
        col.setSpacing(2)
        name_label = QLabel(name)
        name_label.setObjectName("bubbleName")
        if is_user:
            name_label.setAlignment(Qt.AlignRight)
            # 用户消息：名字右对齐；气泡不设右对齐（fill），随 col 宽度扩展
            # 到 max_w，与角色气泡一样填满整行再换行（需求 v4-1）
            col.addWidget(name_label, 0, Qt.AlignRight)
            col.addWidget(bubble, 0)
        else:
            col.addWidget(name_label)
            col.addWidget(bubble)
        col.setAlignment(Qt.AlignTop)
        # PySide6 的 QBoxLayout.addLayout() 不支持 alignment 参数，
        # 必须通过 setAlignment() 单独设置（否则发送消息即崩溃）
        if is_user:
            # 需求：用户气泡与角色气泡同宽——col 以 stretch=1 填满整行
            row.addLayout(col, 1)
            avatar = QLabel()
            avatar.setFixedSize(64, 64)
            avatar.setPixmap(_load_pixmap(
                str(self._cfg.user_avatar), 64, 64, circular=True))
            row.addWidget(avatar, 0, Qt.AlignTop)
        else:
            row.addLayout(col, 0)
            row.setAlignment(col, Qt.AlignTop)

        wrap = QWidget()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 2, 0, 2)
        wrap_layout.setSpacing(0)
        wrap_layout.addLayout(row)
        # 需求：每条消息下方一个删除按钮（img/del.png），点击删除这句话
        del_row = QHBoxLayout()
        del_row.setSpacing(0)
        del_btn = QPushButton()
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFlat(True)
        del_btn.setFixedSize(24, 24)
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setIcon(QIcon(str(project_root() / "img" / "del.png")))
        del_btn.setToolTip("删除这句话")
        del_btn.setEnabled(not is_stream)
        del_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:12px;}"
            "QPushButton:disabled{background:transparent;}")
        if is_user:
            del_row.addStretch(1)
        del_row.addWidget(del_btn)
        if not is_user:
            del_row.addStretch(1)
        wrap_layout.addLayout(del_row)
        # clicked 信号会传一个 bool，用 _ 接收避免覆盖 wrap 默认值
        del_btn.clicked.connect(lambda _=False, w=wrap: self._delete_message_widget(w))

        self._chat_layout.insertWidget(self._chat_layout.count() - 1, wrap)
        # 保存头像/名字引用：群聊发言者确定后按角色刷新（头像/名字一一对应）
        bubble._avatar_label = avatar
        bubble._name_label = name_label
        bubble._wrap = wrap
        wrap._bubble = bubble
        wrap._del_btn = del_btn
        wrap._msg = msg
        # HTML5 弹出动画：淡入 + 上滑
        if not is_stream:
            wrap.setGraphicsEffect(None)
            anim = animate(wrap, b"windowOpacity", 0.0, 1.0, 300)
            anim.start()
        self._scroll_to_bottom()
        self._prune_chat_bubbles()
        return bubble

    def _append_attachment_links(self, bubble: QLabel,
                                 paths: List[str]) -> None:
        """在用户气泡的文字下方追加已发送文件的超链接（点击用系统默认程序打开）。

        需求：主界面对话中插入文件并发送后，发送的文字下方应有对应文件的超链接。
        附件路径同时写入消息记录（attachments 字段），历史会话回放时同样恢复。
        """
        wrap = getattr(bubble, "_wrap", None)
        if wrap is None or not paths:
            return
        wrap_layout = wrap.layout()
        if wrap_layout is None:
            return
        # 需求：上传文件先在一横行显示，宽度不足时自动另起一行（流式布局）
        flow = _FlowLayout(margin=0, spacing=8)
        link_max_w = max(self._bubble_max_width() - 20, 120)
        for path in paths:
            if not path:
                continue
            p = Path(str(path))
            href = "file:///" + str(p).replace("\\", "/")
            # 需求 1：文件名 ≤15 字（不含后缀），超长省略
            link = QLabel(
                f'<a style="color:{self._accent}; text-decoration:none;" '
                f'href="{href}">{_img_html("file.png")}'
                f'{_short_filename(str(p))}</a>')
            link.setToolTip(str(p))
            link.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
            link.setCursor(Qt.PointingHandCursor)
            link.setMaximumWidth(link_max_w)
            link.setStyleSheet("font-size:12px; background:transparent;")
            link.linkActivated.connect(
                lambda _u, pth=path: self._open_attachment(pth))
            flow.addWidget(link)
        # 插入到气泡行之后（删除按钮行之前），右对齐显示
        wrap_layout.insertLayout(1, flow)
        flow.setAlignment(Qt.AlignRight)

    def _open_attachment(self, path: str) -> None:
        """用系统默认程序打开附件文件。"""
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开附件失败 %s: %s", path, exc)

    def _prune_chat_bubbles(self) -> None:
        """长对话性能：聊天区仅保留最近 _MAX_BUBBLES 条气泡。

        仅移除最旧的界面控件（数据仍在 self._messages，存档/记忆不受影响），
        避免长对话累积上千个 QWidget 导致布局/重绘卡死（需求 v3-1）。
        """
        while self._chat_layout.count() > _MAX_BUBBLES + 1:
            item = self._chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _append_notice(self, text: str) -> None:
        """聊天区追加一条居中的灰色系统提示（不写入消息记录/存档）。

        用于 \\closed 等纯指令的即时反馈，不调用 LLM。
        """
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(
            "color:#9aa0ac;font-size:12px;padding:6px 2px;"
            "background:rgba(108,142,245,0.06);border-radius:8px;")
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.addWidget(label)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, wrap)
        self._scroll_to_bottom()

    def _clear_chat_area(self) -> None:
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._current_bubble = None

    def _delete_message_widget(self, wrap: QWidget) -> None:
        """删除一条对话：移除气泡 + 从内存消息列表移除 + 重新保存会话。

        需求：每条消息下方的 del.png 删除按钮，用于删除这句话。
        """
        idx = self._chat_layout.indexOf(wrap)
        if idx >= 0:
            item = self._chat_layout.takeAt(idx)
            if item.widget() is not None:
                item.widget().deleteLater()
        msg = getattr(wrap, "_msg", None)
        if msg is not None and msg in self._messages:
            self._messages.remove(msg)
        if self._current_bubble is not None and \
                getattr(self._current_bubble, "_wrap", None) is wrap:
            self._current_bubble = None
        if not self._messages and self._session_path is not None:
            # 全部消息删除后清理会话存档，避免残留旧内容
            try:
                if self._session_path.exists():
                    self._session_path.unlink()
                self._session_path = None
                self._session_title.setText("新会话")
                self._refresh_history_list()
                return
            except Exception:  # noqa: BLE001
                pass
        self._save_session(quiet=True)

    # ------------------------------------------------------------ 会话管理
    def _session_key(self) -> str:
        """当前会话键（用于存档文件名）。

        群聊组名/角色名可能含 Windows 文件名非法字符（: / \\ 等），统一替换，
        否则 group 会话存档会写入失败并弹出「保存失败」提示。
        """
        key = f"group:{self._current_group}" if self._current_group else self._current_role
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", key or "")

    def _new_session(self, quiet: bool = False) -> None:
        self._save_session(quiet=True)
        self._messages = []
        self._session_path = None
        # 切换会话时清空待上传附件（Gemini 风格：附件只属于当前输入）
        if hasattr(self, "_attach_bar"):
            self._clear_pending_attachments()
        # 当前会话已不存在 → 清除历史列表中的主题色框选
        if hasattr(self, "_history_list"):
            self._highlight_current_history()
        self._prev_speaker = ""
        self._clear_chat_area()
        self._session_title.setText("新会话")
        # 新会话复位专业模式 / \closed 状态（立绘恢复普通情绪；需求 v3-2/3/4）
        self._skill_closed = False
        self._pro_skill_active = False
        self._last_portrait_key = None
        self._last_stream_render = 0.0

    def _save_session(self, quiet: bool = False) -> None:
        if not self._messages:
            return
        now = int(time.time())
        if self._session_path is None:
            self._session_path = self._cfg.conversations_dir / (
                f"session_{self._session_key()}_{now}.json")
        data = {
            "role": self._current_role,
            "group": self._current_group,
            "updated_at": now,
            "messages": self._messages,
        }
        # 保留已有标题（自动生成或手动修改的 title 字段，避免被覆盖丢失）
        if self._session_path.exists():
            try:
                old = json.loads(self._session_path.read_text(encoding="utf-8"))
                if old.get("title"):
                    data["title"] = old["title"]
            except Exception:  # noqa: BLE001
                pass
        try:
            self._cfg.conversations_dir.mkdir(parents=True, exist_ok=True)
            self._session_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._bus.conversation_saved.emit(str(self._session_path))
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                styled_warning(self, "保存失败", str(exc))
            return
        # 第一轮对话后自动生成「角色名-总结」标题（调用 API，异步执行，需求 5）
        if not data.get("title") and len(self._messages) >= 2:
            user_msg = next(
                (m.get("content", "") for m in self._messages
                 if m.get("role") == "user"), "")
            reply = next(
                (m.get("content", "") for m in self._messages
                 if m.get("role") == "assistant"), "")
            if user_msg and reply:
                self._generate_session_title_async(
                    str(self._session_path), self._current_role,
                    user_msg, reply)
        # 需求 3：第一轮对话后自动刷新历史会话列表
        if hasattr(self, "_history_list"):
            self._refresh_history_list()

    # ------------------------------------------------------------ 历史标题
    def _generate_session_title_async(self, path: str, role: str,
                                      user_prompt: str, reply: str) -> None:
        """异步调用 LLM 生成历史会话标题（角色名-第一轮对话总结）。"""
        sys_p = self._roles.role_system_prompt(role)
        messages = [
            {"role": "system",
             "content": (
                 f"{sys_p}\n请根据以下第一轮对话，生成一个简洁的历史会话标题。"
                 "格式必须为：角色名-一句话总结（例如：Rainbow_Dash-用一句话打招呼）。"
                 "标题不超过 20 字，不要输出【】情绪标签，不要引号。")},
            {"role": "user",
             "content": f"用户：{user_prompt}\n{role}：{reply}"},
        ]
        worker = LLMWorker(messages, kind="main", stream=False, max_tokens=50)
        worker.request_finished.connect(
            lambda t: self._on_title_generated(path, role, t))
        worker.request_error.connect(lambda _e: None)
        self._title_worker = worker
        worker.start()

    def _on_title_generated(self, path: str, role: str, title: str) -> None:
        """标题生成回调：清理并写入 session json，刷新历史列表。"""
        title = self._roles.strip_emotion_tags(title or "")
        title = re.sub(r"[\s\"'【】]+", "", title).strip()
        title = title[:40]
        if not title:
            title = "新会话"
        if not title.startswith(f"{role}-"):
            title = f"{role}-{title}"
        try:
            p = Path(path)
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            data["title"] = title
            p.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self._refresh_history_list()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_history_list(self) -> None:
        # 保留滚动位置：刷新/重建后不自动跳回顶部（需求：右侧标签栏保持原位）
        vbar = self._history_list.verticalScrollBar()
        prev_scroll = vbar.value() if vbar is not None else 0
        # 清空容器：逐项移除所有条目 widget / 底部留白 stretch（QScrollArea 无 clear()）
        while self._history_layout.count() > 0:
            item = self._history_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._history_layout.addStretch(1)   # 重新添加底部留白
        conv_dir = self._cfg.conversations_dir
        if not conv_dir.is_dir():
            return
        # 按对话先后排序：新的（最后更新）在最上面，老的在下面。
        # 不能按文件名排序——文件名前缀含角色/群组名，字符串排序会把同一角色
        # 的会话聚在一起而不是按时间先后；每个 session json 的 updated_at
        # 才是真实的对话先后（最后保存/活跃时间）。
        sessions = []
        for path in conv_dir.glob("session_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            ts = data.get("updated_at")
            if not isinstance(ts, (int, float)):
                m = re.search(r"_(\d+)\.json$", path.name)
                ts = int(m.group(1)) if m else 0
            sessions.append((int(ts), path, data))
        sessions.sort(key=lambda s: s[0], reverse=True)   # 新的在上，老的在下面
        # 第一行剥离「角色名-」前缀用的候选：所有角色目录名/显示名 + 连字符。
        # 自动生成标题格式固定为「角色名-总结」，但历史数据可能出现 role 字段与
        # 标题前缀不一致，因此按全部已知角色匹配，而不是只看当前记录的 role。
        role_prefixes: List[str] = []
        for _r in self._roles.list_roles():
            for _n in (_r, self._roles.display_name(_r)):
                if _n and f"{_n}-" not in role_prefixes:
                    role_prefixes.append(f"{_n}-")
        role_prefixes.sort(key=len, reverse=True)   # 长名优先，避免前缀互相截断
        for _ts, path, data in sessions:
            try:
                role = data.get("role") or ""
                group = data.get("group") or ""
                # 第一行：第一次对话的浓缩标题。需求：第一行不显示角色名 ——
                # 剥离「角色名-」前缀只保留总结；无标题时回退 群组名
                # （角色名已由第二行显示，不重复出现）。
                title = data.get("title") or ""
                for _p in role_prefixes:
                    if title.startswith(_p):
                        title = title[len(_p):]
                        break
                if not title:
                    title = group or ""
                if not title:
                    title = "会话"
                title = self._roles.strip_emotion_tags(title)
                # 第二行：对话名字/群组 + 对话条数
                count = len(data.get("messages") or [])
                name = group if group else (
                    self._roles.display_name(role) if role else "会话")
                info = f"{name} · 共{count}条"
                row = _HistoryRow(str(path), title, info)
                row.clicked.connect(self._on_history_clicked)
                row.delete_requested.connect(self._delete_history_path)
                row.context_menu_requested.connect(self._show_history_menu)
                # 插入到布局末尾（stretch 之前），条目从顶部排起
                self._history_layout.insertWidget(
                    self._history_layout.count() - 1, row)
            except Exception:  # noqa: BLE001
                continue
        # 用主题色框选当前正在查看的对话
        self._highlight_current_history()
        # 重建后恢复滚动位置（不自动跳回顶部）
        if vbar is not None and vbar.maximum() > 0:
            vbar.setValue(min(prev_scroll, vbar.maximum()))

    def _highlight_current_history(self) -> None:
        """用主题色框选当前正在查看的会话，其余项清除高亮。

        新结构：历史条目为 QScrollArea 容器中逐条添加的 _HistoryRow，
        通过遍历 _history_layout 定位（跳过底部留白 stretch）。
        """
        for i in range(self._history_layout.count()):
            item = self._history_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if not isinstance(w, _HistoryRow):
                continue
            p = w.path
            is_current = (
                self._session_path is not None and p
                and Path(str(p)).resolve() == self._session_path.resolve())
            if is_current:
                w.setStyleSheet(
                    f"QWidget#historyRow {{ border: 2px solid {self._accent};"
                    f" border-radius: 8px;"
                    f" background: {_hex_to_rgba(self._accent, 0.12)}; }}")
            else:
                w.setStyleSheet("")

    def _delete_history_path(self, path: str) -> None:
        """删除指定历史会话文件并刷新列表（⑤）。"""
        try:
            Path(path).unlink()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_history_list()

    def _on_history_clicked(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "加载失败", str(exc))
            return
        self._new_session(quiet=True)
        self._session_path = Path(path)
        # 用主题色框选当前正在查看的对话
        self._highlight_current_history()
        self._messages = list(data.get("messages") or [])
        # 历史标题同样走缩写/截断，避免长角色名或超长总结标题撑开布局
        hist_role = data.get("role") or ""
        hist_title = (data.get("title")
                      or data.get("group")
                      or (self._roles.sidebar_label(hist_role) if hist_role else "")
                      or "会话")
        self._session_title.setText(
            f"{self._roles.truncate_wide(hist_title, 24)}（历史）")
        self._clear_chat_area()
        for msg in self._messages:
            is_user = msg.get("role") == "user"
            content = msg.get("content") or ""
            msg_name = msg.get("name") or ("用户" if is_user else self._current_role)
            msg_role = None
            if not is_user and msg_name in self._roles.list_roles():
                # 群聊/多角色历史：名字显示角色显示名，头像使用该角色立绘（需求 3）
                msg_role = msg_name
                msg_name = self._roles.display_name(msg_name)
            # 历史消息统一隐藏【】情绪标签（只显示对话正文）
            if not is_user:
                content = self._roles.strip_emotion_tags(content)
                # 需求：历史群聊正文不显示 [角色名]: / 角色名： 前缀
                content = ChatWorker._strip_group_prefix(
                    content, self._roles.list_roles())
            else:
                # 需求：历史用户消息显示剥离 \@触发词 指令（记录保留完整内容）
                content = self._strip_skill_display(content)
            bubble = self._append_bubble(msg_name, content, is_user=is_user,
                                         role=msg_role, msg=msg)
            # 历史会话：用户消息下方恢复已发送文件的超链接
            if is_user and bubble is not None and msg.get("attachments"):
                self._append_attachment_links(bubble, msg.get("attachments"))
        # 群聊历史：桌宠跟随所加载会话的最后发言角色
        if self._current_group:
            members = self._current_members()
            for msg in reversed(self._messages):
                if msg.get("role") == "assistant" and msg.get("name") in members:
                    self._bus.speaker_switched.emit(msg["name"])
                    break


    # ------------------------------------------------------------ 设置面板
    def _open_settings(self) -> None:
        """设置面板：单页、框+左对齐标题分类，与主界面 HTML5 风格完全一致。

        分类：对话 API / 多模态文档 / 资源路径 /
        外观（主题背景 + 软件颜色 + 模糊）/ 桌面形象 / 记忆（训练+遗忘）。
        """
        # 需求：设置窗口改为非模态（取消模态强制置顶），
        # 已打开时前置到最上面而非重复创建；技能工具等子窗口可正常弹出。
        if getattr(self, "_settings_dialog", None) is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        # 尺寸随缩放系数放大，但限制不超过屏幕可用区域（全屏放大时整体适配，
        # 右侧滚动条始终可用，不会因超出屏幕而看不到）
        _dlg_w = int(640 * self._scale)
        _dlg_h = int(660 * self._scale)
        _screen = QApplication.primaryScreen()
        if _screen is not None:
            _avail = _screen.availableGeometry()
            _dlg_w = min(_dlg_w, int(_avail.width() * 0.92))
            _dlg_h = min(_dlg_h, int(_avail.height() * 0.92))
        dialog.resize(max(_dlg_w, 520), max(_dlg_h, 420))
        dialog.setStyleSheet(root_qss(self._accent, self._scale,
                                      self._transparent_chat))

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 右侧滚动条（设置内容较长时始终显示滑块）
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 需求：设置界面默认不出现左右滑块（输入框随容器缩放，不撑出横向滚动条）
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("root")
        # 需求：水平方向忽略 sizeHint——容器宽度始终等于视口宽，不撑出左右滑块
        container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body = QVBoxLayout(container)
        body.setContentsMargins(8, 4, 4, 4)
        body.setSpacing(8)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        def _section(title: str) -> "QFormLayout":
            """左对齐标题独立一行 + 下方卡片（标题不压住框）。"""
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            body.addWidget(title_label)
            box = QFrame()
            box.setObjectName("settingsCard")
            form = QFormLayout(box)
            form.setContentsMargins(14, 14, 14, 14)
            form.setSpacing(8)
            # 需求：输入框随容器宽度伸缩（避免表单过宽撑出左右滑块）
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            body.addWidget(box)
            return form

        # ---------- 1. 对话 API ----------
        api_form = _section("对话 API（OpenAI / DeepSeek / Gemini / Ollama / 自定义）")
        provider = QComboBox()
        provider.addItems(["openai", "deepseek", "gemini", "ollama", "custom"])
        provider.setCurrentText(self._cfg.get("api", "provider", default="openai") or "openai")
        # 选择服务商后自动填充对应 API 地址与模型名（⑤）
        _PRESETS = {
            "openai": ("https://api.openai.com/v1", "gpt-4o", "gpt-4o-mini"),
            "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "deepseek-chat"),
            "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
                       "gemini-1.5-flash", "gemini-1.5-flash"),
            "ollama": ("http://localhost:11434/v1", "llama3.1", "llama3.1"),
        }

        def _on_provider(p: str) -> None:
            preset = _PRESETS.get(p)
            if preset:
                api_base.setText(preset[0])
                main_model.setText(preset[1])
                small_model.setText(preset[2])

        provider.currentTextChanged.connect(_on_provider)
        api_base = QLineEdit(self._cfg.get("api", "api_base", default=""))
        api_key = QLineEdit(self._cfg.get("api", "api_key", default=""))
        api_key.setEchoMode(QLineEdit.Password)
        main_model = QLineEdit(self._cfg.get("api", "main_model", default="gpt-4o"))
        small_model = QLineEdit(self._cfg.get("api", "small_model", default="gpt-4o-mini"))
        temp_box = QSpinBox()
        temp_box.setRange(0, 100)
        temp_box.setValue(int(float(self._cfg.get("chat", "temperature", default=0.7)) * 100))
        tokens_box = QSpinBox()
        tokens_box.setRange(64, 16384)
        tokens_box.setValue(int(self._cfg.get("chat", "max_tokens", default=2048)))
        api_form.addRow("服务商", provider)
        api_form.addRow("主模型 API 地址", api_base)
        api_form.addRow("主模型 API Key", api_key)
        api_form.addRow("主模型", main_model)
        small_base_box = QLineEdit(self._cfg.small_base())
        small_key_box = QLineEdit(self._cfg.small_key())
        small_key_box.setEchoMode(QLineEdit.Password)
        api_form.addRow("小模型 API 地址（留空复用主 API）", small_base_box)
        api_form.addRow("小模型 API Key", small_key_box)
        api_form.addRow("小模型(记忆)", small_model)
        api_form.addRow("温度(x100)", temp_box)
        api_form.addRow("最大Tokens", tokens_box)
        # 需求：思维链显示开关——模型返回 reasoning_content 或使用 \@thinking 时展示。
        # 需求：长文本不裁剪，超行自动向下换行（QCheckBox 无 wordWrap，用短文本+提示标签）
        thinking_check = QCheckBox("显示思维链")
        thinking_check.setChecked(self._cfg.show_thinking())
        thinking_hint = QLabel("模型返回 reasoning_content 或 \\@thinking 技能时展示思考过程")
        thinking_hint.setWordWrap(True)
        thinking_hint.setObjectName("statusText")
        thinking_col = QVBoxLayout()
        thinking_col.setSpacing(2)
        thinking_col.addWidget(thinking_check)
        thinking_col.addWidget(thinking_hint)
        api_form.addRow("思维链显示", thinking_col)

        # ---------- 2. 多模态文档 ----------
        vis_form = _section("多模态（图片 / PDF / Word / Excel / PPT / 文本）")
        vision_model = QLineEdit(self._cfg.get("api", "vision_model", default="gpt-4o"))
        vision_base = QLineEdit(self._cfg.get("api", "vision_base", default=""))
        vision_key = QLineEdit(self._cfg.get("api", "vision_key", default=""))
        vision_key.setEchoMode(QLineEdit.Password)
        vis_form.addRow("视觉模型", vision_model)
        vis_form.addRow("API 地址（留空复用主 API）", vision_base)
        vis_form.addRow("API Key", vision_key)

        # ---------- 2.5 生图 API（需求 v5：样式同多模态）+ 保存路径 ----------
        img_form = _section("生图 API（\\@image / 提示词生图 / 模型输出图片）")
        image_model = QLineEdit(self._cfg.get("api", "image_model", default=""))
        image_base = QLineEdit(self._cfg.get("api", "image_base", default=""))
        image_key = QLineEdit(self._cfg.get("api", "image_key", default=""))
        image_key.setEchoMode(QLineEdit.Password)
        img_form.addRow("生图模型", image_model)
        img_form.addRow("API 地址（留空复用主 API）", image_base)
        img_form.addRow("API Key", image_key)
        # 需求：API 生成图片保存路径选择（默认 <项目>/createimage）
        image_dir_box = QLineEdit(str(self._cfg.image_dir()))
        image_dir_browse = QPushButton("浏览")
        image_dir_browse.setStyleSheet(
            ghost_btn_qss(font_size=12, padding="4px 12px"))
        image_dir_browse.setCursor(Qt.PointingHandCursor)

        def _pick_image_dir() -> None:
            from PySide6.QtWidgets import QFileDialog as _FD
            d = _FD.getExistingDirectory(
                dialog, "选择图片保存目录", image_dir_box.text())
            if d:
                image_dir_box.setText(d)

        image_dir_browse.clicked.connect(_pick_image_dir)
        image_dir_row = QWidget()
        _il = QHBoxLayout(image_dir_row)
        _il.setContentsMargins(0, 0, 0, 0)
        _il.setSpacing(6)
        _il.addWidget(image_dir_box, 1)
        _il.addWidget(image_dir_browse)
        img_form.addRow("图片保存目录（默认 ./createimage）", image_dir_row)

        # ---------- 2.6 联网搜索（需求：允许联网开关 + 搜索引擎 API） ----------
        web_form = _section("联网搜索（允许联网开关 + 搜索引擎 API）")
        web_enabled = QCheckBox("允许联网（开启后 \\@network 联网搜索技能可调用搜索引擎）")
        web_enabled.setChecked(self._cfg.web_enabled())
        web_form.addRow("允许联网", web_enabled)
        search_base = QLineEdit(self._cfg.web_search_base())
        search_base.setPlaceholderText(
            "搜索引擎 API 地址，例如 https://api.search.brave.com/res/v1/web/search，"
            "或含 {q} 的模板 URL")
        search_key = QLineEdit(self._cfg.web_search_key())
        search_key.setEchoMode(QLineEdit.Password)
        web_form.addRow("搜索引擎 API 地址", search_base)
        web_form.addRow("搜索引擎 API Key", search_key)

        # ---------- 3. 资源路径 + 头像 ----------
        path_form = _section("资源路径")
        path_boxes: Dict[str, QLineEdit] = {}
        for key, label in (("roles", "角色库"),
                           ("roles_img", "角色图"),
                           ("roles_desktop", "桌面形象"),
                           ("history", "记忆存储"),
                           ("data", "数据目录")):
            box = QLineEdit(self._cfg.get("paths", key, default=f"./{key}"))
            path_boxes[key] = box
            path_form.addRow(label, box)
        avatar_box = QLineEdit(str(self._cfg.user_avatar))
        avatar_box.setPlaceholderText("用户头像图片路径（png/jpg/jpeg/webp/gif/bmp）")
        # 头像预览
        avatar_preview = QLabel()
        avatar_preview.setFixedSize(64, 64)
        avatar_preview.setAlignment(Qt.AlignCenter)
        avatar_preview.setPixmap(_load_pixmap(
            str(self._cfg.user_avatar), 64, 64, circular=True))
        avatar_browse = QPushButton("浏览")
        avatar_browse.setStyleSheet(ghost_btn_qss(font_size=12, padding="4px 12px"))
        avatar_browse.setCursor(Qt.PointingHandCursor)

        def _pick_avatar() -> None:
            start = (str(self._cfg.user_avatar.parent)
                     if self._cfg.user_avatar.parent.exists() else "")
            p, _ = QFileDialog.getOpenFileName(
                dialog, "选择用户头像", start,
                "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
            if p:
                avatar_box.setText(p)
                avatar_preview.setPixmap(
                    _load_pixmap(p, 64, 64, circular=True))

        avatar_browse.clicked.connect(_pick_avatar)
        avatar_row = QWidget()
        avatar_lay = QHBoxLayout(avatar_row)
        avatar_lay.setContentsMargins(0, 0, 0, 0)
        avatar_lay.setSpacing(6)
        avatar_lay.addWidget(avatar_preview)
        avatar_lay.addWidget(avatar_box, 1)
        avatar_lay.addWidget(avatar_browse)
        path_form.addRow("用户头像", avatar_row)

        user_name_box = QLineEdit(self._cfg.user_name())
        user_name_box.setPlaceholderText("聊天中用户显示的名字")
        path_form.addRow("用户昵称", user_name_box)

        # ---------- 4.5 项目目录（母目录） ----------
        proj_form = _section("项目目录（母目录）")
        proj_path = QLabel(str(project_root()))
        proj_path.setWordWrap(True)
        proj_path.setObjectName("projPath")
        proj_form.addRow("当前目录", proj_path)
        move_btn = QPushButton("移动项目到新目录并运行")
        move_btn.setObjectName("ghostBtn")
        move_btn.clicked.connect(self._move_project)
        proj_form.addRow(move_btn)

        # ---------- 5. 外观：主题背景 + 软件颜色 + 图标 + 模糊 + 透明聊天 ----------
        ui_form = _section("外观（主题背景 / 软件颜色 / 应用图标 / 模糊 / 透明聊天窗口）")
        blur_check = QCheckBox("背景模糊特效")
        blur_check.setChecked(self._blur_bg)
        ui_form.addRow("背景模糊", blur_check)

        transparent_check = QCheckBox("透明聊天窗口（除按钮和 PNG 图外全部半透明）")
        transparent_check.setChecked(self._transparent_chat)
        ui_form.addRow("透明聊天窗口", transparent_check)

        icon_box = QLineEdit(self._cfg.get("ui", "icon_path", default="") or "")
        icon_box.setPlaceholderText("例如 ./data/app.ico（留空使用默认图标）")
        icon_browse = QPushButton("浏览")
        icon_browse.setStyleSheet(ghost_btn_qss(font_size=12, padding="4px 12px"))
        icon_browse.setCursor(Qt.PointingHandCursor)

        def _pick_icon() -> None:
            p, _ = QFileDialog.getOpenFileName(
                dialog, "选择应用图标(ICO)", "",
                "图标 (*.ico);;图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
            if p:
                icon_box.setText(p)

        icon_browse.clicked.connect(_pick_icon)
        icon_row = QWidget()
        icon_lay = QHBoxLayout(icon_row)
        icon_lay.setContentsMargins(0, 0, 0, 0)
        icon_lay.setSpacing(6)
        icon_lay.addWidget(icon_box, 1)
        icon_lay.addWidget(icon_browse)
        ui_form.addRow("任务栏/应用图标(ICO)", icon_row)

        font_combo = QComboBox()
        font_combo.addItems(["Microsoft YaHei", "微软雅黑", "SimHei", "SimSun",
                             "PingFang SC", "（系统默认）"])
        cur_font = self._cfg.font_family()
        idx = font_combo.findText(cur_font)
        font_combo.setCurrentIndex(idx if idx >= 0 else font_combo.count() - 1)
        ui_form.addRow("界面字体", font_combo)

        # 需求：主界面聊天字体大小调节（气泡与输入框，默认 14px）
        chat_font_box = QSpinBox()
        chat_font_box.setRange(12, 28)
        chat_font_box.setValue(int(self._cfg.get(
            "ui", "chat_font_size", default=14) or 14))
        chat_font_box.setSuffix(" px")
        chat_font_box.setToolTip("主界面聊天气泡与输入框的字体大小（12-28px）")
        ui_form.addRow("聊天字体大小", chat_font_box)

        # 需求：主题背景从「全部平铺按钮」改为「下拉菜单选择 +
        #       下一行左侧垂直居中的缩略图预览」（不遮挡表单，下拉即生效）
        theme_combo = QComboBox()
        theme_preview = QLabel()
        theme_preview.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _THUMB_MAX_W, _THUMB_MAX_H = 560, 150   # 缩略图最大尺寸（保持宽高比）
        preview_host = QWidget()
        preview_host.setObjectName("themePreviewHost")
        preview_host.setFixedHeight(_THUMB_MAX_H + 30)
        preview_host.setStyleSheet(
            "QWidget#themePreviewHost{"
            "background:rgba(255,255,255,0.55);"
            "border:1px dashed #c4cedd; border-radius:10px;}")
        ph_lay = QHBoxLayout(preview_host)
        ph_lay.setContentsMargins(10, 6, 10, 6)
        ph_lay.addWidget(theme_preview, 0, Qt.AlignLeft | Qt.AlignVCenter)
        ph_lay.addStretch(1)
        ui_form.addRow("主题背景", theme_combo)
        ui_form.addRow(preview_host)   # 独占整行：靠左 + 垂直居中预览

        candidates = self._theme_candidates()
        saved_theme = self._cfg.get("ui", "theme_file", default="") or ""

        def _fill_theme_preview(path: str) -> None:
            pm = QPixmap(path)
            if pm.isNull():
                theme_preview.setText("无法加载预览，请检查图片文件。")
                theme_preview.setFixedSize(_THUMB_MAX_W,
                                           min(_THUMB_MAX_H, theme_preview.height()))
                return
            thumb = pm.scaled(_THUMB_MAX_W, _THUMB_MAX_H,
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
            theme_preview.setText("")
            theme_preview.setFixedSize(thumb.size())
            theme_preview.setPixmap(thumb)

        if not candidates:
            theme_combo.setEnabled(False)
            theme_preview.setText("theme/ 目录为空，可将图片放入后重新打开设置。")
            theme_preview.setFixedSize(_THUMB_MAX_W, 30)
        else:
            for path in candidates:
                theme_combo.addItem(path.name, str(path))
            cur = theme_combo.findData(saved_theme)
            theme_combo.setCurrentIndex(cur if cur >= 0 else 0)

            def _on_theme_changed(index: int) -> None:
                if 0 <= index < theme_combo.count():
                    path = theme_combo.itemData(index)
                    _fill_theme_preview(path)
                    self._apply_theme(path)

            theme_combo.currentIndexChanged.connect(_on_theme_changed)
            _fill_theme_preview(theme_combo.itemData(theme_combo.currentIndex()))

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        for color in ("#6c8ef5", "#e8862c", "#22c55e", "#e11d48",
                      "#8b5cf6", "#0891b2"):
            sw = QPushButton()
            sw.setFixedSize(30, 30)
            sw.setObjectName("colorSwatch")
            sw.setCheckable(True)
            sw.setStyleSheet(
                f"QPushButton#colorSwatch {{ background: {color}; }}")
            sw.setChecked(color.lower() == self._accent.lower())
            sw.clicked.connect(
                lambda _=False, c=color: self._on_accent_swatch(c))
            swatch_row.addWidget(sw)
        custom_btn = QPushButton("自定义…")
        custom_btn.setObjectName("ghostBtn")
        custom_btn.clicked.connect(self._pick_custom_accent)
        swatch_row.addWidget(custom_btn)
        swatch_row.addStretch(1)
        ui_form.addRow("软件颜色", swatch_row)

        # ---------- 5.5 技能工具（管理器入口，风格跟随主界面主题色） ----------
        skill_form = _section("技能工具（\\@技能 唤醒 / 工具库管理）")
        skilltools_btn = QPushButton("打开技能工具管理器")
        skilltools_btn.setObjectName("ghostBtn")
        skilltools_btn.setCursor(Qt.PointingHandCursor)
        skilltools_btn.clicked.connect(self._open_skilltools)
        skill_form.addRow(skilltools_btn)

        # ---------- 6. 桌面形象 ----------
        pet_form = _section("桌面形象（最顶层置顶桌宠）")
        pet_enabled = QCheckBox("启用桌面宠物")
        pet_enabled.setChecked(bool(self._cfg.get("pet", "enabled", default=False)))
        greet = QSpinBox()
        greet.setRange(5, 240)
        greet.setValue(int(self._cfg.get("pet", "greeting_interval_min", default=30)))
        focus = QCheckBox("专注模式")
        focus.setChecked(bool(self._cfg.get("pet", "focus_mode", default=False)))
        floating = QCheckBox("悬浮对话")
        floating.setChecked(bool(self._cfg.get("pet", "floating_chat", default=False)))
        zoom_check = QCheckBox("桌宠滚轮缩放")
        zoom_check.setChecked(self._cfg.pet_zoom_enabled())
        pet_form.addRow("启用桌宠", pet_enabled)
        pet_form.addRow("问候间隔(分钟)", greet)
        pet_form.addRow("专注模式", focus)
        pet_form.addRow("悬浮对话", floating)
        pet_form.addRow("滚轮缩放", zoom_check)


        # ---------- 7. 记忆：训练 + 遗忘 ----------
        adv_form = _section("记忆（德谬歌矩阵训练 / 遗忘）")
        train_path = QLineEdit()
        train_btn = QPushButton("开始学习")
        train_btn.setStyleSheet(btn_qss(padding="6px 24px"))

        def _train() -> None:
            path = train_path.text().strip()
            if not path:
                styled_warning(dialog, "训练", "请先填写 talk.json 路径")
                return
            count = self._memory.train_from_talk_json(path)
            styled_info(dialog, "德谬歌矩阵",
                                    f"已学习 {count} 条记忆（角色一一对应）")

        train_btn.clicked.connect(_train)
        adv_form.addRow("talk.json 路径", train_path)
        adv_form.addRow(train_btn)

        forget_scope = QComboBox()
        forget_scope.addItem("role")
        forget_scope.addItem("group")
        forget_value = QLineEdit(self._current_role)
        forget_btn = QPushButton("确认遗忘")
        forget_btn.setStyleSheet(btn_qss(padding="6px 24px"))

        def _forget() -> None:
            scope = forget_scope.currentText()
            value = forget_value.text().strip()
            if not value:
                return
            if styled_question(dialog, "遗忘协议",
                               f"确认彻底遗忘 {scope} = {value} 的全部记忆？"
                               "（短期/长期/重要记忆与历史存档）",
                               danger=True):
                removed = self._memory.forget(scope, value)
                styled_info(dialog, "遗忘协议",
                                        f"已清除 {removed} 条记忆")

        forget_btn.clicked.connect(_forget)
        adv_form.addRow("遗忘范围", forget_scope)
        adv_form.addRow("角色/群聊名", forget_value)
        adv_form.addRow(forget_btn)


        # ---------- 遗忘日程：短时记忆 / 历史长期摘要记忆 ----------
        forget_st = QCheckBox("短时记忆")
        forget_st.setChecked(True)
        forget_sum = QCheckBox("历史长期摘要记忆")
        forget_sum.setChecked(False)
        forget_plan_btn = QPushButton("确认遗忘")
        forget_plan_btn.setStyleSheet(btn_qss(padding="6px 24px"))
        def _forget_plan() -> None:
            targets = []
            if forget_st.isChecked():
                targets.append("短时记忆")
            if forget_sum.isChecked():
                targets.append("历史长期摘要记忆")
            if not targets:
                styled_info(dialog, "遗忘日程", "请先勾选要遗忘的记忆类型。")
                return
            if not styled_question(dialog, "遗忘日程",
                                   "确认遗忘：" + "、".join(targets) + "？",
                                   danger=True):
                return
            done = []
            if forget_st.isChecked():
                n = self._memory.clear_short_memory()
                done.append("短时记忆 " + str(n) + " 条")
            if forget_sum.isChecked():
                try:
                    from focus_assistant import clear_focus_records
                    clear_focus_records()
                    done.append("历史长期摘要记忆已清空")
                except Exception as exc:  # noqa: BLE001
                    done.append("历史长期摘要记忆清除失败：" + str(exc))
            styled_info(dialog, "遗忘日程", "已" + "、".join(done))

        forget_plan_btn.clicked.connect(_forget_plan)
        adv_form.addRow("遗忘日程-短时记忆", forget_st)
        adv_form.addRow("遗忘日程-历史长期摘要", forget_sum)
        adv_form.addRow(forget_plan_btn)

        # ---------- 遗忘日记：删除 dailydata/dailytext 下全部日记 ----------
        daily_forget_btn = QPushButton("遗忘日记")
        daily_forget_btn.setStyleSheet(btn_qss(padding="6px 24px"))

        def _forget_daily() -> None:
            try:
                from daily import DAILY_TEXT_DIR, list_diaries
            except Exception as exc:  # noqa: BLE001
                styled_warning(dialog, "遗忘日记", f"日记模块加载失败：{exc}")
                return
            diaries = list_diaries()
            if not diaries:
                styled_info(dialog, "遗忘日记", "当前没有任何日记可遗忘。")
                return
            if not styled_question(
                    dialog, "遗忘日记",
                    f"确认删除全部 {len(diaries)} 篇日记？"
                    "（dailydata/dailytext 下的日记将被彻底清除，不可恢复）",
                    danger=True):
                return
            removed = 0
            for data in diaries:
                try:
                    (DAILY_TEXT_DIR / f"{data.get('id')}.json").unlink(
                        missing_ok=True)
                    removed += 1
                except OSError:
                    pass
            styled_info(
                dialog, "遗忘日记", f"已遗忘 {removed} 篇日记。")

        daily_forget_btn.clicked.connect(_forget_daily)
        adv_form.addRow("遗忘日记-全部日记", daily_forget_btn)

        # ---------- 遗忘登陆时间：短期 / 长期 / 关闭功能 ----------
        lt_forget_short = QPushButton("遗忘短期时间")
        lt_forget_short.setStyleSheet(btn_qss(padding="6px 24px"))
        lt_forget_long = QPushButton("遗忘长期时间")
        lt_forget_long.setStyleSheet(btn_qss(padding="6px 24px"))

        def _forget_lt_short() -> None:
            try:
                from logintime import forget_short
            except Exception as exc:  # noqa: BLE001
                styled_warning(dialog, "遗忘登陆时间",
                                    f"登陆时间模块加载失败：{exc}")
                return
            if not styled_question(
                    dialog, "遗忘登陆时间",
                    "确认遗忘全部短期登陆时间记录？"
                    "（data/logintime.json 将被清空，不可恢复）",
                    danger=True):
                return
            n = forget_short()
            styled_info(dialog, "遗忘登陆时间",
                                    f"已遗忘 {n} 条短期登陆时间记录。")

        def _forget_lt_long() -> None:
            try:
                from logintime import forget_long
            except Exception as exc:  # noqa: BLE001
                styled_warning(dialog, "遗忘登陆时间",
                                    f"登陆时间模块加载失败：{exc}")
                return
            if not styled_question(
                    dialog, "遗忘登陆时间",
                    "确认遗忘全部长期登陆时间统计？"
                    "（data/logintime_history.json 将被清空，不可恢复）",
                    danger=True):
                return
            n = forget_long()
            styled_info(dialog, "遗忘登陆时间",
                                    f"已遗忘 {n} 条长期登陆时间记录。")

        lt_forget_short.clicked.connect(_forget_lt_short)
        lt_forget_long.clicked.connect(_forget_lt_long)
        adv_form.addRow("遗忘登陆时间-短期", lt_forget_short)
        adv_form.addRow("遗忘登陆时间-长期", lt_forget_long)

        lt_enabled = QCheckBox("记录打开时间（关闭后不再记录，但不删除已有数据）")
        lt_enabled.setChecked(bool(self._cfg.get(
            "ui", "login_time_enabled", default=True)))
        adv_form.addRow("登陆时间记录", lt_enabled)

        # ---------- 保存 ----------
        def _apply() -> None:
            self._cfg.set(provider.currentText(), "api", "provider")
            self._cfg.set(api_base.text().strip(), "api", "api_base")
            self._cfg.set(api_key.text().strip(), "api", "api_key")
            self._cfg.set(main_model.text().strip() or "gpt-4o", "api", "main_model")
            self._cfg.set(small_model.text().strip() or "gpt-4o-mini", "api", "small_model")
            self._cfg.set(small_base_box.text().strip(), "api", "small_base")
            self._cfg.set(small_key_box.text().strip(), "api", "small_key")
            self._cfg.set(vision_model.text().strip() or "gpt-4o", "api", "vision_model")
            self._cfg.set(vision_base.text().strip(), "api", "vision_base")
            self._cfg.set(vision_key.text().strip(), "api", "vision_key")
            self._cfg.set(image_model.text().strip(), "api", "image_model")
            self._cfg.set(image_base.text().strip(), "api", "image_base")
            self._cfg.set(image_key.text().strip(), "api", "image_key")
            self._cfg.set(image_dir_box.text().strip() or "./createimage",
                          "api", "image_dir")
            # 需求：联网搜索配置
            self._cfg.set(web_enabled.isChecked(), "web", "enabled")
            self._cfg.set(search_base.text().strip(), "web", "search_base")
            self._cfg.set(search_key.text().strip(), "web", "search_key")
            # 需求：思维链显示开关
            self._cfg.set(thinking_check.isChecked(), "ui", "show_thinking")
            self._cfg.set(temp_box.value() / 100.0, "chat", "temperature")
            self._cfg.set(tokens_box.value(), "chat", "max_tokens")
            for key, box in path_boxes.items():
                self._cfg.set(box.text().strip() or f"./{key}", "paths", key)
            self._cfg.set(avatar_box.text().strip(), "user_avatar")
            self._cfg.set((user_name_box.text().strip() or "用户"), "ui", "user_name")
            self._cfg.set(pet_enabled.isChecked(), "pet", "enabled")
            self._cfg.set(greet.value(), "pet", "greeting_interval_min")
            self._cfg.set(focus.isChecked(), "pet", "focus_mode")
            self._cfg.set(floating.isChecked(), "pet", "floating_chat")
            self._cfg.set(zoom_check.isChecked(), "pet", "zoom_enabled")
            self._cfg.set(blur_check.isChecked(), "ui", "blur_background")
            self._cfg.set(lt_enabled.isChecked(), "ui", "login_time_enabled")
            self._cfg.set(transparent_check.isChecked(), "ui", "transparent_chat")
            self._cfg.set(icon_box.text().strip(), "ui", "icon_path")
            fam = font_combo.currentText()
            self._cfg.set("" if fam == "（系统默认）" else fam, "ui", "font_family")
            # 需求：主界面聊天字体大小
            self._cfg.set(chat_font_box.value(), "ui", "chat_font_size")
            # 需求：字体使用整数像素大小（13px），避免 point→非整数像素导致锯齿
            _af = QFont("" if fam == "（系统默认）" else fam)
            _af.setPixelSize(13)
            QApplication.setFont(_af)
            self._cfg.save()
            self._pool.reconfigure()
            self._blur_bg = blur_check.isChecked()
            # 透明聊天窗口状态同步并重建主界面 QSS（按钮/PNG 图不透明）
            self._transparent_chat = transparent_check.isChecked()
            _central = self.centralWidget()
            if _central is not None:
                _central.setStyleSheet(root_qss(self._accent, self._scale,
                                                self._transparent_chat))
            self.update()
            self._apply_icon()
            self._load_theme()
            self._status_label.setText(self._status_text())
            self._bus.settings_updated.emit()
            dialog.accept()

        btn_row = QHBoxLayout()
        about_btn = QPushButton("关于系统")
        about_btn.setStyleSheet(ghost_btn_qss(font_size=12, padding="6px 14px"))
        about_btn.setCursor(Qt.PointingHandCursor)
        about_btn.clicked.connect(lambda: AboutDialog(dialog).exec())
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(btn_qss())
        save_btn.clicked.connect(_apply)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(ghost_btn_qss())
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(about_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        outer.addLayout(btn_row)
        # 需求：设置窗口改为非模态显示（取消模态强制置顶）——
        # 技能工具管理器等子窗口可在其上方正常弹出到最前面；
        # 窗口内容与保存/取消逻辑保持完全不变。
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        self._settings_dialog = dialog
        dialog.finished.connect(
            lambda _res: setattr(self, "_settings_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_accent_swatch(self, color: str) -> None:
        """点击色板：应用主题色并同步各色块选中态。"""
        self._apply_accent(color)
        for sw in self.findChildren(QPushButton):
            if sw.objectName() == "colorSwatch":
                sw.setChecked(sw.styleSheet().find(color.lower()) >= 0)

    def _pick_custom_accent(self) -> None:
        """自定义主题色。"""
        color = QColorDialog.getColor(QColor(self._accent), self, "选择软件主题色")
        if color.isValid():
            self._apply_accent(color.name())
            for sw in self.findChildren(QPushButton):
                if sw.objectName() == "colorSwatch":
                    sw.setChecked(sw.styleSheet().find(color.name().lower()) >= 0)

    def _open_skilltools(self) -> None:
        """从设置面板打开技能工具管理器（复用窗口实例，主题色跟随主界面）。

        技能工具管理器与主程序同进程：直接实例化 SkillManagerWindow，
        主界面切换主题色时通过 _apply_accent 同步（需求：风格变化 skilltools 遵守）。
        """
        try:
            from skilltools import SkillManagerWindow
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "技能工具", f"技能工具管理器加载失败：{exc}")
            return
        if self._skilltools_window is None:
            try:
                self._skilltools_window = SkillManagerWindow()
                self._skilltools_window.apply_accent(self._accent)
            except Exception as exc:  # noqa: BLE001
                styled_warning(self, "技能工具", f"技能工具管理器启动失败：{exc}")
                self._skilltools_window = None
                return
        self._skilltools_window.show()
        self._skilltools_window.raise_()
        self._skilltools_window.activateWindow()

    def _move_project(self) -> None:
        """修改母文件目录：将整个项目复制到所选目录并在此运行。"""
        import shutil
        import subprocess
        import sys

        target = QFileDialog.getExistingDirectory(self, "选择项目要移动到的目录")
        if not target:
            return
        dest = Path(target)
        if not (dest / "main.py").exists():
            dest = dest / project_root().name
        if (dest / "main.py").exists():
            styled_warning(
                self, "移动失败",
                f"目标目录已存在项目：{dest}\n请选择空目录或另一位置。")
            return
        try:
            ignore = shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".venv", ".git", "history", "logs")
            shutil.copytree(project_root(), dest, ignore=ignore)
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "移动失败", str(exc))
            return
        launcher = dest / "start.py"
        subprocess.Popen([sys.executable, str(launcher)], cwd=str(dest),
                         env=utf8_env())
        styled_info(
            self, "移动完成",
            f"项目已复制到新目录：\n{dest}\n\n已在新目录打开启动器。\n"
            f"确认新实例正常后，可手动删除原目录：\n{project_root()}")
        QTimer.singleShot(600, QApplication.instance().quit)

    # ------------------------------------------------------------ 设置更新
    def _on_settings_updated(self) -> None:
        self._load_theme()
        self._refresh_history_list()

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, event: Any) -> None:  # noqa: D102
        self._cleanup_worker()
        self._save_session(quiet=True)
        # 需求：记住退出时的角色/模式（单聊或群聊），下次启动恢复
        self._persist_ui_state()
        event.accept()


# ================================================================
# 模块级全局样式（HTML5 响应式 QSS，按主题色参数化）
# ================================================================
def _config_chat_font_size() -> int:
    """读取用户设置的主界面聊天字体大小（默认 14px）。"""
    try:
        return int(ConfigLoader.instance().get(
            "ui", "chat_font_size", default=14) or 14)
    except Exception:  # noqa: BLE001
        return 14


def _root_qss_base(accent: str, transparent: bool = False,
                   chat_font_size: int = 14) -> str:
    """生成全局 QSS。accent 为主色调（可在设置中修改并实时生效）。

    transparent=True 时启用「透明聊天窗口」（需求 v4）：主聊天界面的
    面板 / 气泡 / 输入框 / 列表 / 下拉框等全部改为半透明，
    按钮（sendBtn / ghostBtn / titleBtn / colorSwatch）与 PNG 图片（立绘、头像）保持不透明。
    """
    sel_rgba = _hex_to_rgba(accent, 0.15)
    # 透明模式下各控件底色（按钮与 PNG 图不受影响）
    card_bg = "rgba(255,255,255,0.42)" if transparent else CARD_BG
    card_bg_alt = "rgba(246,247,252,0.38)" if transparent else CARD_BG_ALT
    input_bg = "rgba(255,255,255,0.55)" if transparent else "white"
    bubble_bg = "rgba(255,255,255,0.42)" if transparent else "#ffffff"
    bubble_user = (_hex_to_rgba(accent, 0.72) if transparent else accent)
    return f"""
QWidget#root {{ background: transparent; }}
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QFrame#sidePanel, QFrame#mainPanel, QFrame#inputArea {{
    background: {card_bg}; border-radius: {RADIUS}px;
}}
QFrame#settingsCard {{
    background: {card_bg}; border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#sectionTitle {{
    color: {TEXT_DARK}; font-size: 15px; font-weight: 600;
    padding-left: 10px; border-left: 4px solid {accent};
}}
QFrame#historyPanel {{ background: {card_bg_alt}; border-radius: 10px; }}
QPushButton#titleBtn {{
    background: transparent; border: none; color: {TEXT_MID};
    font-size: 13px; border-radius: 6px;
}}
QPushButton#titleBtn:hover {{ background: #e8e9f2; color: {TEXT_DARK}; }}
QPushButton#sendBtn {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {accent}, stop:1 {accent});
    color: white; border: none; border-radius: 12px;
    padding: 8px 20px; font-size: 14px; font-weight: 600;
}}
QPushButton#sendBtn:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_lighten(accent, 18)}, stop:1 {accent});
}}
QPushButton#sendBtn:pressed {{ background: {_darken(accent, 12)}; }}
QPushButton#sendBtn:disabled {{
    background: {_hex_to_rgba(accent, 0.5)}; color: rgba(255,255,255,0.85);
}}
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
QPushButton#colorSwatch {{ border: 2px solid {BORDER}; border-radius: 10px; }}
QPushButton#colorSwatch:checked {{ border: 3px solid {TEXT_DARK}; }}
QTextEdit#chatInput {{
    border: 1px solid {BORDER}; border-radius: 10px; background: {input_bg};
    padding: 6px; font-size: {chat_font_size}px; color: {TEXT_DARK};
}}
QTextEdit#chatInput:focus {{ border: 2px solid {accent}; }}
QLabel#portrait {{ border-radius: 12px; }}
QLabel#emotionTag {{ font-size: 13px; font-weight: 600; }}
QLabel#bubbleUser {{
    background: {bubble_user}; color: white; border-radius: 12px;
    font-size: {chat_font_size}px;
}}
QLabel#bubbleAI {{
    background: {bubble_bg}; color: {TEXT_DARK}; border: 1px solid {BORDER};
    border-radius: 12px; font-size: {chat_font_size}px;
}}
QLabel#bubbleName {{ color: {TEXT_LIGHT}; font-size: 11px; }}
QLabel#statusText {{ color: {TEXT_LIGHT}; font-size: 11px; }}
QLabel#historyItem {{
    color: {TEXT_DARK}; font-size: 12px; background: transparent;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QLabel#appTitle {{ color: {TEXT_DARK}; font-size: 14px; font-weight: 600; }}
QLabel#sessionTitle {{ color: {TEXT_DARK}; font-size: 15px; font-weight: 600; }}
QLabel#projPath {{ color: {TEXT_MID}; font-size: 12px; }}
QLabel#aboutTitle {{ color: {TEXT_DARK}; font-size: 20px; font-weight: 600; }}
QLabel#aboutText {{
    color: {TEXT_MID}; font-size: 13px; background: transparent;
    padding: 6px;
}}
QScrollArea#chatScroll {{ background: transparent; border: none; }}
QListWidget {{
    background: {input_bg}; border: 1px solid {BORDER}; border-radius: 8px;
    font-size: 13px; outline: none;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QListWidget::item {{
    padding: 12px 8px; border-radius: 6px;
}}
QListWidget::item:selected {{ background: {sel_rgba}; color: {accent}; }}
QComboBox {{
    background: {input_bg}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 6px 10px; font-size: 13px; color: {TEXT_DARK};
}}
QComboBox:hover {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {input_bg}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {sel_rgba};
    selection-color: {accent};
}}
QScrollBar:vertical {{
    background: #eef0f6; width: 12px; margin: 2px; border-radius: 6px;
}}
QScrollBar::handle:vertical {{
    background: rgba(108,142,245,0.55); border-radius: 6px; min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(108,142,245,0.8); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: #eef0f6; height: 12px; }}
QScrollBar::handle:horizontal {{
    background: rgba(108,142,245,0.55); border-radius: 6px; min-width: 40px;
}}
QMessageBox, QDialog {{ font-size: 13px; }}
QLineEdit {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    font-size: 13px; background: {input_bg}; color: {TEXT_DARK};
}}
QLineEdit:focus {{ border: 2px solid {accent}; }}
QSpinBox {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 4px 6px;
    font-size: 13px; background: {input_bg};
}}
QCheckBox {{ font-size: 13px; color: {TEXT_DARK}; spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: {input_bg};
}}
QCheckBox::indicator:checked {{
    background: {accent}; border-color: {accent};
}}
QGroupBox#settingsSection {{
    background: {card_bg}; border: 1px solid {BORDER};
    border-radius: 12px; margin-top: 4px; font-size: 13px;
}}
QGroupBox#settingsSection::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px;
    color: {TEXT_DARK}; font-weight: 600; font-size: 13px;
}}
"""

def root_qss(accent: str, scale: float = 1.0, transparent: bool = False,
             chat_font_size: Optional[int] = None) -> str:
    """生成全局 QSS。accent 主色调；scale 为界面缩放系数（非全屏等比例缩放）；
    transparent 为透明聊天窗口开关（True 时面板/气泡/输入框/列表半透明，
    按钮与 PNG 图不透明）；chat_font_size 为主界面聊天字体大小（默认读配置）。"""
    if chat_font_size is None:
        chat_font_size = _config_chat_font_size()
    qss = _root_qss_base(accent, transparent, chat_font_size)
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



def _hex_to_rgba(color: str, alpha: float) -> str:
    """#RRGGBB → rgba(r,g,b,a)。"""
    c = QColor(color)
    if not c.isValid():
        c = QColor("#6c8ef5")
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _lighten(color: str, amount: int) -> str:
    c = QColor(color)
    if c.isValid():
        c = c.lighter(100 + amount)
        return c.name()
    return color


def _darken(color: str, amount: int) -> str:
    c = QColor(color)
    if c.isValid():
        c = c.darker(100 + amount)
        return c.name()
    return color


