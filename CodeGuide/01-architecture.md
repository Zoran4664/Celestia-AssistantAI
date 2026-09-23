# 01 · 架构总览：启动链路 / 单例 / 线程 / 信号总线 / 配置中枢

> 适用：改启动顺序、初始化、跨模块通信、配置键、依赖管理、线程模型时先读这本。
> 行号基线：2026-09-19。

---

## 1. 启动链路

### 1.1 `start.py`（319 行）——启动器（tkinter）

| 符号 | 行号 | 作用 |
|---|---|---|
| `ROOT` / `REQ` / `MAIN` | 28-30 | 项目根 / `requirements.txt` / `main.py` |
| `_SPEC_RE` | 34 | 依赖行解析正则 |
| `_FALLBACK_DEPS` | 39-59 | **内置依赖清单（requirements.txt 的镜像，两者必须同步）** |
| `version_tuple` / `satisfies` | 69 / 75 | 版本比较（`==` 按 `>=` 语义） |
| `parse_requirements` | 92 | 解析 requirements.txt，文件缺失时回退 `_FALLBACK_DEPS` |
| `installed_version` / `check_deps` | 112 / 119 | 已装版本 / 汇总达标情况 |
| `upgrade_deps` | 129 | **子线程**逐个 pip 安装未达标库（回调输出行） |
| `check_python` | 192 | Python 版本提示 |
| `launch_direct` | 199 | **子进程**启动 `main.py` |
| `run_gui` | 206 | tkinter 启动器界面（刷新/升级/启动按钮） |
| `main` | 288 | `--direct` / `--setup` 分发；`--setup` 阻塞等待最多 600s |

**坑**
- 新增第三方库必须**同时**改 `requirements.txt` 与 `_FALLBACK_DEPS`。
- tkinter 缺失时降级为直接启动主程序。
- `rarfile` 属于「可选但推荐」（缺失时 `skill_import._extract_rar` 回退 7z/WinRAR/tar）。

### 1.2 `main.py`（228 行）——主程序入口

| 符号 | 行号 | 作用 |
|---|---|---|
| `parse_args` | 31 | `--config --no-pet --role --debug --no-gpu` |
| `main` | 44 | 主流程（返回退出码） |
| `_cleanup_action` | 146 | 记忆清理 Cron 动作 → `spawn_worker(memory.decay_and_forget)` |
| `_dream_action` | 161 | Memory Dream 动作 |
| `_diary_action` | 176 | 自动日记动作（`bus.request("ui:current_role")` → `AutoDiary.generate_diary_async`） |
| `_quit` | 212 | 停 cron/heartbeat 后 `app.quit()` |

**`main()` 顺序（改启动行为就看这里）**

```
force_utf8_stdio(46)
→ QApplication + 高DPI(58+)
→ 单实例守卫(76-80)
→ 日志初始化(83)
→ ConfigLoader.instance(89) → cfg.main_model()/font_family()(90/94)
→ SignalBus / RoleManager / MemoryPipeline / LLMClientPool(105-108)
→ MainWindow(initial_role=..., no_pet=...)(116)
→ [可选] CronManager(141-194)：注册 memory_cleanup(153)/memory_dream(167)/auto_diary(187)，start()
→ [可选] Heartbeat.instance().start()(198-202)   # cfg.heartbeat_enabled()
→ NotifyService.instance()(209)
→ bus.app_quit.connect(_quit)(219) → app.exec()
```

**配置键（main 直接读的）**：`ui.last_role`(113)、`pet.enabled`(121)、`memory.cleanup_interval_hours`(138)、`ui.auto_diary_enabled`(173)、`ui.auto_diary_time`(183)；间接经访问器：`cron.enabled`、`dream.enabled`、`dream.interval_hours`、`heartbeat.enabled`。

---

## 2. 配置中枢 `config_loader.py`（585 行）

- `project_root()`(24)；`DEFAULTS`(30-201) **全部默认配置**；`_deep_merge`(208) 深合并；`_sanitize`(219) 数值规整；`_ConfigModel`(253) 可选 pydantic 校验（失败仅告警）。
- `ConfigLoader.instance(config_path)` 单例；`reload()`(290)、`save()`(310)、`get(*keys, default)`(317)、`set(value, *keys)`(327)、`raw`(334)、`snapshot()`(339)。
- 环境变量回退：`OPENAI_API_BASE` / `OPENAI_API_KEY`（204-205）**优先于配置**（`api_base`(345)/`api_key`(353)）。
- 模型与端点访问器：`main_model`(376)、`small_model`(379)、`vision_model`(382)、`image_base/key/model/dir`(385-397)。
- 路径属性：`path(*keys, default)`(561)、`roles_dir/history_dir/skills_dir/chroma_dir/conversations_dir/skillspub_dir/skilluserdata_dir/theme_dir...`(567-618)。

**DEFAULTS 配置段**（键名/默认值以文件为准）：

| 段 | 说明 | 关键键 |
|---|---|---|
| `api` | 主/小/视觉/生图 接口与模型 | `api_base, api_key, main_model, small_model, small_base/key, vision_*, image_*` |
| `paths` | 各资源目录 | `roles, roles_img, roles_desktop, data, theme, skills, skillspub, skilluserdata, history, conversations` |
| `chat` | 采样默认 | `temperature(0.7), max_tokens(2048)` |
| `memory` | 记忆管线 | `trim_threshold(20), keep_rounds(10), top_k_long_term(3), top_k_important(2), denoise_enabled(True), denoise_timeout_seconds(5.0), dedup_high_similarity(0.15), dedup_partial_similarity(0.45), merge_max_depth(3), merge_drift_threshold(0.55), forgetting_interval_days(30), hit_threshold(8)` |
| `ui` | 界面与开关 | `theme_file, accent, icon_path, font_family, chat_font_size, scale, blur_background, transparent_chat, show_thinking, roleplay_enabled, roleplay_cards, skillspub_enabled, auto_diary_enabled/time, login_time_enabled, user_name, last_role/last_mode/last_group` |
| `pet` / `heartbeat` / `cron` / `notify` / `dream` / `pinned` / `web` / `search` / `memory_compile` / `skill_bundles` / `character_card` / `file_tools` | 各子系统 | 见 07 手册逐模块表 |

**坑**：① 环境变量会覆盖 API 配置；② `set()` 必须配 `save()`；③ `instance()` 非线程安全，应在启动早期单线程创建；④ 改配置后若已建 LLM 客户端，需 `LLMClientPool.reconfigure()`。

---

## 3. 线程与并发模型

| 场景 | 机制 | 说明 |
|---|---|---|
| 主流程 | Qt 主线程事件循环 | 所有 QWidget 操作只能在主线程 |
| 通用后台任务 | `utils/async_worker.spawn_worker(task, ..., on_done, on_fail)` | QThread + 模块级 `_KEEP_ALIVE` 集合防 GC 闪退；`AsyncWorker` 信号 `succeeded/failed/progress` |
| LLM 调用（非对话） | `llm_client.LLMWorker(QThread)` | 信号 `stream_chunk / request_finished / request_error` |
| 主对话 | `ui_manager.ChatWorker(QObject)` + `QThread` | 信号 `token/reasoning/emotion/finished/error/usage`；**禁止在 ChatWorker 内创建控件** |
| Cron 任务 | `cron_manager` QTimer 检查 + `threading.Thread(daemon)` 执行 | action 名与 `main.py` 的 `register_action` 必须一致 |
| 记忆写库 | `MemoryPipeline._db_lock`(RLock) + `ChromaDBManager.lock` | 所有 write/update/delete 必须持锁 |
| 网络探测 | `ponywebsite_checker` 用 `ThreadPoolExecutor` | 最多 8 并发 |

**坑**：① 子线程里创建 QObject/QWidget 会崩（`memory_compile` 因此**不继承 QObject**）；② Python 无法强杀线程，Cron 超时只记录；③ `LLMWorker` 实例必须被引用保活，否则线程中断。

---

## 4. 信号总线 `signal_bus.py`（147 行）

`SignalBus.instance()`(89)；能力注册表 `handle(capability, handler)`(97)/`unhandle`(106)/`request(capability, *args)`(122)/`capabilities()`(143)；注册表用 `threading.RLock`(87) 保护；`_SKIP=None`(31) 约定「不处理」。

> **能力（capability）约定**：`handler` 返回值 `None` 表示跳过，第一个非 None 的返回值即结果；handler 内**禁止耗时操作**（应内部再开线程）。

### 信号一览（名称 · 参数 · 发射者 → 接收者）

| 信号 | 参数 | 发射者 | 接收者 |
|---|---|---|---|
| `role_switched` | str | `ui_manager` 3704/3719/3758 | `pet_manager` 166 |
| `speaker_switched` | str | `ui_manager` 3704/3802/6204/8053 | `pet_manager` 168；测试 |
| `settings_updated` | — | `ui_manager` 9152 | `ui_manager` 3578 |
| `app_quit` | — | `ui_manager` 3465 | `main` 219、`ui_manager` 3583-3584 |
| `notify_emitted` | (str,str) | `notify_service` 93 | `ui_manager._on_notify_emitted` 3588 |
| `pet_say` | str | `notify_service` 99 | `pet_manager._on_pet_say` 251 |
| `show_main_requested` | — | `pet_manager` 358 | `ui_manager` 3577 |
| `pet_proactive` | — | —（预留） | `ui_manager` 3576 |
| `heartbeat_care_requested` | str | `heartbeat` 206 | `pet_manager` 170 |
| `memory_notice` / `memory_updated` | / — | `memory_pipeline` 936/937 | —（预留/日志） |
| `memory_compiled` | str | `memory_compile` 211 | —（预留） |
| `cron_task_done` | (str,str) | `cron_manager` 426 | —（预留） |
| `conversation_saved` | str | `ui_manager` 7689 | —（预留） |
| `skill_invoked` / `image_generated` | str | `ui_manager` 4489 / 5204 | —（预留） |
| `portrait_switched` | (str,str) | `ui_manager` 2965 | —（立绘切换通知） |
| `request_started` / `reply_stream` / `reply_finished` / `reply_error` / `emotion_changed` / `group_changed` / `pet_state` | — | 主界面（旧接口保留） | 已改直连 `ChatWorker` 信号，生产代码不再订阅 |
| `file_updated` / `heartbeat_tick` | — | 未使用 | 预留 |

**加新事件**：在类体加 `xxx = Signal(...)` → emitter 处 `.emit()` → receiver 的 `_connect_bus()`(3572) 里 `.connect()`。跨线程连接默认队列连接，安全。

**已注册能力**：`ui:window_active` → `ui_manager._ui_active_hint`(3593/3591)；`ui:current_role`（`main._diary_action` 请求，**当前无注册者**，会回退默认助手）。

---

## 5. 依赖与运行环境

- `requirements.txt`：PySide6、chromadb、numpy、openai、httpx、Pillow、python-dateutil、pydantic、PyPDF2/pypdf、python-docx、openpyxl、python-pptx、rarfile。
- 降级保护：无 chromadb → 纯内存记忆；默认 embedding 不可用 → `_HashEmbedding`；小模型超时/失败 → 回退原结果；无 tkinter → 直接启动主程序；无 Qt → `llm_client` 不定义 `LLMWorker`。
- Docker：`Dockerfile` / `docker-compose.yml` / `.dockerignore`（部署用途）。

---

## 6. 本文件相关坑速记

2. 任何**新配置键**都要加进 `DEFAULTS`，否则 `get` 只能靠 `default=` 兜底。
3. 新增第三方库 → `requirements.txt` + `start.py::_FALLBACK_DEPS`。
4. 新信号只在 `signal_bus.py` 定义但无人 connect 是允许的（预留），但**不要**在主流程里依赖其副作用。
