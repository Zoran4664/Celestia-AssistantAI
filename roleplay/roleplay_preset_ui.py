"""
roleplay_preset_ui.py — 角色扮演预设管理器（酒馆式参数管理界面）

六页标签：
    1. 采样参数  —— temperature / top_p / top_k / min_p / top_a / 惩罚 / tokens / seed / 推理强度
                   每项独立开关，未开启的沿用全局 config.json
    2. 设定卡    —— rolemanger/style、rolemanger/world、rolepersonal、roletools 多选
    3. 规则卡    —— 有序规则列表；text / setvar / addvar 三种模式（变量定义与消费分离）
    4. 正则脚本  —— 用户消息前 / AI 回复；展示层(markdownOnly) ↔ 模型层(promptOnly)；深度范围
    5. 世界书    —— 触发词 / 常驻 / 排序 / 位置 / 深度 / 概率
    6. 装配与隔离 —— 注入顺序、历史轮数、输出骨架、排他模式、每个对话独立

用法：
    from roleplay.roleplay_preset_ui import RolePlayPresetDialog
    dlg = RolePlayPresetDialog(parent)
    dlg.exec()
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.RolePlayPresetUI")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

#: 使用指南（同时也是窗口内「使用说明」按钮显示的内容）
GUIDE_MD = Path(__file__).resolve().parent.parent / "角色扮演预设管理器使用指南.md"

from roleplay.roleplay_preset import (
    CATEGORY_LABELS, DEFAULT_PRESET, SAMPLING_SPEC, RolePlayPreset)


def _btn_qss(base: str = "#6c8ef5", hover: str = "#8fb0ff",
             pressed: str = "#4a6fd4") -> str:
    return (
        f"QPushButton{{background:{base};color:#fff;border:none;border-radius:8px;"
        f"padding:6px 16px;font-weight:600;}}"
        f"QPushButton:hover{{background:{hover};}}"
        f"QPushButton:pressed{{background:{pressed};}}"
        f"QPushButton:disabled{{background:#c7ccd6;}}"
    )


def _ghost_qss() -> str:
    return (
        "QPushButton{background:rgba(108,142,245,0.08);color:#4a6fd4;"
        "border:1px solid rgba(108,142,245,0.35);border-radius:8px;padding:6px 16px;}"
        "QPushButton:hover{background:rgba(108,142,245,0.16);}"
        "QPushButton:pressed{background:rgba(108,142,245,0.24);}"
    )


class RolePlayPresetDialog(QDialog):
    """角色扮演预设管理器主窗口。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 preset: Optional[RolePlayPreset] = None) -> None:
        super().__init__(parent)
        self._preset = preset or RolePlayPreset.instance()
        self._data: Dict[str, Any] = json.loads(
            json.dumps(self._preset.data(), ensure_ascii=False))
        self._accent = "#6c8ef5"
        self._sampling_widgets: Dict[str, Dict[str, Any]] = {}
        # 各页「来源分组」筛选下拉（导入酒馆预设后按 bundle 过滤）
        self._bundle_filters: Dict[str, QComboBox] = {}

        self.setWindowTitle("角色扮演预设管理器（点击即全屏 · F11 切换）")
        # 需求：窗口不能超出屏幕（小屏笔记本上底部按钮会被任务栏挡住）
        _w, _h = 1180, 860
        _sc = QApplication.primaryScreen()
        if _sc is not None:
            _av = _sc.availableGeometry()
            _w = min(_w, int(_av.width() * 0.94))
            _h = min(_h, int(_av.height() * 0.94))
        self.resize(max(_w, 760), max(_h, 560))
        # 需求：修复「单击打开无法正确显示」——主窗口为无边框 + 半透明背景，
        # 子对话框可能继承其窗口属性/QSS 导致内容不可见；这里显式声明为
        # 带系统标题栏的不透明窗口，并使用一套自包含样式，不再依赖父窗口 QSS。
        try:
            self.setWindowFlag(Qt.FramelessWindowHint, False)
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            # 标题栏保留最大化/还原按钮，方便切回窗口尺寸
            self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        except Exception:  # noqa: BLE001
            pass
        try:
            QShortcut(QKeySequence("F11"), self,
                      activated=self._toggle_fullscreen)
        except Exception:  # noqa: BLE001
            pass
        self._apply_base_style()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)

        self._tabs = QTabWidget()
        # 需求：每页都套一层滚动区——页面内容（设定卡列表、正则脚本、世界书…）
        # 比窗口高时以前会被直接裁掉、又无法滚动，表现为「管理器显示不全」。
        self._tabs.addTab(self._scroll_page(self._build_sampling_tab()),
                          "采样参数")
        self._tabs.addTab(self._scroll_page(self._build_cards_tab()), "设定卡")
        self._tabs.addTab(self._scroll_page(self._build_rules_tab()), "规则卡")
        self._tabs.addTab(self._scroll_page(self._build_regex_tab()), "正则脚本")
        self._tabs.addTab(self._scroll_page(self._build_lore_tab()), "世界书")
        self._tabs.addTab(self._scroll_page(self._build_assembly_tab()),
                          "装配与隔离")
        outer.addWidget(self._tabs, 1)

        outer.addWidget(self._build_footer())

    @staticmethod
    def _scroll_page(inner: QWidget) -> QScrollArea:
        """把标签页内容放进滚动区（内容再长也能滚到，不会被窗口裁掉）。"""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        # 横向不出现滑块：内容宽度始终跟随视口，只在纵向滚动
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 水平方向忽略 sizeHint：内容宽度始终等于视口宽，不撑出横向滑块
        inner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        area.setWidget(inner)
        return area

    # ================================================================ 主题
    def _apply_base_style(self) -> None:
        """自包含基础样式：显式声明各控件配色，屏蔽父窗口 QSS 继承。"""
        a = self._accent
        self.setStyleSheet(f"""
            RolePlayPresetDialog, QDialog#rolePlayPresetDlg {{
                background: #f4f5fa; }}
            QLabel {{ color: #2b2f3a; background: transparent; }}
            QCheckBox {{ color: #2b2f3a; background: transparent; }}
            QGroupBox {{
                color: #2b2f3a; font-weight: 600;
                border: 1px solid #d8dbe6; border-radius: 10px;
                margin-top: 10px; padding-top: 6px; background: #ffffff; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; }}
            QListWidget, QPlainTextEdit, QTextEdit {{
                background: #ffffff; color: #2b2f3a;
                border: 1px solid #d8dbe6; border-radius: 8px;
                selection-background-color: {a}26; selection-color: #2b2f3a; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                background: #ffffff; color: #2b2f3a;
                border: 1px solid #d8dbe6; border-radius: 6px;
                padding: 4px 6px; }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QComboBox:focus {{ border: 1px solid {a}; }}
            QComboBox QAbstractItemView {{
                background: #ffffff; color: #2b2f3a;
                selection-background-color: {a}26; selection-color: #2b2f3a; }}
            QTabWidget::pane {{
                border: 1px solid #d8dbe6; border-radius: 8px;
                background: #ffffff; top: -1px; }}
            QTabBar::tab {{
                background: #eceef5; color: #5a6070;
                padding: 7px 16px; margin-right: 3px;
                border-top-left-radius: 8px; border-top-right-radius: 8px; }}
            QTabBar::tab:selected {{ background: #ffffff; color: {a}; }}
            QTabBar::tab:hover {{ color: {a}; }}
            QScrollBar:vertical, QScrollBar:horizontal {{ background: #eef0f6; }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background: {a}8c; border-radius: 6px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        """)

    def showEvent(self, ev) -> None:  # noqa: N802
        """需求：单击打开就是一个全屏的管理窗口（标题栏仍可最小化/还原）。"""
        super().showEvent(ev)
        if not getattr(self, "_first_show", False):
            self._first_show = True
            try:
                self.showMaximized()
            except Exception:  # noqa: BLE001
                pass

    def _toggle_fullscreen(self) -> None:
        """全屏 / 还原窗口尺寸切换（F11 或底部按钮）。"""
        try:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
        except Exception:  # noqa: BLE001
            pass

    def apply_accent(self, accent: str) -> None:
        """同步主界面主题色（与主程序按钮风格一致）。"""
        if not accent:
            return
        self._accent = accent
        self._apply_base_style()
        for btn in self.findChildren(QPushButton):
            if btn.property("ghost"):
                btn.setStyleSheet(
                    "QPushButton{background:rgba(0,0,0,0.04);color:%s;"
                    "border:1px solid %s55;border-radius:8px;padding:6px 16px;}"
                    "QPushButton:hover{background:%s18;}"
                    "QPushButton:pressed{background:%s28;}"
                    % (accent, accent, accent, accent))
            else:
                btn.setStyleSheet(_btn_qss(accent))

    # ================================================================ 说明条
    def _intro(self, text: str) -> QWidget:
        """每页顶部的「这个功能是什么 / 怎么用」说明条。"""
        bar = QFrame()
        bar.setObjectName("introBar")
        bar.setStyleSheet(
            "QFrame#introBar{background:%s0f;border:1px solid %s33;"
            "border-radius:8px;}" % (self._accent, self._accent))
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.PlainText)
        lbl.setStyleSheet("color:#44495c;font-size:12px;")
        h.addWidget(lbl, 1)
        return bar

    # ================================================================ 来源分组
    def _bundles(self, key: str) -> List[str]:
        """收集某一页（rules/regex/lorebook）的导入来源名称。"""
        out = set()
        for item in (self._data.get(key) or []):
            b = str((item or {}).get("bundle") or "").strip()
            if b:
                out.add(b)
        return sorted(out)

    def _make_filter(self, key: str) -> QWidget:
        """生成某页的「来源分组」筛选下拉。"""
        combo = QComboBox()
        combo.addItem("全部来源", "")
        for b in self._bundles(key):
            combo.addItem(b, b)
        combo.currentIndexChanged.connect(
            lambda _i, k=key: self._reload_by_key(k))
        combo._filter_key = key
        self._bundle_filters[key] = combo
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("来源分组："))
        h.addWidget(combo, 1)
        return wrap

    def _reload_by_key(self, key: str) -> None:
        {"rules": self._reload_rules, "regex": self._reload_re,
         "lorebook": self._reload_lore}.get(key, lambda: None)()

    def _bundle_ok(self, key: str, item: Dict[str, Any]) -> bool:
        """条目是否通过当前页的来源分组筛选。"""
        combo = self._bundle_filters.get(key)
        if combo is None or not combo.currentData():
            return True
        return str((item or {}).get("bundle") or "") == str(combo.currentData())

    def _refresh_filters(self) -> None:
        """导入后刷新各页筛选下拉的选项（保持当前选择）。"""
        for combo in self._bundle_filters.values():
            cur = combo.currentData() or ""
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("全部来源", "")
            key = getattr(combo, "_filter_key", "")
            for b in self._bundles(key or "rules"):
                combo.addItem(b, b)
            idx = combo.findData(cur)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    # ================================================================ 页 1
    def _build_sampling_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】决定本次角色扮演对话的模型采样参数。\n"
            "【怎么用】先勾「启用采样参数覆盖」，再在需要的项目上勾「开」——"
            "没勾「开」的项一律沿用全局 config.json，所以你可以只接管温度、其它不动。\n"
            "【什么时候不用】只想改提示词、不想动模型行为时，整页关掉即可。"))

        self._override_box = QCheckBox("启用采样参数覆盖（未勾选时全部沿用全局设置）")
        self._override_box.setChecked(
            bool((self._data.get("sampling") or {}).get("override")))
        v.addWidget(self._override_box)
        tip = QLabel("勾选后，下方「开」的项目会覆盖 config.json 的同名参数；"
                     "未开的项目不受影响。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6b7280;font-size:12px;")
        v.addWidget(tip)

        params = (self._data.get("sampling") or {}).get("params") or {}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for spec in SAMPLING_SPEC:
            key = spec["key"]
            item = params.get(key) or {}
            row = QHBoxLayout()
            on = QCheckBox("开")
            on.setChecked(bool(item.get("on")))
            on.setFixedWidth(40)
            row.addWidget(on)
            if spec["kind"] == "float":
                box = QDoubleSpinBox()
                box.setRange(float(spec["min"]), float(spec["max"]))
                box.setSingleStep(float(spec.get("step") or 0.01))
                box.setDecimals(2)
                try:
                    box.setValue(float(item.get("value", spec["default"])))
                except Exception:  # noqa: BLE001
                    box.setValue(float(spec["default"]))
            elif spec["kind"] == "int":
                box = QSpinBox()
                box.setRange(int(spec["min"]), int(spec["max"]))
                box.setSingleStep(int(spec.get("step") or 1))
                try:
                    box.setValue(int(item.get("value", spec["default"])))
                except Exception:  # noqa: BLE001
                    box.setValue(int(spec["default"]))
            else:
                box = QComboBox()
                box.addItems([str(o) for o in spec.get("options") or []])
                cur = str(item.get("value", spec["default"]) or "")
                idx = box.findText(cur)
                box.setCurrentIndex(max(0, idx))
            box.setMinimumWidth(140)
            row.addWidget(box, 1)
            tip_lbl = QLabel(str(spec.get("tip") or ""))
            tip_lbl.setStyleSheet("color:#9aa0ac;font-size:11px;")
            tip_lbl.setWordWrap(True)
            row.addWidget(tip_lbl, 2)
            wrap = QWidget()
            wrap.setLayout(row)
            form.addRow(str(spec["label"]), wrap)
            self._sampling_widgets[key] = {"on": on, "box": box, "spec": spec}
        v.addLayout(form)
        v.addStretch(1)
        return w

    # ================================================================ 页 2
    #: 各类别的一句话用途说明（显示在分组标题旁）
    CARD_HINTS: Dict[str, str] = {
        "style": "文风、口吻、篇幅、叙事方式 —— 决定「怎么说话」（**单选**，"
                 "同一时间只加载这一张）",
        "world": "世界背景、地理、种族、势力 —— 决定「发生在哪儿」",
        "rolepersonal": "你自己（user）的人物卡、人物关系补充",
        "roletools": "限制、破限、格式与功能开关 —— 决定「能做什么、不能做什么」",
    }

    def _build_cards_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】设定卡是 roleplay/ 目录下的 JSON 文件，"
            "勾选后整份内容会原文注入系统提示词。\n"
            "【四个类别】对话风格（怎么说话）· 世界观（发生在哪儿）· "
            "个人设定（你是谁）· 角色工具（限制/破限/格式）。\n"
            "【怎么用】鼠标单击切换选中（世界观/个人设定/角色工具可 Ctrl+单击多选，"
            "「对话风格」为单选）；同一时间建议只勾一张「角色工具」预设卡，"
            "多张一起勾会互相打架。\n"
            "【严格加载】只有这里选中（并在设置面板保存）的卡片才会进入对话上下文，"
            "未选中的卡片一律不加载。"))
        try:
            from roleplay.roleplaytool import RolePlayManager
            mgr = RolePlayManager()
        except Exception as exc:  # noqa: BLE001
            v.addWidget(QLabel(f"设定卡模块加载失败：{exc}"))
            return w

        self._card_lists: Dict[str, QListWidget] = {}
        saved = self._data.get("cards") or {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        cats = mgr.categories()
        for i, cat in enumerate(cats):
            label = CATEGORY_LABELS.get(cat, cat)
            gb = QGroupBox(f"{label}（{cat}）")
            gv = QVBoxLayout(gb)
            hint = QLabel(self.CARD_HINTS.get(cat, ""))
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#8a90a0;font-size:11px;")
            gv.addWidget(hint)
            lst = QListWidget()
            # 需求：对话风格是**单选**（同一时间只生效一张风格卡）；
            # 世界观 / 个人设定 / 角色工具保持多选。
            lst.setSelectionMode(
                QAbstractItemView.SingleSelection if cat == "style"
                else QAbstractItemView.MultiSelection)
            try:
                names = mgr.list_cards(cat)
            except Exception:  # noqa: BLE001
                names = []
            for n in names:
                # 显示体积 + 卡片自述，便于判断要不要勾这张
                try:
                    kb = mgr.card_path(cat, n).stat().st_size / 1024
                    desc = str(mgr.load_card(cat, n).get("description") or "")
                except Exception:  # noqa: BLE001
                    kb, desc = 0.0, ""
                item = QListWidgetItem(f"{n}    ({kb:.1f} KB)")
                item.setToolTip(desc[:200] or n)
                lst.addItem(item)
            sel = set(str(x) for x in (saved.get(cat) or []))
            for k in range(lst.count()):
                it = lst.item(k)
                raw = it.text().split("    (")[0]
                if raw in sel:
                    it.setSelected(True)
            gv.addWidget(lst, 1)
            grid.addWidget(gb, i // 2, i % 2)
            self._card_lists[cat] = lst
        v.addLayout(grid, 1)

        row = QHBoxLayout()
        import_c = QPushButton("按类别导入设定卡…（酒馆预设→角色工具，世界书→世界观）")
        import_c.setProperty("ghost", True)
        import_c.setCursor(Qt.PointingHandCursor)
        import_c.setToolTip(
            "选择目录后自动识别：世界书 → 世界观，预设 → 角色工具，"
            "文件名含风格关键词 → 对话风格")
        import_c.clicked.connect(self._on_import_cards)
        row.addWidget(import_c)
        refresh_b = QPushButton("刷新列表")
        refresh_b.setProperty("ghost", True)
        refresh_b.setCursor(Qt.PointingHandCursor)
        refresh_b.clicked.connect(self._refresh_card_lists)
        row.addWidget(refresh_b)
        row.addStretch(1)
        v.addLayout(row)

        note = QLabel("鼠标悬停卡片可看它的自述说明；保存后立即对下一次对话生效。")
        note.setStyleSheet("color:#6b7280;font-size:12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        return w

    def _refresh_card_lists(self) -> None:
        """重新读取 roleplay/ 目录，刷新四类卡片列表（保留已勾选项）。"""
        try:
            from roleplay.roleplaytool import RolePlayManager
            mgr = RolePlayManager()
        except Exception:  # noqa: BLE001
            return
        for cat, lst in getattr(self, "_card_lists", {}).items():
            keep = {lst.item(i).text().split("    (")[0]
                    for i in range(lst.count()) if lst.item(i).isSelected()}
            lst.blockSignals(True)
            lst.clear()
            try:
                names = mgr.list_cards(cat)
            except Exception:  # noqa: BLE001
                names = []
            for n in names:
                try:
                    kb = mgr.card_path(cat, n).stat().st_size / 1024
                    desc = str(mgr.load_card(cat, n).get("description") or "")
                except Exception:  # noqa: BLE001
                    kb, desc = 0.0, ""
                item = QListWidgetItem(f"{n}    ({kb:.1f} KB)")
                item.setToolTip(desc[:200] or n)
                lst.addItem(item)
                if n in keep:
                    item.setSelected(True)
            lst.blockSignals(False)

    # ================================================================ 页 3
    def _build_rules_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】规则卡 = 按顺序追加到系统提示词里的一段固定指令。\n"
            "【三种模式】text 原文直接输出；setvar 把内容写入变量（覆盖）；"
            "addvar 追加到变量（累加）。变量用 {{getvar::变量名}} 消费，"
            "实现「定义与消费分离」。\n"
            "【排序】用 ↑ ↓ 调整，越靠前越先注入；可用宏：{{user}} {{char}} "
            "{{time}} {{random::甲::乙}}。"))
        h = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(self._make_filter("rules"))
        self._rule_list = QListWidget()
        self._rule_list.currentRowChanged.connect(self._on_rule_selected)
        left.addWidget(self._rule_list, 1)
        btns = QHBoxLayout()
        for text, fn in (("新增", self._rule_add), ("删除", self._rule_del),
                         ("启用/停用", self._rule_toggle),
                         ("启用本组", lambda: self._bulk_toggle("rules", True)),
                         ("停用本组", lambda: self._bulk_toggle("rules", False)),
                         ("↑", self._rule_up), ("↓", self._rule_down)):
            b = QPushButton(text)
            b.setProperty("ghost", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            btns.addWidget(b)
        left.addLayout(btns)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._rule_name = QLineEdit()
        self._rule_mode = QComboBox()
        self._rule_mode.addItems(["text（直接输出）", "setvar（写入变量，覆盖）",
                                  "addvar（追加到变量）"])
        self._rule_var = QLineEdit()
        self._rule_var.setPlaceholderText("变量名，如 COT-防全知")
        self._rule_enabled = QCheckBox("启用")
        form.addRow("名称", self._rule_name)
        form.addRow("模式", self._rule_mode)
        form.addRow("变量名", self._rule_var)
        form.addRow("", self._rule_enabled)
        right.addLayout(form)
        right.addWidget(QLabel("内容（可用 {{getvar::变量名}} 消费变量、"
                               "{{random::a::b}} 随机）："))
        self._rule_content = QPlainTextEdit()
        self._rule_content.setMinimumHeight(220)
        right.addWidget(self._rule_content, 1)
        save_b = QPushButton("应用修改")
        save_b.setProperty("ghost", True)
        save_b.setCursor(Qt.PointingHandCursor)
        save_b.clicked.connect(self._rule_apply)
        right.addWidget(save_b)
        h.addLayout(right, 2)

        v.addLayout(h, 1)
        self._reload_rules()
        return w

    def _reload_rules(self) -> None:
        self._rule_list.blockSignals(True)
        self._rule_list.clear()
        for r in (self._data.get("rules") or []):
            if not self._bundle_ok("rules", r):
                continue
            mark = "√" if r.get("enabled") else "×"
            mode = str(r.get("mode") or "text")
            src = str(r.get("bundle") or "")
            tag = f" 〈{src}〉" if src else ""
            self._rule_list.addItem(
                f"{mark} [{mode}] {r.get('name') or ''}{tag}")
        self._rule_list.blockSignals(False)
        if self._rule_list.count() > 0:
            self._rule_list.setCurrentRow(0)

    def _rule_toggle(self) -> None:
        """启停当前选中的规则（批量导入后逐条开关更方便）。"""
        row = self._rule_list.currentRow()
        arr = self._data.get("rules") or []
        if row < 0 or row >= len(arr):
            return
        arr[row]["enabled"] = not arr[row].get("enabled")
        self._reload_rules()
        self._rule_list.setCurrentRow(row)

    def _bulk_toggle(self, key: str, enabled: bool) -> None:
        """按当前选中的「来源分组」批量启停（未选分组时提示）。"""
        combo = self._bundle_filters.get(key)
        bundle = str(combo.currentData() or "") if combo else ""
        if not bundle:
            QMessageBox.information(
                self, "批量启停",
                "请先在顶部「来源分组」中选择一个分组，再批量启停。\n"
                "（避免把多个预设的规则同时打开）")
            return
        n = 0
        for item in (self._data.get(key) or []):
            if str(item.get("bundle") or "") == bundle:
                item["enabled"] = bool(enabled)
                n += 1
        self._reload_by_key(key)

    def _on_rule_selected(self, row: int) -> None:
        rules = self._data.get("rules") or []
        if row < 0 or row >= len(rules):
            return
        r = rules[row]
        self._rule_name.setText(str(r.get("name") or ""))
        mode = str(r.get("mode") or "text")
        self._rule_mode.setCurrentIndex({"text": 0, "setvar": 1, "addvar": 2}.get(mode, 0))
        self._rule_var.setText(str(r.get("variable") or ""))
        self._rule_enabled.setChecked(bool(r.get("enabled")))
        self._rule_content.setPlainText(str(r.get("content") or ""))

    def _rule_apply(self) -> None:
        row = self._rule_list.currentRow()
        rules = self._data.get("rules") or []
        if row < 0 or row >= len(rules):
            return
        rules[row]["name"] = self._rule_name.text().strip() or "未命名规则"
        rules[row]["mode"] = ["text", "setvar", "addvar"][self._rule_mode.currentIndex()]
        rules[row]["variable"] = self._rule_var.text().strip()
        rules[row]["enabled"] = self._rule_enabled.isChecked()
        rules[row]["content"] = self._rule_content.toPlainText()
        self._reload_rules()
        self._rule_list.setCurrentRow(row)

    def _rule_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增规则", "规则名称：")
        if not ok:
            return
        self._data.setdefault("rules", []).append(
            RolePlayPreset.new_rule(name=str(name).strip() or "新规则"))
        self._reload_rules()
        self._rule_list.setCurrentRow(self._rule_list.count() - 1)

    def _rule_del(self) -> None:
        row = self._rule_list.currentRow()
        if row < 0:
            return
        (self._data.get("rules") or []).pop(row)
        self._reload_rules()

    def _move(self, key: str, delta: int, reload_fn) -> None:
        row = self._rule_list.currentRow() if key == "rules" else None
        arr = self._data.get(key) or []
        if row is None or row < 0 or row >= len(arr):
            return
        new = row + delta
        if new < 0 or new >= len(arr):
            return
        arr[row], arr[new] = arr[new], arr[row]
        reload_fn()
        if key == "rules":
            self._rule_list.setCurrentRow(new)

    def _rule_up(self) -> None:
        self._move("rules", -1, self._reload_rules)

    def _rule_down(self) -> None:
        self._move("rules", 1, self._reload_rules)

    # ================================================================ 页 4
    def _build_regex_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】正则脚本 = 自动改写文本，发送前 / 收到后都能改，"
            "不用改角色卡。\n"
            "【两个通道】都不勾 = 两层都生效；仅展示层 = 只有你看到的被改，"
            "模型看到的仍是原文；仅模型层 = 只有送给模型的被改，界面显示原文。"
            "两个都勾 = 配置错误，该条跳过。\n"
            "【深度】0 = 最新一条消息；最大深度填 0 表示不限。"))
        h = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(self._make_filter("regex"))
        self._re_list = QListWidget()
        self._re_list.currentRowChanged.connect(self._on_re_selected)
        left.addWidget(self._re_list, 1)
        btns = QHBoxLayout()
        for text, fn in (("新增", self._re_add), ("删除", self._re_del),
                         ("启用本组", lambda: self._bulk_toggle("regex", True)),
                         ("停用本组", lambda: self._bulk_toggle("regex", False))):
            b = QPushButton(text)
            b.setProperty("ghost", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            btns.addWidget(b)
        left.addLayout(btns)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._re_name = QLineEdit()
        self._re_find = QLineEdit()
        self._re_find.setPlaceholderText("支持 /正则/gi 或裸正则")
        self._re_replace = QLineEdit()
        self._re_replace.setPlaceholderText("替换串，可用 $1 引用分组")
        self._re_user = QCheckBox("用户消息")
        self._re_ai = QCheckBox("AI 回复")
        self._re_md = QCheckBox("仅展示层(markdownOnly)")
        self._re_po = QCheckBox("仅模型层(promptOnly)")
        self._re_min = QSpinBox(); self._re_min.setRange(0, 999)
        self._re_max = QSpinBox(); self._re_max.setRange(0, 999)
        self._re_max.setSpecialValueText("不限")
        self._re_enabled = QCheckBox("启用")
        form.addRow("名称", self._re_name)
        form.addRow("匹配", self._re_find)
        form.addRow("替换", self._re_replace)
        row_place = QHBoxLayout()
        row_place.addWidget(self._re_user); row_place.addWidget(self._re_ai)
        row_place.addStretch(1)
        wrap_place = QWidget(); wrap_place.setLayout(row_place)
        form.addRow("作用位置", wrap_place)
        row_ch = QHBoxLayout()
        row_ch.addWidget(self._re_md); row_ch.addWidget(self._re_po)
        row_ch.addStretch(1)
        wrap_ch = QWidget(); wrap_ch.setLayout(row_ch)
        form.addRow("通道", wrap_ch)
        row_d = QHBoxLayout()
        row_d.addWidget(QLabel("最小深度")); row_d.addWidget(self._re_min)
        row_d.addWidget(QLabel("最大深度")); row_d.addWidget(self._re_max)
        row_d.addStretch(1)
        wrap_d = QWidget(); wrap_d.setLayout(row_d)
        form.addRow("深度范围", wrap_d)
        form.addRow("", self._re_enabled)
        right.addLayout(form)

        right.addWidget(QLabel("说明：深度 0 = 最新一条消息；"
                               "min=1/max=2 表示只对最近两层生效。"
                               "两个通道都不勾则两层都生效，同时勾选视为配置错误并跳过。"))
        right.addWidget(QLabel("示例：把套话标记出来让模型自己看见 → "
                               "匹配 /(不是[^，]+，是[^。]+。)/g  替换 <cliche>$1</cliche>"))
        apply_b = QPushButton("应用修改")
        apply_b.setProperty("ghost", True)
        apply_b.setCursor(Qt.PointingHandCursor)
        apply_b.clicked.connect(self._re_apply)
        right.addWidget(apply_b)
        h.addLayout(right, 2)

        v.addLayout(h, 1)
        self._reload_re()
        return w

    def _reload_re(self) -> None:
        self._re_list.blockSignals(True)
        self._re_list.clear()
        for s in (self._data.get("regex") or []):
            if not self._bundle_ok("regex", s):
                continue
            mark = "√" if s.get("enabled") else "×"
            src = str(s.get("bundle") or "")
            tag = f" 〈{src}〉" if src else ""
            self._re_list.addItem(f"{mark} {s.get('name') or ''}{tag}")
        self._re_list.blockSignals(False)
        if self._re_list.count() > 0:
            self._re_list.setCurrentRow(0)

    def _on_re_selected(self, row: int) -> None:
        arr = self._data.get("regex") or []
        if row < 0 or row >= len(arr):
            return
        s = arr[row]
        self._re_name.setText(str(s.get("name") or ""))
        self._re_find.setText(str(s.get("find") or ""))
        self._re_replace.setText(str(s.get("replace") or ""))
        pl = [int(x) for x in (s.get("placement") or [])]
        self._re_user.setChecked(1 in pl)
        self._re_ai.setChecked(2 in pl)
        self._re_md.setChecked(bool(s.get("markdown_only")))
        self._re_po.setChecked(bool(s.get("prompt_only")))
        self._re_min.setValue(int(s.get("min_depth") or 0))
        self._re_max.setValue(int(s.get("max_depth") or 0))
        self._re_enabled.setChecked(bool(s.get("enabled")))

    def _re_apply(self) -> None:
        row = self._re_list.currentRow()
        arr = self._data.get("regex") or []
        if row < 0 or row >= len(arr):
            return
        pl: List[int] = []
        if self._re_user.isChecked():
            pl.append(1)
        if self._re_ai.isChecked():
            pl.append(2)
        arr[row].update({
            "name": self._re_name.text().strip() or "未命名",
            "find": self._re_find.text(),
            "replace": self._re_replace.text(),
            "placement": pl or [2],
            "markdown_only": self._re_md.isChecked(),
            "prompt_only": self._re_po.isChecked(),
            "min_depth": self._re_min.value(),
            "max_depth": self._re_max.value(),
            "enabled": self._re_enabled.isChecked(),
        })
        self._reload_re()
        self._re_list.setCurrentRow(row)

    def _re_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增正则", "名称：")
        if not ok:
            return
        self._data.setdefault("regex", []).append(
            RolePlayPreset.new_regex(name=str(name).strip() or "新正则"))
        self._reload_re()
        self._re_list.setCurrentRow(self._re_list.count() - 1)

    def _re_del(self) -> None:
        row = self._re_list.currentRow()
        if row < 0:
            return
        (self._data.get("regex") or []).pop(row)
        self._reload_re()

    # ================================================================ 页 5
    def _build_lore_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】世界书 = 按关键词触发才注入的设定，比设定卡省上下文。\n"
            "【触发规则】主触发词命中任意一个即命中；次触发词必须与主触发词同时出现；"
            "勾「常驻」则无条件一直注入。\n"
            "【参数】排序越小越靠前；深度 = 注入到距最新多少层；概率% = 命中后按概率决定。"))
        h = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(self._make_filter("lorebook"))
        self._lore_list = QListWidget()
        self._lore_list.currentRowChanged.connect(self._on_lore_selected)
        left.addWidget(self._lore_list, 1)
        btns = QHBoxLayout()
        for text, fn in (("新增", self._lore_add), ("删除", self._lore_del)):
            b = QPushButton(text)
            b.setProperty("ghost", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            btns.addWidget(b)
        left.addLayout(btns)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._lore_name = QLineEdit()
        self._lore_keys = QLineEdit()
        self._lore_keys.setPlaceholderText("主触发词，逗号分隔")
        self._lore_sec = QLineEdit()
        self._lore_sec.setPlaceholderText("次触发词，逗号分隔（需与主触发词同时出现）")
        self._lore_const = QCheckBox("常驻（不触发也注入）")
        self._lore_enabled = QCheckBox("启用")
        self._lore_order = QSpinBox(); self._lore_order.setRange(0, 9999)
        self._lore_depth = QSpinBox(); self._lore_depth.setRange(0, 999)
        self._lore_prob = QSpinBox(); self._lore_prob.setRange(1, 100)
        self._lore_pos = QComboBox()
        self._lore_pos.addItems(["0 角色定义前", "1 角色定义后", "2 深度锚点后"])
        form.addRow("名称", self._lore_name)
        form.addRow("主触发词", self._lore_keys)
        form.addRow("次触发词", self._lore_sec)
        form.addRow("", self._lore_const)
        row = QHBoxLayout()
        row.addWidget(QLabel("排序")); row.addWidget(self._lore_order)
        row.addWidget(QLabel("深度")); row.addWidget(self._lore_depth)
        row.addWidget(QLabel("概率%")); row.addWidget(self._lore_prob)
        wrap = QWidget(); wrap.setLayout(row)
        form.addRow("参数", wrap)
        form.addRow("插入位置", self._lore_pos)
        form.addRow("", self._lore_enabled)
        right.addLayout(form)
        right.addWidget(QLabel("条目内容："))
        self._lore_content = QPlainTextEdit()
        self._lore_content.setMinimumHeight(180)
        right.addWidget(self._lore_content, 1)
        apply_b = QPushButton("应用修改")
        apply_b.setProperty("ghost", True)
        apply_b.setCursor(Qt.PointingHandCursor)
        apply_b.clicked.connect(self._lore_apply)
        right.addWidget(apply_b)
        h.addLayout(right, 2)

        v.addLayout(h, 1)
        self._reload_lore()
        return w

    def _reload_lore(self) -> None:
        self._lore_list.blockSignals(True)
        self._lore_list.clear()
        for e in (self._data.get("lorebook") or []):
            if not self._bundle_ok("lorebook", e):
                continue
            mark = "√" if e.get("enabled") else "×"
            tag = "常驻" if e.get("constant") else ""
            src = str(e.get("bundle") or "")
            if src:
                tag += f" 〈{src}〉"
            self._lore_list.addItem(f"{mark} {e.get('name') or ''} {tag}")
        self._lore_list.blockSignals(False)
        if self._lore_list.count() > 0:
            self._lore_list.setCurrentRow(0)

    @staticmethod
    def _split_keys(text: str) -> List[str]:
        return [s.strip() for s in (text or "").replace("，", ",").split(",")
                if s.strip()]

    def _on_lore_selected(self, row: int) -> None:
        arr = self._data.get("lorebook") or []
        if row < 0 or row >= len(arr):
            return
        e = arr[row]
        self._lore_name.setText(str(e.get("name") or ""))
        self._lore_keys.setText(",".join(str(k) for k in (e.get("keys") or [])))
        self._lore_sec.setText(
            ",".join(str(k) for k in (e.get("secondary_keys") or [])))
        self._lore_const.setChecked(bool(e.get("constant")))
        self._lore_enabled.setChecked(bool(e.get("enabled")))
        self._lore_order.setValue(int(e.get("order") or 100))
        self._lore_depth.setValue(int(e.get("depth") or 4))
        self._lore_prob.setValue(int(e.get("probability") or 100))
        self._lore_pos.setCurrentIndex(int(e.get("position") or 1))
        self._lore_content.setPlainText(str(e.get("content") or ""))

    def _lore_apply(self) -> None:
        row = self._lore_list.currentRow()
        arr = self._data.get("lorebook") or []
        if row < 0 or row >= len(arr):
            return
        arr[row].update({
            "name": self._lore_name.text().strip() or "未命名",
            "comment": self._lore_name.text().strip() or "未命名",
            "keys": self._split_keys(self._lore_keys.text()),
            "secondary_keys": self._split_keys(self._lore_sec.text()),
            "constant": self._lore_const.isChecked(),
            "enabled": self._lore_enabled.isChecked(),
            "order": self._lore_order.value(),
            "depth": self._lore_depth.value(),
            "probability": self._lore_prob.value(),
            "position": self._lore_pos.currentIndex(),
            "content": self._lore_content.toPlainText(),
        })
        self._reload_lore()
        self._lore_list.setCurrentRow(row)

    def _lore_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增世界书条目", "名称：")
        if not ok:
            return
        self._data.setdefault("lorebook", []).append(
            RolePlayPreset.new_lore(name=str(name).strip() or "新条目"))
        self._reload_lore()
        self._lore_list.setCurrentRow(self._lore_list.count() - 1)

    def _lore_del(self) -> None:
        row = self._lore_list.currentRow()
        if row < 0:
            return
        (self._data.get("lorebook") or []).pop(row)
        self._reload_lore()

    # ================================================================ 页 6
    def _build_assembly_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.addWidget(self._intro(
            "【这一页是干什么的】决定「上下文怎么拼」和「记忆怎么隔离」。\n"
            "上下文装配 = 注入最近几轮对话、是否禁用技能/联网、是否注入记忆与情绪标签。\n"
            "对话隔离 = 角色扮演与日常对话互不读取；「每个对话独立」= 每个新会话各自一份记忆。\n"
            "输出骨架 = 约束模型输出的组织格式，可消费规则卡定义的 {{getvar::变量名}}。"))

        asm = self._data.get("assembly") or {}
        gb1 = QGroupBox("上下文装配")
        f1 = QFormLayout(gb1)
        self._hist_turns = QSpinBox()
        self._hist_turns.setRange(0, 50)
        self._hist_turns.setValue(int(asm.get("history_turns", 5)))
        f1.addRow("注入最近对话轮数", self._hist_turns)
        self._exclusive = QCheckBox("排他模式：只使用角色/群聊 + 角色扮演工具")
        self._exclusive.setChecked(bool(asm.get("exclusive_tools", True)))
        f1.addRow("", self._exclusive)
        self._use_mem = QCheckBox("注入长期记忆（角色扮演命名空间内）")
        self._use_mem.setChecked(bool(asm.get("use_memory", True)))
        f1.addRow("", self._use_mem)
        self._emo_tag = QCheckBox("要求每句末尾 〖情绪标签〗")
        self._emo_tag.setChecked(bool(asm.get("require_emotion_tag", True)))
        f1.addRow("", self._emo_tag)
        self._daily = QCheckBox("注入日常作息上下文（默认关闭）")
        self._daily.setChecked(bool(asm.get("use_daily_context", False)))
        f1.addRow("", self._daily)

        # 需求：主角设定（单选）——指定「你」在扮演里使用的个人设定卡
        self._persona = QComboBox()
        self._persona.setToolTip(
            "选择一张「个人设定」卡作为主角（你本人）的设定，"
            "会以【主角设定（{{user}} 本人）】单独注入，不与角色设定混淆。")
        self._persona.addItem("（不使用主角设定）", "")
        try:
            from roleplay.roleplaytool import RolePlayManager
            for name in RolePlayManager().list_cards("rolepersonal"):
                self._persona.addItem(name, name)
        except Exception:  # noqa: BLE001
            pass
        _cur_persona = str(asm.get("user_persona") or "")
        _idx = self._persona.findData(_cur_persona)
        self._persona.setCurrentIndex(_idx if _idx >= 0 else 0)
        f1.addRow("主角设定（你本人的身份卡，单选）", self._persona)

        # 需求：允许 OOC / 突破世界观（仍参考世界书背景设定）
        self._ooc = QCheckBox(
            "允许 OOC / 突破世界观（仍参考世界书背景设定）")
        self._ooc.setToolTip(
            "允许出戏、元叙事、跨世界观联动等破限演绎；世界书 / 世界观卡"
            "仍作为背景参考注入，保持地名、势力、历史、术语基本自洽。")
        self._ooc.setChecked(bool(asm.get("ooc_mode", False)))
        f1.addRow("", self._ooc)
        v.addWidget(gb1)

        gb2 = QGroupBox("对话隔离")
        f2 = QFormLayout(gb2)
        self._iso_normal = QCheckBox("角色扮演对话与日常对话互不读取（建议常开）")
        self._iso_normal.setChecked(
            bool((self._data.get("isolation") or {}).get("isolate_normal", True)))
        f2.addRow("", self._iso_normal)
        self._per_session = QCheckBox("每个对话独立：每个新会话各自一份记忆")
        self._per_session.setChecked(
            bool((self._data.get("isolation") or {}).get("per_session", False)))
        f2.addRow("", self._per_session)
        tip2 = QLabel("勾选「每个对话独立」后，开启角色扮演时每个新会话都有独立的"
                      "短期与长期记忆；不勾选则所有角色扮演会话共享一份记忆。"
                      "无论是否勾选，角色扮演记忆都与日常对话完全隔离。")
        tip2.setWordWrap(True)
        tip2.setStyleSheet("color:#6b7280;font-size:12px;")
        f2.addRow("", tip2)
        v.addWidget(gb2)

        gb3 = QGroupBox("输出骨架（可用 {{getvar::变量名}} 消费规则卡定义的变量）")
        gv = QVBoxLayout(gb3)
        self._skeleton = QPlainTextEdit()
        self._skeleton.setPlainText(str(asm.get("output_skeleton") or ""))
        self._skeleton.setPlaceholderText(
            "留空表示不约束输出格式。示例：\n"
            "<draft_notes>{{getvar::COT-防全知}}</draft_notes>\n"
            "{{getvar::gs-正文}}")
        self._skeleton.setMinimumHeight(140)
        gv.addWidget(self._skeleton)
        v.addWidget(gb3)
        v.addStretch(1)
        return w

    # ================================================================ 底部
    def _build_footer(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 6, 0, 0)
        save_b = QPushButton("保存并关闭")
        save_b.setCursor(Qt.PointingHandCursor)
        save_b.clicked.connect(self._on_save)
        import_b = QPushButton("导入为规则卡／正则／世界书…")
        import_b.setProperty("ghost", True)
        import_b.setCursor(Qt.PointingHandCursor)
        import_b.setToolTip("把酒馆文件拆开塞进预设内部的三个列表（适合逐条精修）")
        import_b.clicked.connect(self._on_import)
        help_b = QPushButton("使用说明")
        help_b.setProperty("ghost", True)
        help_b.setCursor(Qt.PointingHandCursor)
        help_b.setToolTip("打开《角色扮演预设管理器使用指南》")
        help_b.clicked.connect(self._on_help)
        full_b = QPushButton("全屏/还原")
        full_b.setProperty("ghost", True)
        full_b.setCursor(Qt.PointingHandCursor)
        full_b.setToolTip("F11")
        full_b.clicked.connect(self._toggle_fullscreen)
        cancel_b = QPushButton("取消")
        cancel_b.setProperty("ghost", True)
        cancel_b.setCursor(Qt.PointingHandCursor)
        cancel_b.clicked.connect(self.reject)
        reset_b = QPushButton("恢复默认")
        reset_b.setProperty("ghost", True)
        reset_b.setCursor(Qt.PointingHandCursor)
        reset_b.clicked.connect(self._on_reset)
        h.addWidget(save_b)
        h.addWidget(import_b)
        h.addWidget(help_b)
        h.addWidget(full_b)
        h.addWidget(cancel_b)
        h.addWidget(reset_b)
        h.addStretch(1)
        info = QLabel("保存路径：data/roleplay_preset.json")
        info.setStyleSheet("color:#9aa0ac;font-size:12px;")
        h.addWidget(info)
        return w

    # ================================================================ 动作
    def _collect(self) -> Dict[str, Any]:
        """把界面控件的值汇总成预设 dict。"""
        data = self._data

        # 采样参数
        params: Dict[str, Any] = {}
        for key, wgt in self._sampling_widgets.items():
            box = wgt["box"]
            if isinstance(box, QComboBox):
                val: Any = box.currentText()
            elif isinstance(box, (QSpinBox,)):
                val = int(box.value())
            else:
                val = float(box.value())
            params[key] = {"on": wgt["on"].isChecked(), "value": val}
        data["sampling"] = {"override": self._override_box.isChecked(),
                            "params": params}

        # 设定卡
        cards: Dict[str, List[str]] = {}
        for cat, lst in getattr(self, "_card_lists", {}).items():
            names = [lst.item(i).text().split("    (")[0]
                     for i in range(lst.count()) if lst.item(i).isSelected()]
            # 需求：对话风格单选——只保留一张（列表已是单选，这里再兜底一次）
            if cat == "style" and len(names) > 1:
                names = names[:1]
            cards[cat] = names
        data["cards"] = cards

        # 装配 / 隔离
        data["assembly"] = {
            "inject_order": ["rolepersonal", "world", "style", "roletools"],
            "history_turns": self._hist_turns.value(),
            "output_skeleton": self._skeleton.toPlainText(),
            "exclusive_tools": self._exclusive.isChecked(),
            "use_memory": self._use_mem.isChecked(),
            "require_emotion_tag": self._emo_tag.isChecked(),
            "use_daily_context": self._daily.isChecked(),
            # 主角设定（单选）与 OOC / 破限开关（与设置面板保持一致）
            "user_persona": str(self._persona.currentData() or ""),
            "ooc_mode": bool(self._ooc.isChecked()),
        }
        data["isolation"] = {
            "isolate_normal": self._iso_normal.isChecked(),
            "per_session": self._per_session.isChecked(),
        }
        return data

    def _on_save(self) -> None:
        data = self._collect()
        # 同步设定卡到 config.json，保持旧逻辑（设置面板/其它入口）一致
        try:
            from config_loader import ConfigLoader
            keys = []
            for cat, names in (data.get("cards") or {}).items():
                keys.extend(f"{cat}/{n}" for n in names)
            cfg = ConfigLoader.instance()
            cfg.set(sorted(keys), "ui", "roleplay_cards")
            cfg.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("同步设定卡到 config.json 失败: %s", exc)
        if self._preset.save(data):
            QMessageBox.information(self, "角色扮演预设", "已保存。")
            self.accept()
        else:
            QMessageBox.warning(self, "角色扮演预设", "保存失败，请查看日志。")

    def _on_help(self) -> None:
        """打开《角色扮演预设管理器使用指南》（窗口内直接阅读）。"""
        try:
            text = GUIDE_MD.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            QMessageBox.warning(self, "使用说明",
                                f"未找到说明文件：{GUIDE_MD}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("角色扮演预设管理器 · 使用指南")
        _w, _h = 900, 700                      # 同样不超出屏幕可用区域
        _sc = QApplication.primaryScreen()
        if _sc is not None:
            _av = _sc.availableGeometry()
            _w = min(_w, int(_av.width() * 0.92))
            _h = min(_h, int(_av.height() * 0.92))
        dlg.resize(max(_w, 640), max(_h, 420))
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        try:
            view.setMarkdown(text)
        except Exception:  # noqa: BLE001
            view.setPlainText(text)
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        close_b = QPushButton("关闭")
        close_b.setCursor(Qt.PointingHandCursor)
        close_b.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(close_b)
        lay.addLayout(row)
        try:
            dlg.setStyleSheet(self.styleSheet())
        except Exception:  # noqa: BLE001
            pass
        dlg.exec()

    def _on_import_cards(self) -> None:
        """按类别把酒馆文件导入为设定卡（世界书→世界观，预设→角色工具）。"""
        start = self._last_import_dir() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "选择包含酒馆预设／世界书 JSON 的目录", start)
        if not path:
            return
        self._remember_import_dir(path)
        only_enabled = QMessageBox.question(
            self, "导入范围",
            "只导入源文件中处于「启用」状态的提示词吗？\n"
            "（推荐选「是」；选「否」会把未启用的实验性条目也一起导入，卡片会更大）",
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        try:
            from roleplay.roleplay_import import import_as_cards
            summary = import_as_cards(path, self._preset,
                                      only_enabled=only_enabled)
        except Exception as exc:  # noqa: BLE001
            logger.exception("分类导入设定卡失败")
            QMessageBox.warning(self, "导入设定卡", f"导入失败：{exc}")
            return
        self._refresh_card_lists()
        self._data = json.loads(
            json.dumps(self._preset.data(), ensure_ascii=False))
        self._refresh_filters()
        self._reload_rules()
        self._reload_re()
        self._reload_lore()
        lines = [f"扫描到 {len(summary['files'])} 个文件，已写入 {len(summary['cards'])} 张设定卡：\n"]
        for c in summary["cards"]:
            label = CATEGORY_LABELS.get(c["category"], c["category"])
            lines.append(f"  {c['name']}  →  【{label}】"
                         f"（条目 {c['prompts']}，正则 {c['regex']}）")
        lines.append("\n请回到「设定卡」页勾选需要的卡片。")
        if summary["errors"]:
            lines.append("\n失败：\n" + "\n".join(summary["errors"]))
        QMessageBox.information(self, "导入设定卡", "\n".join(lines))

    def _last_import_dir(self) -> str:
        try:
            from config_loader import ConfigLoader
            last = str(ConfigLoader.instance().get(
                "ui", "roleplay_import_dir", default="") or "")
            return last if last and Path(last).is_dir() else ""
        except Exception:  # noqa: BLE001
            return ""

    def _remember_import_dir(self, path: str) -> None:
        try:
            from config_loader import ConfigLoader
            cfg = ConfigLoader.instance()
            cfg.set(path, "ui", "roleplay_import_dir")
            cfg.save()
        except Exception:  # noqa: BLE001
            pass

    def _on_import(self) -> None:
        """从目录批量导入酒馆预设 / 世界书（分类挂到规则卡／正则／世界书）。"""
        start = self._last_import_dir() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "选择包含酒馆预设／世界书 JSON 的目录", start)
        if not path:
            return
        self._remember_import_dir(path)
        with_sampling = QMessageBox.question(
            self, "导入采样参数",
            "是否同时导入预设文件里的采样参数？\n"
            "（会覆盖当前已开启的采样项，并打开「采样参数覆盖」）",
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        try:
            # 先保存当前编辑内容，再导入，最后回读刷新界面
            self._preset.save(self._collect())
            from roleplay.roleplay_import import import_directory
            summary = import_directory(path, self._preset, with_sampling)
        except Exception as exc:  # noqa: BLE001
            logger.exception("导入酒馆预设失败")
            QMessageBox.warning(self, "导入酒馆预设", f"导入失败：{exc}")
            return
        self._data = json.loads(
            json.dumps(self._preset.data(), ensure_ascii=False))
        self._refresh_filters()
        self._reload_rules()
        self._reload_re()
        self._reload_lore()
        c = summary["counts"]
        msg = (f"扫描到 {len(summary['files'])} 个可导入文件\n\n"
               f"规则卡：{c['rules']} 条\n"
               f"正则脚本：{c['regex']} 条\n"
               f"世界书条目：{c['lorebook']} 条\n\n"
               "已在各页顶部生成「来源分组」筛选。\n"
               "导入的规则卡与正则默认停用：请选择某个分组后用"
               "「启用本组」按需开启（建议一次只启用一个预设组）。")
        if summary["errors"]:
            msg += "\n\n以下文件解析失败：\n" + "\n".join(summary["errors"])
        QMessageBox.information(self, "导入酒馆预设", msg)

    def _on_reset(self) -> None:
        if QMessageBox.question(
                self, "恢复默认", "确定要清空当前预设并恢复默认值吗？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        import copy
        self._data = copy.deepcopy(DEFAULT_PRESET)
        self._preset.save(self._data)
        QMessageBox.information(self, "角色扮演预设", "已恢复默认，请重新打开窗口查看。")
        self.accept()
