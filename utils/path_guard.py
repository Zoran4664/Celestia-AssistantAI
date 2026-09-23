"""
path_guard.py — 路径权限四档模型（V2-D1）

参考 HanaAgent（openhanako）lib/sandbox/path-guard.ts + policy.ts，适配本项目。

四级访问级别（BLOCKED < READ_ONLY < READ_WRITE < FULL）：
- BLOCKED    禁止任何访问（敏感文件 / 系统路径 / 敏感目录）
- READ_ONLY  只读
- READ_WRITE 可读写（配置 file_tools.writable_roots 白名单）
- FULL       完全访问（项目数据目录）

规则（单一来源，check 统一判定）：
- 屏蔽敏感文件：data/config.json（含 API Key）、.env、*.key、*_key.json 等
- 屏蔽敏感目录：__pycache__、.git、.venv、node_modules 等
- FULL 目录：data（config.json 除外）、dailydata、createimage、history、roles、roles_img、roles_desktop、skills、theme
- READ_WRITE：配置的额外可写根
- 项目根下其它：READ_ONLY
- 项目根之外（系统路径等）：BLOCKED

线程纪律：本模块为纯函数 / 轻量类，可在任意线程调用。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Tuple

from config_loader import ROOT, ConfigLoader

logger = logging.getLogger("AI_DeskMate.PathGuard")

# ---------------------------------------------------------------- 级别
BLOCKED = "blocked"
READ_ONLY = "read_only"
READ_WRITE = "read_write"
FULL = "full"

_OP_MIN_LEVEL = {
    "read": (READ_ONLY, READ_WRITE, FULL),
    "write": (READ_WRITE, FULL),
    "delete": (FULL,),
}

# ---------------------------------------------------------------- 敏感名单
_BLOCKED_FILES = {
    "config.json", ".env", ".env.local", "*.key", "*_key.json",
    "secret.json", "secrets.json", ".npmrc",
}
_BLOCKED_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules",
                 ".idea", ".vscode", ".tmp", "logs"}
_BLOCKED_SUFFIXES = {".key", ".pem", ".p12"}


def _is_blocked_file(name: str) -> bool:
    """文件名命中屏蔽名单（支持 * 通配）。"""
    low = name.lower()
    if low in {x.lower() for x in _BLOCKED_FILES if "*" not in x}:
        return True
    if any(low.startswith(x[:-1]) for x in _BLOCKED_FILES
           if x.endswith("*") and len(x) > 1):
        return True
    if any(low.endswith(suf) for suf in _BLOCKED_SUFFIXES):
        return True
    return False


def _is_blocked_dir(name: str) -> bool:
    return name.lower() in {x.lower() for x in _BLOCKED_DIRS}


class PathGuard:
    """路径权限判定器（单例）。"""

    _instance: Optional["PathGuard"] = None

    def __init__(self, cfg: Optional[Any] = None) -> None:
        self._cfg = cfg or ConfigLoader.instance()
        self._root = Path(str(ROOT)).resolve()
        # 用户显式选择的目录（如技能产出「工作文件夹」）→ 允许读写；
        # 仅存在于当前进程（重启后由 UI 恢复会话时重新授权）。
        self._user_roots: set = set()

    @classmethod
    def instance(cls, cfg: Optional[Any] = None) -> "PathGuard":
        if cls._instance is None or cfg is not None:
            cls._instance = cls(cfg)
        return cls._instance

    # ------------------------------------------------------------ 规则
    def _full_roots(self):
        """FULL 目录：项目数据目录（绝对路径列表）。"""
        cfg = self._cfg
        return [
            str(cfg.data_dir), str(cfg.data_dir.parent / "dailydata"),
            str(cfg.data_dir.parent / "createimage"),
            str(cfg.history_dir), str(cfg.roles_dir),
            str(cfg.roles_img_dir), str(cfg.roles_desktop_dir),
            str(cfg.skills_dir), str(cfg.theme_dir),
            # 技能产出目录（skilluserdata）：技能生成的文件写到这里
            str(cfg.skilluserdata_dir),
        ]

    def allow_dir(self, path: str) -> bool:
        """授权一个用户显式选择的目录（如技能产出「工作文件夹」）为可读写。

        :return: 是否成功授权（路径存在且为目录）
        """
        if not str(path or "").strip():
            return False
        p = self._resolve(path)
        try:
            if not p.is_dir():
                return False
        except OSError:
            return False
        self._user_roots.add(str(p))
        return True

    def _rw_roots(self):
        """READ_WRITE 根：配置 file_tools.writable_roots（已解析为绝对路径）。"""
        return list(self._cfg.file_tools_writable_roots() or [])

    def _resolve(self, path: str) -> Path:
        """realpath 解析（跟踪符号链接）；不存在时基于最近的存在的祖先解析。"""
        p = Path(str(path))
        try:
            return p.resolve()
        except OSError:
            return p.absolute()

    def _is_within(self, p: Path, root: Path) -> bool:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------ 判定
    def level_of(self, path: str) -> str:
        """返回路径所属级别。"""
        p = self._resolve(path)
        parts = [x for x in p.parts]

        # 屏蔽目录（任一路径段命中）
        for seg in parts:
            if _is_blocked_dir(seg):
                return BLOCKED
        # 屏蔽文件
        if p.is_file() or p.suffix:
            if _is_blocked_file(p.name):
                return BLOCKED

        # 用户显式授权目录（技能产出工作文件夹等）：可读写，不受项目根限制
        for root_s in self._user_roots:
            if self._is_within(p, Path(root_s)) or str(p) == root_s:
                return READ_WRITE

        # 必须在项目根内
        if not self._is_within(p, self._root):
            return BLOCKED

        # FULL 目录
        for root_s in self._full_roots():
            root_p = Path(root_s)
            if self._is_within(p, root_p) or p == root_p:
                # data 下的 config.json 仍屏蔽
                if p.name and _is_blocked_file(p.name):
                    return BLOCKED
                return FULL

        # READ_WRITE 根
        for root_s in self._rw_roots():
            root_p = Path(root_s)
            if self._is_within(p, root_p) or p == root_p:
                return READ_WRITE

        # 项目根内其它：只读
        return READ_ONLY

    def check(self, path: str, operation: str = "read"
              ) -> Tuple[bool, str, str]:
        """判定操作是否允许。

        :param path: 目标路径
        :param operation: read / write / delete
        :return: (allowed, reason, level)
        """
        level = self.level_of(path)
        allowed_levels = _OP_MIN_LEVEL.get(operation, (READ_ONLY,))
        allowed = level in allowed_levels
        if allowed:
            reason = f"{operation} 允许（{level}）"
        else:
            reason = f"{operation} 被拒绝（{level}）"
        return allowed, reason, level

    def readable(self, path: str) -> bool:
        return self.check(path, "read")[0]

    def writable(self, path: str) -> bool:
        return self.check(path, "write")[0]
