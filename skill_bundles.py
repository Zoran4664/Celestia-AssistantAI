"""
skill_bundles.py — 技能包（V2-C1）

参考 HanaAgent（openhanako）lib/skill-bundles/store.ts + package-service.ts。

技能包 = 一组技能的容器，可整体启用 / 停用 / 打包 zip 分享 / 导入解包。
- 存储：data/skill_bundles.json（bundle 列表：id/name/skillNames/created_at/updated_at）
- 打包：把 bundle 内技能导出为 <名称>-skillbundle.zip（bundle.json manifest + skills/ 数据），
  重名自动加序号；只打包存在于技能库的技能；
- 导入：解包 zip → 校验 manifest → 把技能并入技能库（tools_list.json + skilltools_information.json）
  —— 导入前自动去重（同名技能跳过，不覆盖现有）；
- 批量启停：按 bundle 一次启用 / 停用包内全部技能。

线程纪律：本模块为普通函数 / 轻量类（无 Qt），可在任意线程调用；JSON 原子写。
"""

from __future__ import annotations

import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import ConfigLoader

logger = logging.getLogger("AI_DeskMate.SkillBundles")

_BUNDLE_MANIFEST = "bundle.json"


class SkillBundles:
    """技能包管理器（单例）。"""

    _instance: Optional["SkillBundles"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()

    @classmethod
    def instance(cls) -> "SkillBundles":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 路径
    def store_path(self) -> Path:
        return self._cfg.skill_bundles_file()

    def skills_dir(self) -> Path:
        return self._cfg.skills_dir

    def tools_list_path(self) -> Path:
        return self.skills_dir() / "tools_list.json"

    def info_path(self) -> Path:
        return self.skills_dir() / "skilltools_information.json"

    # ------------------------------------------------------------ 存储
    def _load_store(self) -> List[Dict[str, Any]]:
        try:
            p = self.store_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and isinstance(data.get("bundles"), list):
                    return data["bundles"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包读取失败: %s", exc)
        return []

    def _save_store(self, bundles: List[Dict[str, Any]]) -> None:
        try:
            p = self.store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "bundles": bundles},
                ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包保存失败: %s", exc)

    # ------------------------------------------------------------ 技能库读写
    def _read_skills(self) -> List[Dict[str, Any]]:
        try:
            p = self.tools_list_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                skills = data.get("skills") if isinstance(data, dict) else data
                return list(skills) if isinstance(skills, list) else []
        except Exception:  # noqa: BLE001
            pass
        return []

    def _read_info(self) -> Dict[str, Any]:
        try:
            p = self.info_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _write_skills(self, skills: List[Dict[str, Any]]) -> None:
        self.skills_dir().mkdir(parents=True, exist_ok=True)
        tmp = self.tools_list_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
             "count": len(skills), "skills": skills},
            ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.tools_list_path())

    def _write_info(self, info: Dict[str, Any]) -> None:
        self.skills_dir().mkdir(parents=True, exist_ok=True)
        tmp = self.info_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.info_path())

    def all_skill_names(self) -> List[str]:
        """技能库中全部技能名。"""
        return [str(s.get("name") or "") for s in self._read_skills()
                if s.get("name")]

    # ------------------------------------------------------------ bundle CRUD
    def create_bundle(self, name: str, skill_names: List[str]) -> Optional[Dict[str, Any]]:
        """创建技能包（技能名去重，仅保留存在于技能库的）。"""
        name = (name or "").strip()
        if not name:
            return None
        valid = {n for n in self.all_skill_names()}
        names = list(dict.fromkeys(n for n in (skill_names or [])
                                   if n in valid))
        bundles = self._load_store()
        bundle = {
            "id": f"bundle_{int(time.time())}_{os.urandom(2).hex()}",
            "name": name[:40],
            "skill_names": names,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        bundles.append(bundle)
        self._save_store(bundles)
        return dict(bundle)

    def list_bundles(self) -> List[Dict[str, Any]]:
        return self._load_store()

    def get_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        for b in self._load_store():
            if b.get("id") == bundle_id:
                return dict(b)
        return None

    def update_bundle(self, bundle_id: str, name: Optional[str] = None,
                      skill_names: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        bundles = self._load_store()
        for b in bundles:
            if b.get("id") != bundle_id:
                continue
            if name is not None and (name or "").strip():
                b["name"] = name.strip()[:40]
            if skill_names is not None:
                valid = {n for n in self.all_skill_names()}
                b["skill_names"] = list(dict.fromkeys(
                    n for n in skill_names if n in valid))
            b["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_store(bundles)
            return dict(b)
        return None

    def delete_bundle(self, bundle_id: str) -> bool:
        bundles = self._load_store()
        kept = [b for b in bundles if b.get("id") != bundle_id]
        if len(kept) == len(bundles):
            return False
        self._save_store(kept)
        return True

    # ------------------------------------------------------------ 批量启停
    def set_bundle_enabled(self, bundle_id: str, enabled: bool) -> int:
        """按包批量启用 / 停用技能（写回 tools_list.json），返回生效技能数。"""
        bundle = self.get_bundle(bundle_id)
        if not bundle:
            return 0
        names = set(bundle.get("skill_names") or [])
        skills = self._read_skills()
        changed = 0
        for s in skills:
            if str(s.get("name") or "") in names:
                cur = s.get("enabled", True)
                if bool(cur) != bool(enabled):
                    s["enabled"] = bool(enabled)
                    changed += 1
        if changed:
            self._write_skills(skills)
        return changed

    # ------------------------------------------------------------ 打包 / 导入
    def export_bundle_zip(self, bundle_id: str,
                          target_dir: Optional[Path] = None,
                          exact_path: Optional[Path] = None) -> Optional[Path]:
        """把技能包打成 zip（bundle.json + skills/ 数据）。

        :param bundle_id: 技能包 id
        :param target_dir: 导出目录（自动命名；与 exact_path 二选一）
        :param exact_path: 用户指定的完整保存路径（优先于 target_dir）
        """
        bundle = self.get_bundle(bundle_id)
        if not bundle:
            return None
        if exact_path is not None:
            out_path = Path(exact_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            target_dir = target_dir or self._cfg.data_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            base = "".join(c for c in str(bundle.get("name") or "skillbundle")
                           if c.isalnum() or c in "-_") or "skillbundle"
            out_path = target_dir / f"{base}-skillbundle.zip"
            n = 2
            while out_path.exists():
                out_path = target_dir / f"{base}-{n}-skillbundle.zip"
                n += 1
        skills = self._read_skills()
        info = self._read_info()
        try:
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(_BUNDLE_MANIFEST, json.dumps({
                    "kind": "SkillBundle", "version": 1,
                    "name": bundle.get("name"),
                    "skill_names": bundle.get("skill_names"),
                }, ensure_ascii=False, indent=2))
                for s in skills:
                    name = str(s.get("name") or "")
                    if name not in (bundle.get("skill_names") or []):
                        continue
                    zf.writestr(f"skills/{name}/skill.json",
                                json.dumps(s, ensure_ascii=False, indent=2))
                    detail = info.get(name)
                    if isinstance(detail, dict):
                        zf.writestr(f"skills/{name}/detail.json",
                                    json.dumps(detail, ensure_ascii=False,
                                               indent=2))
            return out_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包导出失败: %s", exc)
            try:
                out_path.unlink()
            except OSError:
                pass
            return None

    def import_bundle_zip(self, zip_path: Path) -> Dict[str, Any]:
        """导入技能包 zip：校验 manifest → 并入技能库（同名跳过）。

        :return: {"added": n, "skipped": n, "bundle_name": str}
        """
        result = {"added": 0, "skipped": 0, "bundle_name": ""}
        zip_path = Path(zip_path)
        if not zip_path.exists():
            return result
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if _BUNDLE_MANIFEST not in zf.namelist():
                    logger.warning("技能包导入失败：缺少 %s", _BUNDLE_MANIFEST)
                    return result
                manifest = json.loads(zf.read(_BUNDLE_MANIFEST).decode("utf-8"))
                result["bundle_name"] = str(manifest.get("name") or zip_path.stem)
                skills = self._read_skills()
                info = self._read_info()
                existing = {str(s.get("name") or "") for s in skills}
                prefix = "skills/"
                for name_item in zf.namelist():
                    if not name_item.startswith(prefix) \
                            or not name_item.endswith("/skill.json"):
                        continue
                    skill_name = name_item[len(prefix):].split("/")[0]
                    try:
                        skill_data = json.loads(
                            zf.read(name_item).decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        continue
                    if not isinstance(skill_data, dict) \
                            or not skill_data.get("trigger"):
                        continue
                    sname = str(skill_data.get("name") or skill_name)
                    if sname in existing:
                        result["skipped"] += 1
                        continue
                    skills.append(skill_data)
                    existing.add(sname)
                    detail_item = (f"skills/{skill_name}/detail.json")
                    if detail_item in zf.namelist():
                        try:
                            detail = json.loads(
                                zf.read(detail_item).decode("utf-8"))
                            if isinstance(detail, dict):
                                info[sname] = detail
                        except Exception:  # noqa: BLE001
                            pass
                    result["added"] += 1
                if result["added"]:
                    self._write_skills(skills)
                    self._write_info(info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包导入失败: %s", exc)
        return result
