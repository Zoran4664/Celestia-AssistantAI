# toolsset.md — 技能工具管理器（skilltools.py）完整设计文档

> 本文档说明 `skilltools.py` 的创作思路、文件结构、每个文件的含义与使用示例。
> 技能工具管理器采用 **Windows 注册表风格** + 主程序同款 **HTML5 响应式界面**，
> 数据组织方式与主程序的 \\@技能 唤醒体系完全一致：`skills/tools_list.json`（技能目录）
> + `skills/skilltools_information.json`（技能详情），一个技能 = 目录条目 + 详情条目。

---

## 一、创作思路

### 1.1 设计目标
在主程序 `Celestia AssistantAI` 之外，提供一个**可视化技能工具库管理页面**。用户可以在图形界面中
新建、查看、修改、删除「AI 技能工具」。每个技能本质是一组「触发词 + 提示词 + 模型参数」，
保存后主程序的 **\\@技能 唤醒** 功能立即生效（在聊天框输入 `\\@触发词` 即可唤起对应技能）。

> **调用方式（v2 变更）**：在聊天输入框输入 `\\@`（先输入反斜杠 `\\` 再输入 `@`）即弹出
> 技能/工具选择菜单，选中后插入 `\\@触发词` 标签；发送时自动解析并注入技能上下文。
> 开关型工具（`\\@thinking` 思维链 / `\\@network` 联网搜索）可同时开启 0-2 个，并可与场景标签组合。

### 1.2 风格继承（与主页面完全一致）
`skilltools.py` 复刻了主程序 `ui_manager.py` 的整套视觉语言：

| 项目 | 取值 |
|------|------|
| 主色（Accent） | `#6c8ef5`，悬停 `#8fb0ff`，按下 `#4a6fd4` |
| 文字色 | 深 `#2b2b33` / 中 `#6b7280` / 浅 `#9aa0ac` |
| 卡片底色 | `rgba(255,255,255,0.92)`，描边 `#e5e7f0`，圆角 14 |
| 窗口 | 无边框圆角半透明（244,245,250,210 @16px） |
| 标题栏 | 自定义：拖动 / 最小化 ─ / 最大化 □ / 关闭 × |
| 按钮 | 渐变主按钮（保存修改）、幽灵按钮（新建/取消）、危险按钮（删除） |
| 输入框 | 白底圆角，focus 时主色描边 |
| 滚动条 | 细圆角主色调滚动条 |
| 字体 | `Microsoft YaHei UI / Microsoft YaHei` |

### 1.3 注册表风格布局
界面仿照 Windows `regedit`：

```
┌──────────────────────────────────────────────────────────────┐
│  技能工具管理器   注册表风格技能库 · Celestia AssistantAI     ─  □  ×    │  ← 标题栏
├───────────────┬──────────────────────────────────────────────┤
│  ▎技能库      │ 翻译          [\@translate]     [保存修改]    │  ← 顶部
│  └─ 我的技能库 │ ┌─────────────────────────────────────────┐ │
│     ├─ 翻译   │ │ ▎基本属性（注册表值）                    │ │  ← 属性卡片
│     ├─ 写代码 │ │  名称            [翻译        ]          │ │
│     ├─ 记忆训练│ │  触发词          [\@translate  ]        │ │
│     │  ...    │ │  别名            [翻译,fanyi,译一下]     │ │
│     │         │ │  分类            [工具         ]         │ │
│   [＋新建技能]│ │  启用            [√ 启用]               │ │
│   [删除]      │ ├─────────────────────────────────────────┤ │
│               │ │ ▎技能说明（注入 LLM 的技能上下文）        │ │
│               │ │ ┌─────────────────────────────────────┐ │ │
│               │ │ │ 执行翻译任务……                       │ │ │
│               │ │ └─────────────────────────────────────┘ │ │
│               │ │ ▎执行要求（可含 {参数名} 占位符）        │ │
│               │ │ ▎参数（编辑参数…：名称/默认值/必填）     │ │
│               │ └─────────────────────────────────────────┘ │
│ 计算机\技能库\翻译   已就绪                         共 6 个   │  ← 状态栏
└──────────────────────────────────────────────────────────────┘
```

- **左侧（键树）**：`我的技能库` 根键 → 每个技能一个子键（文件夹图标），键名显示
  `技能名 [@触发词]`，悬停显示技能说明。
- **右侧（值属性面板）**：仿 regedit 值窗格，按分节卡片展示技能的 8 个字段；
  **双击「名称 / 触发词 / 别名 / 分类」**会弹出注册表式「编辑字符串」对话框。
- **底部状态栏**：仿注册表路径 `计算机\技能库\<技能名>` + 就绪/未保存状态 + 技能总数。

### 1.4 数据持久化设计
- 目录：`skills/`（与主程序 \\@技能 唤醒共用同一数据目录）；
- 目录文件：`skills/tools_list.json`（列表）——存放每个技能的
  `name / trigger / kind / aliases / category / enabled`，供主程序快捷检索；
- 详情文件：`skills/skilltools_information.json`（键名 = 技能名称）——存放每个技能的
  `description / prompt_template / parameters`；
- 保存/删除后**自动重建两个 JSON 文件**（去重 / 剔除孤儿详情 / 按名称排序 / 回填缺失字段）；
- 写入采用**原子替换**（先写 `.tmp` 再 `os.replace`），避免 JSON 写一半损坏；
- 全部 UTF-8 编码；字段缺失时读取自动回填默认值，保证结构完整。


---

## 二、文件结构

```
主文件与主应用\
├── skilltools.py                   # · 技能工具管理器 GUI（python skilltools.py 启动）
├── toolsset.md                     # · 本文档
├── skill_manager.py                #   主程序 \@技能 唤醒管理器（读取下方两个 JSON）
├── skill_popup.py                  #   主程序 \@技能 唤醒弹窗（纯白不透明底，防与聊天区文字叠加）
├── ui_manager.py                   #   主程序界面（\@技能 拦截分发、卡片气泡、设置页入口）
├── api_vault.py                    #   V3：常用接口类 API 管理器（GUI + 数据层）
├── weather_service.py              #   V3：\@weather 和风天气取数 + HTML5 卡片
├── novel_learn.py                  #   V3：\@novellearn 小说总结 + 存档 + 复刻上下文
├── calorie_tracker.py              #   V3：\@calorie 热量估算 + 记录 + 膳食建议
├── ponywebsite_checker.py          #   V3：\@ponywebsite 小马网站 ping / 丢包 / 延迟检测
├── skills\                         # · 技能数据目录（本程序与主程序共用）
│   ├── tools_list.json             #   技能目录（name/trigger/kind/aliases/category/enabled）
│   └── skilltools_information.json #   技能详情（description/prompt_template/parameters）
├── data\                           #   V3 新增数据
│   ├── api_vault.json              #   常用接口（名称/简介/地址/KEY/自定义/规则）
│   └── dailylifedata.json          #   饮食热量记录（records + daily 汇总）
└── novellearn\                     #   V3：小说总结存档 \<文件名\>_\<YYYYMMDDHHMM\>.json
```

> 说明：主项目 `tools/` 下的测试脚本已随发布清理移除（现仅存素材生成脚本）；本程序的技能数据
> 统一存放于 `skills/` 目录，与主程序 \@技能 唤醒完全一致，保存后无需重启即可在聊天中
> `\@触发词` 唤起。

---

## 三、每个文件内容的含义

### 3.1 `skilltools.py`（技能工具管理器主程序）
| 代码段 | 作用 |
|--------|------|
| 风格常量（ACCENT/CARD_BG/BORDER…） | 与主程序完全一致的配色体系 |
| `btn_qss / ghost_btn_qss / danger_btn_qss / root_qss` | HTML5 响应式 QSS 生成器 |
| 数据层（`default_skill_data / list_skills / read_skill / save_skill / delete_skill / rebuild_tools_list / validate_skill`） | 技能库读写与两个 JSON 的自动维护 |
| `SkillManagerWindow` 类 | 注册表风格主窗口（键树 + 属性面板 + 双击编辑 + 参数表格 + 状态栏） |
| `_edit_field_dialog` | 仿 regedit「编辑字符串」对话框 |
| `_edit_parameters_dialog` | 参数表格编辑（参数名 / 默认值 / 必填，可增删行） |
| 无边框缩放（`_resize_event / _init_resize`） | 与主程序一致的四边/四角拖拽缩放 |
| `paintEvent` | 圆角半透明窗口背景 |

### 3.2 `skills/tools_list.json`（技能目录）
由程序在每次保存/删除后自动重建，供主程序 `SkillManager` 检索：

```json
{
  "updated": "2026-08-03 19:20:00",
  "count": 17,
  "skills": [
    { "name": "翻译", "trigger": "translate", "kind": "skill",
      "aliases": ["翻译", "fanyi", "译一下"], "category": "工具", "enabled": true },
    { "name": "思维链", "trigger": "thinking", "kind": "switch",
      "aliases": ["思维链", "推理", "思考"], "category": "开关", "enabled": true }
  ]
}
```

### 3.3 `skills/skilltools_information.json`（技能详情）
键名 = 技能名称，存放注入 LLM 的上下文：

```json
{
  "翻译": {
    "description": "执行翻译任务，将用户给出的内容翻译为目标语言。",
    "prompt_template": "用户调用了「翻译」技能。请把用户消息中待翻译的内容翻译成{target_lang}，只输出译文，不要额外解释。",
    "parameters": [ { "name": "target_lang", "default": "英文", "required": false } ]
  }
}
```

### 3.4 技能字段说明
| 字段 | 键名 | 必填 | 存放位置 | 说明 |
|------|------|------|----------|------|
| 名称 | `name` | 必需 | 目录 + 详情键名 | 技能名称（唯一） |
| 触发词 | `trigger` | 必需 | 目录 | 聊天中 `\@触发词` 唤醒，唯一，不含空格 |
| 类型 | `kind` | 可选 | 目录 | `skill`=普通技能/标签；`switch`=开关工具（可同时开启 0-2 个，如思维链/联网搜索） |
| 别名 | `aliases` | 可选 | 目录 | 逗号分隔的别名列表，可被 `\@` 解析 |
| 分类 | `category` | 可选 | 目录 | 如 工具 / 记忆 / 创作 / 开关 |
| 启用 | `enabled` | 可选 | 目录 | `false` 时主程序 \@唤醒 列表不显示 |
| 技能说明 | `description` | 可选 | 详情 | 注入 LLM 的技能上下文 |
| 执行要求 | `prompt_template` | 可选 | 详情 | 可含 `{参数名}` 占位符 |
| 参数 | `parameters` | 可选 | 详情 | `[{name, default, required}]`，对应占位符 |

### 3.5 内置工具一览（skills/ 默认数据，均可自定义）
| 名称 | 触发词 | 类型 | 说明 |
|------|--------|------|------|
| 思维链 | `thinking` | 开关 | 开启思维链（CoT）输出模式，逐步推理 |
| 联网搜索 | `network` | 开关 | 开启联网搜索模式，引用最新信息 |
| 科研 | `research` | 技能 | 文献检索、实验设计、数据分析与论文写作 |
| 代码 | `code` | 技能 | 生成、解释或修复代码（= 写代码） |
| 文案 | `words` | 技能 | 广告、营销、短视频脚本、品牌故事等文案 |
| 健康咨询 | `health` | 技能 | 健康、营养、运动科学建议（仅供参考） |
| 医学研究 | `med` | 技能 | 医学论文、临床研究、药物机制等学术问题 |
| 创意 | `create` | 技能 | 多学科融合创意创作（参考案例） |
| 数学 | `math` | 技能 | 数学求解、推导与数学建模 |
| 办公 | `work` | 技能 | 邮件、公文、报告、PPT、会议纪要、Excel 等 |
| 教育学习 | `education` | 技能 | 知识讲解、答疑、学习规划与因材施教 |
| 翻译 | `translate` | 技能 | 翻译任务 |
| 记忆训练 | `train` | 技能 | 对话内容提炼并归档记忆 |
| 生成图片 | `image` | 技能 | 根据描述生成图像提示词/画面构思 |
| 读取文件 | `read` | 技能 | 读取本机文件内容（程序侧执行，受路径权限保护） |
| 写入文件 | `write` | 技能 | 写入本机文件（写前备份快照，可回滚） |
| 修改文件 | `edit` | 技能 | 替换文件中的文本（写前备份快照，可回滚） |
| 小说学习 | `novellearn` | 技能 | 总结上传的小说并存档 JSON（详见 §6.3） |
| 天气查询 | `weather` | 技能 | 和风天气查询 + HTML5 天气卡片（详见 §6.4） |
| 饮食热量 | `calorie` | 技能 | 热量估算 + 记录 + 膳食建议（详见 §6.5） |
| 小马网站检测 | `ponywebsite` | 技能 | ping 站点清单 + 丢包/延迟卡片（详见 §6.7） |

> 组合示例：`\@thinking \@network \@research 帮我查一下最新的量子计算论文` ——
> 思维链 + 联网搜索两个开关同时开启，并智能结合「科研」场景标签。

---

## 四、使用示例

### 4.1 查看一个技能
启动 `python skilltools.py` → 左侧点击 `翻译` 键 → 右侧显示该技能全部字段
（名称、触发词、别名、分类、启用、技能说明、执行要求、参数）。

> **设置面板入口（v4）**：主程序设置页「技能工具」分节可一键打开本管理器
> （`MainWindow._open_skilltools`，同进程复用窗口实例）。管理器主题色读取
> `ConfigLoader` 配置并与主界面实时同步：主界面切换软件颜色时，
> 本管理器立即跟随（`apply_accent`）。所有界面样式（按钮 / 输入框 / 下拉框 /
> 滚动条 / 列表 / 开关等）与主界面 root_qss 完全一致。

### 4.2 新建一个技能
1. 点击 `＋ 新建技能`，输入名称（如 `时间`）；
2. 程序自动生成默认触发词 `\@时间`、分类 `工具`、启用；
3. 填写触发词 / 别名 / 技能说明 / 执行要求，可点 `编辑参数…` 添加参数；
4. 点击 `保存修改`，程序写入 `skills/tools_list.json` 与
   `skills/skilltools_information.json` 并自动重建索引。

### 4.3 修改 / 重命名一个技能
在右侧直接编辑任意字段，或**双击字段**弹出注册表式编辑框；修改名称后保存，
详情文件的键名会自动迁移（旧键删除）。

### 4.4 删除一个技能
选中左侧键 → 点击 `删除` → 二次确认后从两个 JSON 中移除并同步重建索引。

### 4.5 命令行验证（无界面）
```bash
python -c "import skilltools; skilltools.rebuild_tools_list(); print(skilltools.list_skills())"
```

---

## 五、验证记录（VERIFICATION）

- 语法编译：`python -m py_compile skilltools.py` 通过（零警告）；
- 数据层（离屏实测）：读写/校验（含 `kind` 类型）、新建/重命名/删除、开关型保存与
  kind 保留、索引重建、GUI 窗口构建/键树/载入/保存 —— 全部通过；
- 与主程序联动（离屏实测）：`\@技能` 唤醒 + 开关组合 + 气泡剥离 + 附件技能上下文 ——
  全部通过；
- 真实 GUI 启动实测：技能工具管理器窗口正常显示并进入事件循环（退出码 0），
  类型下拉与开关型联动正确；
- 主程序冷启动实测：界面正常渲染、无异常退出；
- 需求 3：技能选择弹窗改为纯白不透明底（`skill_popup.py`），不再与上方聊天显示框文字叠加；
  输入框内技能标签保留蓝底。
- 数据完整性：保存/删除前后 `skills/` 两个 JSON 文件始终一致，主程序
  `SkillManager` 可立即读取生效。

---

## 六、V3 扩展：程序侧技能工具 + 常用接口类 API 管理器

> 前面的工具全部属于**提示词型技能**（把技能说明注入 LLM，由模型回答）。V3 新增一类
> **程序侧技能**：技能被唤醒后由**程序自己执行**，不进入普通对话，结果以卡片气泡
> 直接插入对话框。它们同样登记在 `skills/` 的两个 JSON 里，可在技能工具管理器中
> 查看 / 改名 / 停用 / 删除，与普通技能完全一致。

### 6.1 新增文件一览
| 文件 | 作用 |
|------|------|
| `api_vault.py` | 常用接口类 API 管理器（GUI + 数据层，存储型） |
| `weather_service.py` | 和风天气取数 + HTML5 天气卡片渲染 |
| `novel_learn.py` | 小说总结、JSON 存档、角色扮演复刻上下文生成 |
| `calorie_tracker.py` | 热量估算、`data/dailylifedata.json` 记录、膳食建议 |
| `ponywebsite_checker.py` | ping 检测、`data/ponywebsite.txt` 站点清单、丢包/延迟 HTML 卡片 |
| `data/ponywebsite.txt` | 待检测站点清单（每行「名称<Tab>网址」，# 开头为注释） |
| `data/api_vault.json` | API 管理器数据（名称/简介/地址/KEY/自定义内容/规则） |
| `data/dailylifedata.json` | 饮食热量记录（明细 records + 按日汇总 daily） |
| `novellearn/*.json` | 小说总结存档，命名 `<文件名>_<YYYYMMDDHHMM>.json` |

### 6.2 挂载方式（`ui_manager.py`）
1. 发送时在 `_on_send` 内、消息进入普通对话之前拦截：`_handle_program_skill()`
   按 `trigger` 分发（`novellearn` / `weather` / `calorie` / `ponywebsite`），
   与 `\@read` / `\@write` 同级，均属于「即时处理、不走模型」的技能；
2. 后台任务统一走 `utils.async_worker.spawn_worker`（`_spawn_tool_worker`），
   避免长耗时任务卡住界面；
3. 结果由 `_append_tool_card()` 以**角色气泡卡片**插入：支持可选中复制、
   链接可点击、可删除（写入 `self._messages` 便于存档）；
4. `\@novellearn` 的总结还会写入 `self._novel_context`，并随 `ChatWorker`
   注入后续每一轮对话（`novel_context` 参数），因此**本次对话即可复刻剧情**。

### 6.3 `\@novellearn` 小说学习
- 用法：上传小说附件后发送 `\@novellearn`；不带附件发送则列出已有存档，
  加关键词可读取并载入指定总结（如 `\@novellearn 斗破`）。
- 提炼维度：**摘要 / 剧情梗概 / 时间背景 / 人物 / 地点场景 / 风格类型 /
  人物心理与剧情发展 / 故事情节**，并额外产出 **「角色扮演复刻」**
  （世界观 + 角色卡 + 开场场景）。
- 长篇小说按 6000 字分段提炼后汇总，避免超出上下文。
- 存档：`novellearn/<文件名>_<YYYYMMDDHHMM>.json`（UTF-8，原子替换写入）。

### 6.4 `\@weather` 天气查询
- 数据源：和风天气 v7（`https://devapi.qweather.com` + `https://geoapi.qweather.com`），
  KEY 在 **设置 → 技能工具 → 常用接口类 API 管理器 → 和风天气** 中填写。
- 定位：留空则**先用本机 IP 取定位**（ip-api.com 中文 → pconline 兜底 → 回退北京），
  拿到经纬度后直接以「经度,纬度」传给天气接口（不再走 GeoAPI 反查城市）；
  IP 只给了城市名而没给经纬度时才回落到城市检索接口（GeoAPI / 内置城市表）。
  也可 `\@weather 上海`、`\@weather 杭州 西湖区`（多段地名整串查不到会逐级回退）。
- 卡片内容（表格式 HTML5，Qt 富文本子集可正常渲染）：
  实况温度/体感/湿度/风力 → **预警**（红色横幅）→ **空气质量** →
  **未来 3~7 天预报** → **生活指数** → 底部 **Windy.com** 链接。
- 提示词关键词自动追加模块（不增加默认请求量）：

| 提示词 | 追加模块 | 和风接口 |
|--------|----------|----------|
| 降水 / 下雨 / 分钟 | 分钟级降水预报 | `v7/minutely/5m` |
| 天文 / 日出 / 月相 | 日月出没与月相 | `v7/astronomy/{days}` |
| 辐射 / 太阳辐射 / 日照 | 太阳辐射 | `v7/solarradiation/{days}` |
| 时光机 / 过去 / 昨天 | 时光机（历史天气） | `v7/historical/weather` |
| 台风 / 热带气旋 | 热带气旋列表 + 路径 | `v7/tropical/storm-list`、`storm-track` |
| 海洋 / 潮汐 / 海浪 | 海洋（潮汐/海浪） | `v7/ocean/tide`、`ocean/current` |

- 未订阅的付费模块（辐射/时光机/台风/海洋等）失败只降级提示，不影响主卡片。

### 6.5 `\@calorie` 饮食热量
- 用法：`\@calorie 一碗牛肉面加一个茶叶蛋`。
- 估算优先**联网核对**（已配置联网搜索时检索《中国食物成分表》资料并作为参考注入
  提示词；未配置则按常见食物成分估算并如实标注）。务必告知核对入口：
  `https://nlc.chinanutri.cn/fq/`。
- 记录写入 `data/dailylifedata.json`：`records`（每条含时间与早/午/晚/夜宵分档）
  + `daily`（按日汇总 `total_kcal` / `count` / `meals`），同一天多次记录自动累加。
- 输出：食物/分量/热量表 → 本餐合计 → 今日累计与剩余额度 →
  按**《中国居民膳食指南》**的简单饮食指导。

### 6.6 常用接口类 API 管理器（`api_vault.py`）
- 入口：**设置 → 技能工具 → 「常用接口类 API 管理器」**（`MainWindow._open_api_vault`，
  同进程复用窗口实例，主题色随主界面 `apply_accent` 同步）。
- 定位：**纯存储**。因为每个接口调用方式不一致，**所有字段均选填**，不限调用方法，
  只做「登记 + 取用」。

| 字段 | 说明 |
|------|------|
| 名称 | 接口名（技能按名称读取，如「和风天气」） |
| 简介 | 备注用途 / 计费 / 说明 |
| 地址 | BaseURL / 自定义接口地址 |
| API KEY | 默认掩码显示，可切换明文 |
| 自定义内容 | 任意 JSON / Header / Body 等自由文本 |
| 其他规则 | 调用注意事项、限流、配额定额等 |
| 启用 | 关闭后技能忽略该条目 |

- 首次打开自动生成「和风天气」占位条目（不含 KEY，需自行申请：
  `https://console.qweather.com`）。
- 数据文件 `data/api_vault.json`，写入采用 `.tmp` + `os.replace` 原子替换。

### 6.7 验证记录（本次扩展）
- `py_compile`：`ui_manager.py / api_vault.py / weather_service.py /
  novel_learn.py / calorie_tracker.py` 全部通过且**零警告**；lint 0 错误；
- JSON 校验：`skills/tools_list.json`（19 → 22 条）、`skills/skilltools_information.json`
  均通过；三个技能已在目录与详情双文件注册；
- 冒烟：技能解析（`\@weather` / `\@天气` → `weather`）、城市与模块关键词解析
  （`杭州 西湖区`、`台风` → tropical）、天气卡片渲染（含 Windy 链接）、API 存取、
  热量记录与按日累加、小说存档与读回 —— **全部通过**；
- GUI：API 管理器窗口离屏构建 / 新建 / 保存 / 主题色同步通过。

### 6.8 `\@ponywebsite` 小马网站检测
- 用法：`\@ponywebsite`（全部站点）｜`\@ponywebsite 呆站`（按名称/网址筛选）｜
  `\@ponywebsite derpibooru.org`（临时测一个网址，不写进清单）｜
  `\@ponywebsite -n 10`（每站 ping 10 包，默认 4 包）。
- 数据源：`data/ponywebsite.txt`，每行 `名称<Tab>网址`（空格/逗号分隔也能解析，
  `#` 开头为注释），按主机去重保序。
- 检测逻辑：
  1. **ICMP ping**（Windows `-n/-w`、Linux `-c/-W/-i`、macOS `-c/-n/-i`），
     解析发送数 / 接收数 / 丢包率与最小-平均-最大延迟（中英文回显都可解）；
  2. ping 全丢时**自动降级为 TCP 握手**（443 → 80），此时延迟是握手耗时而非 ICMP 延迟，
     卡片里会明确标注；
  3. 站点数 ≤ 3 时顺带做一次 HTTP(S) 状态码探测（4xx/5xx 也算服务活着）。
- 并发：`ThreadPoolExecutor`（默认 8 线程），结果保序；命令失败 / DNS 失败 /
  超时都不抛异常，只在结果里体现。
- 输出：HTML5 结果卡片（概览四宫格：在线 / 丢包 / 不通 / 在线站点平均延迟；
  明细表逐站列出网址链接、IP、丢包、平均延迟、最小/最大、状态），
  末尾再用 notice 汇总「哪些站点不是全通」。
- 验证：清单解析、目标筛选、三种 ping 回显解析、本机实 ping 与 TCP 降级、
  卡片渲染均为实测通过（原自动化测试脚本已随发布清理移除）。
