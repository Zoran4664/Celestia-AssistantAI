"""
role_manager.py — 角色库 / 群聊预设解析（RoleManager 单例）

- 扫描 roles/ 目录解析角色卡（roles.json）与情感映射（emotion.json）
- 群聊发言者解析（parse_speaker）：正则兜底 + 容错机制，兼容
  「[角色A]: 内容」「角色A：内容」「角色A: 内容」「角色A 说：内容」
  「“内容” ——角色A」等多种变体（容忍多余空格、全角/半角冒号）
- 情感标签归一化：从回复文本提取 [emotion:x] / 【xx】 标签，映射为标准情感
- 立绘 / 桌宠动图路径解析（兼容多目录布局）
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.Role")

from config_loader import ConfigLoader

# 标准情感标签
STANDARD_EMOTIONS: List[str] = ["neutral", "happy", "sad", "angry", "surprised"]

# 侧边栏角色显示宽度上限（中文/全角计 2、英文/半角计 1；超过则截断加省略号）。
# 角色目录名过长（如 Twilight_Sparkle）会导致左侧角色下拉/历史侧边栏
# 换行错乱，通过 data/role_abbr.json 的缩写映射或按宽度截断保证单行显示。
SIDEBAR_MAX_WIDTH = 10

# 群聊发言者解析正则（按顺序尝试）
_SPEAKER_PATTERNS = [
    # [角色A]: 内容 / 【角色A】: 内容
    r"^\s*[\[【]\s*(?P<name>[^\]】]+?)\s*[\]】]\s*[:：]\s*(?P<content>.*)$",
    # 角色A: 内容 / 角色A：内容
    r"^\s*(?P<name>[^:：]{1,32}?)\s*[:：]\s*(?P<content>.*)$",
    # 角色A 说：内容
    r"^\s*(?P<name>[^:：]{1,32}?)\s*说\s*[:：]?\s*(?P<content>.*)$",
    # “内容” ——角色A / “内容”——角色A
    r'^[“"](?P<content>.+?)[”"]\s*[-—–]\s*(?P<name>[^，,。\s].{0,20})\s*$',
]

# 情感标签正则
# 支持【xx】/ [xx] / 〖xx〗（双角括号，需求：每句情绪标注用〖〗，绝不显示）
_EMOTION_TAG_RE = re.compile(r"[〖【\[]\s*([^〗】\]]{1,12}?)\s*[〗】\]]")
_EMOTION_BRACKET_RE = re.compile(r"\[?〖?\s*emotion\s*:\s*([a-zA-Z_\u4e00-\u9fff]+)\s*〗?\]?")
# 双角括号情绪标签（〖...〗），strip 时绝对移除不显示
_DOUBLE_BRACKET_RE = re.compile(r"〖[^〗]{1,24}?〗")


class RoleManager:
    """角色库 / 群聊解析单例。"""

    _instance: Optional["RoleManager"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._card_cache: Dict[str, dict] = {}
        self._emoji_cache: Dict[str, dict] = {}
        # 全局「关键词 -> 情感」反查表（跨角色汇总，用于识别【开心】这类中文标签）
        self._kw_to_emotion: Optional[Dict[str, str]] = None
        # 侧边栏角色缩写映射缓存（data/role_abbr.json 懒加载）
        self._abbr_cache: Optional[Dict[str, str]] = None

    @classmethod
    def instance(cls) -> "RoleManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 角色目录
    def list_roles(self) -> List[str]:
        """扫描 roles/ 下含 roles.json 的角色目录名。"""
        d = self._cfg.roles_dir
        if not d.is_dir():
            return []
        out = []
        for sub in sorted(p.name for p in d.iterdir() if p.is_dir()):
            if (d / sub / "roles.json").exists():
                out.append(sub)
        return out

    def role_card(self, name: str) -> dict:
        """读取角色卡 roles.json（带缓存）。"""
        if name in self._card_cache:
            return self._card_cache[name]
        card: dict = {}
        path = self._cfg.roles_dir / name / "roles.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    card = data
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色卡解析失败 %s: %s", name, exc)
        self._card_cache[name] = card
        return card

    def display_name(self, name: str) -> str:
        """角色显示名（缺省回退目录名）。"""
        card = self.role_card(name)
        return card.get("display_name") or card.get("name") or name

    # ------------------------------------------------------------ 侧边栏缩写
    def _abbr_map(self) -> Dict[str, str]:
        """角色目录名 -> 侧边栏缩写 映射（data/role_abbr.json 懒加载）。"""
        if self._abbr_cache is None:
            data: Dict[str, str] = {}
            try:
                p = self._cfg.data_dir / "role_abbr.json"
                if p.exists():
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = {str(k): str(v) for k, v in raw.items()}
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色缩写映射读取失败: %s", exc)
            self._abbr_cache = data
        return self._abbr_cache

    @staticmethod
    def _display_width(text: str) -> int:
        """估算显示宽度：中文/全角字符计 2，其余计 1。"""
        return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)

    @staticmethod
    def truncate_wide(text: str, limit: int = SIDEBAR_MAX_WIDTH,
                      ellipsis: str = "…") -> str:
        """按显示宽度截断，超出部分以 ellipsis 结尾（总宽度不超过 limit，单行不换行错乱）。"""
        if RoleManager._display_width(text) <= limit:
            return text
        ell_w = RoleManager._display_width(ellipsis)
        out: List[str] = []
        width = 0
        for ch in text:
            cw = 2 if ord(ch) > 0x2E7F else 1
            # 预留省略号宽度，保证截断后总宽不超 limit
            if width + cw + ell_w > limit:
                break
            out.append(ch)
            width += cw
        return "".join(out) + ellipsis

    def sidebar_label(self, name: str) -> str:
        """角色在侧边栏显示的缩写/短名。

        优先使用 data/role_abbr.json 的缩写（如 Rainbow_Dash -> RD）；
        无映射时用角色显示名并按 SIDEBAR_MAX_WIDTH 宽度截断兜底，
        从根上避免长角色名导致侧边栏换行/错乱。
        """
        abbr = self._abbr_map().get(name)
        if abbr:
            return abbr
        return self.truncate_wide(self.display_name(name))

    def full_name(self, text: str) -> str:
        """下拉框显示文本 -> 真实角色目录名（缩写反向映射；无匹配返回原样）。"""
        for role, abbr in self._abbr_map().items():
            if abbr == text or role == text:
                return role
        return text

    def default_emotion(self, name: str) -> str:
        """角色默认情感标签。"""
        card = self.role_card(name)
        emo = str(card.get("default_emotion") or "neutral").lower()
        return emo if emo in STANDARD_EMOTIONS else "neutral"

    def character_description(self, name: str) -> str:
        """读取角色卡「character description」字段（角色背景设定）。

        该字段为角色的详细介绍与背景，对话时需要随系统提示词一并注入，
        让角色在回答时学习并遵循这份设定。缺省返回空串。
        """
        card = self.role_card(name)
        desc = card.get("character description") or card.get("character_description") or ""
        return (desc or "").strip()

    def role_system_prompt(self, name: str,
                           fallback: str = "你是一位贴心的桌面 AI 助手。") -> str:
        """组合完整角色系统提示词：system_prompt + 角色背景设定（character description）。

        所有对话入口统一调用本方法，确保角色在对话时学习并按照角色卡中的
        背景设定（character description）回答，同时不删除原 system_prompt 的任何内容。
        """
        card = self.role_card(name)
        sp = card.get("system_prompt") or fallback
        desc = self.character_description(name)
        if desc:
            sp += f"\n\n【角色背景设定（Character Description）】\n{desc}"
        return sp

    # ------------------------------------------------------------ 情感映射
    def emotion_map(self, name: str) -> dict:
        """读取角色情感映射 emotion.json。"""
        if name in self._emoji_cache:
            return self._emoji_cache[name]
        path = self._cfg.roles_dir / name / "emotion.json"
        data: dict = {"mappings": {}, "keywords": {}, "default": "neutral"}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception as exc:  # noqa: BLE001
                logger.warning("情感映射解析失败 %s: %s", name, exc)
        self._emoji_cache[name] = data
        return data

    def _global_kw_map(self) -> Dict[str, str]:
        """汇总所有角色 emotion.json 的 keywords → 情感 反查表。"""
        if self._kw_to_emotion is not None:
            return self._kw_to_emotion
        table: Dict[str, str] = {}
        for role in self.list_roles():
            emap = self.emotion_map(role)
            for emo, words in (emap.get("keywords") or {}).items():
                if isinstance(words, list):
                    for w in words:
                        table[str(w).strip()] = str(emo).lower()
            for emo, words in (emap.get("mappings") or {}).items():
                if isinstance(words, list):
                    for w in words:
                        table[str(w).strip()] = str(emo).lower()
        self._kw_to_emotion = table
        return table

    def detect_emotion(self, text: str, role: Optional[str] = None) -> str:
        """从回复/输入文本归一化情感标签。

        优先级：显式 [emotion:x] 标签 > 【中文关键词】标签 > 用户关键词命中 > 角色默认。
        """
        if not text:
            return "neutral"   # 无法判断情绪时统一输出 neutral（平静）

        m = _EMOTION_BRACKET_RE.search(text)
        if m:
            emo = m.group(1).strip().lower()
            if emo in STANDARD_EMOTIONS:
                return emo

        # 【开心】这类中文标签：先查角色映射，再查全局反查表
        for match in _EMOTION_TAG_RE.finditer(text):
            word = match.group(1).strip()
            if not word:
                continue
            # 标准英文情绪（〖happy〗 等）直接返回
            if word.lower() in STANDARD_EMOTIONS:
                return word.lower()
            if role:
                emap = self.emotion_map(role)
                for emo, words in (emap.get("mappings") or {}).items():
                    if word in words:
                        return str(emo).lower()
            global_hit = self._global_kw_map().get(word)
            if global_hit:
                return global_hit

        # 用户关键词快速命中（角色级优先）
        if role:
            for emo, words in (self.emotion_map(role).get("keywords") or {}).items():
                if isinstance(words, list) and any(w in text for w in words):
                    return str(emo).lower()

        return "neutral"   # 无法判断情绪时统一输出 neutral（平静）

    def strip_emotion_tags(self, text: str) -> str:
        """移除回复中的情感标签（[emotion:x] / 【开心】/〖开心〗 等），保留正文。"""
        out = _EMOTION_BRACKET_RE.sub("", text)
        out = _DOUBLE_BRACKET_RE.sub("", out)   # 〖...〗 绝对不显示
        kw = self._global_kw_map()

        def _repl(match: "re.Match[str]") -> str:
            word = match.group(1).strip()
            if not word:
                return ""
            # 标准英文情绪标签（happy/sad/angry/surprised/neutral 等）
            if word.lower() in STANDARD_EMOTIONS:
                return ""
            # 全局关键词表命中（中文情绪词，如【开心】）
            if word in kw:
                return ""
            return match.group(0)

        out = _EMOTION_TAG_RE.sub(_repl, out)
        # 压缩多余空行
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()

    # ------------------------------------------------------------ 群聊
    def list_groups(self) -> Dict[str, dict]:
        """读取 roles/group.json 的群聊预设字典。"""
        path = self._cfg.roles_dir / "group.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                groups = data.get("groups") if isinstance(data, dict) else None
                if isinstance(groups, dict):
                    return groups
            except Exception as exc:  # noqa: BLE001
                logger.warning("群聊预设解析失败: %s", exc)
        return {}

    def group_members(self, group_name: str) -> List[str]:
        """群聊组成员（角色名列表）。"""
        groups = self.list_groups()
        g = groups.get(group_name) or {}
        members = g.get("members") or []
        return [m for m in members if isinstance(m, str)]

    def speaker_hint(self, group_name: str) -> str:
        """群聊发言格式约束（speaker_hint），无则给出默认提示。"""
        groups = self.list_groups()
        g = groups.get(group_name) or {}
        hint = g.get("speaker_hint")
        return hint or "回复必须以 [角色名]: 内容 开头"

    def group_aliases(self, group_name: str) -> Dict[str, str]:
        """群聊别名映射：中文昵称 → 角色名（来自 group.json 的 aliases 字段）。"""
        groups = self.list_groups()
        g = groups.get(group_name) or {}
        aliases = g.get("aliases") or {}
        return {str(k).strip(): str(v).strip() for k, v in aliases.items()
                if isinstance(v, str) and v.strip()}

    def mentioned_member(self, text: str, members: List[str],
                         aliases: Optional[Dict[str, str]] = None) -> str:
        """从用户输入中解析点名触发的成员（多个名字时取文本中第一个出现者）。

        匹配范围：角色名 / display_name / 下划线名空格形式 / 群聊别名（中文昵称）。
        未命中任何成员时返回空串（表示需要由 AI 判定谁最该发言）。
        """
        if not text or not members:
            return ""
        # 触发词 → 标准角色名
        trigger_map: Dict[str, str] = {}
        for m in members:
            trigger_map[m] = m
            dn = self.display_name(m)
            if dn and dn.strip():
                trigger_map[dn.strip()] = m
            if "_" in m:
                trigger_map[m.replace("_", " ")] = m
        if aliases:
            for k, v in aliases.items():
                trigger_map[str(k).strip()] = str(v).strip()

        hits: List[tuple] = []
        for word, canonical in trigger_map.items():
            if not word:
                continue
            idx = text.find(word)
            if idx != -1:
                hits.append((idx, canonical))
        if not hits:
            return ""
        hits.sort(key=lambda x: x[0])   # 多个名字 → 取文本中第一个出现者
        return hits[0][1]

    def member_intro(self, members: List[str]) -> str:
        """生成群聊成员简档（名字 + 性格），供 API 内部判定发言者时使用。"""
        lines = []
        for m in members:
            card = self.role_card(m)
            personality = (card.get("personality") or "").strip()
            lines.append(f"- {m}" + (f"（{personality}）" if personality else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------ 发言者解析
    def parse_speaker(self, reply: str, members: List[str]) -> str:
        """从群聊回复中解析发言者。解析失败返回空串（由上层兜底）。"""
        if not reply or not members:
            return ""
        for pat in _SPEAKER_PATTERNS:
            m = re.match(pat, reply, re.S)
            if not m:
                continue
            name = (m.group("name") or "").strip()
            if not name:
                continue
            # 容错匹配：精确 / 包含
            for member in members:
                if name == member or member in name or name in member:
                    return member
            return name  # 无法归入成员时返回解析到的名字，供上层处理
        return ""

    def resolve_speaker(self, reply: str, members: List[str],
                        previous: Optional[str] = None) -> str:
        """带兜底的发言者解析。

        解析失败时回退上一个发言者；若仍无（会话首条），则随机选择一名群成员。
        """
        name = self.parse_speaker(reply, members)
        if name in members:
            return name
        if previous:
            return previous
        if members:
            return random.choice(members)
        return ""

    # ------------------------------------------------------------ 立绘 / 动图
    def portrait_path(self, role: str, emotion: Optional[str] = None) -> str:
        """解析角色立绘路径（兼容 <name>-<emotion>.png 与 <name>-.png 两种布局）。

        支持 roles_img/<role>/<role>-<emotion>.png 与 roles_img/<role>-<emotion>.png。
        """
        if emotion is None:
            emotion = self.default_emotion(role)
        base = self._cfg.roles_img_dir
        cands = [
            base / role / f"{role}-{emotion}.png",
            base / role / f"{role}-{emotion}.jpg",
            base / role / f"{role}-{emotion}.jpeg",
            base / role / f"{role}-{emotion}.webp",
            base / f"{role}-{emotion}.png",
            base / f"{role}-{emotion}.jpg",
            base / f"{role}-{emotion}.jpeg",
            base / f"{role}-{emotion}.webp",
        ]
        for c in cands:
            if c.exists():
                return str(c)
        # 默认立绘
        defaults = [
            base / role / f"{role}-.png",
            base / role / f"{role}-.jpg",
            base / f"{role}-.png",
            base / f"{role}-.jpg",
        ]
        for c in defaults:
            if c.exists():
                return str(c)
        return ""

    def pet_gif_path(self, role: str, action: str) -> str:
        """解析桌宠动图路径（支持 roles_desktop/<role>/<role>-<action>.gif）。"""
        base = self._cfg.roles_desktop_dir
        cands = [
            base / role / f"{role}-{action}.gif",
            base / f"{role}-{action}.gif",
        ]
        for c in cands:
            if c.exists():
                return str(c)
        return ""

