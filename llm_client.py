"""
llm_client.py — OpenAI 兼容 LLM 客户端（工厂池 + QThread 异步封装）

- LLMClientPool（工厂模式）：按模型角色（main / small / vision）缓存客户端实例；
  兼容 OpenAI / DeepSeek / Azure OpenAI / 本地 vLLM / Ollama 等 OpenAI 兼容端点。
- LLMWorker(QThread)：流式/非流式请求在子线程执行，仅通过信号回传主线程，
  GUI 永不阻塞（子线程内禁止创建任何 QWidget）。
- 超时控制：memory 小模型调用可传入 timeout 秒数，超时即抛错由上层降级。
"""

from __future__ import annotations

import base64
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("AI_DeskMate.LLM")

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - 未安装 openai SDK 时降级
    OpenAI = None  # type: ignore

try:
    from PySide6.QtCore import QThread, Signal  # type: ignore
    _HAS_QT = True
except Exception:  # pragma: no cover - 无 GUI 环境（CI/无头测试）
    QThread = None  # type: ignore
    Signal = None  # type: ignore
    _HAS_QT = False

from config_loader import ConfigLoader

#: 抓网页时的浏览器 UA（不少站点对空 UA / python-urllib 直接 403）
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
#: 抓网页正文时最多读取的字节数（防超大页面把内存/上下文吃满）
_WEB_FETCH_MAX_BYTES = 3 * 1024 * 1024
#: 网址识别：http(s):// 或 www. 开头；分句标点/引号/尖括号不计入
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`。，；！？、]+", re.I)
#: 成对符号（网址自带括号时要保留，只有「多出来的收尾括号」才裁掉）
_BRACKET_PAIRS = (("）", "（"), (")", "("), ("】", "【"), ("]", "["),
                  ("》", "《"), ("}", "{"))


def _trim_url_tail(u: str) -> str:
    """裁掉网址结尾的句读与**多余的**收尾括号（网址内部括号需配平保留）。"""
    u = u.rstrip(".,;:!?，。；：！？、\"'”’…·")
    for close, open_ in _BRACKET_PAIRS:
        while u.endswith(close) and u.count(close) > u.count(open_):
            u = u[:-1].rstrip(".,;:!?，。；：！？、")
    return u


def extract_urls(text: str) -> List[str]:
    """从文本里抽出网址（去重、保序、去掉结尾标点）。

    需求（用户反馈「\\@network 工具无法访问网址」）：`\\@network https://xxx`
    必须先识别出网址并**直接抓取页面**，而不是把网址当搜索关键词丢给搜索引擎。
    """
    out: List[str] = []
    for m in _URL_RE.finditer(text or ""):
        u = _trim_url_tail(m.group(0))
        if not u or u.lower() in ("http://", "https://", "www."):
            continue
        if not re.match(r"^https?://", u, re.I):
            u = "https://" + u
        if u not in out:
            out.append(u)
    return out


# ---------------------------------------------------------------- 报错人话化
#: 从 ``Error code: 400 - {'error': {'message': '…'}}`` 里抠出人可读的一层文本
_ERROR_DETAIL_RE = re.compile(r"""['"]message['"]\s*:\s*['"]([^'"]{1,400})['"]""")
#: ``Error code: 400`` → 保留状态码（用于匹配规则）
_ERROR_CODE_RE = re.compile(r"Error code:\s*(\d{3})", re.I)
#: 出现这些（英文技术痕迹）说明不是「人话」，需要翻译
_TECH_MARKERS = ("error code", "traceback", "openai.", "urllib", "httpx",
                 "requests.", "http ", "{'", '{"', "exception", "errno",
                 "timed out", "ssl", ".py")
#: 报错特征 → 自然语言原因（从上到下匹配第一命中，键全部小写）。
#: 文案一律用纯文本（报错会被直接插进气泡 HTML，不能用 markdown 标记）。
_ERROR_REASON_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("ipinfringement", "ip infringement", "suspected of being involved in ip",
      "intellectual property"),
     "生成内容被服务商判定为「可能侵犯知识产权」，请避开已知 IP"
     "（动漫 / 游戏 / 影视角色、品牌形象等），换一个原创画面描述再试"),
    (("content policy", "content_policy", "contentpolicy", "safety",
      "prohibited", "violat", "blocked", "sensitive"),
     "内容触发了服务商的安全策略被拦截，请修改描述里的敏感内容后重试"),
    (("insufficient", "quota", "balance", "额度", "余额", "欠费", "arrears"),
     "接口账户余额 / 额度不足，请到服务商后台充值后再试"),
    (("unauthorized", "invalid api key", "invalid_api_key", "incorrect api key",
      "401", "authentication", "无效的令牌", "token is invalid"),
     "API Key 无效或未生效，请检查「设置」里的地址与 Key 是否与服务商一致"),
    (("permission", "forbidden", "403", "no access", "not authorized"),
     "当前 Key 没有调用该模型的权限（或该模型未开通），请到服务商后台确认"),
    (("rate limit", "rate_limit", "429", "too many requests", "限流", "频繁"),
     "请求过于频繁被限流了，请稍等片刻再试"),
    (("model not found", "model_not_found", "unknown model", "no such model",
      "does not exist", "invalid model"),
     "模型名不存在或未开通，请检查「模型」名称是否填写正确"),
    (("not found", "404", "405", "method not allowed", "nginx", "找不到"),
     "接口地址（API 地址）不正确——服务商在该路径上没有这个接口，请检查"
     "是否填错（例如该填 /compatible-mode/v1 却填成了 /api/v1）"),
    (("context length", "context_length", "maximum context", "too long",
      "max_tokens", "token limit", "上下文"),
     "对话内容超出模型最大上下文长度，请精简内容或新开一个会话再试"),
    (("timeout", "timed out", "超时", "deadline"),
     "请求超时（等待服务商响应太久），可能是网络较慢或服务商繁忙，请稍后重试"),
    (("connection", "connect", "network", "unreachable", "getaddrinfo",
      "dns", "certificate", "proxy", "网络", "连接"),
     "网络连接失败，无法访问服务商接口，请检查网络 / 代理设置后重试"),
    (("internal server", "server error", "bad gateway", "unavailable",
      "500", "502", "503", "504", "网关"),
     "服务商服务端暂时故障，请稍后重试"),
)


def _extract_error_detail(raw: str) -> str:
    """从技术性异常文本里提取人可读的一层描述（尽量剥掉代码包装）。"""
    m = _ERROR_DETAIL_RE.search(raw)
    if m:
        return m.group(1).strip()
    t = re.sub(r"^[^：:]{0,16}?(?:失败|错误|异常|出错)[：:]\s*", "", raw)
    t = re.sub(r"\b(?:openai|httpx|requests|urllib)\.[A-Za-z_]*Error[:\s]*", "", t)
    t = _ERROR_CODE_RE.sub(lambda mm: f"HTTP {mm.group(1)}", t)
    return t.strip()


def humanize_error(msg: str) -> str:
    """把技术性的异常 / HTTP 报错翻译成用户看得懂的**自然语言原因**。

    需求（用户 2026-09-21）：报错时要给出自然语言的原因，而不是把
    ``openai.BadRequestError: Error code: 400 - {'error': {...}}`` 这类
    技术文本原样丢给用户。原始文本仍会写入 ``log/error.log`` 供排查。

    - 命中已知特征（404 / 401 / 限流 / 超时 / 内容审查…）→ 返回对应人话原因，
      并在末尾附上「服务商原话」方便对照；
    - 本就是代码里手写的中文提示（如「请求超时（60 秒未收到回复）」）→ 原样返回，
      不做二次包装。
    """
    raw = str(msg or "").strip()
    if not raw:
        return "发生未知错误：服务端没有返回任何错误信息，请稍后重试。"

    # 已经是人话的中文提示 → 原样返回（避免把「请求超时（60 秒…）」再包一层）
    if re.search(r"[\u4e00-\u9fff]", raw) and not any(
            k in raw.lower() for k in _TECH_MARKERS):
        return raw

    low = raw.lower()
    detail = _extract_error_detail(raw)
    # 只有细节「够长且含文字」才附上，避免把 ``HTTP 404`` 之类的碎片当原话
    detail_ok = bool(
        4 <= len(detail) <= 120 and re.search(r"[A-Za-z\u4e00-\u9fff]", detail))
    for keys, reason in _ERROR_REASON_RULES:
        if any(k in low for k in keys):
            if detail_ok and detail not in reason:
                return f"{reason}。（服务商原话：{detail}）"
            return reason
    # 未命中规则：给出通用说明 + 原始细节（截断），保证用户仍能看到反馈
    shown = detail if len(detail) <= 160 else detail[:160] + "…"
    return (f"请求失败：{shown}。"
            "可检查「设置」中的 API 地址 / Key / 模型名，或稍后重试。")


# ---------------------------------------------------------------- messages 规范化
def _message_text(content: Any) -> str:
    """取消息正文的纯文本（兼容字符串与多模态 parts 列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or ""))
            elif p is not None:
                parts.append(str(p))
        return "\n".join(x for x in parts if x)
    return "" if content is None else str(content)


def is_system_role(msg: Any) -> bool:
    """该消息是否为 system 角色（大小写/空白容错）。"""
    return (isinstance(msg, dict)
            and str(msg.get("role") or "").strip().lower() == "system")


def normalize_messages(messages: Any) -> List[Dict[str, Any]]:
    """**发包前唯一的 messages 布局规则**：把任意条数的 system 合并成
    「**有且仅有一条、且位于最前**」的 system 消息；其余消息原样保留、顺序不变。

    —— 为什么需要这条规则（用户 2026-09-22）——
    现象：「主界面模型加载有误，用硅基流动就不行，千问的、DeepSeek 原生的都可以
    （API 正常）」。
    排查（``log/error.log`` 里抓到服务商原话）：硅基流动（SiliconFlow）对
    ``messages`` 校验比 OpenAI 严格 —— **只要出现第 2 条 system**（即使与第一条
    连续排在最前）就直接拒绝：

        {"code": 20015, "message": "\"messages\" in request are illegal:
         System message must be at the beginning...", "data": null}

    而本应用会把「系统提示 / 角色扮演设定 / 相关记忆 / 技能上下文 / 思维链协议 /
    联网搜索结果 / skill库 指引」等**多条 system** 依次追加，千问（DashScope）与
    DeepSeek 原生对此宽容 → 一换供应商就整轮对话失败。

    —— 为什么这条规则对所有厂商都通用 ——
    「单条 system 在最前」是各家 OpenAI 兼容接口的**交集**写法：

    ============  ==================================================  ==========
    厂商/端点       对 system 的要求                                     合并后
    ============  ==================================================  ==========
    OpenAI        允许多条，也允许 developer 角色                       ✅ 合法
    DeepSeek      允许多条（与 OpenAI 同族）                             ✅ 合法
    Gemini        只取**首条**作为 systemInstruction，多条易被丢弃/报错   ✅ 更稳
    （OpenAI 兼容）                                                       （不漏设定）
    Ollama        /v1 兼容层按**单条** system 处理，多条可能被忽略         ✅ 更稳
    DashScope     允许多条                                             ✅ 合法
    硅基流动        只允许**恰好一条且在最前**，否则 400/20015           ✅ 必需
    ============  ==================================================  ==========

    合并方式：按原顺序用空行 ``\\n\\n`` 连接（保留章节边界，不丢任何设定），
    语义等价于原「多条 system 被顺次拼接」。

    —— 实现约定 ——
    * **纯函数**：不改动调用方传入的列表与字典（内部消息对象直接复用），
      返回的永远是**新的外层列表**；
    * **幂等**：``normalize(normalize(x))`` 与 ``normalize(x)`` 等价；
    * 「本就符合规则」需**同时**满足：恰好一条 system、位于首位、且其 content
      已是**非空字符串** → 才原样返回；否则一律走合并（脏数据不留到请求里）；
    * 空内容 / 内容为 None 的 system 直接丢弃（不产生空白 system 消息）；
    * system 的 ``content`` 兼容多模态 parts 列表（取其中的 text）。

    —— 已知边界（本规则**不**负责，另有专门限制）——
    * OpenAI o1/o3 一类推理模型**完全不接受** ``system`` 角色（要 ``developer``）：
      此时仍需用户改模型或另设角色映射，合并救不了；
    * Claude / Gemini 原生协议要求 user/assistant **严格交替**：若历史里出现
      连续同角色消息，仍需另做规整（本应用的历史由程序生成，暂不涉及）。
    """
    src = list(messages or [])
    sys_idx = [i for i, m in enumerate(src) if is_system_role(m)]
    if not sys_idx:
        return src                       # 本来就没有 system → 原样返回
    sys_parts: List[str] = []
    for i in sys_idx:
        txt = _message_text(src[i].get("content")).strip()
        if txt:
            sys_parts.append(txt)
    drop = set(sys_idx)
    rest: List[Any] = [m for i, m in enumerate(src) if i not in drop]
    # 「已完全合规」的判定要**同时**满足：恰好 1 条 system + 在首位 +
    # content 已经是**非空字符串**。少判最后一条会漏掉两类脏数据：
    #   · content 为 None / 空白 → 空 system 留在请求里（服务商可能直接报错）；
    #   · content 是多模态 parts 列表 → system 带 list，硅基流动不接受。
    first = src[0] if src else None
    if (len(sys_idx) == 1 and sys_idx[0] == 0
            and isinstance(first, dict)
            and isinstance(first.get("content"), str)
            and first["content"].strip()):
        return src
    if not sys_parts:
        # 有 system 但内容全为空 / 无效 → 直接丢掉，绝不留下「第 2 条 system」
        return rest
    return [{"role": "system", "content": "\n\n".join(sys_parts)}] + rest


class _HtmlTextExtractor(HTMLParser):
    """把 HTML 转成可读纯文本：丢掉脚本/样式/标签，保留段落结构。"""

    _SKIP = {"script", "style", "noscript", "svg", "head", "template",
             "iframe", "canvas", "form", "nav", "footer"}
    _BLOCK = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4",
              "h5", "h6", "section", "article", "main", "blockquote", "pre",
              "table", "ul", "ol", "dl", "dd", "dt", "figure", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self._parts: List[str] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip or not data:
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v\u00a0]+", " ", raw)
        lines = [ln.strip() for ln in raw.split("\n")]
        out: List[str] = []
        for ln in lines:
            if not ln and (not out or not out[-1]):
                continue          # 连续空行只留一个
            out.append(ln)
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    """HTML → 纯文本（解析失败时退化为粗暴去标签）。"""
    p = _HtmlTextExtractor()
    try:
        p.feed(html or "")
        p.close()
        txt = p.text()
    except Exception:  # noqa: BLE001
        txt = ""
    if not txt:
        txt = re.sub(r"<[^>]+>", " ", html or "")
        txt = re.sub(r"\s+", " ", txt).strip()
    return txt


class AttachmentNoTextError(RuntimeError):
    """附件里**没有可提取的文字**（扫描件 / 图片型 PDF）。

    需求（用户 2026-09-23）：这类文件以前直接「附件未能识别，已跳过」。
    现在 :meth:`LLMClientPool.attachment_text` 会先尝试**多模态识别**——把 PDF
    逐页栅格化后交视觉模型 OCR（见 ``_vision_read_document``）；只有当视觉模型
    不可用 / 识别也拿不到内容时，才抛本异常，由上层把原因转告用户。
    """


class LLMClientPool:
    """工厂模式客户端池：按模型角色创建并缓存 OpenAI 兼容客户端。"""

    _instance: Optional["LLMClientPool"] = None

    MODEL_KINDS = ("main", "small", "vision", "image")

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._clients: Dict[str, "OpenAI"] = {}
        # 端点 → 命中过的「关闭思考」参数下标（见 _THINK_OFF_EXTRAS）
        self._think_off_cache: Dict[str, int] = {}

    @classmethod
    def instance(cls) -> "LLMClientPool":
        """获取全局客户端池单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 工厂
    def client(self, kind: str = "main", timeout: Optional[float] = None) -> "OpenAI":
        """按模型角色创建/复用客户端。kind ∈ {main, small, vision}。"""
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请执行 pip install -r requirements.txt")
        base_url, api_key = self._endpoint(kind)
        key = f"{kind}:{base_url}:{timeout if timeout is not None else 'default'}"
        if key not in self._clients:
            self._clients[key] = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout if timeout is not None else 60.0,
                max_retries=1,
            )
        return self._clients[key]

    def reconfigure(self) -> None:
        """配置变更（api_base / api_key / 模型名）后清空客户端缓存。"""
        self._clients.clear()

    def client_custom(self, base_url: str = "", api_key: str = "",
                      timeout: Optional[float] = None) -> "OpenAI":
        """按任意端点创建/复用 OpenAI 兼容客户端（技能独立 API / 生图 API）。"""
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请执行 pip install -r requirements.txt")
        base = (base_url or "").strip() or self._cfg.api_base()
        # 未显式传 Key 时必须回退到主配置 Key（ConfigLoader 内部再兜底 "EMPTY"）。
        # 旧实现这里直接填字面量 "EMPTY"，服务端会判定为无效凭据 → 401 Token is invalid，
        # 于是所有「不传独立 Key 的技能」（如 @calorie 热量估算）全部失败。
        key = (api_key or "").strip() or self._cfg.api_key()
        # 缓存键带上 Key 指纹：同一端点换 Key 时不至于复用旧客户端
        cache_key = f"custom:{base}:{hash(key) & 0xffffff:x}"
        if cache_key not in self._clients:
            self._clients[cache_key] = OpenAI(
                base_url=base,
                api_key=key,
                timeout=timeout if timeout is not None else 60.0,
                max_retries=1,
            )
        return self._clients[cache_key]

    @staticmethod
    def _create_chat(client: Any, **kwargs: Any) -> Any:
        """**所有 chat 请求的唯一出口**：先按 :func:`normalize_messages` 规整
        messages，再交给 SDK 发包。

        规则只写在这里一处（流式 / 非流式 / 文档描述 / 图片描述 全走它），
        以后新增任何对话请求都自动遵守「唯一一条 system 且在最前」，
        不会因为漏改某个调用点又在硅基流动上踩 400 / code=20015。
        """
        if "messages" in kwargs:
            kwargs["messages"] = normalize_messages(kwargs.get("messages"))
        return client.chat.completions.create(**kwargs)

    def chat_stream_custom(
        self,
        messages: List[Dict[str, Any]],
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        reasoning_cb: Optional[Callable[[str], None]] = None,
        sampling: Optional[Dict[str, Any]] = None,
        usage_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Iterator[str]:
        """流式生成：使用指定端点/Key/模型（技能独立 API；为空回退主 API 配置）。

        sampling：可选采样参数覆盖（角色扮演预设）。只传 OpenAI 标准字段，
        其余（top_k / min_p / top_a / repetition_penalty / reasoning_effort）
        通过 extra_body 下发，兼容 vLLM / Ollama / DeepSeek 等扩展参数。
        usage_cb：可选。流结束时回传本次用量字典（见 `_usage_from_response`）。
        """
        client = self.client_custom(base_url, api_key, timeout=timeout)
        model_name = (model or "").strip() or self._cfg.main_model()
        base_kwargs, extra = self._build_sampling(
            sampling,
            temperature=(
                temperature if temperature is not None
                else float(self._cfg.get("chat", "temperature", default=0.7))
            ),
            max_tokens=(
                max_tokens if max_tokens is not None
                else int(self._cfg.get("chat", "max_tokens", default=2048))
            ),
        )
        yield from self._iter_stream(
            client=client,
            model_name=model_name,
            messages=messages,
            native=base_kwargs,
            extra=extra,
            reasoning_cb=reasoning_cb,
            usage_cb=usage_cb,
        )

    def chat_complete_custom(
        self,
        messages: List[Dict[str, Any]],
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 512,
        timeout: Optional[float] = None,
    ) -> str:
        """非流式补全：使用指定端点/Key/模型（技能独立 API）。

        同样走「思考模式兜底」（见 `_complete_with_fallback`），避免技能侧
        独立端点挂思考型模型时拿到空正文。
        """
        client = self.client_custom(base_url, api_key, timeout=timeout)
        return self._complete_with_fallback(
            client=client,
            model_name=(model or "").strip() or self._cfg.main_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cache_key=(base_url or "").strip() or self._cfg.api_base(),
        )

    def _model(self, kind: str) -> str:
        cfg = self._cfg
        return {
            "main": cfg.main_model(),
            "small": cfg.small_model(),
            "vision": cfg.vision_model(),
            "image": cfg.image_model() or cfg.main_model(),
        }.get(kind, cfg.main_model())

    def _endpoint(self, kind: str) -> "tuple[str, str]":
        """返回该模型角色的 (base_url, api_key)；vision/small/image 支持独立端点。"""
        cfg = self._cfg
        if kind == "vision" and cfg.vision_base():
            return cfg.vision_base(), (cfg.vision_key() or cfg.api_key())
        if kind == "small" and cfg.small_base():
            return cfg.small_base(), (cfg.small_key() or cfg.api_key())
        if kind == "image" and cfg.image_base():
            return cfg.image_base(), (cfg.image_key() or cfg.api_key())
        return cfg.api_base(), cfg.api_key()


    # ------------------------------------------------------------ 采样参数
    @staticmethod
    def _build_sampling(
        sampling: Optional[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """把角色扮演预设的采样覆盖拆成「OpenAI 标准字段」与「extra_body」。

        sampling 中已开启的项优先；未覆盖的 temperature / max_tokens 沿用
        调用方传入值或全局配置。max_context 仅作提示，不下发给接口。
        返回 (标准 kwargs, extra_body)。
        """
        native: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        if temperature is not None:
            native["temperature"] = temperature
        if max_tokens is not None:
            native["max_tokens"] = max_tokens
        for key, val in (sampling or {}).items():
            if key == "max_context":     # 仅提示，不下发
                continue
            if key in {"temperature", "top_p", "max_tokens", "seed",
                       "frequency_penalty", "presence_penalty"}:
                native[key] = val
            else:
                extra[key] = val
        return native, extra

    # ------------------------------------------------------------ 用量统计
    @staticmethod
    def _usage_from_response(usage_obj: Any) -> Dict[str, Any]:
        """把各家 OpenAI 兼容端点的 usage 规范化成统一字典。

        返回：{exact, prompt_tokens, completion_tokens, total_tokens,
               cached_tokens, cached_unknown}
        exact=True 表示服务端确实返回了用量；cached_unknown=True 表示
        服务端未给出「缓存命中」字段（界面显示「未知」而不是 0）。
        """

        def _int_of(obj: Any, key: str) -> Optional[int]:
            try:
                v = getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key)
                return int(v) if v is not None else None
            except Exception:  # noqa: BLE001
                return None

        def _val_of(obj: Any, key: str) -> Optional[int]:
            try:
                if isinstance(obj, dict):
                    return obj.get(key)
                return getattr(obj, key, None)
            except Exception:  # noqa: BLE001
                return None

        info: Dict[str, Any] = {
            "exact": True,
            "prompt_tokens": _int_of(usage_obj, "prompt_tokens") or 0,
            "completion_tokens": _int_of(usage_obj, "completion_tokens") or 0,
            "total_tokens": _int_of(usage_obj, "total_tokens") or 0,
            "cached_tokens": 0,
            "cached_unknown": True,
        }
        # 缓存命中：OpenAI prompt_tokens_details.cached_tokens /
        # Anthropic 网关 cache_read_input_tokens / DeepSeek prompt_cache_hit_tokens 等
        sources: List[Any] = []
        det: Any = None
        try:
            if isinstance(usage_obj, dict):
                det = usage_obj.get("prompt_tokens_details")
            else:
                det = getattr(usage_obj, "prompt_tokens_details", None)
        except Exception:  # noqa: BLE001
            det = None
        if det is not None:
            sources.append(det)
        sources.append(usage_obj)
        cached = 0
        found = False
        for src in sources:
            for key in ("cached_tokens", "cache_read_input_tokens",
                        "prompt_cache_hit_tokens", "cache_hit_tokens"):
                v = _val_of(src, key)
                if isinstance(v, (int, float)):
                    cached += int(v)
                    found = True
        if found:
            info["cached_tokens"] = cached
            info["cached_unknown"] = False
        return info

    def _iter_stream(
        self,
        client: Any,
        model_name: str,
        messages: List[Dict[str, Any]],
        native: Dict[str, Any],
        extra: Dict[str, Any],
        reasoning_cb: Optional[Callable[[str], None]] = None,
        usage_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Iterator[str]:
        """统一的流式迭代：逐片 yield 正文，结束前回传 usage。

        用量：默认向服务端请求 `stream_options={"include_usage": True}`；
        若端点不支持（400 / 类型错误）且尚未产出任何文本，则自动去掉该参数重试一次，
        保证老 / 兼容端点（Ollama、部分中转）不受影响。
        """
        attempts: Tuple[bool, ...] = (True, False) if usage_cb is not None else (False,)
        last_exc: Optional[BaseException] = None
        for idx, with_usage in enumerate(attempts):
            produced = False
            got_usage = False
            try:
                kwargs: Dict[str, Any] = dict(native or {})
                if extra:
                    kwargs["extra_body"] = extra
                if with_usage:
                    kwargs["stream_options"] = {"include_usage": True}
                stream = self._create_chat(
                    client,
                    model=model_name,
                    messages=messages,
                    stream=True,
                    **kwargs,
                )
                for chunk in stream:
                    usage_obj = getattr(chunk, "usage", None)
                    if usage_obj is not None:
                        got_usage = True
                        if usage_cb is not None:
                            usage_cb(self._usage_from_response(usage_obj))
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None)
                    if piece:
                        produced = True
                        yield piece
                    if reasoning_cb is not None:
                        r = getattr(delta, "reasoning_content", None)
                        if r is None:
                            r = getattr(delta, "thinking", None)
                        if r:
                            reasoning_cb(r)
                if usage_cb is not None and not got_usage:
                    # 服务端未返回用量 → 交上层按文本量估算
                    usage_cb({"exact": False})
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # 已有正文产出 → 不能再整体重试（否则正文重复）
                if produced or idx >= len(attempts) - 1:
                    raise
                logger.debug("流式 usage 选项不被支持（%s），已去掉后重试", exc)
        if last_exc is not None:
            raise last_exc  # pragma: no cover - 理论上不可达

    # ------------------------------------------------------------ 主对话（流式）
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        kind: str = "main",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        reasoning_cb: Optional[Callable[[str], None]] = None,
        sampling: Optional[Dict[str, Any]] = None,
        usage_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Iterator[str]:
        """流式生成：逐片段 yield 文本。

        reasoning_cb 可选：DeepSeek-R1 / Qwen 等模型会在流中返回
        reasoning_content（思维链），开启思维链显示时通过该回调收集。
        sampling 可选：角色扮演预设的采样参数覆盖（酒馆式参数管理）。
        usage_cb 可选：流结束时回传本次用量字典（输入/输出/缓存命中）。
        """
        client = self.client(kind, timeout=timeout)
        native, extra = self._build_sampling(
            sampling,
            temperature=(
                temperature if temperature is not None
                else float(self._cfg.get("chat", "temperature", default=0.7))
            ),
            max_tokens=(
                max_tokens if max_tokens is not None
                else int(self._cfg.get("chat", "max_tokens", default=2048))
            ),
        )
        yield from self._iter_stream(
            client=client,
            model_name=self._model(kind),
            messages=messages,
            native=native,
            extra=extra,
            reasoning_cb=reasoning_cb,
            usage_cb=usage_cb,
        )

    # ------------------------------------------------------------ 非流式补全
    # -------------------------------------------------- 思考模式兜底（工具类调用）
    # 思考/推理型模型（DeepSeek-R1 系、Qwen3、GLM 等）会把 max_tokens 预算
    # 全部消耗在 reasoning 上，返回 content="" + finish_reason="length"。
    # 而工具类调用的 max_tokens 只有 8~512（记忆降噪 64 / 提炼 512 / 群聊判定
    # 16 / 技能审查 8 …），预算必然被吃光 —— 表现为「小模型调用没有任何结果」，
    # 记忆提炼、降噪、群聊点名、技能审查全部静默降级。
    # 对策：① 按厂商参数依次试探「关闭思考」；② 仍取不到正文就放大预算重试。
    # 端点不认识的 extra_body 会被忽略（不报错），故用「正文非空」判定命中，
    # 并把命中的下标按端点缓存，后续调用一步到位。
    _THINK_OFF_EXTRAS: Tuple[Dict[str, Any], ...] = (
        {"thinking": {"type": "disabled"}},                    # DeepSeek / GLM / Claude 兼容
        {"enable_thinking": False},                            # Qwen / DashScope / 硅基流动
        {"chat_template_kwargs": {"enable_thinking": False}},  # vLLM / Ollama
    )

    def _thinking_off_enabled(self) -> bool:
        """工具类（非流式）调用是否优先关闭思考模式（配置 api.small_thinking_off）。"""
        return bool(self._cfg.small_thinking_off())

    #: 传输层故障（连不上 / 超时 / 5xx）的关键词 —— 这类错误换 extra_body 没用
    _TRANSPORT_ERR_HINTS = (
        "connection", "connect error", "timed out", "timeout", "proxy",
        "502", "503", "504", "bad gateway", "service unavailable",
        "internal server error", "unreachable", "getaddrinfo", "refused",
        "remoteprotocol", "read error",
    )

    @classmethod
    def _is_transport_error(cls, exc: Exception) -> bool:
        """异常是否属「传输层故障」（而非『端点不接受这个参数』）。

        区分意义：4xx（参数不被接受）→ 换下一个 extra_body 方案还有希望；
        连接失败 / 超时 / 5xx → 换参数毫无意义，应立刻放弃，
        否则一次工具类调用要串行等 4 个方案（实测 26.6 秒才失败）。
        """
        code = getattr(exc, "status_code", None)
        try:
            if code is not None and int(code) >= 500:
                return True
            if code is not None and 400 <= int(code) < 500:
                return False
        except Exception:  # noqa: BLE001
            pass
        name = type(exc).__name__.lower()
        if "connection" in name or "timeout" in name:
            return True
        low = str(exc).lower()
        return any(k in low for k in cls._TRANSPORT_ERR_HINTS)

    def _complete_with_fallback(
        self,
        client: Any,
        model_name: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cache_key: str = "",
    ) -> str:
        """非流式补全 + 思考模式兜底：关思考（多厂商参数试探）→ 放大预算重试。"""
        budget = max(1, int(max_tokens))
        headroom = max(budget * 4, budget + 1024)
        plans: List[Tuple[Optional[Dict[str, Any]], int, Optional[int]]] = []
        if self._thinking_off_enabled():
            hit = self._think_off_cache.get(cache_key)
            order = ([hit] if hit is not None else []) + [
                i for i in range(len(self._THINK_OFF_EXTRAS)) if i != hit]
            plans.extend(
                (self._THINK_OFF_EXTRAS[i], budget, i) for i in order)
        else:
            # 用户显式要求保留思考 → 按原预算正常请求一次
            plans.append((None, budget, None))
        # 兜底：无论能否关闭思考，都给足预算保证正文一定能产出
        plans.append((None, headroom, None))
        for extra, mt, idx in plans:
            try:
                kwargs: Dict[str, Any] = {"extra_body": extra} if extra else {}
                resp = self._create_chat(
                    client,
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=mt,
                    stream=False,
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - 端点拒绝该参数则换下一个
                if self._is_transport_error(exc):
                    # 连不上 / 超时 / 5xx：换 extra_body 不会有任何改善，
                    # 立即放弃（否则 4 个方案串行等待，一次工具调用要 26 秒）
                    logger.warning("非流式补全传输失败，放弃其余方案: %s", exc)
                    break
                logger.debug("非流式补全（关闭思考 %s）被拒绝，换方案: %s", extra, exc)
                continue
            choice = resp.choices[0]
            text = (getattr(choice.message, "content", None) or "").strip()
            if text:
                if idx is not None:
                    self._think_off_cache[cache_key] = idx
                return text
            # 模型**正常结束（stop）却没正文**：说明它主动不输出内容，
            # 换方案重试也是白跑 → 直接返回空串（工具类调用会走各自的回退文案）。
            # 注意只有「预算被推理吃光（length）」才值得放大预算重试。
            if getattr(choice, "finish_reason", None) == "stop":
                logger.debug("非流式补全正常结束但无正文 → 不再重试")
                return ""
            logger.debug(
                "非流式补全正文为空（finish=%s，预算 %d tokens），换方案重试",
                getattr(choice, "finish_reason", None), mt)
        logger.warning("非流式补全所有方案均无正文（model=%s）", model_name)
        return ""

    def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        kind: str = "small",
        temperature: float = 0.2,
        max_tokens: int = 512,
        timeout: Optional[float] = None,
    ) -> str:
        """非流式补全（记忆小模型 / 一次性请求）。

        统一走「思考模式兜底」：思考型模型常把 max_tokens 耗在推理上导致正文
        为空，工具类调用（记忆降噪/提炼、群聊判定、技能审查…）因此全部失效。
        """
        client = self.client(kind, timeout=timeout)
        return self._complete_with_fallback(
            client=client,
            model_name=self._model(kind),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cache_key=self._endpoint(kind)[0],
        )

    # ------------------------------------------------------------ 多模态（视觉/文档）
    _TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js",
                      ".html", ".css", ".yaml", ".yml", ".ini", ".xml"}
    _DOC_SUFFIXES = {".pdf": "pdf", ".docx": "docx", ".doc": "doc",
                     ".xlsx": "xlsx", ".xls": "xls", ".pptx": "pptx", ".ppt": "ppt"}
    _IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

    #: 提取上限（防病态文件；正常文档一律**全文提取**，不再静默截断）
    _EXTRACT_MAX_PAGES = 3000        # PDF 页 / PPT 张
    _EXTRACT_MAX_SHEETS = 20         # Excel 工作表
    _EXTRACT_MAX_ROWS = 20000        # Excel 总行数

    #: 扫描件 / 图片型 PDF 走**视觉模型识别**时的页数上限与栅格化清晰度
    #: （需求：用户 2026-09-23「PDF 无可复制文字 → 附件未能识别，调用多模态解决」）
    VISION_PDF_MAX_PAGES = 10
    VISION_PDF_DPI = 150

    @classmethod
    def _extract_document_text(cls, path: Path) -> Optional[str]:
        """提取文档文本：TXT/PDF/DOCX/XLSX/PPTX 等，纯文本类型直接读取。

        需求（用户反馈「上传的 PDF 识别内容会被截断」）：**不做静默截断**。
        旧实现 PDF 只读前 30 页、PPT 只读前 30 张、Excel 只取前 300 行且不给任何
        提示，长文档后文直接消失。现在全文提取（仅保留 3000 页 / 2 万行这类病态
        保护，且命中时会在文本里写明），PDF/PPT 还会插入 ``[第 N 页]`` 标记，
        便于模型按页作答、也便于判断截断位置。
        """
        suffix = path.suffix.lower()
        if suffix in cls._TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                try:
                    from PyPDF2 import PdfReader  # type: ignore
                except Exception:
                    return None
            try:
                reader = PdfReader(str(path))
                total = len(reader.pages)
                pages = reader.pages[:cls._EXTRACT_MAX_PAGES]
                parts: List[str] = []
                if total > 1:      # 单页文档不必插页标记
                    for i, page in enumerate(pages, 1):
                        parts.append(f"[第 {i} 页]\n{page.extract_text() or ''}")
                else:
                    parts = [page.extract_text() or "" for page in pages]
                if total > len(pages):
                    parts.append(f"…（共 {total} 页，已提取前 {len(pages)} 页）")
                return "\n".join(parts)
            except Exception:
                return None
        if suffix == ".docx":
            try:
                import docx  # type: ignore
                d = docx.Document(str(path))
                return "\n".join(p.text for p in d.paragraphs)
            except Exception:
                return None
        if suffix in (".xlsx", ".xls"):
            try:
                import openpyxl  # type: ignore
                wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
                lines = []
                for ws in wb.worksheets[:cls._EXTRACT_MAX_SHEETS]:
                    lines.append(f"[Sheet: {ws.title}]")
                    for row in ws.iter_rows(values_only=True):
                        lines.append("\t".join("" if c is None else str(c) for c in row))
                if len(lines) > cls._EXTRACT_MAX_ROWS:
                    lines = lines[:cls._EXTRACT_MAX_ROWS]
                    lines.append(f"…（表格行数过多，仅提取前 {cls._EXTRACT_MAX_ROWS} 行）")
                return "\n".join(lines)
            except Exception:
                return None
        if suffix in (".pptx", ".ppt"):
            try:
                from pptx import Presentation  # type: ignore
                prs = Presentation(str(path))
                slides = list(prs.slides)
                lines = []
                for i, slide in enumerate(slides[:cls._EXTRACT_MAX_PAGES], 1):
                    texts = [shape.text for shape in slide.shapes
                             if hasattr(shape, "text") and shape.text]
                    if texts:
                        lines.append(f"[第 {i} 张幻灯片]")
                        lines.extend(texts)
                if len(slides) > cls._EXTRACT_MAX_PAGES:
                    lines.append(f"…（共 {len(slides)} 张，已提取前 "
                                 f"{cls._EXTRACT_MAX_PAGES} 张）")
                return "\n".join(lines)
            except Exception:
                return None
        return None

    def document_describe(self, prompt: str, file_path: str,
                          max_tokens: int = 1024) -> str:
        """多模态文档理解：图片走视觉模型；文本/PDF/Word/Excel/PPT 提取文本后分析。

        支持：png/jpg/webp/gif/bmp · txt/md/json/csv · pdf/docx/xlsx/pptx 等。
        （附件注入对话请改用 :meth:`attachment_text`：文档直接给原文更准。）
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        suffix = path.suffix.lower()

        if suffix in self._IMAGE_SUFFIXES:
            return self.vision_describe(prompt, str(path), max_tokens=max_tokens)

        text = self._extract_document_text(path)
        if text is None:
            raise RuntimeError(f"无法解析该文件类型: {suffix}（可尝试转换为 txt/pdf 后重试）")
        if not self._has_document_text(text) and suffix == ".pdf":
            # 扫描件 / 图片型 PDF：与 attachment_text 同一条多模态兜底（逐页视觉识别）
            text = self._vision_read_document(path, prompt)
            if text.strip():
                logger.info("文档 %s 无文字层 → 已用视觉模型识别（%d 页）",
                            path.name, text.count("[第 "))
        if not self._has_document_text(text):
            raise AttachmentNoTextError(
                "文件里没有可提取的文字（扫描件/图片型 PDF 或空文档）")
        # 需求（用户 2026-09-22「PDF / 长文件仍被截断」）：旧实现硬编码
        # ``text[:12000]`` 且**不给任何提示**，长文档后文直接消失。
        # 改为沿用附件统一上限（配置 api.attach_max_chars），并在截断时写明。
        limit = self._attachment_limit(None)
        if len(text) > limit:
            content = (text[:limit]
                       + f"\n……（文档共 {len(text)} 字，已送入前 {limit} 字）……")
        else:
            content = text
        client = self.client("vision")
        resp = self._create_chat(
            client,
            model=self._model("vision"),
            messages=[{
                "role": "user",
                "content": f"{prompt}\n\n[文件: {path.name}]\n文件内容:\n{content}",
            }],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    #: 附件注入对话时单文件最多带多少字（文档直接给原文，图片给模型描述）。
    #: 实际取值走配置 ``api.attach_max_chars``（设置面板可调，默认 32000）。
    ATTACHMENT_MAX_CHARS = 32000
    #: 超出上限时保留的比例：开头 70% + 结尾 30%（结论/参考文献通常在结尾）
    ATTACHMENT_TAIL_RATIO = 0.3

    #: PDF/PPT 提取时插入的页标记（判断「是否真的没有正文」时要先剔除它）
    _PAGE_MARK_RE = re.compile(r"\[第\s*\d+\s*页[^\]]*\]")

    @classmethod
    def _has_document_text(cls, text: Optional[str]) -> bool:
        """提取结果里**是否真的有正文**。

        坑（2026-09-23 排查扫描件时发现）：多页 PDF 的提取结果即使**一页文字都没有**，
        也会因为插入了 ``[第 N 页]`` 标记而**非空** —— 于是「无文字层 → 走视觉识别」
        的分支只在**单页**扫描件上生效，多页扫描件被当成正常文档送进模型（内容只有
        页标记）。所以判定必须先把页标记剔掉再看是否有内容。
        """
        return bool(cls._PAGE_MARK_RE.sub("", text or "").strip())

    def attachment_text(self, file_path: str,
                        max_chars: Optional[int] = None,
                        question: str = "") -> str:
        """把一个附件转成可注入对话的文本（ChatWorker 附件路径专用）。

        与 :meth:`document_describe` 的关键区别（用户反馈「多模态无法正确识别
        文件」的根因）：
        - **文档**（pdf/docx/xlsx/pptx/txt…）直接返回**提取到的全文原文**
          （不再先让视觉模型转述成一句摘要——原文被压缩后模型自然「识别不准」，
          还白白多一次 30 秒以上的请求）。只有超过单次注入上限（默认 32000 字，
          见 :meth:`_attachment_limit`）时才裁剪，且**开头 70% + 结尾 30%** 都保留、
          中间缺口写明字数（旧实现只留前 12000 字，用户反馈「PDF 内容被截断」）；
        - **图片**走视觉模型**按用户的问题分析**（``question`` 即用户这句话）。
          用户反馈「MoE 只能发送摘要，分析不了图」：此前视觉模型只收到一句
          「描述一下这张图」，产出的泛化摘要自然答不了用户的具体问题；
          现在把用户问题原样交给视觉模型去读图，结果是**针对问题的分析**而不是
          画面概括。发送前会**缩放压缩**（见 :meth:`_prepare_image_data_url`）。

        失败时抛异常（调用方负责把原因告诉用户，不再静默「解析失败」）。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        suffix = path.suffix.lower()
        if suffix in self._IMAGE_SUFFIXES:
            mime, data, size = self._prepare_image_data_url(path)
            client = self.client("vision", timeout=120.0)
            data_url = f"data:{mime};base64,{data}"
            ask = (question or "").strip()
            if ask:
                prompt = ("请**针对下面的问题**分析这张图片，直接给出可用于回答的内容："
                          "务必读出图中文字、数字、表格/图表数据、界面元素等关键信息，"
                          "不要只做泛泛的画面描述。\n问题：" + ask[:300])
            else:
                prompt = ("请用中文详细描述这张图片的内容（画面主体、文字、数据、"
                          "图表或界面元素等），供后续对话参考。")
            resp = self._create_chat(
                client,
                model=self._model("vision"),
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }],
                max_tokens=1500,
            )
            desc = (resp.choices[0].message.content or "").strip()
            logger.debug("图片附件 %s 已压缩到 %.0fKB 后送视觉模型（带用户问题=%s）",
                         path.name, size / 1024.0, bool(ask))
            return "图片分析（视觉模型按用户问题识别）: " + (desc or "（模型未返回内容）")
        text = self._extract_document_text(path)
        if text is None:
            raise RuntimeError(
                f"无法解析该文件类型：{suffix or '（无后缀）'}——"
                f"可在 Excel/Word 里另存为 pdf 或 txt 后重试")
        if not self._has_document_text(text) and suffix == ".pdf":
            # 需求（用户 2026-09-23）：扫描件 / 图片型 PDF 提不出文字时**改走多模态**
            # —— 逐页栅格化后交视觉模型 OCR，识别结果直接当正文注入（不再直接放弃）。
            ocr = self._vision_read_document(path, question)
            if ocr.strip():
                logger.info("附件 %s 无文字层 → 已用视觉模型识别（%d 页）",
                            path.name, ocr.count("[第 "))
                text = ("（本文件是扫描件/图片型 PDF，以下内容由视觉模型逐页识别，"
                        "可能存在识别误差）\n" + ocr)
        if not self._has_document_text(text):
            if suffix == ".pdf":
                raise AttachmentNoTextError(
                    "文件里没有可提取的文字（扫描件/图片型 PDF）；已尝试用视觉模型"
                    "识别但未成功 —— 可在「设置 → API」配置可用的视觉模型后重试")
            raise AttachmentNoTextError(
                "文件里没有可提取的文字（可能是空文档或特殊格式），"
                "可另存为 pdf/txt 后重试")
        limit = self._attachment_limit(max_chars)
        total = len(text)
        pages = text.count("[第 ") or 0        # PDF/PPT 的页标记数量
        if total <= limit:
            head = (f"文件正文（全文 {total} 字"
                    + (f"，共 {pages} 页" if pages else "") + "）:\n")
            return head + text
        # 需求（用户反馈「上传的 PDF 识别内容会被截断」）：默认上限从 12000 提到
        # 32000 字，仍超长时**不再只留开头**——按 70% 开头 + 30% 结尾拼，
        # 中间缺口写明字数与位置提示（结论/参考文献通常在结尾）。
        keep_head = int(limit * (1.0 - self.ATTACHMENT_TAIL_RATIO))
        keep_tail = max(0, limit - keep_head)
        gap = total - keep_head - keep_tail
        logger.info("附件 %s 共 %d 字，超出单次注入上限 %d：保留开头 %d + 结尾 %d 字",
                    path.name, total, limit, keep_head, keep_tail)
        return (
            f"文件正文（全文 {total} 字"
            + (f"，共 {pages} 页" if pages else "")
            + f"，超出单次注入上限 {limit} 字）：\n"
            f"—— 以下是【开头 {keep_head} 字】——\n{text[:keep_head]}\n"
            f"……（中间 {gap} 字未包含，如需这部分内容请让用户指定页码/章节）……\n"
            f"—— 以下是【结尾 {keep_tail} 字】——\n{text[-keep_tail:]}")

    def _attachment_limit(self, max_chars: Optional[int]) -> int:
        """单文件注入上限：显式入参 > 配置 ``api.attach_max_chars`` > 类默认值。"""
        if max_chars:
            try:
                return max(500, int(max_chars))
            except Exception:  # noqa: BLE001
                pass
        try:
            return int(self._cfg.attach_max_chars())
        except Exception:  # noqa: BLE001
            return self.ATTACHMENT_MAX_CHARS

    # ------------------------------------------------------------ 多模态（视觉）
    #: 送视觉模型前的图片上限：最长边与压缩后字节数（避免几十 MB 的 base64 撑爆请求）
    VISION_MAX_EDGE = 1568
    VISION_MAX_BYTES = 4 * 1024 * 1024

    @classmethod
    def _prepare_image_data_url(cls, path: Path) -> Tuple[str, str, int]:
        """读取图片 → 必要时缩放/重编码 → 返回 (mime, base64, 压缩后字节数)。

        用户反馈「多模态无法正确识别文件」的根因之一：手机照片 / 高清截图动辄
        几 MB~几十 MB，直接 base64 会让请求体膨胀数十倍，服务端直接断连
        （表现为 ``APIConnectionError``）。这里统一按最长边 ``VISION_MAX_EDGE``
        缩放；压缩后仍超过 ``VISION_MAX_BYTES`` 时转 JPEG 再压。
        """
        mime = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
        }.get(path.suffix.lower().lstrip("."), "image/png")
        raw = path.read_bytes()
        orig_len = len(raw)
        try:
            import io

            from PIL import Image  # type: ignore

            im = Image.open(io.BytesIO(raw))
            im.seek(0)
            im.load()
            need_edge = max(im.size) > cls.VISION_MAX_EDGE
            need_size = orig_len > cls.VISION_MAX_BYTES
            if need_edge or need_size:
                if need_edge:
                    im.thumbnail((cls.VISION_MAX_EDGE, cls.VISION_MAX_EDGE),
                                 Image.LANCZOS)
                keep_alpha = im.mode in ("RGBA", "LA", "P") and not need_size
                buf = io.BytesIO()
                if keep_alpha:
                    im.save(buf, format="PNG", optimize=True)
                    mime = "image/png"
                else:
                    im.convert("RGB").save(buf, format="JPEG", quality=88,
                                           optimize=True)
                    mime = "image/jpeg"
                raw = buf.getvalue()
                logger.debug("图片预处理 %s：%dKB → %dKB",
                             path.name, orig_len // 1024, len(raw) // 1024)
        except Exception as exc:  # noqa: BLE001 - 缺 Pillow / 图片异常 → 用原始数据
            logger.debug("图片预处理跳过（%s）: %s", path.name, exc)
        if len(raw) > cls.VISION_MAX_BYTES:
            raise RuntimeError(
                f"图片过大（{len(raw) / 1048576:.1f}MB，超过 "
                f"{cls.VISION_MAX_BYTES // 1048576}MB），请先压缩或裁剪后重试")
        return mime, base64.b64encode(raw).decode("ascii"), len(raw)

    @classmethod
    def _pdf_page_images(cls, path: Path, max_pages: int = 0,
                         dpi: int = 0) -> List[Path]:
        """把 PDF 每页栅格化成 PNG，返回文件列表（落在**临时目录**，调用方用完自行删除）。

        用途：**扫描件 / 图片型 PDF** 用 ``pypdf`` 提不出任何文字，只能让视觉模型看图。

        渲染后端按可用性自动挑选（都是**可选依赖**，一个都没有时返回空列表，
        由调用方按「识别不了」兜底）：
          1. ``pymupdf`` / ``fitz``（PyMuPDF，渲染快；AGPL-3.0 或商业授权双许可）；
          2. ``pypdfium2``（Apache-2.0 / BSD 系，许可更宽松，**推荐列入依赖**）。
        """
        cap = int(max_pages or cls.VISION_PDF_MAX_PAGES)
        dpi = int(dpi or cls.VISION_PDF_DPI)
        out_dir = Path(tempfile.mkdtemp(prefix="attach_pdf_"))
        files: List[Path] = []

        # ① PyMuPDF（快）
        try:
            try:
                import pymupdf as _mupdf          # PyMuPDF ≥1.24 的新导入名
            except Exception:  # noqa: BLE001
                import fitz as _mupdf             # 旧导入名
            try:
                doc = _mupdf.open(str(path))
                try:
                    for i in range(min(len(doc), cap)):
                        fp = out_dir / f"p{i + 1}.png"
                        doc[i].get_pixmap(dpi=dpi).save(str(fp))
                        files.append(fp)
                finally:
                    doc.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("PyMuPDF 栅格化失败（%s）：%s", path.name, exc)
            if files:
                return files
        except Exception:  # noqa: BLE001
            pass

        # ② pypdfium2（许可更宽松）
        try:
            import pypdfium2 as _pdfium  # type: ignore
            try:
                doc = _pdfium.PdfDocument(str(path))
                try:
                    scale = max(1.0, dpi / 72.0)
                    # 新旧版本取页 API 不同：get_page(i) → doc[i]
                    get_page = getattr(doc, "get_page", None) or (lambda i: doc[i])
                    for i in range(min(len(doc), cap)):
                        fp = out_dir / f"p{i + 1}.png"
                        get_page(i).render(scale=scale).to_pil().save(str(fp), "PNG")
                        files.append(fp)
                finally:
                    doc.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("pypdfium2 栅格化失败（%s）：%s", path.name, exc)
        except Exception:  # noqa: BLE001
            pass

        if not files:
            logger.info("无可用 PDF 栅格化后端（PyMuPDF / pypdfium2 均缺失）：%s 无法走视觉识别",
                        path.name)
            # 一页都没转出来 → 立即删掉临时目录，别在系统临时目录里留空壳
            shutil.rmtree(out_dir, ignore_errors=True)
        return files

    def _vision_read_document(self, path: Path, question: str = "",
                              max_pages: int = 0) -> str:
        """扫描件 / 图片型文档 → 逐页交**视觉模型**识别（OCR + 必要说明）。

        返回带 ``[第 N 页（视觉模型识别）]`` 页标记的文本；后端或视觉模型不可用时
        返回空串（调用方据此走「识别不了」的兜底提示）。

        需求（用户 2026-09-23）：「现在传 PDF（无可复制文字）提示附件未能识别，
        已跳过……调用多模态可以解决的话解决这个问题」。
        """
        pages = self._pdf_page_images(path, max_pages)
        if not pages:
            return ""
        ask = ("这是一份**扫描件 PDF** 的第 {i} 页。请**逐行如实转写**页面上的全部文字："
               "标题、正文、表格文字、公式、页眉页脚与页码都要，保持原有顺序与分段；"
               "**不要总结、不要评论、不要省略**；纯图形或照片区域用一句话说明即可。")
        q = (question or "").strip()
        if q:
            ask += f"\n（附注：用户的问题是「{q[:120]}」；仅当与页面内容相关时参考，否则忽略。）"
        parts: List[str] = []
        try:
            for i, img in enumerate(pages, 1):
                try:
                    txt = (self.vision_describe(ask.format(i=i), str(img),
                                                max_tokens=2048) or "").strip()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("视觉模型识别第 %d 页失败：%s", i, exc)
                    continue
                if txt:
                    parts.append(f"[第 {i} 页（视觉模型识别）]\n{txt}")
            if parts and len(pages) >= self.VISION_PDF_MAX_PAGES:
                parts.append(f"（只识别了前 {len(pages)} 页；如需后续页面，"
                             "请让用户拆分文件或分次上传）")
        finally:
            try:
                shutil.rmtree(pages[0].parent, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        return "\n\n".join(parts)

    def vision_describe(self, prompt: str, image_path: str, max_tokens: int = 512) -> str:
        """图片理解：将本地图片 base64 编码后交给视觉模型描述（发送前自动缩放）。"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {path}")
        mime, b64, _size = self._prepare_image_data_url(path)
        data_url = f"data:{mime};base64,{b64}"
        client = self.client("vision")

    # ------------------------------------------------------------ 多模态（生图）
    # 两种协议（自动适配）：
    #  1) OpenAI 兼容：``POST {base}/images/generations``（同步返回 b64_json / url）
    #  2) **异步任务式**：``POST {root}/v1/images/tasks`` → ``GET {root}/v1/tasks/{id}``
    #     轮询到 succeeded（beatapi 等聚合站：请求体只认 ``model`` + ``prompt``，
    #     多传 size/n 会 400 unknown field；结果在 ``output.media[].url`` /
    #     ``output.r2_url``，状态 queued / processing / succeeded / failed）
    _IMAGE_TASK_SUBMIT = "/v1/images/tasks"
    _IMAGE_TASK_POLL = "/v1/tasks/{tid}"
    _IMAGE_TASK_INTERVAL = 5.0      # 官方建议 5~10 秒一次（限流 120 次/分钟）
    _IMAGE_TASK_TIMEOUT = 300.0     # 任务式生图整体超时
    _IMAGE_TASK_OK = ("succeeded", "success", "completed", "done", "finished")
    _IMAGE_TASK_BAD = ("failed", "error", "canceled", "cancelled")

    @staticmethod
    def _image_task_root(base: str) -> str:
        """从 base 推导任务 API 的根地址（去掉 /v1/... 路径部分）。"""
        b = (base or "").strip().rstrip("/")
        for marker in ("/v1/images/tasks", "/v1/tasks", "/v1/images/generations",
                       "/v1/images", "/v1"):
            if marker in b:
                return b.split(marker)[0]
        return b

    @classmethod
    def _looks_like_task_base(cls, base: str) -> bool:
        """base 本身已是任务端点（…/v1/images/tasks）→ 直接走任务协议。"""
        return "/images/tasks" in (base or "").lower()

    @staticmethod
    def _http_json(url: str, method: str = "GET", payload: Any = None,
                   api_key: str = "", timeout: float = 60.0) -> Any:
        """发一次 JSON 请求；失败抛出**带状态码与响应体片段**的原因。"""
        import json
        import urllib.error
        import urllib.request

        headers = {"Authorization": f"Bearer {api_key}",
                   "Accept": "application/json",
                   "User-Agent": "AI-DeskMate/1.0"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"网络不可达 {exc.reason}") from exc
        try:
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"响应不是 JSON（前 120 字）：{raw[:120]}") from exc

    @staticmethod
    def _find_value(payload: Any, names: Tuple[str, ...],
                    pred: Optional[Callable[[Any], bool]] = None) -> Any:
        """在嵌套 JSON 里按**广度优先**找第一个键名匹配（且满足 pred）的值。

        兼容各家把任务信息放在顶层 / ``data`` / ``result`` / ``output`` 的写法。
        """
        want = {n.lower() for n in names}
        level = [payload]
        for _ in range(6):
            nxt: List[Any] = []
            for node in level:
                if isinstance(node, dict):
                    for k, v in node.items():
                        if (str(k).lower() in want
                                and not isinstance(v, (dict, list))
                                and (pred is None or pred(v))):
                            return v
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            level = [n for n in nxt if isinstance(n, (dict, list))]
            if not level:
                break
        return None

    @classmethod
    def _image_output_url(cls, payload: Any) -> str:
        """从任务响应里取生成图片的 URL（兼容 output.media[] / r2_url 等写法）。"""

        def _http(v: Any) -> bool:
            return isinstance(v, str) and v.startswith("http")

        # 1) 官方文档写法：output.r2_url 或 output.media[].url（也在 data/result 里找）
        for k in ("output", "data", "result"):
            sub = payload.get(k) if isinstance(payload, dict) else None
            if sub is None:
                continue
            hit = cls._find_value(sub, ("r2_url", "url", "image_url"), pred=_http)
            if _http(hit):
                return str(hit)
        # 2) 兜底：整份响应里找第一个 http 图片地址
        hit = cls._find_value(
            payload, ("r2_url", "url", "image_url", "image"),
            pred=lambda v: _http(v) and any(
                ext in str(v).lower().split("?")[0]
                for ext in (".png", ".jpg", ".jpeg", ".webp")))
        return str(hit) if _http(hit) else ""

    def _save_image_result(self, b64: str = "", url: str = "") -> str:
        """把生图结果落地到配置的保存目录，返回本地文件路径。"""
        out_dir = self._cfg.image_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / (f"gen_{time.strftime('%Y%m%d_%H%M%S')}_"
                          f"{uuid.uuid4().hex[:6]}.png")
        if b64:
            dest.write_bytes(base64.b64decode(b64))
        elif url:
            import httpx  # noqa: PLC0415 - 延迟导入，避免启动加载开销
            r = httpx.get(url, timeout=120.0, follow_redirects=True)
            r.raise_for_status()
            dest.write_bytes(r.content)
        else:
            raise RuntimeError("生图接口未返回可用图片数据（既无 b64_json 也无 url）")
        return str(dest)

    @staticmethod
    def _is_missing_endpoint(exc: Exception) -> bool:
        """异常是否表示「该路径不存在」（用于 OpenAI 兼容 → 任务式回退）。"""
        text = str(exc).lower()
        return any(k in text for k in
                   ("404", "405", "not found", "method not allowed", "nginx"))

    @staticmethod
    def _b64_pred(v: Any) -> bool:
        return isinstance(v, str) and len(v) > 100

    @staticmethod
    def _image_error_hint(msg: str) -> str:
        """把生图接口的常见错误翻译成用户看得懂的提示（额度 / Key）。"""
        low = (msg or "").lower()
        if "额度不足" in msg or "insufficient_credits" in low or "quota" in low:
            return f"{msg}（生图服务商账户余额/额度不足：请到服务商后台充值后重试）"
        if "unauthorized" in low or "401" in msg:
            return f"{msg}（生图 API Key 无效或未激活：请检查「设置 → 生图 API」）"
        return msg

    def _image_generate_task(self, prompt: str, base: str, key: str,
                             model_name: str) -> str:
        """异步任务式生图：提交任务 → 轮询 → 下载结果。"""
        root = self._image_task_root(base) or base
        submit = root + self._IMAGE_TASK_SUBMIT
        # 需求：只发 model + prompt —— 这类端点对多余字段是硬错误
        # （beatapi 实测：size / n 会 400 unknown field）
        try:
            created = self._http_json(submit, "POST",
                                      {"model": model_name, "prompt": prompt},
                                      key, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(self._image_error_hint(str(exc))) from exc
        tid = self._find_value(created, ("task_id", "id"),
                               pred=lambda v: isinstance(v, str) and bool(v.strip()))
        if not isinstance(tid, str) or not tid.strip():
            # 少数端点直接在创建响应里给结果
            url = self._image_output_url(created)
            b64 = self._find_value(created, ("b64_json", "b64", "image_base64"),
                                   pred=self._b64_pred)
            if b64 or url:
                return self._save_image_result(b64=str(b64 or ""), url=url)
            raise RuntimeError(f"生图任务未返回任务 ID：{str(created)[:200]}")
        poll = root + self._IMAGE_TASK_POLL.format(tid=tid.strip())
        deadline = time.monotonic() + self._IMAGE_TASK_TIMEOUT
        status = ""
        while True:
            time.sleep(self._IMAGE_TASK_INTERVAL + random.uniform(0.0, 1.5))
            try:
                data = self._http_json(poll, "GET", None, key, timeout=60.0)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(self._image_error_hint(str(exc))) from exc
            status = str(self._find_value(data, ("status", "state")) or "").lower()
            if status in self._IMAGE_TASK_OK:
                url = self._image_output_url(data)
                b64 = self._find_value(data, ("b64_json", "b64", "image_base64"),
                                       pred=self._b64_pred)
                if b64 or url:
                    return self._save_image_result(b64=str(b64 or ""), url=url)
                raise RuntimeError(
                    f"生图任务已完成但未找到图片地址：{str(data)[:200]}")
            if status in self._IMAGE_TASK_BAD:
                code = self._find_value(data, ("error_code", "code")) or ""
                msg = self._find_value(data, ("error_message", "message")) or ""
                raise RuntimeError(f"生图任务失败：{code} {msg}".strip())
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"生图任务超时（{int(self._IMAGE_TASK_TIMEOUT)} 秒未完成，"
                    f"最后状态 {status or '未知'}）")

    def image_generate(self, prompt: str, size: str = "1024x1024",
                       base_url: str = "", api_key: str = "",
                       model: str = "") -> str:
        """生图（自动适配 OpenAI 兼容 / 异步任务两种协议），返回本地文件路径。

        优先级：显式传入 base_url > 全局生图 API（image_base）> 主 API。
        - base 形如 ``…/v1/images/tasks``（任务端点）→ 直接走任务协议；
        - 否则先按 OpenAI 兼容 ``{base}/images/generations`` 调用，遇到 404/405
          （该路径不存在，如 beatapi 聚合站）自动回退到任务协议。
        图片保存到设置里的保存目录（默认 createimage/gen_<时间戳>_<hex>.png）。
        """
        cfg = self._cfg
        base = (base_url or "").strip() or cfg.image_base() or cfg.api_base()
        key = (api_key or "").strip() or cfg.image_key() or cfg.api_key()
        model_name = (model or "").strip() or cfg.image_model() or "dall-e-3"
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请执行 pip install -r requirements.txt")
        if self._looks_like_task_base(base):
            return self._image_generate_task(prompt, base, key, model_name)
        # 2026-09-21：聚合站常把 OpenAI 兼容生图放在 /compatible-mode/v1，而用户
        # 按习惯填成 /api/v1 → 一直 404。这里按「原样 → {站点根}/compatible-mode/v1
        # → {站点根}/v1」依次尝试，命中即用（未命中才回退异步任务协议）。
        for cand in self._image_base_candidates(base):
            try:
                client = self.client_custom(cand, key, timeout=180.0)
                resp = client.images.generate(
                    model=model_name, prompt=prompt, size=size, n=1)
                items = getattr(resp, "data", None) or []
                if not items:
                    raise RuntimeError("生图接口未返回图片数据")
                item = items[0]
                return self._save_image_result(
                    b64=str(getattr(item, "b64_json", None) or ""),
                    url=str(getattr(item, "url", None) or ""))
            except Exception as exc:  # noqa: BLE001
                if not self._is_missing_endpoint(exc):
                    raise
                # 该候选路径没有 /images/generations → 换下一个候选 base
                logger.warning(
                    "生图端点 %s/images/generations 不存在（%s），尝试其它 base",
                    cand.rstrip("/"), exc)
        # 所有 OpenAI 兼容候选都不可用（聚合站多为异步任务式）→ 换协议重试
        logger.warning("生图改用任务式协议：%s", base.rstrip("/"))
        return self._image_generate_task(prompt, base, key, model_name)

    @staticmethod
    def _image_base_candidates(base: str) -> List[str]:
        """为生图推导候选 base：先按用户填的原样，再补聚合站常见路径变体。

        背景（2026-09-21 用户实测）：``maas.qianwenaiapi.com`` 这类聚合站把
        OpenAI 兼容生图放在 ``/compatible-mode/v1/images/generations``，而用户按
        OpenAI 习惯填成 ``/api/v1``，于是 ``/api/v1/images/generations`` 一直
        404（随后又被误判为「任务式端点」而再次失败）。这里按「原样 →
        ``{站点根}/compatible-mode/v1`` → ``{站点根}/v1``」依次尝试。

        注意：站点根用 urlsplit 精确取 ``scheme://netloc``，避免把
        ``api.beatapi.io`` 里的 ``api`` 误当成路径段。
        """
        import urllib.parse

        b = (base or "").strip().rstrip("/")
        if not b:
            return []
        out = [b]
        try:
            u = urllib.parse.urlsplit(b)
            root = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""
        except Exception:  # noqa: BLE001
            root = ""
        if root:
            for suffix in ("/compatible-mode/v1", "/v1"):
                cand = root + suffix
                if cand not in out:
                    out.append(cand)
        return out

    # ------------------------------------------------------------ 联网搜索（需求 v5）
    #: 百度千帆「AI 搜索」检索端点（POST + JSON；鉴权 Bearer bce-v3/...）。
    #: 设置里很容易被填成文档里的 ``/v2/ai_search/config``（配置查询端点，
    #: 永远返回 ``{"items":[]}``，看起来「调用了但没结果」），这里统一纠正。
    _QIANFAN_HOST = "qianfan.baidubce.com"
    #: 注入对话时单条摘要最多保留多少字（千帆 content 字段可达 2000 字/条）
    SEARCH_SNIPPET_CHARS = 400

    @staticmethod
    def _clean_url(url: str) -> str:
        """URL 规整：把非 ASCII 字符（百科词条名里的中文等）编码成 %XX。

        用户反馈的「访问网址打不开」根因之一：千帆返回的 ``url`` 常带**原文中文**
        （如 ``baike.baidu.com/item/英奇/63339182``）。这种地址进入聊天 HTML 的
        ``<a href>`` 后 Qt 富文本可能解析失败 → 点了没反应 / 打开错页面。
        这里统一 percent-encode，保留 :/?#[]@!$&'()*+,;=% 等保留字符。
        """
        import urllib.parse

        u = (url or "").strip()
        if not u or u.isascii():
            return u
        try:
            return urllib.parse.quote(u, safe=":/?#[]@!$&'()*+,;=%~")
        except Exception:  # noqa: BLE001
            return u

    @staticmethod
    def _plain_text(text: str) -> str:
        """去掉检索摘要里的 HTML 标签并压掉多余空白。

        千帆的 ``content`` 常直接返回网页 DOM 片段（如东方财富行情页的
        ``<table><tr><th>今开: …`` ），原样注入既占 token 又干扰模型阅读。
        """
        t = re.sub(r"<[^>]{1,200}>", " ", text or "")
        t = re.sub(r"&nbsp;?", " ", t)
        return re.sub(r"\s{2,}", " ", t).strip()

    @classmethod
    def _search_items(cls, payload: Any) -> List[Dict[str, Any]]:
        """从不同搜索引擎的 JSON 响应中提取结果条目。

        统一字段：``title`` / ``link`` / ``snippet``（+ 千帆额外给
        ``site`` 站点名、``date`` 发布时间、``score`` 重排分、``aladdin`` 阿拉丁标记）。

        已适配：
        - Brave：``web.results[]``（title / url / description）
        - Bing / SerpAPI：``webPages.value[]`` / ``organic_results[]``
        - 百度千帆 AI 搜索：``references[]``
          （title / url / content+snippet / website / date / rerank_score / is_aladdin）
        """
        items: List[Dict[str, Any]] = []
        if not isinstance(payload, dict):
            return items
        # Brave
        web = payload.get("web") or {}
        for r in (web.get("results") or []):
            if isinstance(r, dict):
                items.append({"title": str(r.get("title") or ""),
                              "link": cls._clean_url(
                                  str(r.get("url") or r.get("link") or "")),
                              "snippet": str(r.get("description") or "")})
        # Bing / SerpAPI 通用（SerpAPI 的 organic_results 用的字段是 link，不是 url）
        for key in ("webPages", "organic_results"):
            block = payload.get(key)
            if isinstance(block, dict):
                block = block.get("value") or []
            for r in (block or []):
                if isinstance(r, dict):
                    items.append({"title": str(r.get("name") or r.get("title") or ""),
                                  "link": cls._clean_url(
                                      str(r.get("url") or r.get("link") or "")),
                                  "snippet": str(r.get("snippet") or r.get("description") or "")})
        # 百度千帆 AI 搜索
        for r in (payload.get("references") or []):
            if not isinstance(r, dict):
                continue
            snippet = str(r.get("content") or r.get("snippet") or "").strip()
            if not snippet:
                snippet = str(r.get("snippet") or "").strip()
            items.append({
                "title": str(r.get("title") or r.get("web_anchor") or ""),
                "link": cls._clean_url(str(r.get("url") or "")),
                "snippet": snippet,
                "site": str(r.get("website") or ""),
                "date": str(r.get("date") or ""),
                "score": r.get("rerank_score"),
                "authority": r.get("authority_score"),
                "aladdin": bool(r.get("is_aladdin")),
            })
        # 统一清理：标题/摘要里的 HTML 片段与多余空白（见 _plain_text）
        for it in items:
            it["title"] = cls._plain_text(it.get("title") or "")
            it["snippet"] = cls._plain_text(it.get("snippet") or "")
        return items

    @staticmethod
    def _rank_search_items(items: List[Dict[str, Any]],
                           keep_aladdin: bool = True) -> List[Dict[str, Any]]:
        """结果过滤与排序（用户反馈「检索不到正确内容」的第二根因）。

        实测（2026-09-21，query「小马宝莉 新世代 重映 时间」）：千帆会把
        **阿拉丁卡片**混在网页结果里，且常排在前面——例如「XX参与配音的动画作品」
        这类百科 starmap 页，与查询意图无关，模型据此作答自然「内容不对」。
        处理：
        1. 丢掉没有标题或没有链接的条目（模型无法给出可点开的来源）；
        2. 有普通网页结果时，阿拉丁卡片一律排到最后（全是阿拉丁时才保留）；
        3. 有条目带 ``rerank_score`` / ``authority_score`` 时按分数降序。
        """

        def _num(v: Any) -> float:
            try:
                return float(v)
            except Exception:  # noqa: BLE001
                return 0.0

        def _score(it: Dict[str, Any]) -> Tuple[float, float]:
            return (_num(it.get("score")), _num(it.get("authority")))

        good = [i for i in items if (i.get("title") or "").strip()
                and (i.get("link") or "").strip()]
        ok = [i for i in good if not i.get("aladdin")]
        aladdin = [i for i in good if i.get("aladdin")]
        ok.sort(key=_score, reverse=True)
        aladdin.sort(key=_score, reverse=True)
        if ok:
            # 有正常网页结果时最多补 1 条阿拉丁（噪音不占满注入名额）
            return ok + (aladdin[:1] if keep_aladdin else [])
        # 全是阿拉丁（如纯百科/人物类 query）才整体保留
        return aladdin if keep_aladdin else []

    @classmethod
    def _is_qianfan(cls, base: str) -> bool:
        """地址是否是百度千帆 AI 搜索（POST 协议）。"""
        return cls._QIANFAN_HOST in (base or "").lower()

    @classmethod
    def _qianfan_search_url(cls, base: str) -> str:
        """把用户填的任意千帆地址（含 /config）归一到可检索的 web_search 端点。"""
        b = (base or "").strip()
        if not b:
            return f"https://{cls._QIANFAN_HOST}/v2/ai_search/web_search"
        if "/v2/" in b:
            return b.split("/v2/")[0].rstrip("/") + "/v2/ai_search/web_search"
        return b.rstrip("/") + "/v2/ai_search/web_search"

    #: 千帆 messages[].content 上限：72 个字符（1 个汉字按 2 个字符计，官方文档）
    _QIANFAN_QUERY_LIMIT = 72
    #: 「编号类」标识：拉丁字母 + 3 位以上数字（+ 可选尾字母）
    _ID_TOKEN_RE = re.compile(r"[a-z]{1,10}\d{3,}[a-z]?", re.I)
    #: 口语化指令前缀（不携带检索信息，占满 72 字符额度只会把真意图挤掉）
    _QUERY_PREFIX_RE = re.compile(
        r"^(?:帮我查一下|帮忙查一下|帮我搜一下|帮忙搜一下|帮我看看|帮我搜|"
        r"查一下|查一查|搜一下|搜索一下|帮我|帮忙|我想知道|我想问|问一下|"
        r"请帮我|请问|麻烦|请|能不能|可以帮我)[，,、。:：\s]*")

    @classmethod
    def _query_ids(cls, query: str) -> List[str]:
        """取出查询里的「编号类」标识（TOI-6019b / RTX 5090 / arXiv:2606.20224…）。

        形态：**拉丁字母 + 3 位以上数字**（+ 可选尾字母），比较时去掉
        ``- _ . 空格`` 等分隔符并转小写（于是「TOI-6019 b」≡「TOI-6019b」）。
        这类标识是**强约束**：命中说明页面真的在讲这个东西，不命中就是无关页面。
        """
        flat = re.sub(r"[^0-9a-zA-Z]", "", query or "").lower()
        ids: List[str] = []
        for m in cls._ID_TOKEN_RE.finditer(flat):
            tok = m.group(0)
            if tok not in ids:
                ids.append(tok)
        return ids[:2]

    @staticmethod
    def _normalized(text: str) -> str:
        """比较用归一化：只留数字与小写字母（中文会被去掉）。"""
        return re.sub(r"[^0-9a-z]", "", (text or "").lower())

    @classmethod
    def _filter_by_ids(cls, items: List[Dict[str, Any]],
                       ids: List[str]) -> List[Dict[str, Any]]:
        """编号类查询的相关性门槛：丢掉标题/摘要里**完全不含该编号**的条目。

        实测（2026-09-21）：
        - query「TOI-6019b 这颗系外行星」→ 5 条全是「甲烷巨行星」SEO 新闻，含编号 0 条；
        - query「TOI-6019b」→ **第 1 条就是 arXiv 上讨论它的论文**；
        泛化词会把编号「稀释」掉（连加一个「系外行星」都会），于是模型拿到的
        全是无关页面 —— 用户反馈「返回的东西比上次还离谱」「这是污染」。
        这里直接按编号做硬门槛：不含编号的一律不进上下文。
        """
        if not ids or not items:
            return items
        kept = [it for it in items
                if any(i in cls._normalized((it.get("title") or "")
                                            + (it.get("snippet") or ""))
                       for i in ids)]
        if len(kept) != len(items):
            logger.info("搜索结果编号过滤：丢弃 %d 条不含 %s 的无关页面",
                        len(items) - len(kept), ids)
        return kept

    @classmethod
    def _qianfan_query(cls, query: str) -> Tuple[str, bool]:
        """把 query 压进千帆的 72 字符额度，返回 ``(关键词, 是否做了截断)``。

        用户反馈「无法正确检索到正确的内容」的根因：官方限制 ``messages[].content``
        **最多 72 个字符（1 汉字=2 字符），超长只取前 72 个字符**。实测把
        「帮我查一下腾讯公司今天最新的股价…（52 汉字）」和它「前 36 个汉字」分别
        提交，返回的 5 条结果**完全相同** —— 也就是说问句后半段（真正的检索意图）
        根本没参与检索。这里先剥掉「帮我查一下」这类不携带信息的指令前缀，
        再按 2/1 权重截断到额度内；被截断时调用方会打开服务端的 query 改写。
        """
        q = re.sub(r"\s+", " ", (query or "").strip())
        q = cls._QUERY_PREFIX_RE.sub("", q).strip() or q
        # 编号类查询：泛化词会把编号「稀释」掉——实测「TOI-6019b 这颗系外行星」
        # 返回 0/5 相关（全是甲烷巨行星新闻），而「TOI-6019b」第一条就是 arXiv
        # 上真正讨论它的论文。因此**只发编号本身**（不加改写，避免被改写成泛化词）。
        ids = cls._query_ids(q)
        if ids:
            logger.info("编号类查询只发编号本身：%r → %r", q, " ".join(ids))
            return " ".join(ids), False
        budget = cls._QIANFAN_QUERY_LIMIT
        out: List[str] = []
        for ch in q:
            w = 2 if ord(ch) > 127 else 1
            if budget - w < 0:
                break
            budget -= w
            out.append(ch)
        kept = "".join(out)
        trimmed = len(kept) < len(q)
        if trimmed:
            logger.info("联网搜索 query 超出千帆 %d 字符额度，已截断：%r → %r",
                        cls._QIANFAN_QUERY_LIMIT, q, kept)
        return (kept or q[:36]), trimmed

    # V2-D5：单个 provider 搜索，返回 items 列表；失败抛异常（供 fallback 链捕获）。
    @classmethod
    def _search_provider_items(cls, base: str, key: str, query: str,
                               top_n: int = 5) -> List[Dict[str, Any]]:
        """按端点类型发一次检索请求。

        协议：
        - 百度千帆 AI 搜索：POST ``/v2/ai_search/web_search``，
          body ``{"messages": [...], "search_source": "baidu_search_v2", ...}``
        - Brave / Bing：GET，各自专用鉴权头
        - 其它：GET（``base`` 含 ``{q}`` 时当模板替换，否则拼 ``?q=``）
        """
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        q = (query or "").strip()
        if cls._is_qianfan(base):
            url = cls._qianfan_search_url(base)
            kw, trimmed = cls._qianfan_query(q)
            # 官方参数（2026-09-14 文档）：
            #  - messages[].content 上限 72 字符（1 汉字=2），超长**只取前 72 字符**；
            #  - resource_type_filter web.top_k 上限 50（默认 20）；
            #  - sort.priority=auto：按 query 类型自动排序（强时效问题推荐）；
            #  - query_policy.enable_rewrite：长 query 效果增强（被我们截断时才开）。
            req_body: Dict[str, Any] = {
                "messages": [{"role": "user", "content": kw}],
                "search_source": "baidu_search_v2",
                # 多要几条候选（注入对话时仍只取 top_n）：编号类查询更要多取，
                # 因为无关页面会挤在前面，靠后面的命中条目救回来（会按编号过滤）
                "resource_type_filter": [
                    {"type": "web",
                     "top_k": max(20 if cls._query_ids(q) else 10,
                                  min(int(top_n or 5), 50))}],
                "sort": {"priority": "auto"},
            }
            if trimmed:
                req_body["query_policy"] = {"enable_rewrite": True}
            body = json.dumps(req_body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "User-Agent": "AI-DeskMate/1.0"})
        else:
            quoted = urllib.parse.quote(q)
            url = (base.replace("{q}", quoted) if "{q}" in base
                   else f"{base}?q={quoted}")
            headers = {"User-Agent": "AI-DeskMate/1.0"}
            # 常见官方端点的鉴权头
            if "search.brave.com" in base:
                headers["X-Subscription-Token"] = key
            elif "microsoft" in base or "bing" in base.lower():
                headers["Ocp-Apim-Subscription-Key"] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
            req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"网络不可达 {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"响应不是 JSON（前 120 字）：{raw[:120]}") from exc
        return cls._search_items(payload)

    def _search_chain(self, providers: List[Dict[str, Any]], query: str,
                      top_n: int, allow_silent: bool = False,
                      strict: bool = False) -> str:
        """按 provider 顺序检索，返回已格式化的结果文本。

        两类「没结果」严格区分：
        - **失败**（HTTP 非 2xx / 网络不可达 / 响应非 JSON）：单引擎时抛异常，
          把原因带到界面上（``allow_silent=True`` 的多引擎链则静默降级）；
        - **接口连通但没有结果**：只可能发生在地址填错端点（如千帆填了
          ``/ai_search/config``）。聊天路径不因此中断对话，仅当 ``strict=True``
          （设置面板「测试搜索」）时抛出，附带「该填哪个端点」的提示。
        """
        errors: List[str] = []
        empties: List[str] = []
        # 编号类查询（TOI-6019b / RTX 5090…）加「必须含该编号」的相关性门槛：
        # 泛化词会把编号稀释成无关页面，这些页面进上下文就是「污染」（见 _filter_by_ids）
        ids = self._query_ids(query)
        for p in providers:
            try:
                items = self._search_provider_items(p["base"], p["key"], query,
                                                    top_n)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{p['label']}：{exc}")
                continue
            raw_n = len(items)
            items = self._filter_by_ids(items, ids)
            items = self._rank_search_items(items)
            if items:
                lines = []
                for it in items[:top_n]:
                    title = (it.get("title") or "").strip()
                    link = (it.get("link") or "").strip()
                    snippet = (it.get("snippet") or "").strip()
                    # 单条摘要截断，避免 5 条 × 2000 字把上下文撑爆
                    if len(snippet) > self.SEARCH_SNIPPET_CHARS:
                        snippet = snippet[:self.SEARCH_SNIPPET_CHARS] + "…"
                    meta = " · ".join(
                        x for x in ((it.get("site") or "").strip(),
                                    (it.get("date") or "").strip()) if x)
                    head = f"- {title}" + (f"（{meta}）" if meta else "")
                    lines.append(f"{head}\n  {snippet}\n  来源: {link}")
                return "\n".join(lines)
            if raw_n and ids:
                empties.append(
                    f"{p['label']}：返回的 {raw_n} 条结果都与查询编号"
                    f"（{'/'.join(ids)}）无关，已全部丢弃")
            else:
                empties.append(
                    f"{p['label']}：接口连通但未返回结果——请确认地址填的是「检索」端点"
                    f"（Brave 填 …/res/v1/web/search；百度千帆填 …/v2/ai_search/web_search，"
                    f"不是 /config）")
        if errors and not allow_silent:
            raise RuntimeError("搜索失败：" + "；".join(errors))
        if strict and not errors and empties:
            raise RuntimeError("搜索失败：" + "；".join(empties))
        return ""

    def web_search(self, query: str, top_n: int = 5) -> str:
        """多引擎联网搜索（V2-D5）：配置 web.search_providers 链 + 默认单引擎，
        失败自动 fallback 到下一个引擎。

        配置：
        - web.search_base + web.search_key：默认引擎（支持含 {q} 的模板 URL；
          兼容 Brave / Bing 官方端点、百度千帆 AI 搜索）
        - web.search_providers：[{"name": "...", "base": "...", "key": "..."}, ...]
          额外引擎（依次作为 fallback）

        失败策略：单引擎（或关闭 fallback）请求失败时抛异常并在界面提示原因；
        多引擎链全部失败保持「静默降级为无搜索结果」的旧行为。
        """
        cfg = self._cfg
        providers: List[Dict[str, Any]] = []
        base = cfg.web_search_base()
        key = cfg.web_search_key()
        if base and key:
            providers.append({"base": base, "key": key, "label": "默认引擎"})
        for i, p in enumerate(cfg.web_search_providers() or []):
            if isinstance(p, dict) and p.get("base") and p.get("key"):
                providers.append({
                    "base": str(p["base"]), "key": str(p["key"]),
                    "label": str(p.get("name") or f"引擎{i + 1}")})
        if not providers:
            raise RuntimeError("未配置搜索引擎 API（设置 → 联网搜索）")
        allow_silent = len(providers) > 1 and bool(cfg.web_search_fallback())
        return self._search_chain(providers, query, top_n,
                                  allow_silent=allow_silent)

    def web_search_custom(self, base: str, key: str, query: str,
                          top_n: int = 5) -> str:
        """用**指定**的地址 / KEY 检索一次（不读配置，失败必抛异常）。

        供设置面板「测试搜索」使用：填完地址与 KEY 先点一次，立刻知道能不能用；
        地址填错端点（如千帆的 /config）时也会明确报出来。
        """
        if not (base or "").strip():
            raise RuntimeError("请先填写搜索引擎 API 地址")
        if not (key or "").strip():
            raise RuntimeError("请先填写搜索引擎 API Key")
        return self._search_chain(
            [{"base": base.strip(), "key": key.strip(), "label": "测试引擎"}],
            query, top_n, strict=True)

    # ------------------------------------------------------------ 打开网址
    def web_fetch(self, url: str, max_chars: int = 20000) -> str:
        """抓取指定网址的**网页正文**，返回「来源 + 标题 + 正文」。

        需求（用户反馈「\\@network 工具无法访问网址」）：此前 `\\@network` 无论后面
        跟什么都只走搜索引擎——贴一个网址过去，它只会把这个网址当关键词去搜，
        **页面本身从来没打开过**。现在由本方法真正 GET 页面、去脚本/样式/标签后
        把正文交给模型。

        失败一律抛异常（调用方把原因显示给用户，不再静默「没查到」）。
        """
        import urllib.error
        import urllib.request

        u = (url or "").strip()
        if not u:
            raise RuntimeError("网址为空")
        if not re.match(r"^https?://", u, re.I):
            u = "https://" + u
        req = urllib.request.Request(u, headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,"
                      "text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                final_url = str(resp.geturl() or u)
                ctype = str(resp.headers.get("Content-Type") or "").lower()
                raw = resp.read(_WEB_FETCH_MAX_BYTES + 1)
                enc = str(resp.headers.get("Content-Encoding") or "").lower()
        except urllib.error.HTTPError as exc:  # 4xx/5xx：带上服务端说明
            detail = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
                # 错误页常是一整篇 HTML：先去标签再截断，避免把源码糊到界面上
                detail = html_to_text(body)[:150]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"网络不可达 {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"打开网址失败：{exc}") from exc

        if enc == "gzip" or raw[:2] == b"\x1f\x8b":
            try:
                import gzip
                raw = gzip.decompress(raw)
            except Exception:  # noqa: BLE001
                pass
        truncated = len(raw) > _WEB_FETCH_MAX_BYTES
        raw = raw[:_WEB_FETCH_MAX_BYTES]
        kind = ctype.split(";")[0].strip()
        if kind and not any(k in kind for k in
                            ("html", "xml", "json", "text", "plain")):
            raise RuntimeError(
                f"该网址返回的是「{kind}」，不是网页文本"
                f"（PDF/图片/压缩包请下载后作为附件发送）")

        charset = ""
        m = re.search(r"charset=([\w\-]+)", ctype)
        if m:
            charset = m.group(1)
        decoded = ""
        for ce in [charset, "utf-8", "gb18030"]:
            if not ce:
                continue
            try:
                decoded = raw.decode(ce)
                break
            except Exception:  # noqa: BLE001
                continue
        if not decoded:
            decoded = raw.decode("utf-8", errors="replace")

        is_html = ("html" in ctype) or ("<html" in decoded[:2000].lower()) \
            or ("<body" in decoded[:4000].lower())
        if is_html:
            parser = _HtmlTextExtractor()
            try:
                parser.feed(decoded)
                parser.close()
            except Exception:  # noqa: BLE001
                pass
            text = parser.text() or html_to_text(decoded)
            title = re.sub(r"\s+", " ", parser.title).strip()
        else:
            text, title = decoded.strip(), ""
        text = self._clip_page(text, max_chars)
        logger.info("网页抓取成功：%s（%d 字，类型 %s）", final_url, len(text), ctype)
        head = f"来源网址: {final_url}\n"
        if title:
            head += f"网页标题: {title}\n"
        if truncated:
            head += "（页面过大，仅保留前 3MB 内容）\n"
        return f"{head}网页正文:\n{text}"

    @staticmethod
    def _clip_page(text: str, max_chars: int) -> str:
        """网页正文超长时**保留开头 + 结尾**并写明缺口（与附件注入同一策略）。"""
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        keep_head = int(max_chars * 0.8)
        keep_tail = max(0, max_chars - keep_head)
        gap = len(text) - keep_head - keep_tail
        return (text[:keep_head]
                + f"\n……（网页正文共 {len(text)} 字，中间 {gap} 字省略）……\n"
                + (text[-keep_tail:] if keep_tail else ""))

    # ------------------------------------------------------------ 工具
    @staticmethod
    def parse_json_loose(text: str) -> Any:
        """LLM 输出常包裹 JSON 于散文/代码块中，提取首个 {...} 块并解析。"""
        import json

        t = (text or "").strip()
        if t.startswith("```"):
            t = t.strip("`")
            if t.lower().startswith("json"):
                t = t[4:]
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end > start:
            t = t[start:end + 1]
        try:
            return json.loads(t)
        except Exception:  # noqa: BLE001
            return None


# ----------------------------------------------------------------------
# QThread 异步封装（仅在有 Qt 环境时定义）
# ----------------------------------------------------------------------
if _HAS_QT:

    class LLMWorker(QThread):
        """通用 LLM 子线程任务：流式/非流式调用，信号回传主线程。

        铁律：子线程中禁止创建/访问任何 QWidget；仅允许通过 Signal 与主线程通信。
        """

        stream_chunk = Signal(str)      # 流式片段
        request_finished = Signal(str)  # 完整回复
        request_error = Signal(str)     # 异常信息

        def __init__(
            self,
            messages: List[Dict[str, Any]],
            kind: str = "main",
            stream: bool = True,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            timeout: Optional[float] = None,
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self._messages = messages
            self._kind = kind
            self._stream = stream
            self._temperature = temperature
            self._max_tokens = max_tokens
            self._timeout = timeout
            self._pool = LLMClientPool.instance()

        def run(self) -> None:  # noqa: D102
            try:
                if self._stream:
                    parts: List[str] = []
                    for piece in self._pool.chat_stream(
                        self._messages,
                        kind=self._kind,
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                        timeout=self._timeout,
                    ):
                        parts.append(piece)
                        self.stream_chunk.emit(piece)
                    self.request_finished.emit("".join(parts))
                else:
                    # 修复（2026-09-22 用户反馈「桌宠悬浮对话第二轮无输出」）：
                    # 旧实现非流式分支**丢掉了 max_tokens / temperature**，一律按
                    # chat_complete 默认 512 走。而 deepseek-flash 这类**推理模型**
                    # 的 reasoning 就要 200~650 tokens，预算被吃光 → content 为空
                    # （实测 max_tokens=200 时 finish=length、content 长度 0）。
                    # 桌宠问候 / 专注鼓励 / 休息提醒 / 心跳关怀全部走这条路径，
                    # 表现就是「只有兜底文案 / 干脆没输出」。
                    _mt = (int(self._max_tokens) if self._max_tokens
                           else 512)
                    # 关键：调用方给的 max_tokens 是**正文**长度意图（如桌宠
                    # 「≤100 字」→200），而推理模型的 reasoning 也计入该预算。
                    # 直接按 200 下发会全被思考吃光 → 正文为空、还要多轮重试。
                    # 这里按「意图 ×4（最少 +1024）」留出推理空间：
                    # 一次请求即可产出正文，且提示词里的字数要求仍会约束实际长度。
                    _mt = max(_mt * 4, _mt + 1024)
                    _temp = (float(self._temperature)
                             if self._temperature is not None else 0.2)
                    # 说明：`chat_complete` 内部已有「关思考试探 + 放大预算」兜底，
                    # 这里不再叠加一次重试（重复重试只会让失败场景等待翻倍）。
                    text = self._pool.chat_complete(
                        self._messages, kind=self._kind,
                        temperature=_temp, max_tokens=_mt,
                        timeout=self._timeout,
                    )
                    self.request_finished.emit(text)
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLMWorker 请求失败")
                self.request_error.emit(f"{type(exc).__name__}: {exc}")
