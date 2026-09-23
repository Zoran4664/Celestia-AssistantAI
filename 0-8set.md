# 0-8set.md — Celestia AssistantAI 全量使用说明书

> 本文档是主项目 **「主文件与主应用」（Celestia AssistantAI）** 的**总说明书**，
> 汇总讲解：项目定位、整体架构、**每一个文件夹**、**每一个系统文件**、
> **添加角色 / 群聊 / 技能 / 日记 / 登陆时间的完整思路**、文件结构、
> 全部功能介绍与使用方法。
>
> 版本：2026-08（覆盖主程序 + 技能工具管理器 + 专注助手 + 日记本 + 登陆时间记录）

---

## 目录

1. [项目总览](#一项目总览)
2. [整体架构与设计思想](#二整体架构与设计思想)
3. [每一个文件夹详解](#三每一个文件夹详解)
4. [每一个系统文件详解](#四每一个系统文件详解)
5. [功能介绍](#五功能介绍)
6. [添加角色的思路与步骤](#六添加角色的思路与步骤)
7. [群聊的配置思路](#七群聊的配置思路)
8. [技能工具的配置思路](#八技能工具的配置思路)
9. [日记本（daily.py）的使用](#九日记本dailypy的使用)
10. [登陆时间记录的使用](#十登陆时间记录的使用)
11. [使用方法速查](#十一使用方法速查)
12. [常见问题](#十二常见问题)

---

## 一、项目总览

`Celestia AssistantAI` 是一个基于 **Python + PySide6 + ChromaDB** 的多模态大模型角色对话
桌面应用。核心特色：

| 特性 | 说明 |
|------|------|
| 界面风格 | HTML5 响应式：半透明白色圆角卡片、渐变/幽灵/危险按钮、Material 投影、细滚动条 |
| 对话体验 | Gemini 式 1:3 分栏（左侧立绘 + 右侧对话）、流式输出、情绪驱动立绘切换 |
| 桌面宠物 | 透明置顶 GIF 状态机，全部动作切换渐隐过渡，支持滚轮缩放 |
| 记忆系统 | 三级深度记忆管线（短期 / 长期 / 重要），降噪、提炼、防重 Merge、遗忘曲线 |
| 群聊 | 点名触发 + AI 判定发言者 + 桌宠跟随最后发言角色 |
| 技能工具 | `\@触发词` 唤醒（思维链 / 联网搜索 / 12+ 场景标签，可自定义） |
| 子项目 | 技能工具管理器（注册表风格）、专注助手、日记本（独立运行） |
| 数据记录 | 登陆时间（每次打开 + 24 时段长期分布），供对话调用 |

**项目目录**：仓库根目录（本文件即为根目录下的 `0-8set.md`；下文所有路径都相对根目录）

---


---

## 二、整体架构与设计思想

### 2.1 启动链路

```
python start.py / main.py
   └─ QApplication（高 DPI → 单实例守卫 → 强制 UTF-8）
       ├─ ConfigLoader      配置单例（读 data/config.json）
       ├─ SignalBus         全局信号总线（观察者模式）
       ├─ RoleManager       角色库 / 群聊解析
       ├─ MemoryPipeline    三级记忆管线（ChromaDB）
       ├─ LLMClientPool     大模型客户端池（main / small / vision）
       ├─ MainWindow        主对话界面（HTML5 响应式）
       ├─ DesktopPet        桌面宠物（可选 --no-pet 禁用）
       └─ 记忆清理定时器 + 系统托盘常驻
```

### 2.2 设计模式

| 模式 | 应用位置 |
|------|---------|
| 门面模式 | `MemoryPipeline` 统一封装检索 / 降噪 / 裁切 / 提炼 / 防重 / 遗忘 |
| 单例模式 | `ConfigLoader`、`ChromaDBManager`、`SignalBus`、`LLMClientPool`、`RoleManager` |
| 观察者模式 | `SignalBus` 全局事件发布/订阅，模块零耦合 |
| 策略模式 | API 服务商切换（openai / deepseek / gemini / ollama / custom） |
| 工厂模式 | `LLMClientPool` 按模型角色（main / small / vision）创建客户端 |
| 模板方法 | 桌宠 GIF 状态机（standing 基态，事件驱动转移 + 渐隐过渡） |

### 2.3 风格统一（全项目一致）

所有界面（主界面 / 设置面板 / 技能工具 / 专注助手 / 日记本 / 确认对话框）共用同一套
HTML5 视觉语言：

| 项目 | 取值 |
|------|------|
| 主色 Accent | `#6c8ef5`（悬停 `#8fb0ff`，按下 `#4a6fd4`） |
| 文字色 | 深 `#2b2b33` / 中 `#6b7280` / 浅 `#9aa0ac` |
| 卡片底色 | `rgba(255,255,255,0.92)`，描边 `#e5e7f0`，圆角 14 |
| 窗口 | 无边框圆角半透明（244,245,250,210 @16px），背景可叠加 theme 图 |
| 标题栏 | 自定义：拖动 / 最小化 ─ / 最大化 □ / 关闭 ×（右上角） |
| 按钮 | 渐变主按钮（保存/确认）、幽灵按钮（取消/次操作）、危险按钮（删除） |
| 输入框 | 白底圆角，focus 时主色描边 |
| 滚动条 | 细圆角主色调滚动条 |
| 字体 | `Microsoft YaHei UI / Microsoft YaHei` |
| 确认框 | HTML5 风格自定义对话框（白卡片 + 主题色/危险按钮 + 幽灵取消） |

> 原则：**全部子板块（skilltools / focus_assistant / daily）风格严格继承主界面**。

---

## 三、每一个文件夹详解

```
主文件与主应用\
├── roles\            · 角色库（角色卡存放处）
├── roles_img\        · 对话立绘（主界面左侧角色形象）
├── roles_desktop\    · 桌宠动图（透明置顶宠物各状态动作）
├── theme\            · 主界面背景图片（可在设置中选用 / 高斯模糊）
├── data\             · 程序数据目录（配置 / 用户数据 / 登陆时间）
├── history\          · 记忆与对话存档（自动生成）
├── skills\           · 技能数据目录（\@技能 唤醒）
├── dailydata\        · 日记本数据目录（dailytext / dailyfile）
├── img\              · 图标与图片资源（托盘 / 删除 / 倒计时等）
├── log\              （空占位目录）
├── logs\             （空占位目录，实际日志在 utils/logs/）
├── utils\            日志 / QThread 任务 / UTF-8 工具（勿手动修改）
├── tools\            开发者测试脚本 / 资源生成器
└── __pycache__\       Python 字节码缓存（自动生成）
```

### 3.1 `roles\` —— 角色库（· 可自定义）

每个角色一个文件夹，**文件夹名 = 角色名**（如 `roles\Rainbow_Dash\`）。当前已含 4 个角色：

```
roles\
├── group.json             群聊组配置
├── Applejack\             角色文件夹
│   ├── roles.json         角色卡（名字/性格/系统提示词/情绪列表）
│   └── emotion.json       情绪关键词映射
├── Fluttershy\
│   ├── roles.json
│   └── emotion.json
├── Rainbow_Dash\
│   ├── roles.json
│   └── emotion.json
└── Twilight_Sparkle\
    ├── roles.json
    └── emotion.json
```

**添加角色**：在 `roles\` 下新建文件夹 → 写 `roles.json`（角色卡）→ 写 `emotion.json`
（情绪映射）→ 重启程序即在左栏出现（详见 [第六章](#六添加角色的思路与步骤)）。

### 3.2 `roles_img\` —— 对话立绘（· 可自定义）

主界面左侧的角色形象，按情绪自动切换（渐隐过渡）：

```
roles_img\<角色名>\
├── 角色名-.png            默认立绘（必需）
├── 角色名-neutral.png     平静
├── 角色名-happy.png       开心
├── 角色名-sad.png         难过
├── 角色名-angry.png       生气
└── 角色名-surprised.png   惊讶
```

> 支持 `.png / .jpg / .jpeg / .webp`。情绪切换带 400ms 渐隐过渡。

### 3.3 `roles_desktop\` —— 桌宠动图（· 可自定义）

透明置顶桌宠各状态动作，全部切换渐隐过渡：

```
roles_desktop\<角色名>\
├── 角色名-standing.gif   静止（默认，必需）
├── 角色名-run.gif        拖动时
├── 角色名-say1.gif       输入时
├── 角色名-say2.gif       输出时
├── 角色名-hello.gif      定时问候
├── 角色名-sleep.gif      夜间休息提醒
└── 角色名-work.gif       专注模式
```

> 仅支持 `.gif`；文件需大于 4KB（小于 4KB 视为占位图而忽略）。

### 3.4 `theme\` —— 背景图片（· 可自定义）

主界面背景模板，放入 `default.png`、`test.png` 等即可在「设置 → 外观」中选用。
支持 `.png / .jpg / .jpeg / .webp`；可开启高斯模糊。日记本等子板块继承此背景。

### 3.5 `data\` —— 程序数据目录

```
data\
├── config.json              全局设置（API/路径/记忆/桌宠/外观/登陆时间开关）
├── about.json               关于系统信息
├── role_abbr.json           角色缩写映射（如 Rainbow_Dash → RD）
├── logintime.json           登陆时间短期记录（每次打开主界面追加一条）
└── logintime_history.json   登陆时间 24 时段长期分布
```

### 3.6 `history\` —— 记忆与对话存档（自动生成）

```
history\
├── chroma\                  长期/重要记忆（ChromaDB 向量库，自动生成）
└── conversations\           短期对话 talk_*.json + 历史会话 session_*.json
```

### 3.7 `skills\` —— 技能数据目录（· 可自定义）

```
skills\
├── tools_list.json                  技能目录（name/trigger/kind/aliases/category/enabled）
└── skilltools_information.json      技能详情（description/prompt_template/parameters）
```

主程序 `\@技能 唤醒` 与技能工具管理器共用此目录，保存后立即生效。

### 3.8 `dailydata\` —— 日记本数据目录（· 自动生成）

```
dailydata\
├── dailytext\              日记 JSON（每篇日记一个 <id>.json）
└── dailyfile\              附件仓库（插入的图片/文件副本，正文以相对路径引用）
```

### 3.9 `img\` —— 图标与图片资源

```
img\
├── 12X12.ico               应用图标（窗口/托盘）
├── answear.png / tell.png / close.png   历史条 / 删除按钮等
├── dailydel.png            日记删除按钮图标
├── del.png                 历史删除图标
├── time.png                专注倒计时叠加文字底图
└── 未标题-1.png            其它图片资源
```

### 3.10 `utils\` —— 工具模块（勿手动修改）

```
utils\
├── logger.py               日志封装（输出到 utils/logs/app.log）
├── utf8.py                 强制 UTF-8 输出（Windows 避免 GBK 乱码）
├── async_worker.py         QThread 异步任务基类（AsyncWorker）
└── logs\app.log            运行日志（自动生成）
```

### 3.11 `tools\` —— 素材工具

仅保留两个非测试用途的脚本：`generate_placeholder_assets.py`（生成占位立绘 /
桌宠动图 / 主题背景，目录为空时可一键铺满）与 `fix_pet_gifs.py`（修复过小的桌宠 GIF）。
原测试 / 探针 / 回归脚本已随发布清理移除，不影响程序运行。

---


## 四、每一个系统文件详解

### 4.1 启动与配置

| 文件 | 作用 |
|------|------|
| `main.py` | **主程序入口**。`python main.py` 启动；参数 `--config`、`--no-pet`、`--role`、`--debug`、`--no-gpu` |
| `start.py` | **启动器**。检测/升级依赖后启动；`python start.py`（GUI）、`--direct`（直接启动）、`--setup`（先装依赖再启动） |
| `config_loader.py` | **配置单例**。读 `data/config.json`，与内置默认值深度合并；路径绝对化；环境变量回退；单例 `ConfigLoader.instance()` |
| `requirements.txt` | 依赖清单（PySide6 / chromadb / openai / Pillow / pypdf 等） |
| `Dockerfile` / `docker-compose.yml` / `.dockerignore` | Docker 运行支持 |

### 4.2 核心模块

| 文件 | 作用 |
|------|------|
| `signal_bus.py` | **全局信号总线**（观察者模式）：LLM 流/情感/桌宠指令/记忆更新/会话保存/设置变更等跨模块事件统一发布订阅 |
| `llm_client.py` | **LLM 客户端**（工厂池 + QThread）：按 main / small / vision 缓存客户端；兼容 OpenAI / DeepSeek / Gemini / Ollama 等 |
| `memory_pipeline.py` | **三级记忆管线**：短期（内存 + talk json）/ 长期（ChromaDB）/ 重要（ChromaDB）；降噪、提炼、防重 Merge、遗忘曲线 |
| `role_manager.py` | **角色库 / 群聊解析**（单例）：扫描角色卡、情感归一化、发言者解析、立绘/桌宠路径 |
| `ui_manager.py` | **主界面与设置面板**：Gemini 式 1:3 分栏、流式气泡、历史侧栏、设置面板、`/theme`、日记入口 |
| `pet_manager.py` | **桌面宠物引擎**：透明置顶 GIF 状态机，渐隐过渡，右键菜单 11 项 |
| `skill_manager.py` | **技能管理器**（单例）：`\@技能` 目录/详情查询、上下文构建、普通技能与开关工具 |
| `skill_popup.py` | **`\@技能` 唤醒弹窗**：无边框 + 投影 + 圆角列表，键盘导航 |
| `focus_assistant.py` | **专注助手**：先设置专注时间 → 置顶倒计时 → 角色语气鼓励/祝贺 |

### 4.3 子项目

| 文件 | 作用 |
|------|------|
| `skilltools.py` | **技能工具管理器**：Windows 注册表风格技能库，可视化增删改查技能 |
| `daily.py` | **日记本子项目**：独立运行的日记程序，风格与主程序一致 |
| `logintime.py` | **登陆时间记录**：记录每次打开时间 + 24 时段长期分布，供对话调用 |

### 4.4 文档

| 文件 | 作用 |
|------|------|
| `README.md` | 完整设计文档（架构 / 快速开始 / 功能 / 记忆 / 角色格式） |
| `set.md` | 基础使用说明（文件夹 / 添加角色 / 图片命名） |
| `roleset.md` | 角色设计完整指南 |
| `helpset.md` | 角色制作与设置完整指南（含字段表 / 示例） |
| `groupset.md` | 群聊功能设置与实现指南 |
| `toolsset.md` | 技能工具管理器设计文档 |
| `dailymanger.md` | 日记本子项目规划文档 |
| `0-8set.md` | **本文档**：全量总说明书 |

### 4.5 关键配置说明（data/config.json）

```json
{
  "api": {
    "provider": "custom",                // openai/deepseek/gemini/ollama/custom
    "api_base": "https://api.siliconflow.cn/v1/",
    "api_key": "sk-xxx",
    "main_model": "deepseek-ai/DeepSeek-V3.2",   // 主对话模型
    "small_base": "...", "small_key": "...",
    "small_model": "Qwen/Qwen3-30B-A3B-Instruct-2507",  // 小模型（记忆/降噪）
    "vision_base": "...", "vision_key": "...",
    "vision_model": "Qwen/Qwen3-VL-30B-A3B-Instruct"    // 视觉模型（多模态）
  },
  "paths": { "roles": "./roles", "roles_img": "./roles_img",
             "roles_desktop": "./roles_desktop", "theme": "./theme",
             "history": "./history", "data": "./data", "skills": "./skills" },
  "chat": { "temperature": 0.7, "max_tokens": 10240, "stream": true },
  "memory": { "trim_threshold": 20, "keep_rounds": 10,
              "top_k_long_term": 3, "top_k_important": 2,
              "forgetting_interval_days": 30, "hit_threshold": 8,
              "cleanup_interval_hours": 24, "denoise_enabled": true,
              "denoise_timeout_seconds": 5.0,
              "dedup_high_similarity": 0.15, "dedup_partial_similarity": 0.45,
              "merge_max_depth": 3, "merge_drift_threshold": 0.55 },
  "pet": { "enabled": false, "greeting_interval_min": 5,
           "sleep_hours": [22, 6], "focus_mode": false,
           "floating_chat": false, "zoom_enabled": true },
  "ui": { "blur_background": true, "random_proactive": false,
          "window_opacity": 0.92, "accent": "#6c8ef5",
          "theme_file": "./theme/test.png",
          "icon_path": "./data/app.ico",
          "transparent_chat": false, "user_name": "用户",
          "font_family": "SimSun",
          "last_role": "Rainbow_Dash", "last_group": "", "last_mode": "单聊",
          "login_time_enabled": true }    // 登陆时间记录开关（关闭不记录但保留数据）
}
```

---


## 五、功能介绍

### 5.1 主对话界面（MainWindow）

- **布局**：Gemini 式 1:3 分栏。左栏上部（4/5）角色立绘（按情绪自动切换，渐隐过渡）；
  左栏下部（1/5）角色下拉、群聊下拉、随机主动对话开关、主题/设置按钮、桌宠开关、
  日记记录入口。右栏为对话气泡区（流式渲染）、输入框、附件按钮、新会话/历史侧栏。
- **气泡**：AI 回复用角色圆形头像 + 白底框；用户头像在设置中自定义；输入框回车发送。
- **历史侧栏**：右侧历史会话列表，点击加载、右键修改标题/删除。
- **`/theme` 命令**：打开设置面板主题分节。
- **情绪驱动立绘**：AI 回复末尾标注 `〖情绪〗`，程序提取后切换对应立绘。
- **多模态附件**：支持图片 / PDF / Word / Excel / PPT / 文本，由视觉模型描述后注入对话。

### 5.2 设置面板（单页、分节卡片）

| 分节 | 功能 |
|------|------|
| 对话 API | 服务商 + 地址 + Key + 主/小模型 + 温度 / Token |
| 多模态文档 | 视觉模型独立 API（图片 / PDF / Word / Excel / PPT） |
| 资源路径 | 角色库 / 角色图 / 桌面形象 / 记忆存储 / 数据目录 / 用户头像 / 昵称 |
| 外观 | 主题背景（theme/ 图片列表）+ 软件颜色（预设/自定义实时换肤）+ 背景模糊 + 透明聊天窗口 + 图标 |
| 技能工具 | 打开技能工具管理器（注册表风格，主题色实时同步） |
| 桌面形象 | 启用桌宠 + 问候间隔 + 专注模式 + 悬浮对话 + 滚轮缩放 |
| 记忆 | 德谬歌矩阵训练（talk.json → 长期记忆）+ 遗忘协议（按角色/群聊抹除） |
| 遗忘日程 | 遗忘短时记忆 / 历史长期摘要记忆 |
| 遗忘日记 | 删除全部日记（dailydata/dailytext） |
| 遗忘登陆时间 | 遗忘短期时间 / 遗忘长期时间 / 关闭记录功能 |

### 5.3 桌面宠物（DesktopPet）

- 透明置顶无边框窗口 + GIF 状态机：standing / run / say1 / say2 / hello / sleep / work；
- 右键菜单 11 项：设置日程 / 设置提醒 / 定时问候 / 小窗对话 / 悬浮对话 / 提醒休息 /
  专注模式 / 专注助手 / 返回主页 / 隐藏桌宠 / 退出；
- 定时问候（默认 30 分钟）；22:00–06:00 睡眠提醒；专注模式失焦劝导；
- 全部动作切换渐隐过渡（300ms crossfade），支持滚轮缩放。

### 5.4 技能工具（`\@技能` 唤醒）

- 输入 `\@`（先反斜杠再 @）弹出技能选择菜单，选中插入 `\@触发词`；
- **开关工具**（可同时开 0-2 个）：`\@thinking` 思维链、`\@network` 联网搜索；
- **场景标签**：`\@research` 科研、`\@code` 代码、`\@words` 文案、`\@health` 健康、
  `\@med` 医学、`\@create` 创意、`\@math` 数学、`\@work` 办公、`\@education` 教育、
  `\@translate` 翻译、`\@train` 记忆训练、`\@image` 生成图片；
- **组合**：`\@thinking \@network \@research 帮我查最新量子计算论文`；
- **关闭**：输入 `/closed`（或 `\closed`）退出所有技能；
- 自定义：`python skilltools.py` 打开注册表风格管理器。

### 5.5 三级记忆管线（MemoryPipeline）

| 层级 | 容器 | 策略 |
|------|------|------|
| 短期 | 内存 deque + `history/conversations/talk_*.json` | 达阈值裁切 |
| 长期 | ChromaDB `long_term`（cosine） | 遗忘曲线：低频过期删除、高频固化 |
| 重要 | ChromaDB `important`（cosine） | 永久豁免，只合并不删 |

流程：前置检索 → 小模型降噪（三重降级）→ 组装上下文 → 输出后归档 → 达阈值裁切 →
小模型提炼 → 向量防重（极高相似丢弃 / 局部相似 Merge / 无关新增）→ 遗忘曲线定时清理。

### 5.6 专注助手（focus_assistant.py）

先设置专注时间 → 弹出置顶悬浮倒计时窗（`img/time.png` 上叠加倒计时文字）→
未到设定时间提前结束：以当前角色语气输出「坚持」鼓励；到达设定时间：
以当前角色语气输出「完成」祝贺。鼓励文案由 LLMWorker 异步生成，不阻塞主线程。

### 5.7 日记本（daily.py）

- 一级界面 `DailyMainWindow`：日记卡片列表（标题 / 建立日期 / 上次修改日期），
  右上角「新建日记」「删除文件」，每篇带删除按钮；
- 二级界面 `DailyEditWindow`：标题、富文本正文、插入图片、插入文件、角色专属多选、保存；
- 文件管理对话框 `FileManageDialog`：列出 dailyfile 附件，可勾选删除（显示 From: 来源日记）；
- 图片缩放选项：完整大小 / 页面的50%大 / 页面的25%中 / 页面的15%小；
- 数据全部在 `dailydata/`：dailytext 存日记 JSON，dailyfile 存附件副本；
- 主界面左栏「日记记录」按钮直接打开；也可 `python daily.py` 独立运行。

### 5.8 登陆时间记录（logintime.py）

- 每次打开主界面，向 `data/logintime.json` 追加一条（时间 / 小时 / 时间戳）；
- 同时累加 `data/logintime_history.json` 24 时段分布；
- 对话时自动注入「用户打开时间习惯」（短期摘要 + 长期分布）供模型感知用户作息；
- 设置面板可遗忘短期 / 长期 / 关闭记录（关闭不记录但保留已有数据）。

---


## 六、添加角色的思路与步骤

### 6.1 思路

角色 = **角色卡（性格/系统提示词）** + **情绪映射** + **对话立绘** + **桌宠动图** + （可选）**群聊组**。
程序按「文件夹名 = 角色名」自动扫描 `roles/` 目录，只要文件齐全、命名正确，重启即生效，
无需改任何代码。

### 6.2 第 1 步：创建角色文件夹

在 `roles\` 下新建文件夹，**文件夹名 = 角色名**（建议英文或简短中文），
例如 `roles\云宝\`。

### 6.3 第 2 步：编写角色卡 `roles\云宝\roles.json`

```json
{
  "name": "云宝",
  "display_name": "云宝",
  "personality": "活泼开朗、元气满满、偶尔有点小冒失",
  "system_prompt": "你是云宝，一个元气满满的少女。说话热情活泼。回复末尾必须用〖〗标注情感标签（可选：开心/平静/难过/生气/惊讶）。",
  "character description": "角色的详细介绍与背景设定（可选）",
  "default_emotion": "happy",
  "emotion_list": ["neutral", "happy", "sad", "angry", "surprised"],
  "first_message": "你好呀！我是云宝，今天要去飞一圈嘛？"
}
```

> `system_prompt` 是角色「灵魂」：身份、性格、语气、知识边界、情感标注要求都写在这里。
> `name` 必须与文件夹名一致。

### 6.4 第 3 步：编写情绪映射 `roles\云宝\emotion.json`

```json
{
  "mappings": {
    "neutral":   ["平静", "中性", "平常"],
    "happy":     ["开心", "愉快", "高兴", "兴奋"],
    "sad":       ["难过", "悲伤", "沮丧", "失落"],
    "angry":     ["生气", "愤怒", "不满"],
    "surprised": ["惊讶", "震惊", "意外"]
  },
  "keywords": {
    "happy":     ["哈哈", "太好了", "开心死了"],
    "sad":       ["难过", "伤心", "哭了"]
  }
}
```

- `mappings`：回复文本中出现的 `〖开心〗` 等标签 → 情绪；
- `keywords`：用户输入关键词 → 情绪（用于快速判断）。

### 6.5 第 4 步：添加对话立绘 `roles_img\云宝\`

```
roles_img\云宝\云宝-.png          默认立绘（必需）
roles_img\云宝\云宝-happy.png     开心
roles_img\云宝\云宝-sad.png       难过
roles_img\云宝\云宝-angry.png     生气
roles_img\云宝\云宝-surprised.png 惊讶
```

### 6.6 第 5 步：添加桌宠动图 `roles_desktop\云宝\`

```
roles_desktop\云宝\云宝-standing.gif   静止（必需，>4KB）
roles_desktop\云宝\云宝-run.gif        拖动
roles_desktop\云宝\云宝-say1.gif       输入
roles_desktop\云宝\云宝-say2.gif       输出
roles_desktop\云宝\云宝-hello.gif      问候
roles_desktop\云宝\云宝-sleep.gif      休息
roles_desktop\云宝\云宝-work.gif       专注
```

### 6.7 第 6 步：（可选）加入群聊

在 `roles\group.json` 的 `groups` 中添加组，把新角色加入 `members`。

### 6.8 第 7 步：重启生效

重启程序（或 `python main.py`），左栏「角色」下拉即出现新角色。

---

## 七、群聊的配置思路

### 7.1 群聊组 `roles\group.json`

```json
{
  "groups": {
    "朋友群聊": {
      "members": ["Twilight_Sparkle", "Rainbow_Dash", "Fluttershy", "Applejack"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头",
      "aliases": { "云宝": "Rainbow_Dash", "萍琪派": "Pinkie_Pie" }
    }
  }
}
```

### 7.2 发言者判定优先级

```
用户消息点名（角色名/别名） ──命中──▶ 该角色直接发言
        │未命中
        ▼
API 内部判定谁最该发言（结果不输出）
        │判定失败
        ▼
解析模型输出中的 [角色名]: 前缀
        │仍失败
        ▼
回退上一发言者（首条则随机选一名成员）
```

### 7.3 使用群聊

1. 启动程序，左栏「群聊」下拉选择组名；
2. 发送消息：
   - **点名**：输入「云宝，明天去飞行表演？」→ Rainbow_Dash 回复；
   - **不点名**：输入「大家周末有什么计划？」→ API 内部判定谁最该发言；
3. 桌宠自动跟随最后发言角色。

---


## 八、技能工具的配置思路

### 8.1 技能数据结构

一个技能 = **目录条目**（`skills/tools_list.json`）+ **详情条目**（`skills/skilltools_information.json`）。

**目录条目**：
```json
{
  "updated": "2026-08-03 19:20:00",
  "count": 17,
  "skills": [
    { "name": "翻译", "trigger": "translate", "kind": "skill",
      "aliases": ["翻译", "fanyi", "译一下"], "category": "工具", "enabled": true },
    { "name": "思维链", "trigger": "thinking", "kind": "switch",
      "aliases": ["思维链", "推理"], "category": "开关", "enabled": true }
  ]
}
```

**详情条目**（键名 = 技能名称）：
```json
{
  "翻译": {
    "description": "执行翻译任务，将用户给出的内容翻译为目标语言。",
    "prompt_template": "用户调用了「翻译」技能。请把用户消息中待翻译的内容翻译成{target_lang}，只输出译文。",
    "parameters": [ { "name": "target_lang", "default": "英文", "required": false } ]
  }
}
```

### 8.2 技能字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 必需 | 技能名称（唯一） |
| `trigger` | 必需 | 聊天中 `\@触发词` 唤醒，唯一，不含空格 |
| `kind` | 可选 | `skill`=普通技能；`switch`=开关工具（可同时开 0-2 个） |
| `aliases` | 可选 | 逗号分隔别名 |
| `category` | 可选 | 分类（工具 / 记忆 / 创作 / 开关） |
| `enabled` | 可选 | `false` 时 `\@` 唤醒列表不显示 |
| `description` | 可选 | 注入 LLM 的技能上下文 |
| `prompt_template` | 可选 | 执行要求，可含 `{参数名}` 占位符 |
| `parameters` | 可选 | `[{name, default, required}]`，对应占位符 |

### 8.3 使用方法

1. **图形化管理**：`python skilltools.py` 打开注册表风格管理器；
   左侧键树选技能，右侧编辑字段，双击字段弹注册表式编辑框；
2. **主界面唤醒**：聊天输入框输入 `\@` 弹选择菜单，选中插入 `\@触发词`；
3. **自定义新技能**：管理器点「＋ 新建技能」→ 填名称/触发词/说明/要求/参数 → 保存；
4. **保存即生效**：主程序 `SkillManager` 立即读取，无需重启。

---

## 九、日记本（daily.py）的使用

### 9.1 数据存储（全部在 `dailydata/`）

```
dailydata\
├── dailytext\              日记 JSON（每篇 <id>.json）
└── dailyfile\              附件副本（图片/文件）
```

日记 JSON 结构：
```json
{
  "id": "20260807_220000_1a2b3c",
  "title": "今天的心情",
  "content_html": "<p>正文内容</p><p><img src=\"dailyfile/20260807_220000_x9y8z7_照片.png\"></p>",
  "created_at": "2026-08-07 22:00:00",
  "updated_at": "2026-08-07 23:15:30",
  "allowed_roles": ["Rainbow_Dash", "朋友群聊"]
}
```

### 9.2 功能

- **一级界面**：日记卡片列表（标题 / 建立 / 修改时间）+ 单篇删除按钮；
- **二级界面**：标题、富文本正文、插入图片、插入文件、角色专属多选、保存；
- **文件管理**：列出 dailyfile 附件，勾选删除，每项显示 `From:来源日记标题`；
- **图片缩放**：完整大小 / 页面的50%大 / 页面的25%中 / 页面的15%小；
- **窗口控制**：最小化/最大化/关闭按钮在右上角，内容占满整个界面，
  背景继承主界面 theme 图（居中裁剪不拉伸）；
- **删除确认**：HTML5 风格确认框（与主界面一致）。

### 9.3 使用方式

1. **主界面入口**：左栏「日记记录」按钮直接打开；
2. **独立运行**：`python daily.py`；
3. 右上角「新建日记」→ 写标题/正文 → 可插入图片/文件 → 保存；
4. 删除文件界面可查看每个附件来自哪篇日记。

---

## 十、登陆时间记录的使用

### 10.1 数据文件（在 `data/`）

| 文件 | 内容 |
|------|------|
| `logintime.json` | 每次打开主界面的时间（`logins` 数组，最近 200 条）+ total + last_login |
| `logintime_history.json` | 24 小时段分布（`buckets`，每小时一段，一天 24 个）+ total |

### 10.2 对话调用

对话时自动向系统提示词注入「【用户的打开时间习惯】」：
- 短期摘要：最近打开本应用的时间（含小时段）；
- 长期分布：最常见的 3 个打开时段及占比。

让角色能感知用户的作息，问候 / 对话更贴近实际使用习惯。

### 10.3 设置

设置面板「记忆」分节提供：
- **遗忘短期时间**：清空 `logintime.json`（带确认）；
- **遗忘长期时间**：清空 `logintime_history.json`（带确认）；
- **登陆时间记录开关**：关闭后不再记录，但**保留已有数据**（`ui.login_time_enabled`）。

---


## 十一、使用方法速查

### 11.1 启动

```bash
python start.py              # 推荐：启动器（自动检查依赖）
python start.py --setup      # 先安装依赖再启动
python main.py               # 直接启动
python main.py --no-pet      # 禁用桌宠
python main.py --role 云宝   # 指定角色
python skilltools.py         # 技能工具管理器（独立）
python daily.py              # 日记本（独立）
```

### 11.2 主界面操作

| 操作 | 方法 |
|------|------|
| 切换角色 | 左栏「角色」下拉 |
| 进入群聊 | 左栏「群聊」下拉选择组名 |
| 随机主动对话 | 左栏「随机主动对话」开关 |
| 切换主题 | 设置 → 外观 → 主题背景 |
| 打开桌宠 | 左栏「桌宠：开启」 |
| 打开日记 | 左栏「日记记录」 |
| 打开设置 | 左栏「设置」或 `/theme` 命令 |
| 唤起技能 | 输入框输入 `\@` 弹菜单 |
| 关闭技能 | 输入 `/closed` |

### 11.3 设置面板分节速查

对话 API → 多模态文档 → 资源路径 → 外观 → 技能工具 → 桌面形象 → 记忆 →
遗忘日程 → 遗忘日记 → 遗忘登陆时间 → 保存。

### 11.4 常用命令

| 命令 | 作用 |
|------|------|
| `/theme` | 打开设置面板外观分节 |
| `/closed`（或 `\closed`） | 退出所有技能/开关 |
| `\@thinking` | 思维链（CoT）开关 |
| `\@network` | 联网搜索开关 |
| `\@research` / `\@code` / `\@med` … | 场景标签 |

---

## 十二、常见问题

| 问题 | 解决 |
|------|------|
| `chromadb` 导入错误 | `pip install -r requirements.txt`；建议 Python 3.10/3.11 |
| 立绘不切换 | 检查 `roles_img\<角色名>\<角色名>-<情绪>.png` 命名（注意横杠） |
| 桌宠无动作 | 确认 `roles_desktop\<角色名>\<角色名>-<动作>.gif` 且 >4KB |
| 情绪标签出现在气泡 | 旧存档可能含标签，重新对话即隐藏 |
| 中文乱码 | 所有 json / 图片路径保持 UTF-8 编码保存，不要用 GBK |
| LLM 无响应 | 检查 `data/config.json` api 段 / 环境变量；查看 `utils/logs/app.log` |
| 记忆未入库 | 确认小模型配置有效、`history/chroma` 目录可写 |
| 历史标题「新会话」 | 第一轮对话后会自动生成「角色名-总结」标题，稍等片刻 |
| 如何修改历史标题 | 右侧历史会话条目右键 →「修改标题」 |
| 日记保存后不显示 | 已修复：保存后自动刷新卡片列表 |
| 删除文件界面想知道来源 | 已支持：每项显示 `From:来源日记标题` |
| 想记录/遗忘打开时间 | 设置 → 记忆 → 遗忘登陆时间 |

---

> 本文档由项目内各模块文档（README.md / set.md / roleset.md / helpset.md /
> groupset.md / toolsset.md / dailymanger.md）汇总整理，持续维护。