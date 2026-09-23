"""
memory_compile.py — 记忆传送带（V2-A1）

参考 HanaAgent（openhanako）lib/memory/compile.ts 的设计思路，
适配本项目的「短期（talk json）+ 长期（ChromaDB）」记忆架构：

传送带：当天对话 → today 摘要（daily/<date>.json）→ week 装配（最近 N 个逻辑日，零 LLM）
        → 对话上下文注入（get_context）

- daily 摘要：每逻辑日一条（凌晨 4 点为日界线），两三句话概括当天聊了什么；
- week 装配：纯文件读取拼装最近 N 个已结束逻辑日的摘要（零 LLM，成本极低）；
- get_context：把「今天 + 最近几天」拼成注入系统提示词的时间叙事文本；
- LLM 摘要生成：走小模型（kind=small）非流式，超时/失败降级为「今天有对话」占位文本，
  绝不阻塞对话主流程；compile_today_async 用 AsyncWorker 在子线程执行。

线程纪律：compile_today_async 在子线程执行，内部禁止触碰 QWidget；
完成后通过 SignalBus.memory_compiled 广播。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config_loader import ConfigLoader
from signal_bus import SignalBus
from utils.logical_time import date_label, logical_day, logical_day_str, recent_logical_days

logger = logging.getLogger("AI_DeskMate.MemoryCompile")

_DAILY_DIRNAME = "daily"
_DAILY_SUFFIX = ".json"


class MemoryCompile:
    """记忆传送带（单例）：daily 摘要存储 + week 装配 + 上下文注入。

    注意：不继承 QObject —— 本类可能被子线程（ChatWorker / AsyncWorker）调用，
    普通单例类避免跨线程创建 QObject 的风险；信号广播统一走 SignalBus。
    """

    _instance: Optional["MemoryCompile"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._bus = SignalBus.instance()
        self._summarizer: Optional[Callable[[str], str]] = None  # 可注入的 LLM 摘要器
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "MemoryCompile":
        """获取全局记忆传送带单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 路径
    def root_dir(self) -> Path:
        """传送带数据根目录（history/memory_compile/）。"""
        return self._cfg.history_dir / "memory_compile"

    def daily_dir(self) -> Path:
        return self.root_dir() / _DAILY_DIRNAME

    def daily_file(self, date_str: str) -> Path:
        return self.daily_dir() / f"{date_str}{_DAILY_SUFFIX}"

    # ------------------------------------------------------------ daily 读写
    def save_daily(self, date_str: str, summary: str,
                   roles: Optional[List[str]] = None) -> None:
        """保存一条逻辑日摘要（原子写）。"""
        date_str = (date_str or logical_day_str())
        data = {
            "date": date_str,
            "summary": (summary or "").strip(),
            "roles": list(roles or []),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            path = self.daily_file(date_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily 摘要保存失败: %s", exc)

    def read_daily(self, date_str: str) -> Optional[Dict[str, Any]]:
        """读取指定逻辑日摘要；不存在返回 None。"""
        try:
            path = self.daily_file(date_str)
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily 摘要读取失败 %s: %s", date_str, exc)
            return None

    def list_daily(self, days: int = 6,
                   now: Optional[Any] = None) -> List[Dict[str, Any]]:
        """读取最近 days 个已结束逻辑日的摘要（不含今天，按日期升序）。"""
        out: List[Dict[str, Any]] = []
        for d in recent_logical_days(days, now):
            entry = self.read_daily(d.strftime("%Y-%m-%d"))
            if entry and (entry.get("summary") or "").strip():
                out.append(entry)
        return out

    # ------------------------------------------------------------ week 装配（零 LLM）
    def assemble_week_text(self, days: int = 0) -> str:
        """纯文件装配最近 N 个逻辑日摘要（零 LLM），用于注入对话上下文。

        :param days: 保留天数；0 = 使用配置 memory_compile.daily_retention_days
        """
        n = days or int(self._cfg.memory_compile_daily_retention_days() or 6)
        entries = self.list_daily(n)
        if not entries:
            return ""
        lines = []
        for e in entries:
            d = e.get("date") or ""
            summary = (e.get("summary") or "").strip()
            if not summary:
                continue
            try:
                from datetime import date as _date
                label = date_label(_date.fromisoformat(d))
            except (ValueError, TypeError):
                label = d
            lines.append(f"{label}（{d}）：{summary}")
        return "\n".join(lines)

    # ------------------------------------------------------------ 今天摘要生成（LLM）
    def _default_summarizer(self) -> Callable[[str], str]:
        """默认摘要器：小模型非流式生成（子线程中调用）。"""

        def _sum(text: str) -> str:
            from llm_client import LLMClientPool
            pool = LLMClientPool.instance()
            try:
                timeout = float(self._cfg.get("memory", "denoise_timeout_seconds",
                                              default=5.0) or 5.0)
                return pool.chat_complete(
                    [{"role": "system",
                      "content": "你是记忆整理助手。请用两三句中文概括下面这段对话"
                                 "（只写事实与话题，不写评价），不要超过 60 字。"},
                     {"role": "user", "content": text}],
                    kind="small", max_tokens=120, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("记忆摘要生成失败（降级）: %s", exc)
                return ""
        return _sum

    def set_summarizer(self, fn: Optional[Callable[[str], str]]) -> None:
        """注入摘要器（测试用）；None 恢复默认。"""
        with self._lock:
            self._summarizer = fn

    def _get_summarizer(self) -> Callable[[str], str]:
        with self._lock:
            return self._summarizer or self._default_summarizer()

    def compile_today_text(self, role: str, group: Optional[str] = None) -> str:
        """生成今天的摘要文本（同步执行，供子线程调用）。

        :return: 摘要文本；失败返回空串（调用方自行降级）
        """
        from memory_pipeline import MemoryPipeline, MODE_NORMAL
        memory = MemoryPipeline.instance()
        try:
            turns = memory.recent_turns(role, group, limit=20, mode=MODE_NORMAL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("传送带取短期记忆失败: %s", exc)
            return ""
        if not turns:
            return ""
        text = "\n".join(f"{t.get('name')}: {t.get('content')}"
                         for t in turns if t.get("content"))
        if not text.strip():
            return ""
        summarizer = self._get_summarizer()
        try:
            summary = summarizer(text) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("传送带摘要器异常: %s", exc)
            return ""
        return summary.strip()

    def compile_today_async(self, role: str, group: Optional[str] = None) -> None:
        """异步执行今天摘要编译（AsyncWorker 子线程），完成后广播 memory_compiled。

        使用 spawn_worker 保活引用，避免 QThread 被 GC 回收导致闪退。
        """
        from utils.async_worker import spawn_worker

        def _run() -> str:
            summary = self.compile_today_text(role, group)
            if summary:
                self.save_daily(logical_day_str(), summary, roles=[role])
            return summary

        def _done(summary: str) -> None:
            if summary:
                self._bus.memory_compiled.emit(f"今日记忆已整理：{summary[:40]}")
            else:
                logger.info("今日无对话或摘要失败，跳过传送带写入")

        spawn_worker(
            _run, on_done=_done,
            on_fail=lambda msg: logger.warning("传送带编译失败: %s", msg))

    # ------------------------------------------------------------ 上下文注入
    def get_context(self, role: str) -> str:
        """返回供系统提示词注入的记忆传送带文本（今天 + 最近几天）。"""
        today = self.read_daily(logical_day_str())
        parts = []
        if today and (today.get("summary") or "").strip():
            parts.append(f"今天的对话：{today['summary'].strip()}")
        week = self.assemble_week_text()
        if week:
            parts.append(f"最近的对话：\n{week}")
        if not parts:
            return ""
        return "【记忆传送带】\n" + "\n".join(parts)
