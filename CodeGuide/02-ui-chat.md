# 02 · 主界面与聊天渲染 `ui_manager.py`

> 全项目最大文件（**9956 行**）。改界面/交互/气泡/思维链/设置面板前必读。
> 行号基线：2026-09-19。**定位方式：先搜符号名，再用行号导航。**

---

## 1. 文件分区

| 区间 | 内容 |
|---|---|
| 1-60 | docstring / import / 日志 |
| 63-420 | HTML5 色常量、全局正则与工具函数（`_img_html`、`btn_qss`、`apply_shadow`、`animate`、`fade_pixmap`、`_load_pixmap`、`_blur_image`） |
| 147-223 | `_FlowLayout` 流式布局 |
| 423-1007 | `ChatWorker`（后台对话线程） |
| 1009-1275 | `AboutDialog` / `_ElidedLabel` / `_StatsLabel` / `_HistoryRow` |
| 1309-1750 | 气泡折叠体系：`CollapsibleBlock` / `ThinkingBlock` / `PlotBlock` / `LongOutputBlock` / `ChoiceRow` |
| 1753-2475 | **显示层清洗函数区**（标签提取、思维链拆分、markdown、链接、选项） |
| 2476-9730 | `MainWindow`（约 73% 体量） |
| 9733-9954 | QSS 与颜色工具（`root_qss`、`_scale_qss`、`_hex_to_rgba`、`_lighten`、`_darken`） |

---

## 2. 显示层清洗（模块级函数，**改文本渲染先看这里**）

### 2.1 统一入口

| 符号 | 行号 | 作用 |
|---|---|---|
| `split_display_blocks(text, hold_tail=False)` | 2089 | **唯一显示拆分入口**：返回 `(正文, 思维链, 折叠内容, 选项)`；内部依次做注释提取 → 思维链相位 → details 折叠 → 剧情设计 → 注入段剥离 → `<w2g>` |
| `history_display_text(text)` | 1884 | 历史回放时的显示清洗 |

### 2.2 思维链相位（角色扮演长设定卡核心）

| 符号 | 行号 | 作用 |
|---|---|---|
| `_COT_HINT_RE` / `_COT_PROTOCOL` | 401 / 407 | 「本轮需要思维链」的提示词正则 / 统一输出协议文本 |
| `_COT_NORM_MAP` | 1989 | 全角标记归一（`｜`→`|`、`▁`→`_`），单字符替换保证索引可切片 |
| `_COT_DELIM_TAGS` / `_COT_DELIM_RE` | 1991 / 1999 | 思考段定界标签集合 / 定界符正则（含 `<!-- -->`） |
| `_COT_PLAIN_TAGS` / `_COT_TAIL_CAND_RE` | 2003 / 2009 | 半截标签前缀判定表 / 流式尾巴正则 |
| `_cot_tag_is_close` / `_hold_partial_delim` | 2012 / 2020 | 判断开/闭标签 / 流式期间按住半截定界符 |
| `split_cot_stream(text, thinking_mode, hold_tail)` | 2060 | 返回 `(思维链, 正文, 是否未闭合)`；**未闭合时其后全部归思维链** |
| `_delim_is_structural(src, m)` | ~2027 | **只认真结构标签**：两侧都紧贴字符的「被引用标签」不切换相位（TGbreak 计划文本的根因修复） |
| `extract_cot_comments` | 1952 | 抽出 HTML 注释里的思考 → `(正文, 思考)` |
| `_PRESET_END_RE` | ~1774 | 预设结束符号（`《end》`、`<end>`）在 `split_display_blocks`／`strip_struct_tags`／`_render_body_html` 三处摘除 |
| `_THINK_TAG_RE` | 1296 | 思维链块内残留标签壳清理 |

**规则**：① 定界符本身不出现在任何一侧；② `thinking_mode=True` 时首个定界符之前的内容也算思考；③ 流式 `hold_tail=True` 时末尾半截标签（`<thi`/`<｜begin▁of▁thi`/`【思`）先不显示；④ **被引用的标签写法不切换相位**（详见 2.6）。

### 2.3 结构标签与折叠块

| 符号 | 行号 | 作用 |
|---|---|---|
| `_STRUCT_TAGS` | 1757 | 结构标签名（`draft_notes/thinking/content/draft/基础确认…`） |
| `strip_struct_tags(text)` | 1774 | 去掉这些标签的 `<>`，**只留文字** |
| `_SUMMARY_TAGS` / `extract_tag_blocks` | 1767 / 1783 | 摘要/草稿类标签 → `(正文, {标签:内容})` |
| `_PLOT_HEAD_RE` / `_PLOT_OPEN_RE` / `_PLOT_TAGS` | 1806-1814 | 「剧情设计/梳理」标记与未闭合草稿标签 |
| `extract_plot_draft(text)` | 2113 | 抽剧情设计 → `(正文, 剧情设计)` |
| `extract_html_folds(text)` | 1838 | 抽 `<details>` 折叠块 → `(正文, 折叠列表)` |
| `strip_injected_blocks(text)` | 1899 | 剥离模型复述的 OOC/记忆注入段 |
| `_OOC_NOTICE_RE` / `_MEMORY_BLOCK_RE` | 1874 / 1878 | 上述两类残留的正则 |

### 2.4 正文渲染与链接

| 符号 | 行号 | 作用 |
|---|---|---|
| `_render_body_html`（MainWindow） | 5716 | 正文 → HTML：剥结构标签 → 图片占位 → 转义 → 轻量 markdown → 还原图片链接 |
| `mini_markdown` | 2159 | 轻量 markdown（Qt 富文本子集） |
| `linkify_urls(text, accent)` | 2238 | 正文裸链接 → 可点击 `<a>` |
| `_BARE_URL_RE` / `_URL_TAIL_PUNCT` / `_URL_SHOW_LIMIT` | 2231-2235 | 链接识别 / 结尾标点 / 显示长度上限(64) |
| `_soft_break` / `_LONG_TOKEN_RE` / `_ZWSP` | 1301 / 1293 / 1294 | 超长无空格串插零宽空格，保证换行 |
| `_img_html(name,size)` | 136 | 内联小图标 `<img>` |

### 2.5 选项与选择框

| 符号 | 行号 | 作用 |
|---|---|---|
| `MAX_CHOICES` | 2290 | 选项上限（8） |
| `extract_choices(text)` | 2390 | 抽 `【选项】` → `(正文, 选项列表)`；`_CHOICE_LINE_RE` 要求「选项/请选择/可选项」成词或「选择：」，**避免把「选择框、…」计划文本当标记** |
| `_EMOTION_OPEN_TAIL_RE` | 1917 | 末尾未闭合情绪标签（`嘿！【开心`）与其后内容一并隐藏；`_display_text` 在 `strip_emotion_tags` 之后应用，避免流式截断露出半截标签 |
| `ChoiceRow` 宽度约束 | 1661 | 按钮 `setMinimumWidth(1)` + `resizeEvent`→`_fit_row_width` 按实际宽度重折文字；**靠 `_chat_container.setMinimumWidth(1)` 保证容器不被按钮撑出视口**（横向滚动条 AlwaysOff，溢出即裁剪） |
| `_row_item_index(wrap, bubble)` | 5857 | 精确定位「承载正文气泡的那一行」在 wrap 布局里的索引（结构：wrap→row→col→气泡） |
| `_enforce_message_order(wrap)` | 5872 | **顺序不变量执行器**（幂等）：列内 `思维链→剧情→正文→完整内容块`；wrap 内 `正文单元行→选项行→操作行`。`_append_choice_row` / `_on_finished` / 历史回放 / `_normalize_message_blocks` 全部接入 |
| `extract_w2g_choices(text)` | 2490 | 抽 `<w2g>` 选择框；开标签必须是结构标签（行首/前面只有空白） |
| `_looks_like_options(block)` | 2482 | 判定 `<w2g>` 内容是否真的是选项（行数超上限/多数行无编号 → 不是），防止把正文吞成按钮 |
| `_CHOICE_*` / `_W2G_*` | 2275-2483 | 选项与 w2g 的识别正则 |

### 2.6 坑：**被引用的标签**（TGbreak「老问题」根因）

预设的「格式检查」会把自己的标签写进说明文字，例如 TGbreak 的计划文本：

```text
- 格式检查: step1：… <draft_notes>（梳理内容）</draft_notes>、正文、<w2g>选择框、<details>摘要。
step3：用->编排顺序：<draft_notes> -> 正文 -> <w2g> -> <details>。
</draft_notes>          ← 这一行才是真正的闭合标签
暮光闪闪正在用魔法…      ← 真正的正文
```

若把**引用的** `</draft_notes>` 当真标签，思维链会被提前关闭 → 计划文本与正文错位；
再叠加 `extract_w2g_choices` 从引用的 `<w2g>` 一直吞到真正的 `</w2g>`，
就会出现「正文只剩 `、正文、`、正文段落变成一排按钮」的经典故障。

两道防线（改动显示层时**务必保留**）：

1. `_delim_is_structural()`：定界符**两侧都紧贴非空白字符**时视为「引用写法」，不切换相位
   （真实标签至少一侧是行首/行尾/空白）；HTML 注释边界不受此限。
2. `<w2g>` 开标签必须是结构标签 + `_looks_like_options()` 选项形态校验；不像选项块时
   只去掉标签壳、内容留在正文，**绝不把正文变成按钮**。


---

## 3. `ChatWorker`（423-1007）——后台对话线程

**信号**：`token`(429)、`reasoning`(430)、`emotion`(431)、`finished`(432)、`error`(433)、`usage`(434)、`notice`(435)。

| 分组 | 方法（行号） |
|---|---|
| 前缀清洗 | `_leading_name_prefix`(524)、`_is_name_like`(545)、`_strip_role_prefix`(559)、`_strip_group_prefix`(576)、`_first_speaker_only`(602)、`_normalize_group_reply`(658) |
| 群聊判定 | `_judge_group_speaker`(650)（判定结果走 `RoleManager.match_member` 归属） |

> **名字口径（2026-09-23）**：上表的前缀清洗与群聊判定**一律**用
> `role_manager.normalize_role_name` 归一化后比较 —— 成员表是下划线名
> （`Twilight_Sparkle`）而模型常输出空格名（`[Twilight Sparkle]:`），
> 逐字符匹配会让**前缀剥不掉**（留在气泡里）、**发言者错位**（回退上一发言者）。
> 详见 `05-roles-roleplay.md` A.1 坑④。

> **群聊只允许一位成员发言（2026-09-23）**：`_first_speaker_only`(602) 会剥掉开头前缀
> 并**在第二位发言人处截断**，其后成员的名字与台词**整段丢弃**（气泡、会话记录、
> 记忆归档一致，都不存）；同一个名字分段重复出现只剥前缀、内容继续保留；
> 正文里的 `注意：`、`[1]:` 不会被误当发言人。三处共用它：收尾
> `_normalize_group_reply`(658)、显示层 `_display_text`(7309)、主动问候。
> 行提示词侧同步加了【硬性要求】禁止模型写第二位成员（`_build_system_prompt`）。
| 引擎/隔离 | `_init_engine`(617)、`_assembly`(631)、`_isolation`(642) |
| 提示词 | `_build_system_prompt`(653) |
| 主流程 | `abort`(719)、`run`(723)（内部 `_attachment_context`（附件→注入文本，见 1.6）/ `_reasoning_cb` 898 / `_usage_cb` 916 / `_collect` 923） |

**system 消息装配顺序**（`run`，改注入内容必看）：
1. `system[0]` 角色卡系统提示（`_build_system_prompt` 653-717，含用户昵称、登陆时间上下文 680）
2. `system[1]` `【角色扮演设定】` + `eng.build_context(...)`（769-807，仅当 `roleplay_enabled`）
3. `system[2]` 相关记忆（`retrieve_before`，808-809）——受 `assembly.use_memory` 控制
4. 技能/思维链协议/联网/skillspub/项目目录/小说学习/历史轮次（810-890）
5. 最后 user 消息（891）

**记忆隔离计算**（760-776）：`mode = "roleplay"`（开启角色扮演且 `isolation.isolate_normal`）否则 `"normal"`；`scope = _roleplay_scope`。

---

## 4. `MainWindow` 关键方法地图

### 4.1 构建与生命周期

| 方法 | 行号 | 说明 |
|---|---|---|
| `__init__` | 2479 | 状态、尺寸(2564-2572)、字体、恢复上次状态 |
| `_restore_ui_state` / `_persist_ui_state` | 2600 / 2623 | 读写 `ui.last_*` |
| `_build_ui` / `_build_title_bar` / `_build_left_panel` / `_build_right_panel` / `_build_chat_area` / `_build_history_widget` / `_build_input_area` / `_build_tray` | 2643 / 2672 / 2857 / 3005 / 3060 / 3080 / 3351 / 3441 | 界面骨架 |
| 无边框拖动与缩放 | `_title_press`(2701)、`_init_resize`(2733)、`_resize_edge`(2759)、`_apply_resize`(2774)、`_update_resize_cursor`(2801)、`_resize_event`(2815)、`resizeEvent`(4068) | 四边/四角缩放 |
| 应用图标（窗口/托盘/任务栏同源） | `app_icon()`(1197)、`MainWindow._app_icon`、`_apply_icon`、`_build_tray`(3602) | 优先级：`ui.icon_path`（**文件存在才用**）→ 内置 `img/logo_128x128.ico`（同族 256/48/32/16/png）→ 纯色兜底；`main.py` 另设 Windows `AppUserModelID` |
| `closeEvent` / `hideEvent` | 9717 / 9724 | 关闭行为（托盘常驻） |

### 4.2 发送与请求

| 方法 | 行号 | 说明 |
|---|---|---|
| `_on_send` | 4777 | **发送总入口**：附件、`/theme`、`\@project`、`\closed`、技能语境、review 授权、生图/文件/程序技能分派 |
| `_on_attach` | 4961 | 附件（**`QFileDialog.getOpenFileNames` 多选**，只挂到待上传列表） |
| `_refresh_attach_bar` / `_remove_pending_attachment` / `_clear_pending_attachments` | 4975 / 5082 / 5096 | 附件条 |
| `_start_chat` | 5208 | 创建 `ChatWorker` + 线程，置 `_busy=True`，建用户/回复气泡 |
| `_route_image_gen` / `_gen_image_task` / `_on_image_generated` | 5102 / 5150 / 5178 | 生图技能路由 |

### 4.3 流式渲染与收尾（**核心**）

| 方法 | 行号 | 说明 |
|---|---|---|
| `_on_stream` | 5957 | 追加 `_stream_buffer`，节流调度刷新 |
| `_schedule_bubble_refresh` / `_flush_bubble_refresh` | 5993 / 6025 | 合并高频 token，调用 `split_display_blocks(raw, hold_tail=True)` |
| `_stream_window` | 6017 | 超长输出的显示窗口裁剪 |
| `_update_bubble_text` | 5781 | 把 `(思维链, 正文)` 落到思维链块与气泡；**正文为空且内容都在思维链时不再回退显示原文**。新增 `cot_override` / `plot_override`（收尾传权威文本；**参数名不能叫 `cot`/`plot`**，否则被函数内同名局部变量覆盖） |
| `_ensure_thinking_block` / `_ensure_plot_block` | 5684 / 5699 | 惰性插入折叠块 |
| `_insert_block_above_bubble` / `_reorder_message_blocks` | 5636 / 5656 | 块顺序维护（思维链在正文之上） |
| `_on_finished` | 6077 | 收尾：`split_cot_stream` → `_split_thinking_text` → 摘要/剧情/选项/`w2g` → 归档 → 统计。**思维链/剧情折叠块用收尾算好的 `_cot`/`plot_part` 灌入**（不能按气泡显示文本反推：长回复走摘要后拆不出思考段），收尾末尾必须跑一次 `_normalize_message_blocks()`（全量、非节流）修正顺序与**实际绘制位置** |
| `_on_reasoning` / `_render_thinking_body` | 5588 / 5598 | 模型原生 reasoning |
| `_thinking_visible` / `_msg_thinking_visible` | 6269 / 6283 | **思维链折叠块是否显示**：全局开关 `ui.show_thinking` **或**本轮/该条消息用了 `\@thinking`（存档字段 `thinking_skill`）。改显示策略只改这两处 |
| `_on_error` / `_on_error_recovered` / `_force_recover_send` / `_cleanup_worker` / `_terminate_output` | 6265 / 6300 / 6334 / 6345 / 6366 | 异常与强制恢复（5s 计时器） |

### 4.4 气泡与会话

| 方法 | 行号 | 说明 |
|---|---|---|
| `_append_bubble` | 6528 | 生成气泡（含复制/停止按钮、图片下载、折叠） |
| `_display_text` / `_strip_skill_display` | 6451 / 6471 | 显示前的 `\@`/`#` 指令剥离 |
| `_enable_bubble_links` | 6510 | 气泡链接可点 |
| `_on_bubble_context_menu` / `_insert_quote` / `_copy_message` | 6754 / 6777 / 7501 | 右键菜单/引用/复制 |
| `_maybe_compress_bubble` / `_attach_full_block` / `_append_choice_row` | 7539 / 7525 / 7551 | 长消息折叠、整段查看、选项行。`REPLY_COMPRESS_LIMIT`（行 7914）= **24000**（= `STREAM_PLAIN_LIMIT`）：**需求「长回复不要隐藏」**，常规长回复在气泡里完整显示，只有极端超长才退化为「摘要 + 完整内容折叠块」 |
| `_prune_chat_bubbles` / `_clear_chat_area` / `_delete_message_widget` | 7170 / 7572 / 7579 | 气泡上限（`_MAX_BUBBLES=80`，行 113） |
| `_new_session` / `_save_session` / `_generate_session_title_async` / `_on_history_clicked` / `_refresh_history_list` | 7618 / 7651 / 7715 / 7961 / 7790 | 会话存档与历史（含置顶/归档：3135-3348） |
| `_session_key` / `_read_session_meta` | 7609 / 7757 | 会话元数据 |

### 4.5 技能与公共库交互

| 方法 | 行号 | 说明 |
|---|---|---|
| `eventFilter` | 4201 | 输入框键盘：`\@` 弹窗、`#` 弹窗、回车/上下/退格（**改快捷键只改这里**） |
| `_refresh_skill_popup` / `_close_skill_menu` / `_on_skill_selected` / `_insert_skill_tag` / `_skill_tag_backspace` | 4439 / 4460 / 4466 / 4491 / 4509 | `\@技能` |
| `_parse_skill_tags` / `_expand_skill_prompt` / `_build_skill_context` / `_process_skill_commands` / `_reset_pro_mode` / `_retain_skill_tags` | 4533 / 4537 / 4545 / 4566 / 4589 / 4606 | 技能指令解析与注入 |
| `_refresh_pub_popup` / `_on_pub_selected` / `_insert_pub_tag` / `_pub_tag_backspace` / `_parse_pub_command` / `_strip_pub_display` | 4653 / 4686 / 4707 / 4721 / 4758 / 4767 | `#技能名` 公共库 |
| `_skill_should_trigger` / `_pub_should_trigger` | 4623 / 4838 | 触发语境判定（**在 KeyPress 里看光标前一位**，见下方「坑」） |

> **坑（2026-09-21 修复）**：这两个判定函数是在 `#` / `@` 的 **KeyPress 事件中**调用的，此刻字符**还没进文档** → 只能看光标**前一个字符**。旧 `_pub_should_trigger` 却断言 `characterAt(pos-1) == '#'`（要求 `#` 已在文档里），对真实键盘永远为假 → **`#` / `/#` 弹窗根本不出现**（`\@` 那条分支写的是前一位是否为 `\`，所以一直正常）。

### 4.6 程序侧技能（`\@weather` 等直接执行）

| 方法 | 行号 | 说明 |
|---|---|---|
| `_handle_program_skill` | 7183 | 分派器（`weather/novellearn/calorie/ponywebsite`） |
| `_spawn_tool_worker` / `_append_tool_card` | 7215 / 7223 | 子线程执行 + 结果卡片 |
| `_run_weather` / `_run_novellearn` / `_run_calorie` / `_run_ponywebsite` | 7246 / 7293 / 7350 / 7392 | 四个程序侧技能 |
| `_handle_file_skill` / `_needs_file_project` | 6978 / 6966 | `\@read/\@write/\@edit` |
| **程序侧 skill库 技能** | `PROGRAM_PUB_SKILLS`（常量）/ `_handle_program_pub_skill` / `_run_sky` | 2026-09-21 新增：`#tothemoon`（观星/天象）点名时由**程序**执行（读本机 IP → 7timer → 卡片），不进普通对话；`_on_send` 里在 `pub_names` 算好后、LLM 之前分派 |
| `_current_workdir` / `_ensure_project_dir` / `_handle_project_command` / `_all_project_paths` | 6823 / 6844 / 6875 / 6908 | 技能产出目录与 `\@project` |

### 4.7 设置面板 `_open_settings`（8058-9179）

- `_section(title)`(8134) 生成分区；`_apply()`(9084-9153) 统一保存（`_cfg.save()` 在 9139）；`dialog.show()` 非模态复用（9175/8066）。

| 分区 | `_section` 行 | 写入的配置键 |
|---|---|---|
| 对话 API | 8152 | `api.*`、`chat.temperature/max_tokens`、`ui.show_thinking` |
| 隐私与清理 | 9879 | 无（→ `privacy_clean.clean`）；含「**清除完成后退出程序**」勾选（调试后交付他人用） |
| 多模态 | 8210 | `api.vision_*`、`api.main_vision`、`api.attach_max_chars` |
| 生图 API | 8220 | `api.image_*` |
| 联网搜索 | 8252 | `web.enabled/search_base/search_key` |
| 资源路径 | 8266 | `paths.*`、`user_avatar`、`ui.user_name` |
| 项目目录 | 8314 | 无（→ `_move_project` 9678） |
| 外观 | 8325 | `ui.blur_background/transparent_chat/icon_path/font_family/chat_font_size`（主题/主色在 `_apply_theme` 3964 / `_apply_accent` 3977） |
| 技能工具 | 8450 | 无（→ `_open_skilltools` 9612 / `_open_api_vault` 9635） |
| skill库管理器 | 8464 | `ui.skillspub_enabled` |
| 角色对话管理 | 8491 | `ui.roleplay_cards`、`ui.roleplay_user_persona`、`ui.roleplay_ooc_mode` + 隔离(8761-8779) |
| 角色卡与技能包 | 8792 | 无（导出/导入/打包/安装/评测：9343/9375/9454/9199/9252） |
| 桌面形象 | 8825 | `pet.*`、`heartbeat.*` |
| 记忆 | 8853 | 训练/遗忘/固定记忆/Dream（多数由模块自行落盘） |
| 隐私与清理（**保存分节之前**） | 约 9110 | 无配置键（确认框 → `spawn_worker(privacy_clean.clean)` → 清空聊天区 / 刷新历史） |

### 4.8 其它常用

| 方法 | 行号 |
|---|---|
| `_apply_theme` / `_apply_accent` / `_apply_scale` / `_load_theme` | 3964 / 3977 / 4005 / 3952 |
| `_bubble_max_width` / `_resize_bubbles` / `_fit_portrait` / `_portrait_available_height` / `_set_portrait` / `_apply_portrait` | 4030 / 4044 / 4123 / 4089 / 2939 / 2967 |
| `_scroll_to_bottom` / `_full_scroll` / `_flush_scroll` | 6395 / 6426 / 6445 |
| `_on_usage` / `_refresh_stats_label` / `_stats_tip` / `_reset_token_stats` | 5384 / 5413 / 5455 / 5338 |
| `_append_notice` | 7444（系统提示气泡） |
| `_toggle_find_bar` / `_find_next` | 7110 / 7119（会话内查找） |
| `_open_daily` / `_open_focus_assistant` / `_toggle_pet` / `_ensure_pet` | 3520 / 3548 / 3483 / 3503 |
| `_connect_bus` / `_on_notify_emitted` / `_ui_active_hint` | 3572 / 3597 / 3593 |

---

## 5. 关键状态变量（生命周期）

| 变量 | 行号 | 含义 | 置位 / 复位 |
|---|---|---|---|
| `_busy` | 2506 | 是否有对话进行中 | `_start_chat` 置 True；`_on_finished`/`_on_error`/`_cleanup_worker` 复位 |
| `_chat_worker` / `_worker_thread` | 2488 / 2487 | 当前 worker 与线程 | `_start_chat` 创建，`_cleanup_worker` 清理 |
| `_current_bubble` | 2489 | 当前流式气泡 | `_start_chat` 设置 |
| `_stream_buffer` | 5293 | 本轮原始流式文本 | `_start_chat` 清空，`_on_stream` 追加 |
| `_reasoning_text` | 5222 | 原生 reasoning 累积 | `_on_reasoning` 追加 |
| `_stream_cot_extra` | 5227 | 流式期间额外并入思维链的内容 | `_flush_bubble_refresh` 累积 |
| `_thinking_mode` | 5225 | 本轮思维链模式 | `_start_chat` 赋值 |
| `_thinking_skill` | 5226 | 本轮是否**用户点名** `\@thinking`（→ 即使 `ui.show_thinking` 关闭也显示思维链，并写入消息 `thinking_skill` 供回放） | `_start_chat` 赋值 |
| `_w2g_options` | 2491 | `<w2g>` 选项累积 | 每轮清空，`_on_finished` 渲染按钮 |
| `_pending_attachments` | 2527 | 待发送附件 | `_on_attach` 追加，`_clear_pending_attachments` 清空 |
| `_pro_skill_active` / `_skill_closed` | 2560 / 2561 | 专业模式 / `\closed` 关闭技能语境 | `_process_skill_commands` 管理 |
| `_messages` / `_bubble_widgets` | 2508 / 2510 | 会话消息与气泡控件（按序对应） | `_start_chat`/`_on_finished`/`_on_history_clicked` |
| `_session_path` / `_current_role/_current_group/_current_mode` | 2515 / 2517 | 会话与当前角色/群组/模式 | `_new_session`/`_on_mode_changed`/`_apply_role` |
| `_proactive_worker` / `_proactive_on` | 3668 / 2520 | 主动对话 | `_on_pet_proactive` / `_on_proactive_toggled` |
| `_roleplay_on` / `_roleplay_scope_id` | 2523 / 2525 | 角色扮演开关 / 会话作用域 | `_on_roleplay_toggled` / `_roleplay_scope` |
| `_accent` / `_scale` / `_transparent_chat` / `_blur_bg` / `_background_pm` | 2531-2535 / 2530 | 外观 | `_apply_*` |
| `_last_stream_render` | 2562 | 流式节流时间戳 | `_flush_bubble_refresh` |

---

## 6. 常见修改场景 → 落点

| 场景 | 改哪里 |
|---|---|
| 改发送/指令分派 | `_on_send`(4777-4960) |
| 改请求参数与气泡创建 | `_start_chat`(5208-5337) |
| 改流式节流/刷新 | `_on_stream`(5957)、`_flush_bubble_refresh`(6025) |
| 改收尾归并 | `_on_finished`(6077-6221) |
| 改思维链拆相 | `split_cot_stream`(2042)、`split_display_blocks`(2089)、`_COT_*`(1989-2017) |
| 改剧情设计/折叠 | `extract_plot_draft`(2113)、`_ensure_plot_block`(5699)、`CollapsibleBlock`(1309) |
| 改选项按钮 | `extract_choices`(2390)、`ChoiceRow`(1645)、`_append_choice_row`(7551) |
| 改正文 markdown/链接/图片 | `_render_body_html`(5716)、`mini_markdown`(2159)、`linkify_urls`(2238) |
| 改立绘/情绪 | `_set_portrait`(2939)、`_fit_portrait`(4123) |
| 改角色名显示 / 顶部选择框宽度 | `RoleManager.sidebar_label`（完整名称，不截断）、`_refresh_select_combo`(3763) 尾部调用的 `_fit_select_combo()`、上限常量 `_SELECT_MAX_W`(114，2026-09-21 由 340 放宽到 520)；**必须同时放宽左栏 `_left_panel` 的最小宽度**（否则布局把选择框压回去，Qt 仍画省略号），并在 `showEvent` 里延迟重算一次（建窗口时字体/QSS 未生效，测出来偏小） |
| 改设置项 | `_open_settings`(8058-9179) + `_apply`(9084) |
| 改主题/缩放/QSS | `_apply_theme`(3964)、`_apply_scale`(4005)、`root_qss`(9902)、`_root_qss_base`(9742) |
| 改快捷键/回车/退格 | `eventFilter`(4201)、`_skill_tag_backspace`(4509)、`_pub_tag_backspace`(4721) |
| 改气泡宽度/自适应 | `_bubble_max_width`(4030)、`_resize_bubbles`(4044) |
| 改历史/存档/回放 | `_save_session`(7651)、`_refresh_history_list`(7790)、`_on_history_clicked`(7961) |
| 改导入导出/技能包 | `_export_current_role_card`(9343)、`_import_role_card`(9375)、`_install_skill_zip`(9199)、`_open_bundle_manager`(9454) |
| 改托盘/最小化 | `_build_tray`(3441)、`_toggle_visible`(3475)、`closeEvent`(9717) |
| 改应用图标 | 模块级 `app_icon()`（窗口/托盘/任务栏共用）；`main.py` 里 `app.setWindowIcon` + `AppUserModelID`；**不要**再自绘纯色 pixmap |
| 改主动对话 | `_on_pet_proactive`(3632)、`_show_proactive`(3681) |
| 改 token/速度显示 | `_on_usage`(5384)、`_refresh_label`→`_refresh_stats_label`(5413)、`_StatsLabel`(1113) |
| 加程序侧技能 | `_on_send` 分派(4841-4844) + `_handle_program_skill`(7183) + `_run_*` |

---

## 7. 坑

1. **别名兼容**：`_role_combo`(2887) = `_select_combo`(2881)、`_group_combo`(2899) = `_mode_combo`(2890)，改动别混用。
2. **正文气泡禁止回退显示原文**：`_update_bubble_text` 在「正文为空但思维链有内容」时必须留空，否则思考过程会漏进正文。
2.1 **不要按「气泡显示文本」反推思维链**（2026-09-21 修）：长回复被压成摘要后，
   摘要里没有思考标记 → 反推得到空思考段 → `_update_collapse_blocks` 会
   `setVisible(False)`，表现为**收尾后思维链凭空消失**。收尾一律用 `_cot` /
   `plot_part` 通过 `cot_override` / `plot_override` 传入。
2.2 **收尾必须跑 `_normalize_message_blocks()`（全量）**：流式期间反复
   `setText/setVisible` 会让折叠块几何体陈旧——块排在正文上方却**画在正文下方**
   （用户反馈「思维链显示在对话下面」）。该方法同时校验布局索引与
   `_painted_below`，61 条消息实测仅 7ms。
2.3 **长回复不折叠**：`REPLY_COMPRESS_LIMIT=24000`，「…（已折叠，共 N 字…）」
   只允许出现在极端超长场景；改小它会立刻触发用户可见的「内容被隐藏」。
3. **`<content>` 标签**：整段删除；若删完回复为空（YinBreak 用它包正文）则用 `_CONTENT_BOUNDARY_RE` 只剥标签（见 `ChatWorker` 内清洗段）。
4. `_settings_dialog` 是**非模态复用**实例，重复打开不重建，改设置项时要考虑「已存在旧控件」。
5. 超长输出走 `_stream_window` 纯文本窗口（`STREAM_PLAIN_LIMIT`），该路径不经过 `_update_bubble_text` 的块渲染。
6. 新增控件属性请统一 `self._xxx` 命名，并注意 `_apply_scale`/`_resize_bubbles` 是否需要同步尺寸。
7. **不要再加「输入草稿持久化」**（2026-09-21 按用户要求整块移除）：曾把输入框内容
   按会话写进 `data/input_drafts.json` 并在启动/切会话时回填，用户明确表示「重启后
   不要保留上次没发出去的话」。输入框只反映当前真实输入。
8. **附件识别失败不许静默**：`ChatWorker._attachment_payload()` 失败时发 `notice`
   信号（→ `_append_notice` 系统提示气泡）并在注入文本里标「未能识别，已跳过」，
   防止模型对着空内容编造文件信息。附件解析一律走
   `LLMClientPool.attachment_text()`（文档给原文、图片先缩放并按**用户问题**分析，
   见 03 文档 1.6）。
9. **图片走哪条路取决于主模型**：主模型支持图片输入（`api.main_vision` 勾选，
   或模型名命中 vl/vision/gpt-4o… 自动判定）→ `run()` 用 `ChatWorker._user_content()`
   把 user 消息组装成多模态数组（text + image_url[]），主模型直接看图；否则才用
   视觉模型转述。用户反馈「MoE 只能发送摘要，分析不了图」即此路径缺失所致。

