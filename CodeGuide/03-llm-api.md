# 03 · 接口层：`llm_client.py` 与 `api_vault.py`

> 改模型/接口/流式解析/思维链提取/多模态/生图/联网搜索时读这本。
> 行号基线：2026-09-19（`llm_client.py` 678 行；`api_vault.py` 约 550 行）。

---

## 1. `llm_client.py`（678 行）总览

**作用**：OpenAI 兼容客户端池 `LLMClientPool`（main / small / vision / image 四种角色）+ 异步 `LLMWorker(QThread)`；含多模态（视觉/文档/生图）、多引擎联网搜索、宽松 JSON 解析。

**被谁用**：`main`、`ui_manager`、`pet_manager`、`focus_assistant`、`auto_diary`、`novel_learn`、`memory_pipeline`、`memory_compile`、`memory_dream`、`skill_install`、`skill_import`、`skill_eval`、`skillspub_manager`。

### 1.1 关键符号

| 符号 | 行号 | 作用 |
|---|---|---|
| `LLMClientPool.__init__` / `instance()` | 49 / 53 | 单例；持有 `_cfg`、`_clients` 缓存 |
| `client(kind, timeout)` | 61 | 按角色取/建客户端（缓存键 = kind+base+timeout） |
| `reconfigure()` | 76 | **清空客户端缓存**（配置变更后必须调用） |
| `client_custom(base_url, api_key, timeout)` | 80 | 任意端点（缓存键含 Key 指纹） |
| `chat_stream_custom` / `chat_complete_custom` | 101 / 144 | 自定义端点流式 / 非流式 |
| `_model(kind)` / `_endpoint(kind)` | 166 / 175 | 角色 → 模型名 / (base, key)，支持独立端点 |
| `_build_sampling(sampling, temperature, max_tokens)` | 188 | **拆分标准字段与 extra_body**（`reasoning_effort` 等非标准键走 extra_body，209-213） |
| `_usage_from_response(usage_obj)` | 217 | 规范化 usage（含缓存命中） |
| `_iter_stream(client, model_name, messages, native, extra, reasoning_cb, usage_cb)` | 278 | **统一流式迭代**（含 usage 重试） |
| `_THINK_OFF_EXTRAS` / `_thinking_off_enabled()` | 385 | 关闭思考的厂商参数候选 / 开关（`api.small_thinking_off`） |
| `_complete_with_fallback(client, model_name, messages, temperature, max_tokens, cache_key)` | 392 | **非流式补全兜底**：关思考（多厂商参数试探 + 端点缓存）→ 放大预算重试 |
| `chat_stream` / `chat_complete` | 466 / 483 | 主对话流式 / 非流式（非流式统一走上面兜底） |
| `_extract_document_text(path)` | 873 | PDF/Word/Excel/PPT/TXT 文本提取 |
| `_has_document_text(text)` | 1008 | **提取结果里是否真的有正文**（先剔 `[第 N 页]` 页标记）——判定扫描件用，见 1.6 坑 |
| `_pdf_page_images(path, max_pages, dpi)` | 1182 | PDF 每页栅格化为 PNG（临时目录）：PyMuPDF → pypdfium2 自动挑选；都缺则返回空 |
| `_vision_read_document(path, question, max_pages)` | 1245 | **扫描件走多模态**：逐页交视觉模型 OCR，返回带页标记文本 |
| `AttachmentNoTextError` | 349 | 无文字层且视觉识别也失败时抛的异常（上层据此提示用户） |
| `attachment_text(file_path, max_chars)` | — | **附件注入对话的唯一入口**：文档给**原文**（截断），图片走视觉模型（发送前自动缩放） |
| `_prepare_image_data_url(path)` / `VISION_MAX_EDGE` / `VISION_MAX_BYTES` | — | 图片送模型前缩放压缩（最长边 1568 / 4MB；见 1.6） |
| `document_describe` / `vision_describe` | 464 / 494 | 文档理解 / 图片理解（base64，兼容保留） |
| `image_generate(prompt, size, base_url, api_key, model)` | 521 | 生图，返回本地路径；**自动适配 OpenAI 兼容 / 异步任务两协议**，详见 1.6 |
| `_search_items` / `_search_provider_items` / `_search_chain` / `web_search` / `web_search_custom` | — | 多引擎搜索 + fallback（默认 30s）；**含百度千帆 POST 协议**，详见 1.5 |
| `parse_json_loose(text)` | 656 | 从散文/代码块里抠首个 `{...}` |
| `LLMWorker` / `LLMWorker.run` | 681 / 710 | QThread 异步；信号 `stream_chunk/request_finished/request_error`(687-689) |

**模块级**：`logger`(24)、`_HAS_QT`(33)（无 Qt 时不定义 `LLMWorker`）、`OpenAI` 导入保护(27-29)；类常量 `MODEL_KINDS`(47)、`_TEXT_SUFFIXES`(405)、`_DOC_SUFFIXES`(407)、`_IMAGE_SUFFIXES`(409)。

### 1.2 思维链（reasoning）提取位置 —— **重点**

```python
# llm_client.py:324-329（在 _iter_stream 内）
delta = chunk.choices[0].delta
r = getattr(delta, "reasoning_content", None)      # 324-325
if not r:
    r = getattr(delta, "thinking", None)           # 326-327  兼容另一套字段名
if r:
    reasoning_cb(r)                                # 328-329  回调到主线程
```

- 回调链路：`ui_manager.ChatWorker.reasoning` 信号(430) → `_reasoning_cb`(898-901) → `reasoning_cb=` 传入(941/948) → `MainWindow._on_reasoning`(5588) → 思维链块 `_render_thinking_body`(5598)。
- 显示开关：设置里的 `ui.show_thinking` **或**本轮用了 `\@thinking`（`MainWindow._thinking_visible()` / `_msg_thinking_visible(msg)`）。2026-09-21 前只看配置项 → `\@thinking` 也一并被隐藏（用户反馈「指令无法开启思维链显示」）。
- 改「什么算思考」应在 **`ui_manager` 的 `split_cot_stream`（文本协议层）**，改「模型原生 reasoning 字段」则在本文件 324-329。

### 1.3 配置键（本文件读的）

| 路径 | 键 | 默认 | 说明 |
|---|---|---|---|
| `chat` | `temperature` / `max_tokens` | 0.7 / 2048 | 默认采样 |
| `api` | `api_base/api_key/main_model/small_model/vision_model` | — | 主/小/视觉 |
| `api` | `small_base/small_key/vision_base/vision_key/image_base/image_key/image_model` | — | 独立端点 |
| `api` | `small_thinking_off` | `True` | 小模型（工具类非流式调用）优先关闭思考，见坑 6 |
| `api` | `image_dir` | `./createimage` | 生图输出目录 |
| `web` | `search_base/search_key/search_providers` | — / `[]` | 搜索 |

### 1.4 坑

1. `_iter_stream` 有 **`produced` 守卫**（337 附近）：已产出正文则不再整体重试，避免重复输出。
2. 客户端缓存键含 timeout 与 Key 指纹 → 改配置后不 `reconfigure()` 会继续用旧客户端/旧 Key。
3. `LLMWorker` 实例必须保活（外部持有引用），且**子线程内不得创建 QWidget**。
4. `_build_sampling` 把非标准参数放 `extra_body`，加新采样参数（如 `reasoning_effort`）必须走这里，否则被 OpenAI SDK 拒绝。
5. 生图超时 180s、搜索 30s、普通请求 60s（61/71/96/535/607）。
6. **思考型模型会让小模型「调不动」**（2026-09-21 实测）：DeepSeek-R1 系 / Qwen3 /
   GLM 等会把 `max_tokens` 预算全耗在 reasoning 上，返回 `content=""` +
   `finish_reason="length"`。小模型调用点预算只有 8~512（降噪 64 / 提炼 512 /
   群聊判定 16 / 技能审查 8 …）→ 必被吃光，表现为记忆不入库、群聊点名失效等，
   且因为各调用方都有 `except → 降级`，**日志只有一条 warning，极难发现**。
   现由 `_complete_with_fallback` 统一兜底：先按厂商参数关思考
   （`thinking.type=disabled` → `enable_thinking=False` → `chat_template_kwargs`，
   命中后按端点缓存），仍无正文则放大预算重试。新增非流式工具调用请直接复用它，
   **不要再手写 `client.chat.completions.create(...)` 取 `content`**。


### 1.5 联网搜索（设置面板「搜索引擎 API」）

| 符号 | 作用 |
|---|---|
| `_search_items(payload)` | 解析各家响应：Brave `web.results[]` / Bing `webPages.value[]` / SerpAPI `organic_results[]` / **百度千帆 `references[]`** |
| `_is_qianfan(base)` / `_qianfan_search_url(base)` | 千帆识别 + 地址归一：`/v2/ai_search/config`（或裸域名）→ `/v2/ai_search/web_search` |
| `_search_provider_items(base, key, query, top_n)` | 按端点发一次请求：**千帆＝POST + JSON**（`messages` / `search_source=baidu_search_v2` / `resource_type_filter[].top_k`，`Authorization: Bearer bce-v3/...`）；Brave＝GET + `X-Subscription-Token`；Bing＝GET + `Ocp-Apim-Subscription-Key`；其它＝`{q}` 模板或 `?q=`。HTTP 非 2xx / 网络不可达 / 响应非 JSON 会抛出**带状态码与响应体片段**的原因 |
| `_search_chain(providers, query, top_n, allow_silent, strict)` | 统一编排：真失败（单引擎）抛异常→界面提示；多引擎链 `allow_silent` 静默降级；「接口连通但零结果」在对话路径不中断对话，`strict=True`（设置面板测试）时抛错并提示该填哪个检索端点 |
| `web_search(query, top_n)` | 读配置：`web.search_base/search_key` + `web.search_providers` fallback 链 |
| `web_fetch(url, max_chars)` | **直接打开网址**（`\@network` + 网址时用）：浏览器 UA、30s、3MB 上限、自动补 `https://`、按 `charset`/utf-8/gb18030 解码、gzip 兜底；HTML 去脚本样式标签 → 纯文本；返回「来源网址 + 网页标题 + 网页正文」，超长按 **开头 80% + 结尾 20%** 保留并写明省略字数；非网页类型（PDF/图片）与 HTTP 错误抛出**可读中文原因** |
| `extract_urls(text)` | 从文本抽网址（`http(s)://` 或 `www.` 开头；去重保序；结尾句读/多余右括号裁掉但**网址自带括号配平保留**） |
| `html_to_text(html)` / `_HtmlTextExtractor` | HTML → 纯文本（丢 `script/style/head/nav/footer`，块级标签换行，压空白与连续空行） |
| `_qianfan_query(query)` | 千帆 72 字符额度内的关键词（剥前缀 + 权重截断 + **编号类只发编号**），返回 `(关键词, 是否截断)` |
| `_query_ids(query)` / `_filter_by_ids(items, ids)` | **编号类查询**识别（字母+≥3 位数字）与「必须含该编号」的相关性硬门槛 |
| `_rank_search_items(items)` / `_clean_url(url)` / `_plain_text(text)` | 结果过滤排序（阿拉丁排后）/ URL 编码 / 去 HTML |
| `web_search_custom(base, key, query, top_n)` | 不读配置、失败必抛（设置面板「测试搜索」用） |

**检索质量相关（2026-09-21 实测 + 官方文档 2026-09-14）**：

| 项 | 结论 / 处理 |
|---|---|
| `messages[].content` **上限 72 字符**（1 汉字=2） | 超长**只取前 72 个字符**。实测长问句与其「前 36 字」返回**完全相同**的结果 → 真意图（在句尾）根本没参与检索，用户反馈「检索不到正确内容」的根因。`_qianfan_query()` 先剥「帮我查一下/请问」等无信息前缀，再按 2/1 权重截断到额度内；**被截断时**下发 `query_policy.enable_rewrite=true`（官方：增强长 query） |
| `sort.priority="auto"` | 按 query 类型自动排序（官方推荐强时效问题，如「今日股价」），始终下发 |
| `resource_type_filter.web.top_k` | 上限 50（默认 20）；现按 `max(10, top_n)` 多要候选，注入时仍只取 `top_n` |
| **阿拉丁卡片噪音** | 千帆把 `is_aladdin` 卡片混在网页结果里且常排前面（实测「小马宝莉 重映时间」首页是「XX参与配音的作品」百科 starmap 页）→ `_rank_search_items()` 把普通网页排前、阿拉丁**最多补 1 条**，并丢弃无标题/无链接条目；有 `rerank_score`/`authority_score` 时降序 |
| 含中文的 URL | 千帆常返回原文中文路径（`baike.baidu.com/item/英奇/…`），进 `<a href>` 后 Qt 解析会失败 → `_clean_url()` percent-encode（用户反馈「网址打不开」） |
| 摘要里的 HTML | `content` 常是 DOM 片段（`<table>…`）→ `_plain_text()` 去标签+压空白，单条截断 `SEARCH_SNIPPET_CHARS=400` |
| 注入格式 | `- 标题（站点 · 时间）` + 摘要 + `来源: 原始网址`；`ChatWorker` 随附「引用要求」（只用上面原始网址、不改写编造）；**没查到时会明确告诉模型「没有找到相关结果，不要编造」** |
| **编号被泛化词「稀释」** | 实测 query「TOI-6019b 这颗系外行星」→ 5 条全是「甲烷巨行星」SEO 新闻（含编号 0 条）；**单独搜「TOI-6019b」→ 第 1 条就是 arXiv 上真正讨论它的论文**（连加一个「系外行星」都会稀释）。处理：`_query_ids()` 命中编号时**只发编号本身**（且不下发改写），并把 `top_k` 提到 20；再用 `_filter_by_ids()` 做硬门槛（标题/摘要归一化后必须含该编号，否则丢弃）——污染页面一律不进上下文，全丢时走「没查到」 |

**坑**：百度千帆的 `…/v2/ai_search/config` 是**配置查询**端点，GET 恒返回
`{"items":[]}` 且 HTTP 200 —— 只支持 GET `?q=` 的旧实现于是「静默无搜索结果」
（用户反馈「搜索 API 调不动」）。填入 `/config` 会被自动纠正到 `/web_search`；
设置面板已加「测试搜索」按钮可直接自证。

**`\@network` 走两条路（2026-09-21 修复「无法访问网址」）**：`ChatWorker.run` 先
`extract_urls(query)` —— ① 有网址 → `web_fetch` 真正打开页面（最多
`ChatWorker.MAX_FETCH_URLS=3` 个），把**网页正文**注入 system（并注明「不是搜索
摘要」+ 引用要求）；② 去掉网址后的文字再走 `web_search`（纯网址消息跳过搜索）。
旧实现无论跟什么都只调 `web_search`：用户贴一个网址，它只把网址当关键词去搜，
**页面从来没被打开过**。全部网址抓取失败 → `error` 信号提示原因并中断本轮。
含 `web_fetch` 的 UA/重定向/去标签/超长/非网页/HTTP 404/网络不可达）。

### 1.6 生图与多模态附件（2026-09-21 重写）

**生图 `image_generate` —— 两种协议自动适配**：

| 协议 | 判定 | 请求 | 取图 |
|---|---|---|---|
| OpenAI 兼容 | 默认 | `POST {base}/images/generations`（model/prompt/size/n） | `data[0].b64_json` 或 `url` |
| **异步任务式** | base 形如 `…/v1/images/tasks`，或 OpenAI 路径返回 **404/405** 自动回退 | `POST {root}/v1/images/tasks`，**body 只发 model + prompt** | `GET {root}/v1/tasks/{id}` 轮询（5s+抖动、300s 超时）→ `output.media[].url` / `output.r2_url` |

- 辅助：`_image_task_root` / `_looks_like_task_base` / `_http_json`（错误带状态码+响应体片段）/ `_find_value`（广度优先，兼容 `data.id` 与顶层 `id`）/ `_image_output_url` / `_save_image_result` / `_image_error_hint`（额度不足、401 → 中文可执行提示）。
- **坑**：任务式端点对多余字段是**硬错误**（beatapi 实测 `size`/`n` → 400 `unknown field`），所以只发 `model`+`prompt`；聚合站的模型别名不一定出现在 `/v1/models` 列表里（用 `{"model":X}` 试探报 `"prompt" is required` 即说明 X 已被收录）。

**多模态附件 `attachment_text(file_path, max_chars, question="")`**：

- **文档**（pdf/docx/xlsx/pptx/txt…）→ `_extract_document_text` 后**直接注入全文原文**
  （上限走 `api.attach_max_chars`，默认 **32000 字**，设置面板「单文件注入上限」可调
  2000~400000）。⚠ 不要再退回「先让视觉模型压成一句摘要」的写法：原文被压缩后
  模型识别不准，且白等 30s+（实测 7.6MB PDF 摘要 35.8s）。
- **提取不做静默截断**（用户反馈「上传的 PDF 识别内容会被截断」）：旧实现 PDF 只读
  前 30 页 / PPT 前 30 张 / Excel 前 300 行且**不给任何提示**，现全部改为全文提取
  （仅保留 3000 页 / 2 万行的病态保护，命中时写进文本），PDF/PPT 插入 `[第 N 页]`
  标记（便于按页作答、便于判断截断位置）。
- **超上限时不再是「只留开头」**：按 **开头 70% + 结尾 30%** 拼接，中间缺口写明
  字数并提示「如需这部分内容请让用户指定页码/章节」（结论/参考文献通常落在结尾）。
- **扫描件 / 图片型 PDF → 改走多模态**（2026-09-23 用户反馈「传 PDF（无可复制文字）
  提示附件未能识别」）：`pypdf` 提不出文字时**不再直接放弃**，而是
  `_pdf_page_images` 把每页栅格化（最长 10 页 / 150dpi）→ `_vision_read_document`
  逐页交视觉模型**如实转写**（要求：标题/正文/表格/公式/页码都要、不总结不省略），
  结果带 `[第 N 页（视觉模型识别）]` 标记直接当正文注入；只有视觉模型也不可用/失败时
  才抛 `AttachmentNoTextError`（提示「设置 → API 配置视觉模型后重试」）。
  栅格化后端按可用性挑选：`pymupdf`/`fitz` → `pypdfium2`（**两个都已列入依赖**：
  基线用许可更宽松的 pypdfium2，PyMuPDF 更快但为 AGPL-3.0/商业双授权 ——
  分发方式不接受 AGPL 时可卸载它，功能不受影响）。
  ⚠ **坑（本次根因）**：多页 PDF 的提取结果即使一页文字都没有，也会因插入了
  `[第 N 页]` 标记而**非空** → 旧的 `if not text.strip()` 判定只在**单页**扫描件上生效，
  多页扫描件被当成正常文档（内容只有页标记）送进模型。判定必须走
  `_has_document_text()`（先剔页标记再看有没有正文）。`document_describe` 同一套兜底。
- **图片** → `_prepare_image_data_url` 先按最长边 1568 缩放、超 4MB 转 JPEG 重编码
  （实测 9.3MB→1.09MB；46.8MB 大图此前直接 `APIConnectionError` 断连），
  再交视觉模型。**必须把用户问题（`question`）交给视觉模型**并明确要求读取
  图中文字/数字/表格数据 —— 用户反馈「MoE 只能发送摘要，分析不了图」：此前
  只发一句「描述一下这张图」，泛化摘要答不了用户的具体问题（实测海报图改带
  问题后能逐项读出片名、导演、格式标识等）。
- 失败**必须抛出可读原因**（不要再静默「解析失败」）：`ChatWorker._attachment_context`
  会发 `notice` 信号（主界面系统提示气泡）并把「未能识别，已跳过」写进上下文。

**图片进主模型还是进视觉模型（`ChatWorker._attachment_payload()`）**：

| 情况 | 行为 |
|---|---|
| 主模型支持图片输入 | 返回图片 data URL，`ChatWorker.run` 用 `_user_content()` 把 **user 消息拼成 OpenAI 多模态数组**（text + image_url[]），主模型亲眼看图；此时**不再调视觉模型** |
| 主模型不支持 | 走上面 `attachment_text(question=用户消息)`，把视觉模型的分析结果作为文本注入 |

- 判定 `ChatWorker._main_model_sees_images()`：配置 `api.main_vision`（设置 →
  多模态 勾选「主对话模型支持图片输入」）优先；**未配置**时按模型名自动判断
  （`_MAIN_VISION_HINTS`：vl / vision / gpt-4o / gemini / claude-3… 命中；
  `deepseek-chat`、`deepseek-flash` 等纯文本模型不命中）。
- ⚠ 别把纯文本模型勾成「支持图片输入」：接口会直接报错。

---

## 2. `api_vault.py`（约 550 行）——常用接口类 API 管理器

**作用**：纯存储的「常用 API 配置库」（不校验字段），读写 `data/api_vault.json`，供技能工具（如天气）按名称取用；自带无边框 GUI。

**被谁用**：`ui_manager`（`import api_vault` 7249、`api_vault.find_profile` 7254-7256、`ApiVaultWindow` 9642）、`weather_service`（读同一 JSON）、`data/api_vault.json`（和风天气条目）。

| 符号 | 行号 | 作用 |
|---|---|---|
| `DATA_FILE` | 44 | `project_root()/data/api_vault.json` |
| `FIELD_LABELS` | 46 | 字段中文名 |
| `_blank` / `_norm` | 56 / 61 | 空条目（含 `uuid4().hex[:12]`）/ 字段回填 |
| `load_profiles` / `save_profiles` | 74 / 86 | 读全部（损坏→`[]`）/ **原子写**（`.tmp`→`os.replace`） |
| `upsert_profile` / `delete_profile` | 100 / 114 | 新增或更新 / 删除 |
| `find_profile(keyword, require_key=False)` | 119 | 按 id/名称/简介匹配（名称完全命中优先；跳过 `enabled=False`） |
| `get_key` / `get_base` | 143 / 149 | 取 KEY / 地址 |
| `ensure_defaults` | 155 | 首次写「和风天气」占位 |
| `ApiVaultWindow` | 221 | GUI（`_reload` 395 / `_save_current` 438） |
| `main` | 537 | 独立运行入口 |

**条目结构**：`{id, name, desc, base, key, extra, rules, enabled}`。

**扩展指南**：加字段 → `_blank`(56) + `_norm`(61) + `FIELD_LABELS`(46) + GUI `_build_ui`(239)。
**坑**：`_norm` 强制字符串化；`find_profile` 大小写不敏感。

---

## 3. 相关落点速查

| 想做 | 改 |
|---|---|
| 换模型/端点 | `config_loader.DEFAULTS["api"]` + `LLMClientPool._endpoint/_model` |
| 加采样参数 | `_build_sampling`(188) + 设置面板「对话 API」分区（`02`） |
| 改流式解析 | `_iter_stream`(278，重点 305-339) |
| 改 reasoning 提取 | `_iter_stream` 324-329 |
| 改生图 | `image_generate`(521) + `ui_manager._route_image_gen`(5102) |
| 改多模态 | `document_describe` / `vision_describe` |
| 扫描件 PDF 走视觉识别（页数/清晰度/OCR 提示词） | `VISION_PDF_MAX_PAGES`(869) / `VISION_PDF_DPI` / `_pdf_page_images`(1182) / `_vision_read_document`(1245) |
| 改搜索 | `web_search`(611) + `web.search_*` 配置 |
| 管理第三方 API KEY | `api_vault.py`（GUI 入口：设置面板「技能工具」→「打开常用API管理」`_open_api_vault` 9635） |
