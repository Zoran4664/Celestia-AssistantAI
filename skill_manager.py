"""
skill_manager.py — 技能管理器（SkillManager 单例）

职责：
- 懒加载 skills/tools_list.json（技能目录）与 skills/skilltools_information.json（技能详情）
- 按触发词 / 名称 / 别名做前缀过滤，供 \\@技能 唤醒弹窗实时筛选
- 把技能说明构建为注入 LLM 的上下文文本（build_context）
- 支持两种技能类型（kind 字段）：
    kind="skill"   普通技能 / 场景标签（如 \\@research、\\@translate）
    kind="switch"  开关型工具（如 \\@thinking 思维链、\\@network 联网搜索，可同时开启 0-2 个）
- 全部路径基于项目根目录绝对拼装；JSON 缺失 / 损坏时降级为空技能列表，绝不抛错
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import ConfigLoader

logger = logging.getLogger("AI_DeskMate.Skill")

# \@技能 触发词合法字符（英文 / 数字 / 下划线 / 连字符 / 中文）
_TAG_TOKEN = r"[A-Za-z0-9_\u4e00-\u9fa5-]+"


class SkillManager:
    """技能目录 / 详情加载与查询门面（单例，懒加载）。"""

    _instance: Optional["SkillManager"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._catalog: List[Dict[str, Any]] = []
        self._info: Dict[str, Any] = {}
        self._loaded = False

    @classmethod
    def instance(cls) -> "SkillManager":
        """获取全局技能管理器单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 加载
    def reload(self, force: bool = False) -> None:
        """加载目录与详情 JSON（幂等，除非 force=True 强制重载）。"""
        if self._loaded and not force:
            return
        self._loaded = True
        self._catalog = []
        self._info = {}
        root: Path = self._cfg.skills_dir
        try:
            catalog_path = root / "tools_list.json"
            if catalog_path.exists():
                data = json.loads(catalog_path.read_text(encoding="utf-8"))
                skills = data.get("skills") if isinstance(data, dict) else data
                if isinstance(skills, list):
                    for it in skills:
                        if isinstance(it, dict) and it.get("trigger"):
                            self._catalog.append(it)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能目录加载失败（降级为空技能列表）: %s", exc)
        try:
            info_path = root / "skilltools_information.json"
            if info_path.exists():
                raw = json.loads(info_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._info = raw
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能详情加载失败（降级为空详情）: %s", exc)
        logger.info("技能加载完成：目录 %d 条，详情 %d 条",
                    len(self._catalog), len(self._info))

    def ensure_loaded(self) -> None:
        """确保已加载（懒加载入口，供首次调用时触发）。"""
        if not self._loaded:
            self.reload()



    # ------------------------------------------------------------ 查询
    def skills(self) -> List[Dict[str, Any]]:
        """返回启用的技能目录（附详情字段：description / prompt_template / parameters）。

        开关型工具（kind=switch）优先排列，便于用户先选开关、再选场景标签。
        """
        self.ensure_loaded()
        result: List[Dict[str, Any]] = []
        for s in self._catalog:
            if s.get("enabled", True) is False:
                continue
            item = dict(s)
            info = self._info.get(s.get("name"))
            if isinstance(info, dict):
                for k, v in info.items():
                    item.setdefault(k, v)
            result.append(item)
        # 开关型（思维链 / 联网搜索）排最前，普通标签 / 技能按名称排序
        result.sort(key=lambda s: (
            0 if s.get("kind") == "switch" else 1,
            str(s.get("name") or "")))
        return result

    def match(self, prefix: str) -> List[Dict[str, Any]]:
        """按触发词 / 名称 / 别名前缀过滤；prefix 为空返回全部技能。"""
        self.ensure_loaded()
        p = (prefix or "").strip().lower()
        if not p:
            return self.skills()
        out = []
        for s in self.skills():
            trigger = str(s.get("trigger") or "").lower()
            name = str(s.get("name") or "").lower()
            aliases = [str(a).lower() for a in (s.get("aliases") or [])]
            if (trigger.startswith(p) or name.startswith(p)
                    or any(a.startswith(p) for a in aliases)):
                out.append(s)
        # 保底：无前缀命中时退化为子串匹配，提高命中率
        if not out:
            out = [s for s in self.skills()
                   if p in str(s.get("trigger") or "").lower()
                   or p in str(s.get("name") or "").lower()
                   or p in " ".join(str(a).lower()
                                    for a in (s.get("aliases") or []))]
        return out

    def resolve(self, token: str) -> Optional[Dict[str, Any]]:
        """将 @token 解析为技能；无法识别返回 None（触发词 > 名称 > 别名）。"""
        self.ensure_loaded()
        t = (token or "").strip().lower()
        if not t:
            return None
        for s in self.skills():
            if str(s.get("trigger") or "").lower() == t:
                return s
        for s in self.skills():
            if str(s.get("name") or "").lower() == t:
                return s
        for s in self.skills():
            aliases = [str(a).lower() for a in (s.get("aliases") or [])]
            if t in aliases:
                return s
        return None

    def parse_tags(self, raw: str) -> List[Dict[str, Any]]:
        """从文本中提取全部已注册的 \\@技能 引用（按出现顺序、去重）。

        标签后可能紧跟普通文本（如「帮我\\@翻译这段话」），若贪婪匹配会
        把整段吞成 token 导致 resolve 失败。因此对每个 \\@ 命中从最长到
        最短逐级截断，取能解析为已注册技能的最长前缀。
        """
        self.ensure_loaded()
        found: List[Dict[str, Any]] = []
        seen = set()
        for m in re.finditer(rf"\\@{_TAG_TOKEN}", raw or ""):
            full = m.group(0)[2:]
            skill: Optional[Dict[str, Any]] = None
            for end in range(len(full), 0, -1):
                cand = self.resolve(full[:end])
                if cand is not None:
                    skill = cand
                    break
            if skill is None:
                continue
            key = skill.get("trigger") or skill.get("name")
            if key in seen:
                continue
            seen.add(key)
            found.append(skill)
        return found

    def build_context(self, skill: Optional[Dict[str, Any]]) -> str:
        """构建注入 LLM 的技能说明文本；skill 无效时返回空串。

        开关型工具（kind=switch，如 \\@thinking / \\@network）输出
        「[工具开启]」语境，强调可同时开启的组合模式。
        """
        if not skill:
            return ""
        name = skill.get("name") or skill.get("trigger") or "技能"
        desc = skill.get("description") or ""
        template = skill.get("prompt_template") or ""
        if skill.get("kind") == "switch":
            lines = [f"[工具开启] 用户已开启「{name}」开关（\\{skill.get('trigger')}）。"]
            if desc:
                lines.append(f"工具说明：{desc}")
            if template:
                lines.append(f"执行要求：{template}")
            return "\n".join(lines)
        lines = [f"[技能调用] 用户调用了技能「{name}」。"]
        if desc:
            lines.append(f"技能说明：{desc}")
        if template:
            lines.append(f"执行要求：{template}")
        params = skill.get("parameters")
        if isinstance(params, list) and params:
            defaults = []
            for p in params:
                if isinstance(p, dict):
                    pn = p.get("name")
                    if pn:
                        pv = p.get("default")
                        defaults.append(f"{pn}={pv if pv is not None else ''}")
            if defaults:
                lines.append("参数默认值：" + "，".join(defaults))
        return "\n".join(lines)
