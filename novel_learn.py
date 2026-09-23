# -*- coding: utf-8 -*-
"""
novel_learn.py — \\@novellearn 小说学习工具（总结 + 存档 + 角色扮演复刻）

做什么：
    1. 读取上传的小说附件（txt/md/pdf/docx 等），分块交给模型提炼；
    2. 输出一份结构化总结：摘要 / 剧情 / 时间 / 人物 / 地点场景 / 风格类型 /
       人物心理与剧情发展 / 故事情节；
    3. 额外产出「角色扮演复刻」段（世界观 + 角色卡 + 开场），本次对话即可直接
       按小说剧情开始角色扮演（主界面把该段注入后续对话上下文）；
    4. 把这份总结以 JSON 存到 novellearn/<文件名>_<年月日时分>.json，供以后读取。

不含任何 QWidget：全部为纯数据/IO，可在子线程执行。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config_loader import project_root

STORE_DIR = project_root() / "novellearn"
CHUNK_SIZE = 6000
MAX_CHUNKS = 14          # 超长小说最多处理前 ~84k 字，避免单次调用过久

SECTIONS = ("摘要", "剧情梗概", "时间背景", "人物", "地点场景",
            "风格类型", "人物心理与剧情发展", "故事情节", "角色扮演复刻")

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> Dict[str, Any]:
    """从模型输出里抠出第一个完整 JSON 对象；失败返回空字典。"""
    if not text:
        return {}
    m = _JSON_RE.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _read_text(path: Path) -> str:
    """读取小说正文（优先复用 LLMClientPool 的文档提取，兼容 pdf/docx）。"""
    try:
        from llm_client import LLMClientPool
        txt = LLMClientPool._extract_document_text(path)  # noqa: SLF001
        if txt:
            return txt
    except Exception:  # noqa: BLE001
        pass
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return path.read_text(encoding=enc, errors="replace")
        except Exception:  # noqa: BLE001
            continue
    return ""


def _chunks(text: str) -> List[str]:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not text:
        return []
    out = []
    for i in range(0, len(text), CHUNK_SIZE):
        out.append(text[i:i + CHUNK_SIZE])
        if len(out) >= MAX_CHUNKS:
            break
    return out


class NovelLearn:
    """小说学习：总结 + 存档。"""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool

    # ------------------------------------------------------------ 存档
    @staticmethod
    def store_dir() -> Path:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        return STORE_DIR

    @staticmethod
    def list_saved() -> List[Dict[str, Any]]:
        """列出已保存的总结（按时间倒序）。"""
        if not STORE_DIR.is_dir():
            return []
        items = []
        for p in sorted(STORE_DIR.glob("*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items.append({"path": str(p), "name": p.name,
                          "book": str((data.get("meta") or {}).get("book") or ""),
                          "saved_at": str((data.get("meta") or {}).get("saved_at") or "")})
        return items

    @staticmethod
    def load_saved(keyword: str) -> Optional[Dict[str, Any]]:
        """按关键词（文件名/书名）读取已有总结。"""
        kw = (keyword or "").strip().lower()
        if not kw:
            return None
        for item in NovelLearn.list_saved():
            hay = (item["name"] + " " + item["book"]).lower()
            if kw in hay:
                try:
                    return json.loads(Path(item["path"]).read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    return None
        return None

    # ------------------------------------------------------------ 总结
    def summarize(self, path: str,
                  on_progress: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """总结一本小说，返回 {"data": {...}, "path": 存档路径, "markdown": 正文}。"""
        if self._pool is None:
            raise RuntimeError("模型客户端不可用")
        p = Path(path)
        text = _read_text(p)
        if not text.strip():
            raise RuntimeError(f"无法读取小说内容：{p.name}")
        parts = _chunks(text)
        if on_progress:
            on_progress(5, f"共 {len(parts)} 段，开始分段提炼…")

        notes: List[Dict[str, Any]] = []
        for i, chunk in enumerate(parts, 1):
            note = self._summarize_chunk(chunk, i, len(parts))
            notes.append(note)
            if on_progress:
                on_progress(5 + int(70 * i / max(1, len(parts))),
                            f"已提炼 {i}/{len(parts)} 段")
        if on_progress:
            on_progress(80, "汇总成总结摘要…")
        merged = self._merge(p.name, notes)
        if on_progress:
            on_progress(92, "写入 novellearn 存档…")
        saved = self._save(p.name, merged, extra={"chars": len(text),
                                                  "chunks": len(parts)})
        return {"data": merged, "path": saved,
                "markdown": format_summary_md(merged, saved)}

    def _call(self, system: str, user: str, max_tokens: int = 2000,
              temperature: float = 0.3) -> str:
        fn = getattr(self._pool, "chat_complete_custom", None)
        if fn is None:
            raise RuntimeError("模型客户端不支持 chat_complete_custom")
        return fn([{"role": "system", "content": system},
                   {"role": "user", "content": user}],
                  temperature=temperature, max_tokens=max_tokens)

    def _summarize_chunk(self, chunk: str, idx: int, total: int) -> Dict[str, Any]:
        system = ("你是小说分析助手。只输出 JSON（不要 Markdown 代码块、不要解释），"
                  "字段：events(本段关键事件数组，每条一句话)、characters(出现人物名数组)、"
                  "places(地点场景数组)、psychology(本段人物心理与动机，一句话)、"
                  "time(本段所处时间/年代线索，没有则空字符串)。")
        user = f"第 {idx}/{total} 段原文：\n{chunk}"
        raw = self._call(system, user, max_tokens=1200)
        data = _extract_json(raw)
        return {
            "events": data.get("events") or [],
            "characters": data.get("characters") or [],
            "places": data.get("places") or [],
            "psychology": str(data.get("psychology") or ""),
            "time": str(data.get("time") or ""),
        }

    def _merge(self, book: str, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        system = (
            "你是小说分析专家，请把各段笔记汇总成一份可用于「角色扮演复刻原作剧情」的设定档案。"
            "只输出 JSON（不要 Markdown 代码块、不要解释），结构如下：\n"
            "{\n"
            '  "摘要": "一段话概括全书",\n'
            '  "剧情梗概": "主线剧情，200-400 字",\n'
            '  "时间背景": "年代 / 时间线 / 关键时间点",\n'
            '  "人物": [{"name":"","identity":"身份","traits":"性格特征","relations":"人物关系"}],\n'
            '  "地点场景": [{"name":"","desc":"场景氛围与作用"}],\n'
            '  "风格类型": "题材 / 文风 / 叙事风格",\n'
            '  "人物心理与剧情发展": "主要人物的心理变化与剧情推进脉络",\n'
            '  "故事情节": [{"stage":"起/承/转/合或章节区间","content":"情节内容"}],\n'
            '  "角色扮演复刻": {"worldview":"世界观与规则","character_cards":'
            '[{"name":"","persona":"人设","speaking_style":"说话风格"}],'
            '"opening":"开场情境与首句台词"}\n'
            "}\n"
            "所有内容使用中文，忠实原作，不要杜撰原作没有的关键剧情。"
        )
        user = f"书名/文件名：{book}\n分段笔记：\n{json.dumps(notes, ensure_ascii=False)[:24000]}"
        raw = self._call(system, user, max_tokens=4000, temperature=0.4)
        data = _extract_json(raw)
        if not data:
            data = {"摘要": raw[:1500]}
        for key in SECTIONS:
            data.setdefault(key, "" if key != "人物" else [])
        if not isinstance(data.get("人物"), list):
            data["人物"] = []
        if not isinstance(data.get("地点场景"), list):
            data["地点场景"] = []
        if not isinstance(data.get("故事情节"), list):
            data["故事情节"] = []
        if not isinstance(data.get("角色扮演复刻"), dict):
            data["角色扮演复刻"] = {}
        return data

    @staticmethod
    def _save(book: str, data: Dict[str, Any],
              extra: Optional[Dict[str, Any]] = None) -> str:
        NovelLearn.store_dir()
        stem = Path(book).stem or "novel"
        stamp = datetime.now().strftime("%Y%m%d%H%M")
        target = STORE_DIR / f"{stem}_{stamp}.json"
        payload = {
            "meta": {"book": book, "saved_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"), "version": 1},
            "summary": data,
        }
        if extra:
            payload["meta"].update(extra)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return str(target)

    @staticmethod
    def roleplay_context(data: Dict[str, Any]) -> str:
        """把总结转成注入后续对话的上下文（供本次对话复刻剧情）。"""
        if not data:
            return ""
        rp = data.get("角色扮演复刻") or {}
        lines = [
            "【小说复刻·角色扮演设定】以下设定来自用户刚刚学习的小说，"
            "本次对话请严格按它扮演与推进剧情（用户可用 \\closed 退出）：",
            f"摘要：{data.get('摘要', '')}",
            f"世界观：{rp.get('worldview', '')}",
            f"风格：{data.get('风格类型', '')}",
            f"时间背景：{data.get('时间背景', '')}",
        ]
        chars = data.get("人物") or []
        if chars:
            lines.append("主要人物：" + "、".join(
                f"{c.get('name', '')}（{c.get('identity', '')}：{c.get('traits', '')}）"
                for c in chars[:12] if isinstance(c, dict)))
        scenes = data.get("地点场景") or []
        if scenes:
            lines.append("地点场景：" + "、".join(
                str(s.get("name") or "") for s in scenes[:12] if isinstance(s, dict)))
        cards = rp.get("character_cards") or []
        if cards:
            lines.append("角色卡：" + " | ".join(
                f"{c.get('name', '')}: {c.get('persona', '')} "
                f"（语气：{c.get('speaking_style', '')}）"
                for c in cards[:12] if isinstance(c, dict)))
        if rp.get("opening"):
            lines.append(f"开场：{rp.get('opening')}")
        return "\n".join(x for x in lines if x)


def format_summary_md(data: Dict[str, Any], saved_path: str = "") -> str:
    """把总结渲染成聊天里展示的文本。"""
    if not data:
        return "（没有生成内容）"
    out: List[str] = ["**小说学习完成**\n"]
    out.append(f"**摘要**：{data.get('摘要', '')}\n")
    out.append(f"**剧情梗概**：{data.get('剧情梗概', '')}\n")
    out.append(f"**时间背景**：{data.get('时间背景', '')}\n")
    chars = data.get("人物") or []
    if chars:
        out.append("**人物**：")
        for c in chars[:15]:
            if isinstance(c, dict):
                out.append(f"- {c.get('name', '')}｜{c.get('identity', '')}｜"
                           f"{c.get('traits', '')}｜关系：{c.get('relations', '')}")
        out.append("")
    scenes = data.get("地点场景") or []
    if scenes:
        out.append("**地点场景**：")
        for s in scenes[:15]:
            if isinstance(s, dict):
                out.append(f"- {s.get('name', '')}：{s.get('desc', '')}")
        out.append("")
    out.append(f"**风格类型**：{data.get('风格类型', '')}\n")
    out.append(f"**人物心理与剧情发展**：{data.get('人物心理与剧情发展', '')}\n")
    story = data.get("故事情节") or []
    if story:
        out.append("**故事情节**：")
        for st in story[:20]:
            if isinstance(st, dict):
                out.append(f"- [{st.get('stage', '')}] {st.get('content', '')}")
        out.append("")
    rp = data.get("角色扮演复刻") or {}
    if rp:
        out.append("**角色扮演复刻**：")
        if rp.get("worldview"):
            out.append(f"- 世界观：{rp['worldview']}")
        for c in (rp.get("character_cards") or [])[:12]:
            if isinstance(c, dict):
                out.append(f"- 角色卡 {c.get('name', '')}：{c.get('persona', '')}"
                           f"（语气：{c.get('speaking_style', '')}）")
        if rp.get("opening"):
            out.append(f"- 开场：{rp['opening']}")
        out.append("")
    if saved_path:
        out.append(f"已存档：`{saved_path}`（以后可直接读取复刻）")
    return "\n".join(out)
