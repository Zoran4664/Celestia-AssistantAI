# -*- coding: utf-8 -*-
"""skill_import.py —— 导入外部技能包（.zip / .rar / .md）到 skill库（skillspub）。

支持的技能包格式（Anthropic 风格 skill 包，本机 ``skill custom/`` 即此结构）::

    <技能名>/SKILL.md          ← 必需：YAML frontmatter + 技能正文/执行说明
    <技能名>/references/*.md   ← 附带参考资料（可选）
    <技能名>/scripts/*.py      ← 附带脚本（可选）
    <技能名>/assets/...        ← 附带资源（可选）

也支持：SKILL.md 直接位于压缩包根目录；或直接导入单个 ``.md`` 文件。

规则（按需求）：
- **技能名 = 不含后缀的文件名**（压缩包取包名，包内只有唯一顶层目录时取该目录名；
  ``.md`` 取文件名）；
- 介绍 = ``SKILL.md`` 正文（原样保留，含 frontmatter，注入 AI 时可读）；
- 索引里的「skill简介」：优先 frontmatter 的 ``description``；
  **没有介绍时调用小模型总结；内容为英文时输出中英文双语**；
- 是否需要创建文件：由 frontmatter 的 ``allowed-tools``（Write/Edit/Bash）或正文
  关键词判断，供 UI 询问用户「是否允许生成文件」（允许后产出写入 skilluserdata）。

线程纪律：普通单例（不依赖 Qt），解压与摘要可在工作线程执行。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import skillspub_core as pub

logger = logging.getLogger("AI_DeskMate.SkillImport")

#: 支持的导入文件后缀
IMPORT_EXTS = (".zip", ".rar", ".md")
#: frontmatter 里声明需要写文件的工具名
_WRITE_TOOLS = ("write", "edit", "bash", "str_replace", "notebookedit")
#: 正文里的「要创建文件」信号（判定是否需要征询用户允许生成文件）
_FILE_INTENT_RE = re.compile(
    r"(创建文件|新建文件|生成文件|写入文件|写到文件|保存文件|保存到|另存为|"
    r"导出到|输出到|写入磁盘|落盘|生成\s*\.?(docx|xlsx|pptx|pdf|md|txt|csv)|"
    r"create\s+(a\s+)?file|write\s+(a\s+)?file|save\s+(the\s+)?(file|document)|"
    r"export\s+to|output\s+to)", re.I)
#: 摘要/简介的最大长度（与 skillspub_core.SUMMARY_LIMIT 保持单一来源）
_SUMMARY_LIMIT = pub.SUMMARY_LIMIT


# --------------------------------------------------------------------- 工具
def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """解析 SKILL.md 的 YAML frontmatter（不依赖 PyYAML，容错优先）。

    :return: (frontmatter 键值字典, 去掉 frontmatter 的正文)
    """
    src = (text or "").lstrip("\ufeff")
    if not src.startswith("---"):
        return {}, src
    lines = src.splitlines()
    if not lines or lines[0].strip() not in ("---", "---\r"):
        return {}, src
    meta: Dict[str, str] = {}
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end < 0:
        return {}, src
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'").strip()
        if key:
            meta[key] = value
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


def looks_english(text: str) -> bool:
    """内容是否以英文为主（用于决定摘要是否中英双语输出）。"""
    src = (text or "")[:4000]
    if not src.strip():
        return False
    ascii_letters = len(re.findall(r"[A-Za-z]", src))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", src))
    if cjk >= 20:
        return False
    return ascii_letters >= 80 and ascii_letters > cjk * 3


def needs_file_creation(text: str, meta: Optional[Dict[str, str]] = None) -> bool:
    """判断该技能是否可能需要创建/写入文件（供 UI 询问用户是否允许生成）。"""
    meta = meta or {}
    tools = str(meta.get("allowed-tools") or meta.get("allowed_tools") or "")
    if tools:
        low = tools.lower()
        if any(t in low for t in _WRITE_TOOLS):
            return True
    return bool(_FILE_INTENT_RE.search((text or "")[:6000]))


def _first_sentence(text: str, limit: int = _SUMMARY_LIMIT) -> str:
    """从正文里截第一段作为摘要兜底（去 markdown 标题/符号）。"""
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line or line.startswith(("#", "---", "```", "|")):
            continue
        line = re.sub(r"^[-*>+\d.\s]+", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if len(line) >= 12:
            return line[:limit]
    return ""


def _guess_keywords(name: str, summary: str, meta: Dict[str, str]) -> List[str]:
    """关键词：frontmatter 优先，其次从名称/摘要里取英文词，最后用技能名。"""
    raw = str(meta.get("keywords") or meta.get("tags") or "").strip()
    words: List[str] = []
    if raw:
        words = [w.strip() for w in re.split(r"[,，、;；|/]+", raw) if w.strip()]
    if not words:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", summary or "")][:6]
    if name and name not in words:
        words.insert(0, name)
    seen: List[str] = []
    for w in words:
        if w and w not in seen:
            seen.append(w)
    return seen[:10]


# --------------------------------------------------------------------- 解压
def _safe_extract_zip(src: Path, dest: Path) -> None:
    """解压 zip（拒绝路径穿越成员）。"""
    with zipfile.ZipFile(src, "r") as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"压缩包内含非法路径：{member}")
        zf.extractall(dest)


def _extract_rar(src: Path, dest: Path) -> str:
    """解压 rar：优先 rarfile 模块，其次 7z/unrar/WinRAR，最后 Windows tar(bsdtar)。

    :return: 错误信息（空串表示成功）
    """
    try:  # 1) rarfile + 系统 unrar
        import rarfile  # type: ignore
        rarfile.UNRAR_TOOL = rarfile.UNRAR_TOOL or "unrar"
        with rarfile.RarFile(str(src)) as rf:
            rf.extractall(str(dest))
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.info("rarfile 解压不可用：%s", exc)

    for exe, args in (
        ("7z", ["x", "-y", f"-o{dest}", str(src)]),
        ("7za", ["x", "-y", f"-o{dest}", str(src)]),
        ("unrar", ["x", "-y", str(src), str(dest) + os.sep]),
        ("WinRAR", ["x", "-y", str(src), str(dest) + os.sep]),
        ("tar", ["-xf", str(src), "-C", str(dest)]),
    ):
        path = shutil.which(exe)
        if not path:
            continue
        try:
            proc = subprocess.run(  # noqa: S603
                [path] + args, capture_output=True, timeout=180, check=False)
            if proc.returncode == 0 and any(dest.iterdir()):
                return ""
        except Exception as exc:  # noqa: BLE001
            logger.info("%s 解压失败：%s", exe, exc)
    return ("无法解压 .rar：请安装 WinRAR / 7-Zip，"
            "或执行 pip install rarfile（配合 unrar）后重试")


def _is_junk(name: str) -> bool:
    return (name.startswith("__MACOSX") or name.startswith(".")
            or name.endswith(".DS_Store"))


class SkillImporter:
    """技能包导入器（单例）。"""

    _instance: Optional["SkillImporter"] = None

    def __init__(self) -> None:
        self._prepared: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def instance(cls) -> "SkillImporter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ 预处理
    def _unpack(self, src: Path) -> Tuple[Optional[Path], str]:
        """解压/准备技能包内容目录，返回 (内容根目录, 错误信息)。"""
        src = Path(src)
        if not src.exists():
            return None, "文件不存在"
        suffix = src.suffix.lower()
        if suffix == ".md":
            tmp = Path(tempfile.mkdtemp(prefix="skillimport_"))
            shutil.copy2(str(src), str(tmp / src.name))
            return tmp, ""
        if suffix not in (".zip", ".rar"):
            return None, f"不支持的文件类型：{suffix or '（无后缀）'}"
        tmp = Path(tempfile.mkdtemp(prefix="skillimport_"))
        try:
            if suffix == ".zip":
                _safe_extract_zip(src, tmp)
            else:
                err = _extract_rar(src, tmp)
                if err:
                    return None, err
        except Exception as exc:  # noqa: BLE001
            return None, f"解压失败：{exc}"
        return tmp, ""

    @staticmethod
    def _skill_root(unpacked: Path, fallback_name: str) -> Tuple[Path, str]:
        """定位技能内容根目录与技能名（不含后缀）。

        包内只有一个顶层目录（且根下没有文件）→ 该目录即技能根、目录名为技能名；
        否则整个解压目录为根、取压缩包文件名（不含后缀）为技能名。
        """
        children = [p for p in unpacked.iterdir() if not _is_junk(p.name)]
        dirs = [p for p in children if p.is_dir()]
        files = [p for p in children if p.is_file()]
        if len(dirs) == 1 and not files:
            return dirs[0], dirs[0].name
        return unpacked, fallback_name

    @staticmethod
    def _find_skill_md(root: Path) -> Optional[Path]:
        """定位 SKILL.md：根目录 → 浅层 → 任意；找不到则退回唯一的 .md。"""
        direct = root / "SKILL.md"
        if direct.exists():
            return direct
        found = sorted((p for p in root.rglob("SKILL.md")),
                       key=lambda p: len(p.parts))
        if found:
            return found[0]
        mds = sorted((p for p in root.rglob("*.md") if not _is_junk(p.name)),
                     key=lambda p: len(p.parts))
        if len(mds) == 1:
            return mds[0]
        return mds[0] if mds else None

    def preview(self, src: Path) -> Dict[str, Any]:
        """预览一个技能包：技能名、是否需要创建文件、是否自带介绍。

        :return: {ok, path, name, folder, needs_files, has_desc, source, meta,
                  description, summary, msg, root}
        """
        src = Path(src)
        item: Dict[str, Any] = {
            "ok": False, "path": str(src), "name": "", "folder": "",
            "needs_files": False, "has_desc": False, "source": "",
            "meta": {}, "description": "", "summary": "", "msg": "", "root": "",
        }
        unpacked, err = self._unpack(src)
        if unpacked is None:
            item["msg"] = err
            return item
        try:
            root, name = self._skill_root(
                unpacked, src.stem if src.suffix.lower() != ".md" else src.stem)
            md = self._find_skill_md(root)
            text = ""
            if md is not None:
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    item["msg"] = f"读取 {md.name} 失败：{exc}"
                    return item
                item["source"] = md.name
            meta, body = parse_frontmatter(text)
            item["meta"] = meta
            item["name"] = str(name).strip() or src.stem
            item["folder"] = pub.folder_for_name(item["name"])
            item["description"] = (body or text).strip() or text.strip()
            item["has_desc"] = bool(str(meta.get("description") or "").strip())
            item["summary"] = str(meta.get("description") or "").strip()[:_SUMMARY_LIMIT]
            check_src = text or item["description"]
            item["needs_files"] = needs_file_creation(check_src, meta)
            item["root"] = str(root)
            item["ok"] = bool(item["description"] or item["summary"])
            if not item["ok"]:
                item["msg"] = "包内未找到可用的 SKILL.md（技能说明）"
            # 缓存完整预览结果（含 ok / needs_files），导入时直接复用，避免二次解压
            cached = dict(item)
            cached.update({"unpacked": unpacked, "root": str(root)})
            self._prepared[str(src)] = cached
            return item
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包预览失败 %s: %s", src, exc)
            item["msg"] = f"预览失败：{exc}"
            return item

    # ------------------------------------------------------------ 摘要
    def summarize(self, text: str, meta: Optional[Dict[str, str]] = None,
                  pool: Optional[Any] = None) -> Tuple[str, str]:
        """生成「skill简介」：优先 frontmatter description → 小模型 → 正文首段。

        英文技能要求小模型输出中英文双语。
        :return: (摘要, 来源说明)
        """
        meta = meta or {}
        desc = str(meta.get("description") or "").strip()
        if desc:
            return desc[:_SUMMARY_LIMIT], "frontmatter"
        body = (text or "").strip()
        if not body:
            return "", "empty"
        english = looks_english(body)
        try:
            if pool is None:
                from llm_client import LLMClientPool
                pool = LLMClientPool.instance()
            if english:
                ask = ("下面是一个 AI 技能（skill）的说明文档。请用两行输出："
                       "第一行以“中文：”开头给出不超过 60 字的中文一句话简介；"
                       "第二行以“English:”开头给出对应英文一句话简介。只输出这两行。")
            else:
                ask = ("下面是一个 AI 技能（skill）的说明文档。"
                       "请用不超过 60 字的一句话中文简介说明它能做什么，"
                       "直接输出这一句话，不要任何前缀。")
            out = pool.chat_complete(
                [{"role": "system", "content": ask},
                 {"role": "user", "content": body[:3000]}],
                kind="small", max_tokens=160, timeout=20.0)
            text_out = re.sub(r"\s*\n\s*", " | ", (out or "").strip())
            text_out = text_out.strip(" |")
            if text_out:
                return text_out[:_SUMMARY_LIMIT * 2], "llm"
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能摘要小模型失败（回退正文首段）：%s", exc)
        fallback = _first_sentence(body)
        return (fallback or Path("").name or "")[:_SUMMARY_LIMIT], "fallback"

    # ------------------------------------------------------------ 导入
    def import_paths(self, paths: List[Path], allow_files: bool = True,
                     pool: Optional[Any] = None,
                     summarize: bool = True,
                     on_progress: Optional[Callable[[str], None]] = None
                     ) -> Dict[str, Any]:
        """批量导入技能包 → skillspub 文件夹 + catalog.json 索引。

        :param allow_files: 是否允许这些技能在 skilluserdata 下生成文件
        :param pool: 小模型客户端（None 时用全局池；失败自动回退）
        :return: {"imported": [...], "failed": [{path, msg}], "count": n}
        """
        result: Dict[str, Any] = {"imported": [], "failed": [], "count": 0}
        pub.ensure_dir(seed=False)
        for src in paths:
            src = Path(src)
            info = self._prepared.get(str(src)) or self.preview(src)
            if on_progress:
                try:
                    on_progress(f"正在导入 {src.name} …")
                except Exception:  # noqa: BLE001
                    pass
            if not info.get("ok"):
                result["failed"].append({"path": str(src),
                                         "msg": info.get("msg") or "无法解析"})
                continue
            name = str(info["name"])
            description = str(info["description"] or "")
            summary = str(info.get("summary") or "")
            meta = dict(info.get("meta") or {})
            if summarize and not summary:
                summary, source = self.summarize(description, meta, pool=pool)
                info["summary"] = summary
            else:
                source = "frontmatter"
            if not summary:
                summary = (info["name"] or src.stem)[:_SUMMARY_LIMIT]
                source = "name"
            folder = pub.folder_for_name(name)
            # 同名（或文件夹占用）时自动加序号，避免覆盖用户已有技能
            exist_names = {e["name"] for e in pub.list_skills()}
            exist_folders = {e["folder"].casefold() for e in pub.list_skills()}
            n = 2
            while name in exist_names or folder.casefold() in exist_folders:
                name = f"{info['name']}-{n}"
                folder = pub.folder_for_name(name)
                n += 1
            try:
                target = pub.skill_folder_path(folder)
                root = Path(str(info["root"]))
                target.mkdir(parents=True, exist_ok=True)
                for child in root.iterdir():
                    if _is_junk(child.name):
                        continue
                    dst = target / child.name
                    if child.is_dir():
                        shutil.copytree(str(child), str(dst), dirs_exist_ok=True)
                    else:
                        shutil.copy2(str(child), str(dst))
                # 没有 SKILL.md 时（直接导入 .md），把说明写成 SKILL.md
                if not (target / pub.SKILL_FILE).exists():
                    (target / pub.SKILL_FILE).write_text(description,
                                                         encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                result["failed"].append({"path": str(src),
                                         "msg": f"写入技能文件夹失败：{exc}"})
                continue
            ok, msg = pub.save_skill({
                "name": name,
                "folder": folder,
                "category": str(meta.get("category") or "导入"),
                "enabled": True,
                "summary": summary,
                "keywords": _guess_keywords(name, summary, meta),
                "description": description or summary,
                "allow_files": bool(allow_files),
            })
            if not ok:
                result["failed"].append({"path": str(src), "msg": msg})
                continue
            result["imported"].append({
                "path": str(src), "name": name, "folder": folder,
                "summary": summary, "summary_source": source,
                "needs_files": bool(info.get("needs_files")),
                "allow_files": bool(allow_files),
            })
            result["count"] += 1
        self.cleanup()
        return result

    def cleanup(self) -> None:
        """清理本次预览/导入产生的临时目录。"""
        for item in list(self._prepared.values()):
            tmp = item.get("unpacked")
            if tmp:
                shutil.rmtree(str(tmp), ignore_errors=True)
        self._prepared.clear()


if __name__ == "__main__":  # 调试入口：python skill_import.py <包路径> [...]
    import sys
    from utils.utf8 import force_utf8_stdio
    force_utf8_stdio()
    imp = SkillImporter.instance()
    for arg in sys.argv[1:]:
        print(imp.preview(Path(arg)))
    print(imp.import_paths([Path(a) for a in sys.argv[1:]], allow_files=True))
