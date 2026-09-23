# CodeGuide · 入口索引（**先读这一本**）

> **用途**：把整个仓库压缩成「按需索引」。读完本文件即可决定**只打开哪一本手册**，不必全仓乱翻，避免浪费 token。
> **基线版本**：2026-09-19 工作区快照。文中行号为该版本实测值，**改代码后会漂移**：请优先按**符号名**（函数/类/变量名）搜索定位，行号只当导航起点。
> **配套**：根目录 `0-8set.md`（总说明书）、`README.md`（版本记录）、`set.md / roleset.md / helpset.md / groupset.md / toolsset.md / skillspubset.md / dailymanger.md / 角色扮演预设管理器使用指南.md`（各需求原始文档，见 `09-data-assets.md` 附录）。

---

## 0. 三步读法

1. 在下面「2. 任务 → 手册」表里找到任务类型 → 打开指定手册。
2. 在该手册的「关键符号表」里按符号名定位，直接 `read_file` 该区间（不要整文件读）。
3. 动手前看该手册的「坑」与「验证命令」；改完执行：

```powershell
python -X utf8 -m py_compile ui_manager.py    # 单文件语法校验（发布版唯一随包自检）
```

> **注意（2026-09-23）**：为发布 GitHub，**`tools/` 测试套件与开发记录已随本次清理
> 移除**（`code_history_list.md`、`08-tools-tests.md`，以及 72 个测试脚本 + 汇总器 +
> 探针 + 测试报告）。本指南各手册里出现的 `tools/xxx_test.py` 验证命令**已不可用**，
> 只保留其记载的「验证思路」；需要回归时请自行补写测试。

---

## 0.1 全局硬性规则（**所有改动都必须遵守**）

| # | 规则 | 说明 |
|---|---|---|
| R1 | **前端界面美化不得使用 emoji 表情** | 界面文案、按钮、状态提示、天气/工具卡片、通知等一律用**文字 + 图形/颜色/QSS**表达，禁止用 😀🌤️⚠️ 之类 emoji 充当图标或装饰。需要图标时用 `img/` 下的图片或纯 CSS 形状。 |
| R2 | **一律使用 UTF-8 执行** | 所有命令加 `python -X utf8 ...`（入口脚本已调 `utils.utf8.force_utf8_stdio()`）；代码中所有文件读写显式 `encoding="utf-8"`（读外部/旧文件时才允许 GBK 回退）；新增文本文件均存为 UTF-8。 |
| R3 | **绝不提交真实密钥**（`2026-09-23` 新增） | 事故复盘：曾把真实 KEY 写进 `skills/skilltools_information.json` 并推到 GitHub，事后只能作废 —— 历史里的字符串删不干净。现在有三层护栏：① 提交时 `.githooks/pre-commit` → `secret_guard.py --staged`，命中即**阻止提交**（占位符可 `SKIP_SECRET_GUARD=1` 绕过）；② 交付/上传前跑 `python -X utf8 privacy_clean.py --yes`（清空 KEY 后自动复查）；③ `data/api_vault.json`、`data/.secret_guard.json` 已进 `.gitignore`。配置/技能里一律留空串 `""`，**不要**把真值补进仓库。**注意**：`core.hooksPath` 是**本地仓库配置**，所以克隆后 / 换机器后要各执行一次 `git config core.hooksPath .githooks`，护栏才生效（步骤与坑见 `10` §0）。 |

---

## 1. 手册清单

| 文件 | 覆盖范围 | 什么时候读 |
|---|---|---|
| `main.md` | 本索引：任务检索、目录地图、命令速查 | 每次任务开始 |
| `01-architecture.md` | 启动链路、单例、线程模型、信号总线、配置中枢、全局约定 | 要改启动/线程/跨模块通信/配置 |
| `02-ui-chat.md` | `ui_manager.py`（9956 行）：主窗口、发送流程、流式渲染、思维链、折叠块、选项、设置面板 | 改界面、气泡、思维链、设置项、快捷交互 |
| `03-llm-api.md` | `llm_client.py`、`api_vault.py`：接口池、流式解析、思维链提取、多模态、生图、联网搜索 | 改模型/接口/流式/API 管理 |
| `04-memory.md` | `memory_pipeline.py`、`memory_compile.py`、`memory_dream.py`、`pinned_memory.py`、`session_search.py`、记忆隔离 | 改记忆读写/检索/遗忘/隔离 |
| `05-roles-roleplay.md` | `role_manager.py`、`character_card.py`、`roles*/`、`roleplay/` 全部：设定卡、引擎、预设、导入、隔离 | 改角色卡、立绘、群聊、角色扮演 |
| `06-skills.md` | 本地技能（`skills/`、`\@技能`）+ 公共技能库（`skillspub/`、`#技能名`）+ 包导入/打包 + 技能产出目录 | 加技能、改技能菜单、改注入 |
| `07-feature-modules.md` | 日记、自动日记、卡路里、专注助手、心跳、定时任务、登录时长、天气、小说学习、网站检测、通知、文件工具、图片查看、桌宠、`utils/` | 改任一功能模块或工具 |
| `09-data-assets.md` | `data/`、`history/`、`img/`、`theme/`、`skilluserdata/`、`dailydata/`、`log/`、根目录需求文档 | 查某个 JSON/资源由谁读写 |
| `10-dev-workflow.md` | 常见开发任务 SOP（加技能/加角色/加配置键/加信号/加程序侧技能…）、规范与坑、验证速查 | 具体动手改功能时 |

---

## 2. 任务 → 手册 检索表

| 我想做的事 | 读 | 主要落点（符号） |
|---|---|---|
| 改发送流程 / 附件 / 指令分派 | `02` | `MainWindow._on_send`、`_on_attach`、`_handle_program_skill` |
| 改流式渲染 / 气泡刷新节流 | `02` | `_on_stream`、`_flush_bubble_refresh`、`_update_bubble_text` |
| 改思维链拆分（`<think>`/`｜begin▁of▁thinking｜`/`<!-- -->`） | `02` | `split_cot_stream`、`split_display_blocks`、`_COT_*` |
| 改正文 markdown / 链接 / 图片 | `02` | `_render_body_html`、`linkify_urls`、`mini_markdown` |
| 改选项按钮（`【选项】`/`<w2g>`） | `02` | `extract_choices`、`extract_w2g_choices`、`ChoiceRow` |
| 新增/调整设置项 | `02` | `_open_settings` 的 `_section(...)` 与 `_apply()` |
| 改立绘/情绪/头像 | `02`+`05` | `_set_portrait`、`RoleManager.portrait_path`、`detect_emotion` |
| 改模型或接口地址 | `03` | `config_loader.DEFAULTS["api"]`、`LLMClientPool._endpoint/_model` |
| 改流式解析 / reasoning 字段 | `03` | `LLMClientPool._iter_stream`（`reasoning_content`→`thinking`） |
| 改生图 / 多模态 | `03` | `image_generate`、`vision_describe`、`document_describe` |
| 改联网搜索 | `03` | `web_search`、`_search_provider_items` |
| 管理常用 API（和风天气等） | `03` | `api_vault.py`（`load_profiles`/`find_profile`） |
| 改记忆检索/归档/防重 Merge | `04` | `retrieve_before`、`archive_after`、`_dedup_and_merge` |
| 改记忆隔离（日常 vs 角色扮演） | `04`+`05` | `ui_manager._roleplay_scope`、`memory_pipeline._st_key/_query_coll` |
| 改每日/每周记忆摘要 | `04` | `memory_compile.compile_today_*`、`assemble_week_text` |
| 改记忆整合 Dream / 回滚 | `04` | `MemoryDream.run_dream`、`rollback` |
| 改固定记忆 / 会话搜索 | `04` | `pinned_memory`、`session_search.search_sessions` |
| 加/改角色卡 | `05` | `roles/<名>/roles.json`、`RoleManager.role_card` |
| 改群聊点名/发言者判定 | `05` | `mentioned_member`、`parse_speaker`、`resolve_speaker` |
| 改角色扮演装配顺序 | `05` | `RolePlayEngine.build_context`、`card_context` |
| 改世界书 / 正则脚本 / 规则卡 | `05` | `lorebook_context`、`apply_regex`、`render_rules` |
| 导入酒馆预设/世界书 | `05` | `roleplay_import.parse_preset/parse_lorebook/import_directory` |
| 加本地技能（`\@技能`） | `06`+`10` | `skills/tools_list.json` + `skilltools_information.json` |
| 加公共技能（`\@skillspub`/`#技能名`） | `06`+`10` | `skillspub/<名>/SKILL.md` + `catalog.json` |
| 改技能弹窗/键盘交互 | `06` | `_refresh_skill_popup`、`eventFilter`、`SkillPopup` |
| 改技能包 zip 导入/打包 | `06` | `skill_import.py`、`skill_bundles.py`、`skill_install.py` |
| 改技能产出目录 `\@project` | `06` | `skill_userdata.py`、`_ensure_project_dir` |
| 改日记本 | `07` | `daily.py`（`DailyMainWindow`/`DailyEditWindow`） |
| 改自动日记 | `07` | `auto_diary.AutoDiary` |
| 改专注助手/番茄钟 | `07` | `focus_assistant.FocusCountdownWindow` |
| 改定时任务（Cron） | `07` | `cron_manager.CronManager` + `main.py` 注册区 |
| 改心跳关怀 | `07` | `heartbeat.Heartbeat`、`pet_manager._on_heartbeat_care` |
| 改天气卡片 | `07` | `weather_service.fetch_bundle/render_weather_html` |
| 改天气**定位**（本机 IP → 静态城市表，禁 GeoAPI） | `07` | `weather_service.resolve_location`、`ip_info`、`local_city_lookup` |
| **一键清除隐私数据**（交付前干净化） | `07`+`09` | `privacy_clean.py`、设置面板「隐私与清理」、`main.py purge_pending` |
| **挂密钥护栏 / 提交前体检**（克隆后必做） | `10`+`main` | `git config core.hooksPath .githooks`（一次）、`secret_guard.py --staged/--all`、`privacy_clean.py --scan` |
| 改角色名显示 / 顶部选择框宽度 | `02`+`05` | `RoleManager.sidebar_label`、`MainWindow._fit_select_combo`、`_SELECT_MAX_W` |
| 改桌宠菜单/提醒/小窗 | `07` | `pet_manager.DesktopPet`、`MiniChatWindow`、`AlertPopup` |
| 改文件读写技能/路径权限 | `07` | `file_tools.FileTools`、`utils/path_guard.PathGuard` |
| 查某个 JSON / 资源由谁读写 | `09` | 各目录表 |

---

## 3. 目录地图（根目录）

```
Celestia-AssistantAI/
├─ main.py / start.py          启动入口（start 是 tkinter 启动器，main 是 Qt 主程序）
├─ ui_manager.py               主界面（9956 行，占项目最大比重）
├─ llm_client.py               接口池 + LLMWorker + 多模态/生图/搜索
├─ config_loader.py            配置中枢（DEFAULTS + ConfigLoader 单例）
├─ signal_bus.py               全局信号总线（SignalBus 单例）
├─ memory_pipeline.py          三级记忆（短期/长期/重要）+ ChromaDB
├─ memory_compile.py           记忆传送带（daily/week 摘要）
├─ memory_dream.py             记忆整合（可回滚）
├─ pinned_memory.py            固定记忆（pinned.md）
├─ session_search.py           会话全文搜索
├─ role_manager.py             角色库/群聊/立绘解析
├─ character_card.py           角色卡 zip 导出导入
├─ roleplay/                   角色扮演子项目（引擎/管理/预设/导入）
├─ skill_manager.py / skill_popup.py / skilltools.py    本地技能（\@技能）
├─ skillspub_core.py / skillspub_manager.py             公共技能库（#技能名）
├─ skill_import.py / skill_bundles.py / skill_install.py / skill_eval.py / skill_userdata.py
├─ daily.py / auto_diary.py                            日记本 / 自动日记
├─ focus_assistant.py / heartbeat.py / cron_manager.py 专注 / 心跳 / 定时
├─ logintime.py / weather_service.py / calorie_tracker.py
├─ novel_learn.py / ponywebsite_checker.py / notify_service.py
├─ file_tools.py / media_viewer.py / api_vault.py / pet_manager.py
├─ utils/                      异步工作线程、UTF-8、风格弹窗、路径权限、逻辑日、日志
├─ tools/                      素材工具（fix_pet_gifs.py / generate_placeholder_assets.py）；
│                              测试套件已于 2026-09-23 随发布移除（见 §0 注意）
├─ CodeGuide/                  ← 本指南
├─ data/ history/ img/ theme/ skillspub/ skills/ roles/ roles_img/ roles_desktop/
│  dailydata/ skilluserdata/ log/ history_guide/
```

**启动器/入口速查**

| 入口 | 说明 |
|---|---|
| `python start.py` | tkinter 启动器：检测依赖版本 → 一键升级 → 启动 `main.py`（子进程） |
| `python start.py --direct` | 跳过 GUI 直接启动主程序 |
| `python main.py` | Qt 主程序（`--config/--no-pet/--role/--debug/--no-gpu`） |
| `python skilltools.py` | 技能工具管理器（独立窗口） |
| `python skillspub_manager.py` | skill 库管理器（独立窗口） |
| `python roleplay/roleplaytool.py` | 角色扮演设定卡管理器（无参开 GUI，带参走 CLI） |
| `python -m roleplay.roleplay_import <目录>` | 批量导入酒馆预设/世界书 |

---

## 4. 全局速查

### 4.1 单例（改状态/加初始化顺序时必看）

| 单例 | 位置 |
|---|---|
| `ConfigLoader.instance()` | `config_loader.py` |
| `SignalBus.instance()` | `signal_bus.py` |
| `LLMClientPool.instance()` | `llm_client.py` |
| `ChromaDBManager.instance()` / `MemoryPipeline.instance()` | `memory_pipeline.py` |
| `MemoryCompile.instance()` / `MemoryDream.instance()` / `PinnedMemory.instance()` | 同名文件 |
| `RoleManager.instance()` | `role_manager.py` |
| `SkillManager.instance()` | `skill_manager.py` |
| `RolePlayPreset.instance()` | `roleplay/roleplay_preset.py` |
| `RolePlayManager`（非单例，可传 `root`） | `roleplay/roleplaytool.py` |
| `SkillImporter/SkillBundles/SkillInstaller/SkillEval/SkillUserData/FileTools/Heartbeat/CronManager/NotifyService/AutoDiary` | 各自文件 |

### 4.2 约定

- **配置读写**：`ConfigLoader` 的 `get(*keys, default=)` / `set(value, *keys)`，**`set` 后必须 `save()`**；默认值集中在 `config_loader.DEFAULTS`。
- **落盘**：JSON 一律「写 `.tmp` → `os.replace`」原子写；目录来自 `ConfigLoader` 的路径属性，不要硬编码。
- **线程**：GUI 只能在主线程；耗时任务用 `utils.async_worker.spawn_worker`（QThread + `_KEEP_ALIVE` 保活）或 `LLMWorker`，通过信号回主线程。
- **显示层清洗**：模型的原始输出只在 **`MainWindow` 侧**做清洗，唯一入口是 `split_display_blocks()`；`ChatWorker` 只负责拼提示词与转发流。
- **中文/编码**：入口都有 `utils.utf8.force_utf8_stdio()`；`\@`、`#` 触发词字符集需与 `skill_manager._TAG_TOKEN`、`skilltools._TRIGGER_RE`、`skillspub_core._PUB_TOKEN_RE` 保持一致。
- **文档维护**：改完功能请顺手更新对应手册的「关键符号表」与「坑」，本指南靠人工维护。

### 4.2.1 三条铁律（2026-09-21 用户明确要求；第 3 条 2026-09-23 追加）

1. **界面美化不要用 emoji**：UI 文案、按钮、卡片、提示一律用文字 / 图标资源表达，
   **禁止**用 emoji 当装饰（`weather_service.icon_for` 恒返回空串就是这个约定的产物）。
2. **一律 UTF-8 执行**：所有脚本入口先 `utils.utf8.force_utf8_stdio()`；命令行统一
   `python -X utf8 …`；JSON 读写显式 `encoding="utf-8"`（Windows 默认 GBK，会炸）。
3. **绝不提交真实密钥**：提交前 `.githooks/pre-commit` 会跑 `secret_guard.py --staged`，
   命中即**阻止提交**；交付/上传前跑 `python -X utf8 privacy_clean.py --yes`
   清空 KEY 并自动复查。配置文件里一律留空串 `""`。
   **前提**：钩子要先挂上 —— `git config core.hooksPath .githooks`（**本地**配置，
   克隆后 / 换机器后各做一次）；没挂时提交**不会**自动体检，要手动跑 §4.3 的 `--staged`。

### 4.3 常用验证命令

```powershell
python -X utf8 -m py_compile ui_manager.py      # 单文件语法校验
git config core.hooksPath .githooks             # 挂密钥护栏（克隆后必做一次）
python -X utf8 secret_guard.py --staged         # 只看暂存内容（钩子内部用）
python -X utf8 secret_guard.py --all            # 提交/上传前密钥体检（护栏）
python -X utf8 main.py --no-pet --debug         # 启动主程序（观察日志 log/error.log）
python -X utf8 privacy_clean.py --scan          # 交付前隐私体检（只读预览）
```

> `tools/` 测试套件已于 2026-09-23 随发布清理移除（见 §0 注意），
> 原先的 `run_all_v2_tests.py` / `run_roleplay_tests.py` / `smoke_test.py`
> 等命令不再可用。
