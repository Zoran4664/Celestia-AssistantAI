"""
file_tools.py — 文件读写工具 + 版本历史（V2-D2）

参考 HanaAgent（openhanako）lib/sandbox/read-enhanced.ts + lib/file-history/，
适配本项目并受 `utils.path_guard.PathGuard` 权限约束：

- read(path)：读取文件（文本文件做 UTF-8/GBK 编码检测；docx/xlsx/pdf 自动提取文本）
- write(path, content)：写文件（受 PathGuard 约束；写前自动快照到 history/file_history/）
- edit(path, old, new)：读-替换-写（同样写前快照）
- list_dir(path)：列目录
- extract_document(path)：文档提取（docx/xlsx/pdf/txt/md/csv）
- 版本历史：list_versions(path) / restore_version(path, version)
  —— 每次写操作前把旧内容存为快照，可回滚任意历史版本（防 AI 改坏文件）

安全：所有操作先过 PathGuard（read → readable；write/edit/delete → writable）；
快照目录 history/file_history/ 保留 file_tools.history_max_days 天。

线程纪律：本模块为普通单例（无 Qt），可在任意线程调用；文件操作为原子写。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config_loader import ConfigLoader, ROOT
from utils.path_guard import PathGuard

logger = logging.getLogger("AI_DeskMate.FileTools")

_MAX_READ_CHARS = 200_000


class FileTools:
    """文件读写工具（单例，受 PathGuard 约束）。"""

    _instance: Optional["FileTools"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._guard = PathGuard.instance(self._cfg)

    @classmethod
    def instance(cls) -> "FileTools":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _abs(self, path: str) -> Path:
        """相对路径统一解析为项目根下绝对路径（防止 cwd 漂移导致权限误判）。"""
        p = Path(str(path))
        if p.is_absolute():
            return p
        return Path(str(ROOT)) / p

    # ------------------------------------------------------------ 快照
    def _history_root(self) -> Path:
        return self._cfg.history_dir / "file_history"

    def _snapshot_dir(self, path: Path) -> Path:
        """按路径 hash 分目录存快照。"""
        h = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
        return self._history_root() / h

    def _snapshot_before_write(self, path: Path) -> None:
        """写前快照旧内容（存在且为文本时）。"""
        if not path.exists() or not path.is_file():
            return
        if not self._cfg.file_tools_history_enabled():
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        sd = self._snapshot_dir(path)
        sd.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        snap = sd / f"{stamp}.txt"
        if not snap.exists():
            snap.write_text(content, encoding="utf-8")
        # 索引
        idx = sd / "index.json"
        entries = []
        if idx.exists():
            try:
                entries = json.loads(idx.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                entries = []
        entries.append({"version": stamp, "file": str(path),
                        "snapshot": snap.name,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        idx.write_text(json.dumps(entries[-50:], ensure_ascii=False, indent=2),
                       encoding="utf-8")

    def list_versions(self, path: str) -> List[Dict[str, Any]]:
        """列出文件的历史版本。"""
        try:
            idx = self._snapshot_dir(Path(path)) / "index.json"
            if not idx.exists():
                return []
            data = json.loads(idx.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def restore_version(self, path: str, version: str) -> bool:
        """把文件恢复到指定版本（需可写权限）。"""
        p = Path(path)
        if not self._guard.writable(str(p)):
            return False
        try:
            sd = self._snapshot_dir(p)
            snap = sd / f"{version}.txt"
            if not snap.exists():
                return False
            self._snapshot_before_write(p)
            p.write_text(snap.read_text(encoding="utf-8"), encoding="utf-8")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("恢复版本失败: %s", exc)
            return False

    # ------------------------------------------------------------ 读
    def read(self, path: str) -> Tuple[bool, str, str]:
        """读取文件（受 PathGuard 约束）。

        :return: (ok, content_or_error, detail)
        """
        p = self._abs(path)
        if not self._guard.readable(str(p)):
            return False, "", "无权读取该路径"
        if not p.exists():
            return False, "", "文件不存在"
        if p.is_dir():
            return False, "", "目标是目录"
        try:
            raw = p.read_bytes()
            # UTF-8 优先，失败回退 GBK
            try:
                text = raw.decode("utf-8")
                enc = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
                enc = "gbk"
            if len(text) > _MAX_READ_CHARS:
                text = text[:_MAX_READ_CHARS] + "\n…（内容过长已截断）"
            return True, text, f"{p.name}（{enc}，{len(raw)} 字节）"
        except Exception as exc:  # noqa: BLE001
            return False, "", f"读取失败：{exc}"

    # ------------------------------------------------------------ 写
    def write(self, path: str, content: str) -> Tuple[bool, str]:
        """写文件（受 PathGuard 约束；写前快照）。"""
        p = self._abs(path)
        if not self._guard.writable(str(p)):
            return False, "无权写入该路径"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            self._snapshot_before_write(p)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(content or "", encoding="utf-8")
            os.replace(tmp, p)
            return True, f"已写入 {p.name}"
        except Exception as exc:  # noqa: BLE001
            return False, f"写入失败：{exc}"

    def edit(self, path: str, old: str, new: str) -> Tuple[bool, str]:
        """在文件中替换文本（读-替换-写，受 PathGuard 约束）。"""
        p = self._abs(path)
        if not self._guard.writable(str(p)):
            return False, "无权修改该路径"
        if not p.exists():
            return False, "文件不存在"
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return False, f"读取失败：{exc}"
        if old not in content:
            return False, "未找到要替换的文本"
        new_content = content.replace(old, new, 1)
        return self.write(str(p), new_content)

    # ------------------------------------------------------------ 目录
    def list_dir(self, path: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        """列出目录内容。"""
        p = self._abs(path)
        if not self._guard.readable(str(p)):
            return False, [], "无权读取该路径"
        if not p.exists() or not p.is_dir():
            return False, [], "目录不存在"
        try:
            items = []
            for child in sorted(p.iterdir()):
                try:
                    items.append({
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": child.stat().st_size if child.is_file() else 0,
                        "mtime": child.stat().st_mtime,
                    })
                except OSError:
                    continue
            return True, items, ""
        except Exception as exc:  # noqa: BLE001
            return False, [], f"读取失败：{exc}"

    # ------------------------------------------------------------ 文档提取
    def extract_document(self, path: str) -> Tuple[bool, str, str]:
        """提取文档文本（docx/xlsx/pdf/txt/md/csv）；非支持类型回退原样读取。"""
        p = self._abs(path)
        if not self._guard.readable(str(p)):
            return False, "", "无权读取该路径"
        if not p.exists():
            return False, "", "文件不存在"
        ext = p.suffix.lower()
        try:
            if ext == ".docx":
                import docx  # type: ignore
                d = docx.Document(str(p))
                text = "\n".join(par.text for par in d.paragraphs)
                return True, text[:_MAX_READ_CHARS], f"{p.name}（docx 提取）"
            if ext in (".xlsx", ".xlsm"):
                import openpyxl  # type: ignore
                wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
                lines = []
                for ws in wb.worksheets[:8]:
                    lines.append(f"# Sheet: {ws.title}")
                    for row in ws.iter_rows(values_only=True):
                        cells = [str(c) for c in row if c is not None]
                        if cells:
                            lines.append(" | ".join(cells))
                    lines.append("")
                wb.close()
                text = "\n".join(lines)
                return True, text[:_MAX_READ_CHARS], f"{p.name}（xlsx 提取）"
            if ext == ".pdf":
                try:
                    from pypdf import PdfReader  # type: ignore
                    reader = PdfReader(str(p))
                    text = "\n".join((page.extract_text() or "")
                                     for page in reader.pages[:25])
                    return True, text[:_MAX_READ_CHARS], f"{p.name}（pdf 提取）"
                except Exception:  # noqa: BLE001
                    return self.read(path)
            # 文本类直读
            return self.read(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("文档提取失败（回退直读）: %s", exc)
            return self.read(path)
