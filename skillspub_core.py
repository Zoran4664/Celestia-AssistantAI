# -*- coding: utf-8 -*-
"""
skillspub_core.py —— skill库（skillspub 公共技能库）数据层与上下文构造。

存储架构（每一项公共技能 = 一个独立文件夹「项目」，JSON 只做索引）：
    skillspub/
    ├── catalog.json          ← 目录索引（skill库(skillspub) 唯一索引；不存详细介绍正文）
    └── <技能文件夹>/          ← 每个技能一个文件夹（文件夹名 = 技能名净化而来）
        ├── SKILL.md          ← 该技能的详细介绍与执行说明（真正注入 LLM 的正文）
        └── ...               ← 该技能自带的其它文件（用户可自由扩展）

catalog.json 每一项（索引）字段：
    name        技能名（唯一，供 AI 按名称调用）
    folder      技能文件夹名（指向 skillspub/<folder>/，内含 SKILL.md 等技能文件）
    summary     skill简介（写入 skillspub 指引）
    category    分类（可留空）
    enabled     是否启用（false 的技能不会出现在指引中，也不会被调用）
    keywords    调用关键词（用于无 LLM 时的文本匹配降级）
    created_at / updated_at

管理器（skillspub_manager）保存/改名/删除时：既写回 catalog.json 索引，
也同步该技能的文件夹文件（新建/覆盖 SKILL.md、改名移动文件夹、删除移除整个文件夹）。

纯逻辑模块，不依赖 Qt，可被聊天线程与 skillspub 管理器共同调用。
"""
import json
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config_loader import ConfigLoader
except Exception:  # 独立脚本运行等场景
    ConfigLoader = None

# 目录内索引文件 / 每个技能文件夹里的介绍文件
CATALOG_NAME = "catalog.json"
SKILL_FILE = "SKILL.md"

# skill简介（索引 summary 字段）的最大字符数：管理器里「总结功能 / 翻译总结」
# 生成简介与导入技能包生成摘要都按这个长度截断（与 skill_import 保持一致）
SUMMARY_LIMIT = 200

# skill库（skillspub）两个内置“开关指令”对应的触发词与运行模式
#   手动模式 manual ：用户自己（或用户指令要求）自由调用，注入完整介绍
#   自动模式 auto   ：AI 程序读取指引后自动挑选组合调用
MANUAL_TRIGGERS = {"skillspub", "技能广场"}
AUTO_TRIGGERS = {"autoskills", "自动技能"}

# 无目录文件时写入的示例技能（3 个常见技能，用于格式化示范；可在管理器中增删）
SEED_SKILLS: List[Dict[str, Any]] = [
    {
        "name": "信息整理与总结",
        "category": "办公",
        "enabled": True,
        "summary": "把长文本、聊天记录或杂乱资料整理成高信息密度的要点结论与行动清单。",
        "keywords": ["整理", "总结", "归纳", "要点", "汇总", "纪要", "提取", "精简", "提炼"],
        "description": "【技能用途】把长段落、聊天记录、会议材料等压缩为要点式结论，便于用户快速决策。\n"
                       "【执行要求】\n"
                       "1. 先区分事实、观点与行动项，分别归纳，不臆造内容；\n"
                       "2. 采用「一句话结论 + 分条要点 + 行动清单」三段式，每条要点尽量独立成行；\n"
                       "3. 保留关键数字、人名、时间等硬信息，重要信息可原文引用；\n"
                       "4. 最后询问是否需要整理为表格/待办清单等其它格式。",
    },
    {
        "name": "文案润色改写",
        "category": "创作",
        "enabled": True,
        "summary": "把草稿或平淡的文字改成更有文采、更清晰、更贴合目标读者与场景的版本。",
        "keywords": ["润色", "改写", "文案", "修辞", "书面语", "润稿", "优化文字"],
        "description": "【技能用途】适合对通知、朋友圈、简介、汇报、广告语等文字进行润色与改写。\n"
                       "【执行要求】\n"
                       "1. 先复述原文核心信息并询问使用场景与语气偏好（正式/亲切/活泼等）；\n"
                       "2. 在保留原意与关键信息的前提下优化表达，避免堆砌华丽词藻；\n"
                       "3. 给出 2~3 个风格不同的改写版本，并简述各自适用场合；\n"
                       "4. 完成后列出为便于挑选所做的关键改动点。",
    },
    {
        "name": "邮件撰写与回复",
        "category": "办公",
        "enabled": True,
        "summary": "根据要点快速生成结构规范的正式邮件草稿，或对现有邮件进行语气与结构润色。",
        "keywords": ["邮件", "写信", "回复邮件", "邮件事宜", "商务邮件", "邮件润色"],
        "description": "【技能用途】把零散要点组织成结构清晰、语气得体的中文商务邮件，也可回复来信。\n"
                       "【执行要求】\n"
                       "1. 先明确收件对象与邮件目的（通知/请求/回复/致谢等），据此决定语气；\n"
                       "2. 遵循「主题行 - 称呼 - 正文（开门见山 - 分条说明 - 行动请求）- 结尾致谢与署名」结构；\n"
                       "3. 正文用词正式简洁、避免口语，每封邮件只围绕一个核心目的；\n"
                       "4. 若为回复邮件，先针对对方问题逐条回应，再补充自己的请求或结论。",
    },
]

_log_prefix = "[skillspub] "


def log(msg: str, warn: bool = False) -> None:
    import logging
    logging.getLogger("skillspub").warning(msg) if warn else logging.getLogger("skillspub").info(msg)


def skillspub_dir() -> Path:
    """skillspub 数据目录（默认项目根/skillspub）。"""
    if ConfigLoader is not None:
        try:
            return Path(ConfigLoader.instance().skillspub_dir)
        except Exception:
            pass
    here = Path(__file__).resolve().parent
    return here / "skillspub"


def catalog_path() -> Path:
    return skillspub_dir() / CATALOG_NAME


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 文件夹工具
def folder_for_name(name: str) -> str:
    """由技能名生成稳定的文件夹名（Windows 合法字符、去首尾空白与点号）。"""
    raw = unicodedata.normalize("NFKC", str(name or "")).strip()
    raw = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", raw)
    raw = re.sub(r"\s+", "_", raw).strip("._")
    return raw or "skill"


def skill_folder_path(folder: str) -> Path:
    """skillspub/<folder>/ 技能文件夹路径。"""
    return skillspub_dir() / (folder or "")


def description_path(folder: str) -> Path:
    """技能介绍文件：skillspub/<folder>/SKILL.md。"""
    return skill_folder_path(folder) / SKILL_FILE


def _write_skill_file(folder: str, description: str) -> None:
    """创建技能文件夹并写入/覆盖 SKILL.md（技能正文文件）。"""
    folder_dir = skill_folder_path(folder)
    folder_dir.mkdir(parents=True, exist_ok=True)
    path = folder_dir / SKILL_FILE
    body = str(description or "")
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        os.replace(str(tmp), str(path))
    except OSError:
        tmp.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------- 基础读写
def _to_bool(value: Any, default: bool = True) -> bool:
    """把 json/手改文本中的布尔容错转换为 python bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off", "否", "禁用", "关闭")


def _normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
    """规整为统一索引字段结构（容忍手改 JSON 时的缺字段/错类型）。

    返回值不包含 description——详细介绍正文一律存于技能文件夹的 SKILL.md。
    """
    name = str(entry.get("name", "")).strip()
    folder = str(entry.get("folder", "")).strip() or folder_for_name(name)
    summary = str(entry.get("summary", "")).strip()
    keywords = entry.get("keywords")
    if not isinstance(keywords, list):
        try:
            keywords = [str(k).strip() for k in re.split(r"[，,、;；/|]+", str(keywords or "")) if str(k).strip()]
        except Exception:
            keywords = []
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    return {
        "name": name,
        "folder": folder,
        "category": str(entry.get("category", "通用")).strip() or "通用",
        "enabled": _to_bool(entry.get("enabled", True)),
        "summary": summary,
        "keywords": keywords,
        # 需求：导入技能包时用户是否允许该技能在 skilluserdata 下生成文件
        "allow_files": _to_bool(entry.get("allow_files", False), False),
        "created_at": str(entry.get("created_at") or now()),
        "updated_at": str(entry.get("updated_at") or now()),
    }


def _read_raw() -> Dict[str, Any]:
    """读取 catalog.json 原始内容；缺失/损坏返回空目录结构。"""
    path = catalog_path()
    if not path.exists():
        return {"updated": now(), "count": 0, "skills": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"读取 {path.name} 失败：{exc}", warn=True)
        return {"updated": now(), "count": 0, "skills": []}
    if not isinstance(data, dict):
        data = {}
    return data


def _load_description(entry: Dict[str, Any]) -> str:
    """读取技能正文：优先读文件夹 SKILL.md；文件不存在时回退旧版内联 description。"""
    folder = entry.get("folder") or ""
    path = description_path(folder)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        log(f"读取 {path} 失败：{exc}", warn=True)
    return str(entry.get("description") or "").strip()


def _index_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """落盘索引条目：去掉 description 等正文/临时字段。"""
    cleaned = _normalize(entry)
    return cleaned


def _read_catalog() -> Dict[str, Any]:
    """读取索引（仅索引字段，不含正文；正文由 list_skills/read_skill 合并）。"""
    data = _read_raw()
    skills = data.get("skills")
    if not isinstance(skills, list):
        skills = []
    data["skills"] = [_index_entry(e) for e in skills
                      if isinstance(e, dict) and str(e.get("name", "")).strip()]
    data["count"] = len(data["skills"])
    return data


def _write_catalog(data: Dict[str, Any]) -> None:
    """原子写回 catalog.json（先写临时文件再替换），保证不掉数据。"""
    folder = skillspub_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = catalog_path()
    data["updated"] = now()
    skills = [s for s in data.get("skills", [])
              if isinstance(s, dict) and str(s.get("name", "")).strip()]
    data["skills"] = [_index_entry(s) for s in skills]
    data["count"] = len(data["skills"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.replace(str(tmp), str(path))
    except OSError:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_dir(seed: bool = True) -> Path:
    """确保 skillspub 目录、索引与技能文件夹存在。

    - 全新目录：写入 3 条示例公共技能（含各自的技能文件夹与 SKILL.md 文件）；
    - 旧版目录（详细介绍内联在 catalog.json）：自动迁移为文件存储，并补全 folder 字段。
    """
    folder = skillspub_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = catalog_path()
    if not path.exists():
        skills = []
        if seed:
            ts = now()
            for e in SEED_SKILLS:
                d = _index_entry(e)
                d["created_at"] = ts
                d["updated_at"] = ts
                _write_skill_file(d["folder"], str(e.get("description") or ""))
                skills.append(d)
        _write_catalog({"updated": now(), "count": len(skills), "skills": skills})
        return folder

    # ---------- 旧版迁移：详细介绍从 catalog.json 迁入各技能文件夹的 SKILL.md ----------
    data = _read_raw()
    skills = data.get("skills")
    if not isinstance(skills, list):
        return folder
    changed = False
    migrated = []
    for e in skills:
        if not isinstance(e, dict) or not str(e.get("name", "")).strip():
            continue
        entry = _index_entry(e)
        body = str(e.get("description") or "").strip()
        fpath = description_path(entry["folder"])
        if not fpath.exists():
            _write_skill_file(entry["folder"], body)
            changed = True
        elif "description" in e:
            changed = True  # 索引仍残留正文 → 本次清理
        migrated.append(entry)
    if changed:
        _write_catalog({"updated": data.get("updated"), "count": len(migrated),
                        "skills": migrated})
    return folder


# ---------------------------------------------------------------- 查询接口
def list_skills(enabled_only: bool = False) -> List[Dict[str, Any]]:
    """全部公共技能（按名称排序，已合并各技能文件夹里的介绍正文）；
    enabled_only=True 只返回已启用的。"""
    data = _read_catalog()
    skills = sorted(data.get("skills", []), key=lambda e: e["name"])
    if enabled_only:
        skills = [e for e in skills if e.get("enabled", True)]
    merged = []
    for e in skills:
        item = dict(e)
        item["description"] = _load_description(e)
        merged.append(item)
    return merged


def read_skill(name: str) -> Optional[Dict[str, Any]]:
    for e in list_skills():
        if e["name"] == name:
            return dict(e)
    return None


def count_skills() -> int:
    return len(list_skills())


def skill_path(name: str) -> Path:
    """返回某技能的文件夹（SKILL.md 所在目录）。"""
    entry = read_skill(name)
    if entry is None:
        return skill_folder_path(folder_for_name(name))
    return skill_folder_path(entry["folder"])


# ---------------------------------------------------------------- 编辑接口
def validate(data: Dict[str, Any], old_name: Optional[str] = None) -> str:
    """返回错误信息；为空字符串表示通过。"""
    name = str(data.get("name", "")).strip()
    summary = str(data.get("summary", "")).strip()
    if not name:
        return "技能名不能为空"
    if not summary:
        return "「skill简介」不能为空（会写入 skillspub 指引供 AI 选择）"
    if re.search(r"[\\/:*?\"<>|\r\n\t]", name) or name in (".", ".."):
        return "技能名包含非法字符（不能包含 < > : \" / \\ | ? * 等）"
    folder = folder_for_name(name)
    others = _read_catalog().get("skills", [])
    if name in [e["name"] for e in others if e["name"] != (old_name or "")]:
        return f"技能「{name}」已存在，技能名需唯一"
    if any(e["folder"].casefold() == folder.casefold()
           for e in others if e["name"] != (old_name or "")):
        return f"文件夹名「{folder}」已被其它技能占用，请更换技能名"
    return ""


def save_skill(data: Dict[str, Any], old_name: Optional[str] = None) -> tuple:
    """新增或修改一条公共技能：同时更新索引与技能文件夹文件，返回 (ok, msg)。

    新技能 → 创建 skillspub/<folder>/ 并写入 SKILL.md；
    改名   → 移动整个技能文件夹（保留其中其它文件）后再写新 SKILL.md；
    仅编辑 → 覆盖该技能文件夹的 SKILL.md 并刷新索引条目。
    """
    desc = str(data.get("description") or "").strip()
    cleaned = _index_entry(data)
    err = validate(cleaned, old_name=old_name)
    if err:
        return False, err
    catalog = _read_catalog()
    skills = catalog.get("skills", [])

    old = next((e for e in skills if e["name"] == (old_name or cleaned["name"])), None)
    if old is not None:
        cleaned["created_at"] = old.get("created_at") or cleaned["created_at"]

    # 处理技能文件夹：改名时整体移动
    old_folder = (old or {}).get("folder") or (folder_for_name(old_name) if old_name else None)
    new_folder = cleaned["folder"]
    if old_folder and old_folder != new_folder:
        old_dir = skill_folder_path(old_folder)
        new_dir = skill_folder_path(new_folder)
        if old_dir.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            for child in old_dir.iterdir():
                if child.is_dir():
                    shutil.copytree(str(child), str(new_dir / child.name), dirs_exist_ok=True)
                else:
                    shutil.copy2(str(child), str(new_dir / child.name))
            shutil.rmtree(str(old_dir), ignore_errors=True)
    _write_skill_file(new_folder, desc)

    kept = [e for e in skills if e["name"] != (old_name or cleaned["name"])]
    kept.append(cleaned)
    catalog["skills"] = kept
    _write_catalog(catalog)
    return True, f"技能「{cleaned['name']}」已保存（索引 + {new_folder}\\{SKILL_FILE}）"


def delete_skill(name: str) -> tuple:
    """删除公共技能：从索引移除，并删除该技能文件夹（含 SKILL.md 等技能文件）。"""
    catalog = _read_catalog()
    before = len(catalog.get("skills", []))
    target = next((e for e in catalog.get("skills", []) if e["name"] == name), None)
    catalog["skills"] = [e for e in catalog.get("skills", []) if e["name"] != name]
    if len(catalog["skills"]) == before:
        return False, f"未找到技能「{name}」"
    _write_catalog(catalog)
    if target is not None:
        folder_dir = skill_folder_path(target["folder"])
        if folder_dir.exists():
            shutil.rmtree(str(folder_dir), ignore_errors=True)
    return True, f"技能「{name}」及其文件夹已删除"


def workdir_lines(workdir: str) -> List[str]:
    """技能产出目录说明（供技能执行时把生成的文件写到指定文件夹）。

    需求：用户在附件栏选择了「工作文件夹」→ 用该文件夹；未选择 → 用本对话在
    skilluserdata 下按「日期+时间」建立的项目文件夹。
    """
    wd = str(workdir or "").strip()
    if not wd:
        return []
    return [
        "",
        "【技能产出目录】",
        f"本轮技能需要创建/保存/导出文件时，一律写到该文件夹：{wd}",
        "（技能说明里出现相对路径时按该目录解析；用户在本条消息里另行指定路径时以其为准。）",
    ]


# ---------------------------------------------------------------- 指引与详情文本
def guide_text(entries: Optional[List[Dict[str, Any]]] = None) -> str:
    """skill库(skillspub) 指引：技能名 + skill简介 的总目录（供 AI/用户浏览与挑选）。"""
    if entries is None:
        entries = list_skills(enabled_only=True)
    if not entries:
        return ""
    lines = [
        "【skill库（skillspub 公共技能库）· 目录指引】",
        "以下公共技能均已开放：一次可同时调用一个或多个技能并相互组合；",
        "需要时请直接按技能名调用（用户也可在输入框用「#技能名」直接点名调用），"
        "并严格遵守对应技能的「详细介绍与执行说明」。",
    ]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. {e['name']} —— {e['summary']}")
    return "\n".join(lines)


def detail_text(entry: Dict[str, Any]) -> str:
    """单条公共技能的详细介绍（注入 LLM 的正文，来自其文件夹的 SKILL.md）。"""
    name = entry.get("name", "未命名")
    category = entry.get("category") or "通用"
    summary = entry.get("summary", "")
    desc = entry.get("description", "")
    head = f"■ 技能「{name}」（分类：{category}）"
    parts = [head]
    folder = str(entry.get("folder") or "").strip()
    if folder:
        # 技能文件夹路径：技能自带的 references/ scripts/ assets/ 都在这里
        parts.append("技能文件夹（含 references/ scripts/ assets/ 等附属文件）："
                     f"{skill_folder_path(folder)}")
    if summary:
        parts.append(f"一句话简介：{summary}")
    if desc:
        parts.append("详细介绍与执行说明：")
        parts.append(desc)
    return "\n".join(parts)


# ---------------------------------------------------------------- 选择/匹配
def mode_from_tags(tags) -> str:
    """根据已解析的技能标签判断是否开启了 skill库（skillspub）及其运行模式。

    返回 'manual'（\\@skillspub 手动）/ 'auto'（\\@autoskills 自动）/ ''（未开启）。
    """
    for t in tags or []:
        if isinstance(t, dict):
            trigger = str(t.get("trigger") or t.get("name") or "").strip().lower()
        else:
            trigger = str(t).strip().lower()
        if trigger in AUTO_TRIGGERS:
            return "auto"
    for t in tags or []:
        if isinstance(t, dict):
            trigger = str(t.get("trigger") or t.get("name") or "").strip().lower()
        else:
            trigger = str(t).strip().lower()
        if trigger in MANUAL_TRIGGERS:
            return "manual"
    return ""


def match_score(entry: Dict[str, Any], text: str) -> int:
    """文本匹配分：技能名命中权重高，关键词逐个累加。"""
    if not text:
        return 0
    score = 0
    if entry.get("name") and entry["name"] in text:
        score += 10
    for kw in entry.get("keywords", []):
        if kw and kw in text:
            score += 3
    return score


def fallback_select(prompt: str, entries: Optional[List[Dict[str, Any]]] = None,
                    top_n: int = 4) -> List[Dict[str, Any]]:
    """无小模型可用时的降级：按文本关键词命中挑选。"""
    if entries is None:
        entries = list_skills(enabled_only=True)
    scored = [(match_score(e, prompt or ""), e) for e in entries]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return [e for _, e in scored[:top_n]]


# ---------------------------------------------------------------- #技能名 解析
# 与 @技能 标签字符集保持一致：数字/字母/下划线/连字符/中文（不含空格，空格视为结束）
_PUB_TOKEN_RE = re.compile(r"[0-9A-Za-z_\u4e00-\u9fa5-]+")
#: 模糊匹配（名字 / 关键词**前缀**）的最短长度，按「CJK 记 2、其它记 1」加权。
#: 用户反馈「`/#tothestars` 失效」的根因之一：`resolve_longest_token` 会把
#: `tothestars` 逐级截断（tothestars→…→`to`），而 1~2 个拉丁字符极易撞上**无关技能
#: 的关键词前缀**，于是 `#tothestars` 被静默解析成 `treatment-plans`（医疗方案），
#: 模型收到的是完全不相干的技能说明。前缀匹配必须够长才允许。
_MIN_FUZZY_WEIGHT = 3


def token_weight(token: str) -> int:
    """候选片段的「长度权重」：CJK 字符记 2，其余记 1。"""
    return sum(2 if ord(c) > 127 else 1 for c in (token or ""))


def resolve_skill_token(token: str,
                        entries: Optional[List[Dict[str, Any]]] = None,
                        allow_keyword: bool = True) -> Optional[Dict[str, Any]]:
    """把「#技能名」后的候选片段解析为目录中的一条已启用技能。

    匹配规则（由强到弱）：
    1) 候选 == 某技能名（大小写不敏感）；
    2) 候选 == 某技能的某个关键词（``allow_keyword=False`` 时跳过）；
    3) 候选是**唯一**某个技能名的前缀（例如 #信息整理 → 信息整理与总结）；
    4) 候选是**唯一**某个技能某关键词的前缀（``allow_keyword=False`` 时跳过）。
    只解析唯一确定的目标，绝不猜测；普通“#话题/英文标签”不会被误伤
    （例如 “#总结一下” 不会命中只含前缀关系的技能名）。

    ``allow_keyword`` 由 :func:`resolve_longest_token` 在**部分匹配**（用户实际输入的
    片段比候选长）时置 False —— 关键词是给「自动挑技能」用的相关性字段，用它去截断
    一个更长的用户输入会撞出莫名其妙的技能（`#star…` → statistical-analysis）。
    规则 3/4 还要求候选权重 ≥ ``_MIN_FUZZY_WEIGHT``（CJK 记 2、其它记 1）。
    """
    token = (token or "").strip()
    if not token:
        return None
    if entries is None:
        entries = list_skills(enabled_only=True)
    low = token.casefold()
    for e in entries:
        if str(e.get("name", "")).casefold() == low:
            return e
    if allow_keyword:
        for e in entries:
            for kw in e.get("keywords", []):
                if str(kw or "").strip().casefold() == low:
                    return e
    if token_weight(token) < _MIN_FUZZY_WEIGHT:
        return None
    # 唯一“名字前缀”命中
    heads = [e for e in entries
             if str(e.get("name", "")).casefold().startswith(low)]
    if len(heads) == 1:
        return heads[0]
    if not allow_keyword:
        return None
    # 唯一“关键词前缀”命中（同一技能多关键词去重）
    khits: List[Dict[str, Any]] = []
    seen = set()
    for e in entries:
        if e["name"] in seen:
            continue
        for kw in e.get("keywords", []):
            if str(kw or "").strip().casefold().startswith(low):
                khits.append(e)
                seen.add(e["name"])
                break
    if len(khits) == 1:
        return khits[0]
    return None


def resolve_longest_token(text: str, start: int,
                          entries: Optional[List[Dict[str, Any]]] = None):
    """从 text[start]（应为 '#'）之后读取一段候选，按最长优先解析。

    返回 (end_index, entry)；解析不到时返回 (start + 1, None)。

    三条硬规矩（用户反馈「`/#tothestars` 失效」的根因）：
      * **部分匹配**只在「多出来的尾巴不是同一段标识符的延续」时才接受 ——
        否则 `#star` 会命中 `#sta`（statistical-…）并把多出来的 `r` 当正文留下
        （拉丁字母/数字/下划线紧贴 = 同一段标识符还在继续）；
      * 部分匹配时，若「用户写的 token 已经把整个技能名包进去、后面又多粘了字」
        （`#信息整理与总结x`）→ 说明他要的不是这个前缀，**不接受**；
      * 部分匹配**只认技能名**（见 :func:`resolve_skill_token` 的 ``allow_keyword``）。
    """
    if text[start] != "#":
        return start + 1, None
    if entries is None:
        entries = list_skills(enabled_only=True)
    if not entries:
        return start + 1, None
    m = _PUB_TOKEN_RE.match(text, start + 1)
    if not m:
        return start + 1, None
    token = m.group(0)
    for cut in range(len(token), 0, -1):
        piece = token[:cut]
        partial = cut < len(token)
        if partial and re.match(r"[0-9A-Za-z_]", token[cut]):
            continue
        entry = resolve_skill_token(piece, entries, allow_keyword=not partial)
        if entry is None:
            continue
        if partial:
            low_name = str(entry.get("name", "")).casefold()
            low_piece = piece.casefold()
            tail_name = low_name[len(low_piece):] if low_name.startswith(
                low_piece) else ""
            rest = token[cut:].casefold()
            if tail_name and rest[:len(tail_name)] == tail_name:
                # token 里已经把整个技能名写完了（后面还粘着别的字）→ 不认这个前缀
                continue
        return m.start() + cut, entry
    return m.end(), None


def pub_refs_in_text(text: str,
                     entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """扫描文本，返回所有被识别为「#技能名」引用的片段。

    每一项：{"start","end","token","entry"}，按出现顺序排列；
    只识别能唯一解析到目录中已启用技能的片段，普通「#话题」不会被误伤。
    """
    if not text or "#" not in text:
        return []
    if entries is None:
        entries = list_skills(enabled_only=True)
    if not entries:
        return []
    refs: List[Dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("#", i)
        if j < 0:
            break
        end, entry = resolve_longest_token(text, j, entries)
        if entry is not None:
            # 用户习惯把指令写成 `/#技能名`（反馈里就是这种写法）：把紧邻的独立
            # `/` 一并算进片段，剥离后正文里不会留下一个孤零零的斜杠
            start = j
            if (start >= 1 and text[start - 1] == "/"
                    and (start == 1 or text[start - 2] in " \t\r\n\u2029\u2028")):
                start -= 1
            refs.append({
                "start": start,
                "end": end,
                "token": text[j + 1:end],
                "entry": entry,
            })
        i = max(end, j + 1)
    return refs


def parse_pub_tags(text: str,
                   entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """解析文本中的「#技能名」，返回命中的技能条目（按出现顺序去重）。"""
    if entries is None:
        entries = list_skills(enabled_only=True)
    refs = pub_refs_in_text(text, entries)
    names: List[str] = []
    for r in refs:
        nm = r["entry"]["name"]
        if nm not in names:
            names.append(nm)
    by_name = {e["name"]: e for e in entries}
    return [by_name[nm] for nm in names if nm in by_name]


def strip_pub_tags(text: str,
                   entries: Optional[List[Dict[str, Any]]] = None) -> str:
    """从文本中移除所有被识别为「#技能名」的片段（供展示与送入 LLM 前清理）。"""
    if not text:
        return text
    if entries is None:
        entries = list_skills(enabled_only=True)
    refs = pub_refs_in_text(text, entries)
    if not refs:
        return text
    out: List[str] = []
    pos = 0
    for r in refs:
        out.append(text[pos:r["start"]])
        pos = r["end"]
    out.append(text[pos:])
    # 清理被剥离位置残留的重复空格
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def match_prefix(query: str,
                 entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """输入框按前缀/包含过滤技能库技能（供 # 弹出菜单展示）。"""
    if entries is None:
        entries = list_skills(enabled_only=True)
    q = (query or "").strip().casefold()
    if not q:
        return entries
    heads: List[Dict[str, Any]] = []
    contains: List[Dict[str, Any]] = []
    kw: List[Dict[str, Any]] = []
    seen = set()
    for e in entries:
        nm = str(e.get("name", "")).casefold()
        if nm.startswith(q):
            heads.append(e)
            seen.add(e["name"])
        elif q in nm:
            contains.append(e)
            seen.add(e["name"])
    for e in entries:
        if e["name"] in seen:
            continue
        if any(q in str(k or "").casefold() for k in e.get("keywords", [])):
            kw.append(e)
    return heads + contains + kw


def _split_names(raw: str) -> List[str]:
    for ch in "，、,;；/|\n":
        raw = raw.replace(ch, "|")
    return [s.strip() for s in raw.split("|") if s.strip()]


def _parse_selected(raw: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从模型回包里解析被选中的技能名（容忍顿号/逗号/近似名）。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if re.search(r"^(无|不适用|none|no|没有|不需要)", raw, re.I):
        return []
    names = set()
    # 第一遍：完整技能名出现在回复中
    for e in entries:
        if e["name"] in raw:
            names.add(e["name"])
    # 第二遍：按分隔符切出的片段回映技能名
    for token in _split_names(raw):
        if not token or token in ("无", "不适用") or re.fullmatch(r"(none|no)", token, re.I):
            continue
        for e in entries:
            if token == e["name"] or token in e["name"] or e["name"] in token:
                names.add(e["name"])
                break
    order = {e["name"]: i for i, e in enumerate(entries)}
    picked = [e for e in entries if e["name"] in names]
    picked.sort(key=lambda e: order[e["name"]])
    return picked


def select_skills_via_llm(pool, prompt: str,
                          entries: Optional[List[Dict[str, Any]]] = None,
                          top_n: int = 6) -> List[Dict[str, Any]]:
    """让 AI 程序自己读取 skillspub 指引并挑选适用于本条消息的技能（可多个）。

    使用 small 小模型做一次性判定，失败/异常时返回空列表，由调用方降级。
    """
    if entries is None:
        entries = list_skills(enabled_only=True)
    if not entries or pool is None:
        return []
    listing = "\n".join(f"- {e['name']}：{e['summary']}" for e in entries)
    sys_msg = (
        "你是一个「skill库（skillspub 公共技能库）」技能调度器。"
        "以下是当前可用的公共技能目录：\n"
        f"{listing}\n\n"
        "阅读用户的最新消息，判断其中哪些技能确实适用于完成该请求：\n"
        "- 允许一次选择多个技能，用于组合完成较复杂的任务；\n"
        "- 只有技能确实有助于完成任务时才选择；如果都不适用，一个也不要选；\n"
        "- 只输出一行，用顿号（、）分隔选中的技能完整名称；都不适用时只输出：无"
    )
    try:
        raw = pool.chat_complete(
            [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt or "（空消息）"},
            ],
            kind="small",
            temperature=0.1,
            max_tokens=256,
            timeout=25,
        )
    except Exception as exc:
        log(f"自动挑选技能失败：{exc}", warn=True)
        return []
    return _parse_selected(raw, entries)[:top_n]


# ---------------------------------------------------------------- 上下文注入
def context_for_prompt(prompt: str, mode: str = "manual", pool=None,
                       workdir: str = "") -> str:
    """构造本轮要注入的 skillspub 系统上下文。

    mode='manual'：\\@skillspub 手动模式——把指引与全部技能详细介绍注入，
                   用户 / AI 可自由调用任意技能（一次多个）。
    mode='auto'  ：\\@autoskills 自动模式——AI 程序读取指引后自动挑选
                   最相关的若干技能注入执行；无小模型时降级为关键词匹配。
    workdir      ：技能产出目录（附件栏选择的工作文件夹 / 本对话项目文件夹）
    """
    mode = "auto" if mode == "auto" else "manual"
    entries = list_skills(enabled_only=True)
    guide = guide_text(entries)
    if not entries:
        return ("【skill库（skillspub）】已开启，但 skill库 目录当前为空。"
                "可在「设置 → skill库管理器」中添加公共技能后再使用。")

    if mode == "manual":
        lines = [
            "【skill库（skillspub）· 手动模式】用户已开启「\\@skillspub」开关：",
            "本轮对话允许直接调用 skill库 中的公共技能，一次可调用一个或多个并互相组合；",
            "请根据用户请求自由选用下方列出的技能，并严格按其「详细介绍与执行说明」执行。",
            "",
            guide,
        ]
        for e in entries:
            lines.append("")
            lines.append(detail_text(e))
        lines.extend(workdir_lines(workdir))
        return "\n".join(lines)

    # ---------- auto：自动挑选 ----------
    selected: List[Dict[str, Any]] = []
    if pool is not None:
        selected = select_skills_via_llm(pool, prompt, entries)
    if not selected:
        selected = fallback_select(prompt, entries, top_n=4)
    lines = [
        "【skill库（skillspub）· 自动模式】用户已开启「\\@autoskills」开关：",
        "你应主动判断本条消息最适合调用 skill库 中的哪些公共技能并组合执行",
        "（一次可同时调用多个）；即使消息没有点名某个技能，只要合适也应按需自动调用。",
        "",
        guide,
    ]
    if selected:
        lines.append("")
        lines.append("本轮自动选用并执行以下技能（可组合）：")
        for e in selected:
            lines.append("")
            lines.append(detail_text(e))
    else:
        lines.append("")
        lines.append("（经判断，本条消息与 skill库 现有技能无显著关联，本轮未自动调用；"
                     "若确需使用，可参考上方目录按技能名调用。）")
    lines.extend(workdir_lines(workdir))
    return "\n".join(lines)


def context_for_direct(names: List[str], entries: Optional[List[Dict[str, Any]]] = None,
                       workdir: str = "") -> str:
    """#技能名 直接调用：用户点名调用一个/多个技能时注入的上下文。

    与 manual 模式的区别：manual 注入全部技能介绍、AI 可自由选用；
    本函数只注入被「#技能名」点名到的技能详细介绍（其余技能本轮明确不执行），
    从而让 prompt 更短、调用更可控。
    """
    if entries is None:
        entries = list_skills(enabled_only=True)
    if not entries or not names:
        return ""
    chosen: List[Dict[str, Any]] = []
    seen = set()
    for token in names:
        entry = resolve_skill_token(token, entries)
        if entry is None or entry["name"] in seen:
            continue
        chosen.append(entry)
        seen.add(entry["name"])
    if not chosen:
        return ""
    label = "、".join(e["name"] for e in chosen)
    lines = [
        "【skill库（skillspub）· #技能名 直接调用】",
        "用户在本条消息中通过「#技能名」指令直接点名调用了以下 skill库 技能"
        "（一次可点名一个或多个，允许相互组合）：",
        f"本论点名技能：{label}",
        "",
        "要求：",
        "1. 只执行以上被点名调用的技能，未被点名的技能一律不要执行；",
        "2. 严格遵守每个被点名技能自己的「详细介绍与执行说明」，组合使用时注意先后顺序；",
        "3. 技能调用不影响你继续用中文自然、完整地回答用户请求。",
    ]
    for e in chosen:
        lines.append("")
        lines.append(detail_text(e))
    lines.extend(workdir_lines(workdir))
    return "\n".join(lines)


# ---------------------------------------------------------------- 调试入口
if __name__ == "__main__":
    ensure_dir()
    print("skillspub 目录：", skillspub_dir())
    print("公共技能数量：", count_skills())
    for s in list_skills():
        print("-", s["name"], "|", s["summary"][:30], "...",
              "| 文件:", s["folder"], "\\", SKILL_FILE)
