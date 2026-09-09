# roleset.md — 角色设计完整指南

> 本文档说明设计一个角色需要的所有内容：角色卡、情绪映射、对话立绘、
> 桌宠动图、群聊配置、触发对话等的**放置路径**与**目的作用**。

---

## 一、角色需要准备的全部文件

| # | 文件 | 路径 | 用途 |
|---|------|------|------|
| 1 | 角色卡 | `roles\<角色名>\roles.json` | 名字/性格/系统提示词/情感列表 |
| 2 | 情绪映射 | `roles\<角色名>\emotion.json` | 情感关键词 → 情感标签 |
| 3 | 对话立绘（默认） | `roles_img\<角色名>\<角色名>-.png` | 主界面左侧角色形象（默认状态） |
| 4 | 对话立绘（各情感） | `roles_img\<角色名>\<角色名>-<情绪>.png` | 按回复情绪自动切换立绘 |
| 5 | 桌宠动图（静止） | `roles_desktop\<角色名>\<角色名>-standing.gif` | 桌宠默认静止动作 |
| 6 | 桌宠动图（拖动） | `roles_desktop\<角色名>\<角色名>-run.gif` | 拖动时动作 |
| 7 | 桌宠动图（说话） | `roles_desktop\<角色名>\<角色名>-say1.gif` / `-say2.gif` | 输入/输出时动作 |
| 8 | 桌宠动图（问候） | `roles_desktop\<角色名>\<角色名>-hello.gif` | 定时问候动作 |
| 9 | 桌宠动图（休息） | `roles_desktop\<角色名>\<角色名>-sleep.gif` | 夜间休息提醒动作 |
| 10 | 桌宠动图（专注） | `roles_desktop\<角色名>\<角色名>-work.gif` | 专注模式劝导动作 |
| 11 | 群聊配置（可选） | `roles\group.json` | 将角色加入群聊组 |

---

## 二、放置路径与作用说明

### 1. 角色卡 `roles\<角色名>\roles.json`
```json
{
  "name": "风宝",
  "personality": "活泼开朗、元气满满",
  "system_prompt": "你是风宝……回复末尾用【】标注情感标签（可选：开心/平静/难过/生气/惊讶）。",
  "default_emotion": "happy",
  "emotion_list": ["neutral", "happy", "sad", "angry", "surprised"]
}
```
- `name`：必须与文件夹名一致；
- `system_prompt`：决定角色性格、说话方式、触发对话的设定；
- `default_emotion`：无情感标签时的默认立绘情感。

### 2. 情绪映射 `roles\<角色名>\emotion.json`
```json
{
  "mappings": { "happy": ["开心", "愉快", "高兴"] },
  "keywords":  { "happy": ["哈哈", "太好了"] }
}
```
- `mappings`：回复文本中【开心】等标签 → 情感；
- `keywords`：用户输入关键词 → 情感（用于快速判断）。

### 3. 对话立绘 `roles_img\<角色名>\`
- 命名：`<角色名>-<情绪>.png`，默认 `<角色名>-.png`；
- 作用：主界面左侧角色形象，随对话情绪自动切换（渐隐过渡）；
- 支持 `.png/.jpg/.jpeg/.webp`。

### 4. 桌宠动图 `roles_desktop\<角色名>\`
- 命名：`<角色名>-<动作>.gif`，动作 ∈ standing/run/say1/say2/hello/sleep/work；
- 作用：透明置顶桌宠各状态动作，切换带渐隐过渡，支持滚轮缩放；
- 仅支持 `.gif`。

### 5. 群聊 `roles\group.json`
```json
{
  "groups": { "讨论组": { "members": ["风宝", "树庭学者"], "speaker_hint": "回复必须以 [角色名]: 内容 开头" } }
}
```
- 作用：群聊模式下由 AI 依据角色卡判定谁最该回复，并切换对应角色立绘。

---

## 三、触发对话（主动问候）

- 随机主动对话开关打开后，会调用**角色卡 system_prompt** + **长期记忆检索结果**
  生成符合该角色性格、结合记忆内容的主动问候（见 ui_manager `_on_pet_proactive`）；
- 定时问候（桌宠右键菜单开启）每 30 分钟触发，同样使用角色特征；
- 若某角色不需要主动问候，可在 `roles.json` 中不启用随机主动开关。

---

## 四、角色制作步骤速查

```
1. roles\新建角色文件夹（名 = 角色名）
2. 写 roles.json（角色卡） + emotion.json（情绪映射）
3. roles_img\角色名\ 放默认立绘 角色名-.png + 各情感立绘
4. roles_desktop\角色名\ 放 7 个动作 gif
5. （可选）group.json 加入群聊
6. 启动应用 → 左栏选择该角色 → 立即生效
```

---

## 五、命名示例（角色名 = Rainbow_Dash）

```
roles\Rainbow_Dash\roles.json
roles\Rainbow_Dash\emotion.json
roles_img\Rainbow_Dash\Rainbow_Dash-.png
roles_img\Rainbow_Dash\Rainbow_Dash-happy.png
roles_img\Rainbow_Dash\Rainbow_Dash-sad.png
roles_desktop\Rainbow_Dash\Rainbow_Dash-standing.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-run.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-say1.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-say2.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-hello.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-sleep.gif
roles_desktop\Rainbow_Dash\Rainbow_Dash-work.gif
```
