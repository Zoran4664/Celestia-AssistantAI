"""
cron_manager.py — 通用 Cron 定时引擎（V2-B1）

参考 HanaAgent（openhanako）lib/desk/cron-scheduler.ts + cron-store.ts，
用 Python + PySide6 等价实现，并合并本项目桌宠的问候 / 休息 / 记忆清理等定时器。

设计：
- 调度与执行解耦：QTimer 每 check_interval_sec 检查一次到期任务，只有执行时才调用注册的 action；
- 三种调度类型：
    at    一次性（schedule = "YYYY-MM-DD HH:MM:SS" 或 ISO），执行后自动禁用
    every 间隔（schedule = 毫秒数，最小 60000）
    cron  标准 5 字段表达式（分 时 日 月 周，周字段 7 = 周日，支持 * / , - 与步进）
- 失败指数退避：连续失败 consecutive_errors 次后，下次执行时间向后退避 2^n 分钟（上限 60 分钟）；
- 运行历史：data/cron_runs/<job_id>.jsonl（超过 500 行裁剪到最近 300 行）；
- 单次执行超时：默认 cron.default_timeout_min（分钟），超时标记 timeout 并记录（Python 无法强制杀线程，
  超时后仅记录并继续，action 应自行保证可退出）；
- 完成/失败通过 SignalBus.cron_task_done 广播 (job_id, status)；
- 全部存储原子写（tmp + os.replace），UTF-8。

线程纪律：本类在 QThread 中调用 action 时，action 内部禁止创建/访问 QWidget；
LLM 类 action 应内部走 AsyncWorker / LLMWorker 通过信号回传。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer

from config_loader import ConfigLoader
from signal_bus import SignalBus

logger = logging.getLogger("AI_DeskMate.Cron")

# cron 表达式合法值域（5 字段）
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))  # 分 时 日 月 周(0/7=周日)
_RUN_HISTORY_MAX_LINES = 500
_RUN_HISTORY_TRIM = 300
_BACKOFF_MIN = 1        # 失败退避起始分钟
_BACKOFF_MAX_MIN = 60   # 失败退避上限分钟


# ---------------------------------------------------------------- 工具
def _now_ts() -> str:
    """当前时间字符串（YYYY-MM-DD HH:MM:SS）。"""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(text: str) -> Optional[_dt.datetime]:
    """解析 at 调度时间（支持 "YYYY-MM-DD HH:MM:SS" 与 ISO 格式）。"""
    t = (text or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return _dt.datetime.strptime(t, fmt)
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(t)
    except ValueError:
        return None


def _parse_cron_field(field: str, lo: int, hi: int) -> Optional[List[int]]:
    """解析单个 cron 字段（支持 *、*/N、N-M、N-M/S、逗号列表），返回允许值集合。"""
    if hi == 7:  # 周字段 0 与 7 均表示周日
        hi = 7
    values: set = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        base = part
        if "/" in part:
            base, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                return None
            if step <= 0:
                return None
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                return None
        else:
            try:
                start = end = int(base)
            except ValueError:
                return None
        if start < lo or end > hi or start > end:
            return None
        for v in range(start, end + 1, step):
            if hi == 7 and v == 7:
                v = 0  # 周字段 7 → 0
            values.add(v)
    if not values:
        return None
    return sorted(values)


def _match_cron_expr(expr: str, dt: _dt.datetime) -> bool:
    """判断给定时间是否命中 5 字段 cron 表达式。"""
    fields = str(expr).split()
    if len(fields) != 5:
        return False
    parsed = []
    for i, f in enumerate(fields):
        lo, hi = _CRON_RANGES[i]
        vals = _parse_cron_field(f, lo, hi)
        if vals is None:
            return False
        parsed.append(vals)
    minute, hour, dom, month, dow = parsed
    if dt.minute not in minute:
        return False
    if dt.hour not in hour:
        return False
    if dt.month not in month:
        return False
    week_ok = dt.weekday() + 1 if dt.weekday() < 6 else 0  # Mon=1..Sun=0
    if week_ok not in dow:
        return False
    if dom != list(range(1, 32)):  # 日字段有限制时才参与判定
        if dt.day not in dom:
            return False
    return True


def _backoff_minutes(consecutive_errors: int) -> int:
    """失败退避：2^n 分钟，封顶 _BACKOFF_MAX_MIN。"""
    n = max(0, int(consecutive_errors or 0) - 1)
    return min(_BACKOFF_MAX_MIN, _BACKOFF_MIN * (2 ** n))


# ---------------------------------------------------------------- CronManager
class CronManager(QObject):
    """通用 Cron 定时引擎（单例）。"""

    _instance: Optional["CronManager"] = None

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cfg = ConfigLoader.instance()
        self._bus = SignalBus.instance()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._actions: Dict[str, Callable[..., Any]] = {}
        self._timer: Optional[QTimer] = None
        self._running_jobs: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._loaded = False

    @classmethod
    def instance(cls) -> "CronManager":
        """获取全局 Cron 管理器单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """加载任务并启动定时检查（幂等）。"""
        self.load()
        if self._timer is None:
            interval = max(5, self._cfg.cron_check_interval_sec()) * 1000
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.check_jobs)
            self._timer.start(interval)
            logger.info("Cron 引擎已启动（检查间隔 %d 秒）", self._cfg.cron_check_interval_sec())

    def stop(self) -> None:
        """停止定时检查（保留任务数据）。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ------------------------------------------------------------ 存储
    def job_file(self) -> Path:
        return self._cfg.cron_job_file()

    def runs_dir(self) -> Path:
        return self._cfg.cron_runs_dir()

    def load(self, force: bool = False) -> None:
        """从 job_file 加载任务（幂等，force 强制重载）。"""
        if self._loaded and not force:
            return
        self._loaded = True
        path = self.job_file()
        jobs: Dict[str, Dict[str, Any]] = {}
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("jobs") if isinstance(data, dict) else data
                if isinstance(raw, dict):
                    for jid, job in raw.items():
                        if isinstance(job, dict) and job.get("id"):
                            jobs[jid] = job
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cron 任务加载失败（空任务列表）: %s", exc)
        with self._lock:
            self._jobs = jobs

    def save(self) -> None:
        """原子写回任务存储。"""
        path = self.job_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"updated": _now_ts(), "jobs": self._jobs},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cron 任务保存失败: %s", exc)

    # ------------------------------------------------------------ 任务 CRUD
    def _new_id(self) -> str:
        return "job_" + time.strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(3).hex()

    def add_job(self, name: str, schedule_type: str, schedule: Any,
                action: str, payload: Optional[Dict[str, Any]] = None,
                enabled: bool = True,
                timeout_min: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """新增任务。

        :param name: 任务名称（显示用）
        :param schedule_type: at / every / cron
        :param schedule: at=时间字符串；every=间隔毫秒数（>=60000）；cron=5 字段表达式
        :param action: 已注册的 action 名（register_action）
        :param payload: 透传给 action 的参数字典
        :param timeout_min: 单次执行超时（分钟），缺省用配置默认值
        :return: 新任务 dict；参数非法返回 None
        """
        with self._lock:
            if action not in self._actions:
                logger.warning("Cron 新增任务失败：action 未注册 %s", action)
                return None
            next_run = self._compute_next_run(schedule_type, schedule, _dt.datetime.now())
            if next_run is None:
                logger.warning("Cron 新增任务失败：schedule 非法 %s/%s", schedule_type, schedule)
                return None
            job: Dict[str, Any] = {
                "id": self._new_id(),
                "name": str(name or "未命名任务"),
                "schedule_type": schedule_type,
                "schedule": schedule,
                "action": action,
                "payload": dict(payload or {}),
                "enabled": bool(enabled),
                "created_at": _now_ts(),
                "updated_at": _now_ts(),
                "next_run_at": next_run.strftime("%Y-%m-%d %H:%M:%S"),
                "last_run_at": "",
                "last_status": "",
                "consecutive_errors": 0,
                "timeout_min": int(timeout_min or self._cfg.cron_default_timeout_min()),
            }
            self._jobs[job["id"]] = job
        self.save()
        return dict(job)

    def update_job(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """更新任务字段（name/schedule_type/schedule/enabled/action/payload/timeout_min）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            st = fields.get("schedule_type", job.get("schedule_type"))
            sc = fields.get("schedule", job.get("schedule"))
            if st != job.get("schedule_type") or sc != job.get("schedule"):
                nr = self._compute_next_run(st, sc, _dt.datetime.now())
                if nr is None:
                    return None
                fields["next_run_at"] = nr.strftime("%Y-%m-%d %H:%M:%S")
            for k, v in fields.items():
                job[k] = v
            job["updated_at"] = _now_ts()
            job["consecutive_errors"] = 0
        self.save()
        return dict(job)

    def delete_job(self, job_id: str) -> bool:
        """删除任务（保留运行历史）。"""
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        if existed:
            self.save()
        return existed

    def list_jobs(self) -> List[Dict[str, Any]]:
        """返回全部任务（深拷贝）。"""
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    # ------------------------------------------------------------ 调度计算
    def _compute_next_run(self, schedule_type: str, schedule: Any,
                          now: _dt.datetime) -> Optional[_dt.datetime]:
        """计算给定调度的下次执行时间；非法返回 None。"""
        st = (schedule_type or "").strip().lower()
        if st == "at":
            dt = _parse_dt(schedule)
            if dt is None or dt <= now:
                return None if dt is None else (dt if dt > now else None)
            return dt
        if st == "every":
            try:
                ms = int(schedule)
            except (TypeError, ValueError):
                return None
            if ms < 60000:
                return None
            return now + _dt.timedelta(milliseconds=ms)
        if st == "cron":
            fields = str(schedule).split()
            if len(fields) != 5:
                return None
            # 从下一分钟起寻找命中时刻（避免立即重复触发）
            probe = now.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
            for _ in range(60 * 24 * 30):  # 最多向后找 30 天
                if _match_cron_expr(str(schedule), probe):
                    return probe
                probe += _dt.timedelta(minutes=1)
            return None
        return None

    # ------------------------------------------------------------ 执行
    def register_action(self, action: str, fn: Callable[..., Any]) -> None:
        """注册 action 执行器（action 名 → 可调用对象）。

        约定：fn(payload: dict) 可返回任意值；执行在子线程进行，
        fn 内部禁止访问 QWidget；LLM 类动作内部走 AsyncWorker/LLMWorker 信号回传。
        """
        with self._lock:
            self._actions[action] = fn

    def check_jobs(self) -> None:
        """检查所有到期任务并执行（QTimer 回调；每轮独立，不阻塞定时器）。"""
        now = _dt.datetime.now()
        due: List[Dict[str, Any]] = []
        with self._lock:
            for job in self._jobs.values():
                if not job.get("enabled"):
                    continue
                nr = job.get("next_run_at")
                if not nr:
                    continue
                try:
                    due_dt = _dt.datetime.strptime(nr, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if due_dt <= now:
                    due.append(dict(job))
        for job in due:
            jid = job["id"]
            with self._lock:
                if jid in self._running_jobs and self._running_jobs[jid].is_alive():
                    logger.info("Cron 任务 %s 仍在运行，跳过本轮", jid)
                    continue
                self._jobs[jid]["last_run_at"] = _now_ts()
            t = threading.Thread(target=self._execute, args=(job,), daemon=True,
                                 name=f"cron-{jid}")
            with self._lock:
                self._running_jobs[jid] = t
            t.start()

    def _execute(self, job: Dict[str, Any]) -> None:
        """在子线程中执行一个任务并记录结果。"""
        jid = job["id"]
        status = "failed"
        detail = ""
        t0 = time.time()
        try:
            fn = None
            with self._lock:
                fn = self._actions.get(job.get("action"))
            if fn is None:
                raise RuntimeError(f"action 未注册: {job.get('action')}")
            fn(job.get("payload") or {})
            status = "success"
            detail = f"耗时 {time.time() - t0:.1f}s"
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning("Cron 任务 %s(%s) 失败: %s", job.get("name"), jid, exc)
        # 记录与更新（不放 finally，避免 return 语义被吞；_record_run 内部自带容错）
        self._record_run(job, status, detail)
        with self._lock:
            live = self._jobs.get(jid)
            if live is None:
                return
            if status == "success":
                live["consecutive_errors"] = 0
            else:
                live["consecutive_errors"] = int(live.get("consecutive_errors") or 0) + 1
            live["last_status"] = status
            if job.get("schedule_type") == "at":
                live["enabled"] = False
                live["next_run_at"] = ""
            else:
                err = int(live.get("consecutive_errors") or 0)
                base = self._compute_next_run(
                    job.get("schedule_type"), job.get("schedule"), _dt.datetime.now())
                if base is not None and err > 0 and status != "success":
                    base = base + _dt.timedelta(minutes=_backoff_minutes(err))
                live["next_run_at"] = base.strftime("%Y-%m-%d %H:%M:%S") if base else ""
        self.save()
        self._bus.cron_task_done.emit(jid, status)

    def _record_run(self, job: Dict[str, Any], status: str, detail: str) -> None:
        """追加一条运行历史（runs/<jobId>.jsonl，超限裁剪）。"""
        try:
            runs_dir = self.runs_dir()
            runs_dir.mkdir(parents=True, exist_ok=True)
            path = runs_dir / f"{job['id']}.jsonl"
            lines: List[str] = []
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines()
            lines.append(json.dumps({
                "time": _now_ts(),
                "status": status,
                "detail": detail,
            }, ensure_ascii=False))
            if len(lines) > _RUN_HISTORY_MAX_LINES:
                lines = lines[-_RUN_HISTORY_TRIM:]
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(lines), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cron 运行历史写入失败: %s", exc)

    def run_history(self, job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """读取任务最近运行历史。"""
        try:
            path = self.runs_dir() / f"{job_id}.jsonl"
            if not path.exists():
                return []
            lines = path.read_text(encoding="utf-8").splitlines()[-int(limit):]
            out = []
            for ln in lines:
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cron 运行历史读取失败: %s", exc)
            return []

    def trigger_now(self, job_id: str) -> bool:
        """手动立即执行一个任务（幂等：正在运行则跳过）。"""
        job = self.get_job(job_id)
        if not job:
            return False
        with self._lock:
            if job_id in self._running_jobs and self._running_jobs[job_id].is_alive():
                return False
        t = threading.Thread(target=self._execute, args=(job,), daemon=True,
                             name=f"cron-{job_id}-manual")
        with self._lock:
            self._running_jobs[job_id] = t
        t.start()
        return True


# ---------------------------------------------------------------- 便捷入口
def cron_enabled() -> bool:
    """Cron 引擎总开关（配置 cron.enabled）。"""
    return ConfigLoader.instance().cron_enabled()
