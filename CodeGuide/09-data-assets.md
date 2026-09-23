# 09 · 数据、资源与需求文档索引

> 查「某个 JSON/图片是谁写谁读」时读这本。
> 基线：2026-09-19（以工作区实际文件为准）。

---

## 1. `data/`（配置与轻量数据）

| 文件 | 作用 / 结构 | 读写方 |
|---|---|---|
| `config.json` | **全局配置**：`api`(provider/base/key/模型)、`paths`、`chat`、`memory`、`pet`、`ui`、`web`、`cron`、`heartbeat`、`notify`、`pinned`、`dream`、`memory_compile`、`search`、`skill_bundles`、`character_card`、`file_tools` | `config_loader.ConfigLoader`（`get/set/save`，设置面板 `_apply`） |
| `api_vault.json` | 常用接口类 API（`profiles[]`：id/name/desc/base/key/extra/rules/enabled） | `api_vault.py`；`weather_service` 取「和风天气」KEY；GUI `ApiVaultWindow` |
| `roleplay_preset.json` | 角色扮演预设（采样/装配/隔离/卡片勾选/规则/正则/世界书/meta） | `roleplay/roleplay_preset.py`、`roleplay_preset_ui.py`、`roleplay_engine.py` |
| `role_abbr.json` | 角色目录名 → 侧栏缩写 | `role_manager._abbr_map()`(107，懒加载缓存) |
| `cron_jobs.json` | Cron 任务持久化（`{updated, jobs{}}`） | `cron_manager`（`job_file`） |
| `cron_runs/<job_id>.jsonl` | 任务运行历史（>500 行裁剪到 300） | `cron_manager._record_run`(428) |
| `dailylifedata.json` | 饮食热量记录（`{updated, records[], daily{}}`） | `calorie_tracker.py`(26) |
| `input_drafts.json` | 输入框草稿（按角色保存未发送文本） | `ui_manager._drafts_file`(4363) |
| `ponywebsite.txt` | 网站检测清单：每行「名称<Tab>网址」，`#` 注释 | `ponywebsite_checker.load_sites`(120)（只读） |
| `qweather_cities.csv` | 和风天气城市表：`id,name,adm1,adm2,lat,lon` | `weather_service._load_city_rows`(381)（只读） |
| `about.json` | 「关于」对话框文案 `{title,text}` | `ui_manager.AboutDialog` |
| `pinned.md` | 固定记忆，每行一条（运行时生成） | `pinned_memory.py` |
| `skill_bundles.json` | 技能包定义（运行时生成） | `skill_bundles.SkillBundles` |
| `focus_history.json` / `focus_summary.json` | 专注记录 / 汇总（运行时生成） | `focus_assistant.record_focus`(179) / `_refresh_focus_summary`(156) |
| `logintime.json` / `logintime_history.json` | 登录明细（最近 200）/ 24 小时段桶（运行时生成） | `logintime.py` |
| `heartbeat_state.json` / `heartbeat_log.jsonl` | 心跳计数 / 日志（运行时生成） | `heartbeat.py` |
| `character_cards/` | 角色卡 zip 导出目录（运行时生成） | `character_card.export_dir` |
| `.privacy_purge_pending.json` | **一键隐私清理的待补删清单**（运行时被 ChromaDB/日志占用的目录，下次启动补删） | `privacy_clean._write_pending / purge_pending`（`main.py` 启动早期调用） |
| `user_avatar.png` | 用户头像（`config.user_avatar`） | 设置面板 |

---

## 2. `history/`（会话与记忆存档）

| 路径 | 结构 | 读写方 |
|---|---|---|
| `history/chroma/chroma.sqlite3` | ChromaDB 向量库（`long_term` / `important` 集合） | `memory_pipeline.ChromaDBManager` |
| `history/conversations/session_<角色>_<unix ts>.json` | 完整会话：`{role, group, updated_at, messages[{role,name,content,ts,thinking?}], roleplay?, scope?}` | `ui_manager._save_session`(7651) / `_on_history_clicked`(7961) |
| `history/conversations/talk_<角色>.json` | 短期记忆数组；角色扮演分区文件名形如 `talk_roleplay_<角色>_<8hex>.json` | `memory_pipeline._st_key`(209) / `_load_short`(232) / `_save_short`(252) |
| `history/memory_compile/daily/<YYYY-MM-DD>.json` | 记忆传送带日报：`{date, summary, roles[], updated_at}` | `memory_compile.save_daily`(74) |
| `history/memory_dream/revisions/<ts>.json`、`memory_dream_state.json` | Dream 快照（可回滚）与运行报告 | `memory_dream._write_snapshot`(122) / `_record_state`(307) |
| `history/file_history/<sha256 前16>/` | 文件工具写前快照 + `index.json`（最近 50 版） | `file_tools._snapshot_before_write`(71) |

---

## 3. 其它目录

| 目录 | 内容 / 约定 | 加载方 |
|---|---|---|
| `roles/` | `roles/<角色>/roles.json` + `emotion.json`；顶层 `group.json` 群聊预设 | `role_manager` |
| `roles_img/<角色>/` | `<角色>-<情绪>.png`（`neutral/happy/sad/angry/surprised`），无情绪为 `<角色>-.png`；`OLD/` 不参与解析 | `role_manager.portrait_path`(407) |
| `roles_desktop/<角色>/` | `<角色>-<动作>.gif`（`standing/run/say1/say2/hello/sleep/work`） | `role_manager.pet_gif_path`(440)；`pet_manager._pet_gif`(187) |
| `skills/` | `tools_list.json`（目录）+ `skilltools_information.json`（详情）；`test_cases/<技能>.json` 为评测用例 | `SkillManager.reload`(49)、`SkillEval` |
| `skillspub/` | `catalog.json` 索引 + 每技能一个文件夹（`SKILL.md`、`references/`） | `skillspub_core` |
| `skilluserdata/` | 技能产出：`<日期-时间>/` + `projects.json`（会话 uid → 文件夹名） | `skill_userdata.py` |
| `dailydata/dailytext/`、`dailydata/dailyfile/`、`dailydata/auto_diary/` | 日记正文/附件副本/自动日记 md | `daily.py`、`auto_diary.py` |
| `novellearn/` | 小说学习结果 `<文件名>_<时间戳>.json` | `novel_learn.py` |
| `createimage/` | 生图输出（`api.image_dir`） | `llm_client.image_generate`(521) |
| `img/` | 功能图标（`file/close/del/copy/pause/time/answear/tell/thinking.gif/createimg.gif`）+ 应用图标 `logo_<尺寸>.ico` | `ui_manager._img_html`(136)、`_apply_icon`(4156)、`pet_manager._img`(63)、`focus_assistant`(107/468)、`daily.py`(61) |
| `theme/` | 背景图（`default.png`、`test.png`、`AJ1/RD1/FS1/TS1…` 角色缩写系列、`Dream/Happy/Life/Sky…` 氛围图） | `ui_manager._theme_candidates`(3945)/`_load_theme`(3952)、`daily.py`(619) |
| `log/` | 主程序错误日志 `error.log`（`ui_manager._log_error` 6254） | 运行时生成 |
| `utils/logs/` | 应用日志 `app.log`（5MB×3 轮转） | `utils/logger.py` |
| `history_guide/` | **开发规划文档**（非运行时）：`V2/使用说明书.md` | 人工阅读 |

---

## 4. 根目录需求文档索引（**改需求前先查这里**）

| 文档 | 记录内容 |
|---|---|
| `README.md` | 项目总设计 + 版本更新记录（V0.1 → V0.3.0 合并 V2） |
| `0-8set.md` | **全量总说明书**：架构、逐文件夹/系统文件详解、添加角色/群聊/技能/日记/登录时间的完整思路、常见问题 |
| `set.md` | 精简版：每个文件夹与系统文件作用、添加角色步骤、图片命名规范 |
| `helpset.md` | 角色制作指南：角色卡 JSON、情绪映射、立绘/桌宠放置、用户头像昵称、群聊配置 |
| `roleset.md` | 角色设计所需全部文件的放置路径与作用表 |
| `groupset.md` | 群聊实现与设置（点名触发、AI 判定发言者、桌宠跟随；`roles/group.json`） |
| `toolsset.md` | 技能工具管理器 `skilltools.py` 设计文档（注册表式界面、两个 JSON 结构、使用示例） |
| `skillspubset.md` | skill 库（`skillspub/`、`catalog.json` 字段）设置说明 |
| `dailymanger.md` | 日记本子项目 `daily.py` 规划（一二三级界面、`dailydata/` 结构） |
| `角色扮演预设管理器使用指南.md` | 预设管理器 6 页说明（采样/设定卡/规则/正则/世界书/装配隔离） |

---

## 5. 数据变更注意事项

1. **用户数据与代码分离**：`data/`、`roles*/`、`history/`、`skillspub/`、`skills/` 都属运行时数据，测试/脚本**不要**直接写（用 temp 目录或先备份再恢复，参考 `integration_test.py`）。
2. 新增配置文件请同时：① 在 `config_loader.DEFAULTS` 加默认键；② 在本手册与 `09` 表里登记；③ 若无文件则代码要容忍缺失（`[]`/`{}`/空字符串）。
3. 所有 JSON 写入统一「`.tmp` → `os.replace`」原子写；文本用 `encoding="utf-8"`。
4. 目录常量一律走 `ConfigLoader` 的路径属性（`roles_dir`/`history_dir`/`chroma_dir`/`skillspub_dir`…），不要硬编码相对路径。
