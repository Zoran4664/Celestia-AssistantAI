"""
roleplaytool.py — 角色扮演设定卡管理工具（roleplay 子项目）

目录结构：
    roleplay/
    ├── roleplaytool.py          # 本文件（核心类 + 独立管理窗口）
    ├── rolemanger/
    │   ├── style/               # 对话风格卡
    │   └── world/               # 世界观设定卡
    ├── rolepersonal/            # 个人设定卡（用户角色补充、人物关系补充等）
    └── roletools/               # 角色限制、破限、功能设置卡

功能：
    - 列出各目录下的 JSON 设定卡（不带后缀）
    - 新建 JSON 卡片（可选择目录）
    - 以文本形式查看 / 修订 JSON 内容（保存前校验 JSON 合法性）
    - 删除卡片
    - RolePlayManager 核心类：供主程序（ui_manager/ChatWorker）导入，
      用于「列表 / 加载 / 保存 / 删除 / 文本转换 / build_context 拼接」。
    - build_context：把勾选保存的卡片（格式 "类别/卡片名"）拼接为可注入
      对话系统上下文的设定文本，只加载存在且可解析的卡片。

用法：
    python roleplay/roleplaytool.py              # 打开图形管理窗口
    python roleplay/roleplaytool.py --list       # 命令行列出全部卡片
    python roleplay/roleplaytool.py --show world/小马国   # 查看单张卡片文本
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 路径与类别映射
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent          # roleplay/ 目录
PROJECT_ROOT = ROOT.parent                      # 主项目目录

# 类别（配置存储键）→ 显示标签
CATEGORY_LABELS: Dict[str, str] = {
    "style": "对话风格",
    "world": "世界观",
    "rolepersonal": "个人设定",
    "roletools": "角色工具",
}

# 卡片在配置中的存储格式："类别/卡片名"，例如 "world/小马国"
_CARD_KEY_SEP = "/"

# 新建卡片模板（可编辑）
_NEW_CARD_TEMPLATE: Dict[str, Any] = {
    "project_name": "",
    "version": "1.0",
    "category": "",
    "description": "",
    "rules": {},
    "few_shot_example": [],
}


class RolePlayManager:
    """角色扮演设定卡管理（可独立于 GUI 使用）。

    通过 root 参数可指向临时目录（测试隔离），默认指向本子项目 roleplay/。
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root).resolve() if root is not None else ROOT

    # ---------------------------------------------------------- 目录
    def category_dir(self, category: str) -> Path:
        """类别 → 目录 Path（不存在则自动创建）。"""
        cat = (category or "").strip().lower()
        if cat == "style":
            d = self._root / "rolemanger" / "style"
        elif cat == "world":
            d = self._root / "rolemanger" / "world"
        elif cat in ("rolepersonal", "roletools"):
            d = self._root / cat
        else:
            # 兼容绝对路径 / 自定义子目录（如 "mydir/sub"）
            raw = Path(category or "").expanduser()
            d = raw if raw.is_absolute() else (self._root / cat)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def categories(self) -> List[str]:
        """返回支持的四个标准类别（顺序：风格/世界观/个人设定/工具）。"""
        return ["style", "world", "rolepersonal", "roletools"]

    def card_path(self, category: str, name: str) -> Path:
        """卡片完整路径。"""
        return self.category_dir(category) / f"{(name or '').strip()}.json"

    # ---------------------------------------------------------- 列表
    def list_cards(self, category: str) -> List[str]:
        """列出某目录下全部 .json 设定卡名（不带后缀，按文件名排序）。"""
        d = self.category_dir(category)
        if not d.is_dir():
            return []
        return sorted(
            p.stem for p in d.glob("*.json")
            if p.is_file() and p.stem.strip()
        )

    def list_all_cards(self) -> Dict[str, List[str]]:
        """按类别分组返回全部设定卡。"""
        return {cat: self.list_cards(cat) for cat in self.categories()}

    # ---------------------------------------------------------- 读写
    def load_card(self, category: str, name: str) -> dict:
        """读取设定卡 JSON；不存在或解析失败返回空 dict。"""
        path = self.card_path(category, name)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def save_card(self, category: str, name: str, data: dict) -> Path:
        """保存 / 新建设定卡（UTF-8，缩进 2）。非法参数抛 ValueError。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("卡片名不能为空")
        if not isinstance(data, dict):
            raise ValueError("卡片内容必须是 JSON 对象（{...}）")
        path = self.card_path(category, name)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def delete_card(self, category: str, name: str) -> bool:
        """删除卡片；存在则删除并返回 True，否则 False。"""
        path = self.card_path(category, name)
        if path.exists():
            path.unlink(missing_ok=True)
            return True
        return False

    def rename_card(self, category: str, old: str, new: str) -> bool:
        """重命名卡片（防止误建旧文件）。"""
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new or old == new:
            return False
        src = self.card_path(category, old)
        dst = self.card_path(category, new)
        if not src.exists() or dst.exists():
            return False
        src.rename(dst)
        return True

    # ---------------------------------------------------------- 文本转换
    @staticmethod
    def card_to_text(data: dict) -> str:
        """dict → JSON 文本（UTF-8 中文直出，缩进 2）。"""
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def text_to_card(text: str) -> dict:
        """JSON 文本 → dict；空内容抛 ValueError，非法 JSON 抛 JSONDecodeError。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空，无法保存空卡片")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("卡片根节点必须是 JSON 对象（{...}）")
        return data

    # ---------------------------------------------------------- 拼接
    def build_context(self, card_keys: List[str]) -> str:
        """把选中的卡片（格式 "类别/卡片名"）拼接为可注入的系统上下文文本。

        只加载真实存在且可解析的卡片；缺失 / 损坏 / 非法 key 自动跳过。
        顺序保持传入顺序（建议按 世界观 → 个人设定 → 风格 → 工具）。
        """
        if not card_keys:
            return ""
        sections: List[str] = []
        for key in card_keys or []:
            key = (key or "").strip().strip(_CARD_KEY_SEP)
            if not key or _CARD_KEY_SEP not in key:
                continue
            category, _, name = key.partition(_CARD_KEY_SEP)
            category = (category or "").strip()
            name = (name or "").strip()
            if not category or not name:
                continue
            data = self.load_card(category, name)
            if not data:
                continue
            label = CATEGORY_LABELS.get(category, category)
            title = str(
                data.get("project_name")
                or data.get("name")
                or data.get("display_name")
                or name
            )
            body = self.card_to_text(data)
            sections.append(f"——【{label}卡：{title}】——\n{body}")
        return "\n\n".join(sections)

    def new_card_template(self, name: str = "") -> dict:
        """返回一份可编辑的新卡片模板。"""
        tpl = json.loads(json.dumps(_NEW_CARD_TEMPLATE))
        tpl["project_name"] = (name or "").strip()
        return tpl

    # ---------------------------------------------------------- key 解析
    @staticmethod
    def split_key(key: str) -> Tuple[str, str]:
        """"类别/卡片名" → (类别, 卡片名)。"""
        key = (key or "").strip().strip(_CARD_KEY_SEP)
        if not key or _CARD_KEY_SEP not in key:
            return "", ""
        category, _, name = key.partition(_CARD_KEY_SEP)
        return (category or "").strip(), (name or "").strip()

    @staticmethod
    def join_key(category: str, name: str) -> str:
        return f"{category}{_CARD_KEY_SEP}{name}".strip(_CARD_KEY_SEP)


# ---------------------------------------------------------------------------
# 命令行入口（无 GUI 时也可查看 / 管理卡片）
# ---------------------------------------------------------------------------
def _cmd_list() -> int:
    mgr = RolePlayManager()
    all_cards = mgr.list_all_cards()
    for cat in mgr.categories():
        names = all_cards.get(cat) or []
        label = CATEGORY_LABELS.get(cat, cat)
        print(f"[{label} {cat}] ({len(names)})")
        for n in names:
            print(f"    {n}")
    return 0


def _cmd_show(key: str) -> int:
    category, name = RolePlayManager.split_key(key)
    if not category or not name:
        print(f"无效的卡片 key：{key!r}（应为 类别/卡片名，如 world/小马国）")
        return 1
    mgr = RolePlayManager()
    data = mgr.load_card(category, name)
    if not data:
        print(f"未找到卡片：{key}")
        return 1
    print(mgr.card_to_text(data))
    return 0


def _cmd_delete(key: str) -> int:
    category, name = RolePlayManager.split_key(key)
    if not category or not name:
        print(f"无效的卡片 key：{key!r}")
        return 1
    mgr = RolePlayManager()
    ok = mgr.delete_card(category, name)
    print(f"已删除 {key}" if ok else f"未找到卡片：{key}")
    return 0 if ok else 1


def run_cli(argv: Optional[List[str]] = None) -> int:
    """命令行模式：--list / --show <key> / --delete <key>。"""
    parser = argparse.ArgumentParser(
        prog="roleplaytool", description="角色扮演设定卡管理（命令行）")
    parser.add_argument("--list", action="store_true", help="列出全部设定卡")
    parser.add_argument("--show", metavar="KEY", help="查看单张卡片，如 world/小马国")
    parser.add_argument("--delete", metavar="KEY", help="删除单张卡片")
    args = parser.parse_args(argv)
    if args.list:
        return _cmd_list()
    if args.show:
        return _cmd_show(args.show)
    if args.delete:
        return _cmd_delete(args.delete)
    parser.print_help()
    return 1



# ---------------------------------------------------------------------------
# 图形管理窗口（PySide6，可独立运行，也可被主程序内嵌打开）
# ---------------------------------------------------------------------------
try:  # PySide6 缺失时仍可命令行使用
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog,
        QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
        QVBoxLayout, QWidget,
    )
    _HAS_GUI = True
except ImportError:  # pragma: no cover
    _HAS_GUI = False


if _HAS_GUI:  # pragma: no cover - 需 GUI 环境

    class RolePlayToolWindow(QWidget):
        """角色扮演设定卡管理窗口：左侧选目录/卡片，右侧文本编辑 JSON。"""

        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self._mgr = RolePlayManager()
            self._current_category = "style"
            self._current_name: Optional[str] = None
            self._dirty = False
            self.setWindowTitle("角色扮演设定卡管理器")
            self.setMinimumSize(880, 560)
            self.resize(960, 640)
            self._build_ui()
            self._refresh_cards()
            self._text.textChanged.connect(self._mark_dirty)

        # ------------------------------------------------------ UI 构建
        def _build_ui(self) -> None:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(12, 12, 12, 12)
            outer.setSpacing(8)

            split = QHBoxLayout()
            split.setSpacing(10)

            left = QFrame()
            left.setObjectName("rpLeft")
            ll = QVBoxLayout(left)
            ll.setContentsMargins(10, 10, 10, 10)
            ll.setSpacing(6)
            self._cat_combo = QComboBox()
            for cat in self._mgr.categories():
                label = CATEGORY_LABELS.get(cat, cat)
                self._cat_combo.addItem(f"{label}（{cat}）", cat)
            self._cat_combo.currentIndexChanged.connect(
                self._on_category_changed)
            ll.addWidget(self._cat_combo)
            self._card_list = QListWidget()
            self._card_list.itemSelectionChanged.connect(
                self._on_selection_changed)
            ll.addWidget(self._card_list, 1)
            refresh_btn = QPushButton("刷新")
            refresh_btn.clicked.connect(self._refresh_cards)
            ll.addWidget(refresh_btn)
            split.addWidget(left, 2)

            right = QFrame()
            right.setObjectName("rpRight")
            rl = QVBoxLayout(right)
            rl.setContentsMargins(10, 10, 10, 10)
            rl.setSpacing(6)
            self._text = QPlainTextEdit()
            self._text.setPlaceholderText(
                "在此以 JSON 文本形式查看 / 修订设定卡内容……")
            self._text.setFont(QFont("Consolas", 10))
            rl.addWidget(self._text, 1)
            btns = QHBoxLayout()
            btns.setSpacing(8)
            # 需求：按钮统一样式名（QSS 只作用于 #rpBtn，避免全局 QPushButton
            # 选择器把主程序/子窗口的按钮样式一起改掉，造成显示异常）
            new_btn = QPushButton("新建卡片")
            new_btn.setObjectName("rpBtn")
            new_btn.clicked.connect(self._on_new)
            save_btn = QPushButton("保存卡片")
            save_btn.setObjectName("rpBtn")
            save_btn.clicked.connect(self._on_save)
            del_btn = QPushButton("删除卡片")
            del_btn.setObjectName("rpBtn")
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.clicked.connect(self._on_delete)
            btns.addWidget(new_btn)
            btns.addWidget(save_btn)
            btns.addWidget(del_btn)
            btns.addStretch(1)
            rl.addLayout(btns)
            split.addWidget(right, 5)

            outer.addLayout(split, 1)
            self._status = QLabel("就绪")
            self._status.setObjectName("rpStatus")
            outer.addWidget(self._status)
            self._apply_qss()

        # ------------------------------------------------------ 样式 / 状态
        def _apply_qss(self, accent: str = "#6c8ef5",
                       dark: bool = False) -> None:
            """应用样式。

            需求：修复「显示异常」——原先硬编码白底 + 全局 QPushButton 选择器，
            在深色主题下文字/按钮看不清，且样式会污染到窗口内其它控件。
            现在：面板底色与文字色随明暗主题切换，按钮样式只作用于 #rpBtn。
            """
            accent = accent or "#6c8ef5"
            if dark:
                panel_bg = "rgba(28,30,38,0.92)"
                panel_bd = "#3a4050"
                text = "#dfe3ee"
                input_bg = "rgba(255,255,255,0.06)"
                item_sel_bg = "rgba(108,142,245,0.28)"
            else:
                panel_bg = "rgba(255,255,255,0.86)"
                panel_bd = "#dde3f0"
                text = "#2b3245"
                input_bg = "#ffffff"
                item_sel_bg = "#dfe7ff"
            self.setStyleSheet(
                f"QWidget{{color:{text};}}"
                f"QFrame#rpLeft, QFrame#rpRight {{"
                f" background: {panel_bg};"
                f" border: 1px solid {panel_bd}; border-radius: 10px; }}"
                f"QLabel#rpStatus {{ color: {text}; padding: 2px 4px; }}"
                f"QPlainTextEdit, QListWidget, QComboBox {{"
                f" background: {input_bg}; color: {text};"
                f" border: 1px solid {panel_bd}; border-radius: 8px;"
                f" padding: 4px 6px; }}"
                f"QPushButton#rpBtn {{ padding: 6px 16px; border: none;"
                f" border-radius: 8px; background: {accent}; color: white; }}"
                f"QPushButton#rpBtn:hover {{ background: #7d9cff; }}"
                f"QListWidget::item:selected {{ background: {item_sel_bg};"
                f" color: {text}; }}"
            )

        def apply_accent(self, accent: str, dark: bool = False) -> None:
            """主程序调用：主题色实时同步（dark=深色主题）。"""
            self._apply_qss(accent or "#6c8ef5", dark=dark)

        def _category(self) -> str:
            return str(self._cat_combo.currentData() or "style")

        def _mark_dirty(self) -> None:
            self._dirty = True
            if self._current_name:
                self._status.setText(f"未保存的修改：{self._current_name}")

        def _confirm_discard(self) -> bool:
            """存在未保存修改时询问；返回是否允许离开当前卡片。"""
            if not self._dirty:
                return True
            ret = QMessageBox.question(
                self, "未保存修改",
                f"「{self._current_name}」有未保存的修改，是否保存？\n"
                "选择 保存 将先写入再切换；选择 不保存 将丢弃修改。",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return False
            if ret == QMessageBox.Save:
                return self._on_save()
            return True

        # ------------------------------------------------------ 列表事件
        def _refresh_cards(self) -> None:
            cat = self._category()
            prev = self._current_name
            names = self._mgr.list_cards(cat)
            self._card_list.blockSignals(True)
            self._card_list.clear()
            for name in names:
                self._card_list.addItem(name)
            self._card_list.blockSignals(False)
            self._current_name = None
            self._dirty = False
            if prev and prev in names:
                items = self._card_list.findItems(prev, Qt.MatchExactly)
                if items:
                    self._card_list.setCurrentItem(items[0])
                    self._on_selection_changed()
                    return
            self._text.setPlainText("")
            self._status.setText(
                f"目录：{cat}（{self._card_list.count()} 张卡片）")

        def _on_category_changed(self, _index: int = 0) -> None:
            if self._confirm_discard():
                self._refresh_cards()

        def _on_selection_changed(self) -> None:
            items = self._card_list.selectedItems()
            if not items:
                return
            name = items[0].text()
            self._current_name = name
            data = self._mgr.load_card(self._category(), name)
            self._text.setPlainText(
                self._mgr.card_to_text(data) if data else "")
            self._dirty = False
            self._status.setText(f"正在编辑：{name}")

        # ------------------------------------------------------ 新建 / 保存 / 删除
        def _on_new(self) -> None:
            cat = self._category()
            name, ok = QInputDialog.getText(
                self, "新建卡片",
                f"输入新卡片名（将保存到目录 {cat}）：")
            name = (name or "").strip()
            if not ok or not name:
                return
            if not self._mgr.load_card(cat, name):
                self._mgr.save_card(cat, name, self._mgr.new_card_template(name))
            self._current_name = name
            self._refresh_cards()
            items = self._card_list.findItems(name, Qt.MatchExactly)
            if items:
                self._card_list.setCurrentItem(items[0])
                self._on_selection_changed()
            self._status.setText(f"已新建：{cat}/{name}（可在右侧填写内容后保存）")

        def _on_save(self) -> bool:
            """把右侧文本保存为当前卡片的 JSON 内容；成功返回 True。"""
            try:
                data = self._mgr.text_to_card(self._text.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return False
            except json.JSONDecodeError as exc:
                QMessageBox.warning(
                    self, "保存失败",
                    f"JSON 格式错误：{exc}\n请修正后重试。")
                return False
            name = self._current_name
            if not name:
                name, ok = QInputDialog.getText(
                    self, "保存卡片",
                    "当前未选择卡片，请为新卡片输入名称：")
                name = (name or "").strip()
                if not ok or not name:
                    return False
                self._current_name = name
            try:
                self._mgr.save_card(self._category(), name, data)
            except ValueError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return False
            self._dirty = False
            self._status.setText(f"已保存：{self._category()}/{name}")
            self._refresh_cards()
            items = self._card_list.findItems(name, Qt.MatchExactly)
            if items:
                self._card_list.setCurrentItem(items[0])
                self._on_selection_changed()
            return True

        def _on_delete(self) -> None:
            name = self._current_name
            if not name:
                return
            ret = QMessageBox.question(
                self, "删除卡片",
                f"确认删除「{name}」？\n（{self._category()}/{name}.json 将被删除，不可恢复）",
                QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
            self._mgr.delete_card(self._category(), name)
            self._current_name = None
            self._dirty = False
            self._refresh_cards()
            self._status.setText(f"已删除：{name}")


def main(argv: Optional[List[str]] = None) -> int:
    """入口：无参数打开图形管理窗口；带参数走命令行。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("--gui",):
        return run_cli(argv)
    if not _HAS_GUI:  # pragma: no cover
        print("未安装 PySide6，无法打开图形窗口；可使用 --list / --show / --delete")
        return 1
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("RolePlayTool")
    win = RolePlayToolWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())




