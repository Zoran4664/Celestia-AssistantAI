"""
auto_diary.py — AI 自动生成第一人称日记（V2-A5）

参考 HanaAgent（openhanako）lib/diary/diary-writer.ts，适配本项目：
- 每天固定时间（由 Cron 引擎调度）让当前角色把「今天聊了什么」写成第一人称日记；
- 素材：记忆传送带今日摘要（memory_compile daily）+ 最近几天摘要 + 角色人格；
- 存储：dailydata/auto_diary/YYYY-MM-DD.md（正文 + --- 分隔的结构化备忘）；
- 不删除 daily.py 手动日记功能：本模块是新增的「角色自动写日记」模式；
- LLM 走 main 模型异步生成（AsyncWorker），失败静默降级，绝不阻塞主流程。

线程纪律：generate_diary_async 在子线程执行，内部不触碰 QWidget。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from config_loader import ConfigLoader
from role_manager import RoleManager
from memory_compile import MemoryCompile
from utils.logical_time import logical_day_str, date_label

logger = logging.getLogger("AI_DeskMate.AutoDiary")

_AUTO_DIRNAME = "auto_diary"
_MAX_DIARY_CHARS = 1200


class AutoDiary:
    """角色自动日记生成器（单例，普通类，可子线程调用）。"""

    _instance: Optional["AutoDiary"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()

    @classmethod
    def instance(cls) -> "AutoDiary":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 路径
    def auto_dir(self) -> Path:
        """自动日记目录（dailydata/auto_diary/）。"""
        data_dir = self._cfg.data_dir.parent
        return data_dir / "dailydata" / _AUTO_DIRNAME

    def diary_path(self, date_str: Optional[str] = None) -> Path:
        return self.auto_dir() / f"{date_str or logical_day_str()}.md"

    def enabled(self) -> bool:
        return bool(self._cfg.get("ui", "auto_diary_enabled", default=False))

    def diary_time(self) -> str:
        """自动日记触发时间（HH:MM，默认 23:00）。"""
        return (self._cfg.get("ui", "auto_diary_time", default="23:00") or "23:00").strip()

    # ------------------------------------------------------------ 素材
    def _build_materials(self, role: str) -> str:
        """组装素材：角色人格 + 今日摘要 + 最近几天摘要。"""
        parts: List[str] = []
        roles = RoleManager.instance()
        card = roles.role_card(role)
        personality = (card or {}).get("personality") or ""
        if personality:
            parts.append(f"你的性格：{personality}")
        mc = MemoryCompile.instance()
        today = mc.read_daily(logical_day_str())
        if today and (today.get("summary") or "").strip():
            parts.append(f"今天的对话摘要：{today['summary'].strip()}")
        week = mc.assemble_week_text()
        if week:
            parts.append(f"最近几天的对话：\n{week}")
        if not parts:
            return ""
        return "\n\n".join(parts)

    # ------------------------------------------------------------ 生成
    def _build_prompt(self, role: str, materials: str) -> List[Dict[str, str]]:
        sys_p = RoleManager.instance().role_system_prompt(role)
        sys_p += (
            "\n\n请以第一人称写一篇私人日记（不是给用户的汇报），"
            "像在写自己的日记：带上时间感和场景感（今天早上 / 聊到下午的时候…），"
            "把感受、灵感自然地融进正文；不要太正式，可以有语气词和小情绪；"
            "不要用「总的来说」收尾。"
        )
        user = (
            f"今天是 {logical_day_str()}。请根据下面的素材写一篇日记：\n\n"
            f"{materials}\n\n"
            "输出纯 Markdown，两部分：\n"
            "1. 日记正文：第一人称叙事，尽量提到素材里的内容；\n"
            "2. 用 `---` 分隔的结构化备忘：每行 `- **HH:MM** 事件简述`。\n"
            f"总篇幅控制在 400-{_MAX_DIARY_CHARS} 字之间。"
        )
        return [{"role": "system", "content": sys_p},
                {"role": "user", "content": user}]

    def generate_diary_text(self, role: str) -> str:
        """同步生成日记文本（供子线程调用）；失败返回空串。"""
        materials = self._build_materials(role)
        if not materials:
            logger.info("自动日记：今天无对话素材，跳过")
            return ""
        from llm_client import LLMClientPool
        pool = LLMClientPool.instance()
        try:
            text = pool.chat_complete(
                self._build_prompt(role, materials),
                kind="main", max_tokens=800, temperature=0.8)
            return (text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动日记生成失败: %s", exc)
            return ""

    def save_diary(self, role: str, text: str) -> Optional[Path]:
        """保存日记到 dailydata/auto_diary/<date>.md（原子写）。"""
        if not text or not text.strip():
            return None
        date_str = logical_day_str()
        try:
            path = self.diary_path(date_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (f"# 日记 {date_str}\n\n"
                       f"（由 {role} 自动撰写 · {time.strftime('%Y-%m-%d %H:%M:%S')}）\n\n"
                       f"{text.strip()}\n")
            tmp = path.with_suffix(".md.tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
            return path
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动日记保存失败: %s", exc)
            return None

    def generate_diary_async(self, role: str,
                             on_done: Optional[Callable[[Optional[Path]], None]] = None) -> None:
        """异步生成并保存今日日记；完成后回调 on_done(路径或 None)。

        使用 spawn_worker 保活引用，避免 QThread 被 GC 回收导致闪退。
        """
        from utils.async_worker import spawn_worker

        def _run() -> Optional[Path]:
            text = self.generate_diary_text(role)
            return self.save_diary(role, text)

        spawn_worker(
            _run, on_done=on_done,
            on_fail=lambda msg: logger.warning("自动日记任务失败: %s", msg))
