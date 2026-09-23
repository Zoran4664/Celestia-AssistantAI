"""
roleplay_engine.py — 角色扮演装配引擎

把「预设（参数 / 规则卡 / 正则 / 世界书 / 变量 / 卡片）」装配成实际
送给模型的系统提示词，并对「用户输入 / 模型输出」做双通道后处理。

融合的机制（来自酒馆预设系列）：

- 变量系统：{{setvar::名::内容}}（覆盖）/ {{addvar::名::内容}}（累加）/
            {{getvar::名}}（消费）。规则「定义与消费分离」：
            规则卡只负责往变量里写，输出骨架/正文规则只负责读。
- 宏：{{user}} {{char}} {{group}} {{members}} {{time}} {{date}} {{weekday}}
      {{random::a::b::c}} {{//注释}}
- 世界书：关键字触发（主/次触发词）+ constant 常驻 + order 排序 +
          position 插入位置 + probability 概率。
- 正则双通道：
    outgoing  —— 用户消息发出前（placement 含 1）
    for_model —— AI 回复送模型/归档前（placement 含 2，prompt_only 或两不勾）
    for_view  —— AI 回复展示给用户（placement 含 2，markdown_only 或两不勾）
  min_depth / max_depth 控制「最近 N 楼」生效范围（0 = 最新一条）。
- 采样参数：只返回「开启了的」项，未开启的沿用全局 config。
"""

from __future__ import annotations

import datetime as _dt
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from roleplay.roleplay_preset import RolePlayPreset

logger = logging.getLogger("AI_DeskMate.RolePlayEngine")

# {{setvar::名::内容}} / {{addvar::名::内容}}
_SETVAR_RE = re.compile(r"\{\{\s*setvar\s*::\s*([^:{}]+?)\s*::([\s\S]*?)\}\}", re.I)
_ADDVAR_RE = re.compile(r"\{\{\s*addvar\s*::\s*([^:{}]+?)\s*::([\s\S]*?)\}\}", re.I)
_GETVAR_RE = re.compile(r"\{\{\s*getvar\s*::\s*([^:{}]+?)\s*\}\}", re.I)
_RANDOM_RE = re.compile(r"\{\{\s*random\s*::([\s\S]*?)\}\}", re.I)
_COMMENT_RE = re.compile(r"\{\{\s*//[\s\S]*?\}\}")
_MACRO_RE = re.compile(
    r"\{\{\s*(user|char|group|members|time|date|weekday|datetime)\s*\}\}", re.I)

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _parse_regex(find: str) -> Tuple[Optional["re.Pattern[str]"], str]:
    """解析酒馆风格正则：/pattern/flags 或裸 pattern。返回 (编译结果, 错误)。"""
    raw = (find or "").strip()
    if not raw:
        return None, ""
    flags = 0
    if raw.startswith("/"):
        end = raw.rfind("/")
        if end > 0:
            flag_str = raw[end + 1:]
            body = raw[1:end]
            if "i" in flag_str:
                flags |= re.I
            if "s" in flag_str:
                flags |= re.S
            if "m" in flag_str:
                flags |= re.M
            raw = body
    try:
        return re.compile(raw, flags), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _to_python_repl(rep: str) -> str:
    """把酒馆风格的替换串转成 Python re 语法。

    支持：$1 / ${1} / $<name> / ${name}（分组引用），$$（字面量 $）。
    """
    if "$" not in rep:
        return rep
    out = rep.replace("$$", "\x00")
    out = re.sub(r"\$\{(\d+|[A-Za-z_][A-Za-z_0-9]*)\}", r"\\g<\1>", out)
    out = re.sub(r"\$<([A-Za-z_][A-Za-z_0-9]*)>", r"\\g<\1>", out)
    out = re.sub(r"\$(\d+)", r"\\\1", out)
    return out.replace("\x00", "$")


class RolePlayEngine:
    """预设装配引擎（无 GUI 依赖，可在工作线程内直接使用）。"""

    def __init__(self, preset: Optional[RolePlayPreset] = None) -> None:
        self._preset = preset or RolePlayPreset.instance()

    # ------------------------------------------------------------------ 数据
    @property
    def preset(self) -> RolePlayPreset:
        return self._preset

    def reload(self) -> None:
        self._preset.load()

    # ------------------------------------------------------------------ 宏
    def expand_macros(self, text: str, *, user: str = "用户", char: str = "",
                      group: str = "", members: Optional[List[str]] = None,
                      now: Optional[_dt.datetime] = None) -> str:
        """展开 {{user}}/{{char}}/{{random::}}/{{//注释}} 等宏。"""
        if not text:
            return ""
        out = _COMMENT_RE.sub("", text)
        now = now or _dt.datetime.now()

        def _rand(m: "re.Match[str]") -> str:
            opts = [s for s in m.group(1).split("::")]
            opts = [s for s in opts if s != ""]
            return random.choice(opts) if opts else ""

        out = _RANDOM_RE.sub(_rand, out)

        def _macro(m: "re.Match[str]") -> str:
            key = m.group(1).lower()
            if key == "user":
                return user
            if key == "char":
                return char
            if key == "group":
                return group
            if key == "members":
                return "、".join(members or [])
            if key == "time":
                return now.strftime("%H:%M")
            if key == "date":
                return now.strftime("%Y年%m月%d日")
            if key == "weekday":
                return _WEEKDAYS[now.weekday()]
            if key == "datetime":
                return now.strftime("%Y年%m月%d日 %H:%M")
            return ""

        out = _MACRO_RE.sub(_macro, out)
        return out

    # ------------------------------------------------------------------ 变量
    @staticmethod
    def apply_variables(text: str, variables: Dict[str, str]) -> str:
        """执行 setvar / addvar 定义，并替换所有 getvar。

        variables 会被就地更新（调用方传入同一 dict 即可跨块共享）。
        """
        if not text:
            return ""

        def _set(m: "re.Match[str]") -> str:
            variables[m.group(1).strip()] = m.group(2)
            return ""

        def _add(m: "re.Match[str]") -> str:
            k = m.group(1).strip()
            v = m.group(2)
            old = variables.get(k, "")
            variables[k] = (old + "\n" + v).strip() if old.strip() else v.strip()
            return ""

        out = _SETVAR_RE.sub(_set, text)
        out = _ADDVAR_RE.sub(_add, out)

        for _ in range(4):   # 支持变量嵌套引用
            new = _GETVAR_RE.sub(
                lambda m: variables.get(m.group(1).strip(), ""), out)
            if new == out:
                break
            out = new
        return out

    # ------------------------------------------------------------------ 卡片
    #: 旧版类别名 → 现行目录名（与 roleplaytool.RolePlayManager 对齐）
    _CAT_ALIASES = {"personal": "rolepersonal", "tools": "roletools"}

    def card_context(self, cards: Optional[Dict[str, List[str]]] = None) -> str:
        """按 inject_order 拼接设定卡内容。cards 缺省取预设中的选择。

        需求（严格加载）：``cards`` 显式传入时**只加载传入的卡片**，即使为空
        也不再回退读取任何旧配置——没勾中的设定卡绝不会进入上下文。
        """
        explicit = cards is not None
        raw = cards if explicit else (
            self._preset.get("cards", default={}) or {})
        # 类别名归一（兼容旧版 personal/tools 命名）
        sel: Dict[str, List[str]] = {}
        for cat, names in (raw or {}).items():
            sel[self._CAT_ALIASES.get(str(cat), str(cat))] = [
                str(n) for n in (names or [])]
        # 兼容旧配置：只有当预设文件**从未写过 cards 字段**时（老版本预设），
        # 且调用方没有显式指定卡片时，才一次性回退到 config.json 做迁移读取。
        # 若用户已在预设/设置面板里明确清空勾选，则严格一张都不加载——
        # 否则「没勾的卡片」会又被旧配置偷偷加载进上下文。
        legacy_ok = False
        try:
            legacy_ok = not self._preset.has_own("cards")
        except Exception:  # noqa: BLE001
            legacy_ok = False
        if legacy_ok and not explicit and not any(
                sel.get(c) for c in ("style", "world",
                                     "rolepersonal", "roletools")):
            try:
                from config_loader import ConfigLoader
                legacy = ConfigLoader.instance().get(
                    "ui", "roleplay_cards", default=[]) or []
            except Exception:  # noqa: BLE001
                legacy = []
            if legacy:
                sel = {}
                for key in legacy:
                    cat, _, name = str(key).partition("/")
                    cat = self._CAT_ALIASES.get(cat, cat)
                    if cat and name:
                        sel.setdefault(cat, []).append(name)
        # 对话风格是**单选**：即使旧配置里残留了多张，也只加载第一张，
        # 其余一律不注入（严格只运行被勾选/选中的那一张）。
        if len(sel.get("style") or []) > 1:
            sel["style"] = (sel.get("style") or [])[:1]
        try:
            from roleplay.roleplaytool import RolePlayManager
        except Exception as exc:  # noqa: BLE001
            logger.warning("RolePlayManager 导入失败: %s", exc)
            return ""
        mgr = RolePlayManager()
        order = (self._preset.get("assembly", "inject_order",
                                 default=["rolepersonal", "world",
                                          "style", "roletools"])
                 or ["rolepersonal", "world", "style", "roletools"])
        order = [self._CAT_ALIASES.get(str(c), str(c)) for c in order]
        blocks: List[str] = []
        labels = {"rolepersonal": "个人设定", "world": "世界观",
                  "style": "对话风格", "roletools": "角色工具"}
        # 主角设定卡由 persona_context 单独注入【主角设定】分节，
        # 这里从「个人设定」多选中剔除，避免同一张卡重复注入两遍。
        persona = self.user_persona()
        for cat in order:
            names = [str(n) for n in (sel.get(cat) or []) if str(n).strip()]
            if persona and cat == "rolepersonal":
                names = [n for n in names if n != persona]
            if not names:
                continue
            keys = [f"{cat}/{n}" for n in names]
            body = mgr.build_context(keys)
            if body:
                blocks.append(f"【{labels.get(cat, cat)}】\n{body}")
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ 主角设定
    def user_persona(self) -> str:
        """主角设定卡名（你在扮演里的身份；空表示不使用）。"""
        return str(self._preset.get("assembly", "user_persona",
                                    default="") or "").strip()

    def persona_context(self, persona: str = "") -> str:
        """主角设定卡正文 → 【主角设定】分节。

        需求：角色扮演设置里可指定「主角设定」（单选），让模型知道 {{user}}
        本人在这段扮演里的身份，而不是把所有个人设定卡都当成角色设定。
        """
        name = (persona or self.user_persona()).strip()
        if not name:
            return ""
        try:
            from roleplay.roleplaytool import RolePlayManager
            body = RolePlayManager().build_context([f"rolepersonal/{name}"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("主角设定卡读取失败 %s: %s", name, exc)
            return ""
        return (body or "").strip()

    # --------------------------------------------------- OOC / 世界观破限
    #: 开启「允许 OOC / 突破世界观」时追加的指令（仍参考世界书背景设定）
    OOC_NOTICE = (
        "【OOC / 破限模式（用户已开启）】\n"
        "- 允许出戏（OOC）：可以脱离角色进行说明、吐槽、复盘，或与用户直接"
        "讨论设定和剧情走向，可用【OOC】标注。\n"
        "- 允许突破世界观：不必因「设定里不可能发生」而拒绝用户请求，可做"
        "跨世界观联动、引入原作不存在的要素、打破第四面墙。\n"
        "- 但必须参考下方【世界书】/【世界观】的**背景设定**：地名、势力、"
        "历史时间线、专有术语与人物关系优先沿用，保持基本自洽；\n"
        "  只有当偏离能明显提升情节张力、或用户明确要求时才偏离，并可简短"
        "说明「这里做了破限处理」。\n"
        "- 角色本身的性格基调与说话口吻保持不变；用户要求回到角色/继续剧情"
        "时，立即恢复正常扮演。\n"
        "- 破限只针对世界观与叙事形式，不用于绕过内容安全底线。"
    )

    def ooc_notice(self) -> str:
        """OOC / 破限模式开启时返回指令段，否则空串。"""
        if not bool(self._preset.get("assembly", "ooc_mode", default=False)):
            return ""
        return self.OOC_NOTICE

    # ------------------------------------------------------------------ 规则
    def _enabled_rules(self) -> List[Dict[str, Any]]:
        rules = self._preset.get("rules", default=[]) or []
        out = [r for r in rules if isinstance(r, dict) and r.get("enabled")]
        out.sort(key=lambda r: int(r.get("order") or 100))
        return out

    def render_rules(self, variables: Dict[str, str]) -> str:
        """渲染规则卡：text 直接输出；setvar/addvar 只写变量不输出。"""
        blocks: List[str] = []
        for r in self._enabled_rules():
            mode = str(r.get("mode") or "text").lower()
            name = str(r.get("name") or "").strip()
            var = str(r.get("variable") or "").strip()
            content = str(r.get("content") or "")
            if mode in ("setvar", "addvar"):
                if not var:
                    continue
                tpl = ("{{setvar::%s::%s}}" if mode == "setvar"
                       else "{{addvar::%s::%s}}") % (var, content)
                self.apply_variables(tpl, variables)
                continue
            if not content.strip():
                continue
            blocks.append((f"[{name}]\n{content}" if name else content))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ 世界书
    def lorebook_context(self, scan_text: str) -> str:
        """按关键字命中 + constant 收集世界书条目（已排序、已去空）。"""
        entries = self._preset.get("lorebook", default=[]) or []
        if not entries:
            return ""
        text = scan_text or ""
        hits: List[Tuple[int, int, str]] = []
        for e in entries:
            if not isinstance(e, dict) or not e.get("enabled"):
                continue
            content = str(e.get("content") or "").strip()
            if not content:
                continue
            constant = bool(e.get("constant"))
            if not constant and not text:
                continue
            ok = constant
            if not ok:
                keys = [str(k).strip() for k in (e.get("keys") or []) if str(k).strip()]
                sec = [str(k).strip() for k in (e.get("secondary_keys") or [])
                       if str(k).strip()]
                ok = any(k in text for k in keys)
                if not ok and sec:
                    # 次要触发词需与主键同时出现（ST selective 语义）
                    ok = any(k in text for k in sec) and any(
                        k in text for k in keys) if keys else any(
                        k in text for k in sec)
            if not ok:
                continue
            try:
                prob = int(e.get("probability") or 100)
            except Exception:  # noqa: BLE001
                prob = 100
            if prob < 100 and random.randint(1, 100) > prob:
                continue
            order = int(e.get("order") or 100)
            pos = int(e.get("position") or 1)
            name = str(e.get("name") or e.get("comment") or "").strip()
            block = f"[{name}]\n{content}" if name else content
            hits.append((pos, order, block))
        hits.sort(key=lambda x: (x[0], x[1]))
        return "\n\n".join(h[2] for h in hits)

    # ------------------------------------------------------------------ 正则
    def _scripts(self, placement: int) -> List[Dict[str, Any]]:
        """当前生效的正则脚本 —— **只读取预设顶层的 ``regex``**。

        设计说明（回答「勾选 TGbreak 为什么不直接加载 ``<w2g>``」，2026-09-21）：

        1. 「勾选设定卡」加载的是卡片的 **prompts（规则正文）**——它们经
           :meth:`build_context` 注入系统提示，所以模型**会**被要求输出 ``<w2g>``；
           卡片的 ``regex_scripts`` 字段则**不随卡加载**（两者互不相干）。
        2. 不加载卡片自带正则，是**有意的**，不是遗漏：
           - 展示层（``markdown_only``）那些正则的作用是把文本包成
             ``<!DOCTYPE html>`` + ``<style>`` + ``<script>`` 的**网页卡片**
             （TGbreak 的「TG-行动选项美化」「TG-思维链美化」「TG-特写」…）。
             Qt 富文本渲染不了这套 HTML/CSS/JS，本应用改用**原生控件**等价实现：

             | 预设正则（不加载） | 本应用的原生实现 |
             |---|---|
             | ``TG-行动选项美化``（``<w2g>`` → 网页卡片） | ``ui_manager.extract_w2g_choices`` → 可点击选项按钮 |
             | ``TG-思维链美化`` / ``TG-一拳超人``（梳理 → 折叠 HTML） | ``extract_plot_draft`` / 思维链折叠块 |
             | ``TG-7楼内摘要隐藏`` | ``extract_tag_blocks`` / 折叠块 |
             | ``TG-对你隐藏``（``《end》`` 等） | ``_PRESET_END_RE`` |

             即：**显示层行为已经内置**，再加载那些正则会变成「生成一坨
             Qt 渲染不了的 HTML」——纯属重复劳动。
           - 模型层（``prompt_only``）那几条是 SillyTavern 的**提示词注入 hack**
             （``TG-别关V3.0.5`` 把用户消息包成 ``<peip>``、``TG-巡回`` 把回复包成
             ``<ai_last_output>``）。它们依赖 ST「prompt_only 只影响送模型的文本」
             的语义；而本应用 ``ChatWorker`` 的 ``clean = incoming_model(text)``
             **同时用于界面展示与记忆归档**，加载后会把 ``<peip>`` /
             ``<ai_last_output>`` 直接显示出来。
        3. 想让某条正则真正生效：在「角色扮演预设管理器 → 正则脚本」页手工新增
           （或导入），它会写进预设顶层 ``regex``，从而被这里读取。
        """
        items = self._preset.get("regex", default=[]) or []
        out: List[Dict[str, Any]] = []
        for s in items:
            if not isinstance(s, dict) or s.get("disabled") or not s.get("enabled"):
                continue
            pl = s.get("placement") or [1, 2]
            if isinstance(pl, int):
                pl = [pl]
            if placement not in [int(x) for x in pl]:
                continue
            out.append(s)
        return out

    @staticmethod
    def _depth_ok(s: Dict[str, Any], depth: int) -> bool:
        try:
            mn = int(s.get("min_depth") or 0)
        except Exception:  # noqa: BLE001
            mn = 0
        try:
            mx = int(s.get("max_depth") or 0)
        except Exception:  # noqa: BLE001
            mx = 0
        if mn and depth < mn:
            return False
        if mx and depth > mx:
            return False
        return True

    def apply_regex(self, text: str, placement: int, *,
                    for_model: bool = True, depth: int = 0) -> str:
        """应用正则。

        for_model=True  → 模型层（prompt_only 或两不勾）
        for_model=False → 展示层（markdown_only 或两不勾）
        """
        if not text:
            return text
        out = text
        for s in self._scripts(placement):
            if not self._depth_ok(s, depth):
                continue
            md = bool(s.get("markdown_only"))
            po = bool(s.get("prompt_only"))
            if md and po:
                continue          # 互斥，配置错误时跳过
            if for_model and md:
                continue
            if (not for_model) and po:
                continue
            pattern, err = _parse_regex(str(s.get("find") or ""))
            if pattern is None:
                if err:
                    logger.debug("正则 %s 编译失败: %s", s.get("name"), err)
                continue
            try:
                out = pattern.sub(_to_python_repl(str(s.get("replace") or "")), out)
            except Exception as exc:  # noqa: BLE001
                logger.warning("正则 %s 执行失败: %s", s.get("name"), exc)
        return out

    def outgoing(self, text: str) -> str:
        """用户消息发出前处理（placement 1）。"""
        return self.apply_regex(text, 1, for_model=True, depth=0)

    def incoming_model(self, text: str, depth: int = 0) -> str:
        """AI 回复 → 送模型 / 归档前的清洗（placement 2，模型层）。"""
        return self.apply_regex(text, 2, for_model=True, depth=depth)

    def incoming_view(self, text: str, depth: int = 0) -> str:
        """AI 回复 → 展示给用户（placement 2，展示层）。"""
        return self.apply_regex(text, 2, for_model=False, depth=depth)

    # ------------------------------------------------------------------ 采样
    def sampling_overrides(self) -> Dict[str, Any]:
        return self._preset.sampling_overrides()

    # --------------------------------------------------- 强制输出格式（<w2g>）
    #: 卡片提到 ``<w2g>`` 时，追加到系统提示**末尾**的强制段。
    #: 需求（用户 2026-09-21「TGbreak 应该每一个[回复]都强制输出 <w2g>」）：
    #: TGbreak 系预设把「正文后，你必须给出选择框 <w2g>」写在卡片的规则正文里，
    #: 而设定卡是**整卡 JSON 注入**（实测该上下文 134K 字符 / 41.7K tokens），
    #: 这条强制要求会被淹在几万字的规则里。真实模型实测（deepseek-flash，
    #: 同一预设 × 4 组输入）模型只有 **1/4** 会真的输出 ``<w2g>`` ——
    #: 表现就是用户看到的「行动选项时有时无」。
    #: 把该要求**提到系统提示末尾单独成段**（模型对末尾指令最敏感，实测 4/4）
    #: 即可稳定复现；仅在上下文确实提到 ``<w2g>`` 时追加，不影响其它预设。
    W2G_ENFORCE = (
        "【强制输出格式（最高优先级，覆盖以上任何与之冲突的说明）】\n"
        "每一条回复的**正文之后**都必须输出选择框，标签固定写作 <w2g>：\n"
        "<w2g>\n"
        "A：小标题：{{user}}要做的决策\n"
        "B：…\n"
        "</w2g>\n"
        "要求：\n"
        "1. 每一条回复都要给出，不得省略、不得换成其它标签写法；\n"
        "2. 2~6 个选项，且必须包含一个「跳过」选项；\n"
        "3. 选项只写 {{user}} 要做的决策本身，不写决策产生的结果；\n"
        "4. <w2g> 与 </w2g> 必须成对闭合，且不得把正文包进去。"
    )

    # ------------------------------------------------------------------ 总装
    def build_context(self, *, user: str = "用户", char: str = "",
                      group: str = "", members: Optional[List[str]] = None,
                      scan_text: str = "",
                      cards: Optional[Dict[str, List[str]]] = None,
                      skeleton: Optional[str] = None) -> str:
        """拼装角色扮演附加上下文（不含角色卡本体，由调用方拼接）。

        顺序：个人设定 → 世界观 → 对话风格 → 角色工具 → 世界书 → 规则 → 输出骨架
        → （必要时）强制输出格式
        全部拼完后再统一走「宏 → 变量」两遍处理，保证规则里定义的变量
        能被输出骨架 {{getvar::}} 正确消费。
        """
        variables: Dict[str, str] = {}
        parts: List[str] = []

        # 主角设定（{{user}} 本人）优先注入，避免被当成角色设定
        persona = self.persona_context()
        if persona:
            parts.append("【主角设定（{{user}} 本人）】\n" + persona)
        card_ctx = self.card_context(cards)
        if card_ctx:
            parts.append(card_ctx)
        # OOC / 破限指令放在世界书之前：先说明「可参考但不必严守」，再给背景设定
        ooc = self.ooc_notice()
        if ooc:
            parts.append(ooc)
        lore = self.lorebook_context(scan_text)
        if lore:
            parts.append("【世界书】\n" + lore)
        rules = self.render_rules(variables)
        if rules:
            parts.append("【规则】\n" + rules)

        asm = self._preset.get("assembly", default={}) or {}
        sk = skeleton if skeleton is not None else str(asm.get("output_skeleton") or "")
        if sk.strip():
            parts.append("【输出要求】\n" + sk)

        # 选择框强制段：卡片/骨架里出现 <w2g> 就把它提到末尾（见 W2G_ENFORCE 说明）
        partial = "\n\n".join(parts)
        if ("<w2g" in partial.lower()
                and "强制输出格式" not in partial):
            parts.append(self.W2G_ENFORCE)

        if not parts:
            return ""
        text = "\n\n".join(parts)
        text = self.expand_macros(text, user=user, char=char, group=group,
                                  members=members)
        text = self.apply_variables(text, variables)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
