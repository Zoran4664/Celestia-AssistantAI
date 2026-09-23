"""
session_search.py — 会话全文搜索（V2-E1）

参考 HanaAgent（openhanako）lib/search/session-search.ts 思路，适配本项目
会话文件格式（history/conversations/session_*.json：{role, group, updated_at,
title, messages:[{role,name,content,ts}]}）。

- 两阶段搜索：先标题/首条消息（title phase），再全文（content phase）；
- 中文分词打分：整句包含 > 字符连续命中 > 分词命中；CJK 逐字符索引；
- 返回带 snippet 与 score 的结果，按分数 + 更新时间排序，上限可配；
- 纯标准库实现（无第三方依赖），无头可测。

线程纪律：本模块为纯函数集合，可在任意线程调用（只读会话文件）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import ConfigLoader

logger = logging.getLogger("AI_DeskMate.SessionSearch")

_PUNCT = set("，。、；：！？,.!?;: \t\n\r（）()[]【】「」\"'‘’“”·—-—_")


def _tokenize(text: str) -> List[str]:
    """中文分词：按标点/空白切分，连续的 CJK 字符按 2/3-gram 提取，英文按词。"""
    out: List[str] = []
    buf = ""
    for ch in text or "":
        if ch in _PUNCT:
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    grams: List[str] = []
    for tok in out:
        if len(tok) <= 1:
            continue
        # 连续 CJK 段落做 2-gram（与查询命中更稳）
        for i in range(len(tok) - 1):
            grams.append(tok[i:i + 2])
    return out + grams


def _score(text: str, query: str) -> float:
    """打分：整句包含 query 最高；query 逐字符连续命中次之；分词命中最低。"""
    text = (text or "").lower()
    q = (query or "").lower()
    if not q or not text:
        return 0.0
    if q in text:
        return 100.0
    score = 0.0
    # 连续字符命中（子串级别）
    for i in range(len(text) - len(q) + 1):
        match = 0
        for j in range(len(q)):
            if text[i + j] == q[j]:
                match += 1
            else:
                break
        if match > 0:
            score = max(score, match * 2.0)
    # 分词命中
    q_tokens = _tokenize(q)
    for tok in q_tokens:
        if len(tok) >= 2 and tok in text:
            score = max(score, len(tok) * 1.5)
    return score


def _snippet(text: str, query: str, width: int = 40) -> str:
    """生成命中上下文片段。"""
    text = text or ""
    q = (query or "").lower()
    low = text.lower()
    idx = low.find(q)
    if idx < 0:
        # 退化为字符级位置
        for ch in q[:1]:
            p = low.find(ch)
            if p >= 0:
                idx = p
                break
    if idx < 0:
        return text[:width]
    start = max(0, idx - width // 3)
    end = min(len(text), idx + len(q) + width * 2 // 3)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ") + suffix


def _read_session(path: Path) -> Optional[Dict[str, Any]]:
    """读取单个会话文件，损坏返回 None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def search_sessions(query: str, limit: int = 0,
                    conversations_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """在会话目录中搜索。

    :param query: 搜索关键词（支持中文）
    :param limit: 结果上限；0 用配置 search.max_results
    :param conversations_dir: 会话目录；None 用配置路径
    :return: [{path, title, role, group, modified, message_count, snippet,
               score, match_kind}]，按分数降序（同分按修改时间降序）
    """
    q = (query or "").strip()
    if not q:
        return []
    cfg = ConfigLoader.instance()
    if limit <= 0:
        limit = int(cfg.search_max_results() or 30)
    cdir = conversations_dir or cfg.conversations_dir
    if not cdir.exists():
        return []

    results: List[Dict[str, Any]] = []
    for path in sorted(cdir.glob("session_*.json")):
        data = _read_session(path)
        if not data:
            continue
        title = str(data.get("title") or "")
        msgs = data.get("messages") or []
        first = ""
        full_text = ""
        for m in msgs[:3]:
            c = str(m.get("content") or "")
            if c:
                first = c
                break
        # title phase
        t_score = _score(f"{title} {first}", q)
        if t_score > 0:
            results.append({
                "path": str(path), "title": title,
                "role": str(data.get("role") or ""),
                "group": str(data.get("group") or ""),
                "modified": float(data.get("updated_at") or path.stat().st_mtime),
                "message_count": len(msgs),
                "snippet": _snippet(f"{title} {first}", q),
                "score": t_score + 50.0,  # 标题命中加权
                "match_kind": "title",
            })
            continue
        # content phase
        if len(msgs) <= 30:
            full_text = " ".join(str(m.get("content") or "")
                                 for m in msgs if m.get("content"))
        c_score = _score(full_text, q) if full_text else 0.0
        if c_score > 0:
            results.append({
                "path": str(path), "title": title,
                "role": str(data.get("role") or ""),
                "group": str(data.get("group") or ""),
                "modified": float(data.get("updated_at") or path.stat().st_mtime),
                "message_count": len(msgs),
                "snippet": _snippet(full_text, q),
                "score": c_score,
                "match_kind": "content",
            })

    results.sort(key=lambda r: (-r["score"], -r["modified"]))
    return results[:limit]
