# groupset.md — 群聊功能设置与实现完整指南

> 本文档说明「群聊」功能**怎么实现**、**怎么设置**，并以两个官方示例为例演示：
> - **例 1：朋友群聊**（4 个角色：Twilight_Sparkle、Rainbow_Dash、Fluttershy、Applejack）
> - **例 2：挚友群聊**（2 个角色：Twilight_Sparkle、Rainbow_Dash）

---

## 一、功能简介

群聊模式下，程序自动完成三件事：

| # | 能力 | 说明 |
|---|------|------|
| 1 | **点名触发** | 用户消息中出现群成员的名字（角色名 / 显示名 / 中文别名），就**由该角色回复**；出现多个名字时，**取文本中最先出现的那个**。 |
| 2 | **AI 判定谁最该发言** | 未点名时，程序先调用一次 API **内部判定**哪一位成员最该回复这条消息；**判定结果只用于选择发言者，绝不输出到对话中**。判定失败则自动回退。 |
| 3 | **桌宠跟随最后发言角色** | 群聊每产生一条新回复，桌面宠物就切换为**发送这条消息的角色**（使用该角色的桌宠动图），与群聊画面保持一致。 |

判定优先级：

```
用户消息点名（角色名/别名）  ──命中──▶ 该角色直接发言
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

---

## 二、功能实现原理（代码层）

### 2.1 群聊组配置 —— `roles/group.json`

群聊组在 `roles/group.json` 中声明，格式：

```json
{
  "groups": {
    "组名": {
      "members": ["角色名1", "角色名2", "..."],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头",
      "aliases": { "中文昵称/别名": "角色名" }
    }
  }
}
```

| 字段 | 类型 | 是否必需 | 说明 |
|------|------|---------|------|
| `members` | 数组 | 必需 | 群成员，必须是 `roles/` 下已存在的角色文件夹名 |
| `speaker_hint` | 字符串 | 可选 | 提示模型用 `[角色名]: 内容` 开头，方便程序识别发言者 |
| `aliases` | 对象 | 可选 | 中文昵称/别名 → 角色名。用于「点名触发」时识别中文名字（如「云宝」→ Rainbow_Dash） |

> 界面左栏「群聊」下拉会自动列出所有组名（以及内置的「群聊（全部角色）」）。
> 选择组名即进入该群聊；选择「单聊」则回到单角色对话。

### 2.2 点名触发 —— `role_manager.py`

新增方法 `mentioned_member(text, members, aliases)`：

- 把「触发词」收集为一张表：每个成员的角色名、`display_name`（角色卡可配）、
  下划线名空格形式（`Twilight_Sparkle` → `Twilight Sparkle`）、以及 `aliases` 中的别名；
- 在用户输入中逐个查找触发词位置，**取出现位置最靠前的那个**（多个名字取第一个）；
- 返回对应的标准角色名；未命中返回空串。

```python
def mentioned_member(self, text, members, aliases=None) -> str:
    # 触发词 → 标准角色名
    trigger_map = {}
    for m in members:
        trigger_map[m] = m
        dn = self.display_name(m)
        if dn and dn.strip():
            trigger_map[dn.strip()] = m
        if "_" in m:
            trigger_map[m.replace("_", " ")] = m
    if aliases:
        trigger_map.update(aliases)
    # 找出文本中第一个出现的触发词
    hits = [(text.find(w), c) for w, c in trigger_map.items() if w and text.find(w) != -1]
    return min(hits)[1] if hits else ""
```

### 2.3 AI 内部判定谁最该发言 —— `ui_manager.py`（ChatWorker）

`ChatWorker._judge_group_speaker(prompt, members)`：

- 用 `role_manager.member_intro(members)` 生成成员简档（名字 + 性格）；
- 组装一段**判定 prompt**，通过 `LLMClientPool.chat_complete(..., kind="small", max_tokens=16, timeout=15)`
  调用 API，要求**只返回一个成员名**；
- 该返回值**只用于确定发言者，不进入对话气泡、不通过任何信号展示**；
- 调用失败、超时或返回非法名字 → 返回空串，由上层回退。

```python
def _judge_group_speaker(self, prompt, members) -> str:
    intro = self._roles.member_intro(members)
    sys_p = (
        "你是一个群聊发言判定器。下面是当前群聊的成员及性格：\n"
        f"{intro}\n\n用户的最新消息：\n{prompt}\n\n"
        "请判断这条消息最应该由哪位成员回复。"
        "只输出该成员的名字，不要输出任何其他文字。"
    )
    raw = self._pool.chat_complete(
        [{"role": "system", "content": sys_p}],
        kind="small", temperature=0.1, max_tokens=16, timeout=15)
    # 匹配返回合法成员名，否则返回空串
    ...
```

### 2.4 系统提示词约束 —— `ui_manager.py`（ChatWorker._build_system_prompt）

群聊时向系统提示词注入：

1. 群成员清单 + `speaker_hint`；
2. **【群聊发言规则】**：根据本条问题判断最应该由哪一位成员发言，
   直接以 `[成员名]: 回复内容` 输出，**不要输出"谁最应该发言"之类的判断过程/说明文字**；
3. **【发言者指定】**：当点名单名 / 判定成功后，额外指定「本条消息已确定由 X 发言，
   请以 [X]: 开头直接输出 X 的回复，不要改为其他成员」；
4. **【硬性要求】只能有一位成员发言**（2026-09-23 用户需求「朋友群聊的时候只能有
   一个最合适的角色回复，而不是多个角色」）：明确禁止输出第二位成员的名字或台词，
   其他成员的反应请省略，或并入这一位成员的话里（用第一人称提到即可）。

### 2.5 回复规范化与发言者解析

- **点名/判定成功**：`_normalize_group_reply(clean, speaker, members)`
  → 内部走 `ChatWorker._first_speaker_only()`：先剥掉开头的成员前缀，再**在第二位发言人处截断**；
- **只留一位发言人**（2026-09-23）：模型一次写多人（`[A]: …` 换行 `[B]: …`）时**只保留第一位**，
  其他成员的段落**整段丢弃** —— 气泡、会话记录、记忆归档用的都是这份结果，不存多余角色；
  同一人分段重复写名字只剥前缀、内容继续保留；名字按归一化比较（下划线/空格/中文别名等价）。
  显示层（`MainWindow._display_text`）与主动问候共用同一函数，流式期间也不会把第二位显示出来。
- **判定失败**：用原有 `RoleManager.resolve_speaker(text, members, prev_speaker)`
  解析模型输出里的 `[角色名]:` 前缀（兼容 `[A]:`、`A：`、`A 说：`、引号句等）；
- **再失败**：回退**上一个发言者**；会话首条则**随机选一名成员**。

### 2.6 桌宠显示最后发言角色 —— `signal_bus.py` + `pet_manager.py`

- `signal_bus.py` 新增信号：`speaker_switched = Signal(str)`（群聊发言者切换事件）；
- `ui_manager.py`：
  - `_on_finished()`：群聊回复完成后 `bus.speaker_switched.emit(speaker)`；
  - `_on_group_changed()`：进入群聊时 `_sync_pet_to_last_speaker()`，
    从该群**最近一次会话**中读取最后一条消息的发言角色并同步桌宠；
  - `_show_proactive()`：群聊随机主动问候也同步桌宠为该角色；
- `pet_manager.py`：`bus.speaker_switched.connect(self._on_role_switched)`，
  复用角色切换逻辑（`_role = speaker`，并切到 `standing.gif`）。

### 2.7 涉及文件一览

| 文件 | 改动/作用 |
|------|-----------|
| `roles/group.json` | 新增群聊组（members / speaker_hint / aliases） |
| `role_manager.py` | 新增 `mentioned_member` / `member_intro` / `group_aliases` |
| `signal_bus.py` | 新增 `speaker_switched` 信号 |
| `ui_manager.py` | ChatWorker 判定链路 + 系统提示词 + 正文规范化；主窗口转发信号 |
| `pet_manager.py` | 订阅 `speaker_switched`，桌宠跟随最后发言角色 |

---

## 三、如何设置（操作步骤）

### 3.1 准备角色

群聊成员必须是 `roles/` 下**已存在**的角色文件夹。例如本项目已就绪：

| 角色名 | 文件夹 | 立绘 | 桌宠动图 |
|--------|--------|------|----------|
| Twilight_Sparkle | `roles/Twilight_Sparkle/` | `roles_img/Twilight_Sparkle/` | `roles_desktop/Twilight_Sparkle/` |
| Rainbow_Dash | `roles/Rainbow_Dash/` | `roles_img/Rainbow_Dash/` | `roles_desktop/Rainbow_Dash/` |
| Fluttershy | `roles/Fluttershy/` | `roles_img/Fluttershy/` | `roles_desktop/Fluttershy/` |
| Applejack | `roles/Applejack/` | `roles_img/Applejack/` | `roles_desktop/Applejack/` |

### 3.2 编写群聊组 —— `roles/group.json`

用 **UTF-8 编码**保存。一个组一个对象；`members` 里的名字必须与角色文件夹名一致。
`aliases` 用来支持中文昵称点名（如「云宝」→ Rainbow_Dash），可留空 `{}`。

### 3.3 启动并使用群聊

1. 启动程序：`python start.py`（或 `python main.py`）；
2. 在**左栏「群聊」下拉**中选择组名（如「朋友群聊」）；
3. 在输入框发送消息：
   - **点名**：输入「云宝，明天要不要去飞行表演？」→ Rainbow_Dash 回复；
   - **不点名**：输入「大家周末有什么计划呀？」→ API 内部判定最该发言的成员回复；
4. 打开桌宠（左栏「桌宠：开启」或主界面托盘），群聊每条新回复后
   **桌宠自动变成发送该消息的角色**。

### 3.4 完整字段速查（group.json）

```json
{
  "groups": {
    "组名": {
      "members": ["角色A", "角色B"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头",
      "aliases": {
        "角色A的中文昵称": "角色A",
        "角色B的中文昵称": "角色B"
      }
    }
  }
}
```

> 提示：
> - 不改代码即可增删群聊组、调整成员、增删别名；
> - `speaker_hint` 建议保留默认「回复必须以 [角色名]: 内容 开头」，
>   它是发言者解析的兜底格式；
> - 想让**全部角色**一起群聊（无需在 group.json 配置），选择下拉中的「群聊（全部角色）」。

---

## 四、示例一：朋友群聊（4 个角色）

### 4.1 配置（已内置在 `roles/group.json`）

```json
{
  "groups": {
    "朋友群聊": {
      "members": ["Twilight_Sparkle", "Rainbow_Dash", "Fluttershy", "Applejack"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头",
      "aliases": {
        "暮光闪闪": "Twilight_Sparkle",
        "紫悦": "Twilight_Sparkle",
        "云宝黛茜": "Rainbow_Dash",
        "云宝": "Rainbow_Dash",
        "小蝶": "Fluttershy",
        "柔柔": "Fluttershy",
        "苹果杰克": "Applejack",
        "阿杰": "Applejack",
        "苹果嘉儿": "Applejack"
      }
    }
  }
}
```

### 4.2 效果演示

**① 点名触发（单名）**
> 用户：`Twilight_Sparkle，帮我解释一下友谊魔法是什么？`
> 输出：`[Twilight_Sparkle]: 友谊魔法是当朋友们齐心协力时产生的奇妙力量，我来详细讲讲…〖开心〗`
>
> → 左侧立绘切换到 Twilight_Sparkle；桌宠变成 Twilight_Sparkle。

**② 点名触发（中文别名）**
> 用户：`云宝，周末要不要去参加飞行大赛？`
> 输出：`[Rainbow_Dash]: 那还用说！我可是小马国最快的飞马！〖兴奋〗`

**③ 多个名字取第一个**
> 用户：`Applejack 你的苹果派真好吃，Fluttershy 你也尝尝？`
> 输出：`[Applejack]: 嘿嘿，这可是我家的招牌！阿杰我摘了最新鲜的苹果…〖开心〗`
>
> → 因为「Applejack」在文本中更靠前，所以由 Applejack 回复（而不是 Fluttershy）。

**④ 未点名 → AI 判定谁最该发言（判定不输出）**
> 用户：`大家最近有什么开心的事吗？`
>
> 程序先调用一次 API 内部判定（结果只用于选人，不显示）→ 例如判定 `Fluttershy`，
> 然后由 Fluttershy 回复：
> 输出：`[Fluttershy]: 我最近救助了一只受伤的小鸟，它已经能飞啦，真的很开心〖开心〗`
>
> → 左侧立绘 + 桌宠同步为 Fluttershy。

**⑤ 桌宠跟随最后发言**
> 连续对话时，每一条回复的角色不同，桌宠会依次变成对应的角色动图；
> 重新进入「朋友群聊」时，桌宠恢复为该群最近一次会话的最后发言角色。

---

## 五、示例二：挚友群聊（2 个角色）

### 5.1 配置（已内置在 `roles/group.json`）

```json
{
  "groups": {
    "挚友群聊": {
      "members": ["Twilight_Sparkle", "Rainbow_Dash"],
      "speaker_hint": "回复必须以 [角色名]: 内容 开头",
      "aliases": {
        "暮光闪闪": "Twilight_Sparkle",
        "紫悦": "Twilight_Sparkle",
        "云宝黛茜": "Rainbow_Dash",
        "云宝": "Rainbow_Dash"
      }
    }
  }
}
```

### 5.2 效果演示

**① 点名触发**
> 用户：`暮光闪闪，你说的那本书借我看看呗？`
> 输出：`[Twilight_Sparkle]: 当然可以，就在金橡树图书馆二楼，我帮你找找〖开心〗`

**② 未点名 → AI 判定**
> 用户：`今天天气这么好，谁陪我出去飞一圈？`
>
> 程序内部判定 → `Rainbow_Dash`，回复：
> 输出：`[Rainbow_Dash]: 我！我！云宝我早就想飞了，出发！〖兴奋〗`

**③ 桌宠跟随**
> 挚友群聊里只有两位成员，桌宠会在 Twilight_Sparkle ↔ Rainbow_Dash 之间跟随切换。

---

## 六、常见问题（FAQ）

| 问题 | 解答 |
|------|------|
| 点名没生效？ | 确认名字与 `members` 中的角色名完全一致，或在 `aliases` 里配了中文昵称；别名/角色名默认区分大小写。 |
| 判定功能会不会很慢/很费？ | 判定使用**小模型**（`small_model`，可独立配端点），`max_tokens=16`、超时 15 秒，非常轻量；判定失败自动跳过，不影响正常对话。 |
| 判定结果会显示出来吗？ | **不会**。判定 API 的返回值只用于内部选择发言者，不进入气泡、不通过任何信号展示；系统提示词也要求模型不要输出"谁该发言"的判断过程。 |
| 群聊回复格式？ | 群聊正文统一为 `[角色名]: 内容`。点名/判定成功时由程序规范化，判定失败时解析模型自带前缀。 |
| 桌宠不变怎么办？ | 确认桌宠已开启（左栏「桌宠：开启」）；群聊回复完成后程序会发 `speaker_switched` 信号，桌宠据此切换。若关闭桌宠后重新打开，需重新选择群聊即可同步。 |
| 想再加一个群？ | 在 `roles/group.json` 的 `groups` 里加一组即可，无需改代码；重启或重新切换下拉即生效。 |
| 群成员没立绘/动图会怎样？ | 缺立绘显示占位灰底，缺动图回退 `standing.gif`；建议按 `helpset.md` 补齐 `roles_img/<角色名>/` 与 `roles_desktop/<角色名>/`。 |

---

## 七、验证

已实测确认（原自动化测试脚本已随发布清理移除，此处保留当时结论）：
- 两个群聊组解析、成员/别名正确；
- 点名触发（单名/多名取第一个/中文别名/未点名）正确；
- API 判定链路（含失败回退）正确，判定结果不进入正文；
- `speaker_switched` 信号与桌宠跟随最后发言角色正确；
- 主窗口群聊下拉显示两个组，GUI 启动/设置面板正常，原有冒烟测试无回归。

