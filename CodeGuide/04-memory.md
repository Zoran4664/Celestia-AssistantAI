# 04 · 记忆系统

> 覆盖 `memory_pipeline.py`（943 行）、`memory_compile.py`、`memory_dream.py`、`pinned_memory.py`、`session_search.py`，以及**记忆隔离**（日常 / 角色扮演）。
> 行号基线：2026-09-19。

---

## 1. 三层记忆模型

| 层 | 存储 | 作用 | 入口 |
|---|---|---|---|
| 短期 | `history/conversations/talk_*.json`（deque） | 最近对话轮次，直接进上下文 | `recent_turns`、`add_short_note` |
| 长期 | ChromaDB 集合 `long_term` | 语义检索的历史事实 | `retrieve_before`、`_query_coll` |
| 重要 | ChromaDB 集合 `important` | 高价值记忆（长期注入） | 同上 |

- 向量库路径：`history/chroma/chroma.sqlite3`（`cfg.chroma_dir`）。
- 降级：无 chromadb → 纯内存模式；默认 onnx embedding 不可用 → `_HashEmbedding`（256 维 trigram）。

---

## 2. `memory_pipeline.py`（943 行）

**单例**：`ChromaDBManager.instance()`(117)、`MemoryPipeline.instance()`(202)。**本身不建线程**，设计为在调用方子线程执行。

### 2.1 关键符号

| 符号 | 行号 | 作用 |
|---|---|---|
| `_HashEmbedding` | 62 | 本地哈希 embedding（降级方案） |
| `ChromaDBManager.__init__` / `_pick_embedding` / `_init_db` / `embed` | 107 / 124 / 139 / 163 | 客户端与两集合初始化、embedding 选择与运行时报错回退 |
| `_st_key(role, group, mode, scope)` | 209 | 短期 key（文件安全；`roleplay_` 前缀 + scope） |
| `_load_short` / `_save_short` | 232 / 252 | 短期 deque 读写（`talk_<key>.json`） |
| `recent_turns(role, group, limit, mode, scope)` | 261 | 最近 N 轮（进上下文） |
| `add_short_note(role, content)` | 279 | 写非对话注记（如专注记录） |
| `clear_short_memory` | 290 | 清空短期 |
| `retrieve_before(prompt, role, group, denoise, mode, scope)` | 312 | **前置检索**：长期+重要 → 降噪 → 命中计数 → 拼串 |
| `_query_coll` | 357 | top-k 检索 + **mode/scope 过滤**（408-421） |
| `_bump_hits` / `_denoise` | 423 / 438 | 命中次数 +1 / 小模型降噪（超时回退原结果） |
| `archive_after(user_prompt, reply, role, group, mode, scope)` | 479 | **后置归档**：入短期 → 达阈值裁切 → 提炼入库 |
| `_distill_and_store` / `_distill_single` | 509 / 554 | 小模型拆分 important/long_term 入库 |
| `_dedup_and_merge` / `_dedup_and_merge_locked` | 583 / 594 | 防重 Merge（**持 `_db_lock`**）；三分支：≤high 丢弃 / ≤partial 合并 / 否则新增 |
| `_merge_memories` | 688 | 小模型合并 + 语义漂移距离 |
| `_insert_memory` | 728 | 写库（`merged_ids` **不得为空列表**，745-748） |
| `decay_and_forget` | 759 | 遗忘曲线：hits≥阈值固化 / 超期删除 |
| `forget(scope, value)` / `_drop_by_meta` | 809 / 831 | 按角色/群聊彻底抹除 |
| `forget_roleplay` / `_drop_by_mode` | 853 / 880 | 删除角色扮演记忆分区 |
| `train_from_talk_json(path)` | 902 | 德谬歌矩阵批量训练 |
| `_emit_notice` | 933 | 发 `memory_notice` + `memory_updated` |

**实例状态**：`ChromaDBManager`：`client/long_term/important`(109-111)、`lock`(112，RLock)、`_ef`/`_hash_fallback`(113-114)；`MemoryPipeline`：`_short`(196)、`_lock`(197)、`_db_lock`(198)、`_conversations_dir`(199)。

### 2.2 配置键

`memory.trim_threshold(20)`、`keep_rounds(10)`、`top_k_long_term(3)`、`top_k_important(2)`、`denoise_enabled(True)`、`denoise_timeout_seconds(5.0)`、`dedup_high_similarity(0.15)`、`dedup_partial_similarity(0.45)`、`merge_max_depth(3)`、`merge_drift_threshold(0.55)`、`forgetting_interval_days(30)`、`hit_threshold(8)`。

### 2.3 坑

1. 所有写操作必须持 `_db_lock`；Chroma 读写在 `ChromaDBManager.lock` 内。
2. 小模型（降噪/提炼/Merge）超时默认 5s，**失败一律回退不阻塞主流程**。
3. 旧数据可能缺 `mode`/`scope` 字段，检索时按 `normal`/`share` 兼容（408-420）。
4. `memory_pipeline.py` 末尾存在一段**死代码残片**（`if t:` + `_dedup_and_merge`，约 942-943 行，位于 `_emit_notice` 的 `except` 分支内）——正常流程不执行，若该 except 触发会 `NameError`，清理时请连带删除。

---

## 3. 记忆隔离（日常 ↔ 角色扮演）

| 层 | 位置 | 逻辑 |
|---|---|---|
| 配置 | `roleplay/roleplay_preset.py` `DEFAULT_PRESET["isolation"]`(141-146) | `isolate_normal=True`、`per_session` |
| 运行 | `ui_manager.py`：`_isolation()`(642-651)、`_roleplay_per_session()`(3854)、`_roleplay_scope()`(3863-3879)、`_session_matches_mode()`(3881) | 计算 `mode` 与 `scope`；`per_session` 时 scope=`uuid.hex[:8]` |
| 存储 | `memory_pipeline.py`：`MODE_NORMAL/MODE_ROLEPLAY`(40-41)、`_st_key`(209-230)、`_query_coll` 过滤(408-421)、`_drop_by_mode`(880) | 短期文件名前缀、长期 metadata 过滤 |

```python
# ui_manager.py ChatWorker.run（760-776，摘要）
_iso = self._isolation()
_mem_mode = "roleplay" if (_roleplay_enabled and _iso.get("isolate_normal", True)) else "normal"
_mem_scope = self._roleplay_scope if _roleplay_enabled else ""
ctx = self._memory.retrieve_before(full_prompt, _role, _group, mode=_mem_mode, scope=_mem_scope)
```


---

## 4. `memory_compile.py`——记忆传送带（daily/week 摘要）

**单例**：`MemoryCompile.instance()`(55)（**不继承 QObject**，避免子线程创建 QObject）。

| 符号 | 行号 | 作用 |
|---|---|---|
| `root_dir/daily_dir/daily_file` | 63 / 67 / 70 | `history/memory_compile/daily/<YYYY-MM-DD>.json` |
| `save_daily` / `read_daily` / `list_daily` | 74 / 94 / 106 | 原子写 / 读 / 最近 N 个已结束逻辑日 |
| `assemble_week_text(days=0)` | 117 | **零 LLM** 纯文件装配（week 段） |
| `_default_summarizer` / `set_summarizer` | 141 / 161 | 摘要器（默认小模型非流式） |
| `compile_today_text` / `compile_today_async` | 170 / 196 | 同步/异步生成今日摘要（异步发 `memory_compiled`） |
| `get_context(role)` | 220 | 注入文本（今天 + 最近几天） |

**配置**：`memory_compile.daily_retention_days(6)`、`memory.denoise_timeout_seconds(5.0)`。
**坑**：逻辑日以**凌晨 4 点**为界（`utils/logical_time`）；摘要失败返回空串且跳过写入。

---

## 5. `memory_dream.py`——记忆整合（可回滚）

**单例**：`MemoryDream.instance()`(57)。

| 符号 | 行号 | 作用 |
|---|---|---|
| `root_dir/revisions_dir/state_path` | 64 / 67 / 70 | `history/memory_dream/`、`revisions/`、`memory_dream_state.json` |
| `_default_integrator` / `set_integrator` | 74 / 112 | 整合器（小模型输出严格 JSON `{groups, forget_ids}`） |
| `_write_snapshot` / `list_revisions` / `rollback` | 122 / 140 / 274 | 快照 / 列出 / 回滚 |
| `run_dream(role=None)` | 157 | 主流程，返回 `{added, removed, groups, revision, skipped}` |
| `_record_state` | 307 | 写运行报告 |

**配置**：`dream.enabled`、`dream.interval_hours`（`main.py` 158/169 注册 Cron）、`memory.denoise_timeout_seconds`。
**坑**：① LLM 输出非 JSON 直接中止（绝不破坏数据）；② 只处理指定 role 且非 frozen、`mode∈(None,normal)` 的条目；③ **删除/新增前必写快照**；④ 单组上限 `_MAX_GROUP_IDS=8`。


---

## 6. `pinned_memory.py`——固定记忆

**单例**：`PinnedMemory.instance()`(52)；落盘 `data/pinned.md`（每行一条，人类可读）。

| 符号 | 行号 | 作用 |
|---|---|---|
| `scrub_pii(text)` | 34 | 手机/邮箱/身份证打码（`_PHONE_RE/_EMAIL_RE/_IDCARD_RE` 29-31） |
| `_path` / `enabled` | 58 / 61 | `pinned.file` / `pinned.enabled(True)` |
| `read_pinned` / `_write` | 65 / 84 | 按行读（忽略空行与 `#`）/ 原子写 |
| `add_pinned` / `remove_pinned` / `clear_pinned` / `set_pinned` | 98 / 114 / 124 / 131 | 增/删/清/替换（含去重与 PII 脱敏） |
| `get_context` | 145 | 注入文本（关闭时返回空） |

**坑**：去重是「精确或包含」匹配（107-108），短条目可能误判；任意线程可用。

---

## 7. `session_search.py`——会话全文搜索

纯函数、无单例；两阶段（标题 → 正文）+ 中文分词打分。

| 符号 | 行号 | 作用 |
|---|---|---|
| `_tokenize` | 31 | 标点切分 + CJK 2-gram |
| `_score` | 54 | 整句 100 / 连续命中×2 / 分词×1.5 |
| `_snippet` | 81 | 命中上下文片段 |
| `_read_session` | 103 | 读会话文件（损坏→None） |
| `search_sessions(query, limit, conversations_dir)` | 112 | 主搜索，返回 `{path,title,role,group,modified,score,match_kind}` |

**配置**：`search.max_results(30)`；会话目录 `history/conversations`。
**坑**：标题命中直接 `continue` 并 `+50` 加权；仅当会话 ≤30 条消息才拼全文比对。


---

