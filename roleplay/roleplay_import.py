"""
roleplay_import.py — SillyTavern 预设 / 世界书导入器

支持识别并导入两类文件（按内容自动判别，不依赖文件名）：

1. 酒馆预设（SillyTavern Preset / "break" 系列预设文件）
   - 顶层含 prompts（列表）+ prompt_order + 可选 extensions.regex_scripts
   - prompts  → 规则卡（rules，mode=text，按 prompt_order 的启停与顺序）
   - regex_scripts → 正则脚本（regex）
   - 顶层采样字段 → 采样参数（可选导入）

2. 酒馆世界书（SillyTavern World Info / character_book 导出）
   - 顶层含 entries（dict 或 list）
   - entries → 世界书条目（lorebook）

所有导入条目都会打上 ``bundle``（来源文件名）标记，方便在预设管理器里
按来源分组筛选；重复导入同一文件时按 id 去重更新。

命令行用法（可直接批量导入目录）：
    python -m roleplay.roleplay_import "<预设目录>"
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AI_DeskMate.RolePlayImport")

# ---------------------------------------------------------------------------
# ST 标记位 prompt（结构性占位，没有正文，不导入为规则卡）
# ---------------------------------------------------------------------------
_MARKERS = {
    "main", "nsfw", "jailbreak", "chatHistory", "dialogueExamples",
    "worldInfoBefore", "worldInfoAfter", "personaDescription",
    "charDescription", "charPersonality", "scenario", "creatorNotes",
    "newChatPrompt", "newGroupChatPrompt", "newExampleChatPrompt",
    "continueNudgePrompt", "impersonationPrompt", "sysPrompt",
}

# 某些 SillyTavern 脚本（SPreset 等）会把「编辑器配置」整段 JSON 塞进
# prompt 里（如 ChatSquash / MacroNest / PromptFormat）。这类内容对模型毫无
# 意义，却常常占掉预设体积的 90%，导入时默认剔除。
_TOOL_DUMP_NAME = ("spreset", "配置", "settings", "scriptconfig")
_TOOL_DUMP_KEYS = {"ChatSquash", "MacroNest", "PromptFormat",
                   "InstructFlagger", "SquashCoT"}


def _is_tool_dump(name: str, content: str) -> bool:
    """判定 prompt 是否为「工具配置 dump」（应剔除）。"""
    low = str(name or "").strip().lower()
    if low.startswith("spreset") or low.startswith("spresetsettings"):
        return True
    if "spreset" in low:
        return True
    s = (content or "").strip()
    if not s.startswith(("{", "[")):
        return False
    try:
        obj = json.loads(s)
    except Exception:  # noqa: BLE001
        return False
    if isinstance(obj, dict):
        return bool(set(obj.keys()) & _TOOL_DUMP_KEYS)
    return False


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------
def detect_kind(data: Any) -> Optional[str]:
    """判断 JSON 结构类型：'preset' / 'lorebook' / None。"""
    if not isinstance(data, dict):
        return None
    if "entries" in data and (
            isinstance(data["entries"], (dict, list))):
        return "lorebook"
    if isinstance(data.get("prompts"), list):
        return "preset"
    book = (data.get("data") or {}).get("character_book") \
        if isinstance(data.get("data"), dict) else None
    if isinstance(book, dict) and "entries" in book:
        return "lorebook"
    return None


def scan_directory(path: str | Path) -> List[Dict[str, Any]]:
    """扫描目录，返回可导入文件清单 [{path, name, kind}]。"""
    root = Path(path)
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("跳过无法解析的文件 %s: %s", p.name, exc)
            continue
        kind = detect_kind(data)
        if kind:
            out.append({"path": p, "name": p.stem, "kind": kind})
    return out


# ---------------------------------------------------------------------------
# 酒馆预设 → 规则卡 + 正则 + 采样
# ---------------------------------------------------------------------------
def _prompt_enable_map(data: Dict[str, Any]) -> Dict[str, bool]:
    """从 prompt_order 取 identifier → enabled（取条数最多的一组）。"""
    best: List[Dict[str, Any]] = []
    for group in (data.get("prompt_order") or []):
        if not isinstance(group, dict):
            continue
        order = group.get("order") or []
        if len(order) > len(best):
            best = order
    return {str(o.get("identifier")): bool(o.get("enabled"))
            for o in best if isinstance(o, dict)}


def _num(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:  # noqa: BLE001
        return default


def parse_preset(data: Dict[str, Any], bundle: str,
                 skip_tool_dump: bool = True) -> Dict[str, Any]:
    """解析酒馆预设，返回 {rules, regex, sampling, skipped}。

    skip_tool_dump=True（默认）时剔除 SPreset 等脚本留下的编辑器配置 dump。
    """
    enable = _prompt_enable_map(data)
    rules: List[Dict[str, Any]] = []
    skipped: List[str] = []
    seen: set = set()
    for idx, p in enumerate(data.get("prompts") or []):
        if not isinstance(p, dict):
            continue
        ident = str(p.get("identifier") or "")
        content = str(p.get("content") or "")
        name = str(p.get("name") or "").strip()
        if ident in _MARKERS or not content.strip():
            continue
        if skip_tool_dump and _is_tool_dump(name, content):
            skipped.append(name or ident)
            continue
        if ident in seen:          # 同一 identifier 重复出现只取第一条
            continue
        seen.add(ident)
        rules.append({
            "id": _new_id("rule"),
            "name": name or f"规则 {idx + 1}",
            "enabled": enable.get(ident, True),
            "mode": "text",
            "variable": "",
            "content": content,
            "order": int(_num(p.get("injection_order"), idx * 10)),
            "bundle": bundle,
        })
    rules.sort(key=lambda r: r["order"])

    regex: List[Dict[str, Any]] = []
    ext = data.get("extensions") or {}
    for idx, s in enumerate(ext.get("regex_scripts") or []):
        if not isinstance(s, dict):
            continue
        pl = s.get("placement") or [2]
        if isinstance(pl, int):
            pl = [pl]
        regex.append({
            "id": _new_id("re"),
            "name": str(s.get("scriptName") or "").strip() or f"正则 {idx + 1}",
            "enabled": not bool(s.get("disabled")),
            "find": str(s.get("findRegex") or ""),
            "replace": str(s.get("replaceString") or ""),
            "placement": [int(x) for x in pl if int(x) in (1, 2)] or [2],
            "markdown_only": bool(s.get("markdownOnly")),
            "prompt_only": bool(s.get("promptOnly")),
            "min_depth": int(_num(s.get("minDepth"), 0)),
            "max_depth": int(_num(s.get("maxDepth"), 0)),
            "bundle": bundle,
        })

    sampling: Dict[str, Any] = {}
    field_map = {
        "temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
        "top_a": "top_a", "min_p": "min_p",
        "repetition_penalty": "repetition_penalty",
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
        "openai_max_tokens": "max_tokens",
        "openai_max_context": "max_context",
    }
    for src, dst in field_map.items():
        if data.get(src) is not None:
            try:
                sampling[dst] = float(data[src])
                if float(data[src]).is_integer() and dst.startswith("max_"):
                    sampling[dst] = int(data[src])
            except Exception:  # noqa: BLE001
                continue
    if data.get("seed") is not None:
        try:
            sampling["seed"] = int(data["seed"])
        except Exception:  # noqa: BLE001
            pass
    eff = str(data.get("reasoning_effort") or "").strip().lower()
    if eff and eff != "auto":
        sampling["reasoning_effort"] = eff

    return {"rules": rules, "regex": regex, "sampling": sampling,
            "skipped": skipped}


# ---------------------------------------------------------------------------
# 酒馆世界书 → 世界书条目
# ---------------------------------------------------------------------------
def _lore_entries(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        return [v for v in raw.values() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []


def parse_lorebook(data: Dict[str, Any], bundle: str) -> List[Dict[str, Any]]:
    """解析酒馆世界书，返回 lorebook 条目列表。"""
    src = _lore_entries(data.get("entries"))
    if not src and isinstance(data.get("data"), dict):
        src = _lore_entries(data["data"].get("character_book", {})
                            .get("entries"))
    out: List[Dict[str, Any]] = []
    for idx, e in enumerate(src):
        keys = [str(k) for k in (e.get("key") or e.get("keys") or [])
                if str(k).strip()]
        name = str(e.get("comment") or e.get("name") or "").strip()
        if not name:
            name = keys[0] if keys else f"条目 {idx + 1}"
        content = str(e.get("content") or "")
        if not content.strip():
            continue
        pos = int(_num(e.get("position"), 1))
        # ST: 0=角色定义前 1=角色定义后 4=@Depth；其余归到角色定义后
        pos = 0 if pos == 0 else (2 if pos == 4 else 1)
        prob = int(_num(e.get("probability"), 100))
        if e.get("useProbability") is False:
            prob = 100
        out.append({
            "id": _new_id("lore"),
            "name": name[:60],
            "comment": name[:60],
            "enabled": not bool(e.get("disable")),
            "keys": keys,
            "secondary_keys": [str(k) for k in (e.get("keysecondary") or [])
                               if str(k).strip()],
            "content": content,
            "constant": bool(e.get("constant")),
            "selective": True,
            "order": int(_num(e.get("order"), 100)),
            "position": pos,
            "depth": int(_num(e.get("depth"), 4)),
            "probability": max(1, min(100, prob)),
            "bundle": bundle,
        })
    return out


# ---------------------------------------------------------------------------
# 分类导入为「设定卡」：对话风格 / 世界观 / 角色工具
#
#   世界书（entries）        → 世界观 world
#   预设（prompts + 正则）   → 角色工具 roletools
#   文件名含风格类关键词     → 对话风格 style
# ---------------------------------------------------------------------------
CARD_CATEGORY_STYLE = "style"
CARD_CATEGORY_WORLD = "world"
CARD_CATEGORY_TOOLS = "roletools"

_STYLE_HINTS = ("style", "风格", "文风", "口吻", "语气", "笔调", "tone")
_WORLD_HINTS = ("world", "lore", "世界观", "地理", "设定书", "种族", "地图")


def classify_card(name: str, kind: str) -> str:
    """按内容自动判定归到哪个设定卡类别。

    规则：世界书一律归世界观；文件名含风格关键词归对话风格；
    其余酒馆预设（破限/规则/正则/采样等配置）一律归角色工具。
    """
    if kind == "lorebook":
        return CARD_CATEGORY_WORLD
    low = str(name or "").lower()
    if any(h in low for h in _STYLE_HINTS):
        return CARD_CATEGORY_STYLE
    if any(h in low for h in _WORLD_HINTS):
        return CARD_CATEGORY_WORLD
    return CARD_CATEGORY_TOOLS


def build_card(data: Dict[str, Any], bundle: str, kind: str,
               only_enabled: bool = True) -> Dict[str, Any]:
    """把源文件整体转成一张设定卡 dict（保留原始结构与启停状态）。"""
    if kind == "lorebook":
        entries = parse_lorebook(data, bundle)
        return {
            "project_name": bundle,
            "version": "1.0",
            "category": "World_Setting_Card",
            "description": (
                f"由 SillyTavern 世界书「{bundle}」导入，"
                f"共 {len(entries)} 个条目（保留触发词/常驻/概率/排序）。"),
            "source_file": f"{bundle}.json",
            "usage": "在「设定卡 → 世界观」勾选后整卡注入；条目过多时建议拆分。",
            "entries": entries,
        }

    parsed = parse_preset(data, bundle)
    rules = parsed["rules"]
    if only_enabled:
        kept = [r for r in rules if r.get("enabled")]
        if kept:                       # 一条都没启用时退回全部，避免空卡
            rules = kept
    chars = sum(len(str(r.get("content") or "")) for r in rules)
    desc = (f"由 SillyTavern 预设「{bundle}」导入："
            f"提示词 {len(rules)} 条／正则 {len(parsed['regex'])} 条"
            f"／正文约 {chars} 字"
            f"（{'仅源文件启用的项' if only_enabled else '全部条目'}）。")
    if parsed.get("skipped"):
        desc += f" 已剔除 {len(parsed['skipped'])} 段工具配置 dump。"
    return {
        "project_name": bundle,
        "version": "1.0",
        "category": "Role_Tool_Card",
        "description": desc,
        "source_file": f"{bundle}.json",
        "usage": (
            "在「设定卡 → 角色工具」勾选后整卡注入；"
            "同一时间建议只勾选一张预设卡，避免规则互相打架。"),
        "sampling": parsed["sampling"],
        "prompts": [
            {"name": r["name"], "enabled": bool(r.get("enabled")),
             "order": r.get("order", 0), "content": r.get("content", "")}
            for r in rules
        ],
        "regex_scripts": parsed["regex"],
    }


def import_as_cards(path: str | Path, preset: Optional[Any] = None,
                    category: Optional[str] = None,
                    only_enabled: bool = True,
                    clear_bundle: bool = True,
                    also_lorebook: bool = True) -> Dict[str, Any]:
    """把目录下的酒馆文件按分类导入为设定卡文件。

    写入 roleplay 对应目录（style → rolemanger/style，world →
    rolemanger/world，roletools → roletools）。同名文件覆盖。

    clear_bundle=True 时，会顺带清除预设中来自同名的旧「规则卡/正则/世界书」
    条目，避免同一份内容既以设定卡又以规则卡形式重复注入。
    also_lorebook=True 时，世界书除写入「世界观设定卡」外，还会额外写一份
    「按触发词命中」的世界书条目（默认停用，避免重复注入）。
    """
    from roleplay.roleplaytool import RolePlayManager  # 局部导入，避免循环依赖

    mgr = RolePlayManager()
    files = scan_directory(path)
    written: List[Dict[str, Any]] = []
    errors: List[str] = []
    for f in files:
        try:
            data = json.loads(Path(f["path"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f['name']}: {exc}")
            continue
        cat = category or classify_card(f["name"], f["kind"])
        card = build_card(data, f["name"], f["kind"],
                          only_enabled=only_enabled)
        try:
            dest = mgr.save_card(cat, f["name"], card)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f['name']}: {exc}")
            continue
        written.append({
            "name": f["name"], "category": cat, "kind": f["kind"],
            "path": dest,
            "prompts": len(card.get("prompts") or card.get("entries") or []),
            "regex": len(card.get("regex_scripts") or []),
        })
        if preset is not None and clear_bundle:
            for key in ("rules", "regex", "lorebook"):
                old = preset.get(key, default=[]) or []
                kept = [x for x in old
                        if str(x.get("bundle") or "") != f["name"]]
                if len(kept) != len(old):
                    preset.set(kept, key)
        # 世界书额外写一份触发式条目（默认停用），放在清理之后
        if (preset is not None and also_lorebook and f["kind"] == "lorebook"):
            entries = parse_lorebook(data, f["name"])
            for e in entries:
                e["enabled"] = False
            old = preset.get("lorebook", default=[]) or []
            preset.set(old + entries, "lorebook")
    if preset is not None:
        preset.save()
    return {"files": files, "cards": written, "errors": errors}


# ---------------------------------------------------------------------------
# 导入到预设（按 bundle 去重：同一来源重复导入时先移除旧条目）
# ---------------------------------------------------------------------------
def import_result(preset: Any, results: List[Dict[str, Any]],
                  import_sampling: bool = False,
                  enable_imported: bool = False) -> Dict[str, int]:
    """把解析结果合并进 RolePlayPreset，返回各类导入数量。

    同一 bundle 重复导入时，先删除该 bundle 的旧条目再写入（幂等）。
    enable_imported=False（默认）时，导入的规则卡与正则一律为停用状态，
    避免多个预设的规则混在一起同时生效；世界书条目保持源文件的启停
    （关键字触发，本身无副作用）。
    """
    counts = {"rules": 0, "regex": 0, "lorebook": 0, "files": len(results)}
    for res in results:
        bundle = res["bundle"]
        for key in ("rules", "regex", "lorebook"):
            items = res.get(key) or []
            if not items:
                continue
            if not enable_imported and key in ("rules", "regex"):
                for it in items:
                    it["enabled"] = False
            old = preset.get(key, default=[]) or []
            kept = [x for x in old if str(x.get("bundle") or "") != bundle]
            preset.set(kept + items, key)
            counts[key] += len(items)
        if import_sampling and res.get("sampling"):
            params = preset.get("sampling", "params", default={}) or {}
            for k, v in res["sampling"].items():
                item = params.get(k) or {}
                item.update({"on": True, "value": v})
                params[k] = item
            preset.set(params, "sampling", "params")
            preset.set(True, "sampling", "override")
    preset.save()
    return counts


def import_directory(path: str | Path, preset: Any,
                     import_sampling: bool = False,
                     enable_imported: bool = False) -> Dict[str, Any]:
    """扫描目录并导入全部可识别文件。返回汇总。"""
    files = scan_directory(path)
    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    for f in files:
        try:
            data = json.loads(Path(f["path"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f['name']}: {exc}")
            continue
        bundle = f["name"]
        if f["kind"] == "preset":
            parsed = parse_preset(data, bundle)
            results.append({"bundle": bundle, **parsed})
        else:
            results.append({"bundle": bundle,
                            "lorebook": parse_lorebook(data, bundle)})
    counts = import_result(preset, results, import_sampling, enable_imported)
    return {"files": files, "counts": counts, "errors": errors}


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
def main(argv: List[str]) -> int:
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    args = [a for a in argv[1:] if not a.startswith("--")]
    target = args[0] if args else "."
    from roleplay.roleplay_preset import RolePlayPreset
    preset = RolePlayPreset.instance()

    if "--as-cards" in argv:
        cat: Optional[str] = None
        for name, val in (("--category-style", CARD_CATEGORY_STYLE),
                          ("--category-world", CARD_CATEGORY_WORLD),
                          ("--category-tools", CARD_CATEGORY_TOOLS)):
            if name in argv:
                cat = val
        summary = import_as_cards(target, preset, category=cat,
                                  only_enabled="--all" not in argv)
        print(f"扫描到 {len(summary['files'])} 个可导入文件，已写入设定卡：")
        for c in summary["cards"]:
            print(f"  [{c['category']:11s}] {c['name']}"
                  f"  （条目 {c['prompts']}，正则 {c['regex']}）"
                  f" -> {c['path']}")
        print("卡片总数：", len(summary["cards"]))
        if summary["errors"]:
            print("失败：", summary["errors"])
        return 0

    summary = import_directory(target, preset,
                               import_sampling="--with-sampling" in argv)
    print(f"扫描到 {len(summary['files'])} 个可导入文件：")
    for f in summary["files"]:
        print(f"  [{f['kind']:8s}] {f['name']}")
    print("导入结果：", summary["counts"])
    if summary["errors"]:
        print("失败：", summary["errors"])
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
