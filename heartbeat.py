"""
heartbeat.py — 心跳巡检（V2-B2）

参考 HanaAgent（openhanako）lib/desk/heartbeat.ts：让桌宠从「被动应答」变「主动陪伴」。

机制：
- QTimer 每 heartbeat.interval_min 分钟巡检一次监听目录（默认
  history/conversations、dailydata、createimage + 配置的额外目录）；
- 对目录做 mtime 快照差量检测（新增/修改/删除），有变化时生成一条「主动关怀」：
    * 优先通过 SignalBus.request("role:generate_message", scene, hint, fallback)
      由角色模块（pet_manager / ui_manager 注册）以当前角色语气生成；
    * 无人注册时回退到 hint/fallback 文本；
- 主动关怀输出：NotifyService.notify_pet（桌宠气泡）；
- 每天最多 heartbeat.max_active_per_day 次（防打扰），计数存 data/heartbeat_state.json；
- 巡检日志存 data/heartbeat_log.jsonl（保留最近 200 条）；
- 手动触发：trigger_once()。

线程纪律：LLM 生成关怀语内部走 AsyncWorker / LLMWorker（bus.request 的 handler
应自行开线程），Heartbeat 自身只做定时与快照比对，不阻塞 GUI。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer

from config_loader import ConfigLoader
from signal_bus import SignalBus
from notify_service import NotifyService

logger = logging.getLogger("AI_DeskMate.Heartbeat")

_LOG_MAX_LINES = 200
_STATE_FILE = "heartbeat_state.json"
_LOG_FILE = "heartbeat_log.jsonl"


class Heartbeat(QObject):
    """心跳巡检（单例）。"""

    _instance: Optional["Heartbeat"] = None

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cfg = ConfigLoader.instance()
        self._bus = SignalBus.instance()
        self._notify = NotifyService.instance()
        self._snapshot: Dict[str, float] = {}   # 绝对路径 -> mtime
        self._timer: Optional[QTimer] = None
        self._lock = threading.RLock()
        self._busy = False

    @classmethod
    def instance(cls) -> "Heartbeat":
        """获取全局心跳巡检单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """启动巡检定时器（幂等）。"""
        if self._timer is None:
            interval = max(1, int(self._cfg.heartbeat_interval_min() or 31)) * 60 * 1000
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.tick)
            self._timer.start(interval)
            self._refresh_snapshot()
            self._ensure_state_file()
            logger.info("心跳巡检已启动（间隔 %d 分钟）",
                        self._cfg.heartbeat_interval_min())

    def _ensure_state_file(self) -> None:
        """确保状态文件存在（开启巡检即落盘，便于用户感知配置已生效）。"""
        try:
            p = self._state_path()
            if not p.exists():
                self._write_state(self._read_state())
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳状态文件初始化失败: %s", exc)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ------------------------------------------------------------ 目录
    def watch_dirs(self) -> List[Path]:
        """本次巡检的目录列表（默认数据目录 + 配置额外目录）。"""
        out: List[Path] = []
        # 默认监听：会话目录 / data / dailydata / createimage
        for p in (self._cfg.conversations_dir,
                  Path(str(self._cfg.data_dir)),
                  self._cfg.data_dir.parent / "dailydata",
                  self._cfg.data_dir.parent / "createimage"):
            if p.exists():
                out.append(p)
        for d in self._cfg.heartbeat_watch_dirs():
            p = Path(d)
            if p.exists():
                out.append(p)
        # 去重（大小写不敏感）
        seen = set()
        unique = []
        for p in out:
            k = str(p).lower()
            if k not in seen:
                seen.add(k)
                unique.append(p)
        return unique

    # ------------------------------------------------------------ 快照与差量
    def _scan(self) -> Dict[str, float]:
        """扫描目录，返回 {绝对路径: mtime}（仅文件，忽略隐藏/缓存目录与自身产物）。"""
        snap: Dict[str, float] = {}
        skip_names = {"__pycache__", ".git", ".tmp"}
        # 巡检自身写入的产物必须排除，否则自己的写入会把下一轮触发成"有变化"
        skip_files = {_STATE_FILE, _LOG_FILE}
        for root in self.watch_dirs():
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames
                                   if d not in skip_names and not d.startswith(".")]
                    for fn in filenames:
                        if fn in skip_files or fn.endswith(".tmp"):
                            continue
                        fp = os.path.join(dirpath, fn)
                        try:
                            snap[fp] = os.path.getmtime(fp)
                        except OSError:
                            continue
            except OSError as exc:
                logger.warning("心跳扫描目录失败 %s: %s", root, exc)
        return snap

    def _refresh_snapshot(self) -> None:
        with self._lock:
            self._snapshot = self._scan()

    def _diff(self, snap: Dict[str, float]) -> Dict[str, List[str]]:
        """对比快照，返回 {added: [], modified: [], removed: []}。"""
        with self._lock:
            old = self._snapshot
        added = [k for k in snap if k not in old]
        modified = [k for k in snap if k in old and abs(snap[k] - old[k]) > 0.001]
        removed = [k for k in old if k not in snap]
        return {"added": added, "modified": modified, "removed": removed}

    # ------------------------------------------------------------ 巡检
    def tick(self) -> None:
        """定时巡检入口（QTimer 回调）。有变化才生成关怀。"""
        if self._busy:
            return
        self._busy = True
        try:
            snap = self._scan()
            diff = self._diff(snap)
            self._snapshot = snap
            total = len(diff["added"]) + len(diff["modified"]) + len(diff["removed"])
            if total == 0:
                return
            self._maybe_act(diff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳巡检异常: %s", exc)
        finally:
            self._busy = False

    def trigger_once(self) -> None:
        """手动触发一次巡检（立即扫描并可能生成关怀）。"""
        self.tick()

    def _maybe_act(self, diff: Dict[str, List[str]]) -> None:
        """当天关怀次数未超限时，生成并投递主动关怀。"""
        date = time.strftime("%Y-%m-%d")
        state = self._read_state()
        count = state.get(date, 0)
        max_active = max(1, int(self._cfg.heartbeat_max_active_per_day() or 6))
        if count >= max_active:
            logger.info("心跳：今天关怀已达上限 %d 次，跳过", max_active)
            return
        added = diff["added"][:3]
        modified = diff["modified"][:3]
        hint_parts = []
        if added:
            hint_parts.append("发现新文件：" + "、".join(Path(p).name for p in added))
        if modified:
            hint_parts.append("有文件更新：" + "、".join(Path(p).name for p in modified))
        hint = "；".join(hint_parts) or "检测到文件变化"
        scene = "heartbeat"
        fallback = f"看到工作区有变化了（{hint}），需要我帮忙看看吗？"
        # 两级关怀路径（避免双显示与主线程阻塞）：
        # 1) 同步 request：有角色模块注册同步生成器时用之；
        # 2) 异步信号 heartbeat_care_requested：由 pet_manager 等连接，用 LLMWorker
        #    以角色语气异步生成并展示；此时 heartbeat 不投递 fallback（防双显示）。
        sync_text = self._bus.request("role:generate_message", scene, hint, fallback)
        if sync_text is not None and str(sync_text).strip():
            self._notify.notify_pet(str(sync_text))
        else:
            self._bus.heartbeat_care_requested.emit(hint)
        state[date] = count + 1
        self._write_state(state)
        self._log({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "hint": hint,
                   "count": count + 1})
        logger.info("心跳主动关怀已触发: %s", hint)

    # ------------------------------------------------------------ 状态与日志
    def _state_path(self) -> Path:
        return self._cfg.data_dir / _STATE_FILE

    def _log_path(self) -> Path:
        return self._cfg.data_dir / _LOG_FILE

    def _read_state(self) -> Dict[str, int]:
        try:
            p = self._state_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳状态读取失败: %s", exc)
        return {}

    def _write_state(self, state: Dict[str, int]) -> None:
        try:
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳状态写入失败: %s", exc)

    def _log(self, entry: Dict[str, Any]) -> None:
        try:
            p = self._log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            lines: List[str] = []
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()
            lines.append(json.dumps(entry, ensure_ascii=False))
            if len(lines) > _LOG_MAX_LINES:
                lines = lines[-100:]
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(lines), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳日志写入失败: %s", exc)
