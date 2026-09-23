# 05 · 角色系统与角色扮演子项目

> 改角色卡、立绘、群聊、角色扮演设定卡/预设/世界书/正则时读这本。
> 行号基线：2026-09-19（`role_manager.py` 415 行；`roleplay/` 合计约 3300 行）。

---

# A. 角色系统

## A.1 `role_manager.py`（415 行）——角色库单例

**作用**：扫描 `roles/` 解析角色卡（`roles.json`）与情感映射（`emotion.json`）；提供群聊发言者解析、情感标签归一、立绘/桌宠动图路径解析。

**被谁用**：`ui_manager`、`pet_manager`、`focus_assistant`、`auto_diary`、`main`。

| 符号 | 行号 | 作用 |
|---|---|---|
| `STANDARD_EMOTIONS` | 26 | `neutral/happy/sad/angry/surprised` |
| `SIDEBAR_MAX_WIDTH = 10` | 31 | 宽度上限常量（中文算 2）；**仅供 `truncate_wide` 默认参数使用**，角色名已不再截断 |
| `_SPEAKER_PATTERNS` | 34-43 | 群聊发言者解析正则 |
| `_NAME_SEP_RE` / `_NAME_WRAP_RE` / `_MIN_FUZZY_LEN` | 69-73 | 名字归一化：分隔符集 / 首尾装饰符 / 子串匹配最短长度 |
| `normalize_role_name` | 76 | **名字归一化键** —— 下划线≡空格≡连字符≡中点≡全角≡大小写（全项目「名字比较」的唯一口径，见本册 A.1 坑④） |
| `_EMOTION_TAG_RE` / `_EMOTION_BRACKET_RE` / `_DOUBLE_BRACKET_RE` | 47 / 48 / 50 | 情感标签 |
| `RoleManager` / `instance()` | 53 / 67 | 单例入口 |
| `list_roles` / `role_card` | 74 / 85 | 扫描含 `roles.json` 的目录 / 读卡（有缓存） |
| `display_name` / `_abbr_map` / `_display_width` / `truncate_wide` / `sidebar_label` / `full_name` | 101 / 107 / 122 / 127 / 145 / 159 | 显示名与侧栏缩写（`data/role_abbr.json` → 宽度截断兜底） |
| `default_emotion` / `character_description` / `role_system_prompt` / `emotion_map` | 166 / 172 / 182 / 197 | 角色卡字段读取（`character description` 与 `character_description` 都兼容） |
| `_global_kw_map` / `detect_emotion` / `strip_emotion_tags` | 213 / 231 / 270 | 文本 → 标准情感 / 去掉情感标签 |
| `list_groups` / `group_members` / `speaker_hint` / `group_aliases` / `mentioned_member` / `member_intro` | 294 / 307 / 314 / 321 / 371 / 427 | 群聊 `roles/group.json` 与点名（`mentioned_member` 末段按归一化键兜底扫一遍） |
| `match_member` | 437 | **名字 → 成员标准名**：归一化精确（成员名/display_name/别名键）→ 归一化子串（`Twilight`↔`Twilight_Sparkle`）；归不到返回空串 |
| `parse_speaker` / `resolve_speaker` | 483 / 507 | 解析/兜底判定发言者；均接受 `aliases`；名字一律走 `match_member` |
| `portrait_path` / `pet_gif_path` | 407 / 440 | 立绘 / 桌宠 GIF 路径 |

**配置键**：`paths.roles`(76/90/201/296)、`paths.roles_img`(414)、`paths.roles_desktop`(442)、`paths.data`(112，读 `role_abbr.json`)。

**目录约定**

```
roles/                     角色卡：roles/<角色名>/roles.json + emotion.json（顶层 group.json 为群聊预设）
roles_img/<角色名>/<角色名>-<情绪>.png    情绪： neutral/happy/sad/angry/surprised；无情绪用 <角色名>-.png；OLD/ 不参与解析
roles_desktop/<角色名>/<角色名>-<动作>.gif  动作： standing/run/say1/say2/hello/sleep/work
```
`roles.json` 关键字段：`name, personality, system_prompt, default_emotion, emotion_list, first_message, character description`。

**坑**：① 角色卡/情感/缩写都有进程内缓存，改 JSON 需重启；② `sidebar_label` 对超长缩写也做截断；③ 情感标签正则把任意 `【xx】` 当候选，需 `_global_kw_map` 命中才删除。

④ **同一角色有两套名字写法，任何名字比较都必须先归一化**（2026-09-23 修复的事故）：
成员表 / 角色目录名是**下划线**形式（`roles/Twilight_Sparkle`、`group.json` 的 `members`），
而模型输出是**空格**形式（`[Twilight Sparkle]: …` —— 角色卡 `system_prompt` 里自称
「你是Twilight Sparkle」，模型照着念就会写成空格）。旧代码用 `==` / `re.escape(name)`
做**逐字符**匹配 → 必然失配，出现两个可见故障：

- `parse_speaker` / `_judge_group_speaker` 归不到成员 → `resolve_speaker` 回退
  「上一发言者」→ **Twilight 的话显示成 Applejack 说的**（发言人与角色名错位）；
- `ChatWorker._strip_group_prefix` 剥不掉 → `[Twilight Sparkle]:` **原样留在气泡正文**里。

→ 规则：**一律** `normalize_role_name()` 后比较，或直接用 `match_member()` 归属。
落点共 5 处，新增解析/剥离代码时照此办理：
`parse_speaker`(483)、`resolve_speaker`(507)、`mentioned_member`(371 末段)、
`ui_manager._judge_group_speaker`(650)、`ui_manager.ChatWorker._strip_role_prefix`(556) /
`_strip_group_prefix`(573)。其中 `_strip_group_prefix` 被**三处**共用：收尾归一化、
流式显示层剥前缀、主动问候；改它等于同时改三处。

**验证思路**（本册无测试脚本，见 `main.md` §0 注意）：造一条
`[Twilight Sparkle]: …`（成员表为下划线名、上一发言者设为 `Applejack`），
断言 ① `resolve_speaker` 返回 `Twilight_Sparkle`（不是 Applejack）、
② 剥离后正文不含 `Twilight` 且以原文正文开头；
另需反向断言 `[1]:`（有序列表）、`[笑]:`（单字标签）、`注意：`（普通冒号句）**不**被误剥。

## A.2 `character_card.py`（238 行）——角色卡打包

**单例**：`CharacterCard.instance()`(50)。zip 结构顶层为 `role/`、`portrait/`、`desktop/` + `manifest.json`。

| 符号 | 行号 | 作用 |
|---|---|---|
| `export_role_zip(role, include_assets, target_dir, exact_path)` | 70 | 打包（zip 内部布局 104-129） |
| `preview_zip(zip_path)` | 139 | 读 manifest 预览 `{valid, role, display_name, ...}` |
| `import_zip(zip_path)` | 171 | 解包落地；**同名角色整体冲突则不写任何文件**（208-209）；`..` 路径跳过（204） |

**配置**：`character_card.export_dir`(默认 `./data/character_cards`)、`character_card.max_upload_mb(80)`。

---

# B. 角色扮演子项目 `roleplay/`

```
roleplay/
├─ __init__.py(3)
├─ roleplaytool.py(589)       设定卡 CRUD + build_context + 管理窗口
├─ roleplay_preset.py(426)    预设模型（采样/装配/隔离/规则/正则/世界书）
├─ roleplay_engine.py(489)    装配引擎（提示词总装 + 正则双通道）
├─ roleplay_preset_ui.py(1290) 6 页酒馆式预设管理器
├─ roleplay_import.py(526)    酒馆预设/世界书导入
├─ rolemanger/style/          对话风格卡（单选）
├─ rolemanger/world/          世界观卡
├─ rolepersonal/              个人设定卡（用户身份/人物关系）
└─ roletools/                 角色工具卡（破限/格式/功能开关）
```

## B.1 `roleplaytool.py`（589 行）——设定卡管理

`RolePlayManager`(65)（**可传 `root` 做测试隔离**）：`category_dir`(75)（`style/world` → `rolemanger/`，`rolepersonal/roletools` → 顶层同名目录）、`categories`(91)、`list_cards`(100)、`load_card`(115)、`save_card`(126，空名/非 dict 抛 `ValueError`)、`delete_card`(140)、`rename_card`(148)、`card_to_text`(162)、`text_to_card`(167)、**`build_context(card_keys)`(179)**（按传入顺序拼 `——【{label}卡：{title}】——` + 整份 JSON 文本）、`new_card_template`(211)、`split_key/join_key`(218/227)。
`RolePlayToolWindow`(309) 为管理窗口；`main(argv)`(567) 无参开 GUI、带参走 CLI。

**加类别**：`category_dir`(75-89) + `categories`(91) + `CATEGORY_LABELS`(44-49)。

## B.2 `roleplay_preset.py`（426 行）——预设模型

- 路径：`data/roleplay_preset.json`（`DEFAULT_PRESET_PATH` 53）；`DEFAULT_PRESET`(116-151) 与文件深合并，旧配置可升级。
- `SAMPLING_SPEC`(61-98)：12 项采样参数规格（UI 自动生成控件）；`_OPENAI_NATIVE`(101-104) 直传字段。
- `RolePlayPreset.instance()`(207) / `reset_instance()`(213)；`load`(222)、`save`(279)、`data`(296)、`has_own`(300，区分「显式清空」与「从未配置」)、`get/set`(314/326)、`_merge`(340)、`sampling_overrides`(352)、`new_rule/new_regex/new_lore`(382/394/409)。
- 迁移：`_migrate_categories`(168)、`_migrate_legacy_cards`(238，一次性把 `config.ui.roleplay_cards` 迁进预设并打标记 `meta.legacy_cards_migrated`)。

## B.3 `roleplay_engine.py`（489 行）——装配引擎（**核心**）

| 符号 | 行号 | 作用 |
|---|---|---|
| `_parse_regex` / `_to_python_repl` | 48 / 72 | `/pat/flags` 解析 / 酒馆替换串（`$1`、`{{$1}}`、`$<name>`）转 Python |
| `RolePlayEngine` / `preset` / `reload` | 86 / 93 / 97 | 引擎（无 GUI 依赖） |
| `expand_macros` | 101 | `{{user}} {{char}} {{random::}} {{//注释}}` 等 |
| `apply_variables` | 141 | `setvar/addvar/getvar`（支持嵌套 4 层） |
| `card_context(cards=None)` | 176 | 按 `assembly.inject_order` 拼设定卡；**显式传 cards 时严格只加载传入卡片**；style 强制单选；主角设定卡从 rolepersonal 剔除 |
| `user_persona` / `persona_context` | 249 / 254 | 主角设定卡名 / 正文 |
| `OOC_NOTICE` / `ooc_notice` | 273 / 288 | OOC 破限指令常量 / 开关（`assembly.ooc_mode`） |
| `_enabled_rules` / `render_rules` | 295 / 301 | 启用规则（按 `order`）/ 渲染（`setvar` 只写变量不输出） |
| `lorebook_context(scan_text)` | 322 | 世界书命中（主/次触发词、`constant`、`position`、`order`、`probability`） |
| `_scripts` / `_depth_ok` / `apply_regex` | 366 / 380 / 396 | 正则通道（placement：1 用户出向 / 2 AI 模型层 / 2 展示层）与深度限制 |
| `outgoing` / `incoming_model` / `incoming_view` | 428 / 432 / 436 | 三个正则入口 |
| `build_context(*, user, char, group, members, scan_text, cards, skeleton)` | 445 | **总装**（见下） |

### `build_context` 装配顺序（`roleplay_engine.py` 445-488）

```
1 主角设定 persona        → 【主角设定（{{user}} 本人）】      460-462
2 设定卡 cards            → 【个人设定】/【世界观】/【对话风格】/【角色工具】  463-465
3 OOC / 破限              → （ooc_mode 开时）                  467-469
4 世界书 lorebook         → 【世界书】                         470-472
5 规则 rules              → 【规则】                           473-475
6 输出骨架 skeleton       → 【输出要求】                       477-480
   ↓ join("\n\n")
7 expand_macros(...)  →  apply_variables(...)  →  压缩空行    485-488
```
**注意**：OOC 段放在世界书**之前**（有意为之：先声明「可参考但不严守」再给背景）；宏与变量在全块拼完后统一处理，因此规则里的 `setvar` 可被骨架里的 `getvar` 消费。

**注入位置**（`ui_manager.ChatWorker.run`）：`system[1]` = `【角色扮演设定】` + `rp_ctx`（795-807）。

## B.4 `roleplay_preset_ui.py`（1290 行）——预设管理器

6 页：采样参数(285) / 设定卡(363) / 规则卡(481) / 正则脚本(647) / 世界书(802) / 装配与隔离(946)；`_collect`(1079) 汇总 → `_on_save`(1127) 保存并同步 `ui.roleplay_cards`；`_on_import`(1240)、`_on_import_cards`(1184)、`_on_help`(1146)、`_on_reset`(1280)。
**加页**：`__init__`(108-119) + 新的 `_build_*_tab` + `_collect`(1079-1125)。
**坑**：主窗口无边框/半透明会被子对话框继承导致不可见 → `__init__` 92-98 显式关闭；`_collect` 再次把风格卡截为 1 张(1103-1104)。

## B.5 `roleplay_import.py`（526 行）——酒馆导入

| 符号 | 行号 | 作用 |
|---|---|---|
| `detect_kind` / `scan_directory` | 79 / 95 | 判别 preset/lorebook / 扫描目录 |
| `parse_preset(data, bundle, skip_tool_dump)` | 136 | 预设 → `{rules, regex, sampling, skipped}`（同 identifier 只取第一条） |
| `parse_lorebook(data, bundle)` | 236 | 世界书 → lorebook 条目（ST 位置 `0/1/4` 压成 `0/1/2`） |
| `classify_card` / `build_card` | 293 / 309 | 自动归类 / 整文件 → 一张设定卡 |
| `import_as_cards` / `import_result` / `import_directory` | 358 / 421 / 457 | 按类别导入为卡片 / 合并进预设（按 bundle 幂等）/ 全流程 |
| `main(argv)` | 484 | `python -m roleplay.roleplay_import <目录>` |

**坑**：默认 `enable_imported=False`（导入的规则/正则一律**停用**，避免多预设打架）；同名 bundle 的旧规则/正则/世界书会被清掉防重复注入。

## B.6 卡片 JSON 字段约定

| 类别 | 目录 | 关键字段 |
|---|---|---|
| 风格/世界观（手写） | `rolemanger/style`、`rolemanger/world` | `project_name, version, category, description, rules{}, few_shot_example[]` |
| 世界观（导入） | `rolemanger/world` | 追加 `source_file, usage, entries[]`（`keys/secondary_keys/content/constant/selective/order/position/depth/probability`） |
| 工具（导入） | `roletools` | `source_file, usage, sampling, prompts[], regex_scripts[]` |
| 个人设定/关系 | `rolepersonal` | `characters{}`（注意 `user.json` 是角色卡式结构，字段不同） |

## B.7 隔离（isolation）

- 配置层：`DEFAULT_PRESET["isolation"]`（`roleplay_preset.py` 141-146）+ 预设 UI 页6（1003-1019）+ 设置面板 8761-8779。
- 运行层：`ui_manager._isolation`(642-651)、`_roleplay_per_session`(3854)、`_roleplay_scope`(3863)、`_session_matches_mode`(3881)。
- 存储层：见 `04-memory.md` 第 3 节。

---

## C. 修改指南 & 验证

| 想改 | 落点 |
|---|---|
| 侧栏名字/缩写 | `role_manager.sidebar_label`(145) + `truncate_wide`(127) |
| 群聊发言格式兼容 | `_SPEAKER_PATTERNS`(34-43) + `match_member`(437) + `parse_speaker`(483) |
| 群聊名字写法不一致（下划线/空格/别名）→ 错位/前缀残留 | `normalize_role_name`(76) + `match_member`(437)（见 A.1 坑④） |
| 群聊只允许**一位**成员回复（模型一次写多人） | `ui_manager.ChatWorker._first_speaker_only`(602)（截断规则见 `02-ui-chat.md`） |
| 情感识别 | `detect_emotion`(231) |
| 立绘/动图查找 | `portrait_path`(407) / `pet_gif_path`(440) |
| 角色卡打包内容 | `export_role_zip`(70) / `import_zip`(171) |
| 设定卡拼接格式 | `roleplaytool.build_context`(179) |
| 装配顺序/分节标题 | `roleplay_engine.build_context`(445) + `card_context`(176) |
| 宏/变量 | `expand_macros`(101) / `apply_variables`(141) |
| 世界书触发 | `lorebook_context`(322) |
| 正则通道/深度 | `apply_regex`(396) / `_scripts`(366) / `_depth_ok`(380) |
| 采样参数 | `roleplay_preset.SAMPLING_SPEC`(61) |
| 预设 UI 页面 | `roleplay_preset_ui._build_*_tab` |
| 酒馆导入映射 | `roleplay_import.parse_preset`(136) / `parse_lorebook`(236) |

