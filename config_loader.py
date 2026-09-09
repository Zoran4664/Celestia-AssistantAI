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
    },
    # 需求：联网搜索（允许联网开关 + 搜索引擎 API）
    "web": {
        "enabled": False,
        "search_base": "",
        "search_key": "",
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

