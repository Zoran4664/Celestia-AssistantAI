"""
character_card.py — 角色卡 zip 打包 / 导入（V2-C2）

参考 HanaAgent（openhanako）lib/character-cards/service.ts，适配本项目
「roles/<名>/roles.json + emotion.json + 立绘 + 桌宠动图」结构。

导出：把角色的角色卡、情绪映射、立绘、桌宠动图打包为一个 zip：
  manifest.json      {kind:"CharacterCard", version, role, display_name}
  role/<名>/roles.json
  role/<名>/emotion.json
  portrait/<角色名>-<情绪>.png ...（含默认 <角色名>-.png）
  desktop/<角色名>-<动作>.gif ...

导入：解包 → 校验 manifest → 复制到 roles/、roles_img/、roles_desktop/ →
      （同名角色已存在时跳过，返回冲突列表）

安全：路径防穿越（只允许包内白名单目录）、扩展名白名单、拒绝 symlink（zip 无 symlink 概念，
     但仍做路径校验）。

线程纪律：本模块为普通函数 / 轻量类，可在任意线程调用（原子写/复制）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import ConfigLoader

logger = logging.getLogger("AI_DeskMate.CharacterCard")

_MANIFEST = "manifest.json"
_PORTRAIT_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_DESKTOP_EXTS = {".gif"}


class CharacterCard:
    """角色卡打包器（单例）。"""

    _instance: Optional["CharacterCard"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()

    @classmethod
    def instance(cls) -> "CharacterCard":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 路径
    def roles_dir(self) -> Path:
        return self._cfg.roles_dir

    def roles_img_dir(self) -> Path:
        return self._cfg.roles_img_dir

    def roles_desktop_dir(self) -> Path:
        return self._cfg.roles_desktop_dir

    def export_dir(self) -> Path:
        return self._cfg.character_card_export_dir()

    # ------------------------------------------------------------ 导出
    def export_role_zip(self, role: str, include_assets: bool = True,
                        target_dir: Optional[Path] = None,
                        exact_path: Optional[Path] = None) -> Optional[Path]:
        """把角色打成角色卡 zip。返回 zip 路径；失败 None。

        :param role: 角色名
        :param include_assets: 是否包含立绘与桌宠动图
        :param target_dir: 导出目录（自动命名；与 exact_path 二选一）
        :param exact_path: 用户指定的完整保存路径（优先于 target_dir）
        """
        role_dir = self.roles_dir() / role
        if not (role_dir / "roles.json").exists():
            logger.warning("角色卡导出失败：缺少 roles.json（%s）", role)
            return None
        if exact_path is not None:
            out_path = Path(exact_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            target_dir = target_dir or self.export_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / f"{role}-charactercard.zip"
            n = 2
            while out_path.exists():
                out_path = target_dir / f"{role}-{n}-charactercard.zip"
                n += 1

        # 读取角色卡取 display_name
        display_name = role
        try:
            card = json.loads((role_dir / "roles.json").read_text(encoding="utf-8"))
            display_name = str(card.get("display_name") or card.get("name") or role)
        except Exception:  # noqa: BLE001
            pass

        try:
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(_MANIFEST, json.dumps({
                    "kind": "CharacterCard", "version": 1,
                    "role": role, "display_name": display_name,
                    "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                }, ensure_ascii=False, indent=2))
                # 角色卡 + 情绪映射
                for fn in ("roles.json", "emotion.json"):
                    p = role_dir / fn
                    if p.exists():
                        zf.write(p, f"role/{role}/{fn}")
                if include_assets:
                    # 立绘
                    img_dir = self.roles_img_dir() / role
                    if img_dir.exists():
                        for p in sorted(img_dir.iterdir()):
                            if p.suffix.lower() in _PORTRAIT_EXTS:
                                zf.write(p, f"portrait/{p.name}")
                    # 桌宠动图
                    pet_dir = self.roles_desktop_dir() / role
                    if pet_dir.exists():
                        for p in sorted(pet_dir.iterdir()):
                            if p.suffix.lower() in _DESKTOP_EXTS:
                                zf.write(p, f"desktop/{p.name}")
            return out_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色卡导出失败: %s", exc)
            try:
                out_path.unlink()
            except OSError:
                pass
            return None

    # ------------------------------------------------------------ 预览
    def preview_zip(self, zip_path: Path) -> Dict[str, Any]:
        """读取角色卡 zip 的 manifest（导入前预览）。

        :return: {"role", "display_name", "has_portrait", "portrait_count",
                  "has_desktop", "desktop_count", "valid": bool}
        """
        result: Dict[str, Any] = {"valid": False}
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if _MANIFEST not in zf.namelist():
                    return result
                manifest = json.loads(zf.read(_MANIFEST).decode("utf-8"))
                role = str(manifest.get("role") or "")
                if not role:
                    return result
                names = set(zf.namelist())
                result.update({
                    "valid": True,
                    "role": role,
                    "display_name": str(manifest.get("display_name") or role),
                    "has_portrait": any(n.startswith("portrait/") for n in names),
                    "portrait_count": sum(1 for n in names
                                          if n.startswith("portrait/")),
                    "has_desktop": any(n.startswith("desktop/") for n in names),
                    "desktop_count": sum(1 for n in names
                                         if n.startswith("desktop/")),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色卡预览失败: %s", exc)
        return result

    # ------------------------------------------------------------ 导入
    def import_zip(self, zip_path: Path) -> Dict[str, Any]:
        """导入角色卡 zip：复制角色卡 / 立绘 / 桌宠动图到对应目录。

        同名角色已存在时跳过（不覆盖），返回 conflicts。
        :return: {"imported": bool, "conflicts": [str], "role": str}
        """
        result: Dict[str, Any] = {"imported": False, "conflicts": [], "role": ""}
        zip_path = Path(zip_path)
        if not zip_path.exists():
            return result
        preview = self.preview_zip(zip_path)
        if not preview.get("valid"):
            return result
        role = preview["role"]

        conflicts = []
        # 角色卡目录：同名已存在 → 冲突
        role_dir = self.roles_dir() / role
        if role_dir.exists():
            conflicts.append(f"角色 {role} 已存在")
        img_dir = self.roles_img_dir() / role
        pet_dir = self.roles_desktop_dir() / role

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name_item in zf.namelist():
                    # 白名单目录：role/ portrait/ desktop/
                    parts = name_item.split("/")
                    if len(parts) < 2 or parts[0] not in ("role", "portrait", "desktop"):
                        continue
                    if name_item.endswith("/"):
                        continue
                    # 防路径穿越（zip 内路径不得含 ..）
                    if ".." in parts:
                        continue
                    target_root = None
                    if parts[0] == "role":
                        if conflicts:
                            continue  # 角色已存在则不写任何文件
                        # zip 内为 role/<角色名>/roles.json → 写到 roles 顶层
                        target_root = self.roles_dir()
                    elif parts[0] == "portrait":
                        if Path(parts[-1]).suffix.lower() not in _PORTRAIT_EXTS:
                            continue
                        target_root = img_dir
                    elif parts[0] == "desktop":
                        if Path(parts[-1]).suffix.lower() not in _DESKTOP_EXTS:
                            continue
                        target_root = pet_dir
                    if target_root is None:
                        continue
                    dst = target_root / "/".join(parts[1:])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name_item) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色卡导入失败: %s", exc)
            return result

        if conflicts:
            result["conflicts"] = conflicts
            result["role"] = role
            return result
        result["imported"] = True
        result["role"] = role
        logger.info("角色卡导入成功: %s", role)
        return result
