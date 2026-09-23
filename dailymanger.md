# dailymanger.md — 日记本子项目规划文档（daily.py）

> 生成日期：2026-08-07
> 项目：Celestia AssistantAI（主文件与主应用）
> 子项目：日记本 daily.py
> 目标：在主项目目录内新增一个独立运行的日记本程序，
>       界面风格与主程序完全一致（HTML5 响应式 / 主题色 #6c8ef5 / 无边框圆角半透明窗口），
>       数据全部落在主目录 /dailydata 下，全 UTF-8 编码，界面不含任何 emoji。

---

## 一、总体说明

daily.py 是主项目的子项目，可独立运行（`python daily.py`），仅依赖 PySide6。
它不修改 / 不删除主项目任何既有代码，只在主目录下新建 dailydata 数据目录。

功能层级：
- 一级界面（DailyMainWindow）：日记列表主页，展示每篇日记的标题、建立日期、上次修改日期，
  并提供单篇删除按钮（图片路径 /img/dailydel.png）；右上角提供「新建日记」与「删除文件」。
- 二级界面（DailyEditWindow）：单篇日记的编辑页，包含标题、正文、插入图片、插入文件、
  角色专属（多选角色 / 群组）与保存按钮。
- 文件管理对话框（FileManageDialog）：列出 /dailydata/dailyfile 下所有附件文件，可勾选删除。

---

## 二、文件与目录结构（每个路径的含义）

| 路径（相对主目录） | 类型 | 含义 |
|--------------------|------|------|
| `daily.py` | 文件 | 日记本子项目主程序（本项目的唯一代码文件） |
| `dailymanger.md` | 文件 | 本文档：路径 / 变量 / 数据结构说明与调整示例 |
| `dailydata/` | 目录 | 日记数据根目录，运行时自动创建 |
| `dailydata/dailytext/` | 目录 | 日记正文的 JSON 文件目录（每篇日记一个 `<id>.json`） |
| `dailydata/dailyfile/` | 目录 | 附件仓库：插入的图片与文件会被复制到这里，正文以相对路径引用 |
| `img/dailydel.png` | 文件 | 单篇日记「删除」按钮图标（主项目 img 目录内已有，直接复用） |
| `roles/` | 目录 | 主项目角色库：读取其中的角色目录（含 roles.json 者）供角色专属选择 |
| `roles/group.json` | 文件 | 主项目群聊预设：读取其中的群组名供角色专属选择 |
| `data/config.json` | 文件 | 主项目配置（可选读取主题色 ui.accent，失败时回退默认蓝 #6c8ef5） |

注意：所有相对路径均以主目录（`daily.py` 所在目录）为基准解析为绝对路径，
与主程序 config_loader.py 的 path() 行为保持一致。

---

## 三、日记 JSON 数据结构（每篇日记存为一个 .json 文件）

文件命名：`<id>.json`，id 形如 `20260807_220000_1a2b3c`（时间戳 + 6 位随机 hex）。
写入方式：原子写（先写 .tmp 再 os.replace），避免 JSON 写一半损坏；encoding 一律 UTF-8。

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

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 日记唯一标识，生成后不变，用于文件名与索引 |
| `title` | string | 日记标题（一级界面卡片标题） |
| `content_html` | string | 正文（QTextEdit 的 HTML）。图片以 `src="dailyfile/<文件名>"` 相对路径存储；打开显示时自动转绝对路径 |
| `created_at` | string | 首次保存时间（第一次点保存时写入，之后不再改动），格式 `YYYY-MM-DD HH:MM:SS` |
| `updated_at` | string | 最近修改时间（每次保存都会刷新），格式同上 |
| `allowed_roles` | list[string] | 角色专属（选填）：可阅读这篇日记的角色名 / 群组名。空列表 = 不限制。未来主程序记忆提取据此判断哪些角色可以了解该日记内容 |

---

## 四、代码模块与关键变量说明（每个变量的意义）

### 4.1 路径常量
| 变量 | 类型 | 意义 |
|------|------|------|
| `ROOT` | `Path` | 主目录绝对路径（`daily.py` 所在目录） |
| `DAILY_DIR` | `Path` | 数据根目录 `ROOT/dailydata` |
| `DAILY_TEXT_DIR` | `Path` | 日记 JSON 目录 `DAILY_DIR/dailytext` |
| `DAILY_FILE_DIR` | `Path` | 附件目录 `DAILY_DIR/dailyfile` |
| `IMG_DELETE` | `Path` | 删除按钮图标 `ROOT/img/dailydel.png` |

### 4.2 风格常量（与主程序 ui_manager.py / skilltools.py 完全一致）
| 变量 | 值 | 意义 |
|------|-----|------|
| `ACCENT` | `#6c8ef5` | 主题色（按钮渐变 / 选中态 / 边框高亮） |
| `ACCENT_HOVER` | `#8fb0ff` | 主按钮悬停渐变起点 |
| `ACCENT_PRESSED` | `#4a6fd4` | 主按钮按下背景 |
| `TEXT_DARK` | `#2b2b33` | 主文字色 |
| `TEXT_MID` | `#6b7280` | 次级文字色 |
| `TEXT_LIGHT` | `#9aa0ac` | 弱化文字色（状态 / 提示） |
| `CARD_BG` | `rgba(255,255,255,0.92)` | 卡片 / 面板背景 |
| `CARD_BG_ALT` | `rgba(246,247,252,0.9)` | 次面板背景 |
| `BORDER` | `#e5e7f0` | 边框色 |
| `RADIUS` | `14` | 面板圆角半径（px） |
| `DANGER` | `#d64545` | 危险操作（删除）用色 |
| `FONT_FAMILY` | `"Microsoft YaHei UI", "Microsoft YaHei", sans-serif` | 全局字体族 |
| `_TS_FMT` | `"%Y-%m-%d %H:%M:%S"` | 时间戳格式（created_at / updated_at 共用） |

### 4.3 工具函数
| 函数 | 返回值 | 意义 |
|------|--------|------|
| `_now_str()` | `str` | 当前时间字符串（按 _TS_FMT） |
| `_unique_id()` | `str` | 生成日记 id：`YYYYMMDD_HHMMSS_<6位hex>` |
| `_safe_name(s)` | `str` | 文件名消毒：只保留中文 / 字母 / 数字 / `-_.`，防止路径穿越 |
| `ensure_dirs()` | `None` | 确保 dailytext / dailyfile 目录存在 |
| `_hex_to_rgba(c, a)` | `str` | `#RRGGBB` → `rgba(r,g,b,a)` |
| `_lighten(c, n)` / `_darken(c, n)` | `str` | 颜色变亮 / 变暗（生成 QSS 用） |
| `_root_qss()` / `_scale_qss(qss, scale)` | `str` | 生成全局 QSS；按缩放系数缩放 px（font-size 保底 8px） |
| `apply_shadow(w, blur, y, alpha)` | `QGraphicsDropShadowEffect` | 给控件加 Material 投影 |

### 4.4 数据读写
| 函数 | 意义 |
|------|------|
| `list_roles()` | 扫描 `ROOT/roles/` 中含 `roles.json` 的目录名（升序） |
| `role_display(name)` | 角色显示名：优先 roles.json 的 display_name / name，缺省回退目录名 |
| `list_groups()` | 读取 `ROOT/roles/group.json` 的 `groups` 键名列表 |
| `list_diaries()` | 扫描 dailytext 下所有 json 并按其 created_at 倒序返回 |
| `read_diary(did)` | 按 id 读取单篇（损坏 / 不存在返回 None） |
| `save_diary(data)` | 保存日记：首次写 created_at，刷新 updated_at，原子写 json，返回是否成功 |
| `delete_diary(did)` | 删除单篇日记 json，返回是否成功 |
| `copy_into_dailyfile(src)` | 把外部文件复制进 dailyfile，返回相对路径 `dailyfile/<新名>`；失败返回 None |
| `list_dailyfiles()` | 列出 dailyfile 下所有文件（按名称排序） |
| `delete_dailyfile(name)` | 删除 dailyfile 中单个文件（名称经 _safe_name 防穿越） |
| `_rel_to_abs(html)` | 将 html 中 `src="dailyfile/..."` 转绝对 `file:///` 路径（显示用） |
| `_abs_to_rel(html)` | 将绝对路径 src 转回 `dailyfile/...` 相对路径（存储用） |

### 4.5 界面类
| 类 | 职责 |
|----|------|
| `DailyMainWindow(QMainWindow)` | 一级界面：日记卡片列表 + 右上角新建 / 删除文件 + 窗口控制 |
| `DailyEditWindow(QMainWindow)` | 二级界面：标题 / 正文 / 插图 / 插文件 / 角色多选 / 保存 |
| `FileManageDialog(QDialog)` | 附件文件管理：列出 dailyfile，勾选删除 |

DailyMainWindow 关键成员：
| 成员 | 意义 |
|------|------|
| `_accent` | 当前主题色（从主项目配置读取，失败回退默认蓝） |
| `_scale` | 界面缩放系数（默认 1.0，供后续 DPI 适配） |
| `_dragging` / `_drag_offset` | 标题栏拖拽移动窗口状态 |
| `_cards` | 当前日记卡片 widget 列表（刷新时清空重建） |
| `_scroll` | 日记卡片滚动区容器 |

DailyEditWindow 关键成员：
| 成员 | 意义 |
|------|------|
| `_diary_id` | 当前编辑的日记 id（新建时为 None，保存时生成） |
| `_title_edit` | 标题输入框 QLineEdit |
| `_editor` | 正文富文本 QTextEdit |
| `_role_list` | 角色专属多选列表 QListWidget（可勾选，含角色与群组条目） |

---

## 五、调整示例（如何修改本子项目）

### 示例 1：修改数据目录位置
原代码：
```python
DAILY_DIR = ROOT / "dailydata"
```
若想把日记数据放到主项目 history 子目录下，改为：
```python
DAILY_DIR = ROOT / "history" / "dailydata"
```
其余代码无需改动，因为 dailytext / dailyfile 均由 DAILY_DIR 推导：
```python
DAILY_TEXT_DIR = DAILY_DIR / "dailytext"
DAILY_FILE_DIR = DAILY_DIR / "dailyfile"
```

### 示例 2：修改时间戳显示格式
原代码：
```python
_TS_FMT = "%Y-%m-%d %H:%M:%S"
```
若想带星期，改为：
```python
_TS_FMT = "%Y-%m-%d %H:%M:%S %A"
```
保存与展示会自动同步（created_at / updated_at 均使用该格式）。

### 示例 3：修改主题色
daily.py 启动时会尝试读取主项目 data/config.json 的 `ui.accent`；
若读取失败或想固定颜色，修改：
```python
ACCENT = "#6c8ef5"   # 改为其它色值，例如 "#7c5cf5"
```
所有按钮渐变 / 选中态 / 边框高亮都会随之变化（QSS 由 _root_qss 动态生成）。

### 示例 4：调整角色专属可选项
原代码从 `ROOT/roles` 扫描角色、从 `ROOT/roles/group.json` 读取群组。
若只想开放部分角色，可在 `list_roles()` 内过滤：
```python
BLOCKED = {"某个角色目录名"}
out = [n for n in out if n not in BLOCKED]
```

### 示例 5：正文图片显示方式
插入图片后正文存的是相对路径：
```html
<img src="dailyfile/20260807_220000_x9y8z7_照片.png">
```
打开日记时调用 `_rel_to_abs(content_html)` 转为：
```html
<img src="file:///<项目根>/dailydata/dailyfile/20260807_220000_x9y8z7_照片.png">
```
保存前调用 `_abs_to_rel(...)` 还原为相对路径，保证 json 可移植。

