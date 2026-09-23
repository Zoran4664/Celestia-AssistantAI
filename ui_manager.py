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
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AI_DeskMate.UI")

from PySide6.QtCore import (
    Qt, QThread, QObject, Signal, QTimer, QPoint, QSize, QPropertyAnimation,
    QEasingCurve, QParallelAnimationGroup, QEvent, QRect, QFileSystemWatcher,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath,
    QPixmap, QLinearGradient,
    QMouseEvent, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLayout, QLayoutItem, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from config_loader import ConfigLoader, project_root
from signal_bus import SignalBus
from role_manager import RoleManager, normalize_role_name
from memory_pipeline import MemoryPipeline
from llm_client import LLMClientPool, LLMWorker, humanize_error
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
# 限制专业内容不要带角色（小马）的语气（需求 v3-2）；
# 2026-09-21：增加「不要举例子/类比，只有完成工作后才用角色化语气询问下一步」
# 的硬约束（用户反馈：调技能时模型用角色腔举例子/打比方，浪费 token 且不像工作流）。
_PRO_MODE_INSTRUCTION = (
    "[专业模式] 当前启用了专业/场景标签（如 \\@research、\\@med、\\@education 等）。"
    "请以专业、客观、正式的语气直接作答，内容聚焦于解决问题本身；"
    "不要使用角色（小马）的口癖、卖萌语气、感叹词或角色扮演口吻。"
    "**禁止**为了演示或铺垫而用角色身份举例子、打比方、编对话、列「假如我是你 / 假如我是她」之类的小剧场；"
    "**禁止**在正式答复前用角色口吻做铺垫（例如「那我先帮你…」「让我仔细看看…」），"
    "拿到任务直接给出结果或必要的步骤清单即可。"
    "只有当本步工作已经做完、给出最终答复之后，才允许用角色化的语气问一句「下一步要做什么」之类的简短询问；"
    "如果本步已经完整回答完用户问题，无需追加角色化收尾，直接结束。"
    "回复末尾仍需按〖〗情绪标注要求标注（通常为 neutral）。"
)

# \closed 关闭指令：独立成词（前后为空白或常见中英文标点）
_CLOSED_CMD_RE = re.compile(r"(?:^|\s)[\\/]closed(?=\s|[，。；、！？,.!?；]|$)")

# \@project 指令：查看/更改本对话在 skilluserdata 下的项目名（即文件夹名）
_PROJECT_CMD_RE = re.compile(r"^\s*\\@project\b\s*", re.I)

# 需求：模型输出中的 <content>...</content> 标签（含内部内容）在界面一律不显示。
# 成对标签删除整段内容；自闭合 <content/> 只删标签本身。
_CONTENT_TAG_RE = re.compile(r"<content>.*?</content>", re.S | re.I)
_CONTENT_SELF_CLOSE_RE = re.compile(r"<content\s*/>", re.I)
#: 正文**任意位置**的「[名字]:」前缀 —— 群聊「多角色截断」扫描用
#: （见 ChatWorker._first_speaker_only；需求：朋友群聊只允许一位成员回复）
_INLINE_NAME_PREFIX_RE = re.compile(r"[\[【]\s*([^\]】\n]{1,40}?)\s*[\]】]\s*[:：]\s*")
#: 只剥 <content> 边界、保留内容的版本（YinBreak 等预设用它包裹**正式正文**，
#: 整段删除会让回复变空，此时改用这个兜底）
_CONTENT_BOUNDARY_RE = re.compile(r"</?\s*content\s*>", re.I)

# 长对话性能：聊天区最多保留的气泡数（超出自动裁掉最旧的界面气泡；
# 消息记录 self._messages 仍完整保留，用于存档与记忆）
_MAX_BUBBLES = 80
#: 顶部角色/群组选择框的最大宽度上限（需求：角色名**完整显示**）。
#: 旧值 340 会把「Twilight_Sparkle暮光闪闪」这类中英混排长名切成「…暮光闪…」；
#: 现放宽到 520（足够显示常见长名），仅用于拦住极端超长名字撑变形左栏。
_SELECT_MAX_W = 520

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
        avail = max(1, eff.width())
        for item in self._items:
            hint = item.sizeHint()
            # 需求：单个子控件比整行还宽时也要换行并压到可用宽度内——
            # 以前只有「行内已有控件」才换行，第一个超宽按钮会直接把这一行
            # （乃至整个消息块）撑到视口外面，选项按钮因此超出屏幕。
            w = min(hint.width(), avail)
            if x + w > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, hint.height())))
            x += w + self._spacing
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


def fade_pixmap(label: QLabel, source: QPixmap, ms: int = 1200) -> None:
    """立绘/表情图切换：总共 1.2 秒渐隐过渡。

    流程：第一张先淡出消失（ms/2）→ 换图 → 第二张再淡入出现（ms/2）。
    用 QGraphicsOpacityEffect 实现子控件透明度动画（windowOpacity 只对顶层
    窗口生效）；动画对象保存在 label 上避免被 GC 回收导致闪退。

    source 传入**原始图**而非预缩放图：换图时刻按 label 当时的实际尺寸
    重新等比缩放，避免动画调度时（如窗口首次布局前的瞬时尺寸）生成的
    旧尺寸图在动画结束后覆盖正确适配，导致立绘超出区域被裁剪。
    """
    effect = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(effect)

    def _scaled_now() -> QPixmap:
        w, h = label.width(), label.height()
        if w > 0 and h > 0:
            return source.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return source

    def _swap() -> None:
        label.setPixmap(_scaled_now())
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


#: 命中即可判定「本轮要求思维链 / 思考过程」的提示词（用于自动套用输出协议）
_COT_HINT_RE = re.compile(
    r"思维链|思考过程|推理过程|写作前确认|基础确认|chain[-_ ]?of[-_ ]?thought"
    r"|<think|【思维链】|\bCOT\b",
    re.I)

#: 思维链统一输出协议：思考过程 → 思维链，最终结果 → 正式正文（Gemini 深度思考式）
_COT_PROTOCOL = (
    "【输出协议·思维链模式】本轮必须严格按「先思考、后结果」的结构输出：\n"
    "1. 所有推理、分析、权衡、自检、草稿、剧情梳理等**思考过程**必须写在"
    "【思维链】…【回答】之间；若设定卡要求用 HTML 注释承载思考，"
    "则一律写成 <!-- … -->，不要写进正式正文；\n"
    "2. 思考过程必须**完整位于最终结果之前**，不得与正式正文交错、"
    "不得在结果之后补充；\n"
    "3. 【回答】（或【正式回答】/【正文】）之后只输出最终成品内容——"
    "即真正要呈现给用户的文本，不得再出现思考、过程说明、自检或注释；\n"
    "4. 正式正文里不要复述思考过程，也不要残留【思维链】【回答】等标记；\n"
    # 修复（用户 2026-09-22「普通对话 / 指令对话的思维链过度发散、输出过长」）：
    # 旧协议只规定「必须先思考」，没有任何长度约束，模型于是长篇推演、正文也跟着膨胀。
    "5. 【篇幅约束】**思考过程必须精简**：简单问答/闲聊不超过 3 句（约 100 字内），"
    "普通任务不超过 8 句（约 300 字内）；只写必要的关键判断，"
    "禁止反复自我推翻、禁止罗列与结论无关的备选方案；\n"
    "6. 【正文约束】**正式正文要直接给结论**，不要复述或扩写思考过程："
    "闲聊 1~3 句，问答与指令任务按「结论先行 + 必要细节」作答，"
    "除非用户明确要求「详细/展开/长篇」，否则不要写成大段文章。"
)


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
    usage = Signal(dict)                # 本轮用量（输入/输出/缓存/耗时/速度）
    notice = Signal(str)                # 非致命提示（如附件解析失败，主界面提示气泡）

    #: \@network 里最多直接打开的网址数量（一次抓太多页面既慢又占上下文）
    MAX_FETCH_URLS = 3

    def __init__(self, prompt: str, role: str, group: str,
                 attachments: Optional[List[str]] = None,
                 members: Optional[List[str]] = None,
                 prev_speaker: str = "",
                 user_name: str = "用户",
                 skill_context: str = "",
                 api_override: Optional[Dict[str, Any]] = None,
                 thinking_mode: bool = False,
                 web_search_query: str = "",
                 skillspub_mode: str = "",
                 skillspub_names: Optional[List[str]] = None,
                 roleplay_enabled: bool = False,
                 roleplay_cards: Optional[List[str]] = None,
                 roleplay_scope: str = "",
                 skill_workdir: str = "",
                 project_paths_ctx: str = "",
                 novel_context: str = "") -> None:
        super().__init__()
        self._skill_workdir = skill_workdir
        self._project_paths_ctx = project_paths_ctx
        # 需求：\@novellearn——本对话已学习的小说设定（供本次对话复刻剧情）
        self._novel_context = (novel_context or "").strip()
        self._aborted = False
        self._prompt = prompt
        self._skill_context = skill_context   # @技能 注入的说明上下文（可为空）
        self._api_override = api_override or {}  # 需求 v6：技能独立 API（留空复用主 API）
        self._thinking_mode = thinking_mode   # 需求：\@thinking 思维链技能
        self._web_search_query = (web_search_query or "").strip()  # 需求：\@network 联网搜索
        # 需求：skill库（skillspub）——'' 未开启 / 'manual' \@skillspub / 'auto' \@autoskills
        self._skillspub_mode = (skillspub_mode or "").strip().lower()
        # 需求：#技能名 直接调用——主界面解析出的被点名技能名列表（可为空）
        self._skillspub_names = [
            str(n or "").strip() for n in (skillspub_names or []) if str(n or "").strip()]
        # 需求：角色扮演——主界面「开启角色扮演」开启 + 预设中勾选保存的设定卡列表
        self._roleplay_enabled = bool(roleplay_enabled)
        self._roleplay_cards = list(roleplay_cards or [])
        # 角色扮演会话作用域：「每个对话独立」开启时为会话唯一 ID，否则为空（共享）
        self._roleplay_scope = str(roleplay_scope or "")
        # 角色扮演装配引擎（run() 内懒加载，避免构造期读盘阻塞主线程）
        self._engine: Optional[Any] = None
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

    #: 文本开头的「名字前缀」候选（按顺序尝试；**是否采纳由名字归属决定**）
    _PREFIX_PATTERNS = (
        # [名字]: / 【名字】：
        r"^\s*[\[【]\s*(?P<name>[^\]】\n]{1,40}?)\s*[\]】]\s*[:：]\s*",
        # 名字 说： / 名字说:
        r"^\s*(?P<name>[^:：\n]{1,40}?)\s*说\s*[:：]?\s*",
        # 名字: / 名字：
        r"^\s*(?P<name>[^:：\n]{1,40}?)\s*[:：]\s*",
    )

    @staticmethod
    def _leading_name_prefix(text: str) -> Tuple[str, int, bool]:
        """识别文本开头的「名字前缀」，返回 ``(名字, 前缀结束下标, 是否方括号形式)``。

        需求（用户 2026-09-23）：剥离前缀与解析发言者必须用**同一套名字口径**
        （见 :func:`role_manager.normalize_role_name`）。旧实现把成员名
        ``re.escape`` 后逐字符匹配 —— 成员表是下划线名（Twilight_Sparkle）、
        模型输出是空格名（Twilight Sparkle）时永远不中，前缀就原样留在气泡里。

        本函数只负责「切出名字」，**是否采纳**由调用方判定。
        """
        t = text or ""
        for idx, pat in enumerate(ChatWorker._PREFIX_PATTERNS):
            m = re.match(pat, t, re.S)
            if not m:
                continue
            name = (m.group("name") or "").strip()
            if name:
                return name, m.end(), idx == 0
        return "", 0, False

    @staticmethod
    def _is_name_like(name: str) -> bool:
        """方括号里的内容像不像「角色名」（成员表未命中时的兜底剥离用）。

        要求：2–40 字符、含字母或汉字、不含句读标点。这样 ``[1]:``（有序列表）、
        ``[12]:``、``[笑]:``（单字标签）都不会被误剥。
        """
        s = (name or "").strip()
        if len(s) < 2 or len(s) > 40:
            return False
        if re.search(r"[！？。，、；：!?.,;:\[\]【】]", s):
            return False
        return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", s))

    @staticmethod
    def _strip_role_prefix(text: str, role: str) -> str:
        """移除文本开头的「角色名:」前缀（含 [角色名]/【角色名】/角色名：/角色名 说：）。

        需求（用户 2026-09-23）：名字按**归一化**比较，不再要求逐字符相同 ——
        角色目录名 ``Twilight_Sparkle`` 与模型输出的 ``Twilight Sparkle``
        视为同一角色，前缀必须剥掉（否则会原样显示在气泡里）。
        """
        t = (text or "").strip()
        wanted = normalize_role_name(role)
        if not t or not wanted:
            return t
        name, end, _ = ChatWorker._leading_name_prefix(t)
        if name and end and normalize_role_name(name) == wanted:
            return t[end:].strip()
        return t

    @staticmethod
    def _strip_group_prefix(text: str, members: List[str],
                            aliases: Optional[Dict[str, str]] = None) -> str:
        """剥离文本开头**任意成员**的名字前缀（[角色名]: / 角色名： / 角色名 说：）。

        需求：
        * 群聊正文不显示名字前缀（发言者姓名由气泡标题 name_label 展示）；
        * 2026-09-23：成员表是下划线名、模型输出是空格名时也必须剥掉 → 归一化比较；
        * 方括号里若是不在成员表的**自造名**（模型没照规定格式写），仍按
          ``[名字]:`` 剥掉 —— 否则这几个字会原样显示在气泡里（本次 bug 现象之一）。

        **群聊回复请用 :meth:`_first_speaker_only`** —— 它以本函数为基础，并额外
        截断「模型一次写多位成员」的情况（需求：朋友群聊只允许一位成员回复）。
        """
        t = (text or "").strip()
        if not t or not members:
            return t
        name, end, bracketed = ChatWorker._leading_name_prefix(t)
        if not name or not end:
            return t
        if RoleManager.instance().match_member(name, members, aliases):
            return t[end:].strip()
        if bracketed and ChatWorker._is_name_like(name):
            return t[end:].strip()
        return t

    @staticmethod
    def _first_speaker_only(text: str, members: List[str],
                            aliases: Optional[Dict[str, str]] = None) -> str:
        """群聊回复**只保留第一位发言人的内容**，后续其他成员的段落整段丢弃。

        需求（用户 2026-09-23）：「朋友群聊的时候只能有一个最合适的角色回复，
        而不是多个角色，后面的（其他角色）丢弃」。模型常一次吐出多人对话：

            [Twilight_Sparkle]: 你好呀
            [Rainbow_Dash]: 嘿，我来啦        ← 从这里起整段丢弃（气泡与会话记录都不留）

        规则（名字统一按 :func:`role_manager.normalize_role_name` 归一化后比较）：
          * **第一个**名字前缀（不管写的是谁）视为发言人标签 → 剥掉，内容保留；
          * 之后出现**同一个名字**（模型分段重复写名字）→ 只剥前缀，内容继续保留；
          * 之后出现**别的名字**（含方括号里的自造名）→ **从此处截断**，后续全丢；
          * 不属于任何成员、又不形如方括号名字的（如正文里的「注意：」）→ 原样保留。

        扫描边界 = 位置 0 + 每个换行之后（模型按行写）+ 正文里任意 ``[名字]:``
        （模型偶尔写成一行多人），因此行内切换角色也能截住。
        """
        t = text or ""
        if not t:
            return t
        roles = RoleManager.instance()
        # 候选边界：开头、每个换行之后、正文中任意 [名字]: 处
        cands = {0}
        cands.update(m.start() for m in _INLINE_NAME_PREFIX_RE.finditer(t))
        cands.update(i + 1 for i, ch in enumerate(t) if ch == "\n")
        out: List[str] = []
        pos = 0
        first_key = ""
        for p in sorted(cands | {len(t)}):
            if p < pos:
                continue                     # 落在刚跳过的名字前缀之内
            seg = t[pos:p]
            if p >= len(t):
                out.append(seg)              # 收尾：剩余正文
                break
            name, end, bracketed = ChatWorker._leading_name_prefix(t[p:])
            hit = roles.match_member(name, members, aliases) \
                if (name and members) else ""
            is_speaker = bool(name) and bool(
                hit or (bracketed and ChatWorker._is_name_like(name)))
            if not is_speaker:
                out.append(seg)
                pos = p
                continue
            key = hit or normalize_role_name(name)
            if first_key and key != first_key:
                out.append(seg)              # 换人说话 → 到此为止（截断）
                break
            first_key = first_key or key
            out.append(seg)                  # 发言人标签本身不上屏
            pos = p + end
        return "".join(out).strip()

    @staticmethod
    def _normalize_group_reply(text: str, speaker: str, members: List[str],
                               aliases: Optional[Dict[str, str]] = None) -> str:
        """清理群聊回复：剥掉成员前缀 + **只保留第一位发言人的内容**。

        需求：
        * 界面不显示 [角色名]: / 角色名： 前缀（姓名由气泡标题展示）；
        * 用户 2026-09-23：朋友群聊只能有一位成员回复 → 模型多写的其他成员段落
          整段丢弃（气泡、会话记录、记忆归档都用这份结果）。
        """
        return ChatWorker._first_speaker_only(text, members, aliases)

    # ------------------------------------------------------- 用量估算（兜底）
    _CJK_RE = re.compile(
        r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uf900-\ufaff\uff00-\uffef]")

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        """粗略估算 token 数：接口未返回 usage 时的兜底（仅用于界面参考）。

        中日韩等宽字符按 ~0.75 token/字、其余按 ~3.5 字符/token 估算。
        """
        s = text or ""
        cjk = len(cls._CJK_RE.findall(s))
        other = max(0, len(s) - cjk)
        return max(1, int(cjk * 0.75 + other / 3.5))

    def _build_usage_payload(self, usage: Dict[str, Any],
                             messages: List[Dict[str, Any]], reply_text: str,
                             started: float, first_at: float,
                             ended: float) -> Dict[str, Any]:
        """整理本轮用量：优先服务端 usage，缺失时按文本量估算。"""
        exact = bool(usage.get("exact"))
        if exact:
            p = int(usage.get("prompt_tokens") or 0)
            c = int(usage.get("completion_tokens") or 0)
        else:
            try:
                p = sum(self._estimate_tokens(str(m.get("content") or ""))
                        for m in messages)
            except Exception:  # noqa: BLE001
                p = 0
            c = self._estimate_tokens(reply_text or "")
        cached = int(usage.get("cached_tokens") or 0)
        elapsed = max(0.0, ended - started)
        first = max(0.0, (first_at or ended) - started)
        return {
            "exact": exact,
            "prompt_tokens": int(p),
            "completion_tokens": int(c),
            "total_tokens": int(usage.get("total_tokens") or (p + c)),
            "cached_tokens": cached,
            "cached_unknown": bool(usage.get("cached_unknown", True)),
            "seconds": elapsed,
            "first_delay": first,
            "tps": (c / elapsed) if (elapsed > 0 and c > 0) else 0.0,
        }

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
        # 需求（用户 2026-09-23）：判定结果也必须走**归一化**归属匹配 ——
        # 小模型常把成员名写成空格形式（Twilight Sparkle）而成员表是
        # Twilight_Sparkle，旧的逐字符比较会判定失败 → 回退成「上一发言者」，
        # 于是出现「Twilight 的话显示成 Applejack 说的」。
        hit = self._roles.match_member(name, members)
        if hit:
            logger.info("群聊发言判定结果: %s（依据：%r）", hit, raw)
            return hit
        logger.warning("群聊发言判定结果非法（%r），回退解析", raw)
        return ""

    # ---------------------------------------------------------- 角色扮演装配
    def _init_engine(self) -> Optional[Any]:
        """懒加载角色扮演装配引擎（失败返回 None，不阻塞对话）。"""
        if self._engine is not None:
            return self._engine
        if not self._roleplay_enabled:
            return None
        try:
            from roleplay.roleplay_engine import RolePlayEngine
            self._engine = RolePlayEngine()
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色扮演引擎加载失败: %s", exc)
            self._engine = None
        return self._engine

    def _assembly(self) -> Dict[str, Any]:
        """角色扮演装配选项（引擎不可用时返回空 dict，全部沿用默认行为）。"""
        eng = self._init_engine()
        if eng is None:
            return {}
        try:
            asm = eng.preset.get("assembly", default={}) or {}
            return asm if isinstance(asm, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _isolation(self) -> Dict[str, Any]:
        """角色扮演隔离选项（isolate_normal / per_session）。"""
        eng = self._init_engine()
        if eng is None:
            return {}
        try:
            iso = eng.preset.get("isolation", default={}) or {}
            return iso if isinstance(iso, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _build_system_prompt(self, chosen_speaker: str = "") -> str:
        card = self._roles.role_card(self._role)
        sp = self._roles.role_system_prompt(self._role)
        asm = self._assembly() if self._roleplay_enabled else {}
        emo_list = card.get("emotion_list") or ["neutral", "happy", "sad", "angry", "surprised"]
        # 需求：每一句回复末尾都必须用〖〗标注情绪（从情绪列表选择），
        # 〖〗与其中文字仅供界面识别切换立绘，绝不显示在对话正文中。
        # 角色扮演预设可关闭该要求（长篇叙事场景不需要逐句情绪标签）。
        if not (self._roleplay_enabled and not asm.get("require_emotion_tag", True)):
            sp += (
                f"\n\n【情绪标注要求】每一句回复末尾都必须用〖〗标注当前情绪，"
                f"必须且只能从以下情绪中选择一个："
                f"{'、'.join(str(e) for e in emo_list)}。"
                f"〖〗内的文字仅供程序识别，不要在对话正文中显示〖〗及其内容。"
            )
        # 告知角色用户昵称，让角色知道并称呼对方的名字
        user_name = (self._user_name or "").strip() or "用户"
        sp += f"\n\n用户称呼：{user_name}（请在合适的时机直接称呼这个名字）。"
        # 需求：角色扮演模式下「忘记其他对话」——日常作息、今日记忆传送带、
        # 固定记忆均来自非角色扮演对话，默认全部不注入（可在预设里单独开）。
        if self._roleplay_enabled and not asm.get("use_daily_context"):
            pass
        else:
            # 需求：注入用户打开本应用的常用时间（data/logintime.json + 长期分布），
            # 让对话/问候可以感知用户作息；失败或开关关闭时忽略
            try:
                from logintime import login_time_context
                lt_ctx = login_time_context()
                if lt_ctx:
                    sp += "\n\n【用户的打开时间习惯】" + lt_ctx
            except Exception:  # noqa: BLE001
                pass
        if not self._roleplay_enabled:
            # V2-A1：注入记忆传送带（今天 + 最近几天的对话摘要），让角色有时间叙事感
            try:
                from memory_compile import MemoryCompile
                mc_ctx = MemoryCompile.instance().get_context(self._role)
                if mc_ctx:
                    sp += "\n\n" + mc_ctx
            except Exception:  # noqa: BLE001
                pass
            # V2-A2：注入固定记忆（永远命中，优先级最高）
            try:
                from pinned_memory import PinnedMemory
                pm_ctx = PinnedMemory.instance().get_context()
                if pm_ctx:
                    sp += "\n\n" + pm_ctx
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
                # 需求（用户 2026-09-23）：朋友群聊**只允许一位成员回复**，
                # 模型一次性写多人对话（[A]:…[B]:…）时，程序只会保留第一位。
                "【硬性要求】**本轮只能有一位成员发言**：只输出这一位的 [成员名]: 内容，"
                "**禁止**输出第二位成员的名字或台词；其他成员的反应请省略，"
                "或并入这一位成员的话里（用第一人称提到他们即可）。"
            )
            if chosen_speaker:
                sp += (
                    f"\n【发言者指定】本条消息已确定由 {chosen_speaker} 发言，"
                    f"请以 [{chosen_speaker}]: 开头直接输出 {chosen_speaker} 的回复，"
                    "不要改为其他成员。"
                )
        return sp

    def abort(self) -> None:
        """请求中断：流式生成循环在下一片 token 处退出（立即终止输出）。"""
        self._aborted = True

    def _attachment_context(self) -> str:
        """把待上传附件转成注入对话的文本（兼容旧调用；图片走视觉模型分析）。"""
        return self._attachment_payload()[0]

    #: 常见支持图片输入（多模态）的模型名特征——只在未显式配置 api.main_vision 时
    #: 用于自动判断（用户主模型如 deepseek-chat/deepseek-flash 是纯文本，不在此列）
    _MAIN_VISION_HINTS = (
        "vl", "vision", "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o4-mini",
        "gemini", "claude-3", "claude-4", "claude-sonnet", "claude-opus",
        "glm-4v", "glm-5v", "omni", "internvl", "llava", "minicpm-v",
        "step-1v", "deepseek-vl", "pixtral", "vision-preview", "multimodal",
    )

    @classmethod
    def _main_model_sees_images_for(cls, model: str) -> bool:
        """按模型名判断是否支持图片输入（设置面板初始化勾选状态时复用）。"""
        low = (model or "").lower()
        return any(h in low for h in cls._MAIN_VISION_HINTS)

    def _main_model_sees_images(self) -> bool:
        """主对话模型是否支持**图片输入**（决定图片是直接发主模型还是先转文字）。

        优先级：配置 ``api.main_vision``（设置面板可勾选）> 按模型名自动判断。
        用户反馈「MoE 只能发送摘要，分析不了图」：主模型（MoE 文本模型）拿到的
        只是视觉模型的一句摘要。若主模型本身支持图片，直接把图发给它，它就能
        真正「看图分析」；不支持时（如 deepseek-chat）走视觉模型按问题分析。
        """
        cfg = ConfigLoader.instance()
        v = cfg.get("api", "main_vision", default=None)
        if isinstance(v, bool):
            return v
        model = (str((self._api_override or {}).get("api_model") or "").strip()
                 or str(cfg.main_model() or ""))
        return self._main_model_sees_images_for(model)

    @staticmethod
    def _user_content(text: str, images: List[str]) -> Any:
        """组装 user 消息内容：有图片时用 OpenAI 多模态 content 数组。"""
        if not images:
            return text
        return [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in images]

    def _attachment_payload(self) -> Tuple[str, List[str]]:
        """返回 ``(注入文本, 图片 data URL 列表)``。

        需求（用户反馈「MoE 只能发送摘要分析不了图」）：
        - 主模型支持图片 → 图片以 data URL 随本条 user 消息一起发给它（模型亲眼看图，
          不再依赖转述）；同一条消息里只给一行文件说明；
        - 主模型不支持图片 → 用视觉模型**按用户的问题**分析图片，把分析结果作为
          文本注入（此前只让视觉模型「描述一下」，泛化摘要答不了具体问题）；
        - 文档一律注入提取到的原文；识别失败**不再静默**：发 ``notice`` 提示到
          主界面，同时告诉模型该文件读不了，避免它编造内容。
        """
        extra = ""
        images: List[str] = []
        send_images = self._main_model_sees_images()
        for path in self._attachments:
            fname = Path(path).name
            try:
                suffix = Path(path).suffix.lower()
                if send_images and suffix in LLMClientPool._IMAGE_SUFFIXES:
                    mime, b64, _size = self._pool._prepare_image_data_url(Path(path))
                    images.append(f"data:{mime};base64,{b64}")
                    extra += (f"\n[用户上传图片: {fname}]"
                              "（图片已随本条消息附带，请直接看图回答）")
                    continue
                text = self._pool.attachment_text(path, question=self._prompt)
                extra += f"\n[用户上传文件: {fname}]\n{text}"
            except Exception as exc:  # noqa: BLE001
                self.notice.emit(f"附件《{fname}》未能识别，已跳过：{exc}")
                extra += (f"\n[用户上传文件: {fname}（未能识别，已跳过；"
                          f"请告知用户该文件无法读取）]")
        return extra, images

    def run(self) -> None:
        _run_started = time.perf_counter()   # 本轮总耗时起点（含附件解析 / 记忆检索）
        try:
            # 附件：文档→原文文本；图片→（主模型支持则直接附图 / 否则视觉模型按问题分析）
            _attach_text, _attach_images = self._attachment_payload()
            full_prompt = self._prompt + _attach_text

            # 角色扮演：装配引擎 + 装配选项（引擎不可用时静默降级为普通对话）
            eng = self._init_engine()
            asm = self._assembly() if eng is not None else {}
            # 角色扮演：用户消息出向前正则（placement=1）
            if eng is not None:
                try:
                    full_prompt = eng.outgoing(full_prompt)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("角色扮演输入正则失败: %s", exc)

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

            # 需求：角色扮演记忆与日常记忆分开存储 —— 按开关状态选择命名空间。
            # 「角色扮演对话与日常对话互不读取」关闭时，角色扮演回落到日常命名空间。
            _iso = self._isolation() if eng is not None else {}
            _iso_normal = bool(_iso.get("isolate_normal", True))
            _mem_mode = ("roleplay" if (self._roleplay_enabled and _iso_normal)
                         else "normal")
            # 「每个对话独立」开启时，作用域为本会话 ID；否则角色扮演会话共享
            _mem_scope = (self._roleplay_scope if self._roleplay_enabled else "")
            ctx = ""
            use_memory = bool(asm.get("use_memory", True)) if eng is not None else True
            if use_memory:
                try:
                    ctx = self._memory.retrieve_before(full_prompt, self._role,
                                                       self._group, mode=_mem_mode,
                                                       scope=_mem_scope)
                except Exception:  # noqa: BLE001
                    ctx = ""   # 记忆检索异常不阻塞对话
            messages: List[Dict[str, Any]] = [
                {"role": "system",
                 "content": self._build_system_prompt(chosen_speaker)}
            ]
            # 需求：角色扮演——主界面开启「开启角色扮演」时，用装配引擎把
            # 设定卡（世界观/个人设定/风格/工具）+ 世界书 + 规则卡 + 输出骨架
            # 拼成一条 system 消息注入（位于角色卡之后、记忆之前）。
            # 变量系统（setvar/addvar/getvar）与宏在此统一展开。
            rp_ctx = ""     # 装配出的角色扮演上下文（供思维链协议判定使用）
            if self._roleplay_enabled and eng is not None:
                # 需求（严格加载）：把调用方给出的勾选清单显式传给装配引擎，
                # 只加载被勾选的设定卡——没勾中的卡片一律不进上下文。
                _sel: Dict[str, List[str]] = {}
                for _k in (self._roleplay_cards or []):
                    _cat, _, _name = str(_k).partition("/")
                    if _cat and _name:
                        _sel.setdefault(_cat, []).append(_name)
                try:
                    rp_ctx = eng.build_context(
                        user=self._user_name, char=self._role,
                        group=self._group, members=self._members,
                        scan_text=full_prompt, cards=_sel)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("角色扮演上下文装配失败: %s", exc)
                    rp_ctx = ""
                if rp_ctx:
                    messages.append({
                        "role": "system",
                        "content": "【角色扮演设定】以下为本次角色扮演已加载的设定，"
                                   "请在对话中严格遵守并体现：\n" + rp_ctx,
                    })
            if ctx:
                messages.append({"role": "system", "content": "相关记忆：\n" + ctx})
            # 需求：角色扮演「排他模式」——开启后只使用角色/群聊与角色扮演工具，
            # 不再注入技能上下文（技能、联网搜索、skill库等日常工具）。
            _exclusive = bool(asm.get("exclusive_tools", True)) if eng is not None else False
            # 技能上下文注入（@技能 唤醒：技能说明以 system 级上下文进入 LLM）
            if self._skill_context and not _exclusive:
                messages.append({"role": "system", "content": self._skill_context})
            # 需求：思维链统一输出协议（Gemini 深度思考式）
            # 触发条件：\@thinking，或已加载的设定卡里要求了思维链 / 思考过程 / COT。
            # 角色工具卡常自定义思考小标题（或把思考写在 HTML 注释里），这里统一约束
            # 「思考在前、结果在后」，展示层即可把思考全部收进思维链折叠块。
            _cot_on = bool(self._thinking_mode) or (
                bool(rp_ctx) and bool(_COT_HINT_RE.search(rp_ctx)))
            if _cot_on:
                messages.append({"role": "system", "content": _COT_PROTOCOL})
            # 需求：\@network 联网——① 直接给网址 → 抓网页正文；② 其余走搜索引擎
            if self._web_search_query and not _exclusive:
                from llm_client import extract_urls
                query = self._web_search_query
                urls = extract_urls(query)
                # ① 需求（用户反馈「\@network 工具无法访问网址」）：用户贴了网址时
                # 必须先**真正打开页面**（旧实现把网址当关键词丢给搜索引擎，页面
                # 从来没被访问过 → 表现就是「无法访问网址」）
                if urls:
                    fetched, failed = [], []
                    for _u in urls[:self.MAX_FETCH_URLS]:
                        try:
                            fetched.append(self._pool.web_fetch(_u))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("抓取网址失败 %s：%s", _u, exc)
                            failed.append(f"{_u} → {exc}")
                    if not fetched:
                        self.error.emit("打开网址失败：" + "；".join(failed))
                        return
                    messages.append({
                        "role": "system",
                        "content": "以下是程序已打开并抓取的**网页正文**（不是搜索摘要）："
                                   "\n\n" + "\n\n---\n\n".join(fetched)
                                   + ("\n\n（另有未能打开的网址：" + "；".join(failed)
                                      + "）" if failed else "")
                                   + "\n\n【引用要求】请基于上面的网页正文作答，"
                                     "引用时给出该网页的原网址；正文里没有的信息请"
                                     "如实说明没有查到，不要用记忆编造。"})
                # ② 去掉网址后的文字部分继续交给搜索引擎（纯网址消息则跳过搜索）
                rest = query
                for _u in urls:
                    rest = rest.replace(_u, " ")
                    rest = rest.replace(_u.split("://", 1)[-1], " ")
                rest = rest.strip(" \t\r\n，。；、,;")
                if not urls or rest:
                    try:
                        results = self._pool.web_search(rest or query)
                    except Exception as exc:  # noqa: BLE001
                        self.error.emit(f"联网搜索失败：{exc}")
                        return
                    if results:
                        messages.append({
                            "role": "system",
                            "content": "以下是实时网络搜索结果（越靠前越相关，已按相关性/"
                                       "时效排序）：\n" + results +
                                       "\n\n【引用要求】只采用与问题直接相关、发布时间较新的"
                                       "来源；答案里给出的每条链接必须是上面「来源:」里的"
                                       "**原始网址**，不要改写、拼接或编造网址；若结果与"
                                       "问题无关，请直接说明没有查到可用信息。"})
                    elif not urls:
                        # 没查到（或返回页面与查询无关，已被编号/相关性过滤）→ 明确要求
                        # 如实说明，避免拿无关的 SEO 垃圾页面或记忆编造（用户反馈：
                        # 「返回的东西比上次还离谱，全是 SEO 内容农场」）
                        messages.append({
                            "role": "system",
                            "content": "本次联网搜索**没有找到与问题相关的结果**（接口没返回"
                                       "内容，或返回页面与查询无关已被过滤）。请如实告知用户"
                                       "没有查到可用来源；不要用无关结果、记忆或推测编造"
                                       "事实、数字与链接。"})
            # 需求：skill库（skillspub）——\@skillspub（手动）/ \@autoskills（自动）时，
            # 注入 skill库 指引与所选技能的详细介绍；目录为空或异常时不阻塞对话。
            if self._skillspub_mode and not _exclusive:
                pub_inject = ""
                try:
                    from skillspub_core import context_for_prompt
                    pub_inject = context_for_prompt(
                        self._prompt, mode=self._skillspub_mode, pool=self._pool,
                        workdir=self._skill_workdir)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skillspub 上下文注入失败: %s", exc)
                    pub_inject = ""
                if pub_inject:
                    messages.append({"role": "system", "content": pub_inject})
            proj_ctx = getattr(self, "_project_paths_ctx", "")
            if proj_ctx:
                messages.append({"role": "system", "content": proj_ctx})
            # 需求：\@novellearn——本次对话已学习的小说设定，按它复刻剧情
            # （角色扮演排他模式下同样注入：它本身就是扮演素材）
            novel_ctx = getattr(self, "_novel_context", "")
            if novel_ctx:
                messages.append({"role": "system", "content": novel_ctx})
            # 需求：#技能名 直接调用——只注入被点名到的 skill库 技能详细介绍
            # （区分于 \@skillspub 全量注入；名称在主界面解析后传入本线程）
            if self._skillspub_names and not _exclusive:
                try:
                    from skillspub_core import context_for_direct
                    direct = context_for_direct(self._skillspub_names)
                    if direct:
                        messages.append({"role": "system", "content": direct})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skillspub #技能名 注入失败: %s", exc)
            _hist_n = 5
            if eng is not None:
                try:
                    _hist_n = max(0, int(asm.get("history_turns", 5)))
                except Exception:  # noqa: BLE001
                    _hist_n = 5
            try:
                recent = self._memory.recent_turns(
                    self._role, self._group, limit=_hist_n, mode=_mem_mode,
                    scope=_mem_scope) if _hist_n > 0 else []
            except Exception:  # noqa: BLE001
                recent = []
            for _i, turn in enumerate(recent):
                tname = self._user_name if turn["role"] == "user" else turn["name"]
                body = turn["content"]
                if eng is not None:
                    # 历史消息按「距离最新消息的层数」应用模型层正则（0 = 最新）
                    _depth = max(0, len(recent) - _i)
                    try:
                        body = eng.incoming_model(body, depth=_depth)
                    except Exception:  # noqa: BLE001
                        pass
                messages.append({"role": turn["role"],
                                 "content": f"{tname}: {body}"})
            # 需求：主模型支持图片时把图直接随本条 user 消息发给它（见 _attachment_payload）
            messages.append({
                "role": "user",
                "content": self._user_content(
                    f"{self._user_name}: {full_prompt}", _attach_images)})

            if self._aborted:
                return
            parts: List[str] = []

            def _reasoning_cb(piece: str) -> None:
                """收集模型原生思维链（reasoning_content）并回传主线程。"""
                self._reasoning_parts.append(piece)
                self.reasoning.emit(piece)

            # 角色扮演预设的采样参数覆盖（酒馆式参数管理；未开启的项沿用全局）
            _sampling: Optional[Dict[str, Any]] = None
            if eng is not None:
                try:
                    _sampling = eng.sampling_overrides() or None
                except Exception:  # noqa: BLE001
                    _sampling = None

            api = self._api_override or {}
            custom_base = str(api.get("api_base") or "").strip()
            # 需求：右上角「token 用量 / 响应速度」——收集服务端 usage（无则估算）
            _usage: Dict[str, Any] = {"exact": False}

            def _usage_cb(info: Dict[str, Any]) -> None:
                if info:
                    _usage.clear()
                    _usage.update(info)

            _first_at = 0.0            # 首个正文片段到达时刻（首字延迟）

            def _collect(piece: str) -> bool:
                """收集一个片段；返回 False 表示已中止。"""
                nonlocal _first_at
                if self._aborted:
                    return False
                if not _first_at:
                    _first_at = time.perf_counter()
                parts.append(piece)
                self.token.emit(piece)
                return True

            if custom_base:
                # 需求 v6：每个技能可独立调用不同 API（留空则走主 API）
                for piece in self._pool.chat_stream_custom(
                        messages,
                        base_url=custom_base,
                        api_key=str(api.get("api_key") or ""),
                        model=str(api.get("api_model") or ""),
                        reasoning_cb=_reasoning_cb,
                        sampling=_sampling,
                        usage_cb=_usage_cb):
                    if not _collect(piece):
                        break
            else:
                for piece in self._pool.chat_stream(messages, kind="main",
                                                    reasoning_cb=_reasoning_cb,
                                                    sampling=_sampling,
                                                    usage_cb=_usage_cb):
                    if not _collect(piece):
                        break
            _gen_ended = time.perf_counter()
            text = "".join(parts)
            # 角色扮演：AI 回复的模型层正则（送记忆/归档前清洗）
            if eng is not None:
                try:
                    text = eng.incoming_model(text, depth=0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("角色扮演输出正则失败: %s", exc)

            emo = self._roles.detect_emotion(text, self._role)
            self.emotion.emit(emo)
            clean = self._roles.strip_emotion_tags(text)
            # 需求：模型输出中的 <content>...</content> 标签（含内部内容）
            # 从最终结果中剔除，使存档 / 记忆归档 / 群聊判定都使用干净文本。
            _no_content = _CONTENT_TAG_RE.sub("", clean)
            if not _no_content.strip() and _CONTENT_TAG_RE.search(clean):
                # 预设（YinBreak 等）把**正式正文**包在 <content> 里：整段删除
                # 会让整条回复变空，这种情况只剥标签、保留正文
                _no_content = _CONTENT_BOUNDARY_RE.sub("", clean)
            clean = _CONTENT_SELF_CLOSE_RE.sub("", _no_content)
            speaker = self._role
            if self._group and self._members:
                # 别名也带上传给解析/剥离：模型可能写中文昵称（如「暮光闪闪」）
                _aliases = self._roles.group_aliases(self._group)
                if chosen_speaker in self._members:
                    # 点名/判定场景：强制该成员发言，并统一正文为 [成员名]: 内容
                    speaker = chosen_speaker
                    clean = self._normalize_group_reply(
                        clean, speaker, self._members, _aliases)
                else:
                    # 兜底：解析模型输出中的发言者前缀；失败回退上一发言者/随机
                    speaker = self._roles.resolve_speaker(
                        text, self._members, self._prev_speaker, _aliases)
                    # 需求：群聊正文不显示 [角色名]: / 角色名： 前缀
                    clean = self._normalize_group_reply(
                        clean, speaker, self._members, _aliases)
            else:
                # 单聊时移除模型自行添加的「角色名:」前缀（名字已显示在气泡标题，
                # 避免出现 "Rainbow_Dash: Rainbow_Dash: 内容" 的重复）
                clean = self._strip_role_prefix(clean, self._role)

            # 需求：右上角「token 用量 / 响应速度」——本轮用量回传主线程展示
            try:
                self.usage.emit(self._build_usage_payload(
                    _usage, messages, text, _run_started, _first_at, _gen_ended))
            except Exception as exc:  # noqa: BLE001
                logger.debug("用量统计失败（不影响对话）: %s", exc)
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


class _StatsLabel(QLabel):
    """右上角「token 用量 / 响应速度」灰色小字。

    需求：主页面会话标题右侧、靠近「选择路径」按钮左侧以灰色小字显示
    总 token 用量与本轮（当前小轮）token 用量（输入/输出）、缓存命中
    与响应速度。宽度不足时省略尾部（完整明细见悬浮提示）。

    尺寸要点：sizeHint 始终按**完整文本**计算（而不是已省略后的文本），
    否则「文本越短 → 宽度越小 → 文本更短」会陷入死循环，标签最终被压成
    几像素宽、实际不可见；空间不足时由布局把它压缩到 minimumWidth 并省略。
    """

    MAX_WIDTH = 520          # 完整文本的最大期望宽度（超出则交给布局压缩省略）
    MIN_WIDTH = 72           # 最窄可见宽度（保证不因挤压而消失）

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self._updating = False
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(11)
        self.setFont(f)
        self.setObjectName("statText")
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.setStyleSheet(f"color:{TEXT_LIGHT}; background:transparent;")
        self.setToolTip("暂无用量数据：发送一条消息后显示 token 消耗与响应速度。")

    def sizeHint(self) -> QSize:  # type: ignore[override]
        """按完整文本给期望宽度（与当前省略结果无关，避免宽度塌缩）。"""
        base = super().sizeHint()
        if not self._full_text:
            return QSize(self.MIN_WIDTH, base.height())
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self._full_text) + 6
        return QSize(max(self.MIN_WIDTH, min(w, self.MAX_WIDTH)), base.height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        base = super().minimumSizeHint()
        return QSize(self.MIN_WIDTH, base.height())

    def setStats(self, text: str, tip: str = "") -> None:
        """更新显示文本（自动省略）与悬浮明细。"""
        self._full_text = text or ""
        if tip:
            self.setToolTip(tip)
        self.updateGeometry()      # 文本变长/变短后让布局重新分配宽度
        self._apply()

    def clearStats(self) -> None:
        self._full_text = ""
        self.setText("")
        self.updateGeometry()
        self.setToolTip("暂无用量数据：发送一条消息后显示 token 消耗与响应速度。")

    def _apply(self) -> None:
        fm = QFontMetrics(self.font())
        w = self.width() if self.width() > 0 else 4096
        # 省略尾部（响应速度）：总用量 / 本轮是主要信息，必须优先保留
        new = fm.elidedText(self._full_text, Qt.ElideRight, max(w - 1, 1))
        if new != self.text():
            self.setText(new)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        # 重入保护：setText 可能再次触发 resize → 避免递归（Qt 闪退防护）
        if self._updating:
            return
        self._updating = True
        try:
            self._apply()
        finally:
            self._updating = False


_ICON_CACHE: Dict[str, Any] = {}


def _cached_icon(name: str) -> QIcon:
    """按文件名缓存 QIcon（避免每个条目重复 stat/解码同一张 PNG）。"""
    ico = _ICON_CACHE.get(name)
    if ico is None:
        ico = QIcon(str(project_root() / "img" / name))
        _ICON_CACHE[name] = ico
    return ico


def app_icon(icon_path: str = "") -> QIcon:
    """应用图标（窗口 / 托盘 / 任务栏统一来源）。

    优先级：`icon_path`（**文件存在才用**）→ 内置 `img/logo_128x128.ico`
    （及同族尺寸）→ 纯色兜底。

    注：配置里可能残留指向已不存在文件的路径（如 `./data/app.ico`），
    旧实现遇到这种情况会退回**纯色方块**（用户反馈的「小图标显示不对」）。
    """
    p = Path(icon_path) if icon_path else None
    if p is not None and p.exists():
        ico = QIcon(str(p))
        if not ico.isNull():
            return ico
    for name in ("logo_128x128.ico", "logo_256x256.ico", "logo_48x48.ico",
                 "logo_32x32.ico", "logo_16x16.ico", "logo.png"):
        ico = _cached_icon(name)
        if not ico.isNull():
            return ico
    pm = QPixmap(64, 64)
    pm.fill(QColor(ACCENT))
    return QIcon(pm)


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
        del_btn.setIcon(_cached_icon("del.png"))
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
# 对话渲染组件（酒馆式）：思维链折叠块 / 长输出折叠块 / 选择按钮行
# ================================================================
#: 无边框缩放只关心这几类鼠标事件（eventFilter 装在 app 上，事件量极大，
#: 布局/绘制/定时器事件一律直接放行，省掉每次的类型判断与命中检测）
_RESIZE_MOUSE_TYPES = frozenset((
    QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease))


def _set_style(widget: Any, css: str) -> None:
    """设置样式表（内容未变则跳过）。

    性能：Qt 的 ``setStyleSheet`` 每次都会 unpolish/polish 并重新解析 QSS，
    折叠块在流式期间会被反复调用、每个气泡创建时也会调用多次——长对话里
    累积开销可观（实测 10 轮就有上千次）。内容相同直接跳过。
    """
    try:
        if widget.styleSheet() != css:
            widget.setStyleSheet(css)
    except Exception:  # noqa: BLE001
        pass


#: 连续无空格的长串（超长 URL / base64 / 未换行的英文）无法被 QLabel 断行，
#: 会把折叠块撑到视口外——插入零宽空格让它可以在任意位置断行。
_LONG_TOKEN_RE = re.compile(r"\S{40,}")
_ZWSP = "\u200b"
#: 思维链里残留的 <think> / <details> 之类标签壳（纯文本渲染时只留内容）
_THINK_TAG_RE = re.compile(
    r"</?\s*(?:think|thinking|details|summary|draft_notes|plot)\b[^>]*>",
    re.IGNORECASE)


def _soft_break(text: str) -> str:
    """给超长无空格串插入零宽空格，保证 QLabel 能断行（不撑破布局）。"""
    def _rep(m: "re.Match[str]") -> str:
        s = m.group(0)
        return _ZWSP.join(s[i:i + 20] for i in range(0, len(s), 20))
    return _LONG_TOKEN_RE.sub(_rep, text or "")


class CollapsibleBlock(QWidget):
    """可折叠的内容块（思维链、剧情设计、长工具输出都用它）。

    需求：
    - HTML5 ``<details><summary>`` 观感：标题是一颗圆角 chip 按钮，
      右侧「展开 / 收起」提示，点击整颗按钮切换；
    - 默认「半折叠」：标题下露出**一行小字预览**（Gemini 风），点击才铺开全文；
    - 内容照常写入会话存档，折叠只是显示层行为。

    性能：正文按需渲染（折叠不排版）+ 长度上限，超长思维链展开也不卡。
    """

    #: 正文一次最多铺多少字（再多只显示开头并注明已省略）
    MAX_BODY_CHARS = 20000

    def __init__(self, title: str = "", accent: str = ACCENT,
                 parent: Optional[QWidget] = None,
                 placeholder: str = "（空）") -> None:
        super().__init__(parent)
        self._accent = accent or ACCENT
        self._placeholder = placeholder
        self._collapsed = True
        self._title = ""
        self._full = ""
        self._peek_src = ""
        # Gemini / 元宝风格：流式期间标题走「思考中…」动画 + 顶部细进度条，
        # 完成后显示「N 字 · 用时 N 秒」。
        self._streaming = False
        self._dots = 0
        self._elapsed: Optional[float] = None
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 4, 2, 6)
        v.setSpacing(3)

        # <summary> 行：左标题 + 右「展开/收起」，整体是一颗可点的 chip 按钮
        self._toggle = QPushButton()
        self._toggle.setObjectName("collapseBtn")
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setFlat(True)
        self._toggle.setToolTip("点击展开 / 收起（内容始终写入会话记录）")
        self._toggle.setMinimumHeight(26)
        self._toggle.clicked.connect(self.toggle)
        tw = QHBoxLayout(self._toggle)
        tw.setContentsMargins(12, 3, 12, 3)
        tw.setSpacing(6)
        self._head = QLabel()
        self._head.setObjectName("collapseHead")
        self._act = QLabel()
        self._act.setObjectName("collapseAct")
        # 让点击穿透到按钮本身（否则点在文字上不触发切换）
        for _lb in (self._head, self._act):
            _lb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        tw.addWidget(self._head, 1)
        tw.addWidget(self._act, 0, Qt.AlignRight)
        v.addWidget(self._toggle)

        self._bar = QProgressBar()
        self._bar.setObjectName("thinkBar")
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(3)
        self._bar.setRange(0, 0)          # 0/0 = 持续滚动的忙碌动画
        self._bar.hide()
        v.addWidget(self._bar)

        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(400)
        self._dots_timer.timeout.connect(self._tick_dots)

        # 半折叠预览：折叠态只露一行小字（不换行，按宽度省略）
        self._peek = QLabel()
        self._peek.setObjectName("collapsePeek")
        self._peek.setWordWrap(False)
        # 纯文本渲染：思维链里常有 <think> 之类尖括号，按富文本解析既慢、
        # 又可能被 Qt 当成畸形 HTML 直接吞掉内容
        self._peek.setTextFormat(Qt.PlainText)
        self._peek.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self._peek)

        self._body = QLabel()
        self._body.setObjectName("collapseBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.PlainText)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.hide()
        v.addWidget(self._body)
        # 正文按需渲染：折叠态（默认）根本不铺文本，展开时才渲染
        self._body_dirty = False
        self._last_render = 0.0
        self.set_title(title)
        self._apply_style()
        self._apply_visibility()

    # --------------------------------------------------- 流式状态（Gemini 风）
    def _tick_dots(self) -> None:
        self._dots = (self._dots + 1) % 4
        self._refresh_header()

    def set_streaming(self, on: bool) -> None:
        """流式进行中：标题显示「思考中…」动画并显示忙碌进度条。"""
        on = bool(on)
        if on and not self._streaming:
            self._dots = 0
            self._dots_timer.start()
        elif not on and self._streaming:
            self._dots_timer.stop()
        self._streaming = on
        self._bar.setVisible(on)
        self._refresh_header()

    def mark_done(self, elapsed: Optional[float] = None) -> None:
        """流式结束：停止动画，标题改为「N 字 · 用时 N 秒」。"""
        self._elapsed = None if elapsed is None else float(elapsed)
        self.set_streaming(False)
        if not self._collapsed:
            self._render_body(force=True)     # 补上被节流跳过的末尾内容

    # ---------------------------------------------------------- 接口
    def set_title(self, title: str) -> None:
        self._title = title or ""

    @property
    def body(self) -> QLabel:
        return self._body

    @staticmethod
    def _rough_len(text: str) -> str:
        n = len(text or "")
        return f"{n} 字" if n < 10000 else f"{n / 1000:.1f} 千字"

    def set_text(self, text: str, title: Optional[str] = None) -> None:
        """设置内容（不改变折叠状态）。"""
        if title is not None:
            self.set_title(title)
        self._full = text or ""
        self._peek_src = self._peek_text(self._full)
        if self._collapsed:
            # 折叠态不铺正文：几十万字思维链的历史会话加载时不再逐条排版
            self._body_dirty = True
            if self._body.text():
                self._body.setText("")
        else:
            self._render_body()      # 展开态：流式期间自带节流
        self._refresh_peek()
        self._refresh_header()
        self._apply_visibility()

    def _render_body(self, force: bool = False) -> None:
        """渲染正文（纯文本 + 长度上限 + 流式节流）。

        需求：点开思维链曾经直接卡死——超长正文一次性交给 QLabel 排版，
        几十万字要算上万次换行。现在：折叠不渲染、展开才渲染、超过
        MAX_BODY_CHARS 只铺开头并注明已省略（完整内容始终在会话记录里）。
        """
        if (not force) and self._streaming:
            now = time.monotonic()
            if self._last_render and now - self._last_render < 0.5:
                self._body_dirty = True      # 流式期间最多 0.5s 重排一次
                return
            self._last_render = now
        raw = self._full or self._placeholder
        # 纯文本渲染后 <think> 这类标签会原样显示出来，这里剥掉标签壳保留内容
        raw = _THINK_TAG_RE.sub("", raw)
        if len(raw) > self.MAX_BODY_CHARS:
            raw = (raw[:self.MAX_BODY_CHARS].rstrip()
                   + f"\n\n… 已省略 {self._rough_len(self._full[self.MAX_BODY_CHARS:])}"
                     f"（完整内容已写入会话记录）")
        self._body.setText(_soft_break(raw))
        self._body_dirty = False

    def set_max_width(self, width: int) -> None:
        """限制折叠块宽度（需求：折叠标签不能超出屏幕）。

        思维链/剧情设计的正文可能是一整段没有换行的长文本，QLabel 的
        sizeHint 会把父布局一路撑宽，最终整条消息超出可视区。这里把块本身
        和正文/预览都压到气泡宽度以内。
        """
        w = int(width or 0)
        if w <= 0 or w == getattr(self, "_max_w", 0):
            return
        self._max_w = w
        try:
            self.setMaximumWidth(w)
            inner = max(60, w - 28)
            # 标题 chip 不限宽：标题是短文本，限宽反而会在窄容器里把标题
            # 折成多行，把折叠态顶高（只有正文/预览需要限宽防溢出）
            self._body.setMaximumWidth(inner)
            self._peek.setMaximumWidth(inner)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _peek_text(text: str) -> str:
        """半折叠预览：压成一行、去 markdown 记号，用于折叠态的小字。

        只看开头一小段——预览最终会被 elide 成一行，没必要对几万字的思维链
        全文做正则处理（历史会话有几十条时这一项就能耗掉近一秒）。
        """
        t = re.sub(r"\s+", " ", (text or "")[:400]).strip()
        t = re.sub(r"[`*_#>|]+", "", t).strip()
        return t

    def _refresh_peek(self) -> None:
        """按当前宽度把预览截成一行（超出部分省略号）。"""
        fm = self._peek.fontMetrics()
        self._peek.setFixedHeight(fm.lineSpacing() + 4)
        w = max(60, self._peek.width() - 14)
        self._peek.setText(
            fm.elidedText(self._peek_src, Qt.ElideRight, w) if self._peek_src
            else "")

    def _refresh_header(self) -> None:
        n = self._rough_len(self._full)
        if self._streaming:
            status = "思考中" + "·" * (self._dots + 1)
        elif self._elapsed is not None:
            status = f"{n} · 用时 {self._elapsed:.0f} 秒"
        else:
            status = n
        # Gemini / 元宝风格：标题 + 状态；右侧展开/收起提示
        self._head.setText(f"{self._title} · {status}")
        self._act.setText("收起 ▴" if not self._collapsed else "展开 ▾")
        self._apply_style()

    def _apply_style(self) -> None:
        rgb = _hex_to_rgba(self._accent, 0.10)
        line = _hex_to_rgba(self._accent, 0.45)
        # HTML5 <summary> 观感：默认透明描边胶囊，hover 才上淡色
        _set_style(self._toggle,
                   f"QPushButton#collapseBtn{{background:transparent;"
                   f"border:1px solid {line};border-radius:13px;}}"
                   f"QPushButton#collapseBtn:hover{{background:{rgb};"
                   f"border:1px solid {_hex_to_rgba(self._accent, 0.65)};}}")
        _set_style(self._head,
                   "QLabel{color:#6b7280;font-size:12px;background:transparent;"
                   "border:none;padding:0;}")
        _set_style(self._act,
                   f"QLabel{{color:{self._accent};font-size:11px;"
                   f"background:transparent;border:none;padding:0;}}")
        _set_style(self._bar,
                   f"QProgressBar{{border:none;background:{rgb};"
                   f"border-radius:1px;}}"
                   f"QProgressBar::chunk{{background:{self._accent};"
                   f"border-radius:1px;}}")
        _set_style(self._peek,
                   f"QLabel{{color:#9aa2b1;font-size:11px;background:transparent;"
                   f"border-left:2px solid {_hex_to_rgba(self._accent, 0.30)};"
                   f"padding:1px 0 1px 8px;margin-left:12px;}}")
        _set_style(self._body,
                   f"QLabel{{color:#7b8394;font-size:12px;background:{rgb};"
                   f"border-left:3px solid {line};"
                   f"padding:8px 12px;border-radius:6px;}}")

    # ---------------------------------------------------------- 折叠
    def _apply_visibility(self) -> None:
        """折叠态：一行小字预览；展开态：完整内容。"""
        if self._collapsed:
            self._peek.setVisible(bool(self._peek_src))
            self._body.hide()
            if self._body.text():
                # 收起就把正文释放掉：几十条长思维链的文本不必常驻在控件里
                self._body.setText("")
            self._body_dirty = True
        else:
            self._peek.hide()
            if getattr(self, "_body_dirty", False) or not self._body.text():
                self._render_body(force=True)
            self._body.show()
        # 隐藏正文后必须立即重算布局，否则收起后仍残留正文的高度
        # （只 updateGeometry 时父布局要等下一轮事件循环才收缩）
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        self.updateGeometry()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self._apply_visibility()
        self._refresh_header()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """宽度变化时重算一行预览的省略位置。"""
        super().resizeEvent(event)
        if self._peek_src:
            self._refresh_peek()

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    @property
    def collapsed(self) -> bool:
        return self._collapsed


class ThinkingBlock(CollapsibleBlock):
    """思维链 / 摘要折叠块。

    需求：思维链是「单独的折叠框」——默认收起只占一行，内容仍然完整写入
    会话存档与消息记录（记录但不显示）。
    """

    def __init__(self, accent: str = ACCENT,
                 parent: Optional[QWidget] = None,
                 title: str = "思考过程") -> None:
        super().__init__(title=title, accent=accent, parent=parent,
                         placeholder="（本轮没有思维链内容）")


class PlotBlock(CollapsibleBlock):
    """「剧情设计」折叠块（TGbreak 等预设的 <draft_notes> / COT 梳理内容）。

    需求：剧情设计那部分和思维链一样折叠显示（Gemini / 元宝风格）——
    默认收起只占一行，展开才看梳理内容，内容照常写入会话存档。
    """

    def __init__(self, accent: str = ACCENT,
                 parent: Optional[QWidget] = None,
                 title: str = "剧情设计") -> None:
        super().__init__(title=title, accent=accent, parent=parent,
                         placeholder="（本轮没有剧情设计内容）")


class LongOutputBlock(CollapsibleBlock):
    """过长的工具 / 结构化输出折叠块。

    需求：工具输出、加载的角色设定等很长的内容只显示摘要，完整内容折叠；
    摘要之外的原文写入记录，界面不铺开。
    """

    def __init__(self, accent: str = ACCENT,
                 parent: Optional[QWidget] = None,
                 title: str = "完整输出") -> None:
        super().__init__(title=title, accent=accent, parent=parent,
                         placeholder="")


class ChoiceRow(QWidget):
    """HTML5 风格「选择按钮行」（酒馆 Quick Replies 式）。

    需求：需要用户做出选择的场景（模型给出的选项、工具要求选择参数/路径等）
    用可点击按钮呈现；点击**只把选项填入输入框**（不直接执行），用户可以继续
    补充文字，点「发送」才真正执行。
    """

    chosen = Signal(str)          # 参数：被点击的选项文本

    #: 选项按钮最多折几行（再多就省略，避免一颗按钮占半屏）
    MAX_LINES = 3

    def __init__(self, options: Optional[List[str]] = None,
                 accent: str = ACCENT, parent: Optional[QWidget] = None,
                 title: str = "", max_width: int = 0) -> None:
        super().__init__(parent)
        self._accent = accent or ACCENT
        self._max_w = int(max_width or 0)
        self._last_fit_w = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)
        if title:
            tip = QLabel(title)
            tip.setObjectName("statusText")
            tip.setWordWrap(True)
            lay.addWidget(tip)
        self._flow = _FlowLayout()
        self._flow.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._flow)
        if self._max_w > 0:
            # 创建期立即封顶（此前只有 set_max_width 才封，错过时机的调用方
            # 首次布局就按超宽文本排按钮，右缘会被视口直接裁掉）
            self.setMaximumWidth(self._max_w)
        for opt in options or []:
            self.add_option(opt)

    def resizeEvent(self, event: Any) -> None:  # noqa: D102
        """按实际宽度重排按钮文字（窗口由宽变窄后 _max_w 可能是旧值）。"""
        super().resizeEvent(event)
        self._fit_row_width()

    def _fit_row_width(self) -> None:
        """按当前实际宽度（与 _max_w 取小）重排按钮，宽度未变时不重复算。"""
        w = self.width()
        if self._max_w > 80:
            w = min(w, self._max_w)
        if w <= 80 or w == self._last_fit_w:
            return
        self._last_fit_w = w
        for btn in self.findChildren(QPushButton):
            self._fit_button(btn)

    def set_max_width(self, width: int) -> None:
        """按可视宽度限制按钮行宽度并重排按钮文字（窗口缩放后同步）。"""
        w = int(width or 0)
        if w <= 0 or w == self._max_w:
            return
        self._max_w = w
        self._last_fit_w = 0      # 让 _fit_row_width 按新宽度重新折行
        try:
            self.setMaximumWidth(w)
        except Exception:  # noqa: BLE001
            pass
        for btn in self.findChildren(QPushButton):
            self._fit_button(btn)

    def _wrap_text(self, text: str, max_w: int, fm: Any = None) -> str:
        """把过长的选项文字折成多行（QPushButton 不会自动换行）。"""
        if max_w <= 80 or not text:
            return text
        fm = fm or self.fontMetrics()
        inner = max_w - 32                      # 左右 padding + 边框
        if fm.horizontalAdvance(text) <= inner:
            return text                          # 放得下：原样（快路径）
        lines: List[str] = []
        cur = ""
        for ch in text:
            if fm.horizontalAdvance(cur + ch) > inner and cur:
                lines.append(cur)
                cur = ch
                if len(lines) >= self.MAX_LINES:
                    break
            else:
                cur += ch
        if len(lines) >= self.MAX_LINES:
            lines.append(fm.elidedText(cur + "…", Qt.ElideRight, inner)
                         if cur else "…")
        else:
            lines.append(cur)
        return "\n".join(l for l in lines if l)

    def _fit_button(self, btn: QPushButton) -> None:
        raw = btn.property("optRaw") or btn.text()
        if self._max_w > 80:
            btn.setMaximumWidth(self._max_w)
            # 用按钮自己的字体度量折行：QSS 的 font-size:12px 在按钮上，
            # DPI/缩放下与行控件字体不一致时，用行字体会算错折行预算
            btn.setText(self._wrap_text(raw, self._max_w, btn.fontMetrics()))
        else:
            btn.setMaximumWidth(16777215)

    def add_option(self, text: str) -> QPushButton:
        """追加一个选项按钮（HTML5 风格圆角 chip）。"""
        raw = str(text)
        btn = QPushButton(raw)
        btn.setProperty("optRaw", raw)
        btn.setObjectName("choiceBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("点击填入输入框（不会直接发送），可补充内容后再点发送")
        # 关键：显式最小宽度 ≥1，压掉 QPushButton 默认 minimumSizeHint（=整段
        # 文本宽度）。否则窄视口下「按钮文本宽 + 头像 + 边距」会把聊天容器
        # 的最小宽度撑到视口之外——横向滚动条又是 AlwaysOff，整行按钮直接
        # 被右缘裁掉（点不到被裁住的选项）。
        btn.setMinimumWidth(1)
        btn.clicked.connect(lambda _=False, t=raw: self.chosen.emit(t))
        self._fit_button(btn)
        self._flow.addWidget(btn)
        self._apply_style(btn)
        return btn

    def _apply_style(self, btn: QPushButton) -> None:
        btn.setStyleSheet(
            f"QPushButton{{background:rgba(255,255,255,0.9);"
            f"color:{TEXT_DARK};border:1px solid {_hex_to_rgba(self._accent, 0.45)};"
            f"border-radius:14px;padding:5px 14px;font-size:12px;}}"
            f"QPushButton:hover{{background:{_hex_to_rgba(self._accent, 0.14)};"
            f"border-color:{self._accent};}}"
            f"QPushButton:pressed{{background:{_hex_to_rgba(self._accent, 0.24)};}}")

    def options(self) -> List[str]:
        """返回选项原文（按钮文字可能因折行带了 \\n，这里取原始值）。"""
        return [str(b.property("optRaw") or b.text())
                for b in self.findChildren(QPushButton)]


# ---------------------------------------------------------------------------
# 显示层清洗：结构标签剥离 / 摘要隐藏 / 轻量 markdown
# ---------------------------------------------------------------------------
#: 结构性标签（来自规则卡 JSON 的输出骨架等）——显示时只保留内容，不显示尖括号
_STRUCT_TAGS = (
    "draft_notes", "notes", "thinking", "thought", "cot", "reasoning",
    "output", "reply", "body", "answer", "response", "cliche", "scene",
    "summary", "摘要", "总结", "memo", "记忆", "memory",
    # 预设的正文容器（浮生 <draft> / YinBreak <content>）：只保留内容不显示尖括号
    "draft", "content",
    # 酒馆/角色工具卡的「写作前确认」段——属于思考过程，归入思维链
    "基础确认", "写作前确认", "confirmation",
)
#: 摘要/草稿类标签——内容默认不在对话里铺开（折叠或隐藏，只进入记录）
_SUMMARY_TAGS = ("draft_notes", "notes", "thinking", "thought", "cot",
                 "reasoning", "summary", "摘要", "总结", "memo",
                 "基础确认", "写作前确认", "confirmation")
_STRUCT_TAG_RE = re.compile(
    r"</?\s*(" + "|".join(_STRUCT_TAGS) + r")\s*(?:/)?\s*>", re.I)
#: 预设自定义的「结束符号」（TGbreak / TGF 要求模型在回复末尾输出 ``《end》``；
#: 预设自带的酒馆正则会删掉它，这里做等价处理：不显示、也不留在正文里）
_PRESET_END_RE = re.compile(r"《\s*end\s*》|</?\s*end\s*/?\s*>", re.I)


def strip_struct_tags(text: str) -> str:
    """去掉结构性标签的尖括号（保留标签内的文字内容）。

    需求：不要把 JSON（规则卡 / 输出骨架）里的 <></> 带进对话。
    只处理已知的结构标签，代码块里的 HTML 等不受影响。
    """
    return _PRESET_END_RE.sub("", _STRUCT_TAG_RE.sub("", text or ""))


def extract_tag_blocks(text: str) -> Tuple[str, Dict[str, str]]:
    """把摘要/草稿类标签的内容抽出来，返回 (剩余正文, {标签名: 内容})。

    需求：摘要部分「记录而不显示」——抽出的内容进入消息记录与存档，
    界面只显示去掉这些块之后的正文。
    """
    src = text or ""
    blocks: Dict[str, str] = {}
    rest = src
    for tag in _SUMMARY_TAGS:
        pat = re.compile(rf"<\s*{tag}\s*>([\s\S]*?)<\s*/\s*{tag}\s*>", re.I)
        found = pat.findall(rest)
        if not found:
            continue
        blocks[tag] = "\n".join(x.strip() for x in found if x.strip())
        rest = pat.sub("", rest)
    return rest, blocks


# ---------------------------------------------------------------------------
# 剧情设计 / COT 梳理段抽取（需求：与思维链一样折叠，Gemini / 元宝风格）
# ---------------------------------------------------------------------------
#: 剧情设计类梳理段落的行首标记（TGbreak 等预设的 <draft_notes> 内容）
_PLOT_HEAD_RE = re.compile(
    r"^[ \t]*(?:[-•*][ \t]*)?(?:剧情(?:推动)?设计|重构现状|性格分析|"
    r"角色现状快查|剧情推理|起笔规划|反省|字数检查|文风检查|ooc[ _]?检查|"
    r"变量审视|变量纠错|思考过程|梳理)[ \t]*[:：]", re.I | re.M)
#: 流式输出中途：草稿类标签已开但尚未闭合
_PLOT_OPEN_RE = re.compile(
    r"<\s*(draft_notes|notes|thinking|thought|cot|reasoning)\s*>", re.I)
#: 哪些标签的内容算「剧情设计」（其余算「思考过程 / 摘要」）
_PLOT_TAGS = ("draft_notes", "notes", "cot")
#: 酒馆预设展示层正则注入的 HTML 折叠块（Qt 富文本不支持 <details>/<style>）
_HTML_DETAILS_RE = re.compile(r"<details\b[^>]*>([\s\S]*?)</details>", re.I)
_HTML_STYLE_RE = re.compile(r"<style\b[^>]*>[\s\S]*?</style>", re.I)
_HTML_SUMMARY_RE = re.compile(r"<summary\b[^>]*>[\s\S]*?</summary>", re.I)
_HTML_DIV_RE = re.compile(r"</?\s*div\b[^>]*>", re.I)


def split_plot_blocks(blocks: Dict[str, str]) -> Tuple[str, str]:
    """把抽取到的标签块分成 (剧情设计, 思考过程/摘要)。

    ``<draft_notes>`` / ``<notes>`` / ``<cot>`` 属于剧情设计（COT 梳理），
    其余（summary / 摘要 / 总结 / memo）属于思考过程与摘要。
    """
    plot_parts: List[str] = []
    other: List[str] = []
    for k, v in (blocks or {}).items():
        if not v:
            continue
        seg = f"[{k}] {v}"
        (plot_parts if str(k).lower() in _PLOT_TAGS else other).append(seg)
    return "\n".join(plot_parts).strip(), "\n".join(other).strip()


def extract_html_folds(text: str) -> Tuple[str, List[str]]:
    """抽出模型/正则注入的 HTML 折叠块，返回 (剩余正文, 折叠内容列表)。

    酒馆预设（如 TGbreak）的展示层正则会把梳理内容包成
    ``<details><summary>…</summary>…</details>`` 并附带 ``<style>``；
    Qt 富文本不支持这些标签，会原样显示成一坨。这里把内容交给「剧情设计」
    折叠框，正文只保留真正的回复。
    """
    src = text or ""
    low = src.lower()
    if "<details" not in low and "<style" not in low and "<div" not in low:
        return src, []
    folds: List[str] = []
    rest = _HTML_STYLE_RE.sub("", src)

    def _rep(m: "re.Match[str]") -> str:
        inner = _HTML_SUMMARY_RE.sub("", m.group(1) or "")
        inner = _HTML_DIV_RE.sub("", inner)
        if inner.strip():
            folds.append(inner.strip())
        return ""

    rest = _HTML_DETAILS_RE.sub(_rep, rest)
    rest = _HTML_DIV_RE.sub("", rest)
    return rest, folds


# ---------------------------------------------------------------------------
# 展示层「只给模型参考、不该显示给用户」的注入残留
# ---------------------------------------------------------------------------
#: 任何形态的 HTML 折叠标签（含属性、含未闭合）——界面一律不显示尖括号
_HTML_DETAILS_ANY_RE = re.compile(
    r"</?\s*(?:details|summary|disclosure)\b[^>]*>", re.I)
#: 流式期间：``<details>`` 已出、``</details>`` 未到——其后全部算折叠内容
_HTML_DETAILS_OPEN_RE = re.compile(r"<details\b[^>]*>([\s\S]*)$", re.I)
#: 模型复述出来的 OOC / 破限模式系统提示（用户开启破限是自己点的，不必回显）
_OOC_NOTICE_RE = re.compile(
    r"^[ \t]*[【\[]\s*ooc[^】\]]{0,60}[】\]][^\n]*"
    r"(?:\n[ \t]*(?:[-•*·]|\d+[.、)])[^\n]*)*", re.I | re.M)
#: 记忆检索注入的「提炼短对话 / 重要记忆 / 长期记忆」——只作为模型参考
_MEMORY_BLOCK_RE = re.compile(
    r"^[ \t]*[【\[]\s*(?:重要记忆|长期记忆|短期记忆|记忆(?:提炼|片段|检索|摘要)?"
    r"|历史对话(?:提炼|摘要)?|对话(?:提炼|摘要)|提炼(?:的)?短对话)\s*[】\]][^\n]*"
    r"(?:\n[ \t]*(?:[-•*·]|\d+[.、)])[^\n]*)*", re.I | re.M)
#: 末尾未闭合的情绪标签：流式截断会留下「嘿！【开心」这类半截标签，
#: 界面上必须连同其后内容一起隐藏（否则用户会看到半个「【开心」）
_EMOTION_OPEN_TAIL_RE = re.compile(r"[【〖][^】〗\n]{0,40}$")


def history_display_text(text: str) -> str:
    """历史回放时的显示层清洗。

    会话存档保存的是**模型原始输出**，里面可能残留 ``<details>`` 折叠块、
    ``<w2g>`` 选择框标签和 ``<!-- ec: … -->`` 指令注释。这些在生成时被
    显示层剥离了，回放时若不处理，QLabel 会按富文本去解析不支持的标签，
    表现为「历史对话显示异常 / 一片空白」。
    """
    t, _refs = strip_injected_blocks(text or "")
    t = _W2G_ANY_RE.sub("", t)
    t = _HTML_COMMENT_RE.sub("", t)
    t = _HTML_DETAILS_ANY_RE.sub("", t)
    t = _PRESET_END_RE.sub("", t)      # 预设结束符号《end》不显示
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def history_display_parts(text: str) -> Tuple[str, str]:
    """历史回放的显示拆分：返回 ``(正文, 思维链)``。

    旧版本存档的 ``content`` 里可能残留整段思考（``<基础确认>``、``<draft_notes>``、
    ``<think>``、``<｜begin▁of▁thinking｜>`` 等生成时未被拆分的写法）。回放时若直接
    铺进气泡，就会出现「思维链显示在对话正文里 / 正文下方」的错位。这里统一再拆一次：
    正文交给气泡，思考段交回放逻辑挂到思维链折叠块（正文气泡上方）。
    """
    body = history_display_text(text)
    cot, body, _pending = split_cot_stream(body)
    return body, cot.strip()


def strip_injected_blocks(text: str) -> Tuple[str, List[str]]:
    """剥离展示层不该出现的注入残留，返回 (剩余正文, 被剥离的段落)。

    覆盖三类（都不在正文气泡里铺开，内容仍写进会话记录，可挂折叠块查阅）：
      1) HTML 折叠标签 ``<details>/<summary>``（含流式未闭合、含属性）；
      2) 模型复述的 ``【OOC】…破限模式…`` 系统提示；
      3) 记忆检索注入的「提炼短对话 / 重要记忆 / 长期记忆」段落。
    """
    src = text or ""
    refs: List[str] = []
    if not src.strip():
        return src, refs

    def _clean(seg: str) -> str:
        seg = _HTML_SUMMARY_RE.sub("", seg)
        seg = _HTML_DIV_RE.sub("", seg)
        return seg.strip()

    # 1) 未闭合的 <details>（流式输出中途最常见）
    m = _HTML_DETAILS_OPEN_RE.search(src)
    if m is not None and _HTML_DETAILS_RE.search(src) is None:
        inner = _clean(m.group(1) or "")
        if inner:
            refs.append(inner)
        src = src[:m.start()]
    # 1.5) 酒馆预设的指令注释块 <!-- ec: A = … B = … 正文必须采用B。 -->
    m = _HTML_COMMENT_OPEN_RE.search(src)
    if m is not None and "-->" not in src:
        seg = (m.group(1) or "").strip()
        if seg:
            refs.append(seg)
        src = src[:m.start()]

    def _rep_c(mc: "re.Match[str]") -> str:
        seg = (mc.group(0) or "").replace("<!--", "").replace("-->", "").strip()
        if seg:
            refs.append(seg)
        return ""

    src = _HTML_COMMENT_RE.sub(_rep_c, src)
    # 2) OOC / 破限提示 + 记忆提炼短对话（整段拿走，含随后的列表行）
    for pat in (_OOC_NOTICE_RE, _MEMORY_BLOCK_RE):
        def _rep(m2: "re.Match[str]") -> str:
            seg = (m2.group(0) or "").strip()
            if seg:
                refs.append(seg)
            return ""
        src = pat.sub(_rep, src)
    # 3) 兜底：任何残留的折叠标签尖括号都不显示（内容已在折叠块里）
    src = _HTML_DETAILS_ANY_RE.sub("", src)
    return re.sub(r"\n{3,}", "\n\n", src).strip(), refs


def extract_cot_comments(text: str) -> Tuple[str, str]:
    """抽出 HTML 注释 ``<!-- … -->`` 里的思考过程，返回 (剩余正文, 思维链)。

    需求：不少角色工具卡（潮汐 / 浮生 / 破限类）把「思考过程」写在 HTML 注释里
    声明为用户不可见。此前这些注释会在流式期间被转义成 ``&lt;!-- … --&gt;``
    直接混进正式正文，看起来就是「思考内容被当成正文输出」；这里统一识别成
    **思维链**内容，正式正文只保留最终结果。
    """
    src = text or ""
    if not src.strip() or "<!--" not in src:
        return src, ""
    parts: List[str] = []

    def _rep(mc: "re.Match[str]") -> str:
        seg = (mc.group(0) or "").replace("<!--", "").replace("-->", "").strip()
        if seg:
            parts.append(seg)
        return ""

    src = _HTML_COMMENT_RE.sub(_rep, src)
    # 流式期间注释可能还没闭合：未闭合的尾巴同样算思考过程
    if "-->" not in src:
        m = _HTML_COMMENT_OPEN_RE.search(src)
        if m is not None:
            tail = src[m.end():].strip()
            if tail:
                parts.append(tail)
            src = src[:m.start()]
    cot = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
    return re.sub(r"\n{3,}", "\n\n", src).strip(), cot


# ---------------------------------------------------------------------------
# 思维链「相位」拆分（需求：先整段进思维链，思维链输出完再流式出正文）
# ---------------------------------------------------------------------------
#: DeepSeek / 部分预设的思维链标记是全角形态（``<｜begin▁of▁thinking｜>``），
#: 先归一成半角再统一匹配；单字符替换保证索引与原串一一对应，可直接按索引切片。
_COT_NORM_MAP = {ord("\uff5c"): "|", ord("\u2581"): "_"}
#: 思考段的定界标签（正则片段）：开标签 / 闭标签（``</tag>``）共用
_COT_DELIM_TAGS = (
    r"begin[_\s]*of[_\s]*thinking",     # <｜begin▁of▁thinking｜>（灰魂 pro 等）
    r"end[_\s]*of[_\s]*thinking",       # <｜end▁of▁thinking｜>
    r"think(?:ing|ing_process)?", r"thought", r"reasoning", r"cot",
    r"chain[_\s]*of[_\s]*thought", r"draft_notes", r"notes",
    # 浮生：<draft>（正文草稿+自检注释）与 <修改确认>（逐项审查）都属于过程稿，
    # 真正的正文放在 <content> 里——不把过程稿铺进正文气泡
    r"draft", "修改确认",
    "基础确认", "写作前确认", "confirmation",     # 浮生 / YinBreak 的思考段
)
#: 定界符：尖括号标签（可带 / 闭合、可带 | 包裹）或 HTML 注释边界
_COT_DELIM_RE = re.compile(
    r"<\s*\|?\s*(?P<slash>/?)\s*(?P<tag>" + "|".join(_COT_DELIM_TAGS) +
    r")\s*\|?\s*>|(?P<cmt><!--|-->)", re.I)
#: 前缀判定用的纯标签名（流式尾巴只打了一半时用来判断「要不要先按住」）
_COT_PLAIN_TAGS = (
    "begin_of_thinking", "end_of_thinking", "think", "thinking",
    "thinking_process", "thought", "reasoning", "cot", "chain_of_thought",
    "draft_notes", "notes", "draft", "修改确认",
    "基础确认", "写作前确认", "confirmation",
)
#: 流式期间「半个定界符」的尾巴：<thi / <｜begin▁of▁thi / 【思
_COT_TAIL_CAND_RE = re.compile(r"(<[^<>]{0,40}|【[^【】]{0,12})$")


def _cot_tag_is_close(tag: str, slash: str) -> bool:
    """判断定界符是「结束思考段」还是「开始思考段」。"""
    if slash:
        return True
    t = re.sub(r"[\s\-]+", "_", (tag or "")).strip().lower()
    return t.startswith("end_")


def _delim_is_structural(src: str, m: "re.Match[str]") -> bool:
    """定界符是「真正的结构标签」，还是正文里**被引用的标签写法**？

    预设的「格式检查」常把自己的标签写进说明文字里，例如 TGbreak 的计划文本：

        - 格式检查: step1：… <draft_notes>（梳理内容）</draft_notes>、正文、<w2g>选择框、<details>摘要。step2：…

    这里的 ``</draft_notes>`` 只是**被引用的文字**。若当成真标签，思维链会被提前关闭，
    正文（甚至计划文本）就会跑到正文/思维链的对面去——正是「TGbreak 老问题」的根因。

    判定：被引用的标签**两侧都紧贴非空白字符**（如 ``：<draft_notes>（`` / ``</draft_notes>、``），
    真标签则至少一侧是行首/行尾/空白（``<w2g>`` 独占一行、``<｜begin▁of▁thinking｜>正文`` 在行首、
    ``…。</thinking>`` 在行尾）。HTML 注释边界不参与该判定。
    """
    if m.group("cmt"):
        return True
    start, end = m.start(), m.end()
    before = src[start - 1] if start > 0 else ""
    after = src[end] if end < len(src) else ""
    glued_before = bool(before) and not before.isspace()
    glued_after = bool(after) and not after.isspace()
    return not (glued_before and glued_after)


def _hold_partial_delim(body: str) -> str:
    """流式期间把末尾「半个定界符」先按住，不铺进正文。

    例如 ``…</thi`` / ``<｜begin▁of▁thi`` / ``【思`` 这一帧先不显示，
    等下一帧补全后再决定归属——否则标签壳会先闪进正文再消失
    （灰魂 pro 的 ``<｜begin▁of▁thinking｜>`` 就是这样漏出来的）。
    """
    m = _COT_TAIL_CAND_RE.search(body or "")
    if m is None:
        return body
    tail = m.group(1)
    if tail.startswith("<!--") or tail.startswith("【"):
        return body[:m.start()]
    probe = (tail[1:].translate(_COT_NORM_MAP).strip("|").strip()
             .lower().lstrip("/"))
    probe = re.sub(r"[\s\-]+", "_", probe)
    # probe 为空 = 只打了一个 "<"；都可能是思考标签的前缀，先按住
    if not probe or any(t.startswith(probe) for t in _COT_PLAIN_TAGS):
        return body[:m.start()]
    return body


def split_cot_stream(text: str, thinking_mode: bool = False,
                     hold_tail: bool = False) -> Tuple[str, str, bool]:
    """按「思维链相位」拆分文本，返回 (思维链, 正文, 思考段是否仍未闭合)。

    需求（角色扮演长设定卡）：思考过程必须**整段先进入思维链**，思维链输出完
    （闭合标签 / 结束标记到达）之后，正文才开始流式输出。此前只认
    ``【思维链】…【回答】`` 一种中文标记，预设实际使用的 ``<think>``、
    ``<｜begin▁of▁thinking｜>``、``<基础确认>``、``<!-- -->`` 在闭合前会被
    当成正文漏出来，标签尖括号本身也会被显示出来。

    规则：
      * 已知思考标签**未闭合**时，其后所有内容都归思维链（流式期间绝不漏到正文）；
      * 定界符本身（含尖括号）不会出现在思维链与正文任何一侧；
      * ``thinking_mode`` 下第一个定界符之前的内容也算思考（与
        :meth:`_split_thinking_text` 保持一致）。
    """
    src = text or ""
    if not src.strip():
        return "", "", False
    norm = src.translate(_COT_NORM_MAP)
    # 只认同「结构标签」：正文/计划里**引用**的标签写法（两侧都紧贴字符）不参与相位切换，
    # 否则 TGbreak 的格式说明会把思维链提前关闭，导致正文跑到思维链、或思维链跑到正文
    marks = [m for m in _COT_DELIM_RE.finditer(norm)
             if _delim_is_structural(norm, m)]
    if not marks:
        return "", (_hold_partial_delim(src) if hold_tail else src), False
    cot_parts: List[str] = []
    body_parts: List[str] = []
    in_cot = bool(thinking_mode)      # 思维链模式下，首个标记之前也算思考
    pos = 0
    for m in marks:
        seg = src[pos:m.start()]
        (cot_parts if in_cot else body_parts).append(seg)
        pos = m.end()
        cmt = m.group("cmt")
        if cmt:
            in_cot = (cmt == "<!--")
        else:
            in_cot = not _cot_tag_is_close(m.group("tag"), m.group("slash") or "")
    tail = src[pos:]
    (cot_parts if in_cot else body_parts).append(tail)
    cot = re.sub(r"\n{3,}", "\n\n", "\n".join(cot_parts)).strip()
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_parts)).strip()
    if hold_tail:
        # 两侧都要按住半截定界符：否则标签壳会先在思维链 / 正文里闪一下
        cot = _hold_partial_delim(cot)
        body = _hold_partial_delim(body)
    return cot, body, in_cot


def split_display_blocks(text: str,
                        hold_tail: bool = False) -> Tuple[str, str, str, List[str]]:
    """拆出「不铺在正文里」的辅助内容，返回 (正文, 思维链, 折叠内容, 选择框)。

    统一入口（流式渲染与收尾渲染共用）：
      * HTML 注释里的思考过程 → **思维链**（见 :func:`extract_cot_comments`）；
      * 剧情设计 / COT 梳理、OOC 破限提示、记忆提炼短对话 → 折叠块；
      * ``<w2g>`` 选择框 → 选项按钮；
    正文只留真正的最终结果。
    """
    body, cot = extract_cot_comments(text or "")
    # 预设结束符号（TGbreak / TGF 的《end》）属展示层标记：在这里统一摘掉，
    # 保证正文气泡、会话记录与历史回放都不会出现它
    body = _PRESET_END_RE.sub("", body)
    # 需求：先整段进思维链、思维链输出完再出正文——未闭合的思考标签
    # （<think> / <｜begin▁of▁thinking｜> / <基础确认> 等）之后全部归思维链
    cot_tags, body, _pending = split_cot_stream(body, hold_tail=hold_tail)
    if cot_tags:
        cot = (cot + "\n" + cot_tags).strip() if cot else cot_tags
    body, folds = extract_html_folds(body)
    body, plot = extract_plot_draft(body)
    body, refs = strip_injected_blocks(body)
    body, w2g = extract_w2g_choices(body)
    parts = [p.strip() for p in (list(folds) + [plot] + list(refs)) if p.strip()]
    return body.strip(), cot, "\n\n".join(parts).strip(), w2g


def extract_plot_draft(text: str) -> Tuple[str, str]:
    """抽出「剧情设计」类思考过程，返回 (剩余正文, 剧情设计内容)。

    覆盖三种来源：
      1) 流式输出中途：``<draft_notes>`` 已开但尚未闭合，其后全部算梳理内容；
      2) 模型没写标签：正文开头就是「剧情设计: / 重构现状: …」等梳理标记；
      3) HTML 折叠块（见 :func:`extract_html_folds`）。
    """
    src = text or ""
    if not src.strip():
        return src, ""
    # 1) 未闭合的草稿标签（流式期间最常见）
    m = _PLOT_OPEN_RE.search(src)
    if m is not None:
        tail = src[m.end():]
        close = re.search(rf"<\s*/\s*{m.group(1)}\s*>", tail, re.I)
        if close is None:
            return src[:m.start()].strip(), tail.strip()
        plot = tail[:close.start()]
        rest = (src[:m.start()] + tail[close.end():])
        return rest.strip(), plot.strip()
    # 2) 纯文本梳理段（只在正文开头几行内判定，避免误伤正文）
    lines = src.split("\n")
    start = -1
    for i, ln in enumerate(lines[:6]):
        if _PLOT_HEAD_RE.match(ln):
            start = i
            break
    if start < 0:
        return src, ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        s = ln.strip()
        if not s:
            continue
        if (ln[:1] in (" ", "\t") or s[:1] in ("-", "•", "*")
                or _PLOT_HEAD_RE.match(ln)):
            continue
        end = j
        break
    plot = "\n".join(lines[start:end]).strip()
    rest = ("\n".join(lines[:start]) + "\n" + "\n".join(lines[end:])).strip()
    return rest, plot


def mini_markdown(text: str) -> str:
    """极简 markdown → HTML（粗体 / 斜体 / 行内代码 / 引用 / 列表）。

    需求：对话风格向酒馆靠拢——模型常用的 **强调**、`代码`、> 引用、- 列表
    能正常呈现，而不是原样堆在气泡里。
    """
    t = text or ""
    # 围栏代码块（先抽出来，避免内部的星号/短横线被当成强调或列表）
    fences: List[str] = []

    def _fence_rep(m: "re.Match[str]") -> str:
        fences.append(m.group(1))
        return f"\x00FENCE{len(fences) - 1}\x00"

    t = re.sub(r"```[^\n]*\n?([\s\S]*?)```", _fence_rep, t)
    # 行内代码优先（避免内部的下划线/星号被当成强调）
    codes: List[str] = []

    def _code_rep(m: "re.Match[str]") -> str:
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"

    t = re.sub(r"`([^`\n]+)`", _code_rep, t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", t)

    lines = t.split("\n")
    out: List[str] = []
    in_list = False
    for ln in lines:
        s = ln.rstrip()
        if re.match(r"^\s*>\s?", s):
            out.append(
                f'<div style="border-left:3px solid {BORDER};'
                f'padding-left:8px;color:#6b7280;">'
                f'{re.sub(r"^\s*>\s?", "", s)}</div>')
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)$", s)
        if m:
            if not in_list:
                out.append('<ul style="margin:2px 0 2px 18px;">')
                in_list = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            size = max(13, 19 - 2 * len(m.group(1)))
            out.append(f'<div style="font-size:{size}px;font-weight:600;">'
                       f"{m.group(2)}</div>")
            continue
        out.append(s)
    if in_list:
        out.append("</ul>")
    t = "\n".join(out)
    for i, c in enumerate(codes):
        t = t.replace(f"\x00CODE{i}\x00",
                      f'<code style="background:rgba(0,0,0,0.06);'
                      f'padding:1px 4px;border-radius:4px;">{c}</code>')
    for i, c in enumerate(fences):
        t = t.replace(
            f"\x00FENCE{i}\x00",
            f'<div style="background:rgba(127,140,170,0.13);'
            f'border-radius:6px;padding:6px 8px;margin:4px 0;">{c.strip()}</div>')
    return t


#: 正文里的裸链接（http/https/本地文件）；前面紧跟引号或等号说明它已经在
#: href / src 属性里，不能重复包一层 <a>。
#: 字符集排除 *：模型输出常见 `**URL**`（markdown 加粗）或尾巴残留 `URL**`，
#: 不排除会把 `**` 一起吃进 href，链接打不开（用户反馈 2026-09-21）。
_BARE_URL_RE = re.compile(
    r'(?<!["\'=;])(https?://[^\s<>"\'*]+|file:///[^\s<>"\'*]+)')
#: URL 结尾常跟着的中英文标点（不属于链接本身）；末尾追加 `*`/`**`/`***`
#: 以处理 markdown 加粗尾巴（剥离后再做 linkify，** 会先被 mini_markdown
#: 吃掉，但裸尾巴仍可能残留，需要兜底）。
_URL_TAIL_PUNCT = ".,;:!?，。！？；：、,)]）】》」』*"
#: 超链接显示文字的上限（超出折叠成首尾，href 仍是完整链接，可点击可复制原文）
_URL_SHOW_LIMIT = 64


def linkify_urls(text: str, accent: str = "#6c8ef5") -> str:
    """把**已转义**正文里的裸链接包成可点击 ``<a href>``（需求：输出链接即可点）。

    入参是 :func:`_render_body_html` 里 escape 之后的 HTML 文本，所以这里
    **不能再转义**（否则 ``&`` 会变成 ``&amp;amp;``，链接就打不开了）。

    已经在 ``href="..."`` / ``data-img-url="..."`` 里的链接不会被重复处理；
    超长链接只折叠**显示文字**（完整地址仍在 href，复制消息拿到的也是原文）。
    """

    def _rep(m: "re.Match[str]") -> str:
        url = m.group(0)
        tail = ""
        while url and url[-1] in _URL_TAIL_PUNCT:
            tail = url[-1] + tail
            url = url[:-1]
        if not url:
            return m.group(0)
        show = url if len(url) <= _URL_SHOW_LIMIT else (
            url[:52] + "…" + url[-10:])
        return (f'<a href="{url}" style="color:{accent};'
                f'text-decoration:none;">{show}</a>{tail}')

    return _BARE_URL_RE.sub(_rep, text or "")


def _hhmmss(ts: Optional[float]) -> str:
    """时间戳 → HH:MM:SS（缺失时用当前时间），用于酒馆式消息块的时间显示。"""
    import time as _time
    try:
        return _time.strftime(
            "%H:%M:%S", _time.localtime(float(ts) if ts else _time.time()))
    except Exception:  # noqa: BLE001
        return ""


#: 选项标记：【选项】A | B | C / 选项：/ 选择：/ <options>…</options>
#: 注意：这里**不允许**把「选择框、<details>摘要…」这类计划文本当标记——
#: 老正则 `选择` 后面可以跟任意字符，导致 TGbreak 的格式说明被当成选项行，
#: 进而把后面的正文整段吞成「选项」。现在要求 选项/请选择/可选项 直接成词，
#: 或「选择」后必须跟冒号。
_CHOICE_LINE_RE = re.compile(
    r"^\s*【?(?:(?:选项|请选择|可选项|choices?|options?)\s*(?:】|[:：])?|"
    r"选择\s*[:：])\s*(.*?)\s*$",
    re.I | re.M)
_CHOICE_TAG_RE = re.compile(
    r"<\s*(?:options?|choices?)\s*>([\s\S]*?)<\s*/\s*(?:options?|choices?)\s*>",
    re.I)
_CHOICE_SPLIT_RE = re.compile(r"\s*[|｜/／]\s*|\s{2,}")
#: 选项行首编号：A：/ B = / 1. / 一、/ (A) 等
_CHOICE_PREFIX_RE = re.compile(
    r"^\s*(?:[（(]\s*[A-Za-z0-9]{1,2}\s*[)）]|[A-Za-z]|[0-9]{1,2}|"
    r"[一二三四五六七八九十])\s*[:：.、)）=\-–—]\s*")
#: 一行里挤了多个编号选项时的兜底切分：A：xxx B：yyy
_CHOICE_INLINE_RE = re.compile(
    r"\s+(?=(?:[A-Za-z]|[0-9]{1,2})\s*[:：=]\s)")
#: 选项数量上限（模型偶尔会灌一长串，超过就不渲染按钮，避免界面被按钮淹没）
MAX_CHOICES = 8


def _clean_choice(s: str) -> str:
    """清洗单条选项文本（去项目符号与结尾标点）。"""
    return (s or "").strip().strip(" \t-•·*").strip(" \t。;；,，")


def split_choice_block(block: str) -> List[str]:
    """把一个选项块拆成选项列表（**不再把一条选项切碎**）。

    需求：模型给出 ABCD 四个选项，界面就必须正好是这 4 个按钮——
    以前会用 ``|`` / ``/`` / 连续空格二次切分，把「本能反应：惊恐尖叫，
    用魔法把东西全扔出去」这种带分隔符的长句拆成好几条，选项被拆散。

    需求补充：编号要留在按钮上（``A：抓住话柄：……整句``），以前会把 ``A：``
    剥掉，按钮看起来是一串没标识的句子。

    规则（按优先级）：
      1) 有行首编号（A：/ B = / 1. …）→ 一行 = 一个选项，绝不二次切分；
      2) 单行且挤了多个编号 → 按编号位置切分；
      3) 单行无编号 → 兼容老格式 ``A | B | C``，按分隔符切；
      4) 多行无编号 → 一行一个选项。
    """
    lines = [_clean_choice(ln) for ln in (block or "").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return []
    if any(_CHOICE_PREFIX_RE.match(ln) for ln in lines):
        out: List[str] = []
        for ln in lines:
            # 同一行内挤了多个编号选项（A：xx B：yy）时按编号补一刀
            if len(lines) == 1 and _CHOICE_INLINE_RE.search(ln):
                out.extend(_clean_choice(p)
                           for p in _CHOICE_INLINE_RE.split(ln))
            else:
                # 保留编号（A / B / 1），后面接完整的一整句
                m = _CHOICE_PREFIX_RE.match(ln)
                if m is not None:
                    tag = m.group(0).strip(" \t:：.、)）(（=–—-").strip()
                    tail = ln[m.end():].strip(" \t:：")
                    out.append(f"{tag}：{tail}" if tag and tail
                               else (tail or tag))
                else:
                    out.append(ln)
        return [_clean_choice(o) for o in out if _clean_choice(o)]
    if len(lines) == 1:
        return [p for p in (_clean_choice(x)
                            for x in _CHOICE_SPLIT_RE.split(lines[0]))
                if p]
    return lines


def _take_choice_block_lines(text: str) -> Tuple[str, List[str]]:
    """处理「标记行 + 下面连续编号行」的选项块。

    TGbreak 等预设常见写法是标记独占一行、选项写在后面几行::

        【选项】
        A：本能反应：惊恐尖叫……
        B：克制反应：深呼吸后……

    返回 (去掉标记行与选项行后的文本, 选项列表)——一行一项，绝不切碎。
    """
    lines = (text or "").split("\n")
    out: List[str] = []
    opts: List[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = _CHOICE_LINE_RE.match(s) if s else None
        if m is not None and not (m.group(1) or "").strip():
            picked: List[str] = []
            j = i + 1
            last = i
            while j < len(lines):
                t = lines[j].strip()
                if not t:
                    if picked:
                        break            # 选项块已结束
                    last = j             # 标记与选项之间的空行：跳过
                    j += 1
                    continue
                if _CHOICE_PREFIX_RE.match(t) or t[:1] in "-•*·":
                    picked.append(t)
                    last = j
                    j += 1
                else:
                    break
            if picked:
                for p in split_choice_block("\n".join(picked)):
                    if p and p not in opts:
                        opts.append(p)
                i = last + 1              # 标记行 + 选项行一起从正文去掉
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), opts


#: 选项文本长度上限（回放旧存档时的合理性判断：正文段落常远超这个长度）
_CHOICE_MAX_LEN = 80
#: 明显是计划/标签文本的片段——这类「选项」是旧版本误存的，回放时不渲染成按钮
_CHOICE_JUNK = ("<w2g>", "<details>", "step1", "step2", "step3", "格式检查",
                "根据规则必须给出", "<draft_notes>")


def sanitize_stored_choices(opts: Any) -> List[str]:
    """过滤存档里**误存成选项**的正文/计划文本（回放旧会话用）。

    旧版本在「被引用标签」场景下会把正文段落写进 ``msg["choices"]``，
    回放时若照原样渲染，就会出现一整排正文按钮。判定：
      * 含计划/标签关键词，或超长（> ``_CHOICE_MAX_LEN``）→ 丢弃；
      * 无编号前缀、且「像句子」（结尾是句号/感叹号/问号，或逗号 ≥2 个）→ 丢弃。
    正常选项（``A：…`` 这类带编号的）不受影响。
    """
    out: List[str] = []
    for o in opts or []:
        s = str(o or "").strip()
        if not s or len(s) > _CHOICE_MAX_LEN:
            continue
        if any(k in s for k in _CHOICE_JUNK):
            continue
        if not _CHOICE_PREFIX_RE.match(s):
            sentence_like = (s[-1:] in "。！？!?" or s.count("，") >= 2
                             or s.count(",") >= 2)
            if len(s) >= 20 and sentence_like:
                continue
        if s not in out:
            out.append(s)
    return out


def extract_choices(text: str) -> Tuple[str, List[str]]:
    """从模型回复里抽出「选项」，返回 (去掉选项标记后的正文, 选项列表)。

    需求：需要用户选择的场景用按钮呈现（点击只填入输入框，不直接执行）；
    选项保持模型给出的条数（ABCD 四个就渲染四个）。
    """
    src = text or ""
    opts: List[str] = []
    rest = src

    # 0) 「【选项】」独占一行、ABCD 写在下面几行的格式
    rest, block_opts = _take_choice_block_lines(rest)
    for p in block_opts:
        if p not in opts:
            opts.append(p)

    for m in _CHOICE_TAG_RE.finditer(src):
        for p in split_choice_block(m.group(1) or ""):
            if p not in opts:
                opts.append(p)
    rest = _CHOICE_TAG_RE.sub("", rest)

    for m in list(_CHOICE_LINE_RE.finditer(rest)):
        parts = [p for p in split_choice_block(m.group(1) or "") if p]
        if len(parts) < 2 and not _CHOICE_PREFIX_RE.match(parts[0] if parts
                                                          else ""):
            continue                     # 单个候选不算「选项」
        for p in parts:
            if p not in opts:
                opts.append(p)
        rest = rest.replace(m.group(0), "")
    rest = re.sub(r"\n{3,}", "\n\n", rest).strip()
    return rest, opts[:MAX_CHOICES]


# ---------------------------------------------------------------------------
# 选择框标签 <w2g>（TGbreak 等酒馆预设）：渲染成按钮，不显示成文字
# ---------------------------------------------------------------------------
#: 开标签必须是「结构标签」：行首（或前面只有空白）。
#: 计划文本里的引用写法 ``3. 选择框<w2g>（根据规则必须给出）`` 不能开启选择框，
#: 否则会把其后的**正文**一路吞成「选项」（TGbreak 老问题的第二半）。
_W2G_OPEN = r"(?<![^\s])<\s*w2g\b[^>]*>"
_W2G_TAG_RE = re.compile(_W2G_OPEN + r"([\s\S]*?)<\s*/\s*w2g\s*>", re.I)
_W2G_OPEN_RE = re.compile(_W2G_OPEN + r"([\s\S]*)$", re.I)
#: 宽松匹配（第二次机会）：**不要求**开标签前导空白。
#: 需求（用户 2026-09-21「roleplay 下 TGbreak 对话后的行动选项不显示了」）：
#: TGbreak 的格式模板是「正文紧接 ``{{getvar::gs-w2g}}``」，模型常写成
#: 「…正文最后一句。<w2g>」——开标签**紧贴正文**，严格匹配漏掉它，随后标签壳
#: 又被 ``_W2G_ANY_RE`` 剥掉，选项就**凭空消失**（界面上既没按钮也没有文字）。
#: 这里补一次宽松配对；内容仍必须通过 :func:`_looks_like_options`
#: （≥ 一半行是 ``A：``/``B：``/``1.`` 这类编号），所以计划文本里被引用的
#: ``选择框<w2g>（根据规则必须给出）`` 依旧不会被误吞成选项。
_W2G_LOOSE_TAG_RE = re.compile(
    r"<\s*w2g\b[^>]*>([\s\S]*?)<\s*/\s*w2g\s*>", re.I)
_W2G_ANY_RE = re.compile(r"</?\s*w2g\b[^>]*>", re.I)
#: 展示层正则「TG-行动选项美化」把 <w2g> 整块换成了 HTML 卡片，原选项文本被
#: 原样保留在隐藏容器 ``<div id="rawData">…</div>``（replacement 里的 ``$1``）。
#: 万一抽取发生在美化**之后**，也能从这里把选项捞回来，不让它们丢失。
_W2G_CARD_DATA_RE = re.compile(
    r"<div\b[^>]*\bid\s*=\s*[\"']rawData[\"'][^>]*>([\s\S]*?)</div\s*>", re.I)
#: 美化后的整张卡片（`` ``` `` 围栏 + ``<!DOCTYPE html>`` … ``</html>`` ）——从正文
#: 整段移除，避免正文露出一坨 HTML 源码。仅在卡片**确实含 rawData** 时才删，
#: 以免误伤「TG-特写」等其它同样输出 HTML 卡片的展示层规则。
_W2G_CARD_BLOCK_RE = re.compile(
    r"```[ \t]*[\w-]*[ \t]*\r?\n[\s\S]*?</html\s*>[ \t]*\r?\n?```", re.I)
#: 酒馆预设的指令注释块（``<!-- ec: A = … B = … 正文必须采用B。 -->``）等，
#: 只给模型看，界面不显示
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_HTML_COMMENT_OPEN_RE = re.compile(r"<!--([\s\S]*)$")


def _looks_like_options(block: str) -> bool:
    """``<w2g>`` 里的内容是否**真的是一组选项**。

    兜底判断：被引用的 ``<w2g>`` 与真正的 ``</w2g>`` 配对时，抓到的会是「正文 + 选项」，
    行数远超选项上限；此时不应当成选项块（否则正文被吞、界面变成一排正文按钮）。
    """
    lines = [ln.strip() for ln in (block or "").split("\n") if ln.strip()]
    if not lines or len(lines) > MAX_CHOICES * 2:
        return False
    hit = sum(1 for ln in lines
              if _CHOICE_PREFIX_RE.match(ln) or ln[:1] in "-•*·")
    return hit >= max(1, len(lines) // 2)


def extract_w2g_choices(text: str) -> Tuple[str, List[str]]:
    """抽出酒馆预设的「选择框」``<w2g>``，返回 (去掉标签后的正文, 选项列表)。

    需求：``<w2g>`` 段不要显示成文字（更不能被展示层正则替换成一坨 HTML 源码），
    而是渲染成可点击按钮 —— 点击后把选项填进下方输入框，用户可以直接发送，
    也可以自己再补几句一起发送。

    选项格式（TGbreak）：每行一个，``A：小标题：<user>要做的决策``，
    行首编号在按钮上不显示。

    兼容三种形态（按顺序尝试）：
      1) 结构标签（行首/空白前导）``<w2g>…</w2g>``；
      2) **开标签紧贴正文**（``…正文。<w2g>…</w2g>``）——TGbreak 模板所致，
         用宽松配对兜底（内容须通过 :func:`_looks_like_options`）；
      3) **已被展示层正则美化**成 HTML 卡片 —— 选项原文保留在
         ``<div id="rawData">`` 里，从这里捞回来并移除整张卡片。
    """
    src = text or ""
    opts: List[str] = []
    low = src.lower()
    if "<w2g" not in low and "rawdata" not in low:
        return src, opts

    def _collect(block: str) -> None:
        # 复用统一的选项切分：一行一项，绝不把长选项切碎
        for s in split_choice_block(block):
            if s and s not in opts:
                opts.append(s)

    def _rep(m: "re.Match[str]") -> str:
        block = m.group(1) or ""
        if not _looks_like_options(block):
            # 不像选项块（典型：被引用的 <w2g> 与真 </w2g> 配对，把正文一起吞了）：
            # 只去掉标签壳，内容原样留在正文里，绝不把正文变成一排按钮
            return block
        _collect(block)
        return ""

    rest = _W2G_TAG_RE.sub(_rep, src)
    # 第二次机会：开标签紧贴正文（「…正文最后一句。<w2g>」）时严格匹配会漏
    if "<w2g" in rest.lower():
        rest = _W2G_LOOSE_TAG_RE.sub(_rep, rest)
    # 流式期间：开标签已出、闭合标签未到——其后算选择框内容（同样要做选项判定）
    m = _W2G_OPEN_RE.search(rest)
    if m is not None:
        block = m.group(1) or ""
        if _looks_like_options(block):
            _collect(block)
            rest = rest[:m.start()]
    # 已被「TG-行动选项美化」替换成 HTML 卡片：从 rawData 容器捞回选项，
    # 并把整张卡片（``` + <!DOCTYPE html> … </html> ```）从正文移除
    if not opts and "rawdata" in rest.lower():
        m_card = _W2G_CARD_DATA_RE.search(rest)
        if m_card is not None:
            block = m_card.group(1) or ""
            if _looks_like_options(block):
                _collect(block)
            rest = _W2G_CARD_BLOCK_RE.sub(
                lambda mm: ("" if "rawdata" in mm.group(0).lower()
                            else mm.group(0)), rest)
            rest = _W2G_CARD_DATA_RE.sub("", rest)
    # 兜底：任何残留的 w2g 尖括号都不显示
    rest = _W2G_ANY_RE.sub("", rest)
    return rest.strip(), opts


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
        # 本轮 <w2g> 选择框选项（流式期间累积，收尾渲染成可点击按钮）
        self._w2g_options: List[str] = []
        # 技能产出目录（skilluserdata）：一个对话一个「日期+时间」项目文件夹，
        # 附件栏「文件夹」可选择工作文件夹（选择后优先用它）
        self._skilldata_uid_value = ""
        self._skill_workdir = ""
        # 项目路径（「选择路径」按钮，右上角新会话左边）：用户显式选择的读/学习路径，
        # skillpub 生成的技能产出文件夹会自动加入 _auto_paths。
        self._project_paths: List[str] = []
        self._auto_paths: List[str] = []
        # 需求：token 用量 / 响应速度统计（总用量 + 本轮，右上角灰色小字）
        self._reset_token_stats()
        # 本对话是否已明确拒绝「允许技能生成文件」（拒绝后不再反复弹窗询问）
        self._skilldata_denied = False
        # 会话 json 元数据缓存（历史列表不必每次重建都重读全部会话文件）
        self._SESSION_META_CACHE: Dict[str, Any] = {}
        self._busy = False
        self._prev_speaker = ""
        self._messages: List[Dict[str, Any]] = []
        # V2-E4：会话内查找状态（气泡与 _messages 按序对应）
        self._bubble_widgets: List[QLabel] = []
        self._find_index: Optional[int] = None
        self._find_highlight: Optional[int] = None
        self._session_path: Optional[Path] = None
        # 需求：重新打开软件时停留在上次退出时的角色/模式（单聊或群聊）
        self._current_role, self._current_group, self._current_mode = \
            self._restore_ui_state(initial_role)
        self._blur_bg = bool(self._cfg.get("ui", "blur_background", default=False))
        self._proactive_on = bool(self._cfg.get("ui", "random_proactive", default=False))
        self._proactive_remaining = 0
        # 需求：角色扮演——主界面开关，开启后加载角色扮演预设中的设定卡
        self._roleplay_on = bool(self._cfg.get("ui", "roleplay_enabled", default=False))
        # 角色扮演会话作用域 ID（「每个对话独立」时每个会话一份记忆）
        self._roleplay_scope_id = str(uuid.uuid4().hex[:8])
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
        # 需求：#技能名（skill库直接点名调用）——与 @ 技能共用一个弹窗控件样式
        self._pub_popup = None
        self._pub_menu_open = False
        # V2-C3：技能目录热加载（QFileSystemWatcher 监听，保存即生效）
        self._skill_watcher: Optional[QFileSystemWatcher] = None
        self._skill_watch_timer: Optional[QTimer] = None
        self._setup_skill_watcher()
        # 技能工具管理器窗口实例（从设置面板打开后复用，主题色实时同步）
        self._skilltools_window: Optional[QWidget] = None
        self._api_vault_window: Optional[QWidget] = None   # 常用接口类 API 管理器
        self._tool_worker: Optional[Any] = None            # 程序侧技能后台任务保活
        self._novel_context: str = ""                      # \@novellearn 学到的设定
        # skillspub 管理器窗口实例（与技能工具管理器同风格，复用避免重复创建）
        self._skillpub_window: Optional[QWidget] = None
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
        # 需求：立绘 label 尺寸变化立即重新适配（窗口构建完成后安装，安全）
        if hasattr(self, "_portrait_label"):
            self._portrait_label.installEventFilter(self)

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
        if event.type() not in _RESIZE_MOUSE_TYPES:
            return False                     # 非鼠标事件：立刻放行（高频事件很多）
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
        # 需求：角色名完整显示 —— 左栏宽度要跟着最长角色名走（见 _fit_select_combo）
        self._left_panel = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 立绘区（占左侧 4/5，强制 300x400 基准）
        self._portrait_label = QLabel()
        self._portrait_label.setObjectName("portrait")
        self._portrait_label.setAlignment(Qt.AlignCenter)
        self._portrait_label.setMinimumSize(300, 400)
        # 需求：立绘尺寸变化（窗口缩放/全屏/布局重排）时立即重新适配，
        # 保证任何尺寸下图完整显示不裁剪。事件过滤器在 _init_resize 中安装
        #（窗口构建完成后，避免属性未初始化）。
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

        # 3.5 开启角色扮演（需求：开启后加载设置界面「角色对话管理」勾选保存的设定卡）
        self._roleplay_check = QCheckBox("开启角色扮演")
        self._roleplay_check.setChecked(self._roleplay_on)
        self._roleplay_check.toggled.connect(self._on_roleplay_toggled)
        layout.addWidget(self._roleplay_check)

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
            if path and Path(path).exists():
                # 需求：左侧立绘不被按钮/选择框遮挡、完整显示 —— 保存**原始立绘图**，
                # 显示时按立绘区域实际尺寸等比缩放（不经过固定 300x400 画布，避免
                # 比例错乱与二次缩放模糊；窗口缩放时由 _apply_portrait 自动适配）。
                pm = QPixmap(str(path))
            else:
                pm = QPixmap(300, 400)
                pm.fill(QColor("#cfd8ea"))
            self._portrait_pixmap = pm
            self._apply_portrait(animate=True)
        # 不在主界面用文字显示当前情绪（需求：隐藏文字标签）
        self._emotion_label.hide()
        self._bus.portrait_switched.emit(role, emotion)

    def _apply_portrait(self, animate: bool = True) -> None:
        """把立绘原始图按当前立绘区域尺寸等比缩放后显示（保证完整可见）。

        需求：左侧控件增多后立绘区域可能被压缩，固定尺寸的图会显示不全；
        这里始终用原始图 + KeepAspectRatio 完整缩放，任何窗口尺寸下都不裁剪、
        不变形，且尽量填满立绘区域（宽高比与区域不一致时保留上下/左右留白）。
        """
        label = getattr(self, "_portrait_label", None)
        pm = getattr(self, "_portrait_pixmap", None)
        if label is None or pm is None or pm.isNull():
            return
        w, h = label.width(), label.height()
        if w <= 0 or h <= 0:
            # 布局尚未就绪（如首次显示前）：不要用 fallback 尺寸生成固定图，
            # 否则小屏窗口显示后 label 实际更小，图会超出区域被裁剪。
            # 延迟重试，等布局完成后再按真实尺寸适配。
            QTimer.singleShot(60, lambda: self._apply_portrait(animate=animate))
            return
        if animate:
            # 渐隐动画：传入原始图，换图瞬间按 label 当时实际尺寸等比缩放，
            # 防止"调度时尺寸 ≠ 换图时尺寸"造成旧尺寸图覆盖（立绘被左右裁剪）。
            fade_pixmap(label, pm)   # 情绪切换 1.2 秒渐隐过渡
            # 动画结束（约 1.3 秒）后按最新尺寸/比例再校正一次
            QTimer.singleShot(1300, lambda: self._fit_portrait())
        else:
            scaled = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(scaled)

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

        # 需求：会话标题右侧、「选择路径」按钮左侧显示总 token 用量（输入/输出）、
        # 本轮（当前小轮）token 用量（输入/输出）、缓存命中与响应速度——灰色小字，
        # 宽度不足自动省略，完整明细见悬浮提示
        self._stats_label = _StatsLabel()
        top.addWidget(self._stats_label)

        path_btn = QPushButton("选择路径")
        path_btn.setCursor(Qt.PointingHandCursor)
        path_btn.setToolTip(
            "管理项目路径：选中的文件夹作为本次对话的「学习 / 读取路径」"
            "（LLM 可直接读取其中文件），并作为技能产出目录；"
            "执行技能自动生成的文件夹会加入这里。")
        path_btn.clicked.connect(self._open_project_paths)
        path_btn.setStyleSheet(ghost_btn_qss())
        top.addWidget(path_btn)

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
        # 需求：容器最小宽度钳到 1——任一子控件的 minimumSizeHint（超长选项
        # 按钮 / 附件名等）都可能把容器撑到视口之外，而横向滚动条是 AlwaysOff，
        # 溢出部分会被直接裁掉（选项按钮曾被裁到点不到）。显式最小宽度会让
        # widgetResizable 始终把容器压回视口宽度，子控件由流式布局自行换行。
        self._chat_container.setMinimumWidth(1)
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

        # V2-E1：历史会话搜索框（输入即搜，空查询恢复列表）+ 归档管理入口
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        self._history_search = QLineEdit()
        self._history_search.setPlaceholderText("搜索历史会话…")
        self._history_search.setClearButtonEnabled(True)
        self._history_search.setObjectName("historySearch")
        self._history_search_timer: Optional[QTimer] = None
        self._history_search.textChanged.connect(self._on_history_search_changed)
        search_row.addWidget(self._history_search, 1)
        archive_btn = QPushButton("归档")
        archive_btn.setToolTip("归档管理：恢复 / 清理已归档会话")
        archive_btn.setCursor(Qt.PointingHandCursor)
        archive_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        archive_btn.clicked.connect(self._open_archive_manager)
        search_row.addWidget(archive_btn)
        layout.addLayout(search_row)

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
        """历史会话右键菜单：修改标题 / 置顶 / 归档 / 删除（V2-E2 增加置顶与归档）。"""
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        pinned = self._is_pinned(path)
        act_pin = menu.addAction("取消置顶" if pinned else "置顶")
        act_rename = menu.addAction("修改标题")
        act_archive = menu.addAction("归档此会话")
        act_del = menu.addAction("删除此会话")
        act = menu.exec(gpos)
        if act == act_rename:
            self._rename_history_path(path)
        elif act == act_del:
            self._delete_history_path(path)
        elif act == act_archive:
            self._archive_history_path(path)
        elif act == act_pin:
            self._toggle_pin_history(path)

    # ------------------------------------------------------------ V2-E2: 置顶
    def _pinned_sessions_file(self) -> Path:
        return self._cfg.data_dir / "pinned_sessions.json"

    def _load_pinned_sessions(self) -> List[str]:
        try:
            p = self._pinned_sessions_file()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [str(x) for x in data]
        except Exception:  # noqa: BLE001
            pass
        return []

    def _save_pinned_sessions(self, items: List[str]) -> None:
        try:
            p = self._pinned_sessions_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("置顶会话保存失败: %s", exc)

    def _is_pinned(self, path: str) -> bool:
        return str(path) in self._load_pinned_sessions()

    def _toggle_pin_history(self, path: str) -> None:
        items = self._load_pinned_sessions()
        if str(path) in items:
            items.remove(str(path))
        else:
            items.insert(0, str(path))
        self._save_pinned_sessions(items)
        self._refresh_history_list()

    # ------------------------------------------------------------ V2-E2: 归档
    def _archive_dir(self) -> Path:
        return self._cfg.history_dir / "archived"

    def _archive_history_path(self, path: str) -> None:
        """把会话文件移动到 history/archived/（保留文件，可从归档恢复）。"""
        src = Path(path)
        if not src.exists():
            return
        if not styled_confirm(self, "归档会话",
                              "确定归档此会话？\n归档后从列表隐藏，可在「归档管理」中恢复。",
                              ok_text="归档"):
            return
        try:
            dst_dir = self._archive_dir()
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name
            # 归档区重名则加时间戳
            if dst.exists():
                dst = dst_dir / f"{src.stem}_{int(time.time())}{src.suffix}"
            src.replace(dst)
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "归档失败", str(exc))
            return
        self._refresh_history_list()
        styled_info(self, "归档会话", "已归档。可在历史搜索框旁的「归档管理」中恢复。")

    def _open_archive_manager(self) -> None:
        """归档管理对话框：列出已归档会话，支持恢复 / 删除 / 一键清理。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("归档管理")
        dialog.setModal(True)
        dialog.setMinimumSize(480, 380)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        hint = QLabel("已归档的会话不占用主列表；恢复后重新出现在历史列表。")
        hint.setStyleSheet(f"color:{TEXT_MID}; font-size:12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        list_widget = QListWidget()
        list_widget.setObjectName("archiveList")
        layout.addWidget(list_widget, 1)

        # 按钮行：清理 30 天 / 清理 90 天 / 恢复所选 / 删除所选 / 关闭
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _reload_list() -> None:
            list_widget.clear()
            adir = self._archive_dir()
            if not adir.exists():
                return
            for p in sorted(adir.glob("session_*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    title = data.get("title") or data.get("role") or p.stem
                    age_days = (time.time() - float(p.stat().st_mtime)) / 86400
                    item = QListWidgetItem(
                        f"{title} · {age_days:.0f} 天前 · {p.name}")
                    item.setData(Qt.UserRole, str(p))
                    list_widget.addItem(item)
                except Exception:  # noqa: BLE001
                    continue

        def _selected_paths() -> list:
            return [it.data(Qt.UserRole)
                    for it in list_widget.selectedItems() if it.data(Qt.UserRole)]

        def _restore() -> None:
            for p in _selected_paths():
                try:
                    Path(p).replace(self._cfg.conversations_dir / Path(p).name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("恢复归档失败 %s: %s", p, exc)
            _reload_list()
            self._refresh_history_list()

        def _delete_selected() -> None:
            if not styled_confirm(dialog, "删除归档",
                                  "确定删除所选归档会话？此操作不可恢复。",
                                  danger=True, ok_text="删除"):
                return
            for p in _selected_paths():
                try:
                    Path(p).unlink()
                except Exception:  # noqa: BLE001
                    pass
            _reload_list()

        def _cleanup(days: int) -> None:
            adir = self._archive_dir()
            if not adir.exists():
                return
            cutoff = time.time() - days * 86400
            removed = 0
            for p in adir.glob("session_*.json"):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        removed += 1
                except Exception:  # noqa: BLE001
                    continue
            styled_info(dialog, "归档清理",
                        f"已清理 {removed} 条超过 {days} 天的归档会话。")
            _reload_list()

        restore_btn = QPushButton("恢复所选")
        restore_btn.setStyleSheet(btn_qss(padding="6px 16px"))
        restore_btn.clicked.connect(_restore)
        del_btn = QPushButton("删除所选")
        del_btn.setStyleSheet(btn_qss(padding="6px 16px"))
        del_btn.clicked.connect(_delete_selected)
        clean30 = QPushButton("清理30天")
        clean30.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        clean30.clicked.connect(lambda: _cleanup(30))
        clean90 = QPushButton("清理90天")
        clean90.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        clean90.clicked.connect(lambda: _cleanup(90))
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        close_btn.clicked.connect(dialog.close)

        btn_row.addWidget(restore_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(clean30)
        btn_row.addWidget(clean90)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        _reload_list()
        dialog.exec()

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

        # V2-E4：会话内查找条（Ctrl+F 唤起，默认隐藏）
        self._find_bar = QFrame()
        self._find_bar.setObjectName("findBar")
        fh = QHBoxLayout(self._find_bar)
        fh.setContentsMargins(8, 4, 8, 4)
        fh.setSpacing(6)
        self._find_input = QLineEdit()
        self._find_input.setPlaceholderText("查找本会话…（回车下一个）")
        self._find_input.setObjectName("findInput")
        self._find_input.returnPressed.connect(lambda: self._find_next(False))
        fh.addWidget(self._find_input, 1)
        prev_btn = QPushButton("↑")
        prev_btn.setFixedSize(30, 28)
        prev_btn.setToolTip("上一个")
        prev_btn.setStyleSheet(ghost_btn_qss(radius=6, font_size=13))
        prev_btn.clicked.connect(lambda: self._find_next(True))
        next_btn = QPushButton("↓")
        next_btn.setFixedSize(30, 28)
        next_btn.setToolTip("下一个")
        next_btn.setStyleSheet(ghost_btn_qss(radius=6, font_size=13))
        next_btn.clicked.connect(lambda: self._find_next(False))
        close_btn = QPushButton("×")
        close_btn.setFixedSize(30, 28)
        close_btn.setToolTip("关闭查找（Esc）")
        close_btn.setStyleSheet(ghost_btn_qss(radius=6, font_size=13))
        close_btn.clicked.connect(self._toggle_find_bar)
        fh.addWidget(prev_btn)
        fh.addWidget(next_btn)
        fh.addWidget(close_btn)
        self._find_bar.hide()
        v.addWidget(self._find_bar)

        layout = QHBoxLayout()
        layout.setSpacing(8)

        attach_btn = QPushButton("附件")
        attach_btn.setFixedSize(64, 36)
        attach_btn.setToolTip(
            "选择文件或文件夹：文件可 Ctrl 多选（点「发送」时一起上传）；"
            "选中文件夹将作为技能产出目录（执行技能生成的文件写到这里，"
            "不选择则用本对话在 skilluserdata 下的默认项目文件夹）")
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
        # #技能名（skill库）唤醒弹窗——复用同一控件，仅数据不同
        self._pub_popup = SkillPopup(self)
        self._pub_popup.activated.connect(self._on_pub_selected)
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
        # 托盘图标与应用图标同源（用户配置 → 内置 img/logo_128x128.ico → 纯色兜底），
        # 不再自绘纯色方块（原先会显示成一个色块，与左下角窗口图标不一致）
        self._tray = QSystemTrayIcon(self._app_icon(), self)
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

        # ---------------------------- V2 接线（B5/B6）------------------------
        # 统一通知显示：notify_service 只做分发与幂等，此处负责展示（主项目风格弹窗）
        self._bus.notify_emitted.connect(self._on_notify_emitted)
        # 聚焦查询能力：供 notify_service 的 when_unfocused 策略使用（线程安全：读缓存标志）
        self._window_active_hint = True
        self._bus.handle("ui:window_active", self._ui_active_hint)

    def _ui_active_hint(self) -> bool:
        """能力 ui:window_active 的处理器：返回主窗口是否活跃（缓存标志）。"""
        return bool(getattr(self, "_window_active_hint", True))

    def _on_notify_emitted(self, title: str, body: str) -> None:
        """notify_emitted 槽：以主项目风格弹窗展示通知（槽在主线程执行）。"""
        try:
            from utils.styled_msg import styled_info
            text = title if not body else f"{title}\n{body}"
            styled_info(self, title or "通知", text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("通知展示失败: %s", exc)

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
        # （名字按归一化比较、别名一并传入；群聊还只保留第一位发言人的内容）
        if self._current_group:
            clean = ChatWorker._first_speaker_only(
                clean, self._current_members(),
                self._roles.group_aliases(self._current_group))
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
        self._fit_select_combo()

    def _fit_select_combo(self) -> None:
        """把顶部选择框撑到能完整显示最长条目（需求：角色名不被 … 截断）。

        `RoleManager.sidebar_label` 返回完整名称；只把**选择框**的最小宽度设够还不够
        —— 左栏比它窄时布局会把选择框压回去，Qt 依旧画省略号（用户反馈「名字被 …
        截断」）。所以这里同时把**左栏**放宽到放得下最长名字（按窗口宽度设上限，
        避免小窗口下挤扁聊天区）。
        """
        fm = self._select_combo.fontMetrics()
        widest = 0
        for i in range(self._select_combo.count()):
            widest = max(widest,
                         fm.horizontalAdvance(self._select_combo.itemText(i)))
        # 左右内边距 + 下拉箭头 + 一点余量（Qt 下拉框文字区 ≈ 宽度 - 36px）
        need = widest + 76
        target = min(need, _SELECT_MAX_W)
        self._select_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._select_combo.setMinimumWidth(max(target, 120))
        # 窗口很窄时仍可能被压回去：给悬浮提示兜底（里面是完整名字，不是省略号）
        self._select_combo.setToolTip(
            " / ".join(self._select_combo.itemText(i)
                       for i in range(self._select_combo.count())))
        panel = getattr(self, "_left_panel", None)
        if panel is not None:
            # 立绘 300 + 左右边距 32 = 332 是左栏底线；需要时再放宽
            budget = max(332, int(self.width() * 0.38))
            panel.setMinimumWidth(int(min(max(need + 32, 332),
                                          max(budget, 332))))

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

    def _on_roleplay_toggled(self, checked: bool) -> None:
        """开启/关闭角色扮演：开关状态持久化到配置 + 切换会话/记忆空间。"""
        self._roleplay_on = bool(checked)
        self._cfg.set(self._roleplay_on, "ui", "roleplay_enabled")
        self._cfg.save()
        # 同步到角色扮演预设（预设管理器与设置面板共用一份 enabled 状态）
        try:
            from roleplay.roleplay_preset import RolePlayPreset
            RolePlayPreset.instance().set(self._roleplay_on, "enabled")
            RolePlayPreset.instance().save()
        except Exception:  # noqa: BLE001
            pass
        self._status_label.setText(self._status_text())
        # 需求：开启角色扮演后「忘记其他对话」——切到角色扮演会话空间，
        # 关闭后回到日常会话空间，两侧会话互不显示。
        self._switch_session_for_mode()

    # ------------------------------------------------------------ 角色扮演隔离
    def _roleplay_per_session(self) -> bool:
        """预设「每个对话独立」开关：开启后每个新会话各自一份记忆。"""
        try:
            from roleplay.roleplay_preset import RolePlayPreset
            return bool(RolePlayPreset.instance().get(
                "isolation", "per_session", default=False))
        except Exception:  # noqa: BLE001
            return False

    def _roleplay_scope(self) -> str:
        """当前角色扮演会话作用域 ID。

        - 未开启角色扮演 → 空串（走日常命名空间）
        - 开启角色扮演但未开「每个对话独立」 → 空串（所有角色扮演会话共享记忆，
          但仍与非角色扮演对话隔离）
        - 开启「每个对话独立」 → 本会话唯一 ID（会话之间也不共享）
        """
        if not getattr(self, "_roleplay_on", False):
            return ""
        if not self._roleplay_per_session():
            return ""
        sid = getattr(self, "_roleplay_scope_id", "") or ""
        if not sid:
            sid = uuid.uuid4().hex[:8]
            self._roleplay_scope_id = sid
        return sid

    def _session_matches_mode(self, data: dict) -> bool:
        """会话是否属于当前空间（角色扮演 / 日常）。

        「角色扮演对话与日常对话互不读取」关闭时不做过滤，两个空间互通。
        """
        # 需求（用户 2026-09-22「没开角色扮演就不要调用任何角色扮演资料」）：
        # 旧实现无条件构造 RolePlayPreset 单例——它的构造函数会整份读盘
        # data/roleplay_preset.json（设定卡/规则卡/正则/世界书/采样，约 81KB），
        # 而本方法在**软件启动**刷新会话列表时就会被调用。
        # 现在未开启角色扮演时完全不碰预设，隔离行为按默认（isolate_normal=True）。
        iso: Dict[str, Any] = {}
        if bool(getattr(self, "_roleplay_on", False)):
            try:
                from roleplay.roleplay_preset import RolePlayPreset
                iso = RolePlayPreset.instance().get("isolation", default={}) or {}
            except Exception:  # noqa: BLE001
                iso = {}
        if not bool(iso.get("isolate_normal", True)):
            return True
        return bool(data.get("roleplay")) == bool(self._roleplay_on)

    def _find_latest_session(self, roleplay: bool) -> Optional[Path]:
        """在当前角色/群聊下找最近一条属于指定空间的会话。"""
        conv_dir = self._cfg.conversations_dir
        if not conv_dir.is_dir():
            return None
        best: Optional[tuple] = None
        for path in conv_dir.glob("session_*.json"):
            data = self._read_session_meta(path)
            if data is None:
                continue
            if bool(data.get("roleplay")) != bool(roleplay):
                continue
            if (data.get("role") or "") != self._current_role:
                continue
            if (data.get("group") or "") != (self._current_group or ""):
                continue
            try:
                ts = int(data.get("updated_at") or 0)
            except Exception:  # noqa: BLE001
                ts = 0
            if best is None or ts > best[0]:
                best = (ts, path)
        return best[1] if best else None

    def _switch_session_for_mode(self) -> None:
        """切换角色扮演开关后切到对应空间的会话（无则新建）。"""
        # 界面尚未构建完成（初始化时 setChecked 触发的信号）时直接跳过
        if not hasattr(self, "_messages") or not hasattr(self, "_history_list"):
            return
        try:
            self._save_session(quiet=True)
        except Exception:  # noqa: BLE001
            pass
        self._messages = []
        self._session_path = None
        self._roleplay_scope_id = ""
        self._prev_speaker = ""
        self._clear_chat_area()
        target = self._find_latest_session(bool(self._roleplay_on))
        if target is not None:
            try:
                self._on_history_clicked(str(target))
            except Exception:  # noqa: BLE001
                self._session_title.setText("新会话")
        else:
            self._session_title.setText("新会话")
        if hasattr(self, "_history_list"):
            self._refresh_history_list()

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
        # 配置里允许写**相对项目根**的路径（如 "./theme/test.png"）：
        # 相对路径一律按项目根解析（与 daily.py 的 _load_theme 一致），
        # 否则从别的目录启动 / 移动项目后就找不到文件，只能退回第一张主题。
        if chosen is not None and not chosen.is_absolute():
            chosen = project_root() / chosen
        if chosen is None or not chosen.exists():
            chosen = candidates[0]
        self._apply_theme(str(chosen))

    @staticmethod
    def _theme_config_value(path: str) -> str:
        """主题写入配置时，项目内的图片存**相对路径**（便携：换机器/换目录仍有效）。"""
        try:
            rel = Path(path).resolve().relative_to(project_root().resolve())
            return "./" + rel.as_posix()
        except Exception:  # noqa: BLE001 - 项目外/非法路径 → 原样存绝对路径
            return str(path)

    def _apply_theme(self, path: str) -> None:
        self._background_pm = (
            _blur_image(path, 24) if self._blur_bg else QPixmap(path))
        self._cfg.set(self._theme_config_value(path), "ui", "theme_file")
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
        # skillspub 管理器跟随主界面主题色（与技能工具管理器保持同风格）
        if getattr(self, "_skillpub_window", None) is not None:
            try:
                self._skillpub_window.apply_accent(color)
            except Exception:  # noqa: BLE001
                pass
        # 常用接口类 API 管理器跟随主界面主题色
        if getattr(self, "_api_vault_window", None) is not None:
            try:
                self._api_vault_window.apply_accent(color)
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
        # 立绘区：只更新最小尺寸（适度下限，避免窄面板/低高度窗口溢出挤压控件），
        # 实际显示由 _apply_portrait 按立绘区域实际尺寸等比适配（完整不裁剪、不变形）。
        _ps = min(scale, 1.2)
        self._portrait_label.setMinimumSize(
            max(220, int(300 * _ps)), max(300, int(400 * _ps)))
        self._fit_portrait()
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
        # 选项按钮行与折叠块同样跟随视口宽度，缩放后不会超出屏幕
        for row in self._chat_container.findChildren(ChoiceRow):
            row.set_max_width(max_w)
        for blk in self._chat_container.findChildren(CollapsibleBlock):
            blk.set_max_width(max_w)

    def resizeEvent(self, event: Any) -> None:  # noqa: D102
        super().resizeEvent(event)
        self._apply_scale()
        # 需求：窗口缩放/全屏时立绘等比跟随，保证左上角图片完整显示不被裁剪。
        # 全屏有过渡动画，布局尺寸分阶段变化：立即校正一次，再延迟两次兜底，
        # 确保最终尺寸（尤其是全屏动画结束）时立绘依然完整适配。
        QTimer.singleShot(0, self._fit_portrait)
        QTimer.singleShot(150, self._fit_portrait)
        QTimer.singleShot(400, self._fit_portrait)

    def showEvent(self, event: Any) -> None:  # noqa: D102
        super().showEvent(event)
        # V2：主窗口活跃标志（通知聚焦策略 ui:window_active 使用）
        self._window_active_hint = True
        # 需求：小屏窗口首次打开时立绘也要完整显示 —— 初始 _set_portrait 时
        # 立绘 label 尚未布局（尺寸为 0），此时生成的图可能大于实际立绘区域
        # 导致显示不全；窗口首次显示后布局已就绪，延迟几次按真实尺寸校正。
        QTimer.singleShot(0, self._fit_portrait)
        QTimer.singleShot(120, self._fit_portrait)
        QTimer.singleShot(350, self._fit_portrait)
        # 需求：角色名完整显示 —— 建窗口时字体/QSS 还没生效，那次测量偏小；
        # 显示后（真实字体已确定）再按最长条目重算一次选择框与左栏宽度。
        QTimer.singleShot(0, self._fit_select_combo)
        QTimer.singleShot(150, self._fit_select_combo)

    def _portrait_available_height(self) -> int:
        """估算立绘区可用的最大高度：面板高度 − 立绘区下方控件总高。

        控件高度优先取实际布局高度，未布局时用 sizeHint 兜底，
        保证锁定立绘高度时不会把下方控件挤出窗口（小屏空间不足时保护）。
        """
        label = getattr(self, "_portrait_label", None)
        if label is None:
            return 0
        panel = label.parentWidget()
        if panel is None or panel.layout() is None:
            return 0
        layout = panel.layout()
        used = 0.0
        count = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w is None or w is label:
                continue
            if w.isHidden():
                continue
            h = w.height()
            if h <= 0:
                h = w.sizeHint().height()
            used += max(h, 1)
            count += 1
        sp = layout.spacing()
        if sp > 0 and count > 0:
            used += sp * count
        m = layout.contentsMargins()
        used += m.top() + m.bottom()
        return max(0, int(panel.height() - used - 4))

    def _fit_portrait(self) -> None:
        """立绘完整适配：锁定立绘区高度 ≈ 宽度 × 原图比例，图完整填满。

        需求：全屏后立绘区若被 stretch 无限拉高会变成瘦长形，图片按宽等比
        缩放后上下留白过大；这里把立绘区高度锁定为"宽度×原图比例"（默认
        3:4），让图完整且尽量填满区域、不变形。可用高度不足（小屏）时收缩
        到可用高度，保证不挤压下方控件；图始终按区域等比完整显示。
        """
        label = getattr(self, "_portrait_label", None)
        if label is None:
            return
        w = label.width()
        if w <= 0:
            # 布局尚未就绪：延迟重试，等尺寸确定后再锁定/适配
            QTimer.singleShot(60, self._fit_portrait)
            return
        pm = getattr(self, "_portrait_pixmap", None)
        ratio = 4.0 / 3.0
        if pm is not None and not pm.isNull():
            ratio = pm.height() / max(pm.width(), 1)
        target = int(w * ratio)
        avail = self._portrait_available_height()
        if avail > 0:
            target = min(target, avail)
        target = max(200, target)
        if label.minimumHeight() != target or label.maximumHeight() != target:
            label.setFixedHeight(target)
            # 高度锁定会触发布局重排，label 尺寸再变 → 调度一次收敛，
            # 尺寸稳定后不再设置 fixedHeight，循环自然结束。
            QTimer.singleShot(0, self._fit_portrait)
            return
        self._apply_portrait(animate=False)

    def _app_icon(self) -> QIcon:
        """窗口 / 托盘图标：统一走模块级 `app_icon()`（配置 → 内置 logo → 兜底）。"""
        return app_icon(str(self._cfg.get("ui", "icon_path", default="") or ""))

    def _apply_icon(self) -> None:
        """应用 ICO 小图标（窗口 + 托盘）。配置为空/失效时使用内置 logo。"""
        icon = self._app_icon()
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
        _inp = getattr(self, "_input", None)
        _pop = getattr(self, "_skill_popup", None)
        # ① 技能弹窗打开时的键盘劫持（优先级最高：回车=选中技能，不触发发送）
        if (_inp is not None and obj is _inp and event.type() == event.Type.KeyPress
                and self._skill_menu_open and _pop is not None
                and _pop.isVisible()):
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
        # ①b skill库 #技能名 弹窗键盘（与 @ 技能弹窗同优先级）
        _ppop = getattr(self, "_pub_popup", None)
        if (_inp is not None and obj is _inp and event.type() == event.Type.KeyPress
                and self._pub_menu_open and _ppop is not None
                and _ppop.isVisible()):
            if event.key() == Qt.Key_Up:
                self._pub_popup.move_selection(-1)
                return True
            if event.key() == Qt.Key_Down:
                self._pub_popup.move_selection(1)
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and not (event.modifiers() & Qt.ShiftModifier):
                self._on_pub_selected()
                return True
            if event.key() == Qt.Key_Escape:
                self._close_pub_menu()
                return True
            # 其余按键放行：由 textChanged / cursorPositionChanged 实时刷新过滤
            return False
        # ② 输入框按键
        if _inp is not None and obj is _inp and event.type() == event.Type.KeyPress:
            # V2-E4：Ctrl+F 唤起会话内查找
            if event.key() == Qt.Key_F and (event.modifiers() & Qt.ControlModifier):
                if self._find_bar.isHidden():
                    self._toggle_find_bar()
                return True
            # V2-E4：Esc 关闭查找条并清除高亮
            if event.key() == Qt.Key_Escape and self._find_bar.isVisible():
                self._toggle_find_bar()
                return True
            # 输入框按回车（不带 Shift）发送
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and not (event.modifiers() & Qt.ShiftModifier):
                self._close_skill_menu()
                self._close_pub_menu()
                self._on_send()
                return True
            # 输入 \@ 唤醒技能/工具菜单（由 _skill_should_trigger 判定前一个字符为 \）
            if event.text() == "@" and not (
                    event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
                if self._skill_should_trigger():
                    self._close_pub_menu()
                    self._skill_menu_open = True
                    QTimer.singleShot(0, self._refresh_skill_popup)
                return False
            # 输入 # 唤醒 skill库（#技能名 直接点名调用）选择菜单。
            # 需已开启「允许调用 skillpub」；此前缀若不是正常「#技能」语境则不弹出
            if event.text() == "#" and not (
                    event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
                if bool(self._cfg.get("ui", "skillspub_enabled", default=True)) \
                        and self._pub_should_trigger():
                    self._close_skill_menu()
                    self._pub_menu_open = True
                    QTimer.singleShot(0, self._refresh_pub_popup)
                return False
            # 光标位于 @技能 / #技能名 标签右侧按退格 → 整块删除标签
            if event.key() == Qt.Key_Backspace and (
                    self._skill_tag_backspace() or self._pub_tag_backspace()):
                return True
        # 输入框失焦关闭 @技能 / #skill库 弹窗
        if _inp is not None and obj is _inp and event.type() == event.Type.FocusOut:
            self._close_skill_menu()
            self._close_pub_menu()
        # ④ 立绘 label 尺寸变化 → 立即重新适配（节流合并连续事件），
        # 保证小屏/全屏/窗口缩放任何尺寸下图完整显示、不裁剪。
        if obj is getattr(self, "_portrait_label", None) \
                and event.type() == event.Type.Resize:
            QTimer.singleShot(0, self._fit_portrait)
            return False
        # ③ 无边框窗口四边/四角缩放
        if self._resize_event(obj, event):
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ V2-C3: 技能热加载
    def _setup_skill_watcher(self) -> None:
        """监听 skills/ 目录，技能 JSON 保存后自动重载（不重启生效）。"""
        try:
            self._skill_watcher = QFileSystemWatcher(self)
            sk_dir = self._cfg.skills_dir
            if sk_dir.exists():
                self._skill_watcher.addPath(str(sk_dir))
                for fn in ("tools_list.json", "skilltools_information.json"):
                    p = sk_dir / fn
                    if p.exists():
                        self._skill_watcher.addPath(str(p))
            self._skill_watcher.directoryChanged.connect(self._on_skills_changed)
            self._skill_watcher.fileChanged.connect(self._on_skills_changed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能热加载监听失败（可继续使用）: %s", exc)

    def _on_skills_changed(self, _path: str = "") -> None:
        """技能文件变化：防抖 800ms 后重载。"""
        if self._skill_watch_timer is not None:
            self._skill_watch_timer.stop()
        self._skill_watch_timer = QTimer(self)
        self._skill_watch_timer.setSingleShot(True)
        self._skill_watch_timer.timeout.connect(self._reload_skills)
        self._skill_watch_timer.start(800)

    def _reload_skills(self) -> None:
        """强制重载技能并恢复文件监听（原子替换会丢失 inode 监听）。"""
        try:
            self._skill_mgr.reload(force=True)
            logger.info("技能目录已热加载")
            if self._skill_watcher is not None:
                sk_dir = self._cfg.skills_dir
                for p in (sk_dir,
                          sk_dir / "tools_list.json",
                          sk_dir / "skilltools_information.json"):
                    try:
                        if p.exists() and str(p) not in self._skill_watcher.directories() \
                                and str(p) not in self._skill_watcher.files():
                            self._skill_watcher.addPath(str(p))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能热加载失败: %s", exc)

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

    # ------------------------------------------------------------ 输入框变化
    # 需求（用户）：「重启软件不要再把上次没发出去的话留在输入框」——
    # 已移除 V2-E5 的输入草稿持久化（曾把输入框内容按会话写入
    # data/input_drafts.json 并在启动/切会话时回填）。现在输入框只反映
    # 当前真实输入，重启即为空。

    def _on_input_changed(self) -> None:
        """输入框文本/光标变化时实时刷新技能/skill库弹窗（仅当菜单打开时）。"""
        if self._skill_menu_open:
            self._refresh_skill_popup()
            return
        if getattr(self, "_pub_menu_open", False):
            self._refresh_pub_popup()

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
        """根据已解析的技能列表构建注入 LLM 的上下文（含专业模式语气约束）。

        skill库内部开关（\\@skillspub / \\@autoskills）的说明不在此重复注入，
        由 ChatWorker 按 skillspub_core 统一构造指引与详细介绍。
        """
        parts = []
        for s in tags:
            if str(s.get("trigger") or "").strip().lower() in (
                    "skillspub", "autoskills"):
                continue
            p = self._skill_mgr.build_context(s)
            if p:
                parts.append(p)
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

    # ------------------------------------------------------------ skill库 #技能名 菜单
    # 需求：#技能名（skill库直接点名调用，一次可多个组合）。交互与 \@技能 弹窗一致：
    # 输入「#」唤出技能列表；继续输入按名称/关键词过滤；回车选择；退格整块删除标签。
    #: 「#技能名」允许出现在这些字符之后（行首/空白/常见中英标点、斜杠、反斜杠）
    _PUB_PREV_OK = "，。；、！？,.!?;:：\"'')]}》】…—·/\\"

    def _pub_should_trigger(self) -> bool:
        """判断刚输入的 # 是否处于正常的「#技能」语境（行首/空白/标点之后）。

        ⚠ 本函数在 # 的 KeyPress **事件处理里**调用 —— 此刻 # 还没进文档，
        文档里光标前一位是**上一个字符**。旧实现却断言
        ``characterAt(pos - 1) == '#'``（等于说「# 必须在文档里」），对真实键盘
        输入永远为假 → 弹窗根本不弹（用户反馈「/# 没有出现类似 \\@ 的补全菜单」）。
        现在与 :meth:`_skill_should_trigger`（看前一位是否是 ``\\``）同一套语义：
        看**光标前一个字符**是否为允许的前缀。
        """
        cursor = self._input.textCursor()
        pos = cursor.position()
        if pos < 1:                       # 行首
            return True
        prev = self._input.document().characterAt(pos - 1)
        if not prev or prev in ("\u2029", "\u2028", "\n", "\r"):
            return True                   # 段落分隔符（Qt 内部段落记 U+2029）
        return prev.isspace() or prev in self._PUB_PREV_OK

    def _pub_before_token(self):
        """光标前「#片段」的正则匹配（含 #）；不在合法语境时返回 None。"""
        cursor = self._input.textCursor()
        doc = self._input.document()
        before = (doc.toPlainText()[:cursor.position()]
                  .replace("\u2029", "\n").replace("\u2028", "\n"))
        m = re.search(r"#([A-Za-z0-9_\u4e00-\u9fa5-]*)$", before)
        if m is None:
            return None
        if m.start() > 0:
            prev = before[m.start() - 1]
            if prev and not prev.isspace() and prev not in self._PUB_PREV_OK:
                return None
        return m

    def _refresh_pub_popup(self) -> None:
        """按光标前文本实时过滤 skill库 技能并刷新/隐藏 #技能 弹窗。"""
        if not getattr(self, "_pub_menu_open", False) or self._pub_popup is None:
            return
        # 需求：关闭「允许调用 skillpub」后不再弹 #技能 列表
        if not bool(self._cfg.get("ui", "skillspub_enabled", default=True)):
            self._close_pub_menu()
            return
        m = self._pub_before_token()
        if m is None:
            self._close_pub_menu()
            return
        items: List[Dict[str, Any]] = []
        try:
            from skillspub_core import match_prefix
            items = [{"name": e["name"], "description": e.get("summary", "")}
                     for e in match_prefix(m.group(1))]
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill库过滤失败: %s", exc)
        if not items:
            self._close_pub_menu()
            return
        cr = self._input.cursorRect()
        global_pos = self._input.mapToGlobal(cr.bottomLeft())
        self._pub_popup.set_skills(items)
        self._pub_popup.show_at(global_pos)

    def _close_pub_menu(self) -> None:
        """关闭 skill库 #技能 弹窗并复位状态。"""
        self._pub_menu_open = False
        if getattr(self, "_pub_popup", None) is not None:
            self._pub_popup.hide()

    def _on_pub_selected(self, entry: Optional[Dict[str, Any]] = None) -> None:
        """选中 skill库 技能：#技能名 以高亮标签插入输入框。"""
        if entry is None:
            entry = (self._pub_popup.selected_skill()
                     if getattr(self, "_pub_popup", None) is not None else None)
        if not entry:
            self._close_pub_menu()
            return
        name = str(entry.get("name") or "").strip()
        if not name:
            self._close_pub_menu()
            return
        cursor = self._input.textCursor()
        end = cursor.position()
        start = end
        m = self._pub_before_token()
        if m is not None:
            start = end - len(m.group(0))
        self._close_pub_menu()   # 先关闭，避免插入触发 textChanged 重新弹出
        self._insert_pub_tag(start, end, name)

    def _insert_pub_tag(self, start: int, end: int, name: str) -> None:
        """以高亮字符格式无损插入 #技能名 片段（末尾补一个空格便于后续输入）。"""
        cursor = self._input.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#e7f6e9"))
        fmt.setForeground(QColor(ACCENT))
        fmt.setFontWeight(QFont.Bold)
        cursor.insertText("#" + name + " ", fmt)
        cursor.clearSelection()
        self._input.setTextCursor(cursor)
        self._input.setCurrentCharFormat(QTextCharFormat())

    #: 「看起来想点名技能、但技能库里没有」的拉丁 slug（用于提示，不打扰中文 #话题）
    _MISSING_PUB_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_-]{3,})")

    def _missing_pub_tokens(self, text: str) -> List[str]:
        """挑出文本里「疑似 #技能名 但技能库中没有」的候选（去重保序）。

        需求（用户反馈「`/#tothestars` 失效」）：不存在的技能名此前会被
        `skillspub_core` 的模糊匹配**静默错认**成别的技能（`#tothestars` →
        `treatment-plans`），或当普通文字吞掉，用户完全不知道发生了什么 ——
        真实会话里模型还反问过「这轮挂上的技能跟天体核查不是一回事」。
        现在发送时提示一次。

        只提示**拉丁 slug（≥4 字符）**：中文 `#话题`、代码里的 `C#` 等正常文本不打扰；
        技能库读取异常时静默跳过（绝不因提示功能影响发消息）。
        """
        try:
            from skillspub_core import resolve_skill_token
        except Exception:  # noqa: BLE001
            return []
        out: List[str] = []
        for m in self._MISSING_PUB_RE.finditer(text or ""):
            token = m.group(1)
            if token in out:
                continue
            try:
                if resolve_skill_token(token) is not None:
                    continue
            except Exception:  # noqa: BLE001
                return []
            out.append(token)
        return out

    def _pub_tag_backspace(self) -> bool:
        """退格键：光标位于 skill库 #技能名 标签右侧时整块删除标签。"""
        cursor = self._input.textCursor()
        if cursor.hasSelection():
            return False
        pos = cursor.position()
        if pos <= 0:
            return False
        doc = self._input.document()
        before = (doc.toPlainText()[:pos]
                  .replace("\u2029", "\n").replace("\u2028", "\n"))
        # 末尾允许带一个「标签自带空格」：光标紧跟标签后的空格时一次退格整块删除
        m = re.search(r"#([A-Za-z0-9_\u4e00-\u9fa5-]+)( ?)$", before)
        if m is None:
            return False
        tag = m.group(0)
        try:
            from skillspub_core import resolve_skill_token
            if resolve_skill_token(m.group(1)) is None:
                return False
        except Exception:  # noqa: BLE001
            return False
        c2 = self._input.textCursor()
        c2.setPosition(pos - len(tag))
        c2.setPosition(pos, QTextCursor.KeepAnchor)
        c2.removeSelectedText()
        self._input.setTextCursor(c2)
        return True

    def _retain_pub_names(self, names: List[str]) -> None:
        """发送后把本次调用的 #技能名 重新插入输入框（保留高亮格式）。"""
        for name in names:
            cursor = self._input.textCursor()
            cursor.movePosition(QTextCursor.End)
            pos = cursor.position()
            self._insert_pub_tag(pos, pos, name)

    def _parse_pub_command(self, text: str) -> List[Dict[str, Any]]:
        """解析文本中的 #技能名 → skill库 技能条目（按出现顺序去重；未命中为空）。"""
        try:
            from skillspub_core import parse_pub_tags
            return parse_pub_tags(text or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill库 #技能名 解析失败: %s", exc)
            return []

    def _strip_pub_display(self, text: str) -> str:
        """剥离被识别为 #技能名 的片段（供展示/开关关闭时清掉指令文本）。"""
        if not text:
            return text
        try:
            from skillspub_core import strip_pub_tags
            return strip_pub_tags(text)
        except Exception:  # noqa: BLE001
            return text

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
        # 需求：\@project 指令——查看/更改本对话在 skilluserdata 下的项目名（文件夹名）
        if _PROJECT_CMD_RE.match(text):
            self._input.clear()
            self._handle_project_command(text)
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
        # V2-C4：技能权限审批——review 档（高风险）技能发送前需用户确认
        review_skill = next(
            (s for s in tags
             if str(s.get("permission") or "").strip().lower() == "review"), None)
        if review_skill is not None:
            sname = (review_skill.get("name")
                     or review_skill.get("trigger") or "技能")
            trig = str(review_skill.get("trigger") or "")
            if not styled_confirm(
                    self, "技能授权",
                    f"技能「{sname}」（\\@{trig}）属于高风险操作（如写文件 / "
                    f"执行命令），执行前需要你确认。\n\n是否继续发送这条消息？",
                    ok_text="允许执行"):
                self._input.clear()
                self._append_notice(f"已取消调用技能「{sname}」。")
                return
        image_skill = next(
            (s for s in tags
             if bool(s.get("image_gen"))
             or str(s.get("trigger") or "").lower() in _IMAGE_GEN_TRIGGERS),
            None)
        # V2-D2：文件工具技能（\@read / \@write / \@edit）由程序直接执行
        file_skill = next(
            (s for s in tags
             if str(s.get("trigger") or "").strip().lower()
             in ("read", "write", "edit")), None)
        if file_skill is not None and not pending:
            self._input.clear()
            self._clear_pending_attachments()
            self._handle_file_skill(file_skill, clean)
            # 需求：发送后把本次 \@触发词 留在输入框，下次继续输入内容即可再次使用
            # —— 与普通对话路径一致，文件工具也要保留标签，等用户手动删除才消失
            if not closed_now:
                self._retain_skill_tags(tags)
            return
        # V3：程序侧技能（\@novellearn / \@weather / \@calorie / \@ponywebsite）
        # 由程序直接执行，不进入普通对话（与 \@read / \@write 一样即时处理）
        prog_skill = next(
            (s for s in tags
             if str(s.get("trigger") or "").strip().lower()
             in ("novellearn", "weather", "calorie", "ponywebsite")), None)
        if prog_skill is not None:
            self._input.clear()
            self._clear_pending_attachments()
            self._handle_program_skill(prog_skill, clean, pending)
            # 需求：程序侧技能执行完仍把本次 \@触发词 留在输入框，便于连续调用
            if not closed_now:
                self._retain_skill_tags(tags)
            return
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
        # 需求：关闭「允许调用 skillpub」后 \\@skillspub / \\@autoskills 指令失效，
        # 发送后也不再保留这两个开关标签（避免关闭状态仍被自动"挂在"输入框里）
        if retain and not closed_now and not bool(
                self._cfg.get("ui", "skillspub_enabled", default=True)):
            retain = [t for t in retain
                      if str(t.get("trigger") or "").strip().lower()
                      not in ("skillspub", "autoskills")]
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
        # 需求：skill库（skillspub）——\\@skillspub=manual（手动自由调用）/
        # \\@autoskills=auto（AI 自动挑选组合），空串表示未开启
        skillspub_mode = ""
        try:
            from skillspub_core import mode_from_tags
            skillspub_mode = mode_from_tags(tags)
        except Exception:  # noqa: BLE001 - skillspub 数据异常不应阻塞对话
            skillspub_mode = ""
        if skillspub_mode and not bool(
                self._cfg.get("ui", "skillspub_enabled", default=True)):
            # 需求：关闭「允许调用 skillpub」后，\\@skillspub / \\@autoskills
            # 指令同样失效（AI 不会主动调用 skill），标签仅作普通文字发送
            skillspub_mode = ""
        # 需求：#技能名（skill库 直接点名调用，可一次多个组合）——
        # 仅当「允许调用 skillpub」开启且未输入 \closed 时生效；
        # 关闭时该指令失效，用户不能发起 skill 调用，#片段按普通文字处理。
        skillspub_pub_enabled = bool(
            self._cfg.get("ui", "skillspub_enabled", default=True))
        pub_names: List[str] = []
        if closed_now:
            # 与 \@ 技能标签一致：\closed 状态下 # 指令一并剥离，不再触发
            clean = self._strip_pub_display(clean)
        elif skillspub_pub_enabled:
            try:
                from skillspub_core import parse_pub_tags
                pub_names = [e["name"] for e in parse_pub_tags(text)]
            except Exception as exc:  # noqa: BLE001
                logger.warning("skill库 #技能名 解析失败: %s", exc)
                pub_names = []
        else:
            stripped = self._strip_pub_display(clean)
            if stripped != clean:
                clean = stripped
                self._append_notice(
                    "「允许调用 skillpub」当前处于关闭状态（可到 "
                    "设置 → skill库管理器 开启），本条消息中的 #技能名 指令"
                    "已失效，作为普通文字发送。")
        # 需求（用户反馈「/#tothestars 失效」）：技能库里**没有**这个技能名时，
        # 明确提示，而不是把 #片段 当普通文字静默发出去（此前还会被模糊匹配错认成
        # 别的技能，模型收到完全不相干的说明）。
        if skillspub_pub_enabled and not closed_now:
            _missing = self._missing_pub_tokens(text)
            if _missing:
                self._append_notice(
                    "skill库 里没有这些技能：" + "、".join(_missing)
                    + "。可在输入框输入 # 从列表里选择，或到「设置 → skill库管理器」"
                      "确认已安装的技能名（本条消息按普通文字继续发送）。")
        # 需求（用户反馈「#tothemoon 查询天象不该输出查询地址，应直接读本机 IP 取数」）：
        # `#tothemoon` 属**程序侧**的 skill库 技能 —— 由程序读本机 IP → 取 7timer 观测
        # 条件 → 直接出数据卡片，不进普通对话（与 `\@weather` 同一套体验）。
        # 需求（用户反馈「tothestars 也说查不到」）：`#tothestars` 的瞬变天体核查与
        # VSX 检索由**程序**取数（Rochester 表抓取比对、VizieR 检索 VSX、链接生成），
        # 结果注入上下文后交给模型解读 —— 模型不再写「我核查不了」。
        if "tothestars" in pub_names and skillspub_pub_enabled and not closed_now:
            self._input.clear()
            self._clear_pending_attachments()
            self._start_transient_chat(clean, pending, skill_ctx, pub_names)
            # 需求：把本次 #技能名 留在输入框，便于连续调用同一程序侧 skill
            self._retain_pub_names(pub_names)
            return
        _prog_pub = [n for n in pub_names if n in self.PROGRAM_PUB_SKILLS]
        if _prog_pub and skillspub_pub_enabled and not closed_now:
            self._input.clear()
            self._clear_pending_attachments()
            # `#tothemoon` 分两路（需求：先定位，再按提示词选择输出内容）：
            #   · 「查询观测条件」→ 程序直接出数据卡片（日月出没/月中天/亮度 + 7timer）
            #   · 「查询天象 / 查询专业天象」→ 交由模型组织清单（天象事件要检索与筛选），
            #     但本机 IP 归属地带进 system 上下文，模型不必再问城市
            if "tothemoon" in _prog_pub and self._sky_wants_llm(clean):
                self._start_sky_chat(clean, pending, skill_ctx, pub_names)
                # 需求：把本次 #技能名 留在输入框，便于连续调用同一程序侧 skill
                self._retain_pub_names(pub_names)
                return
            self._handle_program_pub_skill(_prog_pub, clean, pub_names)
            # 需求：把本次 #技能名 留在输入框，便于连续调用同一程序侧 skill
            self._retain_pub_names(pub_names)
            return
        # 若 # 指令被剥离后无实际内容、也无附件，则不调用 LLM
        if not clean.strip() and not pending and not pub_names:
            self._input.clear()
            return
        # 保留 #技能名 标签（与 \@ 标签一致：留在输入框，便于连续调用）
        if not closed_now and skillspub_pub_enabled and pub_names:
            self._retain_pub_names(pub_names)
        # 需求：处理「文件项目」类任务但无任何来源（附件 / 项目路径）时不扫盘，
        # 提示用户先添加文件或路径
        if (not pending and not self._all_project_paths()
                and self._needs_file_project(clean)):
            self._input.clear()
            self._append_notice(
                "未检测到文件或路径：请先通过「附件」添加文件，"
                "或点右上角「选择路径」选择项目文件夹，再处理文件项目。")
            return
        # 需求：调用技能前确保本对话有技能产出文件夹
        # （尚未建立则询问是否允许生成文件；pub_names 此时已解析完成）
        if skillspub_mode or pub_names:
            self._ensure_project_dir(ask=True)
        try:
            self._start_chat(clean, pending, skill_context=skill_ctx,
                             pro_active=pro_active or bool(pub_names),
                             api_override=api_override,
                             thinking_mode=thinking_mode,
                             web_search_query=web_search_query,
                             skillspub_mode=skillspub_mode,
                             skillspub_names=pub_names)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"发送失败：{exc}")

    def _on_attach(self) -> None:
        """添加附件：仅加入待上传列表（不立即发送），编辑完文字点「发送」一起发出。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件（可多选，点「发送」时一起上传）", "",
            "所有文件 (*)")
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
        # 需求：技能产出文件夹（附件栏「文件夹」选择的工作文件夹）显示为可移除 chip
        workdir = str(getattr(self, "_skill_workdir", "") or "")
        has_wd = bool(workdir) and Path(workdir).is_dir()
        if has_wd:
            wcard = QFrame()
            wcard.setObjectName("attachChip")
            wrow = QHBoxLayout(wcard)
            wrow.setContentsMargins(8, 3, 4, 3)
            wrow.setSpacing(6)
            wrow.addWidget(QLabel("文件夹"))
            wname = QLabel(f"技能产出：{Path(workdir).name or workdir}")
            wname.setToolTip(f"{workdir}\n执行技能时生成的文件写到这里")
            wname.setWordWrap(True)
            wname.setMaximumWidth(180)
            wrow.addWidget(wname, 1)
            wrm = QPushButton("×")
            wrm.setCursor(Qt.PointingHandCursor)
            wrm.setToolTip("恢复使用本对话默认的技能产出文件夹")
            wrm.setStyleSheet(
                "QPushButton{background:transparent; color:#8b93a7; border:none;"
                " font-size:12px; padding:2px 8px;}"
                "QPushButton:hover{color:#e11d48;}")
            wrm.clicked.connect(lambda _=False: self._clear_workdir())
            wrow.addWidget(wrm)
            wcard.setStyleSheet(
                "QFrame#attachChip{background:rgba(108,142,245,0.10);"
                " border:1px solid #d5ddf7; border-radius:8px;}"
                "QLabel{color:#3a4a80; font-size:12px; background:transparent;"
                " border:none;}")
            self._attach_layout.addWidget(wcard)
        # 需求：项目路径（「选择路径」选择的读/学习路径 + skillpub 自动生成的）
        # 显示为可移除 chip
        for _pp in self._all_project_paths():
            _pcard = QFrame()
            _pcard.setObjectName("attachChip")
            _prow = QHBoxLayout(_pcard)
            _prow.setContentsMargins(8, 3, 4, 3)
            _prow.setSpacing(6)
            _prow.addWidget(QLabel("项目路径"))
            _pname = QLabel(Path(_pp).name or _pp)
            _pname.setToolTip(_pp)
            _pname.setWordWrap(True)
            _pname.setMaximumWidth(180)
            _prow.addWidget(_pname, 1)
            _prm = QPushButton("×")
            _prm.setCursor(Qt.PointingHandCursor)
            _prm.setToolTip("移除该项目路径")
            _prm.setStyleSheet(
                "QPushButton{background:transparent; color:#8b93a7; border:none;"
                " font-size:12px; padding:2px 8px;}"
                "QPushButton:hover{color:#e11d48;}")
            _prm.clicked.connect(
                lambda _=False, pp=_pp: self._remove_project_path(pp))
            _prow.addWidget(_prm)
            _pcard.setStyleSheet(
                "QFrame#attachChip{background:rgba(108,142,245,0.10);"
                " border:1px solid #d5ddf7; border-radius:8px;}"
                "QLabel{color:#3a4a80; font-size:12px; background:transparent;"
                " border:none;}")
            self._attach_layout.addWidget(_pcard)
        self._attach_bar.setVisible(
            bool(self._pending_attachments) or has_wd
            or bool(self._all_project_paths()))

    def _remove_pending_attachment(self, path: str) -> None:
        """从待上传列表中移除单个附件。"""
        if path in self._pending_attachments:
            self._pending_attachments.remove(path)
        self._refresh_attach_bar()

    def _remove_project_path(self, path: str) -> None:
        """从项目路径中移除单个路径（用户选的或 skillpub 自动生成的）。"""
        if path in self._project_paths:
            self._project_paths.remove(path)
        if path in self._auto_paths:
            self._auto_paths.remove(path)
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
        bubble.setTextFormat(Qt.RichText)
        bubble.setText(self._image_html(str(result_path)))
        # 需求：链接可点击（内部去重，不会重复连接信号）
        self._enable_bubble_links(bubble)
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
                    web_search_query: str = "",
                    skillspub_mode: str = "",
                    skillspub_names: Optional[List[str]] = None) -> None:
        self._busy = True
        self._send_btn.setEnabled(False)
        # 新对话开始：取消上一轮可能残留的 5 秒强制恢复计时器，避免其误清理新请求
        _fr = getattr(self, "_force_recover_timer", None)
        if _fr is not None:
            _fr.stop()
            self._force_recover_timer = None
        self._reasoning_text: List[str] = []   # 本轮模型原生思维链累积
        # 需求：思维链模式（\@thinking 或角色扮演）——思考过程一律进思维链折叠块，
        # 【回答】/【正式回答】之后的内容才是正式正文
        # 修复（用户 2026-09-22「设置里开了思维链，不写 \@thinking 也不生效」）：
        # 旧实现里 `ui.show_thinking` 只影响**折叠块显示**，不参与
        # `_thinking_mode` → `_COT_PROTOCOL` 注入，所以「只开设置」时模型根本
        # 没被要求按思维链协议输出。现在把设置一并算作「启用思维链」。
        _show_thinking = False
        try:
            _show_thinking = bool(self._cfg.show_thinking())
        except Exception:  # noqa: BLE001
            _show_thinking = False
        self._thinking_mode = (bool(thinking_mode)
                               or bool(getattr(self, "_roleplay_on", False))
                               or _show_thinking)
        # 需求：本轮是否**用户点名**用了 \@thinking —— 这种情况下即使设置里
        # 「显示思维链」是关的，也必须把折叠块显示出来（用户反馈「\@thinking
        # 无法正确开启思维链的显示」），并存进消息记录供会话重载后回放
        self._thinking_skill = bool(thinking_mode)
        # 传给工作线程的思维链开关：\@thinking 或（设置开启）都要注入 COT 协议
        _worker_thinking = bool(thinking_mode) or _show_thinking
        self._stream_cot_extra = ""            # 流式期间从 HTML 注释抽出的思考
        # 右上角小字：新的一轮开始，清空「本轮」用量（总用量继续累计）
        self._reset_turn_stats()
        self._stat_turn_live = True
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
        # 需求：调用 skillpub（\\@skillspub / \\@autoskills）或使用 #技能名 直接点名
        # 调用 skill库 技能时，与调用工具一致——左侧立绘显示 <角色名>-work.png。
        pro_active = bool(pro_active or skillspub_names or skillspub_mode)
        pro_changed = self._pro_skill_active != pro_active
        self._pro_skill_active = pro_active
        if pro_changed:
            self._set_portrait(
                self._current_role,
                self._roles.default_emotion(self._current_role))
        self._last_stream_render = 0.0
        self._bubble_refresh_pending = False   # 合并渲染窗口状态
        self._last_full_scroll = 0.0           # 滚动合并节流状态

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
            thinking_mode=_worker_thinking, web_search_query=web_search_query,
            # 需求：skill库（skillspub）——manual/auto 两种调用模式 + #技能名 直接点名
            skillspub_mode=skillspub_mode,
            skillspub_names=skillspub_names or [],
            # 需求：角色扮演——传入主界面开关状态、预设勾选的设定卡与会话作用域
            # 修复（用户 2026-09-22）：未开启角色扮演时不再读取角色扮演相关配置
            roleplay_enabled=self._roleplay_on,
            roleplay_cards=(
                (self._cfg.get("ui", "roleplay_cards", default=[]) or [])
                if self._roleplay_on else []),
            roleplay_scope=self._roleplay_scope(),
            # 需求：技能产出目录——技能生成的文件写到该文件夹（附件栏选择优先）
            skill_workdir=self._current_workdir(),
            # 需求：项目路径（「选择路径」）作为本次对话的读 / 学习路径，注入 LLM
            project_paths_ctx=self._project_paths_context(),
            # 需求：\@novellearn——已学习的小说设定，注入本次对话供复刻剧情
            novel_context=getattr(self, "_novel_context", ""),
        )
        self._chat_worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._chat_worker.run)
        self._chat_worker.token.connect(self._on_stream)
        self._chat_worker.reasoning.connect(self._on_reasoning)
        self._chat_worker.emotion.connect(self._on_emotion)
        self._chat_worker.usage.connect(self._on_usage)
        self._chat_worker.finished.connect(self._on_finished)
        self._chat_worker.error.connect(self._on_error)
        # 非致命提示（附件解析失败等）：走系统提示气泡，不打断本轮对话
        self._chat_worker.notice.connect(self._append_notice)
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

    # ------------------------------------------ token 用量 / 响应速度（右上角小字）
    def _reset_token_stats(self) -> None:
        """清空「总用量 / 本轮」统计（新会话、切换历史会话时调用）。"""
        self._stat_turns = 0
        self._stat_total_in = 0
        self._stat_total_out = 0
        self._stat_total_cached = 0
        self._stat_exact = True          # False = 接口未返回用量，界面显示估算值 ≈
        self._reset_turn_stats()
        lbl = getattr(self, "_stats_label", None)
        if lbl is not None:
            lbl.clearStats()

    def _reset_turn_stats(self) -> None:
        """清空「本轮（当前小轮）」统计：每次用户发起新对话时调用。

        本轮会累计该轮内所有模型调用的用量（工具 / 技能多步调用都算在同一轮），
        并以灰色小字实时显示在右上角「选择路径」左侧。
        """
        self._stat_turn_in = 0
        self._stat_turn_out = 0
        self._stat_turn_cached = 0
        self._stat_turn_calls = 0
        self._stat_turn_cached_unknown = True
        self._stat_turn_seconds = 0.0
        self._stat_turn_first = 0.0
        self._stat_turn_tps = 0.0
        # 本轮正在生成：usage 尚未回传时，输出量按已流式文本实时估算
        self._stat_turn_live = False
        self._stat_turn_live_out = 0
        self._stat_turn_started = time.perf_counter()
        self._stat_turn_live_at = 0.0

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        """token 数紧凑显示：1234 → 1.2k，1234567 → 1.23M。"""
        try:
            v = int(n or 0)
        except Exception:  # noqa: BLE001
            return "0"
        if v < 1000:
            return str(v)
        if v < 1_000_000:
            f = v / 1000.0
            return f"{f:.1f}k" if f < 100 else f"{round(f)}k"
        return f"{v / 1_000_000.0:.2f}M"

    def _on_usage(self, info: Dict[str, Any]) -> None:
        """ChatWorker.usage 槽：累计本对话用量并刷新右上角灰色小字。"""
        try:
            data = dict(info or {})
        except Exception:  # noqa: BLE001
            return
        p = int(data.get("prompt_tokens") or 0)
        c = int(data.get("completion_tokens") or 0)
        cached = int(data.get("cached_tokens") or 0)
        # 本轮（当前小轮）累计：同一轮内的多次模型调用都累加
        self._stat_turn_in += p
        self._stat_turn_out += c
        self._stat_turn_cached += cached
        self._stat_turn_calls += 1
        self._stat_turn_cached_unknown = bool(data.get("cached_unknown", True))
        self._stat_turn_seconds = float(data.get("seconds") or 0.0)
        self._stat_turn_first = float(data.get("first_delay") or 0.0)
        self._stat_turn_tps = float(data.get("tps") or 0.0)
        # 本轮已拿到服务端 usage：结束实时估算状态
        self._stat_turn_live = False
        self._stat_turn_live_out = 0
        # 本对话（总）累计
        self._stat_total_in += p
        self._stat_total_out += c
        self._stat_total_cached += cached
        self._stat_turns += 1
        self._stat_exact = bool(data.get("exact"))
        self._refresh_stats_label()

    def _refresh_stats_label(self) -> None:
        """刷新右上角小字：总用量 / 本轮 token（输入·输出）、缓存命中、响应速度。"""
        lbl = getattr(self, "_stats_label", None)
        if lbl is None:
            return
        # 本轮生成中：输出量按已流式文本估算（实时刷新），此刻还没有服务端 usage
        live = bool(getattr(self, "_stat_turn_live", False))
        live_out = int(getattr(self, "_stat_turn_live_out", 0) or 0)
        if getattr(self, "_stat_turns", 0) <= 0 and not (live and live_out > 0):
            lbl.clearStats()
            return
        mark = "" if self._stat_exact else "≈"
        total = (f"总 {mark}入{self._fmt_tokens(self._stat_total_in)}"
                 f"/出{self._fmt_tokens(self._stat_total_out)}")
        if live and live_out > 0:
            # 输入量要等本轮 usage 回传才有，生成中先显示已输出估算值
            turn = f"本轮 {mark}入…/出≈{self._fmt_tokens(live_out)}"
        else:
            turn = (f"本轮 {mark}入{self._fmt_tokens(self._stat_turn_in)}"
                    f"/出{self._fmt_tokens(self._stat_turn_out)}")
        if live:
            cache = "缓存 …"
        elif self._stat_turn_cached_unknown:
            cache = "缓存 未知"
        else:
            pct = ((self._stat_turn_cached / self._stat_turn_in * 100)
                   if self._stat_turn_in else 0.0)
            cache = (f"缓存 {self._fmt_tokens(self._stat_turn_cached)}"
                     f"·{pct:.0f}%")
        if live:
            sec = time.perf_counter() - self._stat_turn_started
        else:
            sec = self._stat_turn_seconds
        speed = f"{sec:.1f}s" if sec < 100 else f"{sec:.0f}s"
        if self._stat_turn_tps > 0:
            speed += f"·{self._stat_turn_tps:.0f}tok/s"
        parts = [turn, cache, speed]
        # 第一轮还没收到任何 usage 时总用量恒为 0，先不显示「总」段
        if self._stat_total_in or self._stat_total_out:
            parts.insert(0, total)
        lbl.setStats(" ｜ ".join(parts), self._stats_tip())

    def _stats_tip(self) -> str:
        """右上角小字的悬浮明细（完整数值，不做省略）。"""
        mark = "" if getattr(self, "_stat_exact", True) else "≈（估算值）"
        tin = int(getattr(self, "_stat_total_in", 0))
        tout = int(getattr(self, "_stat_total_out", 0))
        calls = int(getattr(self, "_stat_turn_calls", 0) or 0)
        live = bool(getattr(self, "_stat_turn_live", False))
        lines = [
            f"总用量：输入 {tin} / 输出 {tout} / 合计 {tin + tout} tokens {mark}",
            f"　（共 {getattr(self, '_stat_turns', 0)} 轮对话）",
        ]
        if live:
            est = int(getattr(self, "_stat_turn_live_out", 0) or 0)
            lines.append(
                f"本轮（当前小轮）：生成中，已输出约 {est} tokens"
                "（输入量与缓存命中待本轮结束后由接口 usage 给出）")
        else:
            lines.append(
                f"本轮（当前小轮）：输入 {self._stat_turn_in} / "
                f"输出 {self._stat_turn_out} / "
                f"合计 {self._stat_turn_in + self._stat_turn_out} tokens"
                + (f"（{calls} 次模型调用）" if calls > 1 else ""))
        if self._stat_turn_cached_unknown and not live:
            lines.append(
                f"缓存命中：总累计 {self._fmt_tokens(self._stat_total_cached)}"
                "（本轮接口未返回缓存字段）")
        else:
            pct = ((self._stat_turn_cached / self._stat_turn_in * 100)
                   if self._stat_turn_in else 0.0)
            lines.append(
                f"缓存命中：本轮 {self._stat_turn_cached} tokens"
                f"（占本轮输入 {pct:.1f}%），"
                f"总累计 {self._fmt_tokens(self._stat_total_cached)}")
        lines.append(
            f"响应速度：本轮总耗时 {self._stat_turn_seconds:.2f}s，"
            f"首字延迟 {self._stat_turn_first:.2f}s，"
            f"输出速度 {self._stat_turn_tps:.1f} tokens/s")
        lines.append(
            "说明：数值优先取接口返回的 usage（含 prompt_tokens_details 缓存命中）；"
            "端点不支持时按文本量估算并显示 ≈。仅统计主对话模型调用。")
        return "\n".join(lines)

    @staticmethod
    def _enforce_single_style(checked_box: "QCheckBox", cat: str,
                              checks: Dict[str, "QCheckBox"]) -> None:
        """对话风格**单选**：勾选某张风格卡时自动取消同类其它勾选。

        世界书 / 个人设定 / 角色工具保持多选；风格同时只生效一张，
        未选中的风格卡绝不会被加载进上下文。
        """
        if cat != "style":
            return
        if checked_box is None or not checked_box.isChecked():
            return
        prefix = "style/"
        for key, cb in (checks or {}).items():
            if cb is None or cb is checked_box:
                continue
            if str(key).startswith(prefix) and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)

    # 思维链 / 正式正文的分节标记（Gemini 深度思考式：思考在前、结果在后）
    # 强标记：任何模式下都认定为「思考段开头」
    _THINK_OPEN_MARKS = ("思维链", "思考过程", "思考", "推理过程", "推理")
    # 弱标记：仅在思维链模式（\\@thinking / 角色扮演）下认定为「思考段开头」，
    # 避免普通回复里恰好出现「【分析】」小标题时被误判成思考而折叠隐藏。
    _THINK_OPEN_MARKS_LOOSE = ("分析过程", "分析", "思路", "自检", "复盘",
                               "草稿", "内心推演")
    # 结束标记：出现即切回「正式正文」
    _THINK_CLOSE_MARKS = ("正式回答", "最终回答", "正式回复", "最终回复",
                          "正式输出", "最终输出", "回答", "正文", "输出")
    _THINK_MARK_RE_CACHE: Dict[bool, "re.Pattern"] = {}

    @classmethod
    def _compile_think_mark_re(cls, loose: bool = False) -> "re.Pattern":
        rx = cls._THINK_MARK_RE_CACHE.get(loose)
        if rx is None:
            marks = list(cls._THINK_OPEN_MARKS) + list(cls._THINK_CLOSE_MARKS)
            if loose:
                marks = list(cls._THINK_OPEN_MARKS_LOOSE) + marks
            # 长标记放前面，保证【正式回答】不会被【回答】抢先匹配
            marks.sort(key=len, reverse=True)
            rx = re.compile(r"【\s*(" + "|".join(marks) + r")\s*】")
            cls._THINK_MARK_RE_CACHE[loose] = rx
        return rx

    @classmethod
    def _split_thinking_text(cls, text: str,
                             thinking_mode: bool = False) -> Tuple[str, str]:
        """拆分「思维链 … 正式回答」格式文本，返回 (思维链, 正文)。

        兼容多种写法（角色工具卡片常自定义小标题）：
          - 开头：思维链 / 思考过程 / 思考 / 推理过程 / 推理（思维链模式下
            还识别 分析 / 思路 / 自检 / 复盘 / 草稿 等）
          - 结尾：正式回答 / 最终回答 / 正式回复 / 回答 / 正文 / 输出 等
        支持**多段交替**（模型分几轮思考再回答），所有思考段合并为思维链、
        所有结果段合并为正文；未命中任何标记则正文为全文。
        """
        t = text or ""
        close_set = set(cls._THINK_CLOSE_MARKS)
        open_set = set(cls._THINK_OPEN_MARKS) | set(cls._THINK_OPEN_MARKS_LOOSE)
        rx = cls._compile_think_mark_re(loose=thinking_mode)
        marks = list(rx.finditer(t))
        if not marks:
            # 兼容旧写法：只写了【思维链】还没写【回答】
            if t.strip().startswith("【思维链】"):
                return t[len("【思维链】"):].strip(), ""
            return "", t
        # 思维链模式下「第一个标记之前」的内容也属于思考（例如一句过渡语）；
        # 非思维链模式下仅当首个标记是结束标记（模型省略了【思维链】小标题）
        # 才把前导内容算作思考。
        first = marks[0].group(1)
        in_think = thinking_mode or (first in close_set)
        think_parts: List[str] = []
        body_parts: List[str] = []
        pos = 0
        for m in marks:
            seg = t[pos:m.start()]
            (think_parts if in_think else body_parts).append(seg)
            pos = m.end()
            mark = m.group(1)
            if mark in close_set:
                in_think = False
            elif mark in open_set:
                in_think = True
        tail = t[pos:]
        (think_parts if in_think else body_parts).append(tail)
        cot = re.sub(r"\n{3,}", "\n\n", "\n".join(think_parts)).strip()
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_parts)).strip()
        return cot, body

    def _on_reasoning(self, chunk: str) -> None:
        """累积模型原生思维链（reasoning_content），合入渲染节流刷新气泡。

        修复：原先思维链每来一个 chunk 就整文重排一次气泡，长思维链会因
        二次方级开销导致界面卡顿甚至卡死；这里与正文共用同一渲染合并窗口。
        """
        self._reasoning_text.append(chunk)
        if self._current_bubble is not None:
            self._schedule_bubble_refresh()

    def _render_thinking_body(self, body: str, reasoning: str) -> str:
        """兼容旧调用：思维链改为独立折叠块后，正文里不再内联思维链。

        保留本方法是为了不破坏既有调用点；思维链内容由 _thinking_block 展示。
        """
        return self._render_body_html(body)

    @staticmethod
    def _locate_in_message(w) -> tuple:
        """定位控件所在的布局：消息结构为 wrap(QV) → row(QH) → col(QV) → 气泡。

        返回 (layout, index)；找不到返回 (None, -1)。
        """
        if w is None:
            return None, -1
        parent = w.parentWidget()
        lay = parent.layout() if parent is not None else None
        if lay is None:
            return None, -1
        if lay.indexOf(w) >= 0:
            return lay, lay.indexOf(w)
        level = [lay]
        for _ in range(3):     # 最多下探三层：wrap → row → col
            nxt = []
            for l in level:
                for i in range(l.count()):
                    item = l.itemAt(i)
                    sub = item.layout() if item is not None else None
                    if sub is None:
                        continue
                    if sub.indexOf(w) >= 0:
                        return sub, sub.indexOf(w)
                    nxt.append(sub)
            level = nxt
            if not level:
                break
        return None, -1

    def _insert_block_above_bubble(self, bubble, blk, after=None) -> bool:
        """把折叠块插到消息列里、正文气泡**上方**（思维链 → 剧情设计 → 正文）。

        需求：思维链框必须在正文框上方。历史上块被插到了包裹层（头像行之外），
        层级与顺序都不稳定，这里统一插进气泡所在的纵向列。
        """
        lay, idx = self._locate_in_message(bubble)
        if lay is None:
            return False
        if after is not None:
            ai = lay.indexOf(after)
            if ai >= 0:
                idx = ai + 1
            else:
                idx = max(idx, 0)
        if idx < 0:
            idx = lay.count()
        lay.insertWidget(idx, blk)
        return True

    def _reorder_message_blocks(self, bubble) -> None:
        """保证顺序恒为「思维链 → 剧情设计 → 正文气泡」，且都在同一列里。"""
        if bubble is None:
            return
        lay, idx = self._locate_in_message(bubble)
        if lay is None or idx < 0:
            return
        seq = [b for b in (getattr(bubble, "_thinking_block", None),
                           getattr(bubble, "_plot_block", None))
               if b is not None]
        if not seq:
            return
        cur = [self._locate_in_message(b) for b in seq]
        idxs = [i for _, i in cur]
        if (all(l is lay for l, _ in cur) and idxs == sorted(idxs)
                and idxs[-1] < idx):
            return
        # 顺序/层级不对：先摘出，再按固定顺序插回气泡上方
        for b, (l, i) in zip(seq, cur):
            if l is not None and i >= 0:
                l.removeWidget(b)
        pos = lay.indexOf(bubble)
        if pos < 0:
            pos = lay.count()
        for b in seq:
            lay.insertWidget(pos, b)
            pos += 1

    def _row_item_index(self, wrap, bubble) -> int:
        """返回 wrap 布局里「承载正文气泡的那一行」的 item 索引（找不到 -1）。

        结构：wrap(QV)[ row(QH)[头像, col(QV)[…, 气泡]], 选项行?, 操作行 ]
        """
        lay = wrap.layout() if wrap is not None else None
        if lay is None or bubble is None:
            return -1
        for i in range(lay.count()):
            sub = lay.itemAt(i).layout()
            if sub is None:
                continue
            if sub.indexOf(bubble) >= 0:
                return i
            for j in range(sub.count()):
                sub2 = sub.itemAt(j).layout()
                if sub2 is not None and sub2.indexOf(bubble) >= 0:
                    return i
        return -1

    def _enforce_message_order(self, wrap) -> int:
        """把一条消息的内部顺序强制成**不变量**，返回纠正的控件数。

        不变量（自上而下）：
          列内：思维链 → 剧情设计 → 正文气泡 → 完整内容折叠块
          wrap：正文单元行 → 选项行 → 操作行（复制/终止/删除）

        与 `_reorder_message_blocks` 的分工：后者只保证折叠块在气泡上方；
        这里一并保证**选项行必须在正文下方**、**完整内容块在正文下方**。
        历史上选项行用「倒数第二项一定是操作行」来推断插入点，一旦某条消息
        没有操作行就会插到正文上方（表现为「思维链框跑到了选项下面」）。
        """
        if wrap is None:
            return 0
        lay = wrap.layout()
        bubble = getattr(wrap, "_bubble", None)
        if lay is None or bubble is None:
            return 0
        fixed = 0

        # ---- ① 列内：思维链 / 剧情 必须在气泡上方，完整内容块在气泡下方
        self._reorder_message_blocks(bubble)
        blay, bidx = self._locate_in_message(bubble)
        if blay is not None and bidx >= 0:
            full = getattr(bubble, "_full_block", None)
            if full is not None:
                flay, fidx = self._locate_in_message(full)
                if flay is blay and fidx >= 0 and fidx != bidx + 1:
                    blay.removeWidget(full)
                    bidx = blay.indexOf(bubble)
                    blay.insertWidget(bidx + 1 if bidx >= 0 else blay.count(),
                                      full)
                    fixed += 1

        # ---- ② wrap 内：选项行紧跟正文单元行之后（操作行自然落到最后）
        row_i = self._row_item_index(wrap, bubble)
        choices = [c for c in wrap.findChildren(ChoiceRow)
                   if c.parentWidget() is wrap]
        if row_i >= 0 and choices:
            for row in choices:
                cur = lay.indexOf(row)
                want = row_i + 1
                if cur == want:
                    continue
                lay.removeWidget(row)
                lay.insertWidget(want, row)
                fixed += 1
                row_i = self._row_item_index(wrap, bubble)
        return fixed

    def _painted_below(self, bubble, blocks: List[Any]) -> bool:
        """块的**实际绘制位置**是否与布局顺序不符（陈旧几何体检测）。

        判定两件事：① 任一可见块画到了气泡下方；② 块之间绘制顺序与排定顺序相反
        （例如「思考过程」排在「剧情设计」前面，却画在它下面）。
        """
        try:
            by = bubble.mapTo(self._chat_container, bubble.rect().topLeft()).y()
        except Exception:  # noqa: BLE001
            return False
        ys: List[int] = []
        for b in blocks:
            if not b.isVisible():
                continue
            try:
                y = b.mapTo(self._chat_container, b.rect().topLeft()).y()
            except Exception:  # noqa: BLE001
                continue
            if y >= by:
                return True
            ys.append(y)
        return any(ys[i] >= ys[i + 1] for i in range(len(ys) - 1))

    def _normalize_message_blocks(self, throttle: bool = False) -> int:
        """全量校正：把每条消息里的「思考过程 / 剧情设计」折叠块放回该消息正文**上方**。

        与 `_reorder_message_blocks` 的区别：后者只认 `bubble._thinking_block` /
        `_plot_block` 两个引用，**漏掉**「块被建到别的布局、或挂到了别的气泡」的情况
        （用户反馈：独立折叠框跑到了正文下方）。这里按「消息单元 → 该单元的正文气泡
        → 该单元内的折叠块」重新归位，任何层级/顺序错误都会被搬回来。

        返回被搬动的块数量。`throttle=True` 用于流式期间（1.5s 最多扫一次）。
        """
        if throttle:
            now = time.monotonic()
            if now - getattr(self, "_block_sweep_ts", 0.0) < 1.5:
                return 0
            self._block_sweep_ts = now
        fixed = 0
        order = {ThinkingBlock: 0, PlotBlock: 1}
        for i in range(self._chat_layout.count()):
            wrap = self._chat_layout.itemAt(i).widget()
            if wrap is None:
                continue
            # 每条消息先把「选项行必须在正文下方」等不变量补齐（幂等，代价很低）
            self._enforce_message_order(wrap)
            bubble = getattr(wrap, "_bubble", None)
            if bubble is None:
                continue
            lay, bidx = self._locate_in_message(bubble)
            if lay is None or bidx < 0:
                continue
            blocks = [b for b in wrap.findChildren(CollapsibleBlock)
                      if isinstance(b, (ThinkingBlock, PlotBlock))]
            if not blocks:
                continue
            blocks.sort(key=lambda b: order.get(type(b), 9))
            locs = [self._locate_in_message(b) for b in blocks]
            want = list(range(bidx - len(blocks), bidx))
            if all(l is lay for l, _ in locs) and [ix for _, ix in locs] == want:
                # 索引已正确，再检查**实际绘制位置**：陈旧几何体会让折叠框虽然排在
                # 气泡前面、却仍画在气泡下方（用户反馈的另一种「跑下面」）
                if self._painted_below(bubble, blocks):
                    lay.invalidate()
                    lay.activate()
                    wrap.updateGeometry()
                    bubble.updateGeometry()
                    fixed += 1
                continue
            _before = [(type(b).__name__, self._locate_in_message(b)[1])
                       for b in blocks]
            for b in blocks:
                l, ix = self._locate_in_message(b)
                if l is not None and ix >= 0:
                    l.removeWidget(b)
            pos = lay.indexOf(bubble)
            if pos < 0:
                pos = lay.count()
            for b in blocks:
                lay.insertWidget(pos, b)
                b.setVisible(b.isVisible())
                pos += 1
            fixed += len(blocks)
            _wmsg = getattr(wrap, "_msg", None)
            logger.warning(
                "折叠块位置校正：消息「%s」原位置 %s（气泡 idx=%d）→ 已搬回正文上方",
                (_wmsg or {}).get("name", "") if isinstance(_wmsg, dict) else "",
                _before, bidx)
        return fixed

    def _ensure_thinking_block(self) -> Optional["ThinkingBlock"]:
        """确保当前气泡挂有思维链折叠块（流式期间只创建一次）。"""
        bubble = self._current_bubble
        if bubble is None:
            return None
        blk = getattr(bubble, "_thinking_block", None)
        if blk is None:
            # 老消息（历史回放时创建的气泡）补一个，插入气泡上方
            blk = ThinkingBlock(accent=self._accent)
            blk.setVisible(False)
            if not self._insert_block_above_bubble(bubble, blk):
                return None
            bubble._thinking_block = blk
        return blk

    def _ensure_plot_block(self) -> Optional["PlotBlock"]:
        """确保当前气泡挂有「剧情设计」折叠块（与思维链同款，流式只创建一次）。"""
        bubble = self._current_bubble
        if bubble is None:
            return None
        blk = getattr(bubble, "_plot_block", None)
        if blk is None:
            blk = PlotBlock(accent=self._accent)
            blk.set_max_width(self._bubble_max_width())
            blk.setVisible(False)
            # 插在思维链折叠块之后（两者都在气泡上方）
            if not self._insert_block_above_bubble(
                    bubble, blk, after=getattr(bubble, "_thinking_block", None)):
                return None
            bubble._plot_block = blk
        return blk

    def _render_body_html(self, body: str) -> str:
        """正文 -> 聊天 HTML。

        处理顺序（需求：不要把 JSON 里的 <></> 带进对话 + 酒馆式排版）：
          1) 剥离结构标签（<draft_notes> 等）的尖括号，只保留文字；
          2) markdown 图片 / 图片 URL 转为「下载后显示」链接（占位符保护）；
          3) 转义后做轻量 markdown（粗体 / 斜体 / 行内代码 / 引用 / 列表）；
          4) 恢复图片占位为可点击链接。
        """
        import html as _html
        # 预设自定义结束符号（《end》等）任何渲染路径都不显示
        body = _PRESET_END_RE.sub("", body or "")
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

        # markdown 链接 [文本](URL)：同样先占位，避免转义破坏 href
        link_ph: List[tuple] = []

        def _mdlink_rep(m: "re.Match[str]") -> str:
            label = (m.group(1) or "").strip()
            url = m.group(2)
            token = f"\x00LINK{len(link_ph)}\x00"
            link_ph.append((token, label or url, url))
            return token

        # 1) 剥离结构标签的尖括号（规则卡/输出骨架里的 <draft_notes> 等）
        t = strip_struct_tags(body or "")
        # 2) 用占位符替换 markdown 图片 / 裸图片 URL（避免后续转义破坏标签）
        t = re.sub(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)", _md_rep, t)
        t = re.sub(
            r"(?<![\"=])(https?://[^\s<>\"'()]+\.(?:png|jpe?g|webp|gif))(?![)\"])",
            _url_rep, t, flags=re.I)
        t = re.sub(r"\[([^\]\n]{0,120})\]\((https?://[^\s)]+|file:///[^\s)]+)\)",
                   _mdlink_rep, t)
        esc = _html.escape(t).replace("&quot;", '"')
        # 3) 轻量 markdown（酒馆式排版）
        esc = mini_markdown(esc)
        # 4) 裸链接 → 可点击超链接（需求：输出的链接必须是超链接）
        esc = linkify_urls(esc, self._accent)
        # 5) 恢复图片占位符为可点击链接
        for token, alt, url in placeholders:
            u = _html.escape(url, quote=True)
            tag = (f'<a href="{u}" data-img-url="{u}" '
                   f'style="color:{self._accent};text-decoration:none;">'
                   f'{_img_html("createimg.gif")}{alt}</a>')
            esc = esc.replace(token, tag)
        # 6) 恢复 markdown 链接占位为超链接
        for token, label, url in link_ph:
            u = _html.escape(url, quote=True)
            esc = esc.replace(
                token,
                f'<a href="{u}" style="color:{self._accent};'
                f'text-decoration:none;">{_html.escape(label)}</a>')
        return f'<div style="white-space:pre-wrap;">{esc}</div>'

    def _update_bubble_text(self, display: str,
                            cot_override: Optional[str] = None,
                            plot_override: Optional[str] = None) -> None:
        """渲染当前气泡：正文进气泡，思维链/剧情设计进独立折叠块（默认收起）。

        ``cot_override`` / ``plot_override``：调用方（收尾 :meth:`_on_finished`）
        **已算好**的权威思维链 / 剧情设计文本。传 None 时按 ``display`` 现拆
        （流式路径）。必须支持外部传入的原因：长回复走摘要显示时，按摘要反推是
        拆不出思考段的（摘要里没有思考标记）→ 折叠块被判空而隐藏，表现为
        「收尾后思维链凭空消失 / 思考内容落回正文」。

        需求：
        - 思维链是**单独的折叠框**，不再塞进正文；
        - 摘要 / 草稿块（<draft_notes> 等）抽取出来后同样只进折叠框与记录，
          不在正文里铺开（正则隐藏，记录仍然完整）；
        - 剧情设计（COT 梳理）与思维链一样折叠：流式期间标签还没闭合，
          也要收进「剧情设计」折叠框，不能铺在正文里；
        - 角色工具卡写在 HTML 注释里的思考过程同样归**思维链**（见
          :func:`extract_cot_comments`），正式正文只留最终结果。
        """
        if self._current_bubble is None:
            return
        native = "".join(getattr(self, "_reasoning_text", []))
        thinking_part, body_part = self._split_thinking_text(
            display, thinking_mode=bool(getattr(self, "_thinking_mode", False)))
        # 流式期间从 HTML 注释里抽出来的思考过程，并入思维链
        extra_cot = str(getattr(self, "_stream_cot_extra", "") or "").strip()
        if extra_cot and extra_cot not in thinking_part:
            thinking_part = (thinking_part + "\n" + extra_cot).strip() \
                if thinking_part else extra_cot
        # 摘要类块：从正文抽出，只记录 / 折叠，不铺开显示
        body_view, blocks = extract_tag_blocks(body_part)
        if body_view.strip():
            body_part = body_view
        # 需求：剧情设计（未闭合草稿标签 / COT 段 / 酒馆 HTML 折叠块）单独折叠
        body_part, folds = extract_html_folds(body_part)
        body_part, plot = extract_plot_draft(body_part)
        if folds:
            plot = ("\n\n".join(folds) + (("\n\n" + plot) if plot else "")).strip()
        # <draft_notes> 等闭合标签的内容归「剧情设计」，其余归「思考过程 / 摘要」
        plot_blocks, other_blocks = split_plot_blocks(blocks)
        if plot_blocks:
            plot = (plot + "\n" + plot_blocks).strip() if plot else plot_blocks
        cot = (native or thinking_part or "").strip()
        if other_blocks:
            cot = (cot + "\n" + other_blocks).strip() if cot else other_blocks
        # 硬保证：正文里若仍残留思考标记（旧存档 / 预设自定义写法），再抽一次，
        # 确保「思维链永远不会出现在正文气泡里」
        if body_part.strip():
            _cot_left, _body_clean, _ = split_cot_stream(body_part)
            if _cot_left.strip():
                body_part = _body_clean
                cot = (cot + "\n" + _cot_left).strip() if cot else _cot_left.strip()

        # 收尾等调用方已给出权威思维链/剧情文本：直接采用（不再按显示文本反推）
        if cot_override is not None:
            cot = str(cot_override or "").strip()
        if plot_override is not None:
            plot = str(plot_override or "").strip()

        # 需求：正文气泡只铺最终结果。内容都在思维链里时留空——不能因为
        # 正文为空就回退显示整段原文（那会把思考过程 / 半截标签壳漏进正文）
        if body_part.strip():
            _body_view = body_part
        elif not (thinking_part or native or extra_cot):
            _body_view = display     # 没有任何思考内容：原样兜底
        else:
            _body_view = ""
        self._current_bubble.setTextFormat(Qt.RichText)
        self._current_bubble.setText(self._render_body_html(_body_view))
        self._update_collapse_blocks(cot, plot)
        # 需求：思维链框在正文框上方——每帧校正一次（顺序正确时不做任何改动）
        self._reorder_message_blocks(self._current_bubble)
        # 记录到本轮消息（供存档/历史，界面不显示）
        self._current_thinking = cot
        self._current_plot = plot

    def _update_collapse_blocks(self, cot: str, plot: str) -> None:
        """刷新当前气泡的「思考过程 / 剧情设计」折叠块（与正文渲染方式无关）。

        需求：两者都必须是**正文上方**的独立折叠块。超长输出的「纯文本窗口」
        流式路径同样要调用本方法，否则思维链会留在正文里滚动。
        """
        show_th = self._thinking_visible()
        blk = self._ensure_thinking_block()
        if blk is not None:
            if show_th and cot:
                # 性能：思维链折叠框同样做节流（长思维链每帧 setText 很贵）
                if self._block_should_update("cot", cot):
                    blk.set_text(cot)
                # Gemini 风：只有仍在输出时才显示「思考中…」动画与进度条
                blk.set_streaming(bool(getattr(self, "_busy", False)))
                blk.setVisible(True)
            else:
                blk.setVisible(False)
        # 需求：剧情设计与思维链一样折叠（Gemini / 元宝风格：思考中… + 进度条）
        pblk = self._ensure_plot_block()
        if pblk is not None:
            if plot:
                self._plot_seen = True
            # 剧情设计只在开头出现一次，后续帧 plot 为空也要保持折叠块可见
            if show_th and (plot or getattr(self, "_plot_seen", False)):
                if plot and self._block_should_update("plot", plot):
                    pblk.set_text(plot)
                # Gemini 风：只有仍在输出时才显示「思考中…」动画与进度条
                pblk.set_streaming(bool(getattr(self, "_busy", False)))
                pblk.setVisible(True)
            else:
                pblk.setVisible(False)

    def _thinking_visible(self) -> bool:
        """本轮是否展示「思考过程 / 剧情设计」折叠块。

        需求（用户反馈「\\@thinking 指令无法正确开启思维链的显示」）：设置里的
        「显示思维链」默认是**关闭**的，此前连 `\\@thinking` 也一起被隐藏（模型确实
        按思维链协议思考了，但界面不显示）→ 用户明确点名了思维链却看不到。
        现在：全局开关开启 **或**本轮用了 `\\@thinking` 技能 → 显示。
        """
        if bool(getattr(self, "_thinking_skill", False)):
            return True
        try:
            return bool(self._cfg.show_thinking())
        except Exception:  # noqa: BLE001
            return False

    def _msg_thinking_visible(self, msg: Any) -> bool:
        """历史回放：该条消息是否展示思维链折叠块。

        全局开关开启，或这条消息当时就是 `\\@thinking` 状态（存档里记了
        ``thinking_skill``）→ 显示。否则回放时思维链会「凭空消失」。
        """
        if isinstance(msg, dict) and bool(msg.get("thinking_skill")):
            return True
        try:
            return bool(self._cfg.show_thinking())
        except Exception:  # noqa: BLE001
            return False

    def _thinking_should_update(self, cot: str) -> bool:
        """兼容旧调用：思维链折叠框是否需要刷新。"""
        return self._block_should_update("cot", cot)

    def _block_should_update(self, key: str, text: str) -> bool:
        """折叠框是否需要刷新（长度变化够大或距上次够久才刷新）。

        长思维链 / 剧情设计若每帧 setText，成本随长度呈二次方上升。
        """
        now = time.monotonic()
        last_ts = float(getattr(self, f"_{key}_ts", 0.0) or 0.0)
        last_len = int(getattr(self, f"_{key}_len", -1) or -1)
        if (last_len < 0 or abs(len(text) - last_len) >= 400
                or (now - last_ts) >= 1.0):
            setattr(self, f"_{key}_ts", now)
            setattr(self, f"_{key}_len", len(text))
            return True
        return False

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
                from utils.async_worker import spawn_worker
                spawn_worker(
                    self._download_image_worker, url, str(self._cfg.image_dir()),
                    on_done=lambda path, u=url: self._on_image_downloaded(
                        u, str(path)),
                    on_fail=lambda msg: logger.warning(
                        "模型图片下载失败: %s", msg))
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
            # 渲染合并节流（_schedule_bubble_refresh 内部防抖）：
            # 修复长对话/长回复/长思维链场景下每个 token 都整文 setText +
            # adjustSize 造成的界面卡顿卡死；滚动跟随同样在 _scroll_to_bottom
            # 内部按 80ms 合并、非高频调用时保持立即跟随。
            self._schedule_bubble_refresh()
            self._scroll_to_bottom()
        self._bus.reply_stream.emit(chunk)
        self._tick_turn_live_stats()

    def _tick_turn_live_stats(self) -> None:
        """本轮生成中：按已流式文本实时估算输出 token（0.5 秒节流）。

        右上角小字的「本轮」在生成过程中就能看到输出量增长，不必等到本轮结束
        收到服务端 usage 才更新。
        """
        if not getattr(self, "_stat_turn_live", False):
            return
        now = time.perf_counter()
        if now - float(getattr(self, "_stat_turn_live_at", 0.0) or 0.0) < 0.5:
            return
        self._stat_turn_live_at = now
        buf = getattr(self, "_stream_buffer", None) or []
        try:
            self._stat_turn_live_out = ChatWorker._estimate_tokens("".join(buf))
        except Exception:  # noqa: BLE001
            return
        self._refresh_stats_label()

    def _schedule_bubble_refresh(self, delay_ms: Optional[int] = None) -> None:
        """合并气泡全文重排：同一时间窗内仅执行一次，避免逐 token 重排。

        修复：长对话/长回复/长思维链流式输出中，若每收到一个 chunk 都调用
        _update_bubble_text（整篇 rich-text setText + 布局），成本随已输出
        内容长度增长呈二次方上升，是界面卡顿/假死的主要原因。这里把高频
        请求合并到单次 timer 内一次刷新，并且**输出越长、刷新间隔越大**，
        避免长文本每 30ms 就重排一次上万字的富文本。
        """
        if delay_ms is None:
            raw = (self._current_bubble.property("raw")
                   if self._current_bubble is not None else "") or ""
            n = len(raw)
            if n > self.STREAM_PLAIN_LIMIT:
                delay_ms = 220
            elif n > self.STREAM_RICH_LIMIT:
                delay_ms = 120
            else:
                delay_ms = 30
        if getattr(self, "_bubble_refresh_pending", False):
            return
        self._bubble_refresh_pending = True
        QTimer.singleShot(delay_ms, self._flush_bubble_refresh)

    def _stream_window(self, raw: str) -> str:
        """流式期间的显示窗口：超长输出只渲染尾部，避免整篇富文本重排。"""
        n = len(raw)
        tail = raw[-self.STREAM_TAIL_CHARS:]
        head = n - len(tail)
        return (f"…（输出中，前面 {head} 字暂时省略，完成后完整显示）\n\n"
                f"{tail}")

    def _flush_bubble_refresh(self) -> None:
        """延迟窗口到点后刷新一次当前气泡（若已无气泡则跳过）。"""
        self._bubble_refresh_pending = False
        if self._current_bubble is None:
            return
        # 本轮已结束（收尾 / 报错 / 输出中断）：禁止**迟到的**流式刷新覆盖已定稿
        # 气泡——历史问题：报错写入的「输出中断：…」红字会被排队的刷新按 raw
        # 重新渲染而抹掉（表现为「中断后看不到错误提示」）。
        if not getattr(self, "_busy", False):
            return
        raw = self._current_bubble.property("raw") or ""
        if not raw:
            # 正文一个字都还没到也别急着退出：`\@thinking` 下模型常常**先吐
            # reasoning_content**、正文随后才来。此前这里直接 return，思考阶段的
            # 前几秒界面完全空白 —— 用户会以为「思维链根本没打开」。现在只要有
            # 原生思维链且本轮要显示它，就把折叠块先铺出来。
            if not self._thinking_visible() or not "".join(
                    getattr(self, "_reasoning_text", []) or []).strip():
                return
        # 需求：剧情设计（未闭合 <draft_notes> / COT 梳理 / HTML 折叠块）
        # 以及 OOC 破限提示、记忆提炼短对话——都不铺在正文里，交给折叠块；
        # <w2g> 选择框不显示文字，收尾时渲染成可点击按钮
        # 需求：HTML 注释承载的思考过程走**思维链**折叠块，不混进正文
        # 流式期间 hold_tail=True：半截定界符先按住，下一帧补全再决定归属
        body, cot_stream, plot, _w2g = split_display_blocks(raw, hold_tail=True)
        if cot_stream:
            # 流式期间把「思考过程」（HTML 注释 / 未闭合思考标签）累积起来，
            # 交给思维链折叠块实时展示；正文只留最终结果
            self._stream_cot_extra = cot_stream
        # 流式期间只把 <w2g> 内容从正文里藏起来，**不收集选项**：此时闭合标签
        # 往往还没到，收集到的只是「抓住话」「抓住话柄：用户…」这类半截增量，
        # 收尾渲染就会变成一排越来越长的碎片按钮（选项被拆成词）。
        # 选项一律等收尾拿到完整文本后一次性解析。
        if plot:
            self._plot_seen = True
            self._show_plot_stream(plot)
        if len(raw) > self.STREAM_PLAIN_LIMIT:
            # 超长输出：流式期间直接渲染纯文本尾部（Qt 富文本对超大文档极慢，
            # 是长回复卡顿/内存暴涨闪退的主因）；完成后再一次性富文本渲染。
            self._current_bubble.setTextFormat(Qt.PlainText)
            self._current_bubble.setText(self._stream_window(body))
            # 需求：纯文本窗口也不能把思维链留在正文——照样收进正文上方的折叠块
            self._update_collapse_blocks(self._stream_cot_extra, plot)
            self._reorder_message_blocks(self._current_bubble)
            self._normalize_message_blocks(throttle=True)
            return
        if len(raw) > self.STREAM_RICH_LIMIT:
            body = self._stream_window(body)
        # 流式显示时隐藏【】情绪标签（只在左侧立绘/情绪标签显示）
        self._update_bubble_text(self._display_text(body))
        # 兜底：任何层级/顺序错位的折叠块都搬回正文上方（1.5s 最多一次）
        self._normalize_message_blocks(throttle=True)

    def _show_plot_stream(self, plot: str) -> None:
        """流式期间把「剧情设计」内容推进折叠块（Gemini 风：思考中… + 进度条）。"""
        if not self._thinking_visible():
            return
        blk = self._ensure_plot_block()
        if blk is None:
            return
        if self._block_should_update("plot", plot):
            blk.set_text(plot)
        blk.set_streaming(True)
        blk.setVisible(True)

    def _on_emotion(self, emotion: str) -> None:
        self._set_portrait(self._current_role, emotion)
        self._bus.emotion_changed.emit(emotion)

    def _on_finished(self, clean: str, speaker: str, emotion: str) -> None:
        self._busy = False
        self._send_btn.setEnabled(True)
        if hasattr(self, "_timeout_timer"):
            self._timeout_timer.stop()
        # 思维链/正文拆分：正文进记录与气泡；思维链单独存到 msg["thinking"]
        # （需求：思维链/摘要只记录、默认不显示，展开折叠框才看得到）
        # 需求：思考段（<think> / <｜begin▁of▁thinking｜> / <基础确认> / <!-- -->）
        # 整段归思维链——未闭合时其后全部算思考，正文只留最终结果
        # thinking_mode 传 False：与流式路径（split_display_blocks）保持同一
        # 判定口径，避免「首标记之前的内容」在流式与收尾之间跳到不同区块
        _cot_tags, _rest, _cot_pending = split_cot_stream(clean)
        thinking_part, body_part = self._split_thinking_text(_rest)
        if _cot_tags:
            thinking_part = ((_cot_tags + "\n" + thinking_part).strip()
                             if thinking_part else _cot_tags)
        body_part, summary_blocks = extract_tag_blocks(body_part)
        # 需求：选择框 <w2g> 必须在展示层正则**之前**抽走——否则酒馆预设的
        # 展示层正则会把它替换成一坨 HTML 源码（<!DOCTYPE html> + <style>）。
        # 抽出来的选项收尾时渲染成按钮，点击填入输入框（可直接发送或补充后发送）。
        body_part, w2g_opts = extract_w2g_choices(body_part)
        if w2g_opts:
            # 以收尾的完整文本为准（流式期间的半截增量不参与，避免碎片选项）
            self._w2g_options = list(w2g_opts)
        # 角色扮演：展示层正则（只影响界面显示/会话存档，记忆归档仍用模型层文本）
        if getattr(self, "_roleplay_on", False):
            try:
                from roleplay.roleplay_engine import RolePlayEngine
                body_part = RolePlayEngine().incoming_view(body_part, depth=0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色扮演展示层正则失败: %s", exc)
            # 需求（用户 2026-09-21「TGbreak 对话后的行动选项不显示了」）：
            # 展示层正则会把手写格式的 <w2g> 换成 HTML 卡片。若上一步没抽到选项
            # （开标签紧贴正文 / 抽取晚于美化），这里再从卡片隐藏容器
            # ``<div id="rawData">`` 里捞一次，避免选项凭空消失。
            body_part, _card_opts = extract_w2g_choices(body_part)
            for _o in _card_opts:
                if _o and _o not in (self._w2g_options or []):
                    self._w2g_options = list(self._w2g_options or []) + [_o]
        # 需求：<draft_notes> / COT 梳理 / 展示层正则生成的 HTML 折叠块，
        # 以及被模型复述出来的 OOC 破限提示、记忆提炼短对话——都不混进正文，
        # 统一收进折叠块单独记录（记录与存档仍然完整）；
        # HTML 注释承载的思考过程则归入**思维链**（最终结果才留在正文）
        body_part, cot_cm, plot_part, w2g_rest = split_display_blocks(body_part)
        for _o in w2g_rest or []:
            if _o and _o not in (self._w2g_options or []):
                self._w2g_options = list(self._w2g_options or []) + [_o]
        # 需求：模型没有输出有效正文时，把已输出的文字（流式缓冲/原生思维链）
        # 显示出来，而不是留空白气泡；完全无输出则给出提示。
        if not clean and not body_part:
            buffered = self._display_text("".join(self._stream_buffer) or "")
            if not buffered:
                buffered = "".join(getattr(self, "_reasoning_text", []))
            body_part = buffered or "（本轮未生成有效回复）"
        msg = {"role": "assistant", "name": speaker,
               "content": body_part, "ts": time.time()}
        # 思维链 + 摘要块：写入记录（存档/历史可查），界面默认折叠不显示
        _cot = (thinking_part or "").strip()
        _native = "".join(getattr(self, "_reasoning_text", []) or []).strip()
        if _native and _native not in _cot:
            _cot = (_native + "\n" + _cot).strip()
        _plot_blocks, _other_blocks = split_plot_blocks(summary_blocks)
        if _other_blocks:
            _cot = (_cot + "\n" + _other_blocks).strip() if _cot else _other_blocks
        if cot_cm and cot_cm not in _cot:
            _cot = (_cot + "\n" + cot_cm).strip() if _cot else cot_cm
        if not body_part.strip() and _cot.strip():
            # 思考段始终没闭合（模型只输出了思考、没有正式正文）：
            # 至少把内容显示出来，不留一个空气泡
            body_part = _cot.strip()
            msg["content"] = body_part
        if _plot_blocks:
            plot_part = (plot_part + "\n" + _plot_blocks).strip() \
                if plot_part else _plot_blocks
        if _cot:
            msg["thinking"] = _cot
        if plot_part:
            msg["plot"] = plot_part
        # 需求：本轮是 \@thinking 点名的 —— 记进存档，重载会话时思维链折叠块照常回放
        if bool(getattr(self, "_thinking_skill", False)):
            msg["thinking_skill"] = True
        if self._current_bubble is not None:
            # 需求：最终输出隐藏 [角色名] / 角色名： 前缀（姓名由气泡标题展示）
            # 需求：模型给出选项时用按钮呈现（点击只填入输入框，不直接执行）
            body_view, opts = extract_choices(body_part)
            # 需求：<w2g> 选择框同样渲染成按钮（与【选项】合并，去重保序）
            for _w in (self._w2g_options or []):
                if _w not in opts:
                    opts.append(_w)
            if opts:
                body_part = body_view
            # 性能：先算出最终显示文本，**只渲染一次**富文本。
            # 需求（用户）：「长回复不要隐藏」——气泡铺完整正文，不再出现
            # 「…（已折叠，共 N 字，展开查看完整内容）」的首尾摘要；
            # 只有超过 REPLY_COMPRESS_LIMIT（极端超长）才退化为摘要 + 折叠块。
            if len(body_part) > self.REPLY_COMPRESS_LIMIT:
                shown = self._summary_text(body_part)
            else:
                shown = body_part
            self._current_bubble.setProperty("raw", body_part)
            # 思维链 / 剧情设计用**收尾时算好的权威文本**灌进折叠块：
            # 此前只按「气泡显示文本」重新拆一次，长回复走摘要时拆不出思考段 →
            # 折叠块被判空而隐藏（表现为收尾后思维链凭空消失 / 内容落回正文）
            self._update_bubble_text(self._display_text(shown),
                                     cot_override=_cot, plot_override=plot_part)
            if opts:
                self._append_choice_row(self._current_bubble, opts)
            # 需求：极端超长输出才压缩为摘要 + 折叠完整内容（记录仍完整）
            self._attach_full_block(self._current_bubble, body_part)
            # 需求：折叠块结束动画（Gemini 风「思考中…」→「N 字 · 用时 N 秒」）
            _elapsed = None
            _started = getattr(self, "_turn_started", None)
            if _started:
                _elapsed = max(0.0, time.monotonic() - float(_started))
            for _b in (getattr(self._current_bubble, "_thinking_block", None),
                       getattr(self._current_bubble, "_plot_block", None)):
                if _b is not None:
                    _b.mark_done(_elapsed)
            # 需求：收尾再校正一次顺序——保证「思维链 → 剧情设计 → 正文气泡」
            # 恒成立（选项行 / 完整内容折叠块都插在正文下方，不影响本顺序）
            self._reorder_message_blocks(self._current_bubble)
            # 顺序不变量兜底：选项行必须落在正文下方
            self._enforce_message_order(
                getattr(self._current_bubble, "_wrap", None))
            # 全量校正：不只查布局顺序，还查**实际绘制位置**——流式期间折叠块
            # 反复 setText/setVisible 会让几何体陈旧，块虽排在正文上方却画在正文
            # 下方（用户反馈「思维链显示在对话下面」）。收尾必须跑一次。
            self._normalize_message_blocks()
            # 选项已渲染为按钮，记录里去掉原始「【选项】…」标记行
            msg["content"] = body_part
            if opts:
                msg["choices"] = list(opts)
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
        # 需求：本轮结束后 5 秒兜底强制恢复发送（空输出/异常未收尾场景）
        self._schedule_force_recover()
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
            # 需求：角色扮演记忆与日常记忆分开存储 —— 归档时按开关状态选择命名空间
            mem_mode = ("roleplay" if getattr(self, "_roleplay_on", False)
                        else "normal")
            # 「每个对话独立」开启时，只归档到本会话的作用域
            mem_scope = self._roleplay_scope() if mem_mode == "roleplay" else ""
            # spawn_worker 保活引用（防止 QThread 被 GC 回收导致闪退）
            from utils.async_worker import spawn_worker
            worker = spawn_worker(
                self._memory.archive_after,
                prompt, clean, speaker, self._current_group or None,
                mem_mode, mem_scope,
                on_fail=lambda msg: logger.warning("记忆归档失败: %s", msg))
            self._archive_worker = worker
            # V2-A1：对话完成后异步编译「今日记忆传送带」摘要（不阻塞、失败静默）
            self._compile_today_async()
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动记忆归档失败: %s", exc)

    def _compile_today_async(self) -> None:
        """异步编译今日记忆传送带摘要（子线程执行，失败静默降级）。"""
        try:
            from memory_compile import MemoryCompile
            MemoryCompile.instance().compile_today_async(
                self._current_role, self._current_group or None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("今日记忆传送带编译启动失败: %s", exc)

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
        # 记录错误到 log 文件夹（需求 5）—— 原始技术信息写日志，便于排查
        self._log_error(msg)
        # 需求（用户 2026-09-21）：给用户看的是**自然语言原因**，而不是
        # ``openai.BadRequestError: Error code: 400 - {'error': {...}}`` 这类
        # 技术文本；原始文本已在上面的 _log_error 落盘。
        friendly = humanize_error(msg)
        # 防御：若错误发生在气泡创建前（如 _append_bubble 阶段），
        # _current_bubble 可能尚未初始化
        bubble = getattr(self, "_current_bubble", None)
        if bubble is not None:
            # 需求：输出被中断（被撤回/网络错误等）时保留已输出的文字，
            # 错误信息以红字追加在下方，不覆盖已输出的内容。
            buffered = self._display_text("".join(self._stream_buffer) or "")
            if buffered:
                bubble.setProperty("raw", buffered)
                # 性能：中断时同样只对超长内容渲染摘要，完整内容进折叠块
                if len(buffered) > self.REPLY_COMPRESS_LIMIT:
                    shown = self._summary_text(buffered)
                else:
                    shown = buffered
                bubble.setTextFormat(Qt.RichText)
                bubble.setText(
                    self._render_body_html(shown)
                    + f"<div style='color:#d64545;font-size:12px;"
                      f"margin-top:4px;'>输出中断：{friendly}</div>")
                self._attach_full_block(bubble, buffered)
            else:
                # 修复（2026-09-21）：/_image 等「不走流式缓冲」的错误路径上，气泡
                # raw 仍是占位 HTML（"<img…>正在生成图片，请稍候…"）。如果不
                # 把 raw 同步成错误文本，5 秒后 _on_error_recovered 拿到一个
                # 非空且不以「输出失败」开头的 raw，会走「保留正文 + 追加已恢复」
                # 分支，把占位图又渲染回来再叠上「输出中断，已恢复」—— 表现就是
                # 用户看到「正在生成图片，请稍候… / 输出中断，已恢复…」的乱码气泡。
                err_text = f"输出失败：{friendly}"
                bubble.setText(err_text)
                bubble.setProperty("raw", err_text)
        # 需求：5 秒后提示恢复，强制可再次发送消息（不弹模态窗打断）
        QTimer.singleShot(5000, lambda: self._on_error_recovered(friendly))
        self._schedule_force_recover()
        self._bus.reply_error.emit(msg)
        self._cleanup_worker()

    def _on_error_recovered(self, msg: str) -> None:
        """输出失败 5 秒后：提示已恢复，可继续下一轮对话。"""
        self._busy = False
        self._send_btn.setEnabled(True)
        bubble = getattr(self, "_current_bubble", None)
        if bubble is not None:
            try:
                raw = self._display_text(
                    bubble.property("raw") or "")
                if raw and not raw.startswith("输出失败"):
                    bubble.setText(
                        self._render_body_html(raw)
                        + "<div style='color:#9aa0ac;font-size:12px;"
                          "margin-top:4px;'>输出中断，已恢复，可以继续对话</div>")
                    return
            except Exception:  # noqa: BLE001
                pass
            bubble.setText(f"输出失败：{msg}\n（5 秒已过，已恢复，可以继续对话）")

    def _schedule_force_recover(self) -> None:
        """本轮对话结束后 5 秒兜底：强制恢复发送按钮。

        覆盖场景：模型空输出、输出被中断、worker 异常未触发 finished/error 等
        一切导致发送按钮仍禁用的异常路径，保证 5 秒后必然可再次发送消息。
        """
        fr = getattr(self, "_force_recover_timer", None)
        if fr is not None:
            fr.stop()
        fr = QTimer(self)
        fr.setSingleShot(True)
        fr.timeout.connect(self._force_recover_send)
        fr.start(5000)
        self._force_recover_timer = fr

    def _force_recover_send(self) -> None:
        """5 秒兜底恢复：按钮仍禁用时强制启用；同时清理可能残留的旧 worker。"""
        fr = getattr(self, "_force_recover_timer", None)
        if fr is not None:
            fr.stop()
            self._force_recover_timer = None
        if getattr(self, "_busy", False):
            self._cleanup_worker()
        self._busy = False
        self._send_btn.setEnabled(True)

    def _cleanup_worker(self) -> None:
        # 本轮结束（对话线程收尾）：退出实时估算状态，小字按已收到的 usage 定格
        # （仅在确有对话线程时收尾，避免生图等其它后台任务结束时误清本轮状态）
        if getattr(self, "_stat_turn_live", False) and self._worker_thread is not None:
            self._stat_turn_live = False
            self._refresh_stats_label()
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

    def _terminate_output(self) -> None:
        """立即终止当前正在生成的回复（打断流式输出）。"""
        worker = getattr(self, "_chat_worker", None)
        if worker is not None and hasattr(worker, "abort"):
            try:
                worker.abort()
            except Exception:  # noqa: BLE001
                pass
        self._append_notice("已终止输出。")

    #: 历史回放时最多给最近多少条消息渲染思维链 / 剧情设计折叠块
    HISTORY_BLOCK_LIMIT = 40

    # 全量重排节流参数（需求：长对话多轮之后不再卡顿）
    SCROLL_BASE_MS = 0.09        # 基础间隔
    SCROLL_PER_MSG_MS = 0.008    # 每多一条历史消息额外放宽
    SCROLL_MAX_MS = 0.45         # 上限（再慢就该由 force/收尾触发）

    def _scroll_interval(self) -> float:
        """全量布局重排的最小间隔：随历史消息数自适应放宽。

        ``_full_scroll`` 里的 ``adjustSize()`` 会重排**整条**聊天记录，成本随
        已积累的富文本总量线性上升（实测 10 轮后单次已 30ms+）。固定 80ms
        的节流在长对话里会把主线程打满，因此间隔随消息数增长而放宽。
        """
        n = len(getattr(self, "_messages", []) or [])
        return min(self.SCROLL_MAX_MS,
                   self.SCROLL_BASE_MS + n * self.SCROLL_PER_MSG_MS)

    def _scroll_to_bottom(self, force: bool = False) -> None:
        """流式输出期间自动跟随最新对话（高频调用时合并/节流，避免逐 token 重排）。

        修复：原先每次调用都同步 adjustSize()——adjustSize 会触发整条聊天记录
        重新布局，token 频率高时成本随内容长度增长而放大，是长对话/长思维链
        界面卡顿甚至假死的另一主因。现改为：
          - 每次调用只做一次廉价的滚动条跟随（不触发布局重排）；
          - 全量 adjustSize + 滚底：force/非流式时立即执行，流式期间按
            **自适应间隔**（随历史消息数放宽）执行，长对话不再被重排打满；
          - 节流窗口内的请求合并到一次延迟兜底滚动（_flush_scroll）。
        """
        bar = self._chat_scroll.verticalScrollBar()
        try:
            if bar.maximum() > 0:
                bar.setValue(bar.maximum())
        except Exception:  # noqa: BLE001
            pass
        now = time.monotonic()
        last = getattr(self, "_last_full_scroll", 0.0)
        interval = self._scroll_interval()
        if force or (not self._busy) or (now - last) >= interval:
            self._scroll_pending = False
            self._last_full_scroll = now
            self._full_scroll()
            return
        # 高频窗口内：安排一次窗口结束后的兜底完整滚动
        if not getattr(self, "_scroll_pending", False):
            self._scroll_pending = True
            delay = max(1, int((interval - (now - last)) * 1000) + 1)
            QTimer.singleShot(delay, self._flush_scroll)

    def _full_scroll(self) -> None:
        """滚到底（不再整条重排）。

        ``_chat_scroll`` 开了 ``widgetResizable``，滚动区本来就会按容器
        ``sizeHint`` 自动同步高度；以前额外调 ``adjustSize()`` 会把容器
        **宽度**一起改掉，导致**所有**历史气泡的富文本按新宽度重新排版——
        实测 10 轮后单次就 30ms+，多轮之后主线程被它打满。这里改为只通知
        布局有新内容（真正的排版交给滚动区自己按帧做），成本与历史长度无关。
        """
        try:
            self._chat_container.updateGeometry()
        except Exception:  # noqa: BLE001
            pass
        bar = self._chat_scroll.verticalScrollBar()
        try:
            bar.setValue(bar.maximum())
        except Exception:  # noqa: BLE001
            pass

    def _flush_scroll(self) -> None:
        """延迟兜底完整滚动（合并同一高频窗口内的多次请求）。"""
        self._scroll_pending = False
        self._last_full_scroll = time.monotonic()
        self._full_scroll()

    def _display_text(self, text: str) -> str:
        """显示文本时隐藏情绪标签（【】/〖〗/emotion）与 [角色名]/角色名： 前缀。

        需求：单聊/群聊回复正文都不出现 [角色名] 或 角色名： 前缀（姓名由气泡标题展示）。
        需求：模型输出中的 <content>...</content> 标签（含内部内容）在界面不显示。
        """
        t = _CONTENT_TAG_RE.sub("", text or "")
        t = _CONTENT_SELF_CLOSE_RE.sub("", t)
        # 兜底：任何渲染路径下 <details>/<summary> 与 <w2g> 的尖括号都不显示
        t = _HTML_DETAILS_ANY_RE.sub("", t)
        t = _W2G_ANY_RE.sub("", t)
        if "<!--" in t:                  # 指令注释块兜底（界面不显示）
            t = _HTML_COMMENT_RE.sub("", t)
        t = self._roles.strip_emotion_tags(t)
        # 流式截断 / 模型漏写闭合：末尾半截情绪标签（如「嘿！【开心」）连同其后内容隐藏
        t = _EMOTION_OPEN_TAIL_RE.sub("", t)
        # 需求（用户 2026-09-23）：显示层与解析层必须同一套名字口径
        # ① 剥前缀按归一化比较（「[Twilight Sparkle]:」不再原样留下）；
        # ② 群聊**只显示第一位发言人的内容** —— 模型多写的其他成员段落不进气泡。
        if self._current_group:
            t = ChatWorker._first_speaker_only(
                t, self._current_members(),
                self._roles.group_aliases(self._current_group))
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
        # skill库 #技能名：精确剥离已注册的 #技能 片段（与 skillspub_core 的
        # pub_refs_in_text / strip_pub_tags 保持一致；只删标签、保留正文）
        if t:
            try:
                from skillspub_core import strip_pub_tags
                t = strip_pub_tags(t)
            except Exception:  # noqa: BLE001
                pass
        t = _CLOSED_CMD_RE.sub(" ", t)
        return re.sub(r"\s{2,}", " ", t).strip()


    # ------------------------------------------------------------ 气泡渲染
    def _enable_bubble_links(self, bubble: Optional[QLabel]) -> None:
        """气泡内的超链接可点击（重复调用只连一次信号）。

        需求：模型输出的网址必须是可点击的超链接；点击统一交给
        :meth:`_on_media_link_activated`（本地图片进查看器，网址用系统浏览器打开）。
        """
        if bubble is None or bubble.property("link_hooked") is True:
            return
        bubble.setProperty("link_hooked", True)
        bubble.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        try:
            # 自己接管点击（False = 交给 linkActivated，而不是 Qt 内部直接打开）
            bubble.setOpenExternalLinks(False)
        except Exception:  # noqa: BLE001
            pass
        bubble.linkActivated.connect(self._on_media_link_activated)

    def _append_bubble(self, name: str, text: str, is_user: bool = False,
                       is_stream: bool = False, role: Optional[str] = None,
                       msg: Optional[Dict[str, Any]] = None,
                       animate_new: bool = True,
                       with_blocks: bool = True) -> QLabel:
        # 流式气泡：记录本轮开始时间，折叠块结束后显示「用时 N 秒」
        if is_stream:
            self._turn_started = time.monotonic()
            # 折叠块节流状态与「本轮是否出现过剧情设计」标记复位
            self._plot_seen = False
            self._cot_ts = 0.0
            # 本轮选择框 <w2g> 选项复位（流式期间累积，收尾渲染成按钮）
            self._w2g_options = []
            self._cot_len = -1
            self._plot_ts = 0.0
            self._plot_len = -1
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
        # 需求：正文里的链接是超链接（点击用系统默认浏览器打开）
        self._enable_bubble_links(bubble)
        # 需求：强制重新应用 QSS，保证气泡始终带白色底图（避免偶发"只有文字无底图"）
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
        # 需求：不要把 JSON（规则卡/输出骨架）里的 <></> 带进对话——
        # 显示前剥离结构标签的尖括号（raw 仍保留原文，供复制/引用使用）
        bubble.setText(strip_struct_tags(text))
        bubble.setProperty("raw", text)
        # V2-E3：气泡右键菜单（选中文本 → 引用到对话）
        bubble.setContextMenuPolicy(Qt.CustomContextMenu)
        bubble.customContextMenuRequested.connect(self._on_bubble_context_menu)
        self._bubble_widgets.append(bubble)

        col = QVBoxLayout()
        col.setSpacing(2)
        name_label = QLabel(name)
        name_label.setObjectName("bubbleName")
        # 需求：对话风格酒馆化——消息块 = 头像 +（名字 + 时间戳）+ 正文
        ts_label = QLabel(_hhmmss(msg.get("ts") if msg else None))
        ts_label.setObjectName("bubbleTime")
        ts_label.setStyleSheet("color:#9aa0ac;font-size:11px;")
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        if is_user:
            name_label.setAlignment(Qt.AlignRight)
            # 用户消息：名字右对齐；气泡不设右对齐（fill），随 col 宽度扩展
            # 到 max_w，与角色气泡一样填满整行再换行（需求 v4-1）
            head.addStretch(1)
            head.addWidget(ts_label)
            head.addWidget(name_label)
            col.addLayout(head)
            col.addWidget(bubble, 0)
        else:
            head.addWidget(name_label)
            head.addWidget(ts_label)
            head.addStretch(1)
            col.addLayout(head)
            # 需求：思维链是独立的折叠框（默认收起，位于正文上方），
            # 内容照常写入记录，只是不占对话篇幅。
            # 历史回放（with_blocks=False）时不建块：几十条消息的会话没必要
            # 创建几十个用不到的折叠框，加载会明显变慢。
            if with_blocks:
                _th = ThinkingBlock(accent=self._accent)
                _th.set_max_width(max_w)
                _th.setVisible(False)
                bubble._thinking_block = _th
                col.addWidget(_th)
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
        # 需求：每条消息下方的操作行（复制 / 终止 / 删除）——酒馆式消息块
        del_row = QHBoxLayout()
        del_row.setContentsMargins(0, 0, 0, 0)
        # 需求：三个图标贴得更近——按钮 22px / 图标 18px / 间距 1px
        # （原 24px 按钮 + 16px 图标 + 4px 间距 → 图标间视觉空隙约 12px，太散）
        del_row.setSpacing(1)
        # 复制：图标按钮（copy.png），文字改为图标
        copy_btn = QPushButton()
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setFlat(True)
        copy_btn.setFixedSize(22, 22)
        copy_btn.setIconSize(QSize(18, 18))
        copy_btn.setIcon(_cached_icon("copy.png"))
        copy_btn.setToolTip("复制这条消息")
        copy_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;padding:0;margin:0;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:11px;}")
        copy_btn.clicked.connect(
            lambda _=False, b=bubble: self._copy_message(b))
        del_row.addWidget(copy_btn)
        # 终止：立即打断当前正在生成的输出（仅流式气泡可用）
        stop_btn = QPushButton()
        stop_btn.setCursor(Qt.PointingHandCursor)
        stop_btn.setFlat(True)
        stop_btn.setFixedSize(22, 22)
        stop_btn.setIconSize(QSize(18, 18))
        stop_btn.setIcon(_cached_icon("pause.png"))
        stop_btn.setToolTip("终止输出")
        stop_btn.setEnabled(is_stream)
        stop_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;padding:0;margin:0;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:11px;}"
            "QPushButton:disabled{background:transparent;}")
        stop_btn.clicked.connect(lambda _=False: self._terminate_output())
        del_row.addWidget(stop_btn)
        del_btn = QPushButton()
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFlat(True)
        del_btn.setFixedSize(22, 22)
        del_btn.setIconSize(QSize(18, 18))
        del_btn.setIcon(_cached_icon("del.png"))
        del_btn.setToolTip("删除这句话")
        del_btn.setEnabled(not is_stream)
        del_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;padding:0;margin:0;}"
            "QPushButton:hover{background:rgba(0,0,0,0.08);border-radius:11px;}"
            "QPushButton:disabled{background:transparent;}")
        del_row.addWidget(del_btn)
        if is_user:
            del_row.addStretch(1)
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
        # HTML5 弹出动画：淡入 + 上滑（animate_new=False 用于批量回放历史：
        # 几十条消息各跑一个 300ms 透明度动画会把主线程打满，表现为「点历史卡死」）
        if not is_stream and animate_new:
            wrap.setGraphicsEffect(None)
            animate(wrap, b"windowOpacity", 0.0, 1.0, 300)
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
                lambda _u, pth=path: self._on_attachment_clicked(pth))
            flow.addWidget(link)
        # 插入到气泡行之后（删除按钮行之前），右对齐显示
        wrap_layout.insertLayout(1, flow)
        flow.setAlignment(Qt.AlignRight)

    def _on_bubble_context_menu(self, pos: QPoint) -> None:
        """气泡右键菜单：选中文本可「引用到对话」或复制。"""
        bubble = self.sender()
        if not isinstance(bubble, QLabel):
            return
        sel = (bubble.selectedText() or "").strip()
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        if sel:
            act_quote = menu.addAction("引用到对话")
            act_copy = menu.addAction("复制选中文本")
        else:
            act_quote = None
            act_copy = menu.addAction("复制全部")
        act = menu.exec(bubble.mapToGlobal(pos))
        if act == act_quote and sel:
            self._insert_quote(sel)
        elif act == act_copy:
            if sel:
                QApplication.clipboard().setText(sel)
            else:
                QApplication.clipboard().setText(bubble.text())

    def _insert_quote(self, text: str) -> None:
        """把选中文本作为引用插入输入框（保留原文语境，继续追问）。"""
        cursor = self._input.textCursor()
        if not cursor.hasSelection():
            cursor.movePosition(QTextCursor.End)
        snippet = text.replace("\n", " ").strip()[:200]
        cursor.insertText(f"\n「引用」{snippet}\n")
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def _suggest_paths(self, trig: str) -> None:
        """文件工具未给出路径时，用 HTML5 风格按钮列出候选文件/目录。

        需求：工具里需要用户选择的场景用按钮呈现，点击**只填入输入框**（不直接
        执行），用户可以继续补充内容，点「发送」才真正执行。
        """
        usage = f"用法：\\@{trig} 路径 [内容/旧文本|新文本]"
        try:
            from config_loader import ROOT as _root
            base = Path(str(_root)).resolve()
        except Exception:  # noqa: BLE001
            base = Path(os.getcwd())
        try:
            items = sorted(
                (p for p in base.iterdir()
                 if not p.name.startswith(".") and not p.name.startswith("__")),
                key=lambda p: (p.is_file(), p.name.lower()))[:8]
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            self._append_notice(usage)
            return
        opts = [f"\\@{trig} {p.name}" for p in items]
        self._append_notice(
            f"{usage}\n点击下面的文件/目录填入输入框，补充后点「发送」才执行：",
            choices=opts, choice_title="候选路径（点击填入输入框，不直接执行）")

    # ------------------------------------------------ 技能产出目录（skilluserdata）
    def _skilldata_uid(self) -> str:
        """本对话在 skilluserdata 的项目标识（随会话存档，重载会话仍对应同一个）。"""
        uid = str(getattr(self, "_skilldata_uid_value", "") or "").strip()
        if not uid:
            uid = uuid.uuid4().hex[:12]
            self._skilldata_uid_value = uid
        return uid

    def _current_workdir(self) -> str:
        """本对话技能产出目录：附件栏选择的工作文件夹优先，否则用对话项目文件夹。"""
        try:
            from skill_userdata import SkillUserData
        except Exception:  # noqa: BLE001
            return ""
        picked = str(getattr(self, "_skill_workdir", "") or "").strip()
        if picked:
            try:
                if Path(picked).is_dir():
                    # 用户显式选择的工作文件夹 → 授权 PathGuard 允许写入
                    # （否则项目外的目录会被判 BLOCKED，文件工具会拒绝写入）
                    from utils.path_guard import PathGuard
                    PathGuard.instance().allow_dir(picked)
                    return picked
            except Exception:  # noqa: BLE001
                pass
        folder = SkillUserData.instance().project_dir(
            self._skilldata_uid(), create=False)
        return str(folder) if folder else ""

    def _ensure_project_dir(self, ask: bool = True) -> str:
        """确保本对话在 skilluserdata 下有项目文件夹（必要时询问是否允许生成文件）。"""
        try:
            from skill_userdata import SkillUserData
        except Exception:  # noqa: BLE001
            return ""
        sud = SkillUserData.instance()
        existing = sud.project_dir(self._skilldata_uid(), create=False)
        if existing is not None:
            return str(existing)
        if ask and getattr(self, "_skilldata_denied", False):
            return ""     # 本对话已明确拒绝过：不反复询问
        if ask and not styled_question(
                self, "允许技能生成文件？",
                "本对话要执行的技能可能需要创建文件。\n\n"
                "选择「是」会在 skilluserdata 下建立一个以「日期+时间」命名的项目"
                "文件夹（一个对话一个，可用指令 \\@project 新项目名 更改项目名），"
                "技能生成的文件都写到那里。\n\n是否允许？"):
            self._skilldata_denied = True
            self._append_notice("已取消：本次不建立技能产出文件夹"
                                "（本对话不再重复询问，可用 \\@project 新项目名 "
                                "或附件栏「文件夹」随时启用）。")
            return ""
        folder = sud.project_dir(self._skilldata_uid(), create=True)
        if folder:
            self._add_auto_path(str(folder))
            self._append_notice(f"已建立技能产出文件夹：{folder}\n"
                                "（可用指令 \\@project 新项目名 更改项目名）")
            return str(folder)
        return ""

    def _handle_project_command(self, text: str) -> None:
        """\\@project 指令：查看 / 更改本对话在 skilluserdata 下的项目名（文件夹名）。"""
        try:
            from skill_userdata import SkillUserData
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"技能产出目录不可用：{exc}")
            return
        sud = SkillUserData.instance()
        arg = _PROJECT_CMD_RE.sub("", text or "").strip().strip('"').strip()
        uid = self._skilldata_uid()
        if not arg:
            cur = sud.label(uid)
            if cur:
                self._append_notice(f"本对话技能产出文件夹：{sud.root() / cur}\n"
                                    "更改项目名：\\@project 新项目名")
            else:
                self._append_notice(
                    "本对话还没有技能产出文件夹（执行需要生成文件的技能时会自动"
                    "建立）。\n也可现在指定：\\@project 新项目名")
            return
        ok, msg = sud.rename(uid, arg)
        if ok:
            self._skilldata_denied = False
        self._append_notice(msg if ok else f"改名失败：{msg}")

    def _clear_workdir(self) -> None:
        """清除附件栏选择的工作文件夹，恢复本对话默认项目文件夹。"""
        self._skill_workdir = ""
        self._skilldata_denied = False    # 用户主动操作 → 下次可重新询问
        self._refresh_attach_bar()
        self._append_notice("已恢复使用本对话默认的技能产出文件夹。")

    # ------------------------------------------------------------ 项目路径（选择路径）
    def _all_project_paths(self) -> List[str]:
        """全部项目路径（去重、保序）：用户显式选择的 + skillpub 自动生成的。"""
        seen: List[str] = []
        for p in (self._project_paths + self._auto_paths):
            if p and p not in seen:
                seen.append(p)
        return seen

    def _project_paths_context(self) -> str:
        """注入 LLM 的项目路径上下文：仅列路径与一级文件名，不读内容、不扫盘。"""
        paths = self._all_project_paths()
        if not paths:
            return ""
        lines = [
            "【项目路径（本次对话可读取 / 学习的文件夹）】",
            "以下文件夹中的文件可直接读取，处理相关任务请优先到这些路径取文件：",
        ]
        for p in paths:
            pp = Path(p)
            if not pp.is_dir():
                continue
            lines.append(f"- {p}")
            try:
                files = sorted(f.name for f in pp.iterdir() if f.is_file())
            except Exception:  # noqa: BLE001
                files = []
            if files:
                shown = "、".join(files[:50])
                if len(files) > 50:
                    shown += f" 等 {len(files)} 个"
                lines.append(f"    文件：{shown}")
        return "\n".join(lines)

    def _open_project_paths(self) -> None:
        """选择路径（右上角「选择路径」按钮）：选中的文件夹作为本次对话的读 / 学习路径。"""
        start = self._project_paths[-1] if self._project_paths else str(Path.home())
        p = QFileDialog.getExistingDirectory(self, "选择项目路径（文件夹）", start)
        if not p:
            return
        p = str(p)
        if p not in self._project_paths:
            self._project_paths.append(p)
        self._refresh_attach_bar()
        self._append_notice(
            f"已添加项目路径：{p}\n"
            "（作为本次对话的读 / 学习路径；执行技能自动生成的文件夹也会加入这里）")

    def _add_auto_path(self, folder: str) -> None:
        """skillpub 生成的技能产出文件夹自动加入项目路径（附件条 chip 显示）。"""
        f = str(folder or "").strip()
        if not f or f in self._auto_paths:
            return
        self._auto_paths.append(f)
        self._refresh_attach_bar()
        self._append_notice(
            f"已自动将技能产出文件夹加入项目路径：{f}\n"
            "（点右上角「选择路径」可继续添加；它作为本次对话的读取路径）")

    @staticmethod
    def _needs_file_project(text: str) -> bool:
        """是否明确要求「处理文件项目 / 文件夹 / 目录」类任务（需文件或路径来源）。"""
        t = (text or "").lower()
        if not t:
            return False
        return bool(re.search(
            r"文件项目|项目文件|处理\s*(这个|该)?\s*(文件夹|目录)|"
            r"分析\s*(这个|该)?\s*(文件夹|目录)|读(取|一下)?\s*(这个|该)?\s*(文件夹|目录)|"
            r"学习\s*(这个|该)?\s*(文件夹|目录)", t))

    # ------------------------------------------------------------ V2-D2: 文件工具技能
    def _handle_file_skill(self, skill: Dict[str, Any], clean: str) -> None:
        """执行 @read / @write / @edit 文件技能（受 PathGuard 权限保护）。"""
        from file_tools import FileTools
        trig = str(skill.get("trigger") or "").strip().lower()
        ft = FileTools.instance()
        # 提取路径（第一个 token；带引号则取引号内内容）
        # 注意：clean 保留 \@触发词 标签（供 LLM 解析），此处先剥离标签，
        # 否则 tokens[0] 会是 \@write 而非真正的路径（实测 bug：无权写入根因）
        text = self._strip_skill_display(clean or "").strip()
        import shlex
        try:
            tokens = shlex.split(text)
        except Exception:  # noqa: BLE001
            tokens = text.split()
        if not tokens:
            self._suggest_paths(trig)
            return
        path = tokens[0]
        rest = text[len(tokens[0]):].strip() if len(text) > len(tokens[0]) else ""
        # 需求：技能产出目录——写/改文件的相对路径落到本对话的工作文件夹
        # （附件栏选择的路径文件夹优先，否则用 skilluserdata 下的对话项目文件夹）；
        # 读取时若工作文件夹里存在同名文件也优先读它。
        _wd = self._current_workdir()
        if _wd and not Path(path).is_absolute():
            _cand = Path(_wd) / path
            if trig in ("write", "edit"):
                path = str(_cand)
            elif trig == "read" and _cand.exists():
                path = str(_cand)
        # 需求：调用技能读取文件时，也在「项目路径」中查找同名文件
        if trig == "read" and not Path(path).is_absolute() and not Path(path).exists():
            for pp in self._all_project_paths():
                _cand = Path(pp) / path
                if _cand.exists():
                    path = str(_cand)
                    break

        if trig == "read":
            ok, content, detail = ft.read(path)
            if not ok:
                self._append_notice(f"读取失败：{detail}")
                return
            preview = content[:500] + ("…（已截断）" if len(content) > 500 else "")
            self._append_notice(
                f"已读取 {detail}\n```\n{preview}\n```\n完整内容 {len(content)} 字符，"
                f"可点右上角历史查看。")
            return
        if trig == "write":
            if not rest:
                self._append_notice("用法：\\@write 路径 要写入的内容")
                return
            ok, msg = ft.write(path, rest)
            if ok:
                self._append_notice(f"写入成功：{msg}（已自动备份快照，可在设置-文件工具回滚）")
            else:
                self._append_notice(f"写入失败：{msg}")
            return
        if trig == "edit":
            if "|" not in rest:
                self._append_notice("用法：\\@edit 路径 旧文本|新文本")
                return
            old, new = rest.split("|", 1)
            ok, msg = ft.edit(path, old.strip(), new.strip())
            if ok:
                self._append_notice(f"修改成功：{msg}（已自动备份快照）")
            else:
                self._append_notice(f"修改失败：{msg}")
            return

    # ------------------------------------------------------------ V2-F1: 媒体查看器入口
    def _on_attachment_clicked(self, path: str) -> None:
        """附件点击：图片 → 全屏查看器；其它 → 系统默认程序打开。"""
        try:
            from media_viewer import MediaViewer, is_image_path
            if path and is_image_path(path):
                images = self._collect_session_images()
                try:
                    idx = images.index(str(path))
                except ValueError:
                    idx = max(0, len(images) - 1)
                self._media_viewer = MediaViewer.open(images, idx)
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开媒体查看器失败: %s", exc)
        self._open_attachment(path)

    def _on_media_link_activated(self, url: str) -> None:
        """气泡链接点击：本地图片 → 全屏查看器；其它 → 系统打开。"""
        path = str(url)
        if path.startswith("file:///"):
            path = path[len("file:///"):]
        try:
            from media_viewer import MediaViewer, is_image_path
            p = Path(path)
            if p.exists() and is_image_path(str(p)):
                images = self._collect_session_images()
                try:
                    idx = images.index(str(p))
                except ValueError:
                    idx = max(0, len(images) - 1)
                self._media_viewer = MediaViewer.open(images, idx)
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开媒体查看器失败: %s", exc)
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(str(url)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开链接失败: %s", exc)

    def _collect_session_images(self) -> list:
        """收集当前会话中的本地图片路径（附件图片 + 生图结果）。"""
        images: List[str] = []
        for m in self._messages:
            content = str(m.get("content") or "")
            if content.startswith("[生成图片]"):
                p = content[len("[生成图片]"):].strip()
                if p:
                    images.append(p)
            for att in (m.get("attachments") or []):
                if att:
                    images.append(str(att))
        seen = set()
        out = []
        for p in images:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    # ------------------------------------------------------------ V2-E4: 会话内查找
    def _toggle_find_bar(self) -> None:
        """显示 / 隐藏会话内查找条。"""
        self._find_bar.setVisible(not self._find_bar.isVisible())
        if self._find_bar.isVisible():
            self._find_input.setFocus()
            self._find_input.selectAll()
        else:
            self._clear_find_highlight()

    def _find_next(self, back: bool = False) -> None:
        """在 _messages 中查找关键词，滚动到匹配气泡并高亮。"""
        q = (self._find_input.text() or "").strip().lower()
        if not q or not self._messages:
            return
        n = len(self._messages)
        start = self._find_index if self._find_index is not None else -1
        step = -1 if back else 1
        for k in range(n):
            i = (start + step * (k + 1)) % n
            content = str(self._messages[i].get("content") or "").lower()
            if q in content:
                self._find_index = i
                self._apply_find_highlight(i)
                if i < len(self._bubble_widgets):
                    self._chat_scroll.ensureWidgetVisible(
                        self._bubble_widgets[i], 0, 80)
                return
        styled_info(self, "查找", f"未找到包含「{self._find_input.text().strip()}」的消息。")

    def _apply_find_highlight(self, index: int) -> None:
        """高亮指定索引的气泡（动态属性 findMatch，由 root_qss 描边）。"""
        self._clear_find_highlight()
        if index < len(self._bubble_widgets):
            b = self._bubble_widgets[index]
            b.setProperty("findMatch", True)
            _st = QApplication.style()
            if _st is not None:
                _st.unpolish(b)
                _st.polish(b)
            self._find_highlight = index

    def _clear_find_highlight(self) -> None:
        if self._find_highlight is not None and self._find_highlight < len(self._bubble_widgets):
            b = self._bubble_widgets[self._find_highlight]
            b.setProperty("findMatch", False)
            _st = QApplication.style()
            if _st is not None:
                _st.unpolish(b)
                _st.polish(b)
        self._find_highlight = None

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

    # ------------------------------------------------ V3: 程序侧技能工具
    def _handle_program_skill(self, skill: Dict[str, Any], clean: str,
                              pending: List[str]) -> None:
        """执行 \\@novellearn / \\@weather / \\@calorie / \\@ponywebsite
        （程序直接处理，不进普通对话）。"""
        trig = str(skill.get("trigger") or "").strip().lower()
        text = self._strip_skill_display(clean or "").strip()
        # 与文件工具一致：先把用户输入显示成用户气泡，再执行工具
        try:
            uname = self._user_name()
            attach_display = ""
            if pending:
                names = "、".join(Path(p).name for p in pending[:3])
                attach_display = f"[附件] {names}"
            msg: Dict[str, Any] = {"role": "user", "name": uname,
                                   "content": clean, "ts": time.time()}
            ub = self._append_bubble(uname, text or attach_display or f"\\@{trig}",
                                     is_user=True, msg=msg)
            self._messages.append(msg)
            uwrap = getattr(ub, "_wrap", None)
            if uwrap is not None:
                uwrap._msg = msg    # 让「删除」按钮能移除这条记录
        except Exception as exc:  # noqa: BLE001
            logger.warning("程序侧技能用户气泡失败: %s", exc)
        if trig == "weather":
            self._run_weather(text)
        elif trig == "novellearn":
            self._run_novellearn(text, pending)
        elif trig == "calorie":
            self._run_calorie(text)
        elif trig == "ponywebsite":
            self._run_ponywebsite(text)

    def _spawn_tool_worker(self, task: Any, on_done: Any, on_fail: Any) -> None:
        """统一在子线程执行程序侧技能（保活引用，避免 QThread 被 GC 回收崩溃）。"""
        try:
            from utils.async_worker import spawn_worker
            self._tool_worker = spawn_worker(task, on_done=on_done, on_fail=on_fail)
        except Exception as exc:  # noqa: BLE001
            on_fail(str(exc))

    def _append_tool_card(self, title: str, html: str) -> None:
        """把工具结果（HTML5 卡片 / 富文本）作为角色气泡插入对话框。"""
        try:
            name = f"{self._roles.display_name(self._current_role)}·{title}"
            msg: Dict[str, Any] = {"role": "assistant", "name": name,
                                   "content": html, "ts": time.time()}
            bubble = self._append_bubble(name, html, is_stream=False,
                                         role=self._current_role, msg=msg)
            self._messages.append(msg)
            wrap = getattr(bubble, "_wrap", None)
            if wrap is not None:
                wrap._msg = msg    # 让「删除」按钮能移除这条卡片
            # 卡片里的链接（Windy / 图片等）可点击，交给统一链接处理器
            bubble.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            bubble.setOpenExternalLinks(False)
            bubble.linkActivated.connect(self._on_media_link_activated)
            self._scroll_to_bottom(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具结果卡片插入失败: %s", exc)
            self._append_notice(f"工具结果展示失败：{exc}")

    # ------------------------------------------------ \@weather 天气
    def _run_weather(self, text: str) -> None:
        """\\@weather：和风天气取数 + HTML5 卡片展示。

        定位（需求）：**不用 GeoAPI**——先取本机公网 IP → 判断 IP 归属地 →
        内置静态城市表（`data/qweather_cities.csv`，含官方 LocationID）匹配所在地；
        用户手写城市名时同样只查静态城市表。
        """
        try:
            import api_vault
            import weather_service as ws
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"天气工具加载失败：{exc}")
            return
        prof = (api_vault.find_profile("和风天气")
                or api_vault.find_profile("qweather")
                or api_vault.find_profile("天气"))
        key = str((prof or {}).get("key") or "").strip()
        if not key:
            self._append_notice(
                "还没有配置天气接口：设置 → 常用接口类 API 管理器 → 选中「和风天气」"
                "（没有就点「＋ 新建」）→ 填「地址」和「API KEY」后点「保存修改」。\n"
                "KEY 与 API Host 都在 https://console.qweather.com → 项目管理 / 设置 里查看。")
            return
        city = ws.parse_city(text)
        opts = ws.parse_options(text)
        extra = ("（额外展示：" + "、".join(ws.OPT_TITLES[o] for o in opts) + "）"
                 if opts else "")
        self._append_notice(
            f"正在查询{'「' + city + '」' if city else '本机 IP 所在地（先取 IP 归属地）'}"
            f"的天气…{extra}")

        def _task() -> Dict[str, Any]:
            # 需求：\\@weather **不用 GeoAPI 动态取地址** —— 定位链路固定为
            # 「本机 IP → IP 归属地 → 内置静态城市表 / 用户写的城市」，因此这里
            # 既不读历史配置里的 geo= 地址，也不传 geo_base（GeoAPI 只在
            # QWeatherClient.city_lookup 里用到，而它已不再被调用）。
            client = ws.QWeatherClient(
                key=key, base=str((prof or {}).get("base") or ""))
            return ws.fetch_bundle(client, city, opts)

        def _done(bundle: Dict[str, Any]) -> None:
            self._append_tool_card("天气",
                                   ws.render_weather_html(bundle, accent=self._accent))

        def _fail(msg: str) -> None:
            self._append_notice(f"天气查询失败：{msg}")

        self._spawn_tool_worker(_task, _done, _fail)

    # ------------------------------------------------ #tothemoon 观星 / 天象
    #: 提示词里出现这些词 → 交给**模型**组织（天象清单要检索与筛选，不是纯数据）
    _SKY_LLM_KEYWORDS = ("天象", "彗星", "流星雨", "月食", "日食", "深空天体",
                         "黄道光", "超级月亮", "银河之眼", "峨眉月", "新月抱旧月")

    def _sky_wants_llm(self, text: str) -> bool:
        """`#tothemoon` 这次是「查观测条件」还是「查天象」。

        「查询天象 / 查询专业天象」要的是**一个月内当地可见的天象清单**（需要检索、
        筛选、按可见性判断），程序侧算不出来 → 交给模型；只写「观测条件」时才是纯数据，
        由程序直接出卡片（快、不烧 LLM、不编造）。
        """
        low = str(text or "").lower()
        return any(k in low for k in self._SKY_LLM_KEYWORDS)

    #: 「专业天象」档：还要给彗星 / 深空 / 黄道光 → 需要抓实时资料
    _SKY_PRO_KEYWORDS = ("专业天象", "彗星", "深空天体", "黄道光")

    def _sky_pro_mode(self, text: str) -> bool:
        return any(k in str(text or "") for k in self._SKY_PRO_KEYWORDS)

    def _start_sky_chat(self, clean: str, pending: List[str],
                        skill_ctx: str = "",
                        pub_names: Optional[List[str]] = None) -> None:
        """`#tothemoon` + 天象类关键词：取本机 IP + **算出天象** → 注入上下文 → 交给模型。

        需求（用户反馈「AI 说查不到」全写未取得）：celescan.net 是 JS 单页应用，
        抓不到正文、模型也读不到，于是只能编或写「未取得」。现在由**程序**把能确定性
        计算的天象（月相 / 朔望 / 超级月亮 / 蛾眉月 / 亮星合月 / 日月食初判 / 流星雨 /
        黄道光 / 银河之眼）算出来（见 `sky_events`），专业档再抓彗星资料，一并作为
        system 上下文发给模型 —— 模型直接采用这些数，**不要再写「未取得」**。
        """
        pro = self._sky_pro_mode(clean)
        self._append_notice(
            f"正在读取本机 IP 归属地并计算天象（{'、'.join(pub_names or []) or 'tothemoon'}"
            f"{'，专业档' if pro else ''}）…")

        def _task() -> Dict[str, Any]:
            import sky_events as events
            import sky_service as sky
            site = sky.resolve_sky_site(clean)
            rep = events.build_event_report(site, days=30,
                                            tz_hours=sky.tzshift())
            comets = ""
            if pro:
                # 彗星亮度与位置必须靠实时资料：尽力抓，失败就留待模型说明「需核对」
                try:
                    comets = self._pool.web_fetch(
                        "https://starwalk.space/zh-Hans/news/upcoming-comets",
                        max_chars=6000)
                except Exception as exc:  # noqa: BLE001
                    logger.info("彗星资料抓取失败（忽略）：%s", exc)
            return {"site": site, "events": rep, "comets": comets}

        def _go(res: Dict[str, Any]) -> None:
            import sky_events as events
            res = res or {}
            site = res.get("site") or {}
            parts: List[str] = []
            if site:
                adm = str(site.get("adm") or "")
                parts.append(
                    "【本机位置】本机 IP 归属地："
                    + str(site.get("name") or "")
                    + (f"（{adm}）" if adm else "")
                    + "，坐标 " + str(site.get("lat") or "") + ","
                    + str(site.get("lon") or "")
                    + "。用户说的「本地 / 我这里」都指这里，**不要再问用户在哪个城市**；"
                      "天象只报告该地区可见的。")
            rep = res.get("events")
            if rep:
                parts.append(
                    "【天象数据（程序已算好，**直接采用**；除非本段里明确写了「需核对」，"
                    "否则不要写「未取得」）】\n"
                    + events.render_event_text(rep))
            if str(res.get("comets") or "").strip():
                parts.append("【彗星资料（抓取自 starwalk，供专业天象档使用；"
                             "只采用其中明确列出的彗星与亮度）】\n"
                             + str(res["comets"])[:6000])
            extra = "\n".join(parts)
            try:
                self._start_chat(
                    clean, list(pending or []),
                    skill_context=((skill_ctx or "") + "\n" + extra).strip(),
                    pro_active=True, skillspub_names=list(pub_names or []))
            except Exception as exc:  # noqa: BLE001
                self._on_error(f"发送失败：{exc}")

        def _fail(msg: str) -> None:
            logger.warning("观星定位失败（交给模型时说明）：%s", msg)
            _go({})

        self._spawn_tool_worker(_task, _go, _fail)

    def _start_transient_chat(self, clean: str, pending: List[str],
                              skill_ctx: str = "",
                              pub_names: Optional[List[str]] = None) -> None:
        """`#tothestars`：程序取数（Rochester 新星比对 / VSX 检索 / 链接生成）
        → 注入上下文 → 交给模型解读（变星类型、可见性、下一步观测建议）。"""
        self._append_notice("正在核查 Rochester 新星表与 VSX 变星表…（约需 1~2 分钟）")

        def _task() -> Dict[str, Any]:
            import transient_service as ts
            return ts.build_transient_report(clean)

        def _go(report: Dict[str, Any]) -> None:
            import transient_service as ts
            extra = ts.render_transient_text(report or {})
            try:
                self._start_chat(
                    clean, list(pending or []),
                    skill_context=((skill_ctx or "") + "\n" + extra).strip(),
                    pro_active=True, skillspub_names=list(pub_names or []))
            except Exception as exc:  # noqa: BLE001
                self._on_error(f"发送失败：{exc}")

        def _fail(msg: str) -> None:
            logger.warning("巡天取数失败（交给模型说明）：%s", msg)
            _go({"coords": None, "errors": [msg]})

        self._spawn_tool_worker(_task, _go, _fail)

    def _handle_program_pub_skill(self, names: List[str], clean: str,
                                  all_names: Optional[List[str]] = None) -> None:
        """执行**程序侧**的 skill库 技能（`#tothemoon`）。

        需求（用户反馈「`#tothemoon 查询天象` 不是输出咨询的地址，而是查询本地 IP，
        直接查本地 IP 地址的天象」）：与 `\\@weather` 同款体验 —— 程序直接读本机 IP
        → 取 7timer 观测条件 → 出数据卡片，**不进普通对话**（不烧 LLM、不甩链接）。
        """
        text = self._strip_skill_display(clean or "").strip()
        text = (self._strip_pub_display(text).strip() or text)
        label = "、".join("#" + n for n in names) or "程序侧技能"
        # 与文件工具/程序侧技能一致：先把用户输入显示成用户气泡，再执行
        try:
            uname = self._user_name()
            msg: Dict[str, Any] = {"role": "user", "name": uname,
                                   "content": clean, "ts": time.time()}
            ub = self._append_bubble(uname, text or label, is_user=True, msg=msg)
            self._messages.append(msg)
            uwrap = getattr(ub, "_wrap", None)
            if uwrap is not None:
                uwrap._msg = msg
        except Exception as exc:  # noqa: BLE001
            logger.warning("程序侧 skill库 用户气泡失败: %s", exc)
        for name in names:
            if name == "tothemoon":
                self._run_sky(text)
        others = [n for n in (all_names or []) if n not in names]
        if others:
            self._append_notice(
                "本次由程序直接执行（不经模型）：" + label
                + "；同时点名的 " + "、".join("#" + n for n in others)
                + " 未执行，可单独发一条消息调用它。")

    def _run_sky(self, text: str) -> None:
        """`#tothemoon`：本机 IP 定位 + 7timer 观测条件（+ 月相/日出日落）→ 卡片。"""
        try:
            import sky_service as sky
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"观星模块加载失败：{exc}")
            return
        site = {}
        try:
            site = sky.resolve_sky_site(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("观星定位失败（继续走 IP 定位）：%s", exc)
        where = (f"「{site.get('name')}」" if site.get("source") == "table"
                 else "本机 IP 所在地（" + str(site.get("name") or "取 IP 归属地中") + "）")
        self._append_notice(f"正在读取 {where} 的观测条件（7timer 晴天钟）…")

        def _task() -> Dict[str, Any]:
            client = None
            try:
                import api_vault
                import weather_service as ws
                prof = (api_vault.find_profile("和风天气")
                        or api_vault.find_profile("qweather")
                        or api_vault.find_profile("天气"))
                key = str((prof or {}).get("key") or "").strip()
                if key:
                    # 和风 KEY 只用于补「月相 / 日出日落」，没有也不影响观星结论
                    client = ws.QWeatherClient(
                        key=key, base=str((prof or {}).get("base") or ""))
            except Exception as exc:  # noqa: BLE001
                logger.info("跳过高德天文补充（%s）", exc)
            return sky.build_sky_bundle(text, client=client)

        def _done(bundle: Dict[str, Any]) -> None:
            self._append_tool_card(
                "观星", sky.render_sky_html(bundle, accent=self._accent))

        def _fail(msg: str) -> None:
            self._append_notice(
                f"观星条件查询失败：{msg}\n（也可直接看 7timer 晴天钟："
                f"https://www.7timer.info/index.php）")

        self._spawn_tool_worker(_task, _done, _fail)

    # ------------------------------------------------ \@novellearn 小说学习
    def _run_novellearn(self, text: str, pending: List[str]) -> None:
        """\\@novellearn：总结上传的小说并存 JSON，供本次对话角色扮演复刻剧情。"""
        try:
            from novel_learn import NovelLearn, format_summary_md
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"小说学习工具加载失败：{exc}")
            return
        pool = getattr(self, "_pool", None)
        if pool is None:
            self._append_notice("模型客户端不可用，无法总结小说。")
            return
        if pending:
            path = pending[0]
            self._append_notice(
                f"正在学习《{Path(path).name}》：分段提炼中，长篇请稍候…")

            def _task() -> Dict[str, Any]:
                return NovelLearn(pool).summarize(path)

            def _done(res: Dict[str, Any]) -> None:
                data = (res or {}).get("data") or {}
                # 注入后续对话：本次对话即可按小说设定复刻剧情
                self._novel_context = NovelLearn.roleplay_context(data)
                self._append_tool_card("小说学习",
                                       self._render_body_html(str(res.get("markdown") or "")))
                self._append_notice(
                    "本次对话已载入该小说设定，可直接开始角色扮演复刻剧情。\n"
                    f"总结已存档：{res.get('path')}")

            def _fail(msg: str) -> None:
                self._append_notice(f"小说学习失败：{msg}")

            self._spawn_tool_worker(_task, _done, _fail)
            return
        # 无附件：读取已有存档 / 列出存档
        saved = NovelLearn.list_saved()
        if not saved:
            self._append_notice(
                "用法：先上传小说附件，再发送 \\@novellearn。\n"
                "总结会以 JSON 存到 novellearn/ 目录（文件名 + 年月日时分）。")
            return
        hit = NovelLearn.load_saved(text) if text else None
        if hit:
            data = hit.get("summary") or {}
            self._novel_context = NovelLearn.roleplay_context(data)
            self._append_tool_card("小说学习（存档）",
                                   self._render_body_html(format_summary_md(data)))
            self._append_notice("已载入该存档设定，可直接开始角色扮演复刻剧情。")
            return
        listing = "\n".join(
            f"- {s.get('book') or s.get('name')}（{s.get('saved_at')}）"
            for s in saved[:20])
        self._append_notice(
            f"已保存的小说总结（novellearn/）：\n{listing}\n"
            "输入 \\@novellearn 关键词 可读取对应总结并载入设定。")

    # ------------------------------------------------ \@calorie 饮食热量
    def _run_calorie(self, text: str) -> None:
        """\\@calorie：估算吃了多少热量 + 记入 data/dailylifedata.json + 膳食建议。"""
        try:
            from calorie_tracker import CalorieTracker
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"饮食热量工具加载失败：{exc}")
            return
        if not text:
            self._append_notice(
                "用法：\\@calorie 吃了什么（例：\\@calorie 一碗牛肉面加一个茶叶蛋）")
            return
        pool = getattr(self, "_pool", None)
        if pool is None:
            self._append_notice("模型客户端不可用，无法估算热量。")
            return
        self._append_notice("正在估算热量…（优先联网核对中国食物成分表）")

        def _task() -> Dict[str, Any]:
            res = CalorieTracker(pool).estimate(text)
            saved = CalorieTracker.append_record({
                "raw": text,
                "items": res.get("items") or [],
                "total_kcal": res.get("total_kcal") or 0,
                "advice": res.get("advice") or "",
            })
            return {"result": res, "saved": saved}

        def _done(out: Dict[str, Any]) -> None:
            res = (out or {}).get("result") or {}
            saved = (out or {}).get("saved") or {}
            rec = saved.get("record") or {}
            md = CalorieTracker.format_report(
                res, int(saved.get("day_total") or 0),
                str(saved.get("date") or ""), str(rec.get("meal") or ""))
            self._append_tool_card("饮食热量", self._render_body_html(md))

        def _fail(msg: str) -> None:
            self._append_notice(f"热量估算失败：{msg}")

        self._spawn_tool_worker(_task, _done, _fail)

    # ------------------------------------------- \@ponywebsite 小马网站检测
    def _run_ponywebsite(self, text: str) -> None:
        """\\@ponywebsite：ping 站点清单，统计丢包率与最小/平均/最大延迟。"""
        try:
            import ponywebsite_checker as pwc
        except Exception as exc:  # noqa: BLE001
            self._append_notice(f"小马网站检测工具加载失败：{exc}")
            return
        sites = pwc.load_sites()
        if not sites:
            self._append_notice(
                "还没读到站点清单：请在 "
                f"{pwc.site_file()}\n按「名称<Tab>网址」每行一个站点填写（# 开头为注释）。")
            return
        targets, opts = pwc.parse_target(text, sites)
        if not targets:
            kw = str(opts.get("keyword") or "").strip()
            listing = "、".join(str(s.get("name") or s.get("host"))
                                for s in sites[:20])
            self._append_notice(
                f"没有匹配「{kw}」的站点。现有站点：{listing}\n"
                "也可以只测一个网址：\\@ponywebsite derpibooru.org")
            return
        count = int(opts.get("count") or pwc.DEFAULT_COUNT)
        self._append_notice(
            f"正在 ping {len(targets)} 个站点（每站 {count} 包），请稍候…")

        def _task() -> Dict[str, Any]:
            return pwc.check_all(targets, count=count)

        def _done(payload: Dict[str, Any]) -> None:
            self._append_tool_card(
                "小马网站检测",
                pwc.render_result_html(payload, accent=self._accent))
            s = payload.get("summary") or {}
            bad = [str(r.get("name") or r.get("host"))
                   for r in (payload.get("results") or [])
                   if r.get("status") != pwc.STATUS_OK]
            if bad:
                self._append_notice("以下站点不是全通：" + "、".join(bad[:10]))
            else:
                self._append_notice(
                    f"{len(payload.get('results') or [])} 个站点全部在线，"
                    f"平均延迟 {s.get('avg_ms') or '—'} ms。")

        def _fail(msg: str) -> None:
            self._append_notice(f"小马网站检测失败：{msg}")

        self._spawn_tool_worker(_task, _done, _fail)

    # 超过该长度的系统/工具输出改为「摘要 + 折叠完整内容」，避免刷屏
    NOTICE_COMPRESS_LIMIT = 600

    def _append_notice(self, text: str,
                       choices: Optional[List[str]] = None,
                       choice_title: str = "") -> QWidget:
        """聊天区追加一条居中的灰色系统提示（不写入消息记录/存档）。

        用于 \\closed 等纯指令的即时反馈，不调用 LLM。

        需求：
        - 工具/加载输出过长时只显示摘要，完整内容折叠（记录而非铺开）；
        - 需要用户做出选择时（choices）给出 HTML5 风格按钮：点击只把选项
          填入输入框，不直接执行，用户补充后点「发送」才执行。
        """
        body = text or ""
        shown = body
        if len(body) > self.NOTICE_COMPRESS_LIMIT:
            shown = (body[:240].rstrip() + "\n…\n" + body[-120:].lstrip()
                     + f"\n（共 {len(body)} 字，完整内容见下方折叠块）")
        label = QLabel(shown)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(
            "color:#9aa0ac;font-size:12px;padding:6px 2px;"
            "background:rgba(108,142,245,0.06);border-radius:8px;")
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        lay.addWidget(label)
        if len(body) > self.NOTICE_COMPRESS_LIMIT:
            full = LongOutputBlock(accent=self._accent,
                                   title=f"完整输出（{len(body)} 字）")
            full.set_text(body, title=f"完整输出（{len(body)} 字）")
            lay.addWidget(full)
        if choices:
            row = ChoiceRow(list(choices), accent=self._accent,
                            title=choice_title or "点击选项填入输入框（不会直接发送）",
                            max_width=self._bubble_max_width())
            row.chosen.connect(self._on_choice_clicked)
            lay.addWidget(row)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, wrap)
        self._scroll_to_bottom()
        return wrap

    def _on_choice_clicked(self, text: str) -> None:
        """选择按钮被点击：只把选项填入输入框（不发送），等待用户点「发送」。

        需求：选择不直接执行——可以接着输入对话，点发送后才真正执行。
        """
        try:
            cur = self._input.toPlainText()
            merged = (cur.rstrip() + " " + text).strip() if cur.strip() else text
            self._input.setPlainText(merged)
            self._input.moveCursor(QTextCursor.End)
            self._input.setFocus()
        except Exception as exc:  # noqa: BLE001
            logger.warning("选项填入输入框失败: %s", exc)

    def _copy_message(self, bubble: Optional[QLabel]) -> None:
        """复制一条消息到剪贴板（酒馆式消息操作）。"""
        if bubble is None:
            return
        try:
            text = str(bubble.property("raw") or bubble.text() or "")
            QApplication.clipboard().setText(text)
            self._append_notice("已复制这条消息")
        except Exception as exc:  # noqa: BLE001
            logger.warning("复制消息失败: %s", exc)

    # ------------------------------------------------ 长回复压缩 / 选项按钮
    # 需求（用户）：「长回复不要隐藏」——正文气泡直接铺**完整正文**，不再出现
    # 「…（已折叠，共 N 字，展开查看完整内容）」的首尾摘要（1500 字上限太容易
    # 命中：一轮 2000 字的正常总结就会被折叠）。
    # 仅保留极端兜底：超过 24000 字（与流式纯文本窗口 STREAM_PLAIN_LIMIT 同阈值）
    # 才退化为「摘要 + 完整内容折叠块」，避免 Qt 富文本排版超大文档卡死界面。
    REPLY_COMPRESS_LIMIT = 24000

    # 长输出性能保护（需求：长对话 / 长回复不再卡顿、不再因内存暴涨闪退）
    STREAM_RICH_LIMIT = 8000        # 流式期间富文本渲染上限，超过只渲染尾部
    STREAM_PLAIN_LIMIT = 24000      # 超过则流式期间改用纯文本（渲染成本极低）
    STREAM_TAIL_CHARS = 4000        # 流式期间最多渲染最后这么多字符

    #: **程序侧**的 skill库 技能：`#技能名` 点名时由程序直接执行（不进普通对话）。
    #: 需求（用户反馈）：「`#tothemoon 查询天象` 不该输出一个查询地址/链接，而应直接
    #: 读本机 IP、查 IP 所在地的观测条件」→ 与 `\@weather` 同款体验（见 `_run_sky`）。
    PROGRAM_PUB_SKILLS = {"tothemoon"}

    def _summary_text(self, text: str) -> str:
        """超长文本 → 首尾摘要（完整原文另存折叠块，界面不铺开）。"""
        return (text[:420].rstrip() + "\n\n…（已折叠，共 "
                f"{len(text)} 字，展开查看完整内容）\n\n" + text[-160:].lstrip())

    def _attach_full_block(self, bubble: Optional[QLabel], text: str) -> None:
        """给超长回复挂「完整内容」折叠块（不改动气泡当前显示）。"""
        if bubble is None or len(text or "") <= self.REPLY_COMPRESS_LIMIT:
            return
        lay, idx = self._locate_in_message(bubble)
        if lay is None or idx < 0:
            return
        blk = getattr(bubble, "_full_block", None)
        if blk is None:
            blk = LongOutputBlock(accent=self._accent)
            bubble._full_block = blk
            lay.insertWidget(idx + 1, blk)
        blk.set_text(text, title=f"完整回复（{len(text)} 字）")

    def _maybe_compress_bubble(self, bubble: Optional[QLabel],
                               text: str) -> None:
        """超长回复：气泡只显示首尾摘要，完整原文进折叠块（记录仍完整）。

        需求：工具输出 / 加载的角色设定等很长的内容用压缩、摘要、简化的方式
        输出，完整内容折叠（要记录而不是铺在界面上）。
        """
        if bubble is None or len(text or "") <= self.REPLY_COMPRESS_LIMIT:
            return
        bubble.setText(self._render_body_html(self._summary_text(text)))
        self._attach_full_block(bubble, text)

    def _append_choice_row(self, bubble: Optional[QLabel],
                           options: List[str], title: str = "") -> None:
        """在消息下方挂一行 HTML5 风格选择按钮。

        点击只把选项填入输入框（不直接执行），用户可继续输入，点发送才执行。
        """
        if bubble is None or not options:
            return
        wrap = getattr(bubble, "_wrap", None)
        if wrap is None:
            return
        lay = wrap.layout()
        if lay is None:
            return
        row = ChoiceRow(options, accent=self._accent,
                        title=title or "点击选项填入输入框（不会直接发送）",
                        max_width=self._bubble_max_width())
        row.chosen.connect(self._on_choice_clicked)
        # 锚定插入：紧跟「承载正文的那一行」之后。
        # 旧实现用 lay.count()-1 反推操作行位置，一旦该消息没有操作行，
        # 选项行会被插到正文**上方**（表现为「思维链框跑到选项下面」）。
        row_i = self._row_item_index(wrap, bubble)
        if row_i >= 0:
            lay.insertWidget(row_i + 1, row)
        else:
            lay.insertWidget(max(0, lay.count() - 1), row)
        # 收尾再跑一次不变量（选项行必须在正文下方）
        self._enforce_message_order(wrap)

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
        # 新建会话即开启新的角色扮演作用域（「每个对话独立」时每个会话一份记忆）
        self._roleplay_scope_id = str(uuid.uuid4().hex[:8])
        # 切换会话时清空待上传附件（Gemini 风格：附件只属于当前输入）
        if hasattr(self, "_attach_bar"):
            self._clear_pending_attachments()
        # 当前会话已不存在 → 清除历史列表中的主题色框选
        if hasattr(self, "_history_list"):
            self._highlight_current_history()
        self._prev_speaker = ""
        self._clear_chat_area()
        self._session_title.setText("新会话")
        # 需求：用量统计随会话重置（本对话累计）
        if hasattr(self, "_stats_label"):
            self._reset_token_stats()
        # 新会话复位专业模式 / \closed 状态（立绘恢复普通情绪；需求 v3-2/3/4）
        self._skill_closed = False
        # 新会话开启新的技能产出项目（skilluserdata 下一个对话一个文件夹）
        self._skilldata_uid_value = ""
        self._skill_workdir = ""
        self._skilldata_denied = False
        self._pro_skill_active = False
        self._last_portrait_key = None
        self._last_stream_render = 0.0

    def _save_session(self, quiet: bool = False) -> None:
        if not self._messages:
            return
        now = int(time.time())
        is_new = self._session_path is None
        if self._session_path is None:
            self._session_path = self._cfg.conversations_dir / (
                f"session_{self._session_key()}_{now}.json")
        data = {
            "role": self._current_role,
            "group": self._current_group,
            "updated_at": now,
            "messages": self._messages,
            # 需求：开启角色扮演后「忘记其他对话」——会话按空间打标，
            # 角色扮演会话与日常会话在历史侧栏中互不显示。
            "roleplay": bool(getattr(self, "_roleplay_on", False)),
            # 「每个对话独立」时记录本会话作用域，重载后沿用同一份记忆
            "scope": (self._roleplay_scope()
                      if getattr(self, "_roleplay_on", False) else ""),
            # 需求：技能产出目录——本对话在 skilluserdata 的项目标识 + 选择的
            # 工作文件夹随会话存档，重载会话后仍对应同一个项目文件夹
            "skilldata": {
                "uid": self._skilldata_uid(),
                "workdir": str(getattr(self, "_skill_workdir", "") or ""),
            },
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
        # 性能：历史列表重建要读全部会话文件并重建控件（会话越多越慢），
        # 多轮对话时每轮都重建是「越聊越卡」的主因之一——只在会话刚创建或
        # 前两条消息时刷新，之后等标题生成/切换会话/删除时再刷新。
        if hasattr(self, "_history_list") and (
                is_new or len(self._messages) <= 2):
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

    def _read_session_meta(self, path: Any) -> Optional[Dict[str, Any]]:
        """读取会话 json（按 mtime/大小缓存）。

        性能：历史列表每次重建都要把**所有**会话文件读一遍并解析 json，
        会话越多越慢、多轮对话时越聊越卡。这里按文件修改时间缓存解析结果，
        未变动的会话不再重复读盘。
        """
        try:
            p = Path(str(path))
            st = p.stat()
        except Exception:  # noqa: BLE001
            return None
        key = str(p)
        sig = (int(st.st_mtime_ns), int(st.st_size))
        hit = self._SESSION_META_CACHE.get(key)
        if hit is not None:
            if hit[0] == sig:
                return hit[1]
            self._SESSION_META_CACHE.pop(key, None)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        self._SESSION_META_CACHE[key] = (sig, data)
        return data

    def _forget_session_meta(self, path: Any) -> None:
        """会话文件被删除/移动后清掉缓存条目。"""
        try:
            self._SESSION_META_CACHE.pop(str(Path(str(path))), None)
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
            data = self._read_session_meta(path)
            if data is None:
                continue
            ts = data.get("updated_at")
            if not isinstance(ts, (int, float)):
                m = re.search(r"_(\d+)\.json$", path.name)
                ts = int(m.group(1)) if m else 0
            sessions.append((int(ts), path, data))
        sessions.sort(key=lambda s: s[0], reverse=True)   # 新的在上，老的在下面
        # V2-E2：置顶会话优先（置顶内部按置顶顺序，其余按时间倒序）
        pinned_list = self._load_pinned_sessions()
        if pinned_list:
            pinned_set = set(pinned_list)

            def _sort_key(s) -> tuple:
                p = str(s[1])
                if p in pinned_set:
                    return (0, pinned_list.index(p))
                return (1, -s[0])

            sessions.sort(key=_sort_key)
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
                # 需求：开启角色扮演后只看角色扮演会话，关闭后只看日常会话
                # （旧存档无 roleplay 字段，按日常处理，兼容历史数据）
                if self._session_matches_mode(data) is False:
                    continue
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

    def _on_history_search_changed(self, text: str) -> None:
        """历史会话搜索：防抖 300ms；空查询恢复普通列表。"""
        if self._history_search_timer is not None:
            self._history_search_timer.stop()
        self._history_search_timer = QTimer(self)
        self._history_search_timer.setSingleShot(True)
        self._history_search_timer.timeout.connect(
            lambda: self._apply_history_search(text))
        self._history_search_timer.start(300)

    def _apply_history_search(self, text: str) -> None:
        """执行历史搜索并渲染结果（复用 _HistoryRow，点击加载）。"""
        while self._history_layout.count() > 0:
            item = self._history_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._history_layout.addStretch(1)
        q = (text or "").strip()
        if not q:
            self._refresh_history_list()
            return
        try:
            from session_search import search_sessions
            results = search_sessions(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("历史会话搜索失败: %s", exc)
            return
        for r in results:
            try:
                title = r.get("title") or r.get("role") or "会话"
                kind = "标题" if r.get("match_kind") == "title" else "正文"
                snippet = (r.get("snippet") or "").strip()[:28]
                info = f"{r.get('role') or ''} · {kind} · {snippet}"
                row = _HistoryRow(str(r.get("path")), title, info)
                row.clicked.connect(self._on_history_clicked)
                row.delete_requested.connect(self._delete_history_path)
                row.context_menu_requested.connect(self._show_history_menu)
                self._history_layout.insertWidget(
                    self._history_layout.count() - 1, row)
            except Exception:  # noqa: BLE001
                continue
        if not results:
            empty = QLabel("未找到匹配的会话")
            empty.setStyleSheet(
                f"color:{TEXT_LIGHT}; font-size:12px; padding:10px;")
            self._history_layout.insertWidget(
                self._history_layout.count() - 1, empty)

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
        self._forget_session_meta(path)
        self._refresh_history_list()

    def _on_history_clicked(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "加载失败", str(exc))
            return
        self._new_session(quiet=True)
        self._session_path = Path(path)
        # 恢复该会话的角色扮演作用域（「每个对话独立」时沿用同一份记忆）
        self._roleplay_scope_id = str(data.get("scope") or "") or uuid.uuid4().hex[:8]
        # 用主题色框选当前正在查看的对话
        self._highlight_current_history()
        self._messages = list(data.get("messages") or [])
        # 需求：主界面角色名**完整显示**（用户反馈「被 … 截断了」）—— 这里不再按
        # 固定字宽截断（旧的 truncate_wide(x, 24) 会把「Twilight_Sparkle暮光闪闪」
        # 切成「…暮光闪…」）。整条标题交给布局：宽度够就完整显示，真不够时给悬浮提示。
        hist_role = data.get("role") or ""
        hist_title = (data.get("title")
                      or data.get("group")
                      or (self._roles.sidebar_label(hist_role) if hist_role else "")
                      or "会话")
        hist_title = self._roles.truncate_wide(hist_title, 60)   # 仅防极端超长撑爆布局
        self._session_title.setText(f"{hist_title}（历史）")
        self._session_title.setToolTip(f"{hist_title}（历史会话）")
        self._clear_chat_area()
        # 需求：切换历史会话后用量统计重新计数（历史轮次无 usage 数据）
        if hasattr(self, "_stats_label"):
            self._reset_token_stats()
        # 性能：历史回放是「一次性灌进几十上百条消息」，逐条都建折叠块/按钮行
        # 会把主线程打满（表现为点历史卡死）。因此：
        #   - 选项按钮只给最后一条带选项的消息（更早的选项已过期，且数量最多）；
        #   - 思维链 / 剧情设计折叠块只给最近 HISTORY_BLOCK_LIMIT 条渲染。
        _ci = [_i for _i, _m in enumerate(self._messages) if _m.get("choices")]
        # 最后 8 条带选项的消息才重建按钮（更早的选项基本已过期，且按钮行太多会
        # 拖慢回放）；最近几条一定保留，重载会话后按钮不会「凭空消失」。
        last_choice_idx = set(_ci[-8:])
        block_from = max(0, len(self._messages) - self.HISTORY_BLOCK_LIMIT)
        for _i, msg in enumerate(self._messages):
            is_user = msg.get("role") == "user"
            content = msg.get("content") or ""
            _legacy_cot = ""      # 旧存档里残留的思考段（回放时抽出来挂思维链）
            msg_name = msg.get("name") or ("用户" if is_user else self._current_role)
            msg_role = None
            if not is_user and msg_name in self._roles.list_roles():
                # 群聊/多角色历史：名字显示角色显示名，头像使用该角色立绘（需求 3）
                msg_role = msg_name
                msg_name = self._roles.display_name(msg_name)
            # 历史消息统一隐藏【】情绪标签（只显示对话正文）
            if not is_user:
                # 存档里是模型原始输出：先做显示层清洗（<details> / <w2g> /
                # <!-- 指令注释 --> 在生成时就已被剥离，回放同样不能显示）；
                # 旧版本存档可能把整段思考留在 content 里（生成时未拆分）→
                # 这里再拆一次，思考段挂思维链折叠块，正文才进气泡
                content, _legacy_cot = history_display_parts(content)
                content = self._roles.strip_emotion_tags(content)
                # 需求：历史群聊正文不显示 [角色名]: / 角色名： 前缀
                content = ChatWorker._strip_group_prefix(
                    content, self._roles.list_roles())
            else:
                # 需求：历史用户消息显示剥离 \@触发词 指令（记录保留完整内容）
                content = self._strip_skill_display(content)
            bubble = self._append_bubble(msg_name, content, is_user=is_user,
                                         role=msg_role, msg=msg,
                                         animate_new=False,
                                         with_blocks=(_i >= block_from))
            # 历史会话：用户消息下方恢复已发送文件的超链接
            if is_user and bubble is not None and msg.get("attachments"):
                self._append_attachment_links(bubble, msg.get("attachments"))
            # 需求：已记录的思维链/摘要用折叠块回放（记录仍在，界面默认收起）
            _th_text = str(msg.get("thinking") or "").strip()
            if _legacy_cot and _legacy_cot not in _th_text:
                # 旧存档里残留的思考段：与 thinking 字段合并后一起回放
                _th_text = ((_th_text + "\n" + _legacy_cot).strip()
                            if _th_text else _legacy_cot)
            if (bubble is not None and not is_user and _i >= block_from
                    and _th_text
                    and self._msg_thinking_visible(msg)):
                blk = getattr(bubble, "_thinking_block", None)
                if blk is None:
                    # with_blocks=False 的旧消息（或历史回放限流）也要能显示：
                    # 临时切换到该气泡补建折叠块（插在正文上方）
                    _old_cur = self._current_bubble
                    self._current_bubble = bubble
                    blk = self._ensure_thinking_block()
                    self._current_bubble = _old_cur
                if blk is not None:
                    blk.set_text(_th_text)
                    blk.setVisible(True)
            # 需求：剧情设计同样以折叠块回放（与思维链同款，默认收起）
            if (bubble is not None and not is_user and _i >= block_from
                    and str(msg.get("plot") or "").strip()
                    and self._msg_thinking_visible(msg)):
                pblk = getattr(bubble, "_plot_block", None)
                if pblk is None:
                    old = self._current_bubble
                    self._current_bubble = bubble
                    pblk = self._ensure_plot_block()
                    self._current_bubble = old
                if pblk is not None:
                    pblk.set_text(str(msg.get("plot") or ""))
                    pblk.setVisible(True)
            # 需求：历史消息里的选项也用按钮回放（点击只填入输入框）——
            # 只渲染最后一条带选项的消息，避免几十行按钮拖垮布局
            if bubble is not None and msg.get("choices") and _i in last_choice_idx:
                # 旧存档可能把正文/计划文本误存成选项：过滤后再渲染成按钮
                _opts_back = sanitize_stored_choices(msg.get("choices"))
                if _opts_back:
                    self._append_choice_row(bubble, _opts_back)
            # 需求：回放同样保证「思维链 → 剧情设计 → 正文气泡 → 选项行」的顺序
            if bubble is not None and not is_user:
                self._reorder_message_blocks(bubble)
            if bubble is not None:
                self._enforce_message_order(getattr(bubble, "_wrap", None))
        # 需求：回放结束后全量校正一次「思考过程/剧情设计」折叠块的位置
        self._normalize_message_blocks()
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

        # V2-F3：设置搜索框（输入关键词，隐藏不匹配分节；清空恢复全部）
        settings_search = QLineEdit()
        settings_search.setPlaceholderText("搜索设置项（如：记忆 / API / 主题）…")
        settings_search.setClearButtonEnabled(True)
        settings_search.setObjectName("settingsSearch")
        outer.insertWidget(0, settings_search)

        # 收集所有分节（标题 label + 卡片），供搜索过滤
        section_items: List[tuple] = []

        def _filter_settings(keyword: str) -> None:
            kw = (keyword or "").strip().lower()
            if not kw:
                for tl, bx in section_items:
                    tl.show()
                    bx.show()
                return
            for tl, bx in section_items:
                hit = kw in tl.text().lower()
                if not hit:
                    for w in bx.findChildren(QLabel):
                        if kw in w.text().lower():
                            hit = True
                            break
                tl.setVisible(hit)
                bx.setVisible(hit)

        settings_search.textChanged.connect(_filter_settings)

        def _section(title: str) -> "QFormLayout":
            """左对齐标题独立一行 + 下方卡片（标题不压住框）。"""
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            body.addWidget(title_label)
            box = QFrame()
            box.setObjectName("settingsCard")
            form = QFormLayout(box)
            form.setContentsMargins(14, 14, 14, 14)
            # V2-F3：登记分节供搜索过滤
            section_items.append((title_label, box))
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
        # 思考型模型会把 max_tokens 预算全耗在推理上导致小模型无输出，默认关闭思考
        small_thinking_off = QCheckBox("小模型关闭思考模式")
        small_thinking_off.setChecked(self._cfg.small_thinking_off())
        small_thinking_tip = QLabel(
            "记忆降噪/提炼、群聊判定、技能审查等小模型调用的 Token 预算很小，"
            "思考型模型会把预算耗在推理上导致返回空内容，建议保持开启")
        small_thinking_tip.setWordWrap(True)
        small_thinking_tip.setObjectName("statusText")
        _st_col = QVBoxLayout()
        _st_col.setSpacing(2)
        _st_col.addWidget(small_thinking_off)
        _st_col.addWidget(small_thinking_tip)
        api_form.addRow("小模型思考模式", _st_col)
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
        # 需求（用户反馈「上传的 PDF 识别内容会被截断」）：单文件注入上限可调，
        # 默认 32000 字；超过时保留开头 70% + 结尾 30%，中间缺口写明字数。
        attach_chars = QSpinBox()
        attach_chars.setRange(2000, 400000)
        attach_chars.setSingleStep(4000)
        attach_chars.setSuffix(" 字")
        attach_chars.setValue(int(self._cfg.attach_max_chars()))
        attach_chars.setToolTip(
            "单次对话里，一个文件最多把多少字原文交给模型（Excel/Word/PPT/PDF 通用）。\n"
            "超出时保留「开头 70% + 结尾 30%」，中间缺口会写明字数。\n"
            "调大可让长文档更完整；请勿超过主模型上下文（32000 字约 2~3 万 token）。")
        vis_form.addRow("单文件注入上限", attach_chars)
        # 需求（用户反馈「MoE 只能发送摘要，分析不了图」）：主模型支持图片输入时
        # 直接把图发给它，而不是只给视觉模型的转述；未配置时按模型名自动判断。
        main_vision_box = QCheckBox(
            "主对话模型支持图片输入（直接把图片发给主对话模型分析）")
        main_vision_box.setToolTip(
            "勾选后：图片随消息直接发给主对话模型（它亲自看图），不再只吃视觉模型的摘要；\n"
            "不勾选：由上面的「视觉模型」按你的问题分析图片，再把分析结果交给主模型。\n"
            "主对话模型如 deepseek-chat / deepseek-flash 是纯文本模型，请勿勾选。")
        _mv = self._cfg.get("api", "main_vision", default=None)
        if _mv is None:
            _mv = ChatWorker._main_model_sees_images_for(
                str(self._cfg.main_model() or ""))
        main_vision_box.setChecked(bool(_mv))
        vis_form.addRow("", main_vision_box)

        # ---------- 2.5 生图 API（需求 v5：样式同多模态）+ 保存路径 ----------
        img_form = _section("生图 API（\\@image / 提示词生图 / 模型输出图片）")
        image_model = QLineEdit(self._cfg.get("api", "image_model", default=""))
        image_model.setPlaceholderText("例如 dall-e-3 / gpt-image-1 / nano-banana-2-lite")
        image_base = QLineEdit(self._cfg.get("api", "image_base", default=""))
        image_base.setPlaceholderText(
            "OpenAI 兼容：https://xxx/v1（程序打 {地址}/images/generations）；"
            "异步任务式：https://api.beatapi.io/v1/images/tasks")
        image_base.setToolTip(
            "支持两类协议，程序会自动适配：\n"
            "1) OpenAI 兼容 /images/generations（同步返回图片）；\n"
            "2) 异步任务式 /v1/images/tasks（提交任务后轮询，如 BeatAPI、"
            "部分聚合站——地址可直接填 …/v1/images/tasks，也可只填站点根地址）\n\n"
            "提示：部分聚合站（如 maas.qianwenaiapi.com）的 OpenAI 兼容路径是\n"
            "/compatible-mode/v1 而不是 /v1；若填错成 /api/v1，程序会自动\n"
            "尝试 {站点根}/compatible-mode/v1 与 {站点根}/v1，通常无需手动改。")
        image_key = QLineEdit(self._cfg.get("api", "image_key", default=""))
        image_key.setEchoMode(QLineEdit.Password)
        img_form.addRow("生图模型", image_model)
        img_form.addRow("API 地址（留空复用主 API）", image_base)
        img_form.addRow("API Key", image_key)
        _img_hint = QLabel(
            "任务式生图（如 BeatAPI）请求体只接受 模型 + 提示词，因此「画面描述」"
            "请直接写在输入框里（\\@image 后面那段文字即提示词）。任务式生图通常"
            "需要十几秒到数分钟，气泡会一直显示「正在生成图片」。")
        _img_hint.setWordWrap(True)
        _img_hint.setStyleSheet("color:#9aa0ac;font-size:11px;")
        img_form.addRow("", _img_hint)
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
            "搜索引擎 API 地址：Brave 填 https://api.search.brave.com/res/v1/web/search；"
            "百度千帆 AI 搜索填 https://qianfan.baidubce.com/v2/ai_search/web_search；"
            "或含 {q} 的模板 URL")
        search_base.setToolTip(
            "必须填「检索」端点。百度千帆的 /v2/ai_search/config 只是配置查询接口，"
            "填它不会有搜索结果（程序会自动纠正到 /web_search）")
        search_key = QLineEdit(self._cfg.web_search_key())
        search_key.setEchoMode(QLineEdit.Password)
        web_form.addRow("搜索引擎 API 地址", search_base)
        search_key_row = QHBoxLayout()
        search_key_row.setContentsMargins(0, 0, 0, 0)
        search_key_row.setSpacing(6)
        search_key_row.addWidget(search_key, 1)
        # 需求：填完地址 / KEY 能立刻自证可用（此前「设置好了却调不动」无从排查）
        search_test_btn = QPushButton("测试搜索")
        search_test_btn.setCursor(Qt.PointingHandCursor)
        search_test_btn.setToolTip("用上方地址与 KEY 真实检索一次，结果显示在下方")
        search_test_btn.setStyleSheet(ghost_btn_qss(font_size=12, padding="4px 12px"))
        search_key_row.addWidget(search_test_btn)
        web_form.addRow("搜索引擎 API Key", search_key_row)
        search_test_hint = QLabel("点「测试搜索」可立即验证地址与 KEY 是否可用")
        search_test_hint.setWordWrap(True)
        search_test_hint.setStyleSheet("color:#9aa0ac;font-size:11px;")
        web_form.addRow("", search_test_hint)

        def _on_search_test() -> None:
            """后台真实检索一次（子线程执行，禁止在子线程碰控件）。"""
            _base = search_base.text().strip()
            _key = search_key.text().strip()
            if not _base:
                search_test_hint.setText("请先填写搜索引擎 API 地址")
                return
            search_test_btn.setEnabled(False)
            search_test_hint.setText("正在测试…")
            from llm_client import LLMClientPool
            from utils.async_worker import spawn_worker

            def _ok(res: Any) -> None:
                search_test_btn.setEnabled(True)
                lines = str(res or "").strip().splitlines()
                search_test_hint.setText(
                    "测试成功（已返回结果）：" + (lines[0][:80] if lines else ""))

            def _bad(msg: str) -> None:
                search_test_btn.setEnabled(True)
                search_test_hint.setText(f"测试失败：{str(msg)[:160]}")

            spawn_worker(
                lambda: LLMClientPool.instance().web_search_custom(
                    _base, _key, "联网搜索连通性测试", top_n=3),
                on_done=_ok, on_fail=_bad)

        search_test_btn.clicked.connect(_on_search_test)

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
            if cur < 0 and saved_theme:
                # 配置里可能是**相对项目根**的路径（"./theme/x.png"），而下拉项
                # 存的是绝对路径 → 按解析后的绝对路径再匹配一次，保证选中项正确
                want = Path(saved_theme)
                if not want.is_absolute():
                    want = project_root() / want
                cur = theme_combo.findData(str(want))
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
        # 需求：常用接口类 API 管理器——只做存储（名称/简介/地址/KEY/自定义内容/其他规则）
        api_btn = QPushButton("常用接口类 API 管理器")
        api_btn.setObjectName("ghostBtn")
        api_btn.setCursor(Qt.PointingHandCursor)
        api_btn.clicked.connect(self._open_api_vault)
        skill_form.addRow(api_btn)

        # ---------- 5.6 skill库管理器（skillspub 公共技能库 / 管理器入口） ----------
        pub_form = _section("skill库管理器（skillspub 公共技能库目录管理）")
        # 需求：允许调用 skillpub 主开关——关闭后 AI 不主动调用、调用指令全部失效，
        # 用户也不能发起 skill 调用（#技能名 直接点名同样受控）。
        pub_enable_box = QCheckBox(
            "允许调用 skillpub（AI 自动调用 / \\@skillspub / \\@autoskills / #技能名）")
        pub_enable_box.setChecked(
            bool(self._cfg.get("ui", "skillspub_enabled", default=True)))
        pub_enable_box.setToolTip(
            "关闭后：AI 不会主动调用 skillpub 技能，\\@skillspub / \\@autoskills "
            "与 #技能名 等调用指令全部失效，用户也不能发起 skill 调用。")
        pub_form.addRow(pub_enable_box)
        pub_btn = QPushButton("打开 skill库管理器")
        pub_btn.setObjectName("ghostBtn")
        pub_btn.setCursor(Qt.PointingHandCursor)
        pub_btn.clicked.connect(self._open_skillpub_manager)
        pub_form.addRow(pub_btn)
        pub_hint = QLabel(
            "skill库目录：skillspub/catalog.json 仅为索引，每个公共技能一个文件夹"
            "skillspub/<技能文件夹>/（SKILL.md 为该技能详细介绍与执行说明）。\n"
            "发送 \\@skillspub（手动自由调用）、\\@autoskills（AI 自动挑选组合调用）"
            "或在输入框用 #技能名 直接点名调用（可一次多个组合）即可使用；"
            "取消上方「允许调用 skillpub」后上述调用将全部失效。")
        pub_hint.setObjectName("hintText")
        pub_hint.setWordWrap(True)
        pub_form.addRow(pub_hint)

        # ---------- 5.7 角色对话管理（需求：角色扮演设定卡多选加载） ----------
        rp_form = _section(
            "角色对话管理（角色扮演设定卡：分目录多选，点「确定保存」后才生效）")
        rp_checks: Dict[str, QCheckBox] = {}
        try:
            from roleplay.roleplaytool import RolePlayManager, CATEGORY_LABELS
            rp_mgr = RolePlayManager()
        except Exception as exc:  # noqa: BLE001
            rp_mgr = None
            logger.warning("角色扮演模块加载失败: %s", exc)
        if rp_mgr is None:
            rp_form.addRow(
                QLabel("角色扮演模块加载失败，请检查 roleplay/ 子项目是否完整。"))
        else:
            saved_keys = set(
                self._cfg.get("ui", "roleplay_cards", default=[]) or [])
            # 需求：主角设定选择框（单选，指定「你」在扮演里的身份）
            try:
                from roleplay.roleplay_preset import RolePlayPreset
                _preset = RolePlayPreset.instance()
            except Exception as exc:  # noqa: BLE001
                _preset = None
                logger.warning("角色扮演预设加载失败: %s", exc)

            persona_title = QLabel("主角设定（你在这段扮演里的身份，单选）")
            persona_title.setObjectName("sectionTitle")
            rp_form.addRow(persona_title)
            persona_box = QComboBox()
            persona_box.setToolTip(
                "选择一张「个人设定」卡作为**主角（你本人）**的设定；"
                "它会以【主角设定】单独注入，不会和角色设定混淆。")
            persona_box.addItem("（不使用主角设定）", "")
            try:
                for name in rp_mgr.list_cards("rolepersonal"):
                    persona_box.addItem(name, name)
            except Exception:  # noqa: BLE001
                pass
            _saved_persona = ""
            if _preset is not None:
                _saved_persona = str(
                    _preset.get("assembly", "user_persona", default="") or "")
            _pi = persona_box.findData(_saved_persona)
            persona_box.setCurrentIndex(_pi if _pi >= 0 else 0)
            rp_form.addRow(persona_box)

            # 需求：允许 OOC / 突破世界观（仍参考世界书背景设定）的工具开关
            ooc_cb = QCheckBox("允许 OOC / 突破世界观（仍参考世界书背景设定）")
            ooc_cb.setToolTip(
                "开启后允许出戏发言、元叙事、跨世界观联动等破限演绎；\n"
                "世界书 / 世界观卡仍作为**背景参考**注入（地名、势力、历史、"
                "术语保持一致）。\n可在「角色工具」里同时勾选"
                "「OOC与世界观破限」卡片获得更详细的破限规则。")
            _ooc_on = False
            if _preset is not None:
                _ooc_on = bool(_preset.get("assembly", "ooc_mode", default=False))
            ooc_cb.setChecked(_ooc_on)
            rp_form.addRow(ooc_cb)

            for cat in rp_mgr.categories():
                cat_title = CATEGORY_LABELS.get(cat, cat)
                cat_label = QLabel(f"{cat_title}（{cat}）"
                                   + ("　*单选" if cat == "style" else "　*多选"))
                cat_label.setObjectName("sectionTitle")
                cat_label.setToolTip(
                    "只有勾选并保存的卡片才会进入对话上下文，未勾选的一律不加载。"
                    if cat != "style" else
                    "对话风格为单选：勾选一张会自动取消其它风格卡，"
                    "同一时间只加载这一张风格设定。")
                rp_form.addRow(cat_label)
                names = rp_mgr.list_cards(cat)
                if not names:
                    rp_form.addRow(
                        QLabel("（暂无卡片，可点下方「打开角色扮演管理器」新建）"))
                    continue
                # 需求：卡片较多时单行排列会把分节拉得过长，且长卡片名的
                # QCheckBox 不换行会撑破容器宽度 → 改为两列网格 + 过长名省略
                # （QCheckBox 没有 setWordWrap，用省略 + 悬浮提示保证不撑宽）。
                def _elide(s: str, n: int = 18) -> str:
                    return s if len(s) <= n else s[:n - 1] + "…"

                grid = QGridLayout()
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(2)
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 1)
                for i, name in enumerate(names):
                    key = RolePlayManager.join_key(cat, name)
                    cb = QCheckBox(_elide(name))
                    cb.setChecked(key in saved_keys)
                    # 需求：对话风格是**单选**——勾选一张即自动取消同类其它勾选，
                    # 保证同一时刻只有一张风格卡被加载。
                    if cat == "style":
                        cb.toggled.connect(
                            lambda _on, _cb=cb, _cat=cat:
                            self._enforce_single_style(_cb, _cat, rp_checks))
                    try:
                        desc = str(rp_mgr.load_card(cat, name).get(
                            "description") or "")
                    except Exception:  # noqa: BLE001
                        desc = ""
                    cb.setToolTip(f"{name}\n{desc[:200]}" if desc else name)
                    rp_checks[key] = cb
                    grid.addWidget(cb, i // 2, i % 2)
                grid_wrap = QWidget()
                grid_wrap.setLayout(grid)
                rp_form.addRow(grid_wrap)

            def _save_roleplay_cards() -> None:
                """区块内「确定保存」：保存设定卡勾选 + 主角设定 + OOC 开关。"""
                sel = sorted(
                    k for k, cb in rp_checks.items() if cb.isChecked())
                # 需求：对话风格单选——保存前再兜底一次，只保留一张风格卡
                _style_keys = [k for k in sel if k.startswith("style/")]
                if len(_style_keys) > 1:
                    for k in _style_keys[1:]:
                        sel.remove(k)
                        cb = rp_checks.get(k)
                        if cb is not None:
                            cb.setChecked(False)
                self._cfg.set(sel, "ui", "roleplay_cards")
                # 主角设定（单选）与 OOC / 破限开关：写入角色扮演预设
                persona = str(persona_box.currentData() or "")
                ooc_on = bool(ooc_cb.isChecked())
                if _preset is not None:
                    # 需求：勾选结果同时写进预设的 cards，使预设成为**唯一权威来源**。
                    # 否则在管理器里清空勾选后，引擎会回退加载 config 里的旧勾选，
                    # 导致「没选中的卡片」又被注入上下文。
                    _cards: Dict[str, List[str]] = {
                        c: [] for c in (rp_mgr.categories()
                                        if hasattr(rp_mgr, "categories")
                                        else [])}
                    for k in sel:
                        _cat, _, _name = str(k).partition("/")
                        if _cat and _name:
                            _cards.setdefault(_cat, []).append(_name)
                    _preset.set(_cards, "cards")
                    _preset.set(persona, "assembly", "user_persona")
                    _preset.set(ooc_on, "assembly", "ooc_mode")
                    _preset.save()
                # 同步一份到 config.json，便于其它模块读取/回显
                self._cfg.set(persona, "ui", "roleplay_user_persona")
                self._cfg.set(ooc_on, "ui", "roleplay_ooc_mode")
                self._cfg.save()
                extra = []
                if persona:
                    extra.append(f"主角设定：{persona}")
                if ooc_on:
                    extra.append("允许 OOC / 突破世界观（参考世界书背景设定）")
                styled_info(
                    dialog, "角色对话管理",
                    f"已确定加载 {len(sel)} 张设定卡：\n"
                    + ("、".join(sel) if sel else "（未勾选任何卡片）")
                    + (("\n\n" + "\n".join(extra)) if extra else "")
                    + "\n\n主界面开启「开启角色扮演」后，"
                    "对话时才会加载以上卡片。")

            def _open_roleplay_tool() -> None:
                """打开 roleplay 子项目的设定卡管理窗口（同进程内嵌，主题同步）。"""
                try:
                    from roleplay.roleplaytool import RolePlayToolWindow
                except Exception as exc:  # noqa: BLE001
                    styled_warning(dialog, "角色扮演管理器",
                                   f"角色扮演管理器加载失败：{exc}")
                    return
                w = getattr(self, "_roleplay_tool_window", None)
                if w is None:
                    try:
                        w = RolePlayToolWindow()
                        w.apply_accent(self._accent)
                        self._roleplay_tool_window = w
                    except Exception as exc:  # noqa: BLE001
                        styled_warning(dialog, "角色扮演管理器",
                                       f"角色扮演管理器启动失败：{exc}")
                        return
                w.show()
                w.raise_()
                w.activateWindow()

            rp_btn_row = QHBoxLayout()
            rp_btn_row.setSpacing(8)
            rp_save_btn = QPushButton("确定保存")
            rp_save_btn.setStyleSheet(btn_qss(padding="6px 24px"))
            rp_save_btn.setCursor(Qt.PointingHandCursor)
            rp_save_btn.clicked.connect(_save_roleplay_cards)
            rp_btn_row.addWidget(rp_save_btn)
            rp_open_btn = QPushButton("打开角色扮演管理器")
            rp_open_btn.setObjectName("ghostBtn")
            rp_open_btn.setCursor(Qt.PointingHandCursor)
            rp_open_btn.clicked.connect(_open_roleplay_tool)
            rp_btn_row.addWidget(rp_open_btn)

            # 需求：角色扮演记忆遗忘选项 —— 独立清除角色扮演期间的记忆，
            # 日常对话记忆不受影响（记忆按 mode 命名空间分开存储）。
            def _forget_roleplay_memory() -> None:
                if not styled_confirm(
                        dialog, "遗忘角色扮演记忆",
                        "确定要遗忘全部角色扮演记忆吗？\n\n"
                        "将清除开启角色扮演期间产生的短期对话存档与"
                        "长期/重要记忆。\n日常对话记忆不受影响。",
                        danger=True, ok_text="遗忘"):
                    return
                try:
                    n = self._memory.forget_roleplay()
                except Exception as exc:  # noqa: BLE001
                    styled_warning(dialog, "遗忘角色扮演记忆",
                                   f"遗忘失败：{exc}")
                    return
                styled_info(dialog, "遗忘角色扮演记忆",
                            f"已遗忘 {n} 条角色扮演记忆。\n"
                            "日常对话记忆不受影响。")

            rp_forget_btn = QPushButton("遗忘角色扮演记忆")
            rp_forget_btn.setCursor(Qt.PointingHandCursor)
            # 危险操作样式（内联，避免依赖未定义的 QSS 类）
            rp_forget_btn.setStyleSheet(
                "QPushButton{color:#e11d48;border:1px solid rgba(225,29,72,0.4);"
                "border-radius:8px;padding:6px 16px;font-size:13px;"
                "background:rgba(225,29,72,0.06);}"
                "QPushButton:hover{background:rgba(225,29,72,0.12);}"
                "QPushButton:pressed{background:rgba(225,29,72,0.18);}")
            rp_forget_btn.clicked.connect(_forget_roleplay_memory)
            rp_btn_row.addWidget(rp_forget_btn)
            rp_btn_row.addStretch(1)
            rp_form.addRow(rp_btn_row)

            # 需求：角色扮演预设管理器（酒馆式参数管理：采样参数 / 规则卡 /
            # 正则 / 世界书 / 变量 / 装配与隔离）。
            def _open_roleplay_preset() -> None:
                try:
                    from roleplay.roleplay_preset_ui import RolePlayPresetDialog
                except Exception as exc:  # noqa: BLE001
                    styled_warning(dialog, "角色扮演预设",
                                   f"预设管理器加载失败：{exc}")
                    return
                dlg = RolePlayPresetDialog(dialog)
                try:
                    dlg.apply_accent(self._accent)
                except Exception:  # noqa: BLE001
                    pass
                if dlg.exec():
                    # 隔离策略可能变化，刷新历史列表以反映当前会话空间
                    try:
                        self._refresh_history_list()
                    except Exception:  # noqa: BLE001
                        pass

            # 需求：按钮文案过长会把分节卡片撑破（宽度溢出容器），改为短文案
            # + 悬浮提示说明具体内容。
            preset_btn = QPushButton("角色扮演预设管理器…")
            preset_btn.setObjectName("ghostBtn")
            preset_btn.setCursor(Qt.PointingHandCursor)
            preset_btn.setToolTip(
                "打开全屏的酒馆式预设管理器：采样参数 / 设定卡 / 规则卡 / "
                "正则 / 世界书 / 装配与隔离（F11 切换全屏）")
            preset_btn.clicked.connect(_open_roleplay_preset)
            preset_row = QHBoxLayout()
            preset_row.setSpacing(8)
            preset_row.addWidget(preset_btn)
            preset_hint = QLabel("采样参数 · 设定卡 · 规则卡 · 正则 · 世界书")
            preset_hint.setObjectName("statusText")
            preset_hint.setWordWrap(True)
            preset_row.addWidget(preset_hint, 1)
            rp_form.addRow(preset_row)

            # 需求：设置面板 roleplay 部分新增对话隔离开关
            try:
                from roleplay.roleplay_preset import RolePlayPreset
                _preset = RolePlayPreset.instance()
            except Exception:  # noqa: BLE001
                _preset = None
            iso_normal_cb = QCheckBox("角色扮演对话与日常对话互不读取（隔离其他对话）")
            iso_normal_cb.setChecked(
                True if _preset is None else bool(_preset.get(
                    "isolation", "isolate_normal", default=True)))
            per_session_cb = QCheckBox(
                "每个对话独立（开启角色扮演后，每个新会话各自一份记忆）")
            per_session_cb.setChecked(
                False if _preset is None else bool(_preset.get(
                    "isolation", "per_session", default=False)))

            def _save_isolation() -> None:
                if _preset is None:
                    return
                _preset.set(iso_normal_cb.isChecked(), "isolation", "isolate_normal")
                _preset.set(per_session_cb.isChecked(), "isolation", "per_session")
                _preset.save()

            iso_normal_cb.toggled.connect(lambda _v: _save_isolation())
            per_session_cb.toggled.connect(lambda _v: _save_isolation())
            rp_form.addRow(iso_normal_cb)
            rp_form.addRow(per_session_cb)
            iso_hint = QLabel(
                "说明：①「互不读取」开启后，角色扮演对话不会读取日常对话的记忆，"
                "反之亦然；开启角色扮演时也不会看到日常会话记录。\n"
                "②「每个对话独立」开启后，每个新会话都有独立的短期与长期记忆；"
                "关闭时所有角色扮演会话共享一份记忆（但仍与日常对话隔离）。")
            iso_hint.setWordWrap(True)
            iso_hint.setObjectName("hintText")
            rp_form.addRow(iso_hint)

        # ---------- 5.8 角色卡打包 / 技能包（V2-C1/C2） ----------
        v2_form = _section("角色卡与技能包（V2：导出 / 导入 / 打包）")
        cc_row = QHBoxLayout()
        cc_row.setSpacing(8)
        export_cc_btn = QPushButton("导出当前角色卡")
        export_cc_btn.setObjectName("ghostBtn")
        export_cc_btn.setCursor(Qt.PointingHandCursor)
        export_cc_btn.clicked.connect(self._export_current_role_card)
        import_cc_btn = QPushButton("导入角色卡…")
        import_cc_btn.setObjectName("ghostBtn")
        import_cc_btn.setCursor(Qt.PointingHandCursor)
        import_cc_btn.clicked.connect(self._import_role_card)
        cc_row.addWidget(export_cc_btn)
        cc_row.addWidget(import_cc_btn)
        v2_form.addRow(cc_row)
        bundle_btn = QPushButton("技能包管理…（成组打包 / 导入 / 批量启停）")
        bundle_btn.setObjectName("ghostBtn")
        bundle_btn.setCursor(Qt.PointingHandCursor)
        bundle_btn.clicked.connect(self._open_bundle_manager)
        v2_form.addRow(bundle_btn)
        # V2-C4：安装技能包 zip（带安全审查，高风险需显式确认）
        install_btn = QPushButton("安装技能包 zip…（带安全审查）")
        install_btn.setObjectName("ghostBtn")
        install_btn.setCursor(Qt.PointingHandCursor)
        install_btn.clicked.connect(self._install_skill_zip)
        v2_form.addRow(install_btn)
        # V2-C5：技能评测（跑测试用例 → 通过率报告）
        eval_btn = QPushButton("评测技能…（跑测试用例）")
        eval_btn.setObjectName("ghostBtn")
        eval_btn.setCursor(Qt.PointingHandCursor)
        eval_btn.clicked.connect(self._open_skill_eval)
        v2_form.addRow(eval_btn)

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
        # V2-B2：心跳巡检开关（主动关怀；开启后 data/heartbeat_state.json 记录每日次数）
        hb_enabled = QCheckBox("工作区变化主动关怀")
        hb_enabled.setChecked(self._cfg.heartbeat_enabled())
        hb_interval = QSpinBox()
        hb_interval.setRange(5, 480)
        hb_interval.setValue(self._cfg.heartbeat_interval_min())
        pet_form.addRow("主动关怀开关", hb_enabled)
        pet_form.addRow("关怀间隔(分钟)", hb_interval)


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

        # ---------- 固定记忆（V2-A2）：必须记住、不容检索失败的信息 ----------
        try:
            from pinned_memory import PinnedMemory
            pm = PinnedMemory.instance()
            pinned_edit = QTextEdit()
            pinned_edit.setPlainText("\n".join(pm.read_pinned()))
            pinned_edit.setFixedHeight(110)
            pinned_edit.setPlaceholderText("每行一条，例如：\n主人的生日是 5 月 20 日")
            adv_form.addRow("固定记忆（每行一条，自动脱敏）", pinned_edit)

            def _save_pinned() -> None:
                raw = pinned_edit.toPlainText().splitlines()
                items = [ln.lstrip("-").strip() for ln in raw
                         if ln.strip() and not ln.strip().startswith("#")]
                n = pm.set_pinned(items)
                styled_info(dialog, "固定记忆", f"已保存 {n} 条固定记忆。")

            pinned_save_btn = QPushButton("保存固定记忆")
            pinned_save_btn.setStyleSheet(btn_qss(padding="6px 24px"))
            pinned_save_btn.clicked.connect(_save_pinned)
            adv_form.addRow("", pinned_save_btn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("固定记忆分节加载失败（跳过）: %s", exc)

        # ---------- Memory Dream（V2-A3）：手动整合长期记忆（可回滚）----------
        try:
            from memory_dream import MemoryDream
            dream_btn = QPushButton("执行记忆整合（Dream）")
            dream_btn.setStyleSheet(btn_qss(padding="6px 24px"))

            def _run_dream() -> None:
                if not styled_question(
                        dialog, "记忆整合",
                        "将调用模型整合长期记忆中的重复 / 相关碎片为综合记忆，"
                        "执行前会自动备份快照、可回滚。确定执行？"):
                    return
                from utils.async_worker import AsyncWorker

                def _do() -> dict:
                    return MemoryDream.instance().run_dream()

                def _done(result: dict) -> None:
                    if result.get("skipped"):
                        styled_info(dialog, "记忆整合",
                                    f"本次无内容可整合（{result.get('reason')}）。")
                    else:
                        styled_info(dialog, "记忆整合",
                                    f"已合并 {result.get('groups')} 组，"
                                    f"新增 {result.get('added')} 条，"
                                    f"删除 {result.get('removed')} 条。")

                # spawn_worker 保活引用（防止 QThread 被 GC 回收导致闪退）
                from utils.async_worker import spawn_worker
                spawn_worker(
                    _do, on_done=_done,
                    on_fail=lambda msg: styled_warning(
                        dialog, "记忆整合", f"失败：{msg}"))
                styled_info(dialog, "记忆整合", "记忆整合已在后台执行，完成后会提示结果。")

            dream_btn.clicked.connect(_run_dream)
            adv_form.addRow("记忆整合", dream_btn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory Dream 分节加载失败（跳过）: %s", exc)

        # ---------- 隐私与清理（调试后 / 交付前把软件还原成「干净可部署」状态）----------
        priv_form = _section(
            "隐私与清理（一键清除 API KEY / 对话 / 记忆 / 日志 / 上传文件 / 使用记录）")
        priv_hint = QLabel(
            "用途：调试完后把软件还原成干净的、可直接交付他人的状态。\n"
            "会清除：各接口 API KEY 与「常用接口类 API 管理器」条目、全部对话与记忆、"
            "日志、上传 / 生成的文件与图片、日记、技能产出、登陆时间与专注 / 饮食等使用记录。\n"
            "不会动：角色卡、角色扮演设定卡、技能库、主题背景等程序自带内容。\n"
            "此操作不可恢复，建议先备份 data/ 与 history/。")
        priv_hint.setObjectName("hintText")
        priv_hint.setWordWrap(True)
        priv_form.addRow(priv_hint)

        # 需求：调试完要「清干净再交给别人」——清完可以直接退出，避免刚清理完的
        # 会话/日志又被正在运行的程序写回去（下次启动就是干净状态）
        quit_after_box = QCheckBox("清除完成后退出程序（便于打包 / 交付他人）")
        quit_after_box.setToolTip(
            "勾选后：清理完成并提示结果后自动退出程序。\n"
            "建议勾选——程序运行时还会持续写日志与会话，退出后再启动才是干净状态。")
        priv_form.addRow(quit_after_box)

        priv_btn = QPushButton("一键清除隐私数据…")
        priv_btn.setCursor(Qt.PointingHandCursor)
        priv_btn.setStyleSheet(btn_qss(padding="6px 24px"))
        priv_btn.setToolTip("先弹出待清除清单确认，再执行；被占用的目录会在下次启动时自动补删。")

        def _privacy_clean_now() -> None:
            """一键隐私清理：确认后后台执行，完成后刷新界面并提示结果。"""
            try:
                import privacy_clean
            except Exception as exc:  # noqa: BLE001
                styled_warning(dialog, "隐私清理", f"清理模块加载失败：{exc}")
                return
            if not styled_confirm(dialog, "隐私清理（不可恢复）",
                                  privacy_clean.describe_plan(),
                                  ok_text="确认清除"):
                return
            from utils.async_worker import spawn_worker

            def _done(report: dict) -> None:
                try:
                    self._clear_chat_area()
                    self._refresh_history_list()
                    self._session_path = None
                except Exception as exc:  # noqa: BLE001
                    logger.warning("隐私清理后刷新界面失败: %s", exc)
                msg = (f"已清除 {len(report.get('removed', []))} 项，"
                       f"释放约 {privacy_clean.fmt_size(report.get('freed', 0))}。")
                if report.get("config_keys"):
                    msg += "\n已清空配置字段（含各接口 KEY）：" + \
                           "、".join(report["config_keys"])
                if report.get("pending"):
                    msg += ("\n\n以下内容正被程序占用，将在下次启动时自动清除：\n  · "
                            + "\n  · ".join(report["pending"]))
                if report.get("errors"):
                    msg += "\n\n注意：\n" + "\n".join(report["errors"][:6])
                styled_info(dialog, "隐私清理", msg)
                if quit_after_box.isChecked():
                    # 退出前留一点时间让提示框显示出来
                    QTimer.singleShot(1200, lambda: QApplication.instance().quit())

            spawn_worker(privacy_clean.clean, on_done=_done,
                         on_fail=lambda m: styled_warning(dialog, "隐私清理",
                                                           f"清理失败：{m}"))
            styled_info(dialog, "隐私清理", "正在后台清除隐私数据，完成后会提示结果。")

        priv_btn.clicked.connect(_privacy_clean_now)
        priv_form.addRow("清除本机隐私数据", priv_btn)

        # ---------- 保存 ----------
        def _apply() -> None:
            self._cfg.set(provider.currentText(), "api", "provider")
            self._cfg.set(api_base.text().strip(), "api", "api_base")
            self._cfg.set(api_key.text().strip(), "api", "api_key")
            self._cfg.set(main_model.text().strip() or "gpt-4o", "api", "main_model")
            self._cfg.set(small_model.text().strip() or "gpt-4o-mini", "api", "small_model")
            self._cfg.set(small_base_box.text().strip(), "api", "small_base")
            self._cfg.set(small_key_box.text().strip(), "api", "small_key")
            self._cfg.set(small_thinking_off.isChecked(), "api", "small_thinking_off")
            self._cfg.set(vision_model.text().strip() or "gpt-4o", "api", "vision_model")
            self._cfg.set(vision_base.text().strip(), "api", "vision_base")
            self._cfg.set(vision_key.text().strip(), "api", "vision_key")
            # 需求：主对话模型是否支持图片输入（图片直接发主模型而不是只给摘要）
            self._cfg.set(main_vision_box.isChecked(), "api", "main_vision")
            # 需求：单文件注入上限（PDF/Word/Excel/PPT 原文，默认 32000 字）
            self._cfg.set(int(attach_chars.value()), "api", "attach_max_chars")
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
            # 需求：允许调用 skillpub 主开关（关闭则 AI 不主动调用、调用指令全部失效）
            self._cfg.set(pub_enable_box.isChecked(), "ui", "skillspub_enabled")
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
            # V2-B2：心跳巡检配置
            self._cfg.set(hb_enabled.isChecked(), "heartbeat", "enabled")
            self._cfg.set(hb_interval.value(), "heartbeat", "interval_min")
            self._cfg.set(blur_check.isChecked(), "ui", "blur_background")
            self._cfg.set(lt_enabled.isChecked(), "ui", "login_time_enabled")
            self._cfg.set(transparent_check.isChecked(), "ui", "transparent_chat")
            self._cfg.set(icon_box.text().strip(), "ui", "icon_path")
            fam = font_combo.currentText()
            self._cfg.set("" if fam == "（系统默认）" else fam, "ui", "font_family")
            # 需求：主界面聊天字体大小
            self._cfg.set(chat_font_box.value(), "ui", "chat_font_size")
            # 需求：角色扮演——设置面板勾选的设定卡（与区块内「确定保存」同步持久化）
            if rp_checks:
                self._cfg.set(
                    sorted(k for k, cb in rp_checks.items() if cb.isChecked()),
                    "ui", "roleplay_cards")
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

    # ------------------------------------------------------------ V2-C4: 安装技能包（安全审查）
    def _install_skill_zip(self) -> None:
        """选择技能包 zip → 安全审查 → 确认（高风险需显式确认）→ 安装。"""
        try:
            from skill_install import SkillInstaller
            from PySide6.QtWidgets import QFileDialog
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "安装技能包", f"模块加载失败：{exc}")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择技能包 zip", "", "技能包 (*.zip);;所有文件 (*)")
        if not path:
            return
        try:
            pv = SkillInstaller.instance().preview_and_review(path)
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "安装技能包", f"审查失败：{exc}")
            return
        if not pv.get("valid"):
            styled_warning(self, "安装技能包", "不是有效的技能包（缺少 bundle.json）。")
            return
        skills_desc = "\n".join(
            f"- {s['name']}（风险：{s['risk']}）" for s in pv.get("skills") or [])
        risk = pv.get("worst_risk") or "low"
        risk_desc = {"low": "低风险，可直接安装",
                     "medium": "存在可疑内容，建议谨慎",
                     "high": "检测到越权 / 注入 / 密钥诱导类内容"}.get(risk, risk)
        msg = (f"即将安装 {len(pv.get('skills') or [])} 个技能：\n{skills_desc}\n\n"
               f"综合风险：{risk}（{risk_desc}）")
        if risk == "high":
            ok = styled_confirm(self, "安装技能包（高风险）",
                                msg + "\n\n高风险技能可能尝试越权或诱导泄漏信息，"
                                      "确认仍要安装？", danger=True, ok_text="仍要安装")
        else:
            ok = styled_confirm(self, "安装技能包", msg + "\n\n确认安装？",
                                ok_text="安装")
        if not ok:
            return
        try:
            r = SkillInstaller.instance().install_zip(path)
            if r.get("added"):
                self._skill_mgr.reload(force=True)
                styled_info(self, "安装技能包",
                            f"已安装 {r['added']} 个技能"
                            + (f"（跳过 {r['skipped']} 个同名）"
                               if r.get("skipped") else "")
                            + "。技能保存后立即生效。")
            else:
                styled_info(self, "安装技能包",
                            "没有可安装的新技能（可能全部同名或包无效）。")
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "安装技能包", f"安装失败：{exc}")

    # ------------------------------------------------------------ V2-C5: 技能评测
    def _open_skill_eval(self) -> None:
        """技能评测对话框：选技能 → 跑测试用例 → 显示通过率报告。"""
        try:
            from skill_eval import SkillEval
            from utils.async_worker import spawn_worker
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "技能评测", f"模块加载失败：{exc}")
            return
        se = SkillEval.instance()

        dialog = QDialog(self)
        dialog.setWindowTitle("技能评测")
        dialog.setModal(True)
        dialog.setMinimumSize(520, 420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        hint = QLabel("选择技能 → 运行其测试用例（skills/test_cases/<技能名>.json），"
                      "输出触发解析与回复关键词的通过率报告。")
        hint.setStyleSheet(f"color:{TEXT_MID}; font-size:12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        combo = QComboBox()
        combo.setObjectName("settingsCombo")
        all_skills = [str(s.get("name") or "") for s in self._skill_mgr.skills()]
        with_cases = se.list_skills_with_cases()
        # 优先展示有用例的技能，其次全部技能
        ordered = [n for n in with_cases if n in all_skills] + \
                  [n for n in all_skills if n not in with_cases]
        combo.addItems(ordered or ["（无技能）"])
        row.addWidget(combo, 1)
        run_btn = QPushButton("运行评测")
        run_btn.setStyleSheet(btn_qss(padding="6px 20px"))
        row.addWidget(run_btn)
        layout.addLayout(row)

        report = QTextEdit()
        report.setReadOnly(True)
        report.setObjectName("evalReport")
        layout.addWidget(report, 1)

        def _run() -> None:
            name = combo.currentText()
            if not name or name.startswith("（"):
                return
            run_btn.setEnabled(False)
            report.setPlainText(f"正在评测技能「{name}」…")

            def _do() -> dict:
                return se.run_skill_eval(name)

            def _done(r: dict) -> None:
                run_btn.setEnabled(True)
                if r.get("no_cases"):
                    report.setPlainText(
                        f"技能「{r['skill']}」没有测试用例。\n\n"
                        f"请在 skills/test_cases/{r['skill']}.json 添加用例：\n"
                        '[\n  {"input": "示例输入", "expect_trigger": true,'
                        ' "expect_keyword": "期望关键词"}\n]')
                    return
                lines = [
                    f"技能：{r['skill']}",
                    f"用例数：{r['total']}",
                    f"通过：{r['total'] - len(r['failures'])}",
                    f"通过率：{int(r['rate'] * 100)}%",
                    f"（跳过回复断言 {r.get('skipped_reply', 0)} 条）",
                ]
                if r.get("failures"):
                    lines.append("\n失败明细：")
                    for f in r["failures"]:
                        lines.append(f"- 输入：{f['input']}\n  原因：{f['reason']}")
                else:
                    lines.append("\n全部用例通过。")
                report.setPlainText("\n".join(lines))

            spawn_worker(_do, on_done=_done,
                         on_fail=lambda msg: (
                             run_btn.setEnabled(True),
                             report.setPlainText(f"评测失败：{msg}")))

        run_btn.clicked.connect(_run)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn, 0, Qt.AlignRight)
        dialog.exec()

    # ------------------------------------------------------------ V2-C2: 角色卡导出/导入
    def _export_current_role_card(self) -> None:
        """导出当前角色的角色卡 zip（先选择保存路径，再打包角色卡+立绘+动图）。"""
        try:
            from character_card import CharacterCard
            from PySide6.QtWidgets import QFileDialog
            role = self._current_role or ""
            if not role:
                styled_warning(self, "导出角色卡", "当前无角色可导出。")
                return
            default_path = str(self._cfg.character_card_export_dir()
                               / f"{role}-charactercard.zip")
            save_path, _ = QFileDialog.getSaveFileName(
                self, "导出角色卡为", default_path,
                "角色卡包 (*.zip);;所有文件 (*)")
            if not save_path:
                return  # 用户取消
            if not styled_confirm(self, "导出角色卡",
                                  f"将把角色「{role}」的角色卡、立绘与桌宠动图"
                                  f"打包为 zip：\n{save_path}\n\n确定导出？",
                                  ok_text="导出"):
                return
            path = CharacterCard.instance().export_role_zip(
                role, exact_path=save_path)
            if path and path.exists():
                styled_info(self, "导出角色卡",
                            f"已导出：\n{path}\n\n可分享给其它桌宠用户导入。")
            else:
                styled_warning(self, "导出角色卡",
                               "导出失败：缺少角色卡文件或写入失败。")
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "导出角色卡", f"导出异常：{exc}")

    def _import_role_card(self) -> None:
        """导入角色卡 zip（预览 → 确认 → 解包落地）。"""
        try:
            from character_card import CharacterCard
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, "选择角色卡 zip", "",
                "角色卡 (*.zip);;所有文件 (*)")
            if not path:
                return
            cc = CharacterCard.instance()
            pv = cc.preview_zip(path)
            if not pv.get("valid"):
                styled_warning(self, "导入角色卡", "不是有效的角色卡包（缺少 manifest）。")
                return
            role = pv.get("role") or ""
            desc = (f"角色：{pv.get('display_name')}（{role}）\n"
                    f"立绘 {pv.get('portrait_count')} 张 · "
                    f"桌宠动图 {pv.get('desktop_count')} 个")
            if not styled_confirm(self, "导入角色卡",
                                  f"即将导入角色卡：\n{desc}\n确定导入？",
                                  ok_text="导入"):
                return
            result = cc.import_zip(path)
            if result.get("conflicts"):
                styled_warning(self, "导入角色卡",
                               "部分内容未导入：\n" + "\n".join(result["conflicts"]))
            elif result.get("imported"):
                try:
                    self._roles._card_cache.clear()
                except Exception:  # noqa: BLE001
                    pass
                self._refresh_select_combo()
                styled_info(self, "导入角色卡",
                            f"角色「{role}」导入成功。\n重启后可在左栏选择该角色。")
            else:
                styled_warning(self, "导入角色卡", "导入失败：无法解包角色卡。")
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "导入角色卡", f"导入异常：{exc}")

    # ------------------------------------------------------------ V2-C1: 技能包管理
    def _multi_select_skills(self, parent: QWidget,
                             skill_names: List[str]) -> Optional[List[str]]:
        """技能多选对话框（勾选技能 → 返回选中列表；取消返回 None）。"""
        dlg = QDialog(parent)
        dlg.setWindowTitle("选择技能")
        dlg.setModal(True)
        dlg.setMinimumSize(320, 360)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        list_w = QListWidget()
        for n in skill_names:
            item = QListWidgetItem(n)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            list_w.addItem(item)
        lay.addWidget(list_w, 1)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.setStyleSheet(btn_qss(padding="6px 20px"))
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        result = []

        def _ok() -> None:
            result[:] = [list_w.item(i).text()
                         for i in range(list_w.count())
                         if list_w.item(i).checkState() == Qt.Checked]
            dlg.accept()

        ok_btn.clicked.connect(_ok)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        if dlg.exec() != QDialog.Accepted:
            return None
        return result

    def _open_bundle_manager(self) -> None:
        """技能包管理对话框：列出技能包，支持新建 / 删除 / 打包 / 导入 / 批量启停。"""
        try:
            from skill_bundles import SkillBundles
            from PySide6.QtWidgets import QFileDialog
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "技能包", f"模块加载失败：{exc}")
            return
        sb = SkillBundles.instance()

        dialog = QDialog(self)
        dialog.setWindowTitle("技能包管理")
        dialog.setModal(True)
        dialog.setMinimumSize(520, 420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        hint = QLabel(
            "技能包 = 一组技能的容器，用于成组管理 / 分享。\n"
            "技能是什么：输入框输入 \\@触发词 唤起的功能，例如 \\@translate 翻译、"
            "\\@research 科研、\\@thinking 思维链开关。\n"
            "技能字段（由技能库定义）：name 技能名 / trigger 触发词 / kind 类型"
            "（skill=普通技能、switch=可同时开的开关）/ aliases 别名 / category 分类"
            " / enabled 启用状态；详情含 description 说明 / prompt_template 执行要求"
            " / parameters 参数。\n"
            "技能包能做什么：成组启用 / 停用（按钮「启用全部」「停用全部」）、"
            "打包 zip 分享给其它用户（「打包 zip」）、导入别人给的 zip（「导入 zip」）。")
        hint.setStyleSheet(f"color:{TEXT_MID}; font-size:12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        list_widget = QListWidget()
        list_widget.setObjectName("bundleList")
        layout.addWidget(list_widget, 1)

        def _reload() -> None:
            list_widget.clear()
            for b in sb.list_bundles():
                item = QListWidgetItem(
                    f"{b.get('name') or '未命名'} · {len(b.get('skill_names') or [])} 个技能 · {b.get('id')}")
                item.setData(Qt.UserRole, b.get("id"))
                list_widget.addItem(item)

        def _selected_id() -> Optional[str]:
            items = list_widget.selectedItems()
            return items[0].data(Qt.UserRole) if items else None

        def _create() -> None:
            name, ok = QInputDialog.getText(
                dialog, "新建技能包",
                "技能包名称（例如：写作工具包）：\n（技能在下一步勾选）")
            if not ok or not name.strip():
                return
            # 多选技能
            skills = sb.all_skill_names()
            if not skills:
                styled_info(dialog, "新建技能包", "技能库为空，无法创建技能包。")
                return
            picks = self._multi_select_skills(dialog, skills)
            if picks is None:
                return
            b = sb.create_bundle(name.strip(), picks)
            if b:
                styled_info(dialog, "新建技能包",
                            f"已创建技能包「{b['name']}」"
                            f"（{len(b['skill_names'])} 个技能）。\n"
                            "选中该包后可用「启用全部 / 停用全部」成组开关，"
                            "或用「打包 zip」分享给其它用户。")
                _reload()

        def _delete() -> None:
            bid = _selected_id()
            if not bid:
                return
            if styled_confirm(dialog, "删除技能包",
                              "确定删除该技能包？（技能本身不会被删除）",
                              danger=True, ok_text="删除"):
                sb.delete_bundle(bid)
                _reload()

        def _export() -> None:
            bid = _selected_id()
            if not bid:
                return
            bundle = sb.get_bundle(bid)
            if not bundle:
                return
            from PySide6.QtWidgets import QFileDialog
            base = "".join(c for c in str(bundle.get("name") or "skillbundle")
                           if c.isalnum() or c in "-_") or "skillbundle"
            default_path = str(self._cfg.data_dir / f"{base}-skillbundle.zip")
            save_path, _ = QFileDialog.getSaveFileName(
                dialog, "导出技能包为", default_path,
                "技能包 (*.zip);;所有文件 (*)")
            if not save_path:
                return  # 用户取消
            path = sb.export_bundle_zip(bid, exact_path=save_path)
            if path and path.exists():
                styled_info(dialog, "导出技能包", f"已导出：\n{path}")
            else:
                styled_warning(dialog, "导出技能包", "导出失败。")

        def _import() -> None:
            path, _ = QFileDialog.getOpenFileName(
                dialog, "选择技能包 zip", "", "技能包 (*.zip);;所有文件 (*)")
            if not path:
                return
            r = sb.import_bundle_zip(path)
            if r.get("added"):
                styled_info(dialog, "导入技能包",
                            f"已导入 {r['added']} 个技能"
                            + (f"（跳过 {r['skipped']} 个同名）" if r.get("skipped") else "")
                            + "。\n技能保存后立即生效（已开启热加载）。")
                self._skill_mgr.reload(force=True)
            else:
                styled_info(dialog, "导入技能包",
                            "没有可导入的新技能（可能全部同名或包无效）。")

        def _toggle(on: bool) -> None:
            bid = _selected_id()
            if not bid:
                return
            n = sb.set_bundle_enabled(bid, on)
            styled_info(dialog, "技能包",
                        f"已{'启用' if on else '停用'} {n} 个技能。")
            self._skill_mgr.reload(force=True)

        row = QHBoxLayout()
        row.setSpacing(8)
        create_btn = QPushButton("新建")
        create_btn.setStyleSheet(btn_qss(padding="6px 16px"))
        create_btn.clicked.connect(_create)
        del_btn = QPushButton("删除")
        del_btn.setStyleSheet(btn_qss(padding="6px 16px"))
        del_btn.clicked.connect(_delete)
        on_btn = QPushButton("启用全部")
        on_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        on_btn.clicked.connect(lambda: _toggle(True))
        off_btn = QPushButton("停用全部")
        off_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        off_btn.clicked.connect(lambda: _toggle(False))
        export_btn = QPushButton("打包 zip")
        export_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        export_btn.clicked.connect(_export)
        import_btn = QPushButton("导入 zip")
        import_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        import_btn.clicked.connect(_import)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(ghost_btn_qss(radius=8, font_size=12))
        close_btn.clicked.connect(dialog.close)
        for b in (create_btn, del_btn, on_btn, off_btn, export_btn, import_btn, close_btn):
            row.addWidget(b)
        layout.addLayout(row)

        _reload()
        dialog.exec()

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

    def _open_api_vault(self) -> None:
        """从设置面板打开「常用接口类 API 管理器」（纯存储：名称/简介/地址/KEY/自定义/规则）。

        需求：        每个接口调用方法不一致，因此不强制任何字段必填，仅做登记与取用，
        供 \\@weather 等技能工具按名称读取 KEY 与地址。
        """
        try:
            from api_vault import ApiVaultWindow, ensure_defaults
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "API 管理器", f"常用接口类 API 管理器加载失败：{exc}")
            return
        if self._api_vault_window is None:
            try:
                ensure_defaults()          # 首次使用写入「和风天气」占位配置
                self._api_vault_window = ApiVaultWindow()
                self._api_vault_window.apply_accent(self._accent)
            except Exception as exc:  # noqa: BLE001
                styled_warning(self, "API 管理器", f"常用接口类 API 管理器启动失败：{exc}")
                self._api_vault_window = None
                return
        self._api_vault_window.show()
        self._api_vault_window.raise_()
        self._api_vault_window.activateWindow()

    def _open_skillpub_manager(self) -> None:
        """从设置面板打开 skillspub 管理器（与技能工具管理器同风格、同生命周期）。"""
        try:
            from skillspub_manager import SkillPubManagerWindow
        except Exception as exc:  # noqa: BLE001
            styled_warning(self, "skillspub", f"skillspub 管理器加载失败：{exc}")
            return
        if self._skillpub_window is None:
            try:
                self._skillpub_window = SkillPubManagerWindow()
                self._skillpub_window.apply_accent(self._accent)
            except Exception as exc:  # noqa: BLE001
                styled_warning(self, "skillspub", f"skillspub 管理器启动失败：{exc}")
                self._skillpub_window = None
                return
        self._skillpub_window.show()
        self._skillpub_window.raise_()
        self._skillpub_window.activateWindow()

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
        # 需求（用户）：关闭/重启后输入框里不应残留上次没发出去的草稿
        # —— 当前实现没有 _save_input_draft，Qt 销毁窗口也不会自动保存；
        # 但万一以后接入了「输入即时保存」机制，这里强制清空 + 顺手删掉残留
        # 草稿文件，保证下次启动输入框为空。
        try:
            if hasattr(self, "_input") and self._input is not None:
                self._input.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            from config_loader import project_root
            stale = project_root() / "data" / "input_drafts.json"
            if stale.exists():
                stale.unlink()
        except Exception:  # noqa: BLE001
            pass
        event.accept()

    def hideEvent(self, event: Any) -> None:  # noqa: D102
        super().hideEvent(event)
        # V2：窗口隐藏/最小化时更新活跃标志（通知聚焦策略 ui:window_active 使用）
        self._window_active_hint = False


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
QLabel#bubbleUser[findMatch="true"], QLabel#bubbleAI[findMatch="true"] {{
    border: 2px solid {accent};
}}
QFrame#findBar {{
    background: {card_bg_alt}; border: 1px solid {BORDER};
    border-radius: 10px;
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


