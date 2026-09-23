# -*- coding: utf-8 -*-
"""skill_userdata.py —— 技能产出目录：一个对话一个「日期+时间」项目文件夹。

需求（skill库 技能产出落盘）：
- 允许技能生成文件时，在 ``skilluserdata/`` 下建立以「日期+时间」命名的项目
  文件夹，**一个对话（会话）一个**，同一对话多次执行技能复用同一个文件夹；
- 用户可用指令更改项目名（即文件夹名）：``\\@project <新名字>``；
- 附件栏可选择「工作文件夹」：选择后本轮/该对话的技能产出落到该文件夹，
  未选择时落到本对话的默认项目文件夹。

存储结构::

    skilluserdata/
    ├── projects.json          ← 会话 uid → 项目文件夹名（持久映射，重启后仍对应）
    └── 20260918-142530/       ← 项目文件夹（技能产出文件写在这里）

线程纪律：普通单例（不依赖 Qt），可在任意线程调用；JSON 原子写。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from config_loader import ConfigLoader
except Exception:  # 独立脚本运行等场景
    ConfigLoader = None

MAP_NAME = "projects.json"
logger = logging.getLogger("AI_DeskMate.SkillUserData")


def userdata_dir() -> Path:
    """技能产出根目录（默认 项目根/skilluserdata，可用 paths.skilluserdata 配置）。"""
    if ConfigLoader is not None:
        try:
            return Path(ConfigLoader.instance().skilluserdata_dir)
        except Exception:  # noqa: BLE001
            pass
    return Path(__file__).resolve().parent / "skilluserdata"


class SkillUserData:
    """技能产出目录管理（单例）。"""

    _instance: Optional["SkillUserData"] = None

    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}

    @classmethod
    def instance(cls) -> "SkillUserData":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 基础路径
    def root(self) -> Path:
        return userdata_dir()

    def ensure(self) -> Path:
        """确保根目录存在。"""
        folder = self.root()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建 skilluserdata 目录失败: %s", exc)
        return folder

    def _map_path(self) -> Path:
        return self.root() / MAP_NAME

    # ------------------------------------------------------------ 映射读写
    def _read_map(self) -> Dict[str, str]:
        path = self._map_path()
        if not path.exists():
            return dict(self._cache)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 %s 失败: %s", path.name, exc)
            return dict(self._cache)
        items = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            items = {}
        self._cache = {str(k): str(v) for k, v in items.items() if str(v).strip()}
        return dict(self._cache)

    def _write_map(self, data: Dict[str, str]) -> None:
        self.ensure()
        path = self._map_path()
        payload = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "projects": data}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        try:
            os.replace(str(tmp), str(path))
        except OSError:  # 极端情况（被杀软占用）退化为直接写
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        self._cache = dict(data)

    # ------------------------------------------------------------ 名称与目录
    @staticmethod
    def safe_name(name: str) -> str:
        """项目名清洗为合法文件夹名（去非法字符与首尾空白/点号）。"""
        raw = unicodedata.normalize("NFKC", str(name or "")).strip()
        raw = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", raw)
        raw = re.sub(r"\s+", "_", raw).strip("._")
        return raw or time.strftime("%Y%m%d-%H%M%S")

    def folder_name(self, uid: str, create: bool = True) -> str:
        """返回某个会话（uid）的项目文件夹名；不存在时按「日期+时间」建立。"""
        uid = str(uid or "").strip() or "default"
        mapping = self._read_map()
        folder = mapping.get(uid, "")
        if folder and (self.root() / folder).exists():
            return folder
        if folder and not create:
            # 映射在但目录被人删了 → 视为未建立
            return ""
        if not create and not folder:
            return ""
        name = time.strftime("%Y%m%d-%H%M%S")
        candidate = name
        n = 2
        while (self.root() / candidate).exists() or candidate in mapping.values():
            candidate = f"{name}-{n}"
            n += 1
        try:
            (self.root() / candidate).mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建项目文件夹失败: %s", exc)
            return ""
        mapping[uid] = candidate
        self._write_map(mapping)
        return candidate

    def project_dir(self, uid: str, create: bool = True) -> Optional[Path]:
        """返回某个会话的项目文件夹路径；create=False 时未建立则返回 None。"""
        folder = self.folder_name(uid, create=create)
        if not folder:
            return None
        path = self.root() / folder
        if create:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("创建项目文件夹失败: %s", exc)
                return None
        return path if path.exists() else None

    def rename(self, uid: str, new_name: str) -> Tuple[bool, str]:
        """把某会话的项目文件夹改名为 new_name（用户指令 ``\\@project``）。"""
        uid = str(uid or "").strip() or "default"
        clean = self.safe_name(new_name)
        if not clean:
            return False, "项目名不能为空"
        if re.search(r"[\\/:*?\"<>|]", str(new_name or "")):
            return False, "项目名不能包含 \\ / : * ? \" < > | 等字符"
        mapping = self._read_map()
        old = mapping.get(uid, "")
        old_dir = self.root() / old if old else None
        new_dir = self.root() / clean
        others = {k: v for k, v in mapping.items() if k != uid}
        if clean in others.values():
            return False, f"项目名「{clean}」已被其它对话使用，请换一个"
        try:
            self.ensure()
            if old_dir is not None and old_dir.exists() and old_dir != new_dir:
                if new_dir.exists():
                    return False, f"目录「{clean}」已存在，请换一个名字"
                old_dir.rename(new_dir)
            else:
                new_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return False, f"重命名失败：{exc}"
        mapping[uid] = clean
        self._write_map(mapping)
        return True, f"项目名已改为「{clean}」（{new_dir}）"

    # ------------------------------------------------------------ 执行用解析
    def resolve(self, uid: str, override: str = "") -> Optional[Path]:
        """技能产出的实际目录：用户选择的工作文件夹优先，否则本对话项目文件夹。"""
        ov = str(override or "").strip()
        if ov:
            p = Path(ov).expanduser()
            try:
                if p.is_dir():
                    return p
            except Exception:  # noqa: BLE001
                pass
        return self.project_dir(uid, create=False)

    def label(self, uid: str) -> str:
        """当前项目文件夹名（未建立返回空串）。"""
        return self.folder_name(uid, create=False)


if __name__ == "__main__":  # 调试入口
    sud = SkillUserData.instance()
    print("skilluserdata 目录：", sud.root())
    print("示例会话项目文件夹：", sud.project_dir("debug"))
    print("改名：", sud.rename("debug", "我的项目"))
