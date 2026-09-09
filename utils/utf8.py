"""utils/utf8.py — 全项目 UTF-8 编码保障（Windows 下防止 GBK/cp936 乱码）。

整个软件（系统界面文字、系统文本、输入输出文本、日志、JSON 数据文件等）
统一使用 UTF-8 编码，不采用 GBK 等区域编码。

Windows 上 Python 3.13 的 sys.stdout / sys.stderr 默认可能按系统区域
编码（如 GBK/cp936）工作，导致中文输出、重定向文件、子进程交互出现乱码。
本模块提供两个函数在所有入口统一强制 UTF-8：
  - force_utf8_stdio(): 强制当前进程的 stdout/stderr 使用 UTF-8，并设置
    PYTHONUTF8 / PYTHONIOENCODING 环境变量（供子进程继承）。
  - utf8_env(): 返回携带 UTF-8 环境变量的环境字典（用于 subprocess 启动
    main.py / start.py 等子进程）。
"""

from __future__ import annotations

import os
import sys
from typing import Dict


def force_utf8_stdio() -> None:
    """强制当前进程的标准输出/错误流使用 UTF-8 编码。

    必须在任何 print / 日志输出之前调用。对不支持 reconfigure 的环境
    （如 IDLE 或 stdout 为 None 的 GUI 子进程）静默跳过。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 部分环境不支持 reconfigure
            pass
    # 确保当前进程与子进程（如 start.py → main.py）都使用 UTF-8
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def utf8_env() -> Dict[str, str]:
    """返回携带 UTF-8 相关环境变量的环境字典（用于 subprocess）。"""
    env: Dict[str, str] = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env
