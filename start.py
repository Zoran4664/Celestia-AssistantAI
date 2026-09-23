"""
start.py — 启动器（位于主目录：主文件与主应用/）

GUI 操作：
  1. [检测库版本]   读取 requirements.txt 逐项对比已安装版本，达标/未达标一目了然
  2. [升级未达标库] 逐个安装未达标库（失败互不影响），完成后输出成功/失败汇总并刷新达标状态
  3. [直接启动]     跳过检测，直接运行 main.py（可透传 --no-pet/--role 等参数）

命令行：
    python start.py              # 打开启动器 GUI
    python start.py --direct     # 直接启动
    python start.py --setup      # 先检测并升级未达标库，再启动

说明：start.py 与 set.md 均位于本目录（主文件与主应用/）。
     完整依赖清单内置于 start.py（_FALLBACK_DEPS），requirements.txt
     缺失时自动兜底检测；新增库时需同步更新 requirements.txt 与 _FALLBACK_DEPS。
"""

from __future__ import annotations

import importlib.metadata as _md
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ = ROOT / "requirements.txt"
MAIN = ROOT / "main.py"

from utils.utf8 import force_utf8_stdio, utf8_env  # noqa: E402

_SPEC_RE = re.compile(
    r"^([A-Za-z0-9_\-\.]+)\s*(==|>=|<=|~=|>|<)\s*([0-9][0-9A-Za-z\.\+\-]*)")
# 完整依赖清单（requirements.txt 的镜像副本，二者需同步更新）。
# 采用「最低版本」范围：pip 会按当前 Python 版本（3.10~3.13）自动挑兼容版。
# 当 requirements.txt 缺失/为空时，start.py 使用此内置清单兜底检测。
_FALLBACK_DEPS = [
    ("PySide6", ">=", "6.6"),
    ("chromadb", ">=", "0.5"),
    ("numpy", ">=", "1.26"),
    ("openai", ">=", "1.30"),
    ("httpx", ">=", "0.27"),
    ("Pillow", ">=", "10.3"),
    ("python-dateutil", ">=", "2.9"),
    ("pydantic", ">=", "2.7"),
    # 多模态文档扩展（document_describe：PDF/Word/Excel/PPT）
    ("pypdf", ">=", "4"),
    ("PyPDF2", ">=", "3"),
    ("python-docx", ">=", "1.1"),
    ("openpyxl", ">=", "3.1"),
    ("python-pptx", ">=", "0.6"),
    # RAR 解压（技能包 .rar 导入；缺失时自动回退 7z/WinRAR/tar）
    ("rarfile", ">=", "4"),
    # 扫描件 PDF 的视觉识别（PDF 页栅格化 → 视觉模型 OCR；可选，缺失时该能力关闭）
    ("pypdfium2", ">=", "4"),
    # 同上，更快的栅格化后端（PyMuPDF；许可为 AGPL-3.0 或商业双授权，按需使用）
    ("pymupdf", ">=", "1.24"),
    # 注：上述已覆盖项目全部第三方库（PySide6/chromadb/numpy/openai/httpx/
    # Pillow/python-dateutil/pydantic + 文档解析五件套 + rarfile +
    # PDF 栅格化两件套 pypdfium2/pymupdf）。
    # tkinter（启动器 GUI）为 Python 标准库，Windows 自带，无需 pip 安装。
    # 核对方法：项目内第三方 import 全集 → 与本清单逐项对照（见 CodeGuide 10 §0）。
]



# ------------------------------------------------------------------ 版本工具
def _num(s: str) -> int:
    m = re.match(r"(\d+)", s or "")
    return int(m.group(1)) if m else 0


def version_tuple(v: str) -> tuple:
    """'1.30.2' → (1,30,2)；去除 build/epoch 后缀。"""
    v = (v or "").split("+")[0].split("-")[0].strip()
    return tuple(_num(p) for p in re.split(r"[.~_]", v))


def satisfies(installed: str, op: str, req: str) -> bool:
    iv, rv = version_tuple(installed), version_tuple(req)
    if op == "==":          # 版本达标：已安装 ≥ 要求（>= 语义）
        return iv >= rv
    if op == ">=":
        return iv >= rv
    if op == "<=":
        return iv <= rv
    if op == ">":
        return iv > rv
    if op == "<":
        return iv < rv
    if op == "~=":          # 简化为 >=
        return iv >= rv
    return True


def parse_requirements() -> list:
    """解析 requirements.txt；缺失或无法解析时回退到内置 _FALLBACK_DEPS。"""
    deps = []
    if REQ.exists():
        try:
            for line in REQ.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                m = _SPEC_RE.match(line)
                if m:
                    deps.append((m.group(1), m.group(2), m.group(3)))
        except OSError:
            pass
    if deps:
        return deps
    # 兜底：requirements.txt 缺失/为空时，使用 start.py 内置完整清单
    return list(_FALLBACK_DEPS)


def installed_version(name: str) -> str:
    try:
        return _md.version(name)
    except Exception:
        return ""


def check_deps() -> list:
    """返回 [(库名, 目标, 已安装, 是否达标)]"""
    result = []
    for name, op, req in parse_requirements():
        have = installed_version(name)
        ok = bool(have) and satisfies(have, op, req)
        result.append((name, f"{op}{req}", have or "未安装", ok))
    return result


def upgrade_deps(callback, on_finished=None) -> None:
    """后台逐个升级未达标库（pip install 单库，失败互不影响）。

    callback(line)   实时输出一行日志
    on_finished()    全部完成后回调（用于刷新达标/未达标状态）
    结束时输出汇总：安装成功 / 安装失败 的完整清单。
    """

    def _run() -> None:
        specs = parse_requirements()  # [(name, op, req)]
        pending = []
        for name, op, req in specs:
            have = installed_version(name)
            if have and satisfies(have, op, req):
                continue  # 已达标，跳过
            pending.append((name, op, req))
        if not pending:
            callback("[汇总] 所有库均已达标，无需升级。")
            callback("[完成]")
            if on_finished:
                on_finished()
            return

        callback(f"[汇总] 共 {len(pending)} 个未达标库，逐个安装："
                 f"{', '.join(n for n, _, _ in pending)}")
        ok_list, fail_list = [], []
        for name, op, req in pending:
            spec = f"{name}{op}{req}"
            callback(f"[安装] {spec} …")
            code = -1
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install", spec],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env=utf8_env(),
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    callback(line.rstrip())
                proc.wait()
                code = proc.returncode
            except Exception as exc:  # noqa: BLE001
                callback(f"[错误] {exc}")
            have = installed_version(name)
            ok = code == 0 and bool(have) and satisfies(have, op, req)
            if ok:
                ok_list.append(f"{name} {have}")
                callback(f"[成功] {name} 已安装 {have}")
            else:
                fail_list.append(name)
                callback(f"[失败] {name} 未安装成功（pip 退出码 {code}）")
        if ok_list:
            callback("[汇总] 安装成功：" + "；".join(ok_list))
        if fail_list:
            callback("[汇总] 安装失败：" + "、".join(fail_list))
        callback(f"[完成] 升级结束：成功 {len(ok_list)} / 失败 {len(fail_list)}")
        if on_finished:
            on_finished()

    threading.Thread(target=_run, daemon=True).start()


def check_python() -> str:
    v = sys.version_info
    if (3, 10) <= v < (3, 14):
        return f"Python {v.major}.{v.minor}（建议 3.10~3.13，已满足）"
    return f"Python {v.major}.{v.minor}.{v.micro}（建议使用 3.10~3.13）"


def launch_direct(args: list) -> int:
    """直接启动主程序 main.py（透传剩余参数）。"""
    subprocess.call([sys.executable, str(MAIN)] + list(args), env=utf8_env())
    return 0


# ------------------------------------------------------------------ GUI
def run_gui() -> int:
    # tkinter 是 Python 标准库（Windows 自带）。个别精简版 Python 可能缺失，
    # 此时降级为命令行直接启动，避免启动器自身报错崩溃。
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:  # noqa: BLE001
        print(f"[start] tkinter 不可用（{exc}），回退为直接启动主程序。")
        return launch_direct([a for a in sys.argv[1:] if not a.startswith("--")])

    root = tk.Tk()
    root.title("Celestia AssistantAI - 启动器")
    root.geometry("680x520")
    root.minsize(600, 440)

    pad = {"padx": 12, "pady": 5}
    ttk.Label(root, text="Celestia AssistantAI - 启动器",
              font=("Microsoft YaHei", 14, "bold")).pack(anchor="w", **pad)
    ttk.Label(root, text=f"项目目录：{ROOT}",
              font=("Microsoft YaHei", 9)).pack(anchor="w", **pad)
    ttk.Label(root, text=check_python(),
              font=("Microsoft YaHei", 9)).pack(anchor="w", **pad)

    tree = ttk.Treeview(root, columns=("name", "target", "have", "status"),
                        show="headings", height=10)
    tree.heading("name", text="库")
    tree.heading("target", text="要求")
    tree.heading("have", text="已安装")
    tree.heading("status", text="状态")
    tree.column("name", width=150)
    tree.column("target", width=120)
    tree.column("have", width=120)
    tree.column("status", width=90)
    tree.pack(fill="both", expand=True, **pad)

    log = tk.Text(root, height=8, state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, **pad)

    bar = ttk.Frame(root)
    bar.pack(fill="x", **pad)
    ttk.Button(bar, text="检测库版本",
               command=lambda: refresh(tree, log)).pack(side="left", padx=6)
    ttk.Button(bar, text="升级未达标库",
               command=lambda: upgrade(tree, log)).pack(side="left", padx=6)
    ttk.Button(bar, text="直接启动",
               command=lambda: launch(root)).pack(side="right", padx=6)
    ttk.Button(bar, text="退出", command=root.destroy).pack(side="right", padx=6)

    def refresh(tree, log) -> None:
        for row in tree.get_children():
            tree.delete(row)
        for name, target, have, ok in check_deps():
            tree.insert("", "end",
                        values=(name, target, have, "达标" if ok else "未达标"))
        _log(log, "检测完成：已对比 requirements.txt 与已安装版本。")

    def upgrade(tree, log) -> None:
        _log(log, "开始逐个升级未达标库（pip install 单库，失败互不影响）…")
        upgrade_deps(
            lambda line: _log(log, line),
            on_finished=lambda: root.after(0, lambda: refresh(tree, log)),
        )

    def launch(root) -> None:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        cmd = [sys.executable, str(MAIN)] + args
        _log(log, "启动：" + " ".join(cmd))
        subprocess.Popen(cmd, cwd=str(ROOT), env=utf8_env())
        root.after(300, root.destroy)

    refresh(tree, log)
    root.mainloop()
    return 0


def _log(widget, text: str) -> None:
    widget.configure(state="normal")
    widget.insert("end", text + "\n")
    widget.see("end")
    widget.configure(state="disabled")


def main() -> int:
    # 强制 UTF-8（一切输出之前），Windows 下避免 GBK 乱码
    force_utf8_stdio()
    args = sys.argv[1:]
    if "--direct" in args:
        return launch_direct([a for a in args if a != "--direct"])
    if "--setup" in args:
        print("[start] 检测库版本…")
        bad = []
        for name, target, have, ok in check_deps():
            print(f"  {'OK ' if ok else '-- '}{name:<20} 要求 {target:<12}"
                  f"已装 {have or '未安装'}")
            if not ok:
                bad.append(name)
        if bad:
            print(f"[start] 升级未达标库：{', '.join(bad)}")
            done = threading.Event()
            upgrade_deps(lambda line: print(line), on_finished=done.set)
            if not done.wait(timeout=600):  # 最多等待 10 分钟
                print("[start] 警告：升级耗时超过 10 分钟，仍将启动主程序。")
            print("[start] 升级完成，重新检测：")
            for name, target, have, ok in check_deps():
                print(f"  {'OK ' if ok else '-- '}{name:<20} 要求 {target:<12}"
                      f"已装 {have or '未安装'}")
        return launch_direct([a for a in args if a != "--setup"])
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

