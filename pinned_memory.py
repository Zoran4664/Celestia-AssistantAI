"""
pinned_memory.py — 固定记忆（V2-A2）

参考 HanaAgent（openhanako）lib/memory/pinned-memory-store.ts + lib/tools/pinned-memory.ts。

固定记忆 = 「必须记住、不容检索失败」的信息（主人的生日、称呼、喜好等）。
- 存储：data/pinned.md（人类可读 Markdown，可直接手工编辑；每行一条）；
- 注入：系统提示词固定段（永远命中，不受向量检索影响）；
- 去重：新增条目自动去重（包含去重 + 精确去重）；
- PII 脱敏：写入时对常见敏感信息（手机号 / 邮箱 / 身份证）打码。

线程纪律：本模块为普通函数/轻量类，可在任意线程调用（文件读写原子写）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from config_loader import ConfigLoader

logger = logging.getLogger("AI_DeskMate.Pinned")

# 轻量 PII 脱敏（写库前打码，避免敏感信息原样落盘）
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IDCARD_RE = re.compile(r"\d{17}[\dXx]")


def scrub_pii(text: str) -> str:
    """对文本中的手机号 / 邮箱 / 身份证号打码。"""
    if not text:
        return text
    out = _IDCARD_RE.sub(lambda m: m.group(0)[:6] + "**********" + m.group(0)[-1:], text)
    out = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], out)
    out = _EMAIL_RE.sub(lambda m: m.group(0)[:2] + "***@***" + m.group(0).rsplit(".", 1)[-1], out)
    return out


class PinnedMemory:
    """固定记忆管理器（单例）。"""

    _instance: Optional["PinnedMemory"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()

    @classmethod
    def instance(cls) -> "PinnedMemory":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _path(self) -> Path:
        return self._cfg.pinned_file()

    def enabled(self) -> bool:
        return bool(self._cfg.pinned_enabled())

    # ------------------------------------------------------------ 读写
    def read_pinned(self) -> List[str]:
        """读取全部固定记忆条目（按行拆分，忽略空行与 Markdown 标题）。"""
        path = self._path()
        if not path.exists():
            return []
        try:
            lines = []
            for ln in path.read_text(encoding="utf-8").splitlines():
                t = ln.strip()
                if not t or t.startswith("#"):
                    continue
                t = t.lstrip("-").strip() if t.startswith("-") else t
                if t:
                    lines.append(t)
            return lines
        except Exception as exc:  # noqa: BLE001
            logger.warning("固定记忆读取失败: %s", exc)
            return []

    def _write(self, items: List[str]) -> None:
        path = self._path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "# 固定记忆（可直接编辑，每行一条，空行与 # 开头的行忽略）\n"
            content += "\n".join(f"- {t}" for t in items if t.strip())
            content += "\n"
            tmp = path.with_suffix(".md.tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("固定记忆写入失败: %s", exc)

    # ------------------------------------------------------------ 操作
    def add_pinned(self, text: str) -> bool:
        """新增一条固定记忆（自动去重 + PII 脱敏）。"""
        if not self.enabled():
            return False
        t = scrub_pii((text or "").strip())
        if not t:
            return False
        items = self.read_pinned()
        # 去重：精确匹配或包含匹配
        for it in items:
            if it == t or t in it or it in t:
                return False
        items.append(t)
        self._write(items)
        return True

    def remove_pinned(self, text: str) -> bool:
        """删除一条固定记忆（按精确或包含匹配）。"""
        t = (text or "").strip()
        items = self.read_pinned()
        keep = [it for it in items if it != t and t not in it]
        if len(keep) == len(items):
            return False
        self._write(keep)
        return True

    def clear_pinned(self) -> int:
        """清空全部固定记忆，返回删除条数。"""
        n = len(self.read_pinned())
        if n:
            self._write([])
        return n

    def set_pinned(self, items: List[str]) -> int:
        """整体替换固定记忆（每行一条，自动去重 + PII 脱敏），返回最终条数。"""
        seen = set()
        clean: List[str] = []
        for raw in items:
            t = scrub_pii((raw or "").strip())
            if not t or t in seen:
                continue
            seen.add(t)
            clean.append(t)
        self._write(clean)
        return len(clean)

    # ------------------------------------------------------------ 注入
    def get_context(self) -> str:
        """返回注入系统提示词的固定记忆文本（空则返回空串）。"""
        if not self.enabled():
            return ""
        items = self.read_pinned()
        if not items:
            return ""
        return "【固定记忆】以下是必须始终记住的内容（优先级最高，不可遗忘）：\n" + "\n".join(
            f"- {t}" for t in items)
