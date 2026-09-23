"""
config_loader.py — 全局配置单例（ConfigLoader）

职责：
- 读取并校验 data/config.json，与内置默认值深度合并（用户配置缺失的键自动补全，已配置项永不丢失）
- 提供 get / set / save / reload 读写接口
- 相对路径统一解析为基于项目根目录的绝对路径（Paths 解析）
- API 回退策略：环境变量 OPENAI_API_BASE / OPENAI_API_KEY 优先于配置文件
- 单例模式：ConfigLoader.instance()
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

# 项目根目录：本文件（config_loader.py）所在目录即项目根
ROOT = Path(__file__).resolve().parent


def project_root() -> Path:
    """项目根目录（绝对路径）。"""
    return ROOT


# ---------------------------------------------------------------- 默认配置
DEFAULTS: Dict[str, Any] = {
    "api": {
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "api_key": "",
        "main_model": "gpt-4o",
        "small_base": "",
        "small_key": "",
        "small_model": "gpt-4o-mini",
        # 小模型（工具类非流式调用）是否优先关闭「思考/推理」模式。
        # 思考型模型会把 max_tokens 预算全耗在 reasoning 上 → 正文为空，
        # 记忆降噪/提炼、群聊判定、技能审查等全部静默降级（表现为小模型调不动）。
        "small_thinking_off": True,
        "vision_base": "",
        "vision_key": "",
        "vision_model": "gpt-4o",
        # 生图 API（需求 v5）：留空复用主 API；也可在技能管理器中为每个技能单独设置
        "image_base": "",
        "image_key": "",
        "image_model": "",
    },
    "paths": {
        "roles": "./roles",
        "roles_img": "./roles_img",
        "roles_desktop": "./roles_desktop",
        "theme": "./theme",
        "history": "./history",
        "data": "./data",
        "skills": "./skills",
        "skillspub": "./skillspub",
    },
    "user_avatar": "./data/user_avatar.png",
    "chat": {"temperature": 0.7, "max_tokens": 2048, "stream": True},
    "memory": {
        "trim_threshold": 20,
        "keep_rounds": 10,
        "top_k_long_term": 5,
        "top_k_important": 3,
        "forgetting_interval_days": 30,
        "hit_threshold": 8,
        "cleanup_interval_hours": 24,
        "denoise_enabled": True,
        "denoise_timeout_seconds": 5.0,
        "dedup_high_similarity": 0.15,
        "dedup_partial_similarity": 0.45,
        # ChromaDB Merge 防冲突加固（设为 0 / 极大值可关闭对应保护）
        "merge_max_depth": 3,           # Merge 链最大合并次数（防振荡）
        "merge_drift_threshold": 0.55,  # 语义漂移阈值（余弦距离，超过则改新增）
    },
    "pet": {
        "enabled": False,
        "greeting_interval_min": 30,
        "sleep_hours": [22, 6],
        "focus_mode": False,
        "floating_chat": False,
        "zoom_enabled": True,
    },
    "ui": {
        "blur_background": False,
        "random_proactive": False,
        # 角色扮演：主界面「开启角色扮演」开关 + 设置面板保存的勾选设定卡列表
        # 卡片元素格式："类别/卡片名"，如 "world/小马国"、"roletools/nextstep"
        "roleplay_enabled": False,
        "roleplay_cards": [],
        "window_opacity": 0.92,
        "accent": "#6c8ef5",
        "theme_file": "",
        "icon_path": "",
        # 透明聊天窗口：开启后主聊天界面（面板/气泡/输入框/列表）改为半透明，
        # 按钮与 PNG 图片保持不透明（需求 v4）
        "transparent_chat": False,
        "user_name": "用户",
        "font_family": "Microsoft YaHei",
        # 需求：重新打开软件时停留在上次退出时的角色/模式（单聊或群聊）
        "last_role": "",
        "last_group": "",
        "last_mode": "单聊",
        # 需求：记录打开主界面时间（data/logintime.json + 长期分布），供对话调用；
        # 关闭后不再记录但保留已有数据
        "login_time_enabled": True,
        # 需求：思维链显示开关——开启后模型返回 reasoning_content（DeepSeek/Qwen 等）
        # 或使用 \\@thinking 技能时，聊天气泡内展示思考过程
        "show_thinking": False,
        # 需求：允许调用 skillpub 主开关——关闭后 AI 不主动调用 skill库 技能，
        # \\@skillspub / \\@autoskills 与 #技能名 等调用指令全部失效（默认开启）
        "skillspub_enabled": True,
        # V2-A5：角色自动写日记（每天固定时间由 Cron 触发，AI 生成第一人称日记）
        "auto_diary_enabled": False,
        "auto_diary_time": "23:00",
    },
    # 需求：联网搜索（允许联网开关 + 搜索引擎 API）
    "web": {
        "enabled": False,
        "search_base": "",
        "search_key": "",
        # V2：多引擎搜索 provider 链 + auto-fallback（留空 = 仅原 search_base 单引擎）
        "search_providers": [],
        "search_fallback": True,
        "search_rate_limit": True,
    },

    # ================================================================
    # V2 新增配置段（详见 history_guide/V2/）
    # 说明：旧 config.json 缺失这些键会被 _deep_merge 自动补全，无需迁移。
    # ================================================================

    # V2-B1：通用 Cron 定时引擎（统一桌宠问候/提醒/记忆清理等定时器）
    "cron": {
        "enabled": True,            # 引擎总开关
        "check_interval_sec": 60,   # 到期检查间隔（秒）
        "default_timeout_min": 20,  # 单次任务执行超时（分钟）
        "job_file": "./data/cron_jobs.json",     # 任务存储
        "runs_dir": "./data/cron_runs",          # 运行历史目录
    },

    # V2-B2：心跳巡检（桌宠主动关怀；默认关闭，开启后按间隔巡检数据目录变化）
    "heartbeat": {
        "enabled": False,           # 巡检总开关
        "interval_min": 31,         # 巡检间隔（分钟）
        "watch_dirs": [],           # 额外监听目录（默认监听 history/dailydata/createimage）
        "max_active_per_day": 6,    # 每天最多主动关怀次数（防打扰）
    },

    # V2-B5：统一通知服务
    "notify": {
        "enabled": True,
        # 桌面弹窗时机：always=总是 / when_unfocused=仅主窗口失焦时
        "desktop_focus": "always",
        "idempotency_ttl_min": 10,  # 幂等去重 TTL（分钟）
    },

    # V2-A2：固定记忆（pinned.md，永远注入系统提示词）
    "pinned": {
        "enabled": True,
        "file": "./data/pinned.md",
    },

    # V2-A3：Memory Dream 周期性记忆整合（默认关闭，可手动触发）
    "dream": {
        "enabled": False,
        "interval_hours": 24,
    },

    # V2-A1：记忆传送带（today → daily → week → longterm）
    "memory_compile": {
        "enabled": True,
        "daily_retention_days": 6,  # week 段保留最近 N 个逻辑日
        "max_context_tokens": 2000, # 拼装后 memory.md 上限
    },

    # V2-E1：会话搜索
    "search": {
        "max_results": 30,
    },

    # V2-C1：技能包
    "skill_bundles": {
        "file": "./data/skill_bundles.json",
    },

    # V2-C2：角色卡打包
    "character_card": {
        "max_upload_mb": 80,
        "export_dir": "./data/character_cards",
    },

    # V2-D2：文件读写工具 + 版本历史
    "file_tools": {
        "enabled": True,
        # 允许读写的根目录白名单（相对项目根；其余目录受 PathGuard 只读约束）
        "writable_roots": ["./data", "./dailydata", "./createimage"],
        "history_enabled": True,    # 文件历史版本快照开关
        "history_max_days": 30,     # 快照保留天数
        "history_max_total_mb": 500,
    },
}

# 环境变量回退键
_ENV_API_BASE = "OPENAI_API_BASE"
_ENV_API_KEY = "OPENAI_API_KEY"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并：override 覆盖 base，base 中的缺失键保留默认值。"""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _sanitize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """轻量类型规整：保证数值类配置不会因手误写成字符串导致运行崩溃。"""
    chat = raw.get("chat")
    if isinstance(chat, dict):
        for key, cast in (("temperature", float), ("max_tokens", int)):
            v = chat.get(key)
            if v is not None:
                try:
                    chat[key] = cast(v)
                except (TypeError, ValueError):
                    pass
    mem = raw.get("memory")
    if isinstance(mem, dict):
        for key, cast in (
            ("trim_threshold", int), ("keep_rounds", int),
            ("top_k_long_term", int), ("top_k_important", int),
            ("forgetting_interval_days", int), ("hit_threshold", int),
            ("cleanup_interval_hours", int),
            ("denoise_timeout_seconds", float),
            ("dedup_high_similarity", float), ("dedup_partial_similarity", float),
        ):
            v = mem.get(key)
            if v is not None:
                try:
                    mem[key] = cast(v)
                except (TypeError, ValueError):
                    pass
    return raw


# 可选 pydantic 校验（requirements 中已声明 pydantic，缺失时自动跳过）
try:
    from pydantic import BaseModel  # type: ignore

    class _ConfigModel(BaseModel):  # noqa: D101
        api_base: Optional[str] = None
        api_key: Optional[str] = None
        main_model: Optional[str] = None
        small_model: Optional[str] = None
        vision_model: Optional[str] = None
        paths: Optional[Dict[str, Any]] = None
        user_avatar: Optional[str] = None
        chat: Optional[Dict[str, Any]] = None
        memory: Optional[Dict[str, Any]] = None
        pet: Optional[Dict[str, Any]] = None
except Exception:  # pragma: no cover
    _ConfigModel = None  # type: ignore


class ConfigLoader:
    """配置单例。首次实例化时加载配置文件；后续调用复用同一实例。"""

    _instance: Optional["ConfigLoader"] = None
    _cfg: Dict[str, Any] = {}

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = Path(config_path) if config_path else (ROOT / "data" / "config.json")
        self.reload()

    # ------------------------------------------------------------ 单例
    @classmethod
    def instance(cls, config_path: Optional[str] = None) -> "ConfigLoader":
        """获取全局配置单例。传入 config_path 可显式指定配置文件。"""
        if cls._instance is None:
            cls._instance = cls(config_path)
        elif config_path:
            cls._instance._path = Path(config_path)
            cls._instance.reload()
        return cls._instance

    # ------------------------------------------------------------ 读写
    def reload(self) -> None:
        """重新加载配置文件并与默认值深度合并。"""
        raw: Dict[str, Any] = {}
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception as exc:  # noqa: BLE001
                print(f"[ConfigLoader] 配置读取失败，使用默认值: {exc}")
        merged = _deep_merge(DEFAULTS, raw)
        self._cfg = _sanitize(merged)
        # pydantic 校验（可选）：仅当模型可用且校验通过时覆盖
        if _ConfigModel is not None:
            try:
                _ConfigModel(**self._cfg)
            except Exception as exc:  # noqa: BLE001
                print(f"[ConfigLoader] 配置校验告警（已忽略）: {exc}")

    def save(self) -> None:
        """将当前配置写回 JSON 文件（UTF-8，保留中文）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._cfg, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ 访问器
    def get(self, *keys: str, default: Any = None) -> Any:
        """按路径取值：get("memory", "top_k_long_term")。"""
        cur: Any = self._cfg
        for key in keys:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return default
        return cur

    def set(self, value: Any, *keys: str) -> None:
        """按路径写入：set(0.7, "chat", "temperature")，自动创建中间字典。"""
        cur = self._cfg
        for key in keys[:-1]:
            cur = cur.setdefault(key, {})
        cur[keys[-1]] = value

    @property
    def raw(self) -> Dict[str, Any]:
        """原始配置字典（只读视图，修改请用 set/save）。"""
        return self._cfg

    def snapshot(self) -> Dict[str, Any]:
        """深拷贝当前配置，供设置面板编辑使用。"""
        return deepcopy(self._cfg)


    # ------------------------------------------------------------ API 回退
    def api_base(self) -> str:
        """有效 API Base：环境变量优先，其次配置 api.api_base。"""
        env = os.environ.get(_ENV_API_BASE)
        if env:
            return env
        return (self.get("api", "api_base", default=DEFAULTS["api"]["api_base"])
                or DEFAULTS["api"]["api_base"])

    def api_key(self) -> str:
        """有效 API Key：环境变量优先，其次配置 api.api_key，最后兜底 'EMPTY'。"""
        env = os.environ.get(_ENV_API_KEY)
        if env:
            return env
        return self.get("api", "api_key", default="") or "EMPTY"

    def vision_base(self) -> str:
        """视觉模型独立 API 地址（为空则复用 api_base）。"""
        return (self.get("api", "vision_base", default="") or "")

    def vision_key(self) -> str:
        """视觉模型独立 API Key（为空则复用 api_key）。"""
        return (self.get("api", "vision_key", default="") or "")

    def small_base(self) -> str:
        """小模型（记忆）独立 API 地址（为空则复用主 API）。"""
        return (self.get("api", "small_base", default="") or "")

    def small_key(self) -> str:
        """小模型（记忆）独立 API Key（为空则复用主 API）。"""
        return (self.get("api", "small_key", default="") or "")

    def small_thinking_off(self) -> bool:
        """小模型（工具类非流式调用）是否优先关闭思考模式（默认开启）。"""
        return bool(self.get("api", "small_thinking_off", default=True))

    def main_model(self) -> str:
        return self.get("api", "main_model", default=DEFAULTS["api"]["main_model"]) or "gpt-4o"

    def small_model(self) -> str:
        return self.get("api", "small_model", default=DEFAULTS["api"]["small_model"]) or "gpt-4o-mini"

    def vision_model(self) -> str:
        return self.get("api", "vision_model", default=DEFAULTS["api"]["vision_model"]) or "gpt-4o"

    def image_base(self) -> str:
        """生图 API 独立地址（为空则复用主 API）。"""
        return (self.get("api", "image_base", default="") or "")

    def image_key(self) -> str:
        """生图 API 独立 Key（为空则复用主 API Key）。"""
        return (self.get("api", "image_key", default="") or "")

    def image_model(self) -> str:
        """生图模型（为空则由客户端选择默认模型）。"""
        return (self.get("api", "image_model", default="") or "")

    def image_dir(self) -> Path:
        """生图 / API 生成图片保存目录（默认 <项目>/createimage，可配置）。"""
        return self.path("api", "image_dir", default="./createimage")

    def attach_max_chars(self) -> int:
        """单个附件注入对话的字数上限（附件原文；超出时保留开头 + 结尾）。

        需求（用户反馈「上传的 PDF 识别内容会被截断」）：原上限 12000 字，
        一篇 2.4 万字的论文只能进去一半、5 万字的论文只进 1/4。现默认为
        **32000 字**（约 2~3 万 token，64k 上下文模型也放得下），并可按主模型
        上下文在「设置 → 多模态」里调大（上限 400000 字）。
        """
        try:
            val = int(self.get("api", "attach_max_chars", default=32000) or 32000)
        except Exception:  # noqa: BLE001
            val = 32000
        return max(2000, min(val, 400000))

    def web_enabled(self) -> bool:
        """允许联网开关（全局开关；还需技能/指令触发才会真正搜索）。"""
        return bool(self.get("web", "enabled", default=False))

    def web_search_base(self) -> str:
        """搜索引擎 API 地址（为空则无法搜索）。"""
        return (self.get("web", "search_base", default="") or "").strip()

    def web_search_key(self) -> str:
        """搜索引擎 API Key。"""
        return (self.get("web", "search_key", default="") or "").strip()

    def show_thinking(self) -> bool:
        """思维链显示开关。"""
        return bool(self.get("ui", "show_thinking", default=False))

    def accent(self) -> str:
        """软件主题色（主色调）。"""
        return self.get("ui", "accent", default="#6c8ef5") or "#6c8ef5"

    def user_name(self) -> str:
        """用户称呼（聊天中用户显示的名字）。"""
        return (self.get("ui", "user_name", default="用户") or "用户").strip() or "用户"

    def font_family(self) -> str:
        """界面字体（空 = 系统默认）。"""
        return (self.get("ui", "font_family", default="Microsoft YaHei") or "").strip()

    def pet_zoom_enabled(self) -> bool:
        """桌宠滚轮缩放是否启用（默认开启）。"""
        return bool(self.get("pet", "zoom_enabled", default=True))

    # ------------------------------------------------------------ V2 访问器
    def cron_enabled(self) -> bool:
        """通用 Cron 定时引擎总开关。"""
        return bool(self.get("cron", "enabled", default=True))

    def cron_check_interval_sec(self) -> int:
        """Cron 到期检查间隔（秒）。"""
        return int(self.get("cron", "check_interval_sec", default=60) or 60)

    def cron_default_timeout_min(self) -> int:
        """Cron 单次任务执行超时（分钟）。"""
        return int(self.get("cron", "default_timeout_min", default=20) or 20)

    def cron_job_file(self) -> Path:
        """Cron 任务存储文件路径。"""
        return self.path("cron", "job_file", default="./data/cron_jobs.json")

    def cron_runs_dir(self) -> Path:
        """Cron 运行历史目录。"""
        return self.path("cron", "runs_dir", default="./data/cron_runs")

    def heartbeat_enabled(self) -> bool:
        """心跳巡检开关。"""
        return bool(self.get("heartbeat", "enabled", default=False))

    def heartbeat_interval_min(self) -> int:
        """心跳巡检间隔（分钟）。"""
        return int(self.get("heartbeat", "interval_min", default=31) or 31)

    def heartbeat_watch_dirs(self) -> list:
        """心跳额外监听目录（绝对路径列表；相对路径基于项目根解析）。"""
        raw = self.get("heartbeat", "watch_dirs", default=[]) or []
        out = []
        for d in raw:
            p = Path(str(d))
            out.append(str(p if p.is_absolute() else (ROOT / p)))
        return out

    def heartbeat_max_active_per_day(self) -> int:
        """每天最多主动关怀次数。"""
        return int(self.get("heartbeat", "max_active_per_day", default=6) or 6)

    def notify_enabled(self) -> bool:
        """统一通知服务开关。"""
        return bool(self.get("notify", "enabled", default=True))

    def notify_desktop_focus(self) -> str:
        """桌面弹窗时机：always / when_unfocused。"""
        return (self.get("notify", "desktop_focus", default="always") or "always").strip()

    def notify_idempotency_ttl_min(self) -> int:
        """通知幂等去重 TTL（分钟）。"""
        return int(self.get("notify", "idempotency_ttl_min", default=10) or 10)

    def pinned_enabled(self) -> bool:
        """固定记忆开关。"""
        return bool(self.get("pinned", "enabled", default=True))

    def pinned_file(self) -> Path:
        """固定记忆 pinned.md 路径。"""
        return self.path("pinned", "file", default="./data/pinned.md")

    def dream_enabled(self) -> bool:
        """Memory Dream 周期整合开关。"""
        return bool(self.get("dream", "enabled", default=False))

    def dream_interval_hours(self) -> int:
        """Memory Dream 整合间隔（小时）。"""
        return int(self.get("dream", "interval_hours", default=24) or 24)

    def memory_compile_enabled(self) -> bool:
        """记忆传送带开关。"""
        return bool(self.get("memory_compile", "enabled", default=True))

    def memory_compile_daily_retention_days(self) -> int:
        """week 段保留最近 N 个逻辑日。"""
        return int(self.get("memory_compile", "daily_retention_days", default=6) or 6)

    def memory_compile_max_context_tokens(self) -> int:
        """拼装后 memory.md 的 token 上限。"""
        return int(self.get("memory_compile", "max_context_tokens", default=2000) or 2000)

    def search_max_results(self) -> int:
        """会话搜索结果上限。"""
        return int(self.get("search", "max_results", default=30) or 30)

    def skill_bundles_file(self) -> Path:
        """技能包存储文件路径。"""
        return self.path("skill_bundles", "file", default="./data/skill_bundles.json")

    def character_card_max_upload_mb(self) -> int:
        """角色卡上传大小上限（MB）。"""
        return int(self.get("character_card", "max_upload_mb", default=80) or 80)

    def character_card_export_dir(self) -> Path:
        """角色卡导出目录。"""
        return self.path("character_card", "export_dir", default="./data/character_cards")

    def file_tools_enabled(self) -> bool:
        """文件读写工具开关。"""
        return bool(self.get("file_tools", "enabled", default=True))

    def file_tools_writable_roots(self) -> list:
        """文件工具可读写根目录白名单（相对项目根解析为绝对路径）。"""
        raw = self.get("file_tools", "writable_roots", default=[]) or []
        out = []
        for r in raw:
            p = Path(str(r))
            out.append(str(p if p.is_absolute() else (ROOT / p)))
        return out

    def file_tools_history_enabled(self) -> bool:
        """文件历史版本快照开关。"""
        return bool(self.get("file_tools", "history_enabled", default=True))

    def web_search_providers(self) -> list:
        """多引擎搜索 provider 链（空 = 仅原 search_base 单引擎）。"""
        return list(self.get("web", "search_providers", default=[]) or [])

    def web_search_fallback(self) -> bool:
        """搜索 auto-fallback 开关。"""
        return bool(self.get("web", "search_fallback", default=True))

    def web_search_rate_limit(self) -> bool:
        """搜索限流重试开关。"""
        return bool(self.get("web", "search_rate_limit", default=True))

    # ------------------------------------------------------------ 路径解析
    def path(self, *keys: str, default: str = "") -> Path:
        """将配置中的相对路径解析为项目根目录下的绝对路径。"""
        rel = self.get(*keys, default=default) or default
        p = Path(rel)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def roles_dir(self) -> Path:
        return self.path("paths", "roles", default="./roles")

    @property
    def roles_img_dir(self) -> Path:
        return self.path("paths", "roles_img", default="./roles_img")

    @property
    def roles_desktop_dir(self) -> Path:
        return self.path("paths", "roles_desktop", default="./roles_desktop")

    @property
    def theme_dir(self) -> Path:
        return self.path("paths", "theme", default="./theme")

    @property
    def history_dir(self) -> Path:
        return self.path("paths", "history", default="./history")

    @property
    def data_dir(self) -> Path:
        return self.path("paths", "data", default="./data")

    @property
    def skills_dir(self) -> Path:
        """技能数据目录（tools_list.json / skilltools_information.json）。"""
        return self.path("paths", "skills", default="./skills")

    @property
    def skillspub_dir(self) -> Path:
        """skill库（skillspub）目录（catalog.json 索引 + 每技能一个文件夹/SKILL.md）。"""
        return self.path("paths", "skillspub", default="./skillspub")

    @property
    def skilluserdata_dir(self) -> Path:
        """技能产出目录（skilluserdata/<日期时间>/，一个对话一个项目文件夹）。"""
        return self.path("paths", "skilluserdata", default="./skilluserdata")

    @property
    def conversations_dir(self) -> Path:
        """会话与短期记忆落盘目录。"""
        return self.history_dir / "conversations"

    @property
    def chroma_dir(self) -> Path:
        """ChromaDB 持久化目录。"""
        return self.history_dir / "chroma"

    @property
    def user_avatar(self) -> Path:
        return self.path("user_avatar", default="./data/user_avatar.png")

