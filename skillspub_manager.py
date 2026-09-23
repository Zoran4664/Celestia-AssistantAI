# -*- coding: utf-8 -*-
"""
skillspub_manager.py —— skill库管理器（skillspub 公共技能库 · 注册表风格 · 与技能工具管理器同风格）

功能：
- 一个公共技能 = skillspub 里的一个文件夹「项目」（内含 SKILL.md 等技能文件）；
- 可视化管理 skillspub 目录：skillspub/catalog.json 仅作索引，
  skillspub/<技能文件夹>/SKILL.md 存放该技能详细介绍与执行说明；
- 索引条目字段：
      name        技能名（唯一；供 AI 按名称调用）
      folder      技能文件夹名（目录索引字段，指向该技能的文件夹）
      category    分类
      enabled     启用（关闭后不出现在 skillspub 指引中，也不会被调用）
      summary     skill简介（写入 skillspub 指引，供 AI/用户快速挑选）
      keywords    调用关键词（逗号分隔；无小模型时的文本匹配降级用）
- 仿注册表编辑器布局：左侧键树 + 右侧值属性面板
- 新建 / 查看 / 修改 / 删除公共技能：保存时同步更新索引（catalog.json）
  与技能文件夹文件（新建/覆盖 SKILL.md、改名整体移动文件夹、删除移除整个文件夹）

运行方式：python skillspub_manager.py
样式：与 skilltools.py / 主程序 ui_manager.py 保持一致的 HTML5 响应式外观与主题色跟随。

配合两个内置开关指令使用（在技能工具管理器中可启停/改名）：
    \\@skillspub   skill库·手动模式：允许用户/AI 自由调用 skillspub 中所有技能（可多个）
    \\@autoskills  skill库·自动模式：AI 程序读取指引后自动挑选并组合调用（可多个）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 与技能工具管理器共用一套样式助手，保证视觉一致
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize  # type: ignore
from PySide6.QtGui import (  # type: ignore
    QColor, QMouseEvent, QPainter,
)
from PySide6.QtWidgets import (  # type: ignore
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QStyle, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from utils.styled_msg import (  # noqa: E402
    styled_box, styled_info, styled_question, styled_warning)
from utils.async_worker import spawn_worker  # noqa: E402

# 复用技能工具管理器的颜色 / 样式常量与助手函数
from skilltools import (  # noqa: E402
    ACCENT, root_qss, _parse_aliases, _INVALID_FS_CHARS,  # type: ignore
)
# 数据层（纯逻辑，可被聊天线程/管理器共用）
import skillspub_core as pub
# 导入器：外部技能包（.zip / .rar / .md）→ skillspub 文件夹 + 索引
from skill_import import SkillImporter  # noqa: E402


class SkillPubManagerWindow(QMainWindow):
    """无边框圆角窗口：管理 skill库（skillspub）公共技能目录（catalog.json）。"""

    def __init__(self) -> None:
        super().__init__(None)
        # 主题色跟随主界面配置
        try:
            from config_loader import ConfigLoader
            cfg = ConfigLoader.instance()
            self._accent = cfg.accent() or ACCENT
            self._scale = float(cfg.get("ui", "scale", default=1.0) or 1.0)
        except Exception:  # noqa: BLE001 - 配置未初始化时回退默认蓝
            self._accent = ACCENT
            self._scale = 1.0
        self._dragging = False
        self._drag_offset = QPoint()
        self._current_name: Optional[str] = None
        self._dirty = False
        self._importing = False       # 技能包导入进行中（防止重复触发）
        self._summarizing = False     # 小模型生成简介进行中（防止重复触发）

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("skill库管理器（skillspub 公共技能库）")
        self.setMinimumSize(920, 640)
        self.resize(1080, 720)

        pub.ensure_dir()
        self._build_ui()
        self._init_resize()
        self._refresh_tree()
        names = [e["name"] for e in pub.list_skills()]
        self._load_skill(names[0] if names else None)

    # ------------------------------------------------------------ 主题同步
    def apply_accent(self, color: str) -> None:
        """主界面主题色变化时同步生效。"""
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

        title = QLabel("skill库管理器（skillspub 公共技能库）")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        sub = QLabel("注册表风格公共技能库 · 索引 + 每技能一个文件夹")
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

    # ------------------------------------------------------------ 左栏（键树）
    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("skill库（skillspub）")
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

        # 需求：从外部技能包（.zip / .rar / .md）批量导入技能到 skill库
        imp_btn = QPushButton("导入 skills（.zip / .rar / .md）")
        imp_btn.setCursor(Qt.PointingHandCursor)
        imp_btn.setObjectName("ghostBtn")
        imp_btn.setToolTip(
            "导入外部技能包：压缩包内为 <技能名>/SKILL.md 结构（Anthropic 风格）；\n"
            "技能名取不含后缀的文件名；没有介绍时自动调用小模型总结（英文技能"
            "输出中英文）。")
        imp_btn.clicked.connect(self._on_import_skills)
        layout.addWidget(imp_btn)
        return panel

    # ------------------------------------------------------------ 右栏（属性面板）
    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("mainPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

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
            """左对齐标题独立一行 + 下方卡片。"""
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

        # 1. 基本属性
        prop_form = _section("基本属性（注册表值）")
        self._name_box = QLineEdit()
        self._name_box.setPlaceholderText("公共技能名称（唯一；AI 将按名称调用）")
        self._category_box = QLineEdit()
        self._category_box.setPlaceholderText("例如：创作 / 办公 / 科研 / 生活 / 工具")
        self._enabled_box = QCheckBox("启用（关闭后不出现在 skillspub 指引中，也不会被调用）")
        prop_form.addRow("名称", self._name_box)
        prop_form.addRow("分类", self._category_box)
        prop_form.addRow("启用", self._enabled_box)

        # 2. skill简介（写入 skillspub 指引）
        summ_form = _section("skill简介（写入 skillspub 指引，供 AI/用户快速挑选）")
        self._summary_box = QLineEdit()
        self._summary_box.setPlaceholderText("一句话说明该技能能做什么（例如：把长资料整理成要点式结论与行动清单）")
        # 需求：简介框旁放两个小按钮，一键调用小模型「总结功能」/「翻译总结」
        self._sum_btn = QPushButton("总结功能")
        self._sum_btn.setCursor(Qt.PointingHandCursor)
        self._sum_btn.setObjectName("ghostBtn")
        self._sum_btn.setToolTip(
            "调用小模型读取下方「详细介绍与执行说明」，整理总结成符合 skill简介"
            "长度的一句话简介，并填入左侧输入框")
        self._sum_btn.clicked.connect(self._on_summarize_skill)
        self._tr_btn = QPushButton("翻译总结")
        self._tr_btn.setCursor(Qt.PointingHandCursor)
        self._tr_btn.setObjectName("ghostBtn")
        self._tr_btn.setToolTip(
            "调用小模型把当前简介（简介为空时读技能说明）总结并翻译为中文一句话，"
            "并填入左侧输入框")
        self._tr_btn.clicked.connect(self._on_translate_summary)
        summ_row = QHBoxLayout()
        summ_row.setSpacing(8)
        summ_row.addWidget(self._summary_box, 1)
        summ_row.addWidget(self._sum_btn)
        summ_row.addWidget(self._tr_btn)
        summ_form.addRow(summ_row)

        # 3. 调用关键词
        kw_form = _section("调用关键词（逗号分隔；用于自动模式的文本匹配降级）")
        self._keywords_box = QLineEdit()
        self._keywords_box.setPlaceholderText("例如：整理,总结,归纳,纪要")
        kw_form.addRow(self._keywords_box)

        # 4. 详细介绍（写入该技能文件夹的 SKILL.md）
        desc_form = _section("详细介绍与执行说明（保存后写入该技能文件夹的 SKILL.md）")
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText(
            "详细介绍该技能：用途、适用场景、执行步骤/要求、输出格式等。\n"
            "保存时这段文字会写入 skillspub/<技能文件夹>/SKILL.md，\n"
            "AI 被允许调用该技能时会把该文件内容原样注入上下文。")
        self._desc_edit.setMinimumHeight(220)
        desc_form.addRow(self._desc_edit)

        # 字段变化标记 dirty
        for widget in (self._name_box, self._category_box, self._summary_box,
                       self._keywords_box):
            widget.textChanged.connect(self._mark_dirty)
        self._enabled_box.toggled.connect(self._mark_dirty)
        self._desc_edit.textChanged.connect(self._mark_dirty)

        # 双击字段 → 注册表式「编辑字符串」对话框
        for box, key in ((self._name_box, "name"), (self._category_box, "category"),
                         (self._summary_box, "summary"), (self._keywords_box, "keywords")):
            box.mouseDoubleClickEvent = lambda e, k=key: self._edit_field_dialog(k, e)

        # 底部提示条
        tip = QLabel(
            "如何调用：发送消息时带上 \\@skillspub（手动自由调用）、\\@autoskills"
            "（AI 自动挑选组合）或在输入框用 #技能名 直接点名调用（可一次多个组合）；"
            "两个开关可在「技能工具管理器」中启停。保存会同时更新 skillspub/catalog.json"
            "（索引）与该技能的文件夹文件（skillspub/<技能文件夹>/SKILL.md），"
            "下一条消息立即生效。")
        tip.setObjectName("hintText")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return panel

    # ------------------------------------------------------------ 树与属性联动
    def _refresh_tree(self, select_name: Optional[str] = None) -> None:
        """重建左侧键树；可选指定选中的技能。"""
        entries = pub.list_skills()
        self._tree.blockSignals(True)
        self._tree.clear()

        root_item = QTreeWidgetItem(["skill库（skillspub）"])
        root_item.setData(0, Qt.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(QStyle.SP_DirHomeIcon))
        root_item.setExpanded(True)
        self._tree.addTopLevelItem(root_item)

        for entry in entries:
            flag = "" if entry.get("enabled", True) else "（停用）"
            item = QTreeWidgetItem([f"{entry['name']}  {flag}"])
            item.setData(0, Qt.UserRole, entry["name"])
            item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
            item.setToolTip(0, entry.get("summary", "") or "（无一句话简介）")
            root_item.addChild(item)

        self._tree.blockSignals(False)
        if select_name:
            for item in self._walk_items(root_item):
                if item.data(0, Qt.UserRole) == select_name:
                    self._tree.setCurrentItem(item)
                    break
        elif entries:
            first = root_item.child(0)
            if first is not None:
                self._tree.setCurrentItem(first)
        else:
            self._tree.setCurrentItem(root_item)
        self._update_count(len(entries))

    @staticmethod
    def _walk_items(item: QTreeWidgetItem):
        stack = [item]
        while stack:
            cur = stack.pop()
            yield cur
            for i in range(cur.childCount()):
                stack.append(cur.child(i))

    def _on_tree_selected(self) -> None:
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
        self._current_name = name
        if name:
            data = pub.read_skill(name) or {}
        else:
            data = {}
        self._name_box.setText(str(data.get("name", "")))
        self._category_box.setText(str(data.get("category", "通用")))
        self._enabled_box.setChecked(bool(data.get("enabled", True)))
        self._summary_box.setText(str(data.get("summary", "")))
        self._keywords_box.setText("，".join(str(k) for k in (data.get("keywords") or [])))
        self._desc_edit.setPlainText(str(data.get("description", "")))
        self._dirty = False
        self._refresh_prop()

    def _refresh_prop(self) -> None:
        name = self._name_box.text().strip()
        if name:
            self._skill_name_label.setText(name)
            self._trigger_tag.setText("skillspub 公共技能")
            folder = (pub.read_skill(name) or {}).get("folder") or ""
            if folder:
                self._status_path.setText(f"skillspub\\{folder}\\{pub.SKILL_FILE}")
            else:
                self._status_path.setText(f"skill库（skillspub）\\{name}")
        else:
            self._skill_name_label.setText("未选择技能")
            self._trigger_tag.setText("")
            self._status_path.setText("skill库（skillspub）")
        self._status_msg.setText("已就绪" if not self._dirty else "有未保存修改")

    def _update_count(self, n: int) -> None:
        self._status_count.setText(f"共 {n} 个公共技能")

    def _mark_dirty(self, *_: Any) -> None:
        if not self._dirty:
            self._dirty = True
            self._refresh_prop()

    # ------------------------------------------------------------ 数据收集与操作
    def _collect_data(self) -> Dict[str, Any]:
        return {
            "name": self._name_box.text().strip(),
            "category": self._category_box.text().strip() or "通用",
            "enabled": self._enabled_box.isChecked(),
            "summary": self._summary_box.text().strip(),
            "keywords": _parse_aliases(self._keywords_box.text()),
            "description": self._desc_edit.toPlainText().strip(),
        }

    def _on_save_skill(self) -> None:
        data = self._collect_data()
        if not data["name"]:
            styled_warning(self, "提示", "请先填写技能名称。")
            return
        ok, msg = pub.save_skill(data, old_name=self._current_name)
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
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "新建公共技能", "请输入技能名称：")
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
        if pub.read_skill(name):
            styled_warning(self, "提示", f"已存在同名技能「{name}」，请更换名称。")
            return
        self._current_name = None          # 尚未落盘 → 保存时按新建处理
        self._name_box.setText(name)
        self._category_box.setText("通用")
        self._enabled_box.setChecked(True)
        self._summary_box.setText("")
        self._keywords_box.setText("")
        self._desc_edit.setPlainText(
            f"【技能用途】「{name}」能做什么、适用于哪些场景。\n"
            "【执行要求】\n1. ……\n2. ……\n【输出格式】……")
        self._dirty = True
        self._refresh_prop()
        self._status_msg.setText(f"正在新建公共技能「{name}」，点击「保存修改」创建。")

    def _on_delete_skill(self) -> None:
        if not self._current_name:
            styled_info(self, "提示", "请先在左侧选择一个公共技能。")
            return
        name = self._current_name
        folder = (pub.read_skill(name) or {}).get("folder") or ""
        msg = (f"确定删除公共技能「{name}」吗？\n\n"
               "将从 skillspub/catalog.json（skill库索引）中移除，")
        if folder:
            msg += f"并删除其技能文件夹 skillspub/{folder}/（含 SKILL.md 等技能文件）。"
        msg += "\n此操作不可撤销。"
        if not styled_question(self, "确认删除", msg, danger=True):
            return
        ok, msg = pub.delete_skill(name)
        if not ok:
            styled_warning(self, "删除失败", msg)
            return
        self._current_name = None
        self._dirty = False
        self._refresh_tree()
        self._load_skill(None)
        self._status_msg.setText(msg)

    # ------------------------------------------------- skill简介：小模型总结 / 翻译
    def _on_summarize_skill(self) -> None:
        """「总结功能」：调用小模型把技能说明整理成符合简介长度的一句话简介。"""
        self._run_summary("summary")

    def _on_translate_summary(self) -> None:
        """「翻译总结」：调用小模型把简介（无简介时用技能说明）总结并翻译为中文。"""
        self._run_summary("translate")

    def _run_summary(self, mode: str) -> None:
        """启动小模型生成简介（后台线程，避免卡住窗口）。"""
        if self._summarizing:
            return
        name = self._name_box.text().strip() or "未命名技能"
        source = ""
        if mode == "translate":
            # 翻译总结优先基于当前简介；简介为空时退回技能说明全文
            source = self._summary_box.text().strip()
        if not source:
            source = self._desc_edit.toPlainText().strip()
        if not source:
            styled_warning(
                self, "没有可总结的内容",
                "请先选中一个技能、或在「详细介绍与执行说明」里写上技能说明，"
                "再点击「总结功能」/「翻译总结」。")
            return
        self._summarizing = True
        self._set_summary_buttons(False)
        self._status_msg.setText("正在调用小模型总结简介…"
                                 if mode == "summary" else
                                 "正在调用小模型翻译并总结简介…")
        spawn_worker(self._summary_task, name, source, mode,
                     on_done=lambda text, m=mode: self._on_summary_done(text, m),
                     on_fail=self._on_summary_failed)

    @staticmethod
    def _summary_task(name: str, source: str, mode: str) -> str:
        """子线程：调用小模型生成 / 翻译一句话简介。"""
        from llm_client import LLMClientPool
        pool = LLMClientPool.instance()
        src = (source or "").strip()
        if not src:
            return ""
        limit = getattr(pub, "SUMMARY_LIMIT", 200)
        if mode == "translate":
            ask = (f"下面是一段 AI 技能简介或技能说明（可能是英文、中英混排）。"
                   "请把它总结并翻译为【中文】的一句话简介，"
                   f"不超过 {min(60, limit)} 字，要点明这个技能能做什么、适用什么场景。\n"
                   "只输出这一句中文，不要任何前缀、引号、编号或额外说明。")
        else:
            ask = ("下面是一个 AI 技能（skill）的说明文档。请整理总结为"
                   f"不超过 {min(60, limit)} 字的一句话简介，"
                   "要点明它能做什么、适用于什么场景。\n"
                   "若原文是英文：输出两行——第一行以“中文：”开头给出中文简介，"
                   "第二行以“English:”开头给出对应英文简介，只输出这两行。\n"
                   "若原文是中文：只输出这一句中文，不要任何前缀、引号或说明。")
        out = pool.chat_complete(
            [{"role": "system", "content": ask},
             {"role": "user", "content": f"技能名：{name}\n\n{src[:4000]}"}],
            kind="small", max_tokens=160, timeout=25.0)
        text = re.sub(r"\s*\n\s*", " | ", (out or "").strip()).strip(" |")
        return (text or "")[:limit]

    def _on_summary_done(self, text: str, mode: str) -> None:
        """主线程：把小模型结果填入简介框（并标记为待保存）。"""
        self._summarizing = False
        self._set_summary_buttons(True)
        out = (text or "").strip()
        if not out:
            self._status_msg.setText("小模型未返回内容，简介未改动")
            styled_warning(self, "未生成简介",
                           "小模型没有返回内容，请确认已配置小模型后重试，"
                           "或手动填写简介。")
            return
        out = out[:getattr(pub, "SUMMARY_LIMIT", 200)]
        self._summary_box.setText(out)
        self._mark_dirty()
        self._status_msg.setText(
            f"已生成 skill简介（{len(out)} 字，记得保存）" if mode == "summary"
            else f"已翻译并总结为中文简介（{len(out)} 字，记得保存）")

    def _on_summary_failed(self, msg: str) -> None:
        """简介生成失败：恢复按钮并提示。"""
        self._summarizing = False
        self._set_summary_buttons(True)
        self._status_msg.setText("生成简介失败")
        styled_warning(self, "生成简介失败",
                       f"{msg}\n\n请检查小模型配置（设置-模型里的小模型）后重试。")

    def _set_summary_buttons(self, enabled: bool) -> None:
        """生成过程中禁用两个简介按钮，避免重复调用小模型。"""
        for btn in (getattr(self, "_sum_btn", None), getattr(self, "_tr_btn", None)):
            if btn is not None:
                btn.setEnabled(bool(enabled))

    # ------------------------------------------------------------ 导入技能包
    def _on_import_skills(self) -> None:
        """导入外部技能包（.zip / .rar / .md）到 skill库（skillspub）。

        需求：
        - 技能名 = 不含后缀的文件名，并写入 catalog.json 索引；
        - 没有介绍（frontmatter description）时调用小模型总结，英文技能输出中英双语；
        - 技能可能需要创建文件时询问用户是否允许；允许后执行该技能时生成的文件
          写入 skilluserdata 下「日期+时间」命名的项目文件夹（一个对话一个）。
        """
        if self._importing:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入技能包（可多选）", "",
            "技能包 (*.zip *.rar *.md);;压缩包 (*.zip *.rar);;"
            "Markdown 技能 (*.md);;所有文件 (*)")
        if not paths:
            return
        self._importing = True
        self._status_msg.setText(f"正在解析 {len(paths)} 个技能包…")
        spawn_worker(self._import_preview_task, list(paths),
                     on_done=self._on_import_previewed,
                     on_fail=self._on_import_failed)

    @staticmethod
    def _import_preview_task(paths: List[str]) -> List[Dict[str, Any]]:
        """子线程：解析技能包（解压 + frontmatter + 是否需要创建文件）。"""
        imp = SkillImporter.instance()
        return [imp.preview(Path(p)) for p in paths]

    def _on_import_previewed(self, items: Any) -> None:
        """主线程：汇总预览 → 询问是否允许生成文件 → 启动导入。"""
        items = list(items or [])
        ready = [i for i in items if i.get("ok")]
        bad = [i for i in items if not i.get("ok")]
        if bad:
            detail = "\n".join(
                f"· {Path(str(i.get('path'))).name}：{i.get('msg') or '无法解析'}"
                for i in bad[:8])
            styled_warning(self, "部分技能包无法导入",
                           f"以下文件未导入：\n{detail}")
        if not ready:
            self._importing = False
            self._status_msg.setText("导入结束：没有可导入的技能包")
            return
        need = [i for i in ready if i.get("needs_files")]
        allow = True
        if need:
            names = "、".join(str(i.get("name") or "") for i in need[:8])
            if len(need) > 8:
                names += f" 等 {len(need)} 个"
            allow = styled_question(
                self, "是否允许生成文件？",
                "以下技能可能需要创建/写入文件：\n"
                f"{names}\n\n"
                "选择「是」：允许它们生成文件，执行时写入 skilluserdata 下"
                "「日期+时间」命名的项目文件夹（一个对话一个，可用指令 "
                "\\@project 新名字 更改项目名）。\n"
                "选择「否」：只导入技能说明，不建立产出文件夹。")
        self._status_msg.setText(f"正在导入 {len(ready)} 个技能…")
        spawn_worker(self._import_task, [Path(str(i["path"])) for i in ready],
                     bool(allow),
                     on_done=self._on_import_done,
                     on_fail=self._on_import_failed)

    @staticmethod
    def _import_task(paths: List[Path], allow_files: bool) -> Dict[str, Any]:
        """子线程：真正导入（复制技能文件 + 生成摘要 + 写索引）。"""
        return SkillImporter.instance().import_paths(
            list(paths), allow_files=bool(allow_files), pool=None)

    def _on_import_done(self, result: Any) -> None:
        """主线程：导入完成 → 刷新树并选中第一个新技能。"""
        self._importing = False
        data = dict(result or {})
        imported = list(data.get("imported") or [])
        failed = list(data.get("failed") or [])
        if not imported:
            self._dirty = False
            self._refresh_tree()
            self._status_msg.setText("导入失败：没有技能被写入")
            detail = "\n".join(
                f"· {Path(str(f.get('path'))).name}：{f.get('msg')}" for f in failed[:8])
            styled_warning(self, "导入失败", detail or "技能包内容无法识别。")
            return
        first = str(imported[0].get("name") or "")
        self._dirty = False
        self._refresh_tree(select_name=first)
        self._load_skill(first)
        lines = [f"已导入 {len(imported)} 个技能："]
        for it in imported[:12]:
            flag = "允许生成文件" if it.get("allow_files") else "仅技能说明"
            lines.append(f"· {it.get('name')}（{flag}）")
        if failed:
            lines.append("")
            lines.append("以下未导入：")
            lines.extend(f"· {Path(str(f.get('path'))).name}：{f.get('msg')}"
                         for f in failed[:8])
        styled_info(self, "导入完成", "\n".join(lines))
        self._status_msg.setText(f"导入完成：成功 {len(imported)} 个"
                                 + (f"，失败 {len(failed)} 个" if failed else ""))

    def _on_import_failed(self, msg: str) -> None:
        """导入线程异常：恢复状态并提示。"""
        self._importing = False
        self._status_msg.setText("导入失败")
        styled_warning(self, "导入失败", str(msg))

    def _confirm_discard(self) -> bool:
        """有未保存修改时询问：保存 / 放弃 / 取消。"""
        if not self._dirty:
            return True
        box = styled_box(
            self, "未保存的修改",
            "当前公共技能有未保存的修改，是否保存？",
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
                ok, msg = pub.save_skill(data, old_name=self._current_name)
                self._status_msg.setText(msg)
                if not ok:
                    styled_warning(self, "保存失败", msg)
                    return False
        return True

    # ------------------------------------------------------------ 注册表式字段编辑
    _FIELD_LABELS = {
        "name": ("名称", "公共技能名称（唯一；AI 按名称调用）"),
        "category": ("分类", "例如：创作 / 办公 / 科研 / 生活 / 工具"),
        "summary": ("skill简介", "一句话说明该技能能做什么"),
        "keywords": ("调用关键词", "逗号分隔，例如：整理,总结,归纳,纪要"),
        "description": ("详细介绍与执行说明", "保存后写入该技能文件夹的 SKILL.md，被调用时注入 LLM"),
    }

    def _edit_field_dialog(self, key: str, _event: Any = None) -> None:
        """仿 regedit「编辑字符串」：双击字段弹窗编辑。"""
        if key not in self._FIELD_LABELS:
            return
        label, placeholder = self._FIELD_LABELS[key]
        current = {
            "name": self._name_box.text(),
            "category": self._category_box.text(),
            "summary": self._summary_box.text(),
            "keywords": self._keywords_box.text(),
            "description": self._desc_edit.toPlainText(),
        }.get(key, "")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑 {label}")
        dlg.setStyleSheet(root_qss(self._accent, self._scale))
        dlg.resize(600, 340 if key == "description" else 200)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        hint = QLabel(f"值名称：{label}")
        hint.setObjectName("sectionTitle")
        lay.addWidget(hint)

        if key == "description":
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
            value = (editor.toPlainText() if key == "description" else editor.text())
            if key == "name":
                self._name_box.setText(value)
            elif key == "category":
                self._category_box.setText(value)
            elif key == "summary":
                self._summary_box.setText(value)
            elif key == "keywords":
                self._keywords_box.setText(value)
            elif key == "description":
                self._desc_edit.setPlainText(value)
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    # ------------------------------------------------------------ 窗口事件
    def closeEvent(self, event: Any) -> None:  # noqa: D102
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ 无边框窗口缩放
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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SkillspubManager")
    app.setApplicationDisplayName("skill库管理器（skillspub 公共技能库）")
    window = SkillPubManagerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
