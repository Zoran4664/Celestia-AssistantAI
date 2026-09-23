# 10 · 开发/修改工作流 SOP

> 动手改功能时读这本：常见任务的**步骤清单 + 落点 + 验证 + 坑**。配套查阅：`main.md` 任务检索表。
> 基线：2026-09-19。

---

## 0. 通用流程

```
0) 一次性：git config core.hooksPath .githooks   （挂密钥护栏；每台机器 / 每次重新克隆各做一次，见下）
1) 定位：在 main.md 任务表找到手册 → 在手该册按符号名定位
2) 改动：只改最小范围；新增配置/信号/字段要同步 DEFAULTS / 总线 / 文档
3) 自测：python -X utf8 -m py_compile <改动文件>
4) 收尾：更新对应 CodeGuide 手册（符号表 / 坑），删除临时脚本与临时文件
5) 提交：git commit（步骤 0 已挂护栏时，提交前会自动先跑密钥体检）
```

**首次克隆后必做：挂上密钥护栏**

```powershell
git config core.hooksPath .githooks        # 只写本仓库本地配置，不动全局
```

- **为什么**：2026-09-23 本项目曾把真实 API KEY 提交进 `skills/skilltools_information.json` 并推送，
  历史里永久留下那串字符串（只能作废 + 重写历史才清得掉）。护栏把同类事故挡在**提交之前**。
- **做什么**：`.githooks/pre-commit` 会跑 `python -X utf8 secret_guard.py --staged`，
  暂存内容命中密钥特征（`sk-…`、`bce-v3/…`、`ALTAK-…`、`Bearer <长串>`、
  `"api_key"|"password"|…` 非空值、本机 `盘符:\` 路径）就**阻止提交**（exit 1，片段打码输出）。
- **绕过**（确属占位符，如文档里的 `sk-xxxx` 示例）：`SKIP_SECRET_GUARD=1 git commit -m "..."`（仅本次）。
- **手动等价命令**见 §13；上传/交付前跑 `--all` 做整体体检。
- **私有规则**（防自己的真名/邮箱被提交）：`data/.secret_guard.json` 写
  `{"patterns": ["你的真名", "你的邮箱"], "files": ["data/notes.md"]}` —— 该文件已在 `.gitignore` 中。
- **注意**：`core.hooksPath` 是**本地仓库配置**（存在 `.git/config`），所以换机器 / 重新克隆后要重做步骤 0。

**硬性规范**
- **R1 禁 emoji 美化**：界面/卡片/通知/按钮一律用文字 + 颜色/QSS/`img/` 图片表达，不得用 emoji 充当图标或装饰。
- **R2 UTF-8 执行**：全部命令用 `python -X utf8 ...`；文件读写显式 `encoding="utf-8"`（仅读外部旧文件才允许 GBK 回退）；新增文本文件必须 UTF-8 保存。
- 不新增依赖除非必要；若必须新增 → **同时**改 `requirements.txt` 与 `start.py::_FALLBACK_DEPS`
  （`start.py` 注释里写明「两处需同步」；启动器 `--setup` 与 GUI 都按这份清单检测/安装）。
  **怎么核对清单是否完整**：用 `ast` 扫全库 `.py`（排除 `skillspub/` —— 技能自带依赖由技能自己管）
  收集 `import` / `from ... import` 的顶层模块名，减去 `sys.stdlib_module_names` 与本地模块，
  得到「项目实际用到的第三方库全集」，再与两份清单逐项对照（注意 import 名 ≠ 包名：
  `PIL`→Pillow、`fitz`→pymupdf、`pptx`→python-pptx、`docx`→python-docx、`dateutil`→python-dateutil）。
  2026-09-23 用此法查出：`pymupdf` 漏登记（已补）、`python-dateutil` 已无任何 import（历史遗留）。
- 不在子线程创建/操作 QWidget；耗时任务走 `utils.async_worker.spawn_worker` 或 `LLMWorker`，实例要保活。
- 落盘统一原子写 + UTF-8；目录走 `ConfigLoader`。
- 显示层（气泡里显示什么）只在 `MainWindow` 侧处理，入口 `split_display_blocks()`。
- 写操作（文件/记忆/配置）要能容忍数据缺失与并发（用锁或原子写）。

---

## 1. 加一个本地技能（`\@技能名`）

1. 在 `skills/tools_list.json` 的 `skills[]` 加目录条目：`{name, trigger, kind("skill"|"switch"), aliases[], category, enabled}`。
2. 在 `skills/skilltools_information.json` 以**技能名**为键加详情：`description`、`prompt_template`（可用 `{参数名}`）、`parameters[{name,default,required}]`，按需加 `image_gen / network_enabled / api_base|api_key|api_model / permission("review")`。
3. （可选）给 GUI 用：`python skilltools.py` 打开管理器核对。
4. 若是**程序侧技能**（由程序直接执行而非交给模型）：见第 3 节。

**坑**：字段必须**同时**存在于目录与详情两处（`_catalog_entry` 528 / `_info_entry` 540），否则会丢字段；触发词字符集需与 `skill_manager._TAG_TOKEN`、`skilltools._TRIGGER_RE` 一致。

---

## 2. 加一个公共技能（skillspub）

1. 建目录 `skillspub/<技能名>/`，写 `SKILL.md`（可带 Anthropic 风格 frontmatter：`description/keywords/allowed-tools`）。
2. 在 `skillspub/catalog.json` 的 `skills[]` 登记：`name`（唯一）、`folder`、`category`、`enabled`、`summary`（**≤200 且非空**）、`keywords[]`、`allow_files`、`created_at/updated_at`。
3. 若技能需要参考文件：放 `skillspub/<技能名>/references/xxx.md`（`detail_text` 455 会把文件夹路径告诉模型）。
4. 触发方式：`\@skillspub`（全量指引）/ `\@autoskills`（自动挑选，走小模型 `select_skills_via_llm` 724）/ `#技能名`（点名，`context_for_direct` 821）。

**坑**：`folder_for_name`(125) 净化后可能撞名（`validate` 342 会拦）；旧版内联 `description` 会在 `ensure_dir`(259) 时自动迁移到 `SKILL.md`。

---

## 3. 加一个「程序侧技能」（程序直接执行，如 `\@weather`）

1. 在 `skills/tools_list.json` + `skilltools_information.json` 注册技能（第 1 节）。
2. `ui_manager._on_send` 的程序侧技能分派元组追加触发词（搜 `"novellearn", "weather", "calorie", "ponywebsite"`，约 4841-4844）。
3. `ui_manager._handle_program_skill`(7183) 加分支 → 新写 `_run_xxx`（参考 `_run_weather` 7246 / `_run_novellearn` 7293 / `_run_calorie` 7350 / `_run_ponywebsite` 7392）。
4. 耗时的核心逻辑放独立模块（参考 `weather_service.py` / `ponywebsite_checker.py`），**不要写进 ui_manager**；用 `_spawn_tool_worker`(7215) 放子线程，结果用 `_append_tool_card`(7223) 或 `_append_notice`(7444) 展示。

---

## 4. 加一个设置项

1. `config_loader.DEFAULTS` 对应段加键与默认值。
2. `ui_manager._open_settings`(8058-9179) 在对应 `_section(...)` 分区加控件（分区表见 `02` 4.7）。
3. 在 `_apply()`(9084-9153) 里 `self._cfg.set(...)` 落盘（`_cfg.save()` 已在 9139）。
4. 若开关影响运行期对象：在 `_on_settings_updated`(9712) 或相应 `_apply_*` 里即时生效（如 `LLMClientPool.reconfigure()`(76)、`_apply_theme`(3964)、`_apply_scale`(4005)）。

**坑**：`_settings_dialog` 是**非模态复用**实例，重复打开不重建；新增控件需考虑已存在旧布局。

---

## 5. 加一个信号 / 能力（跨模块通信）

1. `signal_bus.py` 类体加 `xxx = Signal(...)`（40-81 区域）。
2. 在 emitter 处 `.emit()`（注意跨线程安全：Qt 队列连接）。
3. 在接收方 `_connect_bus()`（`ui_manager` 3572、`pet_manager` 内）`.connect()`。
4. 能力型（请求-响应）：`bus.handle("module:cap", fn)`(97) + `bus.request("module:cap", ...)`(122)；**handler 内禁止耗时操作**（内部再开线程）。

**坑**：`ui:current_role` 当前无注册者（`main._diary_action` 177 会拿到 `None` 回落默认助手），若依赖它请先注册 handler。

---

## 6. 加一个角色

1. `roles/<角色名>/roles.json`（`name/personality/system_prompt/default_emotion/emotion_list/first_message/character description`）+ `emotion.json`。
2. 立绘 `roles_img/<角色名>/<角色名>-<情绪>.png`、桌宠 `roles_desktop/<角色名>/<角色名>-<动作>.gif`（动作见 `05` A.1）。
3. （可选）`data/role_abbr.json` 加侧栏缩写（注意会被 `SIDEBAR_MAX_WIDTH=10` 截断）。
4. （可选）群聊：`roles/group.json` 加成员与别名。

---

## 7. 改角色扮演装配 / 预设

| 想改 | 落点 |
|---|---|
| 注入顺序、分节标题 | `roleplay_engine.build_context`(445) + `card_context`(176) |
| 主角设定/OOC | `user_persona`(249)、`persona_context`(254)、`OOC_NOTICE`(273)、`ooc_notice`(288) |
| 世界书触发 | `lorebook_context`(322) |
| 规则/变量 | `render_rules`(301)、`apply_variables`(141)、`expand_macros`(101) |
| 正则通道 | `apply_regex`(396)、`_scripts`(366)、`_depth_ok`(380) |
| 采样参数 | `roleplay_preset.SAMPLING_SPEC`(61) + 预设 UI 页1(285) |
| 预设管理器 UI | `roleplay_preset_ui.py`（页2/3/4/5/6 分别 363/481/647/802/946，采集 `_collect` 1079） |
| 隔离 | `DEFAULT_PRESET["isolation"]`(141) + `ui_manager._roleplay_scope`(3863) + `memory_pipeline`（见 `04` 第 3 节） |


---

## 8. 改思维链/正文显示（高频需求）

1. 先判断属于哪一层：
   - **模型原生 reasoning 字段** → `llm_client._iter_stream` 324-329。
   - **文本协议层**（`<think>`、`<｜begin▁of▁thinking｜>`、`<!-- -->`、`【思维链】`） → `ui_manager.split_cot_stream`(2042) 的 `_COT_DELIM_TAGS`(1991)。
   - **展示层**（正文 markdown/链接/图片/折叠） → `_render_body_html`(5716)、`linkify_urls`(2238)、`extract_html_folds`(1838)。
2. 记住三个铁律：① 定界符不上屏；② 未闭合的思考段之后**全部**归思维链；③ 正文气泡在「内容都在思维链里」时**留空**，不回退显示原文。
3. 改完用**逐帧模拟**验证（构造长文本，按前缀切片调用 `split_display_blocks(frame, hold_tail=True)`，检查正文不提前漏出、无标签壳）。

---

## 9. 改记忆行为

| 想改 | 落点 |
|---|---|
| 检索条数/开关 | `memory.top_k_*`、`denoise_enabled` |
| 降噪提示词 | `memory_pipeline._denoise`(438) |
| 归档阈值/顺序 | `archive_after`(479) + `memory.trim_threshold/keep_rounds` |
| 防重/合并 | `_dedup_and_merge_locked`(594)、`_merge_memories`(688) + `dedup_*/merge_*` 配置 |
| 遗忘策略 | `decay_and_forget`(759) + `forgetting_interval_days/hit_threshold` |
| 摘要 | `memory_compile._default_summarizer`(141) / `set_summarizer` |
| Dream 整合 | `memory_dream._default_integrator`(74) / `set_integrator` |
| 隔离 | 见第 7 节最后一行 |


---

## 10. 改界面细节

| 想改 | 落点 |
|---|---|
| 主题色/背景/缩放 | `_apply_theme`(3964)、`_apply_accent`(3977)、`_apply_scale`(4005)、`root_qss`(9902) |
| 气泡宽度/自适应 | `_bubble_max_width`(4030)、`_resize_bubbles`(4044) |
| 快捷键/回车/退格 | `eventFilter`(4201) |
| 设置项 | 见第 4 节 |
| 托盘/关闭行为 | `_build_tray`(3441)、`_toggle_visible`(3475)、`closeEvent`(9717) |
| 顶栏/左栏控件 | `_build_title_bar`(2672)、`_build_left_panel`(2857) |


---

## 12. 常见坑速查（跨模块）

| 坑 | 说明 |
|---|---|
| GUI 测试卡死 | 真实模态对话框未打桩（`QFileDialog.getOpenFileNames` 与 `getOpenFileName` **不同名**、`QMessageBox`）；用 `styled_*` 的确认框同理 |
| 线程闪退 | `spawn_worker`/`LLMWorker` 实例未保活；或子线程碰了 QWidget |
| 配置不生效 | `set()` 未 `save()`；或改 API 后未 `reconfigure()`；或读的是缓存（`role_manager` 的卡/情感缓存、`LLMClientPool._clients`） |
| 数据被测试污染 | 脚本直接写 `data/`、`roles/`、`skills/`；应走 temp/monkeypatch 并在结尾恢复 |
| 字段丢失 | `skilltools` 的目录/详情双写、`skillspub` 的 catalog/SKILL.md 双写，漏一处即丢失 |
| embedding 下载卡住 | 首次初始化 chromadb 默认 embedding 需联网；离线会回退 `_HashEmbedding` |
| 逻辑日不对 | 记忆/日记日期以凌晨 4 点为界（`utils/logical_time`），不要用 `date.today()` |
| 输出标签漏到正文 | 显示层唯一入口是 `split_display_blocks()`；新增标签要同步 `_STRUCT_TAGS`(1757) 或 `_COT_DELIM_TAGS`(1991) |

---

## 13. 验证命令速查

```powershell
python -X utf8 -m py_compile <file.py>                    # 单文件语法
python -X utf8 secret_guard.py --staged                   # 只看暂存内容（pre-commit 钩子用）
python -X utf8 secret_guard.py --tracked                  # 只查已被 git 跟踪的文件
python -X utf8 secret_guard.py --all                      # 查整个工作区（上传/交付前体检）
python -X utf8 privacy_clean.py --scan                    # 交付前隐私体检（只读预览）
git config core.hooksPath .githooks                       # 挂密钥护栏（首次克隆必做，见 §0）
```
