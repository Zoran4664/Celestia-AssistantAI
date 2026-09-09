"""
memory_pipeline.py — 三级深度记忆管线核心（门面模式）

层级：
  短期  : 内存 deque + history/conversations/talk_<key>.json 持久化
  长期  : ChromaDB Collection "long_term"（cosine，按遗忘曲线衰减）
  重要  : ChromaDB Collection "important"（cosine，永久豁免）

管线（LLM 调用均在调用方子线程中执行，GUI 不阻塞）：
  retrieve_before(): 前置检索（ltm+important）→ 小模型降噪（超时降级）→ 命中计数
  archive_after()  : 后置归档：入短期 → 达阈值裁切 → 小模型提炼 → 防重/Merge 入库
  decay_and_forget(): 遗忘曲线清理 + 高频词条固化
  forget()         : 遗忘协议（按角色/群聊彻底抹除）
  train_from_talk_json(): 德谬歌矩阵批量训练

降级保护：
  - chromadb 未安装 / 初始化失败 → 纯内存模式（短期记忆仍生效）
  - 默认 embedding 不可用 → 自动回退本地哈希 embedding
  - 小模型降噪超时/失败 → 回退原始检索结果，绝不阻塞主流程
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.Memory")

try:
    import chromadb  # type: ignore
    from chromadb.config import Settings as _ChromaSettings  # type: ignore
except Exception:  # pragma: no cover - chromadb 未安装时降级
    chromadb = None  # type: ignore
    _ChromaSettings = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

from config_loader import ConfigLoader
from llm_client import LLMClientPool


# ----------------------------------------------------------------------
# 本地哈希 embedding（默认 onnx embedding 不可用时的降级方案）
# ----------------------------------------------------------------------
class _HashEmbedding:
    """确定性 trigram 词袋 embedding，维度 256，L2 归一化。

    纯本地计算，离线可用；仅用于默认 embedding 加载失败时的兜底，
    保证记忆管线在无外网/无 onnx 运行时环境下依然可用。
    """

    # chromadb 1.x 要求 name() 为方法；0.5.x 亦可读取
    DIM = 256

    def name(self) -> str:
        return "hash-embedding"

    def __call__(self, input: Any) -> Any:
        import zlib

        if np is None:
            raise RuntimeError("numpy 未安装，无法使用哈希 embedding")
        if isinstance(input, str):
            input = [input]
        results = []
        for doc in input:
            vec = np.zeros(self.DIM, dtype=np.float32)
            text = (doc or "").lower()
            for ch in text:
                vec[ord(ch) % self.DIM] += 1.0
            for i in range(max(0, len(text) - 2)):
                tri = text[i:i + 3]
                h = zlib.crc32(tri.encode("utf-8")) % self.DIM
                vec[h] += 0.5
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            results.append(vec.tolist())
        return results


# ----------------------------------------------------------------------
# ChromaDB 管理器
# ----------------------------------------------------------------------
class ChromaDBManager:
    """ChromaDB 持久化客户端与集合管理（单例，线程锁保护写操作）。"""

    _instance: Optional["ChromaDBManager"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self.client = None          # PersistentClient
        self.long_term = None       # 长期集合
        self.important = None       # 重要集合
        self.lock = threading.RLock()
        self._ef = _HashEmbedding() if np is not None else None
        self._hash_fallback = np is not None
        self._init_db()

    @classmethod
    def instance(cls) -> "ChromaDBManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 初始化
    def _pick_embedding(self) -> Any:
        """选择 embedding：优先 chromadb 默认（onnx MiniLM），失败回退哈希。"""
        if self._hash_fallback or np is None:
            return _HashEmbedding()
        try:
            from chromadb.utils import embedding_functions  # type: ignore

            ef = embedding_functions.DefaultEmbeddingFunction()
            ef(["探测文本"])  # 触发一次下载/加载，失败即降级
            return ef
        except Exception as exc:  # noqa: BLE001
            logger.warning("默认 embedding 不可用，回退本地哈希 embedding: %s", exc)
            self._hash_fallback = True
            return _HashEmbedding()

    def _init_db(self) -> None:
        if chromadb is None or _ChromaSettings is None:
            logger.warning("chromadb 未安装，记忆库降级为纯内存模式（短期记忆仍生效）")
            return
        try:
            path = str(self._cfg.chroma_dir)
            self._cfg.chroma_dir.mkdir(parents=True, exist_ok=True)
            settings = _ChromaSettings(anonymized_telemetry=False)
            self.client = chromadb.PersistentClient(path=path, settings=settings)
            ef = self._pick_embedding()
            self.long_term = self.client.get_or_create_collection(
                "long_term", metadata={"hnsw:space": "cosine"}, embedding_function=ef
            )
            self.important = self.client.get_or_create_collection(
                "important", metadata={"hnsw:space": "cosine"}, embedding_function=ef
            )
            logger.info("ChromaDB 初始化完成: %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaDB 初始化失败，降级纯内存模式: %s", exc)
            self.client = None
            self.long_term = None
            self.important = None

    # ------------------------------------------------------------ embedding
    def embed(self, texts: Any) -> List[Any]:
        """计算文本 embedding；默认 embedding 运行时报错时自动切换哈希方案。"""
        if self._ef is None:
            raise RuntimeError("embedding 不可用（numpy/chromadb 均缺失）")
        try:
            return self._ef(texts)
        except Exception:  # noqa: BLE001
            if not self._hash_fallback:
                self._hash_fallback = True
                self._ef = _HashEmbedding()
                logger.warning("默认 embedding 运行失败，已切换本地哈希 embedding")
                return self._ef(texts)
            raise

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _col_count(coll: Any) -> int:
        try:
            return coll.count()
        except Exception:  # noqa: BLE001
            return 0

# ----------------------------------------------------------------------
# 记忆管线门面
# ----------------------------------------------------------------------
class MemoryPipeline:
    """三级记忆管线门面（单例）。"""

    _instance: Optional["MemoryPipeline"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._db = ChromaDBManager.instance()
        self._short: Dict[str, Deque[Dict[str, Any]]] = {}
        self._lock = threading.RLock()          # 短期队列锁
        self._db_lock = threading.RLock()       # ChromaDB 写入串行化锁（防并发冲突）
        self._conversations_dir = self._cfg.conversations_dir
        self._conversations_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance(cls) -> "MemoryPipeline":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 短期记忆
    @staticmethod
    def _st_key(role: str, group: Optional[str]) -> str:
        return f"group:{group}" if group else role

    def _load_short(self, key: str) -> Deque[Dict[str, Any]]:
        with self._lock:
            if key in self._short:
                return self._short[key]
            maxlen = max(4, int(self._cfg.get("memory", "trim_threshold", default=20))
                         + int(self._cfg.get("memory", "keep_rounds", default=10)) * 2)
            dq: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
            path = self._conversations_dir / f"talk_{key}.json"
            if path.exists():
                try:
                    items = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(items, list):
                        for it in items:
                            if isinstance(it, dict):
                                dq.append(it)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("短期记忆加载失败 %s: %s", key, exc)
            self._short[key] = dq
            return dq

    def _save_short(self, key: str, dq: Deque[Dict[str, Any]]) -> None:
        path = self._conversations_dir / f"talk_{key}.json"
        try:
            path.write_text(
                json.dumps(list(dq), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("短期记忆保存失败 %s: %s", key, exc)

    def recent_turns(self, role: str, group: Optional[str] = None,
                     limit: int = 10) -> List[Dict[str, str]]:
        """返回最近 limit 轮对话（供主模型上下文拼接）。"""
        dq = self._load_short(self._st_key(role, group))
        items = list(dq)[-limit * 2:]
        return [
            {
                "role": "user" if it.get("role") == "user" else "assistant",
                "name": it.get("name") or ("用户" if it.get("role") == "user" else role),
                "content": it.get("content") or "",
            }
            for it in items
        ]

    def add_short_note(self, role: str, content: str) -> None:
        """把一条非对话注记写入短期记忆（供后续聊天上下文使用）。"""
        if not content or not content.strip():
            return
        key = self._st_key(role, None)
        dq = self._load_short(key)
        with self._lock:
            dq.append({"role": "assistant", "name": role,
                       "content": content.strip(), "ts": time.time(), "note": True})
        self._save_short(key, dq)

    def clear_short_memory(self) -> int:
        """清空全部短期记忆（内存 deque + talk_*.json 落盘），返回清除条数。"""
        count = 0
        with self._lock:
            for dq in self._short.values():
                count += len(dq)
            self._short.clear()
        removed_files = 0
        try:
            for fp in self._conversations_dir.glob("talk_*.json"):
                try:
                    fp.unlink()
                    removed_files += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        if count or removed_files:
            logger.info("清空短期记忆：内存 %d 条，删除存档 %d 个", count, removed_files)
        return count

    # ------------------------------------------------------------ 前置检索
    def retrieve_before(self, prompt: str, role: str,
                        group: Optional[str] = None,
                        denoise: Optional[bool] = None) -> str:
        """对话前检索记忆并组装上下文。

        流程：ChromaDB 初筛（long_term + important）→ 小模型降噪（超时/失败自动
        回退原始结果）→ 命中计数 → 组装「[重要记忆] + [长期记忆]」上下文串。
        返回空串表示无相关记忆。
        """
        if self._db.client is None or not prompt or not prompt.strip():
            return ""
        cfg = self._cfg
        top_lt = int(cfg.get("memory", "top_k_long_term", default=3))
        top_imp = int(cfg.get("memory", "top_k_important", default=2))

        lt_hits = self._query_coll(self._db.long_term, prompt, top_lt, role, group)
        imp_hits = self._query_coll(self._db.important, prompt, top_imp, role, group)
        if not lt_hits and not imp_hits:
            return ""

        denoise_enabled = cfg.get("memory", "denoise_enabled", default=True)
        if denoise is not None:
            denoise_enabled = denoise
        if denoise_enabled and lt_hits:
            lt_hits = self._denoise(prompt, lt_hits)

        self._bump_hits(lt_hits)

        sections: List[str] = []
        if imp_hits:
            sections.append("[重要记忆]\n" + "\n".join(f"- {h['doc']}" for h in imp_hits))
        if lt_hits:
            sections.append("[长期记忆]\n" + "\n".join(f"- {h['doc']}" for h in lt_hits))
        return "\n\n".join(sections)

    # ------------------------------------------------------------ 检索工具
    def _query_coll(self, coll: Any, text: str, k: int,
                    role: str, group: Optional[str]) -> List[Dict[str, Any]]:
        """从指定集合检索 top-k 相关记忆（带角色/群聊元数据过滤）。"""
        if coll is None or k <= 0:
            return []
        try:
            emb = self._db.embed([text])
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 计算失败: %s", exc)
            return []
        where: Dict[str, str] = {"group": group} if group else {"role": role}
        try:
            with self._db.lock:
                res = coll.query(
                    query_embeddings=emb, n_results=k, where=where,
                    include=["documents", "metadatas", "distances"],
                )
        except Exception:  # noqa: BLE001 - 空集合 / where 无匹配
            try:
                with self._db.lock:
                    count = ChromaDBManager._col_count(coll)
                    if count <= 0:
                        return []
                    res = coll.query(
                        query_embeddings=emb,
                        n_results=min(k, count),
                        include=["documents", "metadatas", "distances"],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChromaDB 检索失败: %s", exc)
                return []
        hits: List[Dict[str, Any]] = []
        ids = res.get("ids", [[]])[0] or []
        docs = res.get("documents", [[]])[0] or []
        dists = res.get("distances", [[]])[0] or []
        metas = res.get("metadatas", [[]])[0] or []
        for i, mid in enumerate(ids):
            hits.append({
                "id": mid,
                "doc": docs[i] if i < len(docs) else "",
                "distance": dists[i] if i < len(dists) else 1.0,
                "meta": metas[i] if i < len(metas) else {},
            })
        return hits

    def _bump_hits(self, hits: List[Dict[str, Any]]) -> None:
        """更新长期记忆命中次数与最近使用时间。"""
        for h in hits:
            mid = h.get("id")
            if not mid:
                continue
            meta = dict(h.get("meta") or {})
            meta["hits"] = int(meta.get("hits", 0)) + 1
            meta["last_used"] = time.time()
            try:
                with self._db.lock:
                    self._db.long_term.update(ids=[mid], metadatas=[meta])
            except Exception:  # noqa: BLE001
                pass

    def _denoise(self, prompt: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """小模型降噪：剔除「向量相近但逻辑无关」的脏记忆。

        内置三重降级：denoise_enabled=false 直接跳过；调用超时放弃等待；
        调用失败同样回退原始结果。绝不阻塞主对话流程。
        """
        if not hits:
            return hits
        timeout = float(self._cfg.get("memory", "denoise_timeout_seconds", default=5.0))
        try:
            pool = LLMClientPool.instance()
            items = "\n".join(
                f"{i + 1}. {h['doc']} (相似度 {1 - h['distance']:.2f})"
                for i, h in enumerate(hits)
            )
            system = (
                "你是记忆降噪过滤器。用户问题与候选记忆如下，请输出与问题"
                "逻辑相关的候选编号（逗号分隔）；若无任何相关，输出空列表 []。"
                "只输出编号，不要输出其它内容。"
            )
            user = f"问题: {prompt}\n\n候选记忆:\n{items}"
            resp = pool.chat_complete(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                kind="small", temperature=0.0, max_tokens=64, timeout=timeout,
            )
            idx = [int(x) for x in re.findall(r"\d+", resp)]
            kept = [h for i, h in enumerate(hits) if (i + 1) in idx]
            if kept:
                logger.info("记忆降噪: %d/%d 条保留", len(kept), len(hits))
                return kept
            # 小模型未给出有效编号 → 保守保留全部
            return hits
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆降噪失败/超时，回退原始结果: %s", exc)
            return hits




    # ------------------------------------------------------------ 后置归档
    def archive_after(self, user_prompt: str, reply: str, role: str,
                      group: Optional[str] = None) -> None:
        """对话结束后归档：入短期 → 达阈值裁切 → 小模型提炼 → 防重入库。"""
        key = self._st_key(role, group)
        dq = self._load_short(key)
        with self._lock:
            dq.append({"role": "user", "name": "用户", "content": user_prompt, "ts": time.time()})
            dq.append({"role": "assistant", "name": role, "content": reply, "ts": time.time()})
        self._save_short(key, dq)

        threshold = max(4, int(self._cfg.get("memory", "trim_threshold", default=20)))
        keep = max(2, int(self._cfg.get("memory", "keep_rounds", default=10)))
        if len(dq) >= threshold:
            keep_count = keep * 2
            cut_items = list(dq)[: max(0, len(dq) - keep_count)]
            if cut_items:
                self._distill_and_store(cut_items, role, group)
                with self._lock:
                    dq = deque(list(dq)[-keep_count:], maxlen=dq.maxlen)
                    self._short[key] = dq
                self._save_short(key, dq)
                self._emit_notice(f"记忆归档完成：提炼 {len(cut_items)} 条对话")

    # ------------------------------------------------------------ 提炼入库
    def _distill_and_store(self, items: List[Dict[str, Any]], role: str,
                           group: Optional[str]) -> None:
        """将裁切出的对话交给小模型，拆分为「重要记忆」与「长期记忆」并入库。"""
        pairs: List[str] = []
        i = 0
        while i + 1 < len(items):
            u, a = items[i], items[i + 1]
            if u.get("role") == "user" and a.get("role") == "assistant":
                pairs.append(f"用户: {u.get('content', '')}\n{role}: {a.get('content', '')}")
            i += 2
        if not pairs:
            return
        timeout = float(self._cfg.get("memory", "denoise_timeout_seconds", default=10.0))
        try:
            pool = LLMClientPool.instance()
            system = (
                "你是记忆提炼器。将以下对话提炼为结构化记忆，只输出 JSON："
                '{"important": ["核心约定/长期目标"], "long_term": ["背景/偏好/事实"]}'
                "；没有的类别输出空数组。"
            )
            user = "\n\n".join(pairs)
            resp = pool.chat_complete(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                kind="small", temperature=0.2, max_tokens=512, timeout=timeout,
            )
            data = pool.parse_json_loose(resp) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆提炼失败（跳过本轮归档）: %s", exc)
            return
        important = data.get("important") if isinstance(data.get("important"), list) else []
        long_term = data.get("long_term") if isinstance(data.get("long_term"), list) else []
        for text in important:
            text = str(text).strip()
            if text:
                self._dedup_and_merge(text, role, group, self._db.important)
        for text in long_term:
            text = str(text).strip()
            if text:
                self._dedup_and_merge(text, role, group, self._db.long_term)

    def _distill_single(self, text: str, role: str, group: Optional[str]) -> None:
        """单条信息提炼（德谬歌矩阵训练用）。"""
        if not text.strip():
            return
        timeout = float(self._cfg.get("memory", "denoise_timeout_seconds", default=10.0))
        try:
            pool = LLMClientPool.instance()
            system = (
                "你是记忆提炼器。将以下信息提炼为记忆，只输出 JSON："
                '{"important": ["核心约定/长期目标"], "long_term": ["背景/偏好/事实"]}'
                "；没有的类别输出空数组。"
            )
            resp = pool.chat_complete(
                [{"role": "system", "content": system}, {"role": "user", "content": text}],
                kind="small", temperature=0.2, max_tokens=256, timeout=timeout,
            )
            data = pool.parse_json_loose(resp) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("单条记忆提炼失败: %s", exc)
            return
        important = data.get("important") if isinstance(data.get("important"), list) else []
        long_term = data.get("long_term") if isinstance(data.get("long_term"), list) else []
        for t in important + long_term:
            t = str(t).strip()

    # ------------------------------------------------------------ 防重 / Merge
    def _dedup_and_merge(self, text: str, role: str, group: Optional[str],
                         coll: Any) -> None:
        """向量防重与 Merge 三分支决策（线程安全入口）。

        整个防重/合并决策纳入 _db_lock 串行化，防止多线程（归档 / 训练 /
        遗忘清理）并发写入冲突。
        """
        with self._db_lock:
            self._dedup_and_merge_locked(text, role, group, coll)

    def _dedup_and_merge_locked(self, text: str, role: str,
                                group: Optional[str], coll: Any) -> None:
        """向量防重与 Merge 三分支决策（调用方须持有 _db_lock）：
        极高相似（≤high）→ 丢弃；局部相似（≤partial）→ 小模型 Merge 覆盖；
        互不相干 → 插入新记忆。

        Merge 防冲突加固：
        - Merge 链深度上限（merged_ids），防止记忆合并振荡；
        - Merge 后二次防重：合并结果与库中其它条目高相似则丢弃；
        - 语义漂移检测：合并文本与旧文本向量距离过大时改为新增，避免覆盖失真。
        """
        if coll is None:
            return
        high = float(self._cfg.get("memory", "dedup_high_similarity", default=0.15))
        partial = float(self._cfg.get("memory", "dedup_partial_similarity", default=0.45))
        max_depth = int(self._cfg.get("memory", "merge_max_depth", default=3))
        drift_threshold = float(self._cfg.get("memory", "merge_drift_threshold",
                                              default=0.55))
        try:
            emb = self._db.embed([text])
        except Exception as exc:  # noqa: BLE001
            logger.warning("入库 embedding 失败: %s", exc)
            return
        try:
            with self._db.lock:
                res = coll.query(
                    query_embeddings=emb, n_results=1,
                    include=["documents", "metadatas", "distances"],
                )
            ids = res.get("ids", [[]])[0] or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("防重检索失败: %s", exc)
            return
        if not ids:
            self._insert_memory(coll, text, role, group, emb)
            return
        distance = float((res.get("distances", [[1.0]])[0] or [1.0])[0])
        old_doc = (res.get("documents", [[]])[0] or [""])[0]
        old_meta = (res.get("metadatas", [[]])[0] or [{}])[0] or {}
        mid = ids[0]

        if distance <= high:
            logger.info("防重命中（%.3f）直接丢弃: %s", distance, text[:40])
            return
        if distance <= partial:
            # Merge 链深度防护：合并次数达到上限 → 放弃合并，直接丢弃
            merged_chain = old_meta.get("merged_ids")
            chain = (merged_chain if isinstance(merged_chain, list) else [])
            if len(chain) >= max_depth:
                logger.info("Merge 链已达上限（%d 次），丢弃新记忆: %s",
                            max_depth, text[:40])
                return
            merged, drift = self._merge_memories(old_doc, text)
            merged = merged if merged else text
            # 语义漂移检测：合并结果与旧文本距离过大 → 保留旧条，另存新条
            if drift is not None and drift > drift_threshold:
                logger.info("Merge 语义漂移（%.3f > %.3f），改为新增: %s",
                            drift, drift_threshold, merged[:40])
                self._insert_memory(coll, merged, role, group)
                return
            # 二次防重：合并结果重新 query，若与库中其它条目高相似则丢弃
            try:
                merged_emb = self._db.embed([merged])
                with self._db.lock:
                    res2 = coll.query(
                        query_embeddings=merged_emb, n_results=1,
                        include=["distances"],
                    )
                ids2 = res2.get("ids", [[]])[0] or []
                dist2 = float((res2.get("distances", [[1.0]])[0] or [1.0])[0])
                if ids2 and ids2[0] != mid and dist2 <= high:
                    logger.info("Merge 后二次防重命中（%.3f）丢弃: %s",
                                dist2, merged[:40])
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Merge 后二次防重失败: %s", exc)

            meta = dict(old_meta)
            meta["merged_at"] = time.time()
            meta["merged_ids"] = chain + [mid]
            try:
                with self._db.lock:
                    coll.update(
                        ids=[mid], documents=[merged],
                        embeddings=self._db.embed([merged]), metadatas=[meta],
                    )
                logger.info("Merge 记忆（%.3f）: %s", distance, merged[:60])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Merge 写入失败: %s", exc)
            return
        self._insert_memory(coll, text, role, group, emb)

    def _merge_memories(self, old: str, new: str) -> "tuple[str, Optional[float]]":
        """小模型将新旧两条相关记忆合并为更完整的一条。

        返回 (合并文本, 语义漂移距离)。漂移距离为合并文本与旧文本之间的
        余弦距离（0~2，与 ChromaDB cosine 距离口径一致）；计算失败返回 None，
        上层按无漂移处理（沿用原有覆盖逻辑）。
        """
        timeout = float(self._cfg.get("memory", "denoise_timeout_seconds", default=5.0))
        try:
            pool = LLMClientPool.instance()
            resp = pool.chat_complete(
                [{"role": "system", "content": (
                    "你是记忆合并器。将新旧两条相关记忆合并为一条更完整、无重复的"
                    "记忆。只输出合并后的记忆文本。")},
                 {"role": "user", "content": f"旧记忆: {old}\n新记忆: {new}"}],
                kind="small", temperature=0.2, max_tokens=256, timeout=timeout,
            )
            merged = resp.strip()
            merged = merged if 0 < len(merged) <= 500 else new
        except Exception as exc:  # noqa: BLE001
            logger.warning("Merge 调用失败，保留新记忆: %s", exc)
            return new, None
        # 语义漂移距离（余弦距离 = 1 - 余弦相似度）
        try:
            if np is None:
                return merged, None
            e_old = self._db.embed([old])
            e_merged = self._db.embed([merged])
            a = np.asarray(e_old[0], dtype=np.float64)
            b = np.asarray(e_merged[0], dtype=np.float64)
            na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
            if na > 0 and nb > 0:
                cos = float(np.dot(a, b) / (na * nb))
                drift = max(0.0, 1.0 - cos)
            else:
                drift = 0.0
            return merged, drift
        except Exception:  # noqa: BLE001
            return merged, None

    def _insert_memory(self, coll: Any, text: str, role: str,
                       group: Optional[str], emb: Optional[List[Any]] = None) -> None:
        mid = f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        meta: Dict[str, Any] = {
            "role": role,
            "group": group or "",
            "created_at": time.time(),
            "hits": 0,
            "last_used": time.time(),
            "frozen": 0,
            # 注意：merged_ids 不可写入空列表——ChromaDB 校验要求列表 metadata
            # 非空，否则 add() 抛 ValueError「...to be non-empty in add」，导致
            # 记忆入库全部失败。该字段仅在 Merge 覆盖写入时记录非空链（防振荡）；
            # 旧数据缺省时按空链处理（_dedup_and_merge_locked 已兼容 None/缺失）。
        }
        if emb is None:
            emb = self._db.embed([text])
        try:
            with self._db.lock:
                coll.add(ids=[mid], embeddings=emb, documents=[text], metadatas=[meta])
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆入库失败: %s", exc)

    # ------------------------------------------------------------ 遗忘曲线
    def decay_and_forget(self) -> int:
        """遗忘曲线定时清理：
        - hits ≥ hit_threshold 的词条「固化」（frozen=1，不再清理）
        - 低频且超过 forgetting_interval_days 未命中 → 删除
        - important 集合永久豁免
        返回删除条数。
        """
        if self._db.long_term is None:
            return 0
        interval = float(self._cfg.get("memory", "forgetting_interval_days", default=30)) * 86400
        hit_threshold = int(self._cfg.get("memory", "hit_threshold", default=8))
        try:
            with self._db.lock:
                data = self._db.long_term.get(include=["metadatas"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("遗忘曲线扫描失败: %s", exc)
            return 0
        ids = data.get("ids", []) or []
        metas = data.get("metadatas", []) or []
        now = time.time()
        removed = 0
        frozen = 0
        for mid, meta in zip(ids, metas):
            meta = meta or {}
            if meta.get("frozen"):
                continue
            hits = int(meta.get("hits", 0))
            if hits >= hit_threshold:
                new_meta = dict(meta)
                new_meta["frozen"] = 1
                try:
                    with self._db.lock:
                        self._db.long_term.update(ids=[mid], metadatas=[new_meta])
                    frozen += 1
                except Exception:  # noqa: BLE001
                    pass
                continue
            last = float(meta.get("last_used", meta.get("created_at", now)))
            if now - last > interval:
                try:
                    with self._db.lock:
                        self._db.long_term.delete(ids=[mid])
                    removed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("遗忘删除失败: %s", exc)
        if removed or frozen:
            logger.info("遗忘曲线：删除 %d 条，固化 %d 条", removed, frozen)
        return removed

    # ------------------------------------------------------------ 遗忘协议
    def forget(self, scope: str, value: str) -> int:
        """彻底抹除指定作用域的记忆（scope ∈ {'role', 'group'}）。

        清除：短期会话存档 + long_term + important 中对应元数据条目。
        """
        key = f"group:{value}" if scope == "group" else value
        removed = 0
        with self._lock:
            self._short.pop(key, None)
        path = self._conversations_dir / f"talk_{key}.json"
        if path.exists():
            try:
                path.unlink()
                removed += 1
            except Exception:  # noqa: BLE001
                pass
        for coll in (self._db.long_term, self._db.important):
            removed += self._drop_by_meta(coll, scope, value)
        if removed:
            logger.info("遗忘协议执行：%s = %s，清除 %d 条", scope, value, removed)
        return removed

    def _drop_by_meta(self, coll: Any, scope: str, value: str) -> int:
        """删除集合中 role/group 元数据等于指定值的全部条目。"""
        if coll is None:
            return 0
        key = "group" if scope == "group" else "role"
        try:
            with self._db.lock:
                data = coll.get(include=["metadatas"])
        except Exception:  # noqa: BLE001
            return 0
        ids = data.get("ids", []) or []
        metas = data.get("metadatas", []) or []
        to_del = [mid for mid, m in zip(ids, metas) if (m or {}).get(key) == value]
        if to_del:
            try:
                with self._db.lock:
                    coll.delete(ids=to_del)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除记忆失败: %s", exc)
        return len(to_del)

    # ------------------------------------------------------------ 德谬歌矩阵训练
    def train_from_talk_json(self, path: str) -> int:
        """批量训练：将 talk.json（[{role, content, group}]）经「提炼 → 防重 → 入库」
        注入长期记忆。返回处理条数。
        """
        p = Path(path)
        if not p.exists():
            logger.warning("训练文件不存在: %s", path)
            return 0
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("训练文件解析失败: %s", exc)
            return 0
        if not isinstance(items, list):
            return 0
        count = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            role = it.get("role") or "默认助手"
            content = (it.get("content") or "").strip()
            group = it.get("group")
            if not content:
                continue
            self._distill_single(content, role, group)
            count += 1
        logger.info("德谬歌矩阵训练完成：%d 条", count)
        self._emit_notice(f"德谬歌矩阵训练完成：{count} 条")
        return count

    # ------------------------------------------------------------ 通知
    def _emit_notice(self, text: str) -> None:
        try:
            from signal_bus import SignalBus
            SignalBus.instance().memory_notice.emit(text)
            SignalBus.instance().memory_updated.emit()
        except Exception:  # noqa: BLE001 - 无 GUI 环境时静默
            pass


            if t:
                self._dedup_and_merge(t, role, group, self._db.long_term)
