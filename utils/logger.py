"""
utils/logger.py — 应用日志封装

功能：
- 控制台 + 文件双通道输出，文件按大小轮转（5MB × 3）
- UTF-8 编码，兼容中文日志
- logging 模块本身线程安全，可在 QThread 子线程中直接使用
- 支持 --debug 切换 DEBUG 级别
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# 默认日志目录：本文件同级的 logs/
_DEFAULT_LOG_DIR: Path = Path(__file__).resolve().parent / "logs"


class AppLogger:
    """应用日志单例。首次实例化时初始化，后续调用直接复用已有 Logger。"""

    _instance: Optional["AppLogger"] = None
    _logger: Optional[logging.Logger] = None

    def __new__(cls, *args: object, **kwargs: object) -> "AppLogger":
        """单例：无论实例化多少次，只保留一个实例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, debug: bool = False, log_dir: Optional[Path] = None) -> None:
        """初始化日志系统（幂等：已初始化则直接返回）。"""
        if self._logger is not None:
            return
        self._debug: bool = debug
        self._log_dir: Path = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._logger = self._build_logger(debug)

    # ------------------------------------------------------------------
    def _build_logger(self, debug: bool) -> logging.Logger:
        """构建 Logger：控制台通道 + 轮转文件通道。"""
        logger = logging.getLogger("AI_DeskMate")
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        logger.propagate = False

        # 控制台通道（短格式，适合终端实时查看）
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
            )
        )
        logger.addHandler(console)

        # 文件通道（长格式，含文件名与行号，便于排查）
        file_handler = RotatingFileHandler(
            self._log_dir / "app.log",
            maxBytes=5 * 1024 * 1024,  # 单文件上限 5MB
            backupCount=3,             # 最多保留 3 个历史文件
            encoding="utf-8",          # 中文日志不乱码
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
            )
        )
        logger.addHandler(file_handler)
        return logger

    # ------------------------------------------------------------------
    @staticmethod
    def get() -> logging.Logger:
        """获取全局 Logger（未初始化时以默认参数自动初始化）。"""
        instance = AppLogger._instance
        if instance is None or instance._logger is None:
            instance = AppLogger()
        assert instance._logger is not None, "logger 初始化失败"
        return instance._logger

    @classmethod
    def init(cls, debug: bool = False) -> "AppLogger":
        """显式初始化（供 main.py 启动时调用，返回单例）。"""
        return cls(debug=debug)


def get_logger() -> logging.Logger:
    """模块级便捷函数：返回全局 Logger。"""
    return AppLogger.get()
