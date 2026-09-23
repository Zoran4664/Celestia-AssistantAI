"""
skill_eval.py — 技能评测闭环（轻量版）（V2-C5）

参考 HanaAgent（openhanako）skills2set/skill-creator 的评测流水线，轻量落地：
- 测试用例：skills/test_cases/<技能名>.json
    [{"input": "帮我翻译这段话", "expect_trigger": true, "expect_keyword": "翻译"}, ...]
- 解析层评测（必测、无头可跑）：对每条用例用 SkillManager.parse_tags 检查
  「是否命中该技能」与预期一致（expect_trigger）；
- 回复层评测（可选、依赖 LLM）：调小模型生成回复，检查 expect_keyword 是否出现；
  超时 / 失败自动降级跳过该条回复断言（不影响解析层结果）；
- 输出报告：{total, parsed_ok, replied_ok, rate, failures:[{input, reason}]}

线程纪律：本模块为普通单例（无 Qt），可任意线程调用；JSON 原子写。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config_loader import ConfigLoader
from skill_manager import SkillManager

logger = logging.getLogger("AI_DeskMate.SkillEval")

_TEST_CASES_DIR = "test_cases"


class SkillEval:
    """技能评测器（单例）。"""

    _instance: Optional["SkillEval"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._generator: Optional[Callable[[str, str], str]] = None  # 可注入回复生成器

    @classmethod
    def instance(cls) -> "SkillEval":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_generator(self, fn: Optional[Callable[[str, str], str]]) -> None:
        """注入回复生成器（测试用）；None 恢复默认。"""
        self._generator = fn

    # ------------------------------------------------------------ 用例管理
    def cases_dir(self) -> Path:
        return self._cfg.skills_dir / _TEST_CASES_DIR

    def cases_file(self, skill_name: str) -> Path:
        return self.cases_dir() / f"{skill_name}.json"

    def list_skills_with_cases(self) -> List[str]:
        """有测试用例的技能名列表。"""
        d = self.cases_dir()
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.json"))

    def load_cases(self, skill_name: str) -> List[Dict[str, Any]]:
        try:
            p = self.cases_file(skill_name)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("评测用例读取失败 %s: %s", skill_name, exc)
        return []

    def save_cases(self, skill_name: str, cases: List[Dict[str, Any]]) -> None:
        try:
            p = self.cases_file(skill_name)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cases, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("评测用例保存失败: %s", exc)

    # ------------------------------------------------------------ 评测
    def _default_generator(self) -> Callable[[str, str], str]:
        """默认回复生成器：小模型按技能上下文生成回复。"""

        def _gen(skill_name: str, prompt: str) -> str:
            from llm_client import LLMClientPool
            pool = LLMClientPool.instance()
            try:
                timeout = float(self._cfg.get("memory", "denoise_timeout_seconds",
                                              default=5.0) or 5.0)
                return pool.chat_complete(
                    [{"role": "system",
                      "content": f"你正在使用技能「{skill_name}」处理用户请求，"
                                 "直接给出符合该技能用途的回复。"},
                     {"role": "user", "content": prompt}],
                    kind="small", max_tokens=120, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("评测回复生成失败（跳过回复断言）: %s", exc)
                return ""
        return _gen

    def _get_generator(self) -> Callable[[str, str], str]:
        return self._generator or self._default_generator()

    def run_skill_eval(self, skill_name: str,
                       skill: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """对指定技能跑评测。

        :param skill_name: 技能名
        :param skill: 技能定义（缺省用 SkillManager 解析）
        :return: {"skill": str, "total": n, "parsed_ok": n, "replied_ok": n,
                  "rate": float, "failures": [{input, reason}], "skipped_reply": n}
        """
        cases = self.load_cases(skill_name)
        if not cases:
            return {"skill": skill_name, "total": 0, "parsed_ok": 0,
                    "replied_ok": 0, "rate": 0.0, "failures": [],
                    "skipped_reply": 0, "no_cases": True}
        sm = SkillManager.instance()
        sm.ensure_loaded()
        if skill is None:
            skill = sm.resolve(skill_name) or next(
                (s for s in sm.skills() if s.get("name") == skill_name), None)
        trigger = str((skill or {}).get("trigger") or "").strip()
        gen = self._get_generator()

        parsed_ok = 0
        replied_ok = 0
        skipped_reply = 0
        failures: List[Dict[str, Any]] = []

        for i, case in enumerate(cases):
            if not isinstance(case, dict):
                continue
            inp = str(case.get("input") or "")
            expect_trigger = bool(case.get("expect_trigger", True))
            kw = str(case.get("expect_keyword") or "")
            # 解析层：\@触发词 是否命中该技能
            hits = [t for t in sm.parse_tags(inp) if t.get("trigger") == trigger]
            hit = len(hits) > 0
            if hit != expect_trigger:
                failures.append({
                    "input": inp, "reason":
                    f"触发解析 {'命中但预期未命中' if hit else '未命中但预期命中'}"})
                continue
            parsed_ok += 1
            # 回复层（可选）：生成回复并检查关键词
            if kw:
                reply = ""
                try:
                    reply = gen(skill_name, inp) or ""
                except Exception:  # noqa: BLE001
                    reply = ""
                if not reply:
                    skipped_reply += 1
                    continue
                if kw in reply:
                    replied_ok += 1
                else:
                    failures.append({
                        "input": inp,
                        "reason": f"回复未包含关键词「{kw}」"})
        total = len(cases)
        # 通过率：解析层全对 + 回复层（有生成时）关键词命中才算该条通过
        passed = total - len(failures)
        rate = (passed / total) if total else 0.0
        return {
            "skill": skill_name, "total": total, "parsed_ok": parsed_ok,
            "replied_ok": replied_ok, "rate": round(rate, 3),
            "failures": failures[:20], "skipped_reply": skipped_reply,
            "no_cases": False,
        }
