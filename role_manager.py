"""
role_manager.py — 角色库 / 群聊预设解析（RoleManager 单例）

- 扫描 roles/ 目录解析角色卡（roles.json）与情感映射（emotion.json）
- 群聊发言者解析（parse_speaker）：正则兜底 + 容错机制，兼容
  「[角色A]: 内容」「角色A：内容」「角色A: 内容」「角色A 说：内容」
  「“内容” ——角色A」等多种变体（容忍多余空格、全角/半角冒号）；
  **名字一律先归一化再比较**（``Twilight_Sparkle`` ≡ ``Twilight Sparkle``，
  见 normalize_role_name / match_member），否则模型用空格写法时解析与剥离全部失配
- 情感标签归一化：从回复文本提取 [emotion:x] / 【xx】 标签，映射为标准情感
- 立绘 / 桌宠动图路径解析（兼容多目录布局）
"""

from __future__ import annotations

import json
import logging
import random
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# ================================================================
# 角色名归一化 / 归属匹配 —— 全项目「名字比较」的唯一口径
# ================================================================
# 需求（用户 2026-09-23）：群聊出现「发言人与角色名错位」+「[角色名]: 前缀原样显示」：
#     [Twilight Sparkle]: (优雅地行了个屈膝礼)下午好！……
# 这条本该记在 Twilight_Sparkle 名下，界面却显示成 Applejack 发言，
# 而且「[Twilight Sparkle]:」这几个字直接留在了气泡正文里。
#
# 根因：**同一个角色存在两套名字写法**——
#   * 成员表 / 角色目录名是**下划线**形式：roles/Twilight_Sparkle、
#     roles/group.json 的 members = ["Twilight_Sparkle", …]；
#   * 模型输出是**空格**形式：[Twilight Sparkle]:（角色卡 system_prompt 里
#     自称「你是Twilight Sparkle」，模型照着念就会写成空格）。
# 而发言者解析（parse_speaker）与前缀剥离（_strip_group_prefix）都用
# `==` / `re.escape(name)` 做**逐字符**匹配 → 必然失配，于是：
#   ① parse_speaker 归不到成员 → resolve_speaker 回退「上一发言者」= 张冠李戴；
#   ② 前缀剥离不中 → [Twilight Sparkle]: 原样显示在气泡里。
#
# 规则：**任何**把「文本里的名字」映射到「成员标准名」的地方，都必须先过
# :func:`normalize_role_name` 再比较，覆盖 下划线 / 空格 / 连字符 / 中点 /
# 全角半角 / 首尾括号 / 大小写 的差异。新增解析或剥离代码时照此办理。
_NAME_SEP_RE = re.compile(r"[\s_\-–—·・.．、]+")
_NAME_WRAP_RE = re.compile(r"^[\s\[【（(「『“\"'*#]+|[\s\]】）)」』”\"'*#]+$")

#: 归一化子串匹配时两边都至少要这么长 —— 防止单字母/单字误命中某成员
_MIN_FUZZY_LEN = 2


def normalize_role_name(name: Any) -> str:
    """角色名**归一化键**（只用于比较，绝不用于显示）。

    例：``Twilight_Sparkle`` ≡ ``Twilight Sparkle`` ≡ ``twilight-sparkle`` ≡
    ``Ｔｗｉｌｉｇｈｔ＿Ｓｐａｒｋｌｅ`` ≡ ``[Twilight Sparkle]``。

    步骤：NFKC（全角→半角）→ 去首尾括号/引号/装饰符 → 去分隔符（下划线/空格/
    中点/连字符等）→ 折叠大小写。空输入返回空串。
    """
    s = unicodedata.normalize("NFKC", str(name or ""))
    s = _NAME_WRAP_RE.sub("", s)
    s = _NAME_SEP_RE.sub("", s)
    return s.casefold()


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
        """角色在侧边栏/顶部选择框显示的名称（**完整显示，不做 … 截断**）。

        优先使用 data/role_abbr.json 的映射值（如 Rainbow_Dash -> RD 或
        Rainbow_Dash云宝黛茜）；无映射时用角色显示名。
        需求：主界面角色名必须完整 —— 调用方（选择框）按最长条目自适应宽度，
        因此这里不再按 SIDEBAR_MAX_WIDTH 截断（那会出现「Rainbow_Da…」）。
        """
        return self._abbr_map().get(name) or self.display_name(name)

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

        匹配范围：角色名 / display_name / 下划线名空格形式 / 群聊别名（中文昵称）；
        都没命中时再按 :func:`normalize_role_name` 归一化键扫一遍（大小写、
        分隔符写法无关，如 ``TwilightSparkle``）。
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
        if hits:
            hits.sort(key=lambda x: x[0])   # 多个名字 → 取文本中第一个出现者
            return hits[0][1]
        # 兜底：上面没命中时按**归一化键**再扫一遍 —— 覆盖「TwilightSparkle」
        # 「twilight  sparkle」这类分隔符/大小写写法（用户 2026-09-23：
        # 名字匹配一律走归一化口径）。位置按归一化文本中的下标近似排序。
        norm_text = normalize_role_name(text)
        if not norm_text:
            return ""
        cands: List[Tuple[int, str]] = []
        for m in members:
            for form in (m, self.display_name(m)):
                nm = normalize_role_name(form)
                if nm and nm in norm_text:
                    cands.append((norm_text.find(nm), m))
                    break
        for k, v in (aliases or {}).items():
            nm = normalize_role_name(k)
            if nm and v in members and nm in norm_text:
                cands.append((norm_text.find(nm), v))
        if not cands:
            return ""
        cands.sort(key=lambda x: x[0])
        return cands[0][1]

    def member_intro(self, members: List[str]) -> str:
        """生成群聊成员简档（名字 + 性格），供 API 内部判定发言者时使用。"""
        lines = []
        for m in members:
            card = self.role_card(m)
            personality = (card.get("personality") or "").strip()
            lines.append(f"- {m}" + (f"（{personality}）" if personality else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------ 发言者解析
    def match_member(self, raw: str, members: List[str],
                     aliases: Optional[Dict[str, str]] = None) -> str:
        """把**任意写法**的角色名归到群成员标准名；归不到返回空串。

        （「名字比较」的唯一口径，理由见模块顶部「角色名归一化 / 归属匹配」）

        匹配优先级：
          ① 归一化后**完全相等** —— 覆盖 成员名 / display_name / 别名键 / 别名值；
          ② 归一化后**互为子串**（如 ``Twilight`` ↔ ``Twilight_Sparkle``）——
             两边都 ≥ ``_MIN_FUZZY_LEN`` 字符才允许；多个候选取「在原文里出现
             更早」者，位置相同时取更长者（不让 members 的书写顺序决定结果）；
          ③ 都不中 → 空串，兜底策略交给调用方（回退上一发言者 / 随机）。
        """
        if not raw or not members:
            return ""
        key = normalize_role_name(raw)
        if not key:
            return ""
        alias_map = {str(k).strip(): str(v).strip()
                     for k, v in (aliases or {}).items()}
        # ① 归一化后精确相等
        for m in members:
            if normalize_role_name(m) == key:
                return m
            dn = self.display_name(m)
            if dn and normalize_role_name(dn) == key:
                return m
        for k, v in alias_map.items():
            if v in members and normalize_role_name(k) == key:
                return v
        # ② 归一化后互为子串（两边都要够长，避免单字母乱命中）
        cands: List[Tuple[int, int, str]] = []
        for m in members:
            for form in (m, self.display_name(m)):
                nm = normalize_role_name(form)
                if len(nm) < _MIN_FUZZY_LEN:
                    continue
                if (nm in key or key in nm) and min(len(nm), len(key)) >= _MIN_FUZZY_LEN:
                    pos = key.find(nm)
                    cands.append((pos if pos >= 0 else len(key), -len(nm), m))
                    break
        if not cands:
            return ""
        cands.sort()
        return cands[0][2]

    def parse_speaker(self, reply: str, members: List[str],
                      aliases: Optional[Dict[str, str]] = None) -> str:
        """从群聊回复中解析发言者（**成员标准名**；解析失败返回空串）。

        需求（用户 2026-09-23）：名字必须走 :meth:`match_member` 的**归一化**口径。
        旧实现用 ``name == member or member in name or name in member`` 逐字符比较，
        模型写「[Twilight Sparkle]:」而成员表是「Twilight_Sparkle」时直接失配，
        上层于是回退成「上一发言者」，把这条回复挂到了错误角色名下。

        解析不出成员名时**返回原文里解析到的名字**（可能是模型自造名），
        由 :meth:`resolve_speaker` 决定兜底策略。
        """
        if not reply or not members:
            return ""
        for pat in _SPEAKER_PATTERNS:
            m = re.match(pat, reply, re.S)
            if not m:
                continue
            name = (m.group("name") or "").strip()
            if not name:
                continue
            return self.match_member(name, members, aliases) or name
        return ""

    def resolve_speaker(self, reply: str, members: List[str],
                        previous: Optional[str] = None,
                        aliases: Optional[Dict[str, str]] = None) -> str:
        """带兜底的发言者解析（返回成员标准名）。

        顺序：解析到成员 → 直接用；解析到名字但归不进成员 → 再做一次宽松归属
        匹配（:meth:`match_member` 的子串兜底）；仍不行才回退上一发言者——
        **且上一发言者必须仍在成员表内**（切换群聊后不该沿用旧角色），
        最后随机选一名成员。
        """
        if not members:
            return ""
        name = self.parse_speaker(reply, members, aliases)
        if name in members:
            return name
        hit = self.match_member(name, members, aliases)
        if hit:
            return hit
        if previous in members:
            if name:
                # 记一条日志：模型写了名字却归不进成员表，属可疑情况，便于排查
                logger.warning("群聊发言者「%s」无法归入成员表 %s → 回退上一发言者 %s",
                               name, members, previous)
            return previous
        if previous:
            logger.warning("上一发言者「%s」已不在成员表 %s → 随机选一位",
                           previous, members)
        return random.choice(members)

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

