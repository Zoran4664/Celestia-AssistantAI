# set.md — 本程序使用说明

> 本文档说明每个文件夹的作用、如何添加角色、以及角色图片的命名规范。

---

## 一、文件夹作用一览

```
主文件与主应用\
├── main.py                 主程序入口（python main.py 启动）
├── start.py                启动器（自动检查依赖，python start.py 启动）
├── config_loader.py        配置读取模块（勿手动修改）
├── signal_bus.py           全局信号总线（模块间通信）
├── llm_client.py           大模型客户端（OpenAI 兼容：DeepSeek/Gemini/Ollama）
├── memory_pipeline.py      三级记忆管线（短期/长期/重要）
├── role_manager.py         角色库解析模块
├── ui_manager.py           主界面与设置面板
├── pet_manager.py          桌面宠物引擎
├── requirements.txt        依赖清单
├── README.md               完整设计文档
├── set.md                  本文档
├── data\                   · 程序数据目录
│   ├── config.json            全局设置（API/模型/路径/记忆/桌宠/外观）
│   └── user_avatar.png         用户头像
├── roles\                  · 角色库（角色卡存放处）
│   ├── group.json             群聊组配置
│   └── 角色名\                 每个角色一个文件夹（名 = 角色名）
│       ├── roles.json         角色卡（名字/性格/系统提示词）
│       └── emotion.json       情绪关键词映射
├── roles_img\              · 对话立绘（主界面左侧角色形象）
│   └── 角色名\
│       ├── 角色名-.png            默认立绘
│       └── 角色名-happy.png      情绪立绘
├── roles_desktop\          · 桌宠动图
│   └── 角色名\
│       ├── 角色名-standing.gif    静止
│       ├── 角色名-run.gif         拖动
│       ├── 角色名-say1.gif        输入
│       ├── 角色名-say2.gif        输出
│       ├── 角色名-hello.gif       问候
│       ├── 角色名-sleep.gif       休息
│       └── 角色名-work.gif        专注
├── theme\                 · 背景图片（default.png 等，可在设置中选用）
├── skills\                · 技能工具库（\@技能 唤醒）
│   ├── tools_list.json            技能目录（name/trigger/kind/aliases/category/enabled）
│   └── skilltools_information.json 技能详情（description/prompt_template/parameters）
├── history\               · 记忆与对话存档
│   ├── chroma\              长期/重要记忆（ChromaDB 向量库，自动生成）
│   └── conversations\       短期对话与历史会话（自动生成）
├── utils\                 日志与线程工具（勿手动修改）
└── tools\                 资源生成器 / 冒烟测试（开发者工具）
```

---

## 二、如何添加一个角色

### 第 1 步：创建角色文件夹
在 `roles\` 下新建文件夹，文件夹名就是角色名（建议英文或简短中文），例如 `roles\550W\`。

### 第 2 步：编写角色卡 `roles\550W\roles.json`
```json
{
  "name": "550W",
  "personality": "心思缜密、冷静谨慎",
  "system_prompt": "你是550W，一个心思缜密人工智能，说话冷静谨慎。回复末尾用【】标注情感标签（可选：开心/平静/难过/生气/惊讶）。",
  "default_emotion": "happy",
  "emotion_list": ["neutral", "happy", "sad", "angry", "surprised"]
}
```

### 第 3 步：编写情绪映射 `roles\550W\emotion.json`
```json
{
  "mappings": {
    "neutral": ["平静", "中性"],
    "happy": ["开心", "愉快", "高兴"],
    "sad": ["难过", "悲伤"],
    "angry": ["生气", "愤怒"],
    "surprised": ["惊讶", "震惊"]
  },
  "keywords": {
    "happy": ["哈哈", "太好了"],
    "sad": ["难过", "伤心"]
  }
}
```

### 第 4 步：添加角色图片
- **对话立绘**：放入 `roles_img\550W\`，命名规则见下表；
- **桌宠动图**：放入 `roles_desktop\550W\`，动作图命名见下表；
- **可选**：在 `roles\group.json` 中加入群聊组即可参与群聊。

---

## 三、图片命名规范（重要）

### 对话立绘 `roles_img\<角色名>\`
| 文件名 | 用途 |
|--------|------|
| `角色名-.png` | 默认立绘（无对应情绪图时使用） |
| `角色名-neutral.png` | 平静 |
| `角色名-happy.png` | 开心 |
| `角色名-sad.png` | 难过 |
| `角色名-angry.png` | 生气 |
| `角色名-surprised.png` | 惊讶 |

> 支持 `.png / .jpg / .jpeg / .webp`；情绪切换带渐隐过渡。

### 桌宠动图 `roles_desktop\<角色名>\`
| 文件名 | 动作 |
|--------|------|
| `角色名-standing.gif` | 静止（默认） |
| `角色名-run.gif` | 拖动时 |
| `角色名-say1.gif` | 输入时 |
| `角色名-say2.gif` | 输出时 |
| `角色名-hello.gif` | 定时问候 |
| `角色名-sleep.gif` | 夜间休息提醒 |
| `角色名-work.gif` | 专注模式 |

> 仅支持 `.gif`；全部动作切换为渐隐过渡。

### 命名示例（角色名 = 550W）
```
roles_img\550W\550W-.png
roles_img\550W\550W-happy.png
roles_img\550W\550W-sad.png

roles_desktop\550W\550W-standing.gif
roles_desktop\550W\550W-run.gif
roles_desktop\550W\550W-hello.gif
```

---

## 四、群聊配置 `roles\group.json`
```json
{
  "groups": {
    "讨论组": {
      "members": ["默认助手", "550W"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头"
    }
  }
}
```
群聊中由 AI 依据角色卡判定谁最该回复，并切换对应角色立绘与情绪。

---

## 五、常见路径配置（设置面板中可改）
| 设置项 | 默认路径 | 说明 |
|--------|----------|------|
| 角色库 | `./roles` | 角色卡目录 |
| 角色图 | `./roles_img` | 对话立绘目录 |
| 桌面形象 | `./roles_desktop` | 桌宠动图目录 |
| 记忆存储 | `./history` | 长期记忆与历史对话 |
| 应用图标 | （留空） | 窗口/托盘 ICO 小图标路径 |
| 用户头像 | `./data/user_avatar.png` | 对话中用户头像 |

---

## 六、启动方式
```bash
python start.py            # 推荐：启动器（自动检查依赖）
python main.py             # 直接启动
python start.py --setup    # 先安装依赖再启动
```