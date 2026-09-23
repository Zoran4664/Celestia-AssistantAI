"""
roleplay_preset.py — 角色扮演预设模型（酒馆式参数管理 + 规则卡 / 正则 / 世界书）

设计来源：融合 SillyTavern 预设（氤yin / 潮汐 / 浮生 / 灰魂 / TGbreak）的通用机制，
落地为本项目的「角色扮演预设」：

1. 采样参数管理（酒馆式）
   每个参数独立开关（on）+ 数值（value）：未开启的项沿用全局 config.json，
   开启后才覆盖。这样既能一键全接管，也能只调温度。

2. 规则卡（rules）
   对应 ST 的 prompt 列表 + prompt_order：有序、可启停、可定义变量。
   - mode = text   ：纯文本规则，按 order 顺序直接拼进系统提示词
   - mode = setvar ：把 content 写入变量 {{setvar::name::content}}（单选覆盖）
   - mode = addvar ：把 content 追加到变量 {{addvar::name::content}}（多选累加）
   规则里可用 {{getvar::name}} 消费变量 —— 即「定义与消费分离」。

3. 正则脚本（regex）
   对应 ST 的 regex_scripts：
   - placement: 1=用户消息出向前；2=AI 回复（模型层/展示层）
   - markdown_only：只在展示层生效（给用户看）
   - prompt_only ：只在送模型层生效（给模型/记忆看）
   - min_depth / max_depth：消息深度（0 = 最新一条），实现「近 N 楼」策略
   两者都不勾 = 两层都生效；不能同时勾。

4. 世界书（lorebook）
   对应 ST 的 world info：关键字触发、constant 常驻、depth 注入深度、
   order 排序、position 插入位置、probability 概率、enabled 启停。

5. 隔离（isolation）
   - isolate_normal：角色扮演记忆/会话与非角色扮演严格隔离（恒开）
   - per_session   ：每个新会话独立（开启后，各角色扮演会话之间也不共享记忆）

6. 卡片槽位（cards）
   沿用 rolemanger/style、rolemanger/world、rolepersonal、roletools 四类设定卡，
   格式 "类别/卡片名"，按 inject_order 顺序注入。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.RolePlayPreset")

ROOT = Path(__file__).resolve().parent                 # roleplay/
PROJECT_ROOT = ROOT.parent
DEFAULT_PRESET_PATH = PROJECT_ROOT / "data" / "roleplay_preset.json"

PRESET_VERSION = 1

# ---------------------------------------------------------------------------
# 采样参数规格（酒馆式）
# kind: float / int / choice / bool
# ---------------------------------------------------------------------------
SAMPLING_SPEC: List[Dict[str, Any]] = [
    {"key": "temperature", "label": "温度 temperature", "kind": "float",
     "min": 0.0, "max": 2.0, "step": 0.01, "default": 0.9,
     "tip": "越高越发散；文学创作常用 0.9~1.1"},
    {"key": "top_p", "label": "核采样 top_p", "kind": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "default": 1.0,
     "tip": "1.0 = 不截断尾部"},
    {"key": "top_k", "label": "候选上限 top_k", "kind": "int",
     "min": 0, "max": 2000, "step": 1, "default": 0,
     "tip": "0 = 关闭；64 左右更稳"},
    {"key": "min_p", "label": "最小概率 min_p", "kind": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.0,
     "tip": "0 = 关闭"},
    {"key": "top_a", "label": "top_a", "kind": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.0,
     "tip": "0 = 关闭"},
    {"key": "repetition_penalty", "label": "重复惩罚 repetition_penalty", "kind": "float",
     "min": 0.0, "max": 2.0, "step": 0.01, "default": 1.0,
     "tip": "1.0 = 关闭"},
    {"key": "frequency_penalty", "label": "频率惩罚 frequency_penalty", "kind": "float",
     "min": -2.0, "max": 2.0, "step": 0.01, "default": 0.0,
     "tip": "正值降低重复用词"},
    {"key": "presence_penalty", "label": "存在惩罚 presence_penalty", "kind": "float",
     "min": -2.0, "max": 2.0, "step": 0.01, "default": 0.0,
     "tip": "正值鼓励新话题"},
    {"key": "max_tokens", "label": "单次最大输出 max_tokens", "kind": "int",
     "min": 64, "max": 131072, "step": 64, "default": 4096,
     "tip": "长篇正文建议 8000+"},
    {"key": "max_context", "label": "上下文上限 max_context", "kind": "int",
     "min": 1024, "max": 2000000, "step": 1024, "default": 32768,
     "tip": "仅作提示，实际截断由后端决定"},
    {"key": "seed", "label": "随机种子 seed", "kind": "int",
     "min": -1, "max": 2147483647, "step": 1, "default": -1,
     "tip": "-1 = 每次随机"},
    {"key": "reasoning_effort", "label": "推理强度 reasoning_effort", "kind": "choice",
     "options": ["", "low", "medium", "high"], "default": "",
     "tip": "留空 = 不指定（仅推理模型有效）"},
]

# OpenAI 标准字段（直接传参），其余走 extra_body
_OPENAI_NATIVE = {
    "temperature", "top_p", "max_tokens", "seed",
    "frequency_penalty", "presence_penalty",
}

# ---------------------------------------------------------------------------
# 默认预设
# ---------------------------------------------------------------------------
def _default_sampling() -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for spec in SAMPLING_SPEC:
        params[spec["key"]] = {"on": False, "value": spec["default"]}
    return {"override": False, "params": params}


DEFAULT_PRESET: Dict[str, Any] = {
    "version": PRESET_VERSION,
    "enabled": False,                      # 预设总开关（主界面「开启角色扮演」联动）
    "sampling": _default_sampling(),
    "assembly": {
        # 卡片注入顺序（类别；与 roleplaytool.RolePlayManager 的目录名一致）
        "inject_order": ["rolepersonal", "world", "style", "roletools"],
        # 注入到上下文的最近对话轮数
        "history_turns": 5,
        # 输出骨架：支持 {{getvar::xxx}}；留空表示不使用骨架
        "output_skeleton": "",
        # 角色扮演模式下是否禁用技能 / 联网 / skillspub（只保留角色 + 设定工具）
        "exclusive_tools": True,
        # 是否仍然注入长期记忆（角色扮演命名空间内）
        "use_memory": True,
        # 是否仍然要求 〖情绪标签〗
        "require_emotion_tag": True,
        # 是否注入时间/作息等日常上下文
        "use_daily_context": False,
        # 主角设定：你在扮演里使用的身份卡（rolepersonal 目录下单选；空=不使用）
        "user_persona": "",
        # OOC / 破限模式：允许出戏、元叙事、突破世界观限制，
        # 但仍参考世界书 / 世界观的**背景设定**（地名、势力、历史、术语保持一致）
        "ooc_mode": False,
    },
    "isolation": {
        # 隔离非角色扮演对话（恒开：角色扮演读不到日常记忆，日常也读不到扮演记忆）
        "isolate_normal": True,
        # 每个对话独立：开启后，每个新会话各自一份记忆，互不共享
        "per_session": False,
    },
    "cards": {"style": [], "world": [], "rolepersonal": [], "roletools": []},
    "rules": [],
    "regex": [],
    "lorebook": [],
}

# 类别 → 目录（与 roleplaytool.RolePlayManager 保持一致）
CATEGORY_LABELS: Dict[str, str] = {
    "style": "对话风格",
    "world": "世界观",
    "rolepersonal": "个人设定",
    "roletools": "角色工具",
}

# 旧版类别名 → 现行类别名（读取旧预设文件时自动迁移）
_CATEGORY_ALIASES: Dict[str, str] = {
    "personal": "rolepersonal",
    "tools": "roletools",
}


def _migrate_categories(data: Dict[str, Any]) -> None:
    """把旧版 personal/tools 类别键迁移为 rolepersonal/roletools。"""
    cards = data.get("cards")
    if isinstance(cards, dict):
        for old, new in _CATEGORY_ALIASES.items():
            if old in cards:
                merged = list(cards.get(new) or []) + list(cards.pop(old) or [])
                cards[new] = sorted(set(str(x) for x in merged))
    order = data.get("assembly")
    if isinstance(order, dict):
        inj = order.get("inject_order")
        if isinstance(inj, list):
            order["inject_order"] = [
                _CATEGORY_ALIASES.get(str(x), str(x)) for x in inj]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class RolePlayPreset:
    """角色扮演预设读写（线程安全，单例可选）。

    落盘到 data/roleplay_preset.json；读取时与 DEFAULT_PRESET 做深合并，
    缺字段自动补齐，保证旧版本配置可直接升级。
    """

    _instance: Optional["RolePlayPreset"] = None
    _lock = threading.RLock()

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_PRESET_PATH
        self._data: Dict[str, Any] = deepcopy(DEFAULT_PRESET)
        #: 磁盘上**实际写入**的字段（未经 DEFAULT_PRESET 深合并补全）——用于区分
        #  「用户明确清空了勾选」与「压根没配置过」，避免没勾的卡片被回退加载。
        self._raw: Dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------ 单例
    @classmethod
    def instance(cls) -> "RolePlayPreset":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ------------------------------------------------------------ 读写
    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Dict[str, Any]:
        with self._lock:
            data: Dict[str, Any] = {}
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
                except Exception as exc:  # noqa: BLE001
                    logger.warning("角色扮演预设读取失败（使用默认）: %s", exc)
            self._raw = data
            self._data = self._merge(deepcopy(DEFAULT_PRESET), data)
            _migrate_categories(self._data)
            self._migrate_legacy_cards()
            return self._data

    def _migrate_legacy_cards(self) -> None:
        """一次性迁移：旧版把设定卡勾选只存在 config.json 的 ui.roleplay_cards。

        规则（只做一次，之后永久按「预设为唯一权威来源」严格加载）：
          * 预设里 cards 全空 + config 里有旧勾选 → 搬进预设并落盘；
          * 迁移完成后打标记，此后用户清空勾选即代表「一张都不加载」，
            不再回退读取 config（否则没勾的卡片会被偷偷加载）。
        """
        meta = self._data.setdefault("meta", {})
        if not isinstance(meta, dict) or meta.get("legacy_cards_migrated"):
            return
        cards = self._data.get("cards") or {}
        has_sel = any(cards.get(c) for c in ("style", "world",
                                             "rolepersonal", "roletools"))
        if not has_sel:
            try:
                from config_loader import ConfigLoader
                legacy = ConfigLoader.instance().get(
                    "ui", "roleplay_cards", default=[]) or []
            except Exception:  # noqa: BLE001
                legacy = []
            if legacy:
                alias = {"personal": "rolepersonal", "tools": "roletools"}
                sel: Dict[str, List[str]] = {}
                for key in legacy:
                    cat, _, name = str(key).partition("/")
                    cat = alias.get(cat, cat)
                    if cat and name:
                        sel.setdefault(cat, []).append(name)
                # 对话风格单选：旧配置里若残留多张，只保留第一张
                if len(sel.get("style") or []) > 1:
                    sel["style"] = sel["style"][:1]
                merged = dict(cards)
                for cat, names in sel.items():
                    merged[cat] = names
                self._data["cards"] = merged
                logger.info("已将旧版设定卡勾选迁移到角色扮演预设：%s",
                            sorted(sel.keys()))
        meta["legacy_cards_migrated"] = True
        self.save()

    def save(self, data: Optional[Dict[str, Any]] = None) -> bool:
        with self._lock:
            if data is not None:
                self._data = self._merge(deepcopy(DEFAULT_PRESET), data)
                _migrate_categories(self._data)
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                # 落盘后该字段即视为「用户显式配置过」
                self._raw = deepcopy(self._data)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色扮演预设保存失败: %s", exc)
                return False

    def data(self) -> Dict[str, Any]:
        with self._lock:
            return self._data

    def has_own(self, *keys: str) -> bool:
        """判断预设文件里是否**显式写过**该字段（而非 DEFAULT_PRESET 补的默认值）。

        用于严格区分「用户把某类卡片全部取消勾选」（→ 视为 0 张，不再回退旧配置）
        与「用户从未配置过该字段」（→ 允许一次性读取旧版配置做迁移）。
        """
        with self._lock:
            cur: Any = self._raw
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return False
                cur = cur[k]
            return True

    def get(self, *keys: str, default: Any = None) -> Any:
        """按路径取值：get("sampling", "override")。"""
        with self._lock:
            cur: Any = self._data
            for k in keys:
                if not isinstance(cur, dict):
                    return default
                cur = cur.get(k)
                if cur is None:
                    return default
            return cur

    def set(self, value: Any, *keys: str) -> None:
        with self._lock:
            if not keys:
                return
            cur: Dict[str, Any] = self._data
            for k in keys[:-1]:
                nxt = cur.get(k)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[k] = nxt
                cur = nxt
            cur[keys[-1]] = value

    # ------------------------------------------------------------ 深合并
    @staticmethod
    def _merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        out = deepcopy(base)
        for k, v in (patch or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                # sampling.params 这类「键 -> 结构」字典需逐项合并
                out[k] = RolePlayPreset._merge(out[k], v)
            else:
                out[k] = deepcopy(v)
        return out

    # ------------------------------------------------------------ 采样参数
    def sampling_overrides(self) -> Dict[str, Any]:
        """返回开启了的采样参数（未开启的不出现），供 LLM 调用覆盖全局配置。"""
        smp = self.get("sampling", default={}) or {}
        if not smp.get("override"):
            return {}
        params = smp.get("params") or {}
        out: Dict[str, Any] = {}
        for spec in SAMPLING_SPEC:
            key = spec["key"]
            item = params.get(key) or {}
            if not item.get("on"):
                continue
            val = item.get("value", spec["default"])
            if spec["kind"] == "choice":
                if str(val).strip():
                    out[key] = str(val).strip()
                continue
            if spec["kind"] == "int":
                try:
                    out[key] = int(val)
                except Exception:  # noqa: BLE001
                    continue
                continue
            try:
                out[key] = float(val)
            except Exception:  # noqa: BLE001
                continue
        return out

    # ------------------------------------------------------------ 便捷构造
    @staticmethod
    def new_rule(name: str = "新规则", mode: str = "text",
                 variable: str = "", content: str = "") -> Dict[str, Any]:
        return {
            "id": _new_id("rule"),
            "name": name,
            "enabled": True,
            "mode": mode,                 # text / setvar / addvar
            "variable": variable,
            "content": content,
        }

    @staticmethod
    def new_regex(name: str = "新正则") -> Dict[str, Any]:
        return {
            "id": _new_id("re"),
            "name": name,
            "enabled": True,
            "find": "",
            "replace": "",
            "placement": [1, 2],          # 1=用户消息；2=AI 回复
            "markdown_only": False,       # 仅展示层
            "prompt_only": False,         # 仅模型层
            "min_depth": 0,
            "max_depth": 0,               # 0 = 不限
        }

    @staticmethod
    def new_lore(name: str = "新条目") -> Dict[str, Any]:
        return {
            "id": _new_id("lore"),
            "name": name,
            "comment": name,
            "enabled": True,
            "keys": [],                   # 触发词（主）
            "secondary_keys": [],         # 触发词（次）
            "content": "",
            "constant": False,            # 常驻（不触发也注入）
            "selective": True,            # 非 constant 时按关键字命中
            "order": 100,                 # 越小越靠前
            "position": 1,                # 0=角色定义前；1=角色定义后；2=深度锚点后
            "depth": 4,                   # 注入深度（0 = 最新）
            "probability": 100,           # 命中概率 %
        }
