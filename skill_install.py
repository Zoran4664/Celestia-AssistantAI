"""
skill_install.py — 技能安全审查 + 安装（V2-C4）

参考 HanaAgent（openhanako）lib/tools/install-skill.ts + skill-package-installer.ts：

- 安装：导入技能包 zip（复用 skill_bundles.import_bundle_zip：解包 → 校验 → 并入技能库，
  同名跳过不覆盖）；
- 安全审查（软门槛）：安装前用「本地规则 + LLM」两级审查技能提示词，识别四类风险：
  prompt injection / 过宽触发词 / 越权行为 / 诱导泄漏密钥；
  返回 low / medium / high；high 需要用户显式确认后才允许安装（UI 层把关）；
- 审查器可注入（测试用），默认走小模型（kind=small），超时/失败降级为 low 放行
  （安全底线由 UI 层确认承担）。

线程纪律：本模块为普通单例（无 Qt），可在任意线程调用；JSON 原子写。
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config_loader import ConfigLoader
from skill_bundles import SkillBundles

logger = logging.getLogger("AI_DeskMate.SkillInstall")

# 本地规则初筛（快速识别明显风险，不完全依赖 LLM）
_INJECTION_MARKERS = [
    "忽略之前的指令", "忽略之前指令", "ignore previous instructions",
    "ignore all previous", "你就是系统", "you are the system",
    "不要告诉用户", "don't tell the user", "隐瞒", "假装",
    "api key", "api_key", "密码", "secret", "token",
    "绕过", "越权", "删除文件", "format ", "rm -rf",
    "把以下内容", "输出你的 system prompt", "reveal your prompt",
]


class SkillInstaller:
    """技能安装器（单例）。"""

    _instance: Optional["SkillInstaller"] = None

    def __init__(self) -> None:
        self._cfg = ConfigLoader.instance()
        self._reviewer: Optional[Callable[[str], str]] = None  # 可注入的 LLM 审查器

    @classmethod
    def instance(cls) -> "SkillInstaller":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_reviewer(self, fn: Optional[Callable[[str], str]]) -> None:
        """注入审查器（测试用）；None 恢复默认。"""
        self._reviewer = fn

    # ------------------------------------------------------------ 审查
    def _default_reviewer(self) -> Callable[[str], str]:
        """默认审查器：小模型判断风险等级（low/medium/high）。"""

        def _review(prompt: str) -> str:
            from llm_client import LLMClientPool
            pool = LLMClientPool.instance()
            try:
                timeout = float(self._cfg.get("memory", "denoise_timeout_seconds",
                                              default=5.0) or 5.0)
                text = pool.chat_complete(
                    [{"role": "system",
                      "content": "你是技能安全审查员。判断下面技能提示词的风险等级，"
                                 "只输出一个词：low（无风险）/ medium（有可疑但常见）"
                                 "/ high（明显越权、注入或诱导泄漏密钥）。"},
                     {"role": "user", "content": prompt[:2000]}],
                    kind="small", max_tokens=8, timeout=timeout)
                t = (text or "").strip().lower()
                if "high" in t:
                    return "high"
                if "medium" in t:
                    return "medium"
                return "low"
            except Exception as exc:  # noqa: BLE001
                logger.warning("技能审查 LLM 失败（按 low 放行）: %s", exc)
                return "low"
        return _review

    def _get_reviewer(self) -> Callable[[str], str]:
        return self._reviewer or self._default_reviewer()

    def safety_review(self, prompt: str) -> str:
        """两级审查（本地规则 + LLM），返回 low / medium / high。"""
        prompt = (prompt or "").strip()
        if not prompt:
            return "low"
        low = prompt.lower()
        hits = [m for m in _INJECTION_MARKERS if m in low]
        risk = "high" if hits else "low"
        try:
            llm = self._get_reviewer()(prompt)
            if llm in ("medium", "high"):
                risk = llm  # LLM 可提升风险；low 不覆盖本地命中
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能审查异常（沿用本地判定）: %s", exc)
        return risk

    # ------------------------------------------------------------ 预览与审查
    def preview_and_review(self, zip_path: Path) -> Dict[str, Any]:
        """读取技能包 zip 中的技能定义，逐技能审查。

        :return: {"skills": [{name, risk}], "worst_risk": str, "valid": bool}
        """
        result: Dict[str, Any] = {"skills": [], "worst_risk": "low", "valid": False}
        zip_path = Path(zip_path)
        if not zip_path.exists():
            return result
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                if "bundle.json" not in names:
                    return result
                skills = []
                for n in names:
                    if not n.startswith("skills/") or not n.endswith("/skill.json"):
                        continue
                    try:
                        skill = json.loads(zf.read(n).decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        continue
                    if not isinstance(skill, dict):
                        continue
                    name = str(skill.get("name") or n.split("/")[1])
                    # 详情（description/prompt_template）拼接审查
                    detail = {}
                    detail_item = n.replace("/skill.json", "/detail.json")
                    if detail_item in names:
                        try:
                            detail = json.loads(zf.read(detail_item).decode("utf-8"))
                        except Exception:  # noqa: BLE001
                            detail = {}
                    prompt_src = " ".join([
                        str(skill.get("description") or ""),
                        str(skill.get("prompt_template") or ""),
                        str(detail.get("description") or ""),
                        str(detail.get("prompt_template") or ""),
                    ])
                    risk = self.safety_review(prompt_src)
                    skills.append({"name": name, "risk": risk})
                if not skills:
                    return result
                worst = max((s["risk"] for s in skills), key={
                    "low": 0, "medium": 1, "high": 2}.get)
                result.update({"skills": skills, "worst_risk": worst, "valid": True})
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能包预览失败: %s", exc)
        return result

    # ------------------------------------------------------------ 安装
    def install_zip(self, zip_path: Path) -> Dict[str, Any]:
        """导入技能包 zip（并入技能库，同名跳过）。"""
        return SkillBundles.instance().import_bundle_zip(zip_path)
