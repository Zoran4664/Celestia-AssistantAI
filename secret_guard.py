# -*- coding: utf-8 -*-
"""secret_guard.py — 提交 / 交付前的**密钥与隐私体检护栏**（防泄漏）。

背景（2026-09-23）：本项目曾把真实 API KEY 提交进 `skills/skilltools_information.json`
并推送到 GitHub —— 虽然事后在服务商处作废、并把工作区改回空值，但**历史提交里永久
留下了那串字符串**（清不掉，除非重写历史）。为从流程上杜绝同类事故，本模块把
「密钥特征扫描」固化成**唯一一处**实现，并提供三种用法：

    python -X utf8 secret_guard.py --staged     # 只看已暂存内容（pre-commit 钩子用）
    python -X utf8 secret_guard.py --tracked    # 只查已被 git 跟踪的文件
    python -X utf8 secret_guard.py --all        # 查整个工作区（上传 / 交付前体检）

退出码：0 = 未发现疑似密钥；1 = 发现（逐条打印 `文件:行:规则:片段`）。
片段一律**打码**，避免把密钥本身写进日志 / CI 输出。

配套（三层防线）：
  1. **写代码时**：`.githooks/pre-commit` → `--staged`，命中即**阻止提交**
     （确属占位符可用 `SKIP_SECRET_GUARD=1 git commit ...` 临时绕过）；
  2. **交付 / 上传前**：`privacy_clean.py` 清空 KEY 后会自动再跑一次本模块复查；
  3. **.gitignore**：`data/api_vault.json`（常用接口 API 管理器凭据）等运行时凭据
     文件不入库。

自定义私有规则（**不要提交**）：在 `data/.secret_guard.json` 写
``{"patterns": ["你的姓名", "公司域名"], "files": ["data/notes.md"]}``，
本模块会自动加载（该文件已在 .gitignore 中）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

#: 只扫描这些扩展名（避免把二进制/大文件读进来）
TEXT_EXTS = {".py", ".json", ".md", ".txt", ".csv", ".yml", ".yaml", ".ini",
             ".cfg", ".toml", ".js", ".ts", ".sh", ".bat", ".ps1", ".html",
             ".xml", ".jsonl", ".env", ".pem", ".key", ".bak", ".sqlite", ".db"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".idea"}
MAX_BYTES = 3 * 1024 * 1024        # 超过 3MB 的文本文件跳过

#: 明显是占位符 → 不算泄漏（大小写不敏感，作用于**匹配到的值**）
PLACEHOLDER_HINTS = ("xxx", "your", "example", "placeholder", "sample",
                     "省略", "示例", "****", "***", "...", "…", "<", ">",
                     "${", "{{", "sk-test")

#: 密钥特征：(规则名, 正则, 取值分组号【None = 整条匹配】)
SECRET_RULES: Tuple[Tuple[str, "re.Pattern[str]", Optional[int]], ...] = (
    ("厂商 API KEY（sk-…）",
     re.compile(r"\bsk-[A-Za-z0-9._\-]{20,}"), None),
    ("百度千帆 AI 搜索 Key", re.compile(r"\bbce-v3/[A-Za-z0-9]{8,}"), None),
    ("百度千帆 AK", re.compile(r"\bALTAK-[A-Za-z0-9]{8,}"), None),
    ("Authorization: Bearer <长串>",
     re.compile(r"\bBearer\s+([A-Za-z0-9._\-]{24,})"), 1),
    ("配置里的非空凭据字段",
     re.compile(r"""["'](?:api_key|apiKey|secret|password|access_token"""
                r"""|search_key|small_key|vision_key|image_key)["']\s*:\s*"""
                r"""["']([^"']{8,})["']"""), 1),
)

#: 本机绝对路径（Windows 盘符）——系统目录除外，属隐私/不可移植
PATH_RULE = ("本机绝对路径", re.compile(r"\b[A-Za-z]:[\\/](?![Ww]indows)"), None)


def _mask(value: str) -> str:
    """打码：只留前 8 位 + 长度，避免把密钥写进日志。"""
    v = str(value or "")
    if len(v) <= 8:
        return v[:2] + "…"
    return f"{v[:8]}…（共 {len(v)} 字符）"


def _is_placeholder(value: str) -> bool:
    low = str(value or "").lower()
    return any(h in low for h in PLACEHOLDER_HINTS)


def extra_patterns(root: Optional[Path] = None) -> List[Tuple[str, "re.Pattern[str]"]]:
    """加载用户私有规则 `data/.secret_guard.json`（该文件不入库）。"""
    p = Path(root or ROOT) / "data" / ".secret_guard.json"
    out: List[Tuple[str, "re.Pattern[str]"]] = []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for raw in (data.get("patterns") or []) if isinstance(data, dict) else []:
        try:
            out.append((f"自定义规则（{_mask(str(raw))}）", re.compile(str(raw))))
        except Exception:  # noqa: BLE001
            continue
    return out


def scan_text(text: str, where: str = "",
              extra: Sequence[Tuple[str, "re.Pattern[str]"]] = (),
              with_paths: bool = True) -> List[Dict[str, Any]]:
    """扫描一段文本，返回命中列表 [{file, line, rule, sample}]（sample 已打码）。"""
    hits: List[Dict[str, Any]] = []
    rules: List[Tuple[str, "re.Pattern[str]", Optional[int]]] = list(SECRET_RULES)
    if with_paths:
        rules.append(PATH_RULE)  # type: ignore[arg-type]
    for rule_name, rx, grp in rules:
        for i, line in enumerate(str(text or "").splitlines(), 1):
            for m in rx.finditer(line):
                val = m.group(grp) if grp else m.group(0)
                if _is_placeholder(val):
                    continue
                hits.append({"file": where, "line": i, "rule": rule_name,
                             "sample": _mask(val)})
    for rule_name, rx in extra:
        for i, line in enumerate(str(text or "").splitlines(), 1):
            m = rx.search(line)
            if m:
                hits.append({"file": where, "line": i, "rule": rule_name,
                             "sample": _mask(m.group(0))})
    return hits


# ---------------------------------------------------------------- 三种取数方式
def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def staged_hits(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """扫描**已暂存**内容（pre-commit 用）：按暂存快照读，避免漏掉未落盘改动。"""
    base = Path(root or ROOT)
    names = [x.strip() for x in
             _git("-C", str(base), "diff", "--cached", "--name-only",
                  "--diff-filter=ACM").splitlines() if x.strip()]
    extra = extra_patterns(base)
    hits: List[Dict[str, Any]] = []
    for name in names:
        if Path(name).suffix.lower() not in TEXT_EXTS and Path(name).name != ".gitignore":
            continue
        content = _git("-C", str(base), "show", f":{name}")
        hits.extend(scan_text(content, name, extra))
    return hits


def tracked_hits(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """只扫描已被 git 跟踪的文件。"""
    base = Path(root or ROOT)
    names = [x.strip() for x in
             _git("-C", str(base), "ls-files").splitlines() if x.strip()]
    extra = extra_patterns(base)
    hits: List[Dict[str, Any]] = []
    for name in names:
        p = base / name
        if p.suffix.lower() not in TEXT_EXTS and p.name != ".gitignore":
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            hits.extend(scan_text(p.read_text(encoding="utf-8", errors="ignore"),
                                  name, extra))
        except OSError:
            continue
    return hits


def scan_worktree(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """扫描整个工作区（上传 / 交付前体检）。"""
    base = Path(root or ROOT)
    extra = extra_patterns(base)
    hits: List[Dict[str, Any]] = []
    for p in base.rglob("*"):
        if not p.is_file() or any(d in SKIP_DIRS for d in p.parts):
            continue
        if p.suffix.lower() not in TEXT_EXTS and p.name != ".gitignore":
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            hits.extend(scan_text(p.read_text(encoding="utf-8", errors="ignore"),
                                  str(p.relative_to(base)).replace("\\", "/"), extra))
        except OSError:
            continue
    return hits


# ---------------------------------------------------------------- 报告 / CLI
def format_report(hits: Sequence[Dict[str, Any]]) -> str:
    if not hits:
        return "密钥体检：未发现疑似密钥 ✓"
    lines = [f"密钥体检：发现 {len(hits)} 处疑似泄漏！", ""]
    for h in hits:
        lines.append(f"  · {h['file']}:{h['line']}  [{h['rule']}]  {h['sample']}")
    lines.append("")
    lines.append("处理：把真实值清空（配置类可跑 `python -X utf8 privacy_clean.py --yes` 自动清），")
    lines.append("      确属示例/占位符时用 `SKIP_SECRET_GUARD=1 git commit ...` 绕过。")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        from utils.utf8 import force_utf8_stdio
        force_utf8_stdio()
    except Exception:  # noqa: BLE001
        pass
    if "--staged" in args:
        hits = staged_hits()
        mode = "已暂存内容"
    elif "--tracked" in args:
        hits = tracked_hits()
        mode = "git 已跟踪文件"
    else:
        hits = scan_worktree()
        mode = "整个工作区"
    print(f"（范围：{mode}）", flush=True)
    print(format_report(hits), flush=True)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
