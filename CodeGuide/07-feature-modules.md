# 07 · 功能模块手册（日记 / 专注 / 心跳 / 定时 / 天气 / 桌宠 / utils …）

> 每个模块一节：作用 → 关键符号（行号）→ 配置键 → 落盘 → 修改指南 → 坑 → 测试。
> 行号基线：2026-09-19。

---

## 1. `daily.py`（1904 行）日记本子项目

- **作用**：可独立运行、也可由主界面打开的日记 GUI（一级列表 + 二级富文本编辑 + 附件管理）。
- **调用**：左栏「日记记录」按钮(`ui_manager` 2930) → `_open_daily`(3520) → `DailyMainWindow`(3533)；独立入口 `main()`(1887)。
- **数据层**：`ensure_dirs`(86)、`list_diaries`(431)、`read_diary`(443)、`save_diary`(449)、`delete_diary`(473)、`copy_into_dailyfile`(484)、`list_dailyfiles`(503)、`delete_dailyfile`(510)、`_file_url`(545)、`_rel_to_abs`(550)、`_abs_to_rel`(566)。
- **界面**：`_FramelessMixin`(648)（无边框拖动/缩放/主题）；`DailyMainWindow`(884)（`_refresh_cards` 986 / `_build_card` 1005）；`_LinkTextEdit`(1105)；`DailyEditWindow`(1129)（`_on_save` 1354、`_make_attach_row` 1406、`_make_music_row` 1453、`_refresh_attach_list` 1572、`_on_insert_image` 1621、`_apply_image_scale` 1644、`_on_insert_file` 1708、`done` 1732）；`FileManageDialog`(1746)。
- **配置**：只读 `ui.accent`(612)、`ui.theme_file`(625)、`ui.blur_background`(626)。
- **落盘**：`dailydata/dailytext/<id>.json`（`{id,title,content_html,created_at,updated_at,allowed_roles[],attachments[]}`）、附件副本 `dailydata/dailyfile/<时间>_<6hex>_<原名>`。
- **坑**：正文里附件用**相对路径** `dailyfile/...`，读写真必须经 `_rel_to_abs/_abs_to_rel`（含 `unquote` 归一），否则二次打开图片不显示；`QDialog` 最小化/最大化需追加 `Qt.Window`(667)；音乐播放器须在 `done`/`closeEvent` 停止。

## 2. `auto_diary.py`（154 行）角色自动写日记

`AutoDiary.instance()`(41)；`diary_path`(53)、`enabled`(56)、`_build_materials`(64)、`_build_prompt`(84)、`generate_diary_text`(103)、`save_diary`(120)、`generate_diary_async`(139)。
**配置**：`ui.auto_diary_enabled(False)`、`ui.auto_diary_time("23:00")`（`main._diary_action` 176 由 Cron 触发）。
**落盘**：`dailydata/auto_diary/<YYYY-MM-DD>.md`（原子写）。**坑**：无素材直接跳过；失败静默。

## 3. `calorie_tracker.py`（211 行）饮食热量

`CalorieTracker.load/save`(66/78)、`day_total`(87)、`append_record`(93)、`online_reference`(117)、`estimate`(129)、`format_report`(172)；`recent_days`(205)。
**调用**：`\@calorie` → `_handle_program_skill`(7183) → `_run_calorie`(7350)。
**落盘**：`data/dailylifedata.json`（`{updated, records[], daily{}}`，原子写）。**坑**：模型输出用正则抠 JSON，异常回退空。
**配置**：无（联网走 `pool.web_search`）。

## 4. `focus_assistant.py`（670 行）专注助手（番茄钟）

- 模块级：`record_focus`(179)、`focus_summary_text`(203)、`clear_focus_records`(224)（设置面板「遗忘」用 8923）。
- `FocusDurationDialog`(236)；`_CountdownPanel`(296)；`_FocusAlert`(343)；`FocusCountdownWindow`(423)（`_update_display` 519、`_tick` 522、`_on_end_clicked` 536、`_on_time_up` 554、`closeEvent` 564、`_generate_encouragement` 578、`_record_session` 629、`_show_alert` 660）。
- **调用**：左栏按钮 → `ui_manager._open_focus_assistant`(3548)。
- **落盘**：`data/focus_history.json`（最近 30 条）、`data/focus_summary.json`；并写短期记忆 `add_short_note`(640)。
- **坑**：未完成时 `closeEvent` 会 `ignore()` 转去生成鼓励再关；生成的 `LLMWorker` 必须保活。

## 5. `heartbeat.py`（255 行）心跳巡检/关怀

`Heartbeat.instance()`(60)；`start`(68)、`stop`(89)、`watch_dirs`(95)、`_scan`(120)、`_diff`(147)、`tick`(157)、`_maybe_act`(179)、`_read_state/_write_state`(220/230)。
**配置**：`heartbeat.interval_min(31)`、`heartbeat.watch_dirs`、`heartbeat.max_active_per_day(6)`；默认监听 `history/conversations`、`data`、`dailydata`、`createimage`。
**落盘**：`data/heartbeat_state.json`（每日计数）、`data/heartbeat_log.jsonl`。
**坑**：`_scan` 必须排除自身产物（125），否则自己写文件又触发自己；关怀走 `bus.request("role:generate_message")` 优先，否则发 `heartbeat_care_requested`（200-206）避免双重显示。

## 6. `cron_manager.py`（488 行）定时任务

`CronManager.instance()`(166)；`start/stop`(174/184)、`job_file`(191)、`load/save`(197/217)、`add_job`(235)、`update_job`(277)、`delete_job`(297)、`list_jobs`(305)、`_compute_next_run`(316)、`register_action`(347)、`check_jobs`(356)、`_execute`(386)、`_record_run`(428)、`run_history`(450)、`trigger_now`(468)。
**配置**：`cron.enabled(True)`、`check_interval_sec(60)`、`default_timeout_min(20)`、`job_file(./data/cron_jobs.json)`、`runs_dir(./data/cron_runs)`。
**落盘**：`data/cron_jobs.json`、`data/cron_runs/<job_id>.jsonl`。
**坑**：action 名与 `main.py register_action` 必须一致（`memory_cleanup/memory_dream/auto_diary`）；`at` 型执行后自动禁用；`every` 最小 60s；超时无法强杀线程只记录。

## 7. `logintime.py`（221 行）登录时长问候

`record_login`(74)、`login_time_summary`(126)、`login_time_stats`(145)、`login_time_context`(167)、`forget_short`(179)、`forget_long`(191)、`clear_all`(205)。
**调用**：`MainWindow.__init__`(2591) 记录；system prompt 注入(680)；设置面板遗忘(8975-9010)。
**配置**：`ui.login_time_enabled(True)`；**落盘**：`data/logintime.json`、`data/logintime_history.json`（24 小时段桶）。

## 8. `weather_service.py` 天气

- `\@weather` → `_run_weather`（ui_manager）→ `fetch_bundle` + `render_weather_html`。
- **定位方案（需求：不用 GeoAPI 动态检索）**：`resolve_location(query, timeout)` → `ip_info()` → `local_city_lookup()`（静态城市表）
  0. 地名写的是**本地意图词**（`is_local_intent`：本地 / 本机 / 我这里 / 当前位置 / 所在城市 / 定位 /
     `my location`…）→ 当作「未指定地名」→ 走本机 IP 定位；
  1. 用户写了城市名 → 只查内置静态城市表 `data/qweather_cities.csv`（含官方 LocationID/经纬度）；
  2. 未写城市名 → `ip_info()` 取本机公网 IP 与归属地（pconline + ip-api，含 120s 缓存）→ 静态城市表匹配所在地；
  3. 静态表未收录 → 退回 IP 经纬度（`location=经度,纬度`）。
  `location.source ∈ {table, ip-table, ip-coord}`，卡片副标题据此标注「本机 IP 定位 / 本机 IP 经纬度」。
- **坑（2026-09-21 修复）**：`parse_city` 只剥「天气/查询」等套话，于是 `\@weather 查询本地天气`
  剥完只剩「本地」，被当成**地名关键词**去查静态城市表 → 直接得到「内置城市表未收录「本地」」。
  现在 `LOCAL_INTENT_WORDS` + `is_local_intent()` 把这类**意图词**从地名里摘掉（剥完为空即本机 IP 定位），
  `resolve_location` / `fetch_bundle` 也各自兜一层（避免别的调用方直接传「本地」进来）。
- 关键符号：`is_custom_host`、`parse_options`、`parse_city`、`is_local_intent`、`LOCAL_INTENT_WORDS`、`ip_info`、`ip_city`(兼容包装)、`public_ip`、`norm_host`、`resolve_location`、`QWeatherClient`（`now/daily/hourly/indices/warning/air/minutely/astronomy_*/storm_*/ocean_tide`）、`local_city_lookup`、`_load_city_rows`、`fetch_bundle`、`render_weather_html`。
- **数据来源**：`data/api_vault.json`（「和风天气」KEY，`ui_manager._run_weather` 用 `api_vault.find_profile`）；静态城市表 `data/qweather_cities.csv`。
- **坑**：公共地址已停服，必须用账号独立 Host（`is_custom_host` 校验）；只能单种鉴权 `X-QW-Api-Key`；接口恒返回 Gzip 需统一解压；`QWeatherClient.city_lookup`（GeoAPI）**已不再被调用**，仅保留兼容。
- **加接口**：`QWeatherClient` 新方法 + `OPT_KEYWORDS/OPT_TITLES` + `fetch_bundle` + `render_weather_html`。

## 8.2 `sky_service.py` 观星 / 天象（`#tothemoon`，2026-09-21 新增）

- **需求（用户反馈）**：「`#tothemoon 查询天象` 不是输出咨询的地址，而是查询本地 IP、直接查本地 IP 地址的天象」
  → `ui_manager.PROGRAM_PUB_SKILLS = {"tothemoon"}`：点名这个技能时**先定位**，
  再按提示词关键词分流（`_sky_wants_llm`）：
  - **「查询观测条件」（或没写关键词）→ 程序执行**（`_handle_program_pub_skill` →
    `_run_sky`）：像 `\@weather` 一样直接出数据卡片（日月出没 / 上中天 / 亮度 /
    7timer 观测条件），**不进普通对话、不甩链接**；
  - **「查询天象 / 查询专业天象」（含彗星、流星雨、月食…等词）→ 交给模型**：
    天象清单需要检索与可见性筛选，程序算不出来；但**本机 IP 归属地由程序取好后
    注入 system 上下文**（`_start_sky_chat`），模型不必也无法反问用户城市。
- **定位**（复用 `weather_service`）：`resolve_sky_site()`
  1. 没写地名 / 写的是「本地、本机、我这里」（`is_local_intent`）→ **本机 IP 归属地**（`ip_info`，IP 库不给经纬度时用静态城市表补齐）；
  2. 明确写了城市 → 静态城市表；3. 城市表没收录 → **退回本机 IP**（不给死胡同）。
- **取数**：`fetch_sky(lat, lon)` = 7timer `astro.php?...&output=json`；`init`(UTC) + `timepoint`(3h 一档) + `tzshift` → 本地时间；`summarize()` 按官方图例给结论
  （云量 1~9 / 视宁度 1~8 / 透明度 1~8；不适合 / 适合 / 勉强 + 最佳时段）。
- **天象**：`_fetch_astro()` 尽力补月相（`moonPhase` 是**逐小时列表**，取首条 name+illumination）、日出日落与月出月落（`_hhmm()` 去掉日期时区）、**上中天（升起/落下中点，`_transit()`，跨日回绕）**、**亮度（太阳 -26.7 常数；月亮 `-12.73 − 2.5·log10(照度)`，`moon_magnitude()`）**；失败不影响结论。卡片上标注「估算」，不冒充实测值；**晨光始 / 昏影终不给数**（避免编造），只给 7timer 图与官方 wiki 链接。
- 关键符号：`resolve_sky_site`、`fetch_sky`、`summarize`、`build_sky_bundle`、`render_sky_html`、`_hhmm`、`_moon_phase`、`CLOUD_LEGEND` / `SEEING_LEGEND` / `TRANSP_LEGEND`、`SKY_TZSHIFT_KEY`（`api.sky_tzshift`，默认 8）。
- **坑**：`summarize` 挑最佳时段时必须比**平均**，不能比总和 —— 末尾单格总和天然更小，会永远「胜出」把最佳时段钉在最后 3 小时。
- **天象计算（2026-09-21 新增 `sky_events.py`）**：用户反馈模型回答「查询天象」时
  **全部写「未取得」** —— 因为 celescan.net 是 JS 单页应用（抓下来只有「正在计算…」占位），
  模型读不到正文。改为由程序**确定性计算**（Almanac 简化级数，黄经精度 ≈0.1°、月地距离
  ≈数百 km，无第三方依赖）：朔望时刻、月相与照度、超级月亮、蛾眉月 / 新月抱旧月窗口、
  四颗亮星（角宿一/毕宿五/心宿二/轩辕十四）合月日期与角距、日月食**初判**（朔望时月球黄纬
  阈值）、流星雨年表、黄道光与银河之眼季节窗口；彗星与交食精确时刻标注「**需核对**」并给出处。
  关键符号：`sun_longitude`、`moon_position`、`elongation`、`illuminated_fraction`、
  `phase_name`、`_phase_series` / `_crossings`、`syzygies`、`super_moon`、
  `crescent_earthshine`、`star_conjunctions`、`eclipse_hints`、`meteor_showers`、
  `seasonal_windows`、`build_event_report`、`render_event_text`。
- **坑**：判定朔望时不能直接用「与目标角的带符号差」过零 —— 在目标角**对面（180°）处会出现
  假穿越**（旧实现把满月当新月，两者只差 22 分钟）。必须先把角距展开成**连续递增**序列
  （`_phase_series`）再找 `target + 360k`。

## 8.3 `transient_service.py` 巡天与天文科研（`#tothestars`，2026-09-21 新增）

- **需求（用户反馈）**：「`#tothestars` 也说查不到」—— Rochester 新星表与 VSX 都是
  程序能查的数据，不该让模型写「我核查不了」。`ui_manager._on_send` 里 `#tothestars`
  点名时走 `_start_transient_chat()`：**程序取数 → 注入 system 上下文 → 交给模型解读**
  （变星类型判断、可见性、观测建议仍是模型的活）。
- **坐标解析** `parse_coords()`：时角式（`09 03 36.36 +02 24 20.6` / `09:03:36.36
  +02:24:20.6` / `09h03m36.36s +02°24′20.6″`）与十进制度都支持；`sexagesimal()` 回写
  `09:03:36.36 / +02:24:20.6`。
- **① NOVA（Rochester）**：`nova_hits()` 抓
  https://www.rochesterastronomy.org/novae.html 全文 → 解析 `R.A. = …, Decl. = …`
  条目（该站把秒写成 **`40s.938`**，先归一成 `40.938` 再解析）→ 球面角距
  `separation_arcsec()` **±10″ 内算命中**（命中输出名称/角距/星等；未命中标注「弱结论」）。
- **② VSX（双源互验，2026-09-23）**：`vsx_lookup()` 为对外入口，内部查**两个源**，
  **任一命中即算命中**（用户规范：「VSX 网站和 VizieR 双向验证，两个存在一个即可」）：
  - **源①** `_vsx_vizier_lookup()`：**VizieR asu-tsv**（`-source=B/vsx -c=… -c.geom=r
    -c.bd=0.1666667 -out=** **-sort=_r -out.max=1000**）按 10 角分检索，取回条数见 `count`。
    **`-sort=_r` + 放大 `-out.max` 是关键**：VizieR 默认按目录内部顺序（recno）返回，
    旧代码把**展示条数**当 `-out.max`（30）→ 服务端先砍、客户端才排序 → **离目标最近的那几颗
    被截掉**（用户反馈「查不到 VSSP 等小项目命名的变星」即此：坐标处 0.00′ 的条目消失，
    列表起点被推到 1.85′）。实测该半径内共 65 条，`-out.max=30` 会漏掉包含 0.00′ 的那批。
  - **源②** `vsx_site_lookup()`：**VSX 官网 VOTable**（`vsx.aavso.org/index.php?view=query.votable`）
    + 参数 `coords=<RA> <Dec>&format=d&geom=r&size=10&unit=2&order=9&filter=0,1`；规格见
    AAVSO 论坛帖 `archive.aavso.org/direct-web-query-vsxvsp`（`unit` 1=度 / 2=角分 / 3=角秒，
    `order=9` 按角距排序，`filter=0,1` = 确认变星 + 疑似变星）。实测 10′ 内 **1 秒 65 行**，
    与 VizieR 同数；**多出 AUID / 星座**，但**没有 OID 与角距列**。
  - `_merge_vsx_rows()` 合并：**名称归一相同**或位置差 ≤`VSX_MERGE_ARCSEC`(2″) 视为同一颗；
    以源①为骨架补 `auid`/`const`；**官网独有条目**（镜像尚未收录）按名称用
    `_vsx_site_oid()`（`view=api.object&format=json`）回查 OID 补详情页，每轮最多
    `VSX_OID_MAX_LOOKUP`(5) 个。每行带 `sources`（`VizieR` / `官网` / 两者）。
  - 详情页 = `vsx.aavso.org/index.php?view=detail.top&oid=<OID>`。
  - **坑（全部实测踩过，改这段必看）**：
    ① URL 里已含 `?view=…` 时参数**不能**再带 `view`（重复 → 官网静默返回 **0 条**）；
    ② `format=d` 时 `radec2000` 是 **`"320.25883,43.69372"`（十进制逗号）**、不是时角式，
       解析漏了会让**每一行都被跳过**，且**不报错**（曾表现为「官网取回 0 条」）；
    ③ 官网 `coords` 的赤纬**不要带 `+` 号**（`%2B43.69…` 会被解析失败）；
    ④ 官网约束写错时会**忽略约束返回整个目录**（实测 14 MB、十几分钟）→ 坐标与半径务必写全；
    ⑤ 官网返回是 **VOTable(XML)**，字段 id 全小写（`name/const/radec2000/varType/…`），
       用 `xml.etree.ElementTree` 解析即可（`astropy.io.votable` 反而解析不了它的 VOTable）。
  - 返回结构：`found / rows（含 auid/const/oid/sources）/ count / vizier_count / site_count /
    errors / truncated / max_fetch`；渲染层会写明两个源各取回多少条、合并去重多少条。
- **③④⑤ 链接**：`build_links()` 生成 TNS / ALeRCE(ZTF) / CDS 网址（URL 编码由程序统一，
  与用户规范示例逐字一致）；TNS/ALeRCE 有反机器人机制 → **程序不代查、只给链接**。
- 汇总：`build_transient_report()` / `render_transient_text()`（措辞固定「直接采用 /
  需核对」，模型据此输出，不再出现「我核查不了」）。

## 8.5 `privacy_clean.py` 一键隐私清理（交付前干净化）

- **作用**：清掉本机使用痕迹 → API KEY、常用接口条目、对话/记忆、日志、上传与生成文件、使用记录；**不动**角色卡、设定卡、技能库、主题。
- **用法**：`python -X utf8 privacy_clean.py`（预览 + 交互确认）/ `--yes`（静默，交付前用）/ `--scan`（只看）；界面入口：设置 → **隐私与清理 → 一键清除隐私数据…**。
- 关键符号：`PRIVATE_FILES` / `PRIVATE_DIRS` / `PRIVATE_CONFIG_KEYS`（清理清单）、`scan()`、`describe_plan()`（确认框文案）、`clean(root=, reset_config=, dry_run=)`（返回 `removed/pending/errors/freed/config_keys`）、`_truncate_file()`（被占用文件清空兜底）、`_write_pending()` / `purge_pending()`（占用目录登记后**下次启动**补删）、`fmt_size()`、`main()`。
- **占用兜底机制**：ChromaDB / 日志句柄在运行时无法删除 → 路径写入 `data/.privacy_purge_pending.json`，`main.py` 启动早期（记忆库初始化前）调用 `purge_pending()` 补删。
- **UI 侧**：`ui_manager._open_settings` 的「隐私与清理」分节（`styled_confirm` 确认 → `spawn_worker(privacy_clean.clean)` → 清空聊天区 / 刷新历史）。

## 9. `novel_learn.py`（314 行）小说学习

`_chunks`(67)、`NovelLearn.summarize`(124)、`roleplay_context`(234)、`format_summary_md`(267)；常量 `CHUNK_SIZE=6000`、`MAX_CHUNKS=14`。
**调用**：`\@novellearn` → `_run_novellearn`(7293) → 注入 `self._novel_context`（`ui_manager` 854）。
**落盘**：`novellearn/<文件名>_<时间戳>.json`。**坑**：最多处理约 84k 字；文档读取复用 `LLMClientPool._extract_document_text`。

## 10. `ponywebsite_checker.py`（635 行）小马网站检测

`load_sites`(120)、`parse_target`(145)、`_parse_ping_text`(221)、`ping_host`(278)、`tcp_probe`(313)、`http_probe`(324)、`check_site`(359)、`check_all`(418)、`format_report_md`(499)、`render_result_html`(537)、`run`(617)；常量 `MAX_WORKERS=8`、`DEFAULT_COUNT=4`、`MAX_COUNT=20`。
**调用**：`\@ponywebsite` → `_run_ponywebsite`(7392)。**数据**：只读 `data/ponywebsite.txt`（名称<Tab>网址，`#` 注释）。
**坑**：Windows ping 回显 GBK，`_decode`(196) 多编码尝试；站点 ≤3 时默认开 HTTP 探测。

## 11. `notify_service.py`（118 行）通知

`NotifyService.instance()`(47)；`notify`(55)（返回 `{通道: sent/skipped/skipped_focus}`）、`notify_pet`(96)、`cleanup_expired`(104)。
**配置**：`notify.idempotency_ttl_min(10)`、`notify.desktop_focus(always/when_unfocused)`。
**流**：`notify_service` → `bus.pet_say`(99) → `pet_manager._on_pet_say`(251)；`bus.notify_emitted`(93) → `ui_manager._on_notify_emitted`(3597)。`when_unfocused` 时用 `bus.request("ui:window_active")`(87)。

## 12. `file_tools.py`（256 行）文件读写技能

`FileTools.instance()`(49)；`_abs`(55)（相对路径基于项目根，防 cwd 漂移）、`_snapshot_before_write`(71)、`list_versions`(101)、`restore_version`(112)、`read`(130)、`write`(158)、`edit`(173)、`list_dir`(190)、`extract_document`(214)。
**调用**：`\@read/\@write/\@edit` → `_handle_file_skill`(6978)。**权限**：全部经 `utils/path_guard.PathGuard`。
**配置**：`file_tools.history_enabled(True)`、`file_tools.writable_roots`。**落盘**：`history/file_history/<sha256 前16>/…` + `index.json`（最近 50 条）。
**坑**：读 UTF-8 失败回退 GBK；写前自动快照。

## 13. `media_viewer.py`（235 行）图片查看器

`is_image_path`(39)、`_ImageView`(52)（滚轮缩放/拖拽/双击还原）、`MediaViewer`(121)（`_load_current` 180、`_navigate` 193、`keyPressEvent` 204）、静态 `MediaViewer.open(paths, index)`(228)。
**调用**：气泡附件/链接点击 → `_on_attachment_clicked`(7048) / `_on_media_link_activated`(7064)。**坑**：仅主线程。

## 14. `pet_manager.py`（1260 行）桌面宠物

| 类 | 行号 | 作用 |
|---|---|---|
| `DesktopPet` | 121 | 透明置顶桌宠（GIF 渐隐状态机）：`_pet_gif`(187)、`_crossfade`(214)、`set_state`(234)、`_show_bubble`(257)、`_show_menu`(308)、`_add_schedule`(418)、`_add_reminder`(432)、`_start_timers`(461)、`_generate_greeting`(484)、`_ensure_mini_chat`(537)、`_setup_focus_mode`(620)、`_check_sleep`(728)、`_check_focus`(752)、`_check_reminders`(763)、`_trigger_reminder`(794)、`_generate_role_message`(830)、`_show_alert`(856)、`_on_heartbeat_care`(867)、`_sync_follow_windows`(912)` |
| `_FrameWidget` | 962 | 底框图片控件（`content_rect()` 供子控件布局） |
| `MiniChatWindow` | 1003 | 200 字极简对话小窗（`say1.gif` 输入 / `say2.gif` 输出） |
| `AlertPopup` | 1244 | 悬浮提醒小窗（日程/提醒/休息/专注） |

**信号**：`role_switched`(166)、`speaker_switched`(168)、`heartbeat_care_requested`(170)、`pet_say`(251) 等都由 `_connect_bus` 类逻辑接入。
**配置**：`pet.enabled/greeting_interval_min/focus_mode/floating_chat/zoom_enabled`。
**资源**：`img/`（`answear.png`/`tell.png` 底框，`_img()` 兼容 `answer/answear` 拼写 63-69）；角色 GIF 来自 `roles_desktop/<角色>/<角色>-<动作>.gif`。

## 15. `utils/`（7 个 .py）

| 文件 | 行数 | 关键符号 |
|---|---|---|
| `async_worker.py` | 68 | `AsyncWorker`(18，信号 `succeeded/failed/progress` 22-24，`run` 43)、`spawn_worker(task, *args, on_done, on_fail, **kwargs)`(59)、`_KEEP_ALIVE`(56) 防 GC |
| `utf8.py` | 36 | `force_utf8_stdio`(22)、`utf8_env`(41) |
| `styled_msg.py` | 254 | `styled_box`(105)、`styled_info`(136)、`styled_warning`(143)、`styled_critical`(150)、`styled_question`(157)、`styled_confirm`(178) |
| `path_guard.py` | 188 | 四档权限 `BLOCKED/READ_ONLY/READ_WRITE/FULL`(35-38)；`PathGuard.instance()`(85)、`allow_dir`(105)、`level_of`(141)、`check`(182)、`readable/writable`(199/202) |
| `logical_time.py` | 50 | `logical_day`(16)（凌晨 4 点为界）、`logical_day_str`(24)、`date_label`(35)、`recent_logical_days`(48) |
| `logger.py` | 87 | `AppLogger`(23)、`get_logger`(91)，默认日志 `utils/logs/app.log`（与 `log/error.log` **不同**） |
| `__init__.py` | 1 | 包声明 |

---

## 16. 模块间调用关系（谁触发谁）

```
main.py ──(Cron)──▶ memory_pipeline.decay_and_forget / memory_dream.run_dream / auto_diary.generate_diary_async
        └─(Heartbeat)─▶ heartbeat.Heartbeat.start ──bus──▶ pet_manager._on_heartbeat_care / notify_service.notify_pet
ui_manager 左栏按钮 ─▶ daily.DailyMainWindow / focus_assistant.FocusCountdownWindow
ui_manager _on_send ─▶ (\@read|write|edit) file_tools ─▶ path_guard
                    └▶ (\@weather|novellearn|calorie|ponywebsite) _handle_program_skill
                          ├▶ weather_service.fetch_bundle/render_weather_html（API 取自 api_vault）
                          ├▶ novel_learn.summarize（结果存 _novel_context 注入 system）
                          ├▶ calorie_tracker.append_record（data/dailylifedata.json）
                          └▶ ponywebsite_checker.check_all
ui_manager 气泡点击 ─▶ media_viewer.MediaViewer.open
focus_assistant._record_session ─▶ memory_pipeline.add_short_note
memory_compile.compile_today_async ─▶（小模型）→ history/memory_compile/daily/*.json
```

**加一个「程序侧技能」**（如 `\@xxx` 由程序直接执行）：`_on_send` 分派元组(4841-4844) + `_handle_program_skill`(7183) 加分支 + 新 `_run_xxx`（内部用 `_spawn_tool_worker` 放子线程）+ 在 `skills/tools_list.json` 注册该技能。

---
