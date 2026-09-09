"""
utils/async_worker.py — 通用 QThread 异步任务基类

设计目标：
- 将任意耗时任务（LLM 调用、数据库写入、文件 IO）放入子线程执行；
- 通过 Qt 信号跨线程回传结果，主线程槽函数在 GUI 事件循环中执行，绝不阻塞界面；
- 继承 QThread，run() 即子线程入口；
- 子线程内禁止创建/访问任何 QWidget，仅允许通过 Signal 与主线程通信。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class AsyncWorker(QThread):
    """通用任务线程：在子线程中执行 task(*args, **kwargs)。"""

    # 信号定义（跨线程时 Qt 自动使用队列连接，槽函数在主线程执行）
    succeeded = Signal(object)     # 正常返回值（任意对象）
    failed = Signal(str)           # 异常信息（"Type: message"）
    progress = Signal(int, str)    # 进度 (percent 0~100, message)

    def __init__(
        self,
        task: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        参数：
            task: 在子线程执行的函数对象
            *args / **kwargs: 传递给 task 的参数
        """
        super().__init__()
        self._task = task
        self._args = args
        self._kwargs = kwargs

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D102 - QThread 子线程入口
        try:
            result: Any = self._task(*self._args, **self._kwargs)
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001 - 兜底捕获，保证线程正常退出
            self.failed.emit(f"{type(exc).__name__}: {exc}")
