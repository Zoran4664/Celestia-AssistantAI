"""
memory_dream.py — Memory Dream 周期性记忆整合（V2-A3）

参考 HanaAgent（openhanako）lib/memory/dream/runner.ts 思路，适配本项目
「ChromaDB 长期记忆（long_term）」：

流程（由 LLM 分阶段驱动，全程可回滚）：
  读取该角色 long_term 全部条目（文本 + id + metadata）
    → 快照备份（data/memory_dream/revisions/<ts>.json，可回滚）
    → LLM 整合：把语义重复 / 相关的碎片合并为综合记忆（JSON 输出）
    → 执行：删除组内旧 id → 新增综合记忆条目；可选 forget_ids 删除
    → 写运行报告（memory_dream_state.json）

安全设计：
- 只处理指定 role 且非 frozen（未固化）的条目；
- LLM 输出严格 JSON，解析失败即中止（绝不破坏数据）；
- 全部删除/新增前先写快照；rollback(revision_id) 可恢复；
- 摘要器可注入（测试用）；默认走小模型（kind=small），超时降级跳过。

线程纪律：本类为普通单例（不继承 QObject），可在子线程调用；
写操作全部走 MemoryPipeline 自带的 self._db.lock 保护。
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
from memory_pipeline import MemoryPipeline, MODE_NORMAL

logger = logging.getLogger("AI_DeskMate.MemoryDream")

_DREAM_DIRNAME = "memory_dream"
_REVISIONS_DIRNAME = "revisions"
_STATE_FILE = "memory_dream_state.json"
_DEFAULT_LIMIT = 100       # 单轮最多处理的条目数（防超时）
_MAX_GROUP_IDS = 8         # 单组合并上限


class MemoryDream:
    """Memory Dream 整合器（单例）。"""

    _instance: Optional["MemoryDream"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._memory = MemoryPipeline.instance()
        self._integrator: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "MemoryDream":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 路径
    def root_dir(self) -> Path:
        return self._cfg.history_dir / _DREAM_DIRNAME

    def revisions_dir(self) -> Path:
        return self.root_dir() / _REVISIONS_DIRNAME

    def state_path(self) -> Path:
        return self.root_dir() / _STATE_FILE

    # ------------------------------------------------------------ LLM 整合
    def _default_integrator(self) -> Callable[[List[Dict[str, Any]]], Dict[str, Any]]:
        """默认整合器：把条目文本交给小模型，要求输出严格 JSON。

        期望输出：{"groups": [{"ids": [...], "summary": "综合记忆"}],
                   "forget_ids": [...]}
        """

        def _integrate(items: List[Dict[str, Any]]) -> Dict[str, Any]:
            from llm_client import LLMClientPool
            pool = LLMClientPool.instance()
            texts = "\n".join(f"[{i}] {it['text']}" for i, it in enumerate(items))
            prompt = (
                "下面是同一角色的长期记忆碎片（编号 [i]）。请把语义重复或强相关的"
                "碎片合并成综合记忆，并标出应当遗忘（过时/冗余）的碎片编号。\n\n"
                f"{texts}\n\n"
                "只输出 JSON，不要任何其它文字：\n"
                '{"groups": [{"ids": ["编号i1", "编号i2"], "summary": "综合记忆文本"}],'
                ' "forget_ids": ["编号j"]}\n'
                "要求：ids 使用原文编号格式（如 [0] 的编号是 0）；"
                "每个 group 至少 2 个 ids；summary 用中文且不超过 80 字；"
                "没有可合并或可遗忘的项时输出 {\"groups\": [], \"forget_ids\": []}。")
            try:
                timeout = float(self._cfg.get("memory", "denoise_timeout_seconds",
                                              default=5.0) or 5.0)
                raw = pool.chat_complete(
                    [{"role": "system",
                      "content": "你是记忆整理助手，严格按用户要求输出 JSON。"},
                     {"role": "user", "content": prompt}],
                    kind="small", max_tokens=400, timeout=timeout)
                data = pool.parse_json_loose(raw)
                if not isinstance(data, dict):
                    raise ValueError("非 JSON 对象")
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Memory Dream 整合调用失败: %s", exc)
                return {"groups": [], "forget_ids": []}
        return _integrate

    def set_integrator(self, fn: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]]) -> None:
        """注入整合器（测试用）；None 恢复默认。"""
        with self._lock:
            self._integrator = fn

    def _get_integrator(self) -> Callable[[List[Dict[str, Any]]], Dict[str, Any]]:
        with self._lock:
            return self._integrator or self._default_integrator()

    # ------------------------------------------------------------ 快照
    def _write_snapshot(self, items: List[Dict[str, Any]]) -> str:
        """写入本次处理的条目快照，返回 revision id。"""
        rev = time.strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(3).hex()
        try:
            path = self.revisions_dir() / f"{rev}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "revision": rev,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "items": items,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return rev
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 快照写入失败: %s", exc)
            return ""

    def list_revisions(self) -> List[Dict[str, Any]]:
        """列出可回滚的快照。"""
        out = []
        try:
            for p in sorted(self.revisions_dir().glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    out.append({"revision": data.get("revision"), "path": str(p),
                                "created_at": data.get("created_at"),
                                "count": len(data.get("items") or [])})
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 快照列表失败: %s", exc)
        return out

    # ------------------------------------------------------------ 主流程
    def run_dream(self, role: Optional[str] = None) -> Dict[str, Any]:
        """执行一轮 Memory Dream 整合。

        :param role: 指定角色；None = 全部角色（推荐指定角色，范围可控）
        :return: {"added": n, "removed": n, "groups": n, "revision": str, "skipped": bool}
        """
        db = self._memory._db
        if db is None or db.long_term is None:
            return {"skipped": True, "reason": "chroma_unavailable"}
        try:
            with db.lock:
                data = db.long_term.get(include=["documents", "metadatas"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 读取长期记忆失败: %s", exc)
            return {"skipped": True, "reason": "read_failed"}

        ids = data.get("ids", []) or []
        docs = data.get("documents", []) or []
        metas = data.get("metadatas", []) or []

        # 过滤：指定角色 + 非 frozen
        items: List[Dict[str, Any]] = []
        for mid, doc, meta in zip(ids, docs, metas):
            meta = meta or {}
            if meta.get("mode") not in (None, MODE_NORMAL):
                continue
            if role and meta.get("role") != role:
                continue
            if meta.get("frozen"):
                continue
            if not doc or not str(doc).strip():
                continue
            items.append({"id": mid, "text": str(doc).strip(),
                          "meta": dict(meta)})
        if not items:
            return {"skipped": True, "reason": "no_items"}
        items = items[-_DEFAULT_LIMIT:]

        # 快照备份
        revision = self._write_snapshot(items)
        if not revision:
            return {"skipped": True, "reason": "snapshot_failed"}

        # LLM 整合
        integrator = self._get_integrator()
        try:
            result = integrator(items) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 整合器异常: %s", exc)
            return {"skipped": True, "reason": "integrator_error", "revision": revision}
        groups = result.get("groups") if isinstance(result, dict) else None
        forget_raw = result.get("forget_ids") if isinstance(result, dict) else None

        idx = {i: it for i, it in enumerate(items)}
        if not isinstance(groups, list):
            logger.info("Dream 无可合并项，跳过执行")
            self._record_state(revision, 0, 0, 0)
            return {"skipped": True, "reason": "no_groups", "revision": revision}

        # 校验并执行
        added = removed = group_count = 0
        try:
            with db.lock:
                for g in groups:
                    if not isinstance(g, dict):
                        continue
                    gids_raw = g.get("ids") or []
                    summary = str(g.get("summary") or "").strip()
                    if not summary or not isinstance(gids_raw, list):
                        continue
                    real_ids = []
                    for rid in gids_raw:
                        try:
                            i = int(rid)
                        except (TypeError, ValueError):
                            continue
                        if i in idx:
                            real_ids.append(idx[i]["id"])
                    if len(real_ids) < 2:
                        continue
                    real_ids = real_ids[:_MAX_GROUP_IDS]
                    # 删除组内旧条目
                    db.long_term.delete(ids=real_ids)
                    removed += len(real_ids)
                    # 新增综合记忆（沿用组内第一个条目的 role/group）
                    first_meta = idx[int(gids_raw[0])]["meta"]
                    self._memory._insert_memory(
                        db.long_term, summary,
                        first_meta.get("role") or role or "",
                        first_meta.get("group"), mode=MODE_NORMAL)
                    added += 1
                    group_count += 1
                # 遗忘：仅删除明确编号且未被合并的条目
                if isinstance(forget_raw, list):
                    merged_ids = {i for g in groups if isinstance(g, dict)
                                  for i in g.get("ids") or []}
                    forget_del = []
                    for rid in forget_raw:
                        try:
                            i = int(rid)
                        except (TypeError, ValueError):
                            continue
                        if i in idx and str(i) not in merged_ids:
                            forget_del.append(idx[i]["id"])
                    if forget_del:
                        db.long_term.delete(ids=forget_del)
                        removed += len(forget_del)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 执行写库失败: %s", exc)
            return {"skipped": True, "reason": "apply_failed", "revision": revision}

        self._record_state(revision, added, removed, group_count)
        logger.info("Memory Dream：合并 %d 组，新增 %d 条，删除 %d 条（revision=%s）",
                    group_count, added, removed, revision)
        return {"added": added, "removed": removed, "groups": group_count,
                "revision": revision, "skipped": False}

    def rollback(self, revision: str) -> int:
        """从快照恢复条目（重新 add 快照中的全部条目），返回恢复条数。

        注意：恢复会重新插入快照中的所有条目（即使部分已被遗忘曲线删除），
        重复 id 由 ChromaDB 自动去重（同 id 覆盖）。恢复后建议再跑一轮
        遗忘曲线清理。
        """
        path = self.revisions_dir() / f"{revision}.json"
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("items") or []
            db = self._memory._db
            if db is None or db.long_term is None:
                return 0
            restored = 0
            with db.lock:
                for it in items:
                    if not isinstance(it, dict) or not it.get("id"):
                        continue
                    self._memory._insert_memory(
                        db.long_term, str(it.get("text") or ""),
                        (it.get("meta") or {}).get("role") or "",
                        (it.get("meta") or {}).get("group"),
                        mode=MODE_NORMAL)
                    restored += 1
            return restored
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 回滚失败 %s: %s", revision, exc)
            return 0

    # ------------------------------------------------------------ 状态
    def _record_state(self, revision: str, added: int, removed: int,
                      groups: int) -> None:
        try:
            p = self.state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            state = {"last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "revision": revision, "added": added,
                     "removed": removed, "groups": groups}
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dream 状态写入失败: %s", exc)
