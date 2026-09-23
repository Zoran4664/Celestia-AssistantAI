"""
main.py — 启动入口（主文件与主应用）

启动流程：
  高 DPI → QApplication → 单实例守卫 → 日志 → 配置 → 信号总线/角色/记忆/LLM
  → 主窗口（HTML5 响应式风格）→ 桌面宠物 → 记忆清理定时器 → 系统托盘常驻

启动参数：
  --config <path>  指定配置文件（默认 ./data/config.json）
  --no-pet         禁用桌面宠物
  --role <name>    启动时加载的角色（默认「默认助手」）
  --debug          输出 DEBUG 级日志
  --no-gpu         强制禁用 GPU 加速
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.utf8 import force_utf8_stdio  # noqa: E402 - 需在 ROOT 加入 sys.path 后导入
from utils.styled_msg import styled_warning  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Celestia AssistantAI - 多模态大模型角色对话 Agent")
    parser.add_argument("--config", type=str,
                        default=str(ROOT / "data" / "config.json"),
                        help="配置文件路径")
    parser.add_argument("--no-pet", action="store_true", help="禁用桌面宠物")
    parser.add_argument("--role", type=str, default="", help="启动时加载的角色")
    parser.add_argument("--debug", action="store_true", help="输出 DEBUG 级日志")
    parser.add_argument("--no-gpu", action="store_true", help="强制禁用 GPU 加速")
    return parser.parse_args()


def main() -> int:
    # 强制 UTF-8（在一切 print / 日志输出之前），Windows 下避免 GBK 乱码
    force_utf8_stdio()
    args = parse_args()
    if args.no_gpu:
        os.environ["QT_OPENGL"] = "software"
        os.environ["QT_QUICK_BACKEND"] = "software"

    # ------------------------------------------------------------------ Qt
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtCore import Qt
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtGui import QFont
    except ImportError as exc:  # pragma: no cover
        print("=" * 60)
        print("[启动失败] PySide6 / Qt 依赖导入失败。")
        print("请先安装依赖：pip install -r requirements.txt")
        print("建议 Python 版本 3.10 / 3.11（chromadb 0.5.3 兼容性最佳）。")
        print(f"详细信息：{exc}")
        print("=" * 60)
        return 1

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("AI_DeskMate")
    app.setApplicationDisplayName("Celestia AssistantAI")
    app.setQuitOnLastWindowClosed(False)

    # ------------------------------------------------------------ 单实例
    server = QLocalServer()
    server.removeServer("AI_DeskMate_SingleInstance")
    if not server.listen("AI_DeskMate_SingleInstance"):
        styled_warning(None, "已在运行", "Celestia AssistantAI已有一个实例正在运行。")
        return 1

    # ------------------------------------------------------------ 日志
    from utils.logger import AppLogger
    AppLogger.init(debug=args.debug)
    logger = AppLogger.get()

    # ------------------------------------------------------------ 核心单例
    from config_loader import ConfigLoader
    cfg = ConfigLoader.instance(config_path=args.config)
    logger.info("配置加载完成: %s", cfg.main_model())

    # ------------------------------------------------------------ 应用图标
    # 需求：任务栏 / Alt-Tab 的小图标必须是应用自带的 logo。
    # Windows 未设置 AppUserModelID 时会按 python.exe 分组并沿用其图标，
    # 因此这里显式指定进程 ID + 应用级图标（与窗口、托盘同源）。
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Celestia.AssistantAI")
        except Exception as exc:  # noqa: BLE001
            logger.debug("设置 AppUserModelID 失败（忽略）: %s", exc)
    try:
        from ui_manager import app_icon as _app_icon
        app.setWindowIcon(
            _app_icon(str(cfg.get("ui", "icon_path", default="") or "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("应用图标设置失败（忽略）: %s", exc)

    # 界面字体：优先配置的字体（默认微软雅黑），未安装则跟随系统默认。
    # 需求：字体使用整数像素大小（13px），避免 10pt→13.33px 非整数像素导致文字锯齿。
    fam = cfg.font_family()
    _fam = fam if fam and QFont(fam).exactMatch() else ""
    _base_font = QFont(_fam)
    _base_font.setPixelSize(13)
    app.setFont(_base_font)

    # ------------------------------------------------------------ 隐私清理补删
    # 「设置 → 隐私与清理」运行时可能因 ChromaDB / 日志句柄占用而删不掉部分目录，
    # 那些路径会登记到 data/.privacy_purge_pending.json，这里在记忆库初始化**之前**
    # 补删，保证下次启动就自动清干净（且不影响本次运行）。
    try:
        import privacy_clean
        _purged = privacy_clean.purge_pending()
        if _purged:
            logger.info("隐私清理：补删上次被占用的 %d 项", _purged)
    except Exception as exc:  # noqa: BLE001
        logger.warning("隐私清理补删失败（忽略）: %s", exc)

    from signal_bus import SignalBus
    from role_manager import RoleManager
    from memory_pipeline import MemoryPipeline
    from llm_client import LLMClientPool

    bus = SignalBus.instance()
    roles = RoleManager.instance()
    memory = MemoryPipeline.instance()
    pool = LLMClientPool.instance()

    # ------------------------------------------------------------ 主窗口
    from ui_manager import MainWindow
    # 需求：重新打开软件时停留在上次退出时的角色（配置保存的角色优先）
    saved_role = (cfg.get("ui", "last_role", default="") or "").strip()
    initial_role = args.role or saved_role or (
        roles.list_roles()[0] if roles.list_roles() else "默认助手")
    window = MainWindow(initial_role=initial_role)
    window.show()

    # ------------------------------------------------------------ 桌宠（默认不开启）
    pet = None
    pet_enabled = bool(cfg.get("pet", "enabled", default=False))
    if pet_enabled and not args.no_pet:
        try:
            from pet_manager import DesktopPet
            # 使用主窗口恢复后的角色，保证桌宠与主界面角色一致
            pet = DesktopPet(main_window=window, initial_role=window._current_role)
            pet.show()
            window.set_pet(pet)
            # 群聊模式：桌宠跟随该群最近一次会话的最后发言角色
            if window._current_group:
                window._sync_pet_to_last_speaker()
            logger.info("桌面宠物已启动")
        except Exception as exc:  # noqa: BLE001
            logger.warning("桌宠启动失败（不影响主窗口）: %s", exc)

    # ------------------------------------------------------------ 记忆清理
    # V2-B1：通用 Cron 引擎接管记忆清理定时器（等价逻辑：每 cleanup_interval_hours 小时清理一次）
    cleanup_hours = max(1.0, float(cfg.get("memory", "cleanup_interval_hours",
                                           default=24)))
    cron_mgr = None
    if cfg.cron_enabled():
        from cron_manager import CronManager

        cron_mgr = CronManager.instance()

        def _cleanup_action(payload: dict) -> None:  # noqa: ARG001
            # spawn_worker 保活引用，防止 QThread 被 GC 回收导致闪退
            from utils.async_worker import spawn_worker
            spawn_worker(
                memory.decay_and_forget,
                on_fail=lambda msg: logger.warning("记忆清理失败: %s", msg))

        cron_mgr.register_action("memory_cleanup", _cleanup_action)
        cron_mgr.add_job("记忆定期清理", "every", int(cleanup_hours * 3600 * 1000),
                         "memory_cleanup")

        # V2-A3：Memory Dream 周期性记忆整合（默认关闭，开启后按 interval_hours 执行）
        if cfg.dream_enabled():
            from memory_dream import MemoryDream

            def _dream_action(payload: dict) -> None:  # noqa: ARG001
                from utils.async_worker import spawn_worker
                spawn_worker(
                    MemoryDream.instance().run_dream,
                    on_fail=lambda msg: logger.warning("Memory Dream 失败: %s", msg))

            cron_mgr.register_action("memory_dream", _dream_action)
            cron_mgr.add_job("记忆 Dream 整合", "every",
                             int(cfg.dream_interval_hours() * 3600 * 1000),
                             "memory_dream")

        # V2-A5：角色自动写日记（每天固定时间；角色取当前角色）
        if bool(cfg.get("ui", "auto_diary_enabled", default=False)):
            from auto_diary import AutoDiary

            def _diary_action(payload: dict) -> None:  # noqa: ARG001
                role = bus.request("ui:current_role") or ""
                AutoDiary.instance().generate_diary_async(
                    role or "默认助手",
                    lambda path: logger.info(
                        "自动日记已生成: %s", path) if path else None)

            diary_time = (cfg.get("ui", "auto_diary_time", default="23:00")
                          or "23:00").strip()
            try:
                hh, mm = (int(x) for x in diary_time.split(":", 1))
                cron_mgr.register_action("auto_diary", _diary_action)
                cron_mgr.add_job("角色自动写日记", "cron",
                                 f"{mm} {hh} * * *", "auto_diary")
                logger.info("自动日记已启用（每天 %02d:%02d）", hh, mm)
            except (ValueError, TypeError) as exc:
                logger.warning("自动日记时间配置非法，跳过: %s", exc)

        cron_mgr.start()

    # ------------------------------------------------------------ V2：心跳巡检（默认关闭）
    heartbeat = None
    if cfg.heartbeat_enabled():
        try:
            from heartbeat import Heartbeat
            heartbeat = Heartbeat.instance()
            heartbeat.start()
            logger.info("心跳巡检已启动")
        except Exception as exc:  # noqa: BLE001
            logger.warning("心跳巡检启动失败（不影响主窗口）: %s", exc)

    # ------------------------------------------------------------ V2：统一通知服务
    from notify_service import NotifyService
    notify_svc = NotifyService.instance()  # noqa: F841 - 供各模块 notify() 使用

    # ------------------------------------------------------------ 退出
    def _quit() -> None:
        if cron_mgr is not None:
            cron_mgr.stop()
        if heartbeat is not None:
            heartbeat.stop()
        app.quit()

    bus.app_quit.connect(_quit)

    code = app.exec()
    server.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
