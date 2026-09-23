# 06 · 技能体系（本地技能 `\@技能` + 公共技能库 skillspub）

> 加技能、改技能菜单/注入、改技能包导入打包、改技能产出目录时读这本。
> 行号基线：2026-09-19。

---

## 0. 两套体系对照

| | 本地技能（工具库） | 公共技能库 skillspub |
|---|---|---|
| 唤醒 | `\@触发词`（弹窗输入） | `\@skillspub`（手动全量）/ `\@autoskills`（自动挑选）/ `#技能名`（点名） |
| 数据 | `skills/tools_list.json`（目录）+ `skills/skilltools_information.json`（详情），**无子目录** | `skillspub/catalog.json`（索引）+ 每技能一个文件夹 `skillspub/<folder>/SKILL.md` |
| 维护 | `skilltools.py` 技能工具管理器 | `skillspub_manager.py` skill 库管理器 |
| 上下文 | `SkillManager.build_context` | `skillspub_core.context_for_prompt / context_for_direct` |
| 现状 | 26 个（4 个 `switch`：thinking/network/autoskills/skillspub；22 个普通） | **28 个**：用户导入的 26 个 + 内置 `tothemoon`（查询可观测的天象：定位 → 观测条件 / 天象 / 专业天象三档）+ 内置 `tothestars`（巡天与天文科研：瞬变天体 TNS/VSX/ZTF/NOVA + CDS 星图、VSX 变星、FTS 解析、SOHO/CCOR 同星判断）。两者均在 2026-09-20「测试误清空 skillspub」事故中丢失后按文档重建 |

**两个 `switch` 的 2026-09-21 行为修正**：

| 技能 | 行为 |
|---|---|
| `\@thinking` | 本轮按思维链协议输出，**并强制显示思维链折叠块**（`ui.show_thinking` 关闭时也显示，`MainWindow._thinking_skill` / 存档字段 `thinking_skill`）；正文未开始时（模型先吐 `reasoning_content`）也会先把折叠块铺出来 |
| `\@network` | 消息里**带网址 → 先 `web_fetch` 真开页面**（最多 3 个），去掉网址后的文字再走 `web_search`；纯网址消息不搜索。详见 `03-llm-api.md` 1.5 |

**程序侧 skill库 技能（`2026-09-21`）**：

| 技能 | 行为 |
|---|---|
| `#tothemoon` | 属 `ui_manager.PROGRAM_PUB_SKILLS` —— **先定位（默认本机 IP）**，再按关键词分流：`查询观测条件` → 程序直接出数据卡片（日月出没/上中天/亮度 + 7timer）；`查询天象` / `查询专业天象`（含彗星、流星雨、月食…）→ 交给模型，但本机 IP 归属地已注入 system 上下文，模型不再反问城市。需求来源「查询天象不该输出一个查询地址，而应直接查本地 IP 的天象」。详见 `07-feature-modules.md` 8.2 |
| `#tothestars` | 程序先取数（`transient_service`：Rochester 新星表 ±10″ 比对、**VSX 10 角分检索＝VizieR + 官网双源互验**、生成 TNS/ALeRCE/CDS 链接）→ 注入上下文 → 模型解读；TNS/ALeRCE 仍**只给链接**（反机器人）。详见 `07-feature-modules.md` 8.3 |

---

# A. 本地技能

## A.1 `skill_manager.py`（212 行）

`SkillManager.instance()`(42)；`reload(force)`(49) 读两个 JSON（缺失/损坏降级为空）；`skills()`(87) 合并详情字段并按 `kind=="switch"` 优先排序；`match(prefix)`(109) 前缀过滤（无命中退化子串）；`resolve(token)`(132) 触发词 > 名称 > 别名；`parse_tags(raw)`(150) 从文本抽 `\@技能`（**从最长到最短逐级截断**取能解析的最长前缀）；`build_context(skill)`(177) 生成 `[工具开启]…` / `[技能调用]…`。

- `_TAG_TOKEN`(27)：触发词合法字符，必须与 `skilltools._TRIGGER_RE`、`skillspub_core._PUB_TOKEN_RE` 一致。
- 配置：`paths.skills`（默认 `./skills`）。

## A.2 `skill_popup.py`（210 行）

`SkillPopup`(70)：无边框圆角弹窗；`set_skills`(102)（switch 前加 `[开关]`，最多 8 行）、
`_resize_to_content`(120)、`show_at`(157)、`move_selection`(185)、`selected_skill`(192)、`current_count`(200)。
焦点策略 `NoFocus`，键盘（回车/上下/Esc）由 `ui_manager.eventFilter`(4201) 处理。

**尺寸自适应（`_resize_to_content`，改这里必读）**——修过三个「@ 补全列表错乱」老毛病：

1. 行高写死 36px ≠ 真实渲染行高（约 31px，随 DPI 变）→ 底部空白/末行被裁；
   现以 `sizeHintForRow(0)` 实测为准。
2. 宽度交给 `adjustSize()`，与内容无关（内容 488px 弹窗只有 274px）→ 文字截断；
   现用**字体度量**现算内容宽（`_labels` + `horizontalAdvance`），夹在 260–520px 之间，
   超宽才出横向滚动。**不要用 `sizeHintForColumn`**：内容远超视口时它只量可见行，返回假宽度。
3. 半透明无边框窗口**边显示边改大小会留残影** → 行看着重叠、点的行和看到的不是同一行
   （每次按键过滤都会 resize）。改大小前先 `hide()` 一拍再 `show()`（同一事件循环内，无闪烁）。

## A.3 `skilltools.py`（1518 行）——技能工具管理器 + 数据层

**常量/路径**：颜色常量(71-81)、`CATALOG_FILE`(89)、`INFO_FILE`(90)、`_skills_dir`(92)、`SKILLS_DIR`(100，**测试 monkeypatch 它**)、`_TRIGGER_RE`(103)、`_INVALID_FS_CHARS`(105)、`FIELDS`(108)、`_DEFAULT_CATEGORY(工具)`(118)。

**数据层**：`default_skill_data`(364)、`_read_catalog_raw`(385)、`_read_info_raw`(397)、`list_skills`(408)、`read_skill`(418)、`write_json_atomic`(450)、`_parse_aliases`(459)、**`validate_skill`(469)**、`_catalog_entry`(528)、`_info_entry`(540)、**`save_skill`(563)**（新建/改名，成功后重建两个 JSON）、`delete_skill`(594)、`rebuild_tools_list`(611)。

**GUI**：`SkillManagerWindow`(635)（`_build_left_panel` 775 / `_build_right_panel` 815 / `_refresh_tree` 971 / `_load_skill` 1029 / `_collect_data` 1089 / `_on_save_skill` 1109 / `_on_new_skill` 1126 / `_on_delete_skill` 1166 / `_edit_field_dialog` 1214 / `_edit_parameters_dialog` 1282），入口 `main()`(1508)。

**技能字段**

| 字段 | 位置 | 说明 |
|---|---|---|
| `name` / `trigger` / `kind` / `aliases` / `category` / `enabled` | 目录 JSON | `kind`：`skill` 普通标签 / `switch` 开关工具（同时最多开 2 个） |
| `description` / `prompt_template` / `parameters` | 详情 JSON | `parameters:[{name, default, required}]`，模板用 `{参数名}` |
| `image_gen` | 详情 | true → 直接走生图 API |
| `network_enabled` | 详情 | true → 可调搜索 API |
| `api_base/api_key/api_model` | 详情 | 独立接口（留空复用主 API） |
| `permission` | 详情 | `"review"` → 发送前需用户确认（写文件/改文件） |

**坑**：新增字段要同时改 `_catalog_entry`(528) 与 `_info_entry`(540)，否则写入丢字段；手改 JSON 后建议跑 `rebuild_tools_list()`(611)。

## A.4 其余本地技能扩展模块

| 文件 | 行数 | 作用 | 单例/入口 |
|---|---|---|---|
| `skill_import.py` | 461 | 外部技能包（`.zip/.rar/.md`）→ skillspub 文件夹 + catalog（见 C.1） | `SkillImporter.instance()`(202) |
| `skill_install.py` | 163 | 安装前安全审查（本地规则 + 小模型）→ `low/medium/high` | `SkillInstaller.instance()`(51)；`preview_and_review`(108)、`safety_review`(91)、`install_zip`(160) |
| `skill_bundles.py` | 314 | 技能包（一组本地技能）启停 / 打包 zip / 导入 | `SkillBundles.instance()`(43)；`export_bundle_zip`(205)、`import_bundle_zip`(259)、`set_bundle_enabled`(186) |
| `skill_eval.py` | 180 | 技能评测：解析层（必测）+ 回复层（可选，小模型） | `SkillEval.instance()`(43)；`run_skill_eval`(112) |
| `skill_userdata.py` | 212 | 技能产出目录 `skilluserdata/<日期-时间>/` + `\@project` 改名 | `SkillUserData.instance()`(58)；`folder_name`(119)、`project_dir`(146)、`rename`(160)、`resolve`(190) |

---

# B. 公共技能库 skillspub

## B.1 `skillspub_core.py`（869 行）——数据层 + 上下文构造（纯逻辑，无 Qt）

**常量**：`CATALOG_NAME(41)`、`SKILL_FILE(42)`、`SUMMARY_LIMIT=200(46)`、`MANUAL_TRIGGERS={skillspub,技能广场}(51)`、`AUTO_TRIGGERS={autoskills,自动技能}(52)`、`SEED_SKILLS(55-95)`、`_PUB_TOKEN_RE(525)`。

**catalog 条目字段**：`name`（唯一，AI 按名调用）、`folder`、`summary`（≤200，**非空是硬校验**）、`category`、`enabled`、`keywords`、`allow_files`、`created_at/updated_at`。**正文不放 catalog，只在 `skillspub/<folder>/SKILL.md`。**

| 组 | 函数（行号） |
|---|---|
| 路径/读写 | `skillspub_dir`(105)、`catalog_path`(116)、`folder_for_name`(125)、`skill_folder_path`(133)、`description_path`(138)、`_write_skill_file`(143)、`_normalize`(167)、`_read_raw`(196)、`_load_description`(211)、`_index_entry`(223)、`_read_catalog`(229)、`_write_catalog`(241)、`ensure_dir`(259，含旧版内联 description → SKILL.md 的一次性迁移 281-302) |
| 查询/编辑 | `list_skills`(307)、`read_skill`(322)、`count_skills`(329)、`skill_path`(333)、`validate`(342)、`save_skill`(362，改名时整体移动文件夹 384-395)、`delete_skill`(404)、`workdir_lines`(420) |
| 指引/详情 | `guide_text`(438)、`detail_text`(455)、`mode_from_tags`(477)、`match_score`(499)、`fallback_select`(512)、`select_skills_via_llm`(724) |
| `#技能名` 解析 | `resolve_skill_token`(528)、`resolve_longest_token`(574)、`pub_refs_in_text`(597)、`parse_pub_tags`(629)、`strip_pub_tags`(644)、`match_prefix`(664) |
| 上下文装配 | `context_for_prompt(prompt, mode, pool, workdir)`(763)（skillspub 全量 / autoskills 自动挑选）、`context_for_direct(names, entries, workdir)`(821)（`#技能名` 点名，只注入被点名技能） |

**配置**：`paths.skillspub`、`ui.skillspub_enabled(True)`。

## B.2 `skillspub_manager.py`（993 行）——skill 库管理器

`SkillPubManagerWindow`(60)：`_build_left_panel`(204)（新建(242)/删除/**导入 skills(249)**）、`_build_right_panel`(254)（含「总结功能」319 /「翻译总结」326 一键小模型生成简介）、`_refresh_tree`(381)、`_load_skill`(435)、`_collect_data`(475)、`_on_save_skill`(485)、`_on_new_skill`(501)、`_on_delete_skill`(531)、`_on_summarize_skill`(555)、**`_on_import_skills`(650)**、`_import_preview_task`(674)、`_on_import_previewed`(679)、`_import_task`(715)、`_on_import_done`(720)、`closeEvent`(854)、`main()`(983)。

## B.3 `skillspub/` 目录约定

```
skillspub/
├─ catalog.json          唯一索引（不含技能正文）
├─ <技能名>/SKILL.md     技能正文（原样注入 LLM；可带 Anthropic 风格 YAML frontmatter）
├─ <技能名>/references/*.md、scripts/、assets/   技能自带附属文件（detail_text 会给出文件夹路径提示）
```

**新增一个 skillspub 技能（手写 3 步）**
1. 建 `skillspub/<技能名>/` 并写 `SKILL.md`（技能用途 + 执行要求 + 输出格式）。
2. 在 `catalog.json` 的 `skills[]` 追加：`{name, folder, category, enabled, summary(≤200 非空), keywords, allow_files, created_at, updated_at}`。
3. 直接用 `\@skillspub` / `\@autoskills` / `#技能名` 调用（`list_skills` 每次实时读盘，无需重启）。

> 也可用「skill 库管理器 → ＋新建技能」或「导入 skills(.zip/.rar/.md)」完成，见 C.1。

---

## C. 技能包链路

### C.1 外部技能包（.zip/.rar/.md）→ skillspub

```
[按钮] skillspub_manager.py:242  "导入 skills（.zip / .rar / .md）"
 → _on_import_skills(650) → QFileDialog(661) → spawn_worker(_import_preview_task)(669)
 → SkillImporter.preview(path)(skill_import.py:262)
      _unpack(208) → zip: _safe_extract_zip(143，拒路径穿越) / rar: _extract_rar(153，rarfile→7z/WinRAR/tar→tar)
      _skill_root(233) → _find_skill_md(247) → parse_frontmatter(56) → needs_file_creation(101)
 → _on_import_previewed(679) → 询问「是否允许生成文件？」(styled_question 700)
 → spawn_worker(_import_task)(709) → SkillImporter.import_paths(355)
      pub.ensure_dir(259) → 同名自动加序号(-2/-3, 396-400) → 复制文件树(405-412)
      → 无 SKILL.md 时把正文写成 SKILL.md(414) → pub.save_skill(362)
 → _on_import_done(720)：刷新树 + 选中新技能
```

### C.2 本地技能包（bundle）打包 / 导入

| 操作 | 入口 | 落点 |
|---|---|---|
| 打包导出 | `ui_manager` 技能包管理器「打包 zip」(9596) → `_export`(9535) | `SkillBundles.export_bundle_zip`(205)：`bundle.json` + `skills/<name>/skill.json(+detail.json)` |
| 导入（并入技能库，同名跳过） | 「导入 zip」(9599) → `_import`(9557) | `import_bundle_zip`(259) → `_write_skills`(109)/`_write_info`(118) |
| 带安全审查安装 | 设置面板「安装技能包 zip…」(8812) → `_install_skill_zip`(9199) | `SkillInstaller.preview_and_review`(108) → 风险 high 需确认 → `install_zip`(160) |

**落盘**：技能库 `skills/tools_list.json` + `skills/skilltools_information.json`；skillspub `skillspub/<folder>/SKILL.md` + `catalog.json`；bundle 存储 `data/skill_bundles.json`；技能产出 `skilluserdata/<日期-时间>/`。

### C.3 技能产出目录（`\@project`）

`SkillUserData.instance()`：一个会话一个「日期+时间」文件夹（`folder_name` 119，冲突自动加序号）；`resolve(uid, override)`(190) 优先「工作文件夹」再项目文件夹；映射存 `skilluserdata/projects.json`。主界面侧：`_ensure_project_dir`(6844)、`_handle_project_command`(6875)、`_clear_workdir`(6900)。

---

## D. 主界面侧交互（细节见 `02-ui-chat.md` 4.5）

| 功能 | 符号 |
|---|---|
| `\@` 弹窗 | `_refresh_skill_popup`(4439)、`_on_skill_selected`(4466)、`_insert_skill_tag`(4491)、`_skill_tag_backspace`(4509) |
| 技能解析/注入 | `_parse_skill_tags`(4533)、`_expand_skill_prompt`(4537)、`_build_skill_context`(4545)、`_process_skill_commands`(4566) |
| `#技能名` | `_refresh_pub_popup`(4653)、`_on_pub_selected`(4686)、`_parse_pub_command`(4758)、`_strip_pub_display`(4767) |
| 触发语境 | `_skill_should_trigger`(4623，看前一位是否为 `\`)、`_pub_should_trigger`(4838，看前一位是否行首/空白/`_PUB_PREV_OK` 标点，**`/`、`\` 也算** → `/#`、`\#` 都能唤出菜单) |
| 发送时分派 | `_on_send`(4777：review 授权 4808、生图 4824、文件 4830、程序侧技能 4841) |
| 键盘 | `eventFilter`(4201) |

> **坑（2026-09-21）**：`_pub_should_trigger` 在 `#` 的 KeyPress 里被调用，**此时 `#` 还没进文档**；
> 旧实现断言 `characterAt(pos-1) == '#'` → 真实键盘永远不弹菜单。修法／测试写法的坑详见 `02-ui-chat.md` 4.5。

---

## E. 修改指南 & 验证

| 想改 | 落点 |
|---|---|
| 加/改本地技能 | `skills/tools_list.json` + `skilltools_information.json`（或管理器）；字段见 A.3 |
| 改技能注入文案 | `SkillManager.build_context`(177) |
| 加程序侧技能（直接执行） | `_on_send` 分派(4841-4844) + `_handle_program_skill`(7183) + 新 `_run_*` |
| 改 `#技能名` 解析规则 | `skillspub_core.resolve_skill_token`(528) / `resolve_longest_token`(574) |
| 改 skillspub 上下文 | `context_for_prompt`(763) / `context_for_direct`(821) |
| 改导入行为 | `skill_import.preview`(262) / `import_paths`(355) |
| 改安全审查 | `skill_install._default_reviewer`(61) / `safety_review`(91) |


> 注意：`data/roleplay_preset.json` 里 `cards.roletools` 出现过 `nextstep`，但真实目录没有 `nextstep.json` —— `build_context` 会**静默跳过缺失卡片**，属正常行为。
