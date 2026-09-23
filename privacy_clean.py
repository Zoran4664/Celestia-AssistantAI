# -*- coding: utf-8 -*-
"""privacy_clean.py — 一键隐私清理（调试后 / 交付前把软件还原成「干净可部署」状态）。

清掉的内容（全部为本机使用痕迹，**不含**角色卡、技能库、主题等程序自带内容）：

  · API 信息      data/config.json 里的各 API KEY、user_avatar、ui.user_name
                  + data/api_vault.json（常用接口类 API 管理器全部条目）
  · 对话 / 记忆   history/conversations（会话存档 + 短期记忆）、history/chroma（向量库）、
                  history/memory_compile、history/memory_dream、history/file_history、
                  data/pinned.md
  · 日志          log/、utils/logs/、data/heartbeat_log.jsonl
  · 上传 / 产出    skilluserdata/、dailydata/（日记与附件）、novellearn/、createimage/、
                  data/character_cards/
  · 使用记录      data/input_drafts.json、data/logintime*.json、data/focus_*.json、
                  data/dailylifedata.json、data/heartbeat_state.json、
                  data/cron_jobs.json、data/cron_runs/

用法：
    python -X utf8 privacy_clean.py            # 预览 + 交互确认后清理
    python -X utf8 privacy_clean.py --yes      # 不询问直接清理（交付前用）
    python -X utf8 privacy_clean.py --scan     # 只列出将要清理的内容

被主界面「设置 → 隐私与清理」调用；`purge_pending()` 在 main.py 启动早期调用，
补删上一轮因文件被占用（典型是 ChromaDB / 日志句柄）而没删掉的目录。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 需要整体删除的文件（标签, 相对项目根的路径）
PRIVATE_FILES: Tuple[Tuple[str, str], ...] = (
    ("常用接口 API 条目", "data/api_vault.json"),
    ("输入框草稿", "data/input_drafts.json"),
    ("固定记忆", "data/pinned.md"),
    ("登陆时间记录", "data/logintime.json"),
    ("登陆时间分布", "data/logintime_history.json"),
    ("专注记录", "data/focus_history.json"),
    ("专注摘要", "data/focus_summary.json"),
    ("饮食热量记录", "data/dailylifedata.json"),
    ("心跳状态计数", "data/heartbeat_state.json"),
    ("心跳日志", "data/heartbeat_log.jsonl"),
    ("定时任务清单", "data/cron_jobs.json"),
    ("主程序错误日志", "log/error.log"),
)

#: 需要清空（删除内部全部内容，保留目录本身）的目录（标签, 相对项目根的路径）
PRIVATE_DIRS: Tuple[Tuple[str, str], ...] = (
    ("对话存档与短期记忆", "history/conversations"),
    ("向量记忆库（ChromaDB）", "history/chroma"),
    ("记忆传送带日报", "history/memory_compile"),
    ("记忆 Dream 快照", "history/memory_dream"),
    ("文件版本快照", "history/file_history"),
    ("技能产出文件", "skilluserdata"),
    ("日记与附件", "dailydata"),
    ("小说学习结果", "novellearn"),
    ("生成 / 下载的图片", "createimage"),
    ("角色卡导出文件", "data/character_cards"),
    ("定时任务运行历史", "data/cron_runs"),
    ("主程序错误日志目录", "log"),
    ("应用运行日志", "utils/logs"),
)

#: 配置里要清空的隐私字段：((配置路径...), 清空后的值, 中文说明)
PRIVATE_CONFIG_KEYS: Tuple[Tuple[Tuple[str, ...], Any, str], ...] = (
    (("api", "api_key"), "", "主对话接口 KEY"),
    (("api", "small_key"), "", "小模型接口 KEY"),
    (("api", "vision_key"), "", "视觉/多模态接口 KEY"),
    (("api", "image_key"), "", "生图接口 KEY"),
    (("web", "search_key"), "", "联网搜索 KEY"),
    (("ui", "user_name"), "用户", "用户名"),
    (("user_avatar",), "./data/user_avatar.png", "用户头像路径"),
)

#: 待补删清单（被占用没删掉的目录，下次启动重试）
PENDING_FILE = "data/.privacy_purge_pending.json"

#: 技能工具管理器：每个技能可单独填 API（`\@技能` 走独立端点），KEY 同样属于隐私。
#: 2026-09-23 审计发现的**覆盖盲区**：旧实现只清 ``data/config.json`` 的字段与
#: ``data/api_vault.json``，技能级 KEY 会被原样留下 —— 而该文件受版本控制，
#: 交付 / 上传时会把真实密钥一起带出去（实际发生过：2 个真实 KEY 已进入待提交文件）。
SKILL_INFO_FILE = "skills/skilltools_information.json"


# ---------------------------------------------------------------- 基础工具
def project_root() -> Path:
    """项目根目录（优先用 ConfigLoader，取不到则用本文件所在目录）。"""
    try:
        from config_loader import project_root as _pr
        return _pr()
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent


def _config_path(root: Path) -> Path:
    """配置文件路径：优先 ConfigLoader 实际使用的路径（且必须落在 root 内）。

    这样单元测试传入临时 root 时不会误改真实 `data/config.json`。
    """
    try:
        from config_loader import ConfigLoader
        p = getattr(ConfigLoader.instance(), "_path", None)
        if p is not None:
            p = Path(p)
            try:
                p.relative_to(root)
                return p
            except ValueError:
                pass
    except Exception:  # noqa: BLE001
        pass
    return root / "data" / "config.json"


def _dir_size(path: Path) -> int:
    """目录/文件占用字节数（失败按 0 计）。"""
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total
    except Exception:  # noqa: BLE001
        return 0


def fmt_size(num: int) -> str:
    """字节数 → 人类可读。"""
    val = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} GB"


def _targets(root: Path) -> List[Dict[str, Any]]:
    """展开成目标清单：[{label, path, kind("file"|"dir"), exists, size}]。"""
    out: List[Dict[str, Any]] = []
    for label, rel in PRIVATE_FILES:
        p = root / rel
        out.append({"label": label, "path": p, "kind": "file",
                    "exists": p.exists(), "size": _dir_size(p) if p.exists() else 0})
    for label, rel in PRIVATE_DIRS:
        p = root / rel
        # log/ 与 log/error.log 会重复出现：合并成一项（目录包含文件）
        if any(t["kind"] == "dir" and p == t["path"] for t in out):
            continue
        if any(t["kind"] == "file" and t["path"] == p for t in out):
            continue
        out.append({"label": label, "path": p, "kind": "dir",
                    "exists": p.is_dir(), "size": _dir_size(p) if p.is_dir() else 0})
    return out


# ---------------------------------------------------------------- 预览
def skill_key_entries(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """列出技能工具管理器里**填了 KEY** 的技能（只读，供 scan / 预览用）。"""
    path = Path(root or project_root()) / SKILL_INFO_FILE
    out: List[Dict[str, str]] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(raw, dict):
        return out
    for name, item in raw.items():
        if isinstance(item, dict):
            key = str(item.get("api_key") or "").strip()
            if key:
                out.append({"desc": f"技能「{name}」独立 KEY", "value": key[:40]})
    return out


def clear_skill_keys(root: Optional[Path] = None) -> List[str]:
    """清空技能工具管理器里各技能的 ``api_key``，返回被清空的技能名。

    只清 KEY，**保留** ``api_base`` / ``api_model``：这样回填一个 KEY 就能继续用，
    不必重配每个技能的端点与模型。
    """
    path = Path(root or project_root()) / SKILL_INFO_FILE
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    hits: List[str] = []
    for name, item in raw.items():
        if isinstance(item, dict) and str(item.get("api_key") or "").strip():
            item["api_key"] = ""
            hits.append(str(name))
    if not hits:
        return []
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)          # 原子写：与项目其它 JSON 落盘约定一致
    return hits


def scan(root: Optional[Path] = None) -> Dict[str, Any]:
    """只读预览：列出将要清理的文件/目录/配置字段（供界面确认框与 CLI --scan）。"""
    root = Path(root or project_root())
    items = _targets(root)
    cfg_path = _config_path(root)
    cfg_keys: List[Dict[str, str]] = []
    try:
        if cfg_path.exists():
            raw = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
            for keys, _blank, desc in PRIVATE_CONFIG_KEYS:
                cur: Any = raw
                ok = True
                for k in keys:
                    if isinstance(cur, dict) and k in cur:
                        cur = cur[k]
                    else:
                        ok = False
                        break
                if ok and str(cur or "").strip():
                    cfg_keys.append({"desc": desc, "value": str(cur)[:40]})
    except Exception as exc:  # noqa: BLE001
        cfg_keys.append({"desc": f"配置读取失败：{exc}", "value": ""})
    # 技能工具管理器里的独立 KEY 同样是隐私（旧实现漏了这里）
    cfg_keys.extend(skill_key_entries(root))
    files = [it for it in items if it["kind"] == "file" and it["exists"]]
    dirs = [it for it in items if it["kind"] == "dir" and it["exists"]]
    return {"root": str(root), "files": files, "dirs": dirs,
            "config_keys": cfg_keys, "config_path": str(cfg_path),
            "total_bytes": sum(it["size"] for it in files + dirs)}


def describe_plan(root: Optional[Path] = None) -> str:
    """生成给用户看的中文清单（设置面板确认框 / CLI 用）。"""
    info = scan(root)
    lines = ["将清除以下本机隐私与使用痕迹（不可恢复）：", ""]
    lines.append("【API 与个人信息】共 %d 项"
                 % len(info["config_keys"]))
    for k in info["config_keys"]:
        lines.append(f"  · {k['desc']}：{k['value'] or '（空）'}")
    if not info["config_keys"]:
        lines.append("  · （无）")
    lines.append("")
    lines.append("【对话 / 记忆 / 日志 / 上传文件 / 使用记录】")
    for it in info["files"]:
        lines.append(f"  · {it['label']}（{fmt_size(it['size'])}）")
    for it in info["dirs"]:
        lines.append(f"  · {it['label']} 目录（{fmt_size(it['size'])}）")
    if not info["files"] and not info["dirs"]:
        lines.append("  · （无）")
    lines.append("")
    lines.append(f"合计约 {fmt_size(info['total_bytes'])}。"
                 "角色卡、技能库、主题背景等程序自带内容不会被删除。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 执行
def _retry_delete(path: Path) -> bool:
    """删除文件或目录；成功 True。被占用时返回 False（由调用方登记待补删）。"""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


def _truncate_file(path: Path) -> bool:
    """文件被占用无法删除时的兜底：清空内容（日志类文件常见）。"""
    try:
        if path.is_file():
            with open(path, "w", encoding="utf-8"):
                pass
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _clear_dir(path: Path, root: Path,
               pending: List[str], errors: List[str]) -> None:
    """清空目录内部内容，保留目录本身。"""
    if not path.is_dir():
        return
    try:
        children = list(path.iterdir())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{path} 无法读取：{exc}")
        return
    for child in children:
        if _retry_delete(child):
            continue
        if child.is_file() and _truncate_file(child):
            continue
        # 目录被占用（典型：ChromaDB 句柄）→ 登记待下次启动补删
        try:
            pending.append(str(child.relative_to(root)))
        except ValueError:
            pending.append(str(child))
        errors.append(f"{child.name} 正被占用，已登记到下次启动时清除"
                      f"（如需立刻生效请重启程序）")


def _write_pending(root: Path, pending: List[str]) -> None:
    """记录待补删路径（去重）。"""
    if not pending:
        return
    p = root / PENDING_FILE
    old: List[str] = []
    try:
        if p.exists():
            old = [str(x) for x in (json.loads(p.read_text(encoding="utf-8"))
                                    .get("paths") or [])]
    except Exception:  # noqa: BLE001
        old = []
    merged = sorted({x for x in old + pending if x})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"updated": time.time(), "paths": merged},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def purge_pending(root: Optional[Path] = None) -> int:
    """补删上次被占用未删除的目录（**应在 MemoryPipeline/ChromaDB 初始化之前调用**）。"""
    root = Path(root or project_root())
    p = root / PENDING_FILE
    if not p.exists():
        return 0
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) or {}
        paths = [str(x) for x in (raw.get("paths") or [])]
    except Exception:  # noqa: BLE001
        paths = []
    left: List[str] = []
    done = 0
    for rel in paths:
        target = Path(rel)
        if not target.is_absolute():
            target = root / rel
        if not target.exists():
            continue
        if _retry_delete(target) or (target.is_file() and _truncate_file(target)):
            done += 1
        else:
            left.append(rel)
    if left:
        _write_pending(root, left)
    else:
        try:
            p.unlink()
        except Exception:  # noqa: BLE001
            pass
    return done


def clean(*, root: Optional[Path] = None, reset_config: bool = True,
          dry_run: bool = False) -> Dict[str, Any]:
    """执行清理，返回报告 {removed, pending, errors, freed, config_keys}。"""
    root = Path(root or project_root())
    info = scan(root)
    report: Dict[str, Any] = {"removed": [], "pending": [], "errors": [],
                              "freed": 0, "config_keys": [], "leaks": [],
                              "root": str(root)}
    if dry_run:
        report["freed"] = info["total_bytes"]
        report["removed"] = [it["label"] for it in info["files"] + info["dirs"]]
        report["config_keys"] = [k["desc"] for k in info["config_keys"]]
        return report

    pending: List[str] = []
    for it in info["files"]:
        if _retry_delete(it["path"]) or _truncate_file(it["path"]):
            report["removed"].append(it["label"])
            report["freed"] += it["size"]
        else:
            pending.append(str(it["path"].relative_to(root)))
            report["errors"].append(f"{it['label']} 正被占用，已登记下次启动清除")
    for it in info["dirs"]:
        before = _dir_size(it["path"])
        _clear_dir(it["path"], root, pending, report["errors"])
        empty = True
        if it["path"].is_dir():
            try:
                empty = not any(it["path"].iterdir())
            except Exception:  # noqa: BLE001
                empty = True
        if empty:
            report["removed"].append(it["label"])
        report["freed"] += max(0, before - _dir_size(it["path"]))

    if reset_config:
        cfg_path = Path(info["config_path"])
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            if not isinstance(raw, dict):
                raw = {}
            for keys, blank, desc in PRIVATE_CONFIG_KEYS:
                node = raw
                for k in keys[:-1]:
                    nxt = node.get(k)
                    if not isinstance(nxt, dict):
                        nxt = {}
                        node[k] = nxt
                    node = nxt
                node[keys[-1]] = blank
                report["config_keys"].append(desc)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cfg_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(cfg_path)
            # 头像文件本身也属于个人信息
            try:
                avatar = root / "data" / "user_avatar.png"
                if avatar.exists():
                    _retry_delete(avatar)
            except Exception:  # noqa: BLE001
                pass
            # 技能工具管理器里的独立 KEY 也一并清空（2026-09-23 审计补漏）。
            # 该文件受版本控制，漏清 = 交付 / 上传时把真实密钥一起公开。
            for _name in clear_skill_keys(root):
                report["config_keys"].append(f"技能「{_name}」独立 KEY")
            # 让运行中的进程立即用回默认值
            try:
                from config_loader import ConfigLoader
                ConfigLoader.instance().reload()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"配置清理失败：{exc}")

    _write_pending(root, pending)
    report["pending"] = pending
    # 交付/上传前的最后一道自查：清完之后，工作区里是否**还**残留密钥特征？
    # （由 secret_guard 统一判定；发现即列进报告，不影响清理结果本身。）
    try:
        import secret_guard  # noqa: PLC0415 - 延迟导入，避免无谓开销
        report["leaks"] = secret_guard.scan_worktree(root)
    except Exception:  # noqa: BLE001
        report["leaks"] = []
    return report


# ---------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    """命令行入口：`--scan` 只看，`--yes` 不询问直接清理。"""
    import sys
    try:
        from utils.utf8 import force_utf8_stdio
        force_utf8_stdio()
    except Exception:  # noqa: BLE001
        pass
    args = list(sys.argv[1:] if argv is None else argv)
    print(describe_plan())
    if "--scan" in args:
        # 只读预览顺带做一次密钥体检（不修改任何东西）
        try:
            import secret_guard  # noqa: PLC0415
            print()
            print(secret_guard.format_report(secret_guard.scan_worktree()))
        except Exception:  # noqa: BLE001
            pass
        return 0
    if "--yes" not in args:
        try:
            ans = input("\n确认清除以上内容？输入 yes 继续：").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes", "是"):
            print("已取消，未做任何修改。")
            return 1
    report = clean()
    print("\n清理完成：删除/清空 %d 项，释放约 %s。"
          % (len(report["removed"]), fmt_size(report["freed"])))
    if report["config_keys"]:
        print("已清空配置字段：" + "、".join(report["config_keys"]))
    if report["pending"]:
        print("以下内容被程序占用，将在下次启动时自动清除：")
        for p in report["pending"]:
            print("  · " + p)
    for e in report["errors"]:
        print("注意：" + e)
    # 清理后的密钥复查（护栏）：确认工作区里再没有残留密钥特征
    _print_leak_check(report)
    return 0


def _print_leak_check(report: Dict[str, Any]) -> None:
    """打印 secret_guard 的复查结果（没有该模块时静默跳过）。"""
    try:
        import secret_guard  # noqa: PLC0415
        hits = report.get("leaks")
        if hits is None:
            hits = secret_guard.scan_worktree()
        print()
        print(secret_guard.format_report(hits))
    except Exception:  # noqa: BLE001
        print("\n（提示）可用 `python -X utf8 secret_guard.py --all` 做提交前密钥体检。")


if __name__ == "__main__":
    raise SystemExit(main())
