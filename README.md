## 一、项目介绍
<img width="3212" height="734" alt="Celestia AssistantAI" src="https://github.com/user-attachments/assets/cd37bf67-8cca-46e8-9a5f-f6e1dc0d9841" />

Celestia AssistantAI是一个基于 Python + PySide6 + ChromaDB 的多模态大模型角色对话 Agent 桌面（花瓶）应用，使用了调用deepseek V4的Cline与DSH（大烧货）完成。页面风格参考了常见LLM网页端的布局。未来的目标是实现日常办公学习的辅助以及文字聊天。
对话方面本项目使用了chromaDB等实现了三级记忆的管理，确保在不遗忘关键记忆的同时能以最大性价比得到个性化的体验（灵感和思路参考了B站Play0编写的昔莲Cyrene-Agent，德谬歌在发力嗯）。

本项目通过提示词实现了在日常对话时角色形象会随对话内容的情绪改变（目前内部尚未完成设计，只在测试性实现思路。目前项目内置4位角色（Twilight Sparkle, Rainbow Dash, Fluttershy, Applejack）和5种情绪（平静、震惊、伤心、生气、开心）），这部分未来预计会持续丰富，现演示角色的角色图片为ZoinkscoobFurryNoobAI_V10配合基于Gemini banana模型风格的Lora使用秋叶WebUI与comfyUI生成。其他图片素材均没有仔细绘制。

同时本应用内置正在持续完善的桌面桌宠功能（目前使用gif和图片切换实现了该部分功能，效果不尽人意，未来再完善了）。

日常聊天支持多角色对话（还在优化，角色切换的实现效果有时略抽风）和随机主动对话，同时支持加载与管理skill（现在里面的就是我占位用的，后续要改）能够协助办公学习。

由于梁圣最近因DeepseekV4全面涨价评级降低为梁子，所以为了节省API费用，以及便宜的鲸鱼娘暂未睁眼，以及部分绘图需求，目前模型支持设置主模型/整理记忆用的小模型与MoE多模态模型的分别配置，以及skill的特殊使用需求独立模型，以及支持ollama本地部署模型和硅基流动等聚合站点的API（如因聚合站点的免费账户TPM/RPM存在限制，因此增加了较为严格的打断机制以免影响体验）。

桌宠功能包括了休息提醒、倒计时提醒等实用工具，以及专为网课增加的专注助手（选择页面，若页面被切入后台则进行提示提醒与鞭策）。同时本应用会记录应用打开时间、使用情况等信息，后期在提示对话内容也会更加的个性化。

本项目支持配置Search API(如谷歌/百度等)，能够打通联网搜索功能，并支持思维链显示（需模型原生支持，否则为伪思维链）。

本项目也内置了简单的日记本与记录工具，通过记录，角色也能够越来越智能。

本项目也支持读取json格式的标准对话文本数据，你也可以导入扩增后/提取后的对话进行微调。

目前正在调试Role Play功能，该功能拟实现达到简易酒馆的体验效果，并且可以配置工具功能（例如切换语言风格与对话任务，甚至你可以加入破甲指令！）。
<img width="1440" height="720" alt="intro" src="https://github.com/user-attachments/assets/4a4d916a-bccc-4c82-b35d-2545c92511e0" />

---

## 二、版本更新记录

V0.1
项目初步实现了纯文本对话功能与记忆，功能，并设计了主界面角色形象随情绪变化的功能。

V0.1.1
项目确定了UI的风格，以及风格与显示管理的功能。添加了背景图设置（默认图使用ZoinkscoobFurryNoobAI_V10生成）。

V0.1.2
项目完成了桌宠及桌宠功能的实现（专注助手和提醒等），多角色对话和主动对话，以及对话三级记忆策略。

V0.1.3
项目完善了桌宠功能，优化了体验（使用起来更像人了！）。

V0.1.4
项目优化了桌宠功能的提醒问候内容。

V0.2
项目支持了多模态模型以及生图/文本识别模型的适配。优化了记忆学习功能。

V0.2.1
项目优化了三级记忆的模型适配，减少了无用/高价API的消耗。

V0.2.2
项目支持了skill设置与管理功能，以及联网与思维链开关的功能。

V0.2.3
项目优化了日常使用习惯的记录，能够让角色对话更个性化。

V0.2.4
项目支持了日记记录等功能，能够为模型提供更多的个人使用习惯信息。

V0.2.5
模型优化了主动设置的遗忘策略。
<img width="3038" height="1408" alt="SourcecodeGodLaunch" src="https://github.com/user-attachments/assets/a279c550-2550-44cc-8cc4-d2feec077d1f" />

---

## 三、文件路径介绍

```
主文件与主应用\
├── start.py                   # 缺少库检测与用户启动入口
├── main.py                    # 启动入口
├── config_loader.py           # 配置单例（默认值合并/路径解析/环境变量回退）
├── signal_bus.py              # 全局信号总线
├── llm_client.py              # OpenAI 兼容客户端池 + LLMWorker(QThread)
├── memory_pipeline.py         # 三级记忆管线（短期/长期/重要）
├── role_manager.py            # 角色库/群聊解析/情感归一化/立绘路径
├── ui_manager.py              # 主对话界面（HTML5 响应式）
├── focus_assistant.py         # 专注助手（先设置专注时间→置顶倒计时→角色语气鼓励）
├── skill_manager.py           # 技能管理器（\@ 技能目录/详情查询/上下文构建）
8├── skill_popup.py             # \@ 技能唤醒弹窗（纯白不透明底，键盘导航）
├── skilltools.py              # 技能工具管理器（Windows 注册表风格窗口）
├── pet_manager.py             # 桌面宠物引擎（渐隐过渡状态机）
├── requirements.txt           # 依赖清单
├── Dockerfile / docker-compose.yml / .dockerignore
├── README.md                  # 本文档
├── roles\                     # ★ 角色卡目录
│   ├── group.json             #   群聊组预设（组名 → 成员角色名列表）
│   └── 默认助手\
│       ├── roles.json         #   角色卡（名字/性格/系统提示/情感列表）
│       └── emotion.json       #   情绪类型 → 关键词映射
├── roles_img\                 # ★ 角色立绘目录
│   └── 默认助手\
│       ├── 默认助手-.png       #   默认立绘（name-.png）
│       └── 默认助手-happy.png  #   情感立绘（name_-emotion_.png）
├── theme\                     # ★ 背景模板图片（default.png 等）
├── roles_desktop\             # ★ 桌宠动图
│   └── 默认助手\
│       ├── 默认助手-standing.gif / -run / -say1 / -say2 / -hello / -sleep / -work（目前添加的部分例子）
├── history\                   # ★ 记忆与对话存档
│   ├── chroma\                #   ChromaDB 向量库（长期/重要记忆）
│   └── conversations\         #   短期对话 talk_<key>.json + 会话 session_*.json
├── data\
│   ├── config.json            # 系统配置（API/路径/记忆/桌宠/UI）
│   └── role_abbr.json         # 角色缩写映射（如 Rainbow_Dash → RD）
├── skills\                    # 技能数据（tools_list.json / skilltools_information.json）
├── utils\                     # 日志 / QThread 任务基类
└── tools\
    ├── generate_placeholder_assets.py   # 占位立绘/动图/主题生成器
    └── smoke_test.py                    # 无头冒烟测试
```
---

## 四、快速开始（使用方法）

### 4.1 本地运行（推荐 Python 3.10 / 3.11）
请确保您的计算机安装了Python环境（推荐3.10,/3.11，但实测3.9/3.12和3.13也可以用）
如果没有环境请去https://python.org下载（不要用百度搜永久免费版python！）

# 4.2 安装环境依赖
安装相关的库可以点击start.py安装，也可以直接使用pip install指令
pip install -r 路径/requirements.txt

注：如果速度太慢可以考虑切换国内源（清华/阿里云/中科大/华为云，推荐清华源）
指令形式参考pip install ChromaDB -i https://pypi.tuna.tsinghua.edu.cn/simple/


# 4.3 配置模型服务
在启动后，可以进入设置界面配置LLM APIKey。

建议主模型选择一个具有一个比较贵的高质量长上下文的模型（DeepSeek V4/Gemini/gpt等等）,提取记忆用的小模型可以配置一些便宜的节省经费（例如：Qwen3 7B/Qwen/Qwen3.5-35B-A3B）。

多模态用于阅读文档图片等，这个API必须原生支持多模态MoE，国内的Qwen就是原生支持的，歌且各种微调的版本比较多，建议去中转站。

生图API必须模型支持图片生成功能，若生成图片则调用该API。

特殊技能的独立API可以在设置-技能工具管理器中单独设置，需要\@开启才可以调用。

## 五、页面与功能

### 5.1 首页
- **左栏上部**：对话角色立绘；图片来自 `roles_img/<角色名>/<角色名>-<情绪>.png`，
  按回复文本情绪自动切换。
- **左栏下部**：角色下拉（切换即换立绘）、群聊下拉（群聊内由角色设定卡判定谁最该回复，回复时切换对应角色立绘，不过功能有时略抽风）
  **随机主动对话**开关（打开后随机在 1-5 轮对话内主动发起一次对话）;
  **设置**字面意思不解释；
- **右栏对话窗口**：对话窗口，，用户的称呼与头像在设置中自定义
  **右栏选择**：新会话 / 历史侧栏（读取 `history/conversations/session_*.json`）；
- **风格配色**：半透明白色基色 + 下方设置中可以选择主题和背景图，也可开启高斯模糊（看起来更好看）。

### 5.2 设置页面
设置页面的设置内容有以下内容：
| 分节 | 功能 |
|------|------|
| 对话 API | 服务商 openai/deepseek/gemini/ollama/custom + 地址 + Key + 主/小模型 + 温度/Token |
| 多模态文档 | 视觉模型独立 API（地址/Key，留空复用主 API）；支持图片/PDF/Word/Excel/PPT/文本 |
| 多模态文档 | 视觉模型独立 API（地址/Key，留空复用主 API）；支持图片/PDF/Word/Excel/PPT/文本 |
| 资源路径 | 角色库 `./roles`、角色图 `./roles_img`、桌面形象 `./roles_desktop`、记忆存储 `./history`、数据目录 `./data`、用户头像 |
| 外观 | **主题**（列出 theme/ 目录图片，点击应用）+ **软件系统颜色与风格**（预设色板/自定义，实时换肤）+ 背景模糊 + **透明聊天窗口功能**（除按钮和 PNG 图外全部半透明） |
| 技能工具 | **打开技能工具管理器**（注册表风格技能库，风格与主界面完全一致、主题色实时同步） |
| 桌面形象 | 启用置顶桌宠 + 问候间隔 + 专注模式 + 悬浮对话 |
| 记忆训练 （这里用了德谬歌矩阵式的训练（x）三千万世的记忆训练方法凝聚于此！） | 导入标准格式的对话文件可以提炼记忆（此处导入对话文件talk.json用于训练不同角色的长期记忆，角色一一对应）|
| 遗忘（角色/群聊彻底抹除） | RT不解释 |

### 5.3 桌面宠物
目前无Live 2D的形象，所有切换仅仅是静态图/动态图的切换。
- 例如：拖动时 `角色名-run.gif`，静止 `角色名-standing.gif`，输入 `say1.gif`、输出 `say2.gif`、
  问候 `hello.gif`、休息 `sleep.gif`、专注 `work.gif`；
- **右键菜单**：设置日程、设置提醒、定时问候（每 30 分钟，使用了hello.gif）、小窗对话（200 字内，say1/say2.gif）、悬浮对话（鼠标悬停弹出）、提醒休息（22 点-次日 6 点每小时，使用了sleep.gif）、专注模式（选择窗口，如果时间没到这个窗口最小化了就提醒）、返回主页、隐藏桌宠、退出桌宠。

### 5.4 \@技能工具调用（思维链 / 联网搜索 / 技能，可自定义）
- 在聊天输入框输入 `\@`（先输入 `\` 再输入 `@`）即弹出技能/工具选择菜单，选中后插入
  `\@触发词` 标签；发送时自动解析并注入技能上下文（`skills/tools_list.json` +
  `skills/skilltools_information.json`）。
- **开关型工具**（可同时开启 0-2 个，弹窗中带 `[开关]` 标记）：
  - `\@thinking` 思维链（CoT）输出：逐步推理后再给结论；
  - `\@network` 联网搜索：优先引用最新网络信息并标注来源。
- **场景标签（技能skill）**：比如`\@code` 代码、`\@words` 文案、`\@med` 医学研究、`\@work` 办公、`\@education` 教育学习、`\@translate` 翻译、`\@train` 记忆训练、`\@image` 生成图片。
- 示例：
  组合示例：`\@thinking \@network \@research 帮我在pubmed中查一下最新的ADHD管理相关的循证医学论文`（需配置联网Search API）；
- **自定义设置**：可以点击设置点击打开技能管理器或运行 `python skilltools.py` 打开技能工具管理器，支持新建/修改/删除技能与开关，下拉可选「普通技能 / 开关工具」；
- 用户气泡显示时会自动隐藏 `\@` 指令（消息记录保留完整内容供 LLM 解析）。
- **关闭指令**：输入 `/closed`（或 `\closed`，兼容反斜杠写法）提交后**退出所有技能/开关**，后续消息不再套用任何技能语境，
  重新输入 `\@触发词`（如 `\@med`）可再次启用。
- **设置入口**：设置面板「技能工具」分节可一键打开技能工具管理器，
  其风格与主界面完全一致，主界面切换主题色时 skilltools 实时同步。
---

## 六、三级记忆管线（memory_pipeline.py）

| 层级 | 容器 | 策略 |
|------|------|------|
| 短期 | 内存 deque + `history/conversations/talk_*.json` | 达阈值裁切 |
| 长期 | ChromaDB `long_term`（cosine） | 遗忘曲线：低频过期删除、高频固化 |
| 重要 | ChromaDB `important`（cosine） | 永久豁免，只合并不删 |

流程：前置检索（ltm+important top-k）→ 小模型降噪（开关/超时/异常三重降级）→
组装上下文 → 输出后归档 → 达阈值裁切 → 小模型提炼（重要/长期）→
向量防重（极高相似丢弃 / 局部相似 Merge / 无关新增）→ 遗忘曲线定时清理。

---
## 七、角色库与立绘格式

### 角色卡 `roles/<角色名>/roles.json`
```json
{
  "name": "暮光",
  "personality": "温柔耐心、知识渊博、乐于助人",
  "system_prompt": "你是暮光……回复末尾用【emotion:】标注情感标签……，标签要求在emotion_list中选择",
  "character description": "角色的详细介绍与背景设定（可选）。对话时程序会自动注入系统提示词，角色需按此设定回答。",
  "default_emotion": "neutral",
  "emotion_list": ["neutral","happy","sad","angry","surprised"]
}
```

### 情绪映射 `roles/<角色名>/emotion.json`
```json
{
  "mappings": { "happy": ["开心","愉快","高兴"] },
  "keywords": { "happy": ["哈哈","太好了"] }
}
```
- 立绘文件命名：`roles_img/<角色名>/<角色名>-<情绪>.png`，默认 `roles_img/<角色名>/<角色名>-.png`
- 桌宠动图：`roles_desktop/<角色名>/<角色名>-<动作>.gif`（动作 ∈ standing/run/say1/say2/hello/sleep/work）

### 群聊 `roles/group.json`
```json
{
  "groups": {
    "讨论组": {
      "members": ["角色A", "角色B"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头"
    }
  }
}
```
发言者解析：正则兜底（`[角色A]: 内容`、`角色A：内容`、`角色A 说：内容`、`“内容”——角色A`），
解析失败回退上一个发言者，会话首条失败则随机选择群成员。

---
## 八、日记功能
日记功能包括了写日记与管理日记功能。这部分日记可以用于记忆提炼，日记可以插入图片和选定给哪个角色/角色组看。

## 九、常见问题

| 问题 | 解决 |
|------|------|
| `chromadb` 导入错误 | `pip install -r requirements.txt`；建议 Python 3.10/3.11 |
| 立绘不切换 | 检查 `roles_img/<角色名>/<角色名>-<情绪>.png` 命名和存在情况 |
| 桌宠背景不透明 | 确认 `WA_TranslucentBackground` + `FramelessWindowHint`；个别驱动 `--no-gpu` |
| LLM 无响应 | 检查 `data/config.json` api 段 / 环境变量；查看 `logs/app.log` |
| 记忆未入库 | 确认小模型配置有效、`history/chroma` 目录可写 |

