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
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

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


class LLMClientPool:
    """工厂模式客户端池：按模型角色创建并缓存 OpenAI 兼容客户端。"""

    _instance: Optional["LLMClientPool"] = None

    MODEL_KINDS = ("main", "small", "vision", "image")

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._clients: Dict[str, "OpenAI"] = {}

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
        key = (api_key or "").strip() or "EMPTY"
        cache_key = f"custom:{base}"
        if cache_key not in self._clients:
            self._clients[cache_key] = OpenAI(
                base_url=base,
                api_key=key,
                timeout=timeout if timeout is not None else 60.0,
                max_retries=1,
            )
        return self._clients[cache_key]

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
    ) -> Iterator[str]:
        """流式生成：使用指定端点/Key/模型（技能独立 API；为空回退主 API 配置）。"""
        client = self.client_custom(base_url, api_key, timeout=timeout)
        model_name = (model or "").strip() or self._cfg.main_model()
        stream = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=(
                temperature if temperature is not None
                else float(self._cfg.get("chat", "temperature", default=0.7))
            ),
            max_tokens=(
                max_tokens if max_tokens is not None
                else int(self._cfg.get("chat", "max_tokens", default=2048))
            ),
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece
            if reasoning_cb is not None:
                r = getattr(delta, "reasoning_content", None)
                if r is None:
                    r = getattr(delta, "thinking", None)
                if r:
                    reasoning_cb(r)

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
        """非流式补全：使用指定端点/Key/模型（技能独立 API）。"""
        client = self.client_custom(base_url, api_key, timeout=timeout)
        model_name = (model or "").strip() or self._cfg.main_model()
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return resp.choices[0].message.content or ""

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


    # ------------------------------------------------------------ 主对话（流式）
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        kind: str = "main",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        reasoning_cb: Optional[Callable[[str], None]] = None,
    ) -> Iterator[str]:
        """流式生成：逐片段 yield 文本。

        reasoning_cb 可选：DeepSeek-R1 / Qwen 等模型会在流中返回
        reasoning_content（思维链），开启思维链显示时通过该回调收集。
        """
        client = self.client(kind, timeout=timeout)
        stream = client.chat.completions.create(
            model=self._model(kind),
            messages=messages,
            temperature=(
                temperature if temperature is not None
                else float(self._cfg.get("chat", "temperature", default=0.7))
            ),
            max_tokens=(
                max_tokens if max_tokens is not None
                else int(self._cfg.get("chat", "max_tokens", default=2048))
            ),
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece
            if reasoning_cb is not None:
                r = getattr(delta, "reasoning_content", None)
                if r is None:
                    r = getattr(delta, "thinking", None)
                if r:
                    reasoning_cb(r)

    # ------------------------------------------------------------ 非流式补全
    def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        kind: str = "small",
        temperature: float = 0.2,
        max_tokens: int = 512,
        timeout: Optional[float] = None,
    ) -> str:
        """非流式补全（记忆小模型 / 一次性请求）。"""
        client = self.client(kind, timeout=timeout)
        resp = client.chat.completions.create(
            model=self._model(kind),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------ 多模态（视觉/文档）
    _TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js",
                      ".html", ".css", ".yaml", ".yml", ".ini", ".xml"}
    _DOC_SUFFIXES = {".pdf": "pdf", ".docx": "docx", ".doc": "doc",
                     ".xlsx": "xlsx", ".xls": "xls", ".pptx": "pptx", ".ppt": "ppt"}
    _IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

    @staticmethod
    def _extract_document_text(path: Path) -> Optional[str]:
        """提取文档文本：TXT/PDF/DOCX/XLSX/PPTX 等，纯文本类型直接读取。"""
        suffix = path.suffix.lower()
        if suffix in LLMClientPool._TEXT_SUFFIXES:
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
                parts = [page.extract_text() or "" for page in reader.pages[:30]]
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
                for ws in wb.worksheets[:5]:
                    lines.append(f"[Sheet: {ws.title}]")
                    for row in ws.iter_rows(values_only=True):
                        lines.append("\t".join("" if c is None else str(c) for c in row))
                return "\n".join(lines[:300])
            except Exception:
                return None
        if suffix in (".pptx", ".ppt"):
            try:
                from pptx import Presentation  # type: ignore
                prs = Presentation(str(path))
                lines = []
                for slide in prs.slides[:30]:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text:
                            lines.append(shape.text)
                return "\n".join(lines)
            except Exception:
                return None
        return None

    def document_describe(self, prompt: str, file_path: str,
                          max_tokens: int = 1024) -> str:
        """多模态文档理解：图片走视觉模型；文本/PDF/Word/Excel/PPT 提取文本后分析。

        支持：png/jpg/webp/gif/bmp · txt/md/json/csv · pdf/docx/xlsx/pptx 等。
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
        content = text[:12000] if len(text) > 12000 else text
        client = self.client("vision")
        resp = client.chat.completions.create(
            model=self._model("vision"),
            messages=[{
                "role": "user",
                "content": f"{prompt}\n\n[文件: {path.name}]\n文件内容:\n{content}",
            }],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------ 多模态（视觉）
    def vision_describe(self, prompt: str, image_path: str, max_tokens: int = 512) -> str:
        """图片理解：将本地图片 base64 编码后交给视觉模型描述。"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {path}")
        suffix = path.suffix.lower().lstrip(".")
        mime = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
        }.get(suffix, "image/png")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        client = self.client("vision")
        resp = client.chat.completions.create(
            model=self._model("vision"),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------ 多模态（生图）
    def image_generate(self, prompt: str, size: str = "1024x1024",
                       base_url: str = "", api_key: str = "",
                       model: str = "") -> str:
        """调用生图 API（OpenAI /images/generations 兼容）生成图片，返回本地文件路径。

        优先级：显式传入 base_url > 全局生图 API（image_base）> 主 API。
        API 返回 b64_json 或 url 均可；图片保存到 img/generated/gen_<时间戳>_<hex>.png。
        """
        cfg = self._cfg
        base = (base_url or "").strip() or cfg.image_base() or cfg.api_base()
        key = (api_key or "").strip() or cfg.image_key() or cfg.api_key()
        model_name = (model or "").strip() or cfg.image_model() or "dall-e-3"
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请执行 pip install -r requirements.txt")
        client = self.client_custom(base, key, timeout=180.0)
        resp = client.images.generate(
            model=model_name, prompt=prompt, size=size, n=1)
        data = getattr(resp, "data", None) or []
        if not data:
            raise RuntimeError("生图接口未返回图片数据")
        item = data[0]
        # 需求：生图/API 生成图片保存目录可在设置中配置（默认 <项目>/createimage）
        out_dir = cfg.image_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"gen_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        dest = out_dir / fname
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
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

    # ------------------------------------------------------------ 联网搜索（需求 v5）
    @staticmethod
    def _search_items(payload: Any) -> List[Dict[str, Any]]:
        """从不同搜索引擎的 JSON 响应中提取 (title, link, snippet)。"""
        items: List[Dict[str, Any]] = []
        if not isinstance(payload, dict):
            return items
        # Brave
        web = payload.get("web") or {}
        for r in (web.get("results") or []):
            if isinstance(r, dict):
                items.append({"title": str(r.get("title") or ""),
                              "link": str(r.get("url") or ""),
                              "snippet": str(r.get("description") or "")})
        # Bing / SerpAPI 通用
        for key in ("webPages", "organic_results"):
            block = payload.get(key)
            if isinstance(block, dict):
                block = block.get("value") or []
            for r in (block or []):
                if isinstance(r, dict):
                    items.append({"title": str(r.get("name") or r.get("title") or ""),
                                  "link": str(r.get("url") or ""),
                                  "snippet": str(r.get("snippet") or r.get("description") or "")})
        return items

    def web_search(self, query: str, top_n: int = 5) -> str:
        """调用搜索引擎 API 联网搜索，返回格式化摘要文本。

        配置：web.search_base（支持含 {q} 的模板 URL；也兼容 Brave/Bing 官方端点）
        + web.search_key。失败抛异常由调用方降级为普通对话。
        """
        import json
        import urllib.parse
        import urllib.request

        cfg = self._cfg
        base = cfg.web_search_base()
        key = cfg.web_search_key()
        if not base or not key:
            raise RuntimeError("未配置搜索引擎 API（设置 → 联网搜索）")
        q = urllib.parse.quote(query.strip())
        if "{q}" in base:
            url = base.replace("{q}", q)
        else:
            url = f"{base}?q={q}"
        headers = {"User-Agent": "AI-DeskMate/1.0"}
        # 常见官方端点的鉴权头
        if "search.brave.com" in base:
            headers["X-Subscription-Token"] = key
        elif "microsoft" in base or "bing" in base.lower():
            headers["Ocp-Apim-Subscription-Key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        items = self._search_items(payload)
        if not items:
            return ""
        lines = []
        for it in items[:top_n]:
            title = it.get("title") or ""
            link = it.get("link") or ""
            snippet = it.get("snippet") or ""
            lines.append(f"- {title}\n  {snippet}\n  来源: {link}")
        return "\n".join(lines)

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
                    text = self._pool.chat_complete(
                        self._messages, kind=self._kind, timeout=self._timeout
                    )
                    self.request_finished.emit(text)
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLMWorker 请求失败")
                self.request_error.emit(f"{type(exc).__name__}: {exc}")
