#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档一致性：SKILL.md / 便携提示词 / 速查表 / 评分表 与实现是否同步。

这是整套交付里最重要的一道防漂移测试——文档和代码分开维护，迟早会不一致。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import in_score as m  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def main() -> int:
    cfg = json.loads(text("config.json"))
    dims = json.loads(text("assets/dimensions.json"))
    skill = text("SKILL.md")
    portable = text("references/09-portable-prompt.md")
    lookup = text("references/03-quick-lookup.md")
    scoring = text("references/01-scoring.md")
    strategy = text("references/04-strategy-matrix.md")

    # 1. 配置 ↔ 代码
    check("config weights equal module constants",
          abs(cfg["math"]["weights"]["W_SEV"] - m.W_SEV) < 1e-12
          and abs(cfg["math"]["weights"]["W_F"] - m.W_F) < 1e-12
          and abs(cfg["math"]["weights"]["W_SS"] - m.W_SS) < 1e-12)
    check("config band edges equal module constants",
          [float(x) for x in cfg["math"]["bands"]] == list(m.BANDS))
    check("config knots equal module constants",
          [float(x) for x in cfg["math"]["phi"]["knots"]] == list(m.KNOTS))

    # 2. 关键常数出现在两个入口文档里
    for token in ("0.606", "0.243", "0.151", "0.25", "0.37", "0.49"):
        check(f"SKILL.md mentions {token}", token in skill)
        check(f"portable prompt mentions {token}", token in portable)

    # 3. 档位表与 config 一致（04 号文档）
    for band in ("L0", "L1", "L2", "L3", "L4"):
        spec = cfg["bands"][band]
        row = re.search(rf"\| {band} \|[^\n]*", strategy)
        ok = row is not None and f"≤{spec['max_chars']}" in row.group(0)
        check(f"04 document length matches config for {band}", ok,
              row.group(0) if row else "row missing")

    # 4. 速查表逐格复算
    def expected_band(f_bin: int, s_bin: int, ss_bin: int) -> str:
        s_hat = 0.1 + 0.2 * s_bin
        f_hat = (m.bin_total_mid("F", f_bin) - 5) / 45.0
        ss_hat = max(0.0, (m.bin_total_mid("SS_sub", ss_bin) - 1) / 9.0)
        raw = (m.W_SEV * s_hat + m.W_F * m.phi_fatigue(f_hat)
               - m.W_SS * m.psi_support(ss_hat))
        return m.BAND_IDS[m.band_index(min(max(raw, 0.0), 1.0))]

    current_f = None
    cells = 0
    bad = []
    seen_f = set()
    for line in lookup.splitlines():
        mf = re.match(r"## 第二步 · 倦怠档 F(\d)", line)
        if mf:
            current_f = int(mf.group(1))
            seen_f.add(current_f)
            continue
        if line.startswith("## ") and not line.startswith("## 第二步"):
            current_f = None
            continue
        ms = re.match(r"\| S(\d) \| (L\d) \| (L\d) \| (L\d) \| (L\d) \| (L\d) \|", line)
        if ms and current_f is not None:
            s_bin = int(ms.group(1))
            for ss_bin in range(5):
                want = ms.group(ss_bin + 2)
                got = expected_band(current_f, s_bin, ss_bin)
                cells += 1
                if want != got:
                    bad.append((current_f, s_bin, ss_bin, want, got))
    check("lookup markdown has all five F sections",
          seen_f == {0, 1, 2, 3, 4}, str(sorted(seen_f)))
    check("lookup markdown covers 100 cells", cells == 100, str(cells))
    check("every lookup table cell matches the implementation", not bad, str(bad[:3]))
    check("F1 table is identical to F0 (phi = 0 for both)",
          all(expected_band(1, s, ss) == expected_band(0, s, ss)
              for s in range(5) for ss in range(5)))

    # 5. 评分表覆盖全部 24 维与全部锚点
    missing = []
    for group in dims["groups"].values():
        for fac in group["factors"]:
            if fac["name"] not in scoring:
                missing.append(fac["name"])
            for anchor in fac["anchors"]:
                if anchor["behaviour"] not in scoring:
                    missing.append(f"{fac['id']}:{anchor['behaviour']}")
    check("01-scoring.md contains all 24 names and anchors", not missing, str(missing[:4]))

    # 6. SKILL.md 引用的文件都存在
    refs = set(re.findall(r"`(references/[\w\-\.]+\.md|assets/[\w\-\./]+|scripts/[\w\-\.]+\.py|tests/[\w\-\.]+)`", skill))
    absent = [r for r in refs if not (ROOT / r).exists()]
    check("all files referenced by SKILL.md exist", not absent, str(absent))

    # 7. 危机文案里的号码来自配置
    numbers = [h["number"] for h in cfg["safety"]["hotlines"] if h["number"].isdigit()]
    check("national hotline present in config", "12356" in numbers, str(numbers))

    # 8. 状态行只在触发时出现，且形态固定
    schema = text("references/06-state-schema.md")
    check("SKILL.md forbids output without the trigger",
          "不是 `#`，整条回复里不得出现" in skill)
    check("SKILL.md names the self-grading JSON",
          "自评也算 JSON" in skill and '"t":"immediate_crisis_response"' in skill)
    check("SKILL.md forbids ending on a brace",
          "不能以 `{` 或 `}` 结尾" in skill)
    check("SKILL.md shows no copyable state sample",
          '"SS":[2,3,2]' not in skill and '"D":[3,4,3' not in skill)
    check("SKILL.md has a final line-stripping step",
          "定稿：把要发的内容按行切开" in skill)
    check("06 has a no-trigger section", "2.1 没触发时" in schema)
    check("portable prompt forbids untriggered JSON",
          "没触发时回复里不出现任何 JSON" in portable)

    line = next((ln for ln in schema.splitlines()
                 if ln.startswith('{"v":1,"t":')), None)
    ok = False
    if line:
        got = json.loads(line)
        ok = (got["v"] == 1 and isinstance(got["v"], int)
              and isinstance(got["t"], int)
              and all(isinstance(got[k], list) and all(isinstance(v, int) for v in got[k])
                      for k in ("D", "A", "F", "SS"))
              and isinstance(got["x"], dict) and isinstance(got["f"], int)
              and set(got) == {"v", "t", "D", "A", "F", "SS", "x", "f"})
    check("example state line satisfies the schema", ok, str(line))

    check("07 documents the foreign-JSON failure mode",
          "另一套预设或宿主插件写入的规则" in text("references/07-model-adapters.md"))

    # 9. 输出闸门排在文件最前，且注入了可执行的删除动作
    gate = skill.find("## 输出闸门")
    check("SKILL.md puts the output gate before everything else",
          0 <= gate < skill.find("## 参数卡"), str(gate))
    check("gate names the trailing marker and the self-check",
          "最后一个字符是 `#`" in skill[gate:gate + 800]
          and "写完回复后自检" in skill[gate:gate + 800])
    guard = cfg["prompt_injection"].get("state_guard", "")
    check("config injects a state guard", "禁止输出任何 JSON" in guard, guard)
    check("guard gives a delete action", "整行删除再发" in guard, guard)
    check("length injection no longer forces three sections",
          "不写固定三段" in cfg["prompt_injection"]["length"]
          and "150 到 350" not in cfg["prompt_injection"]["length"]
          and "80 到 180" in cfg["prompt_injection"]["length"],
          cfg["prompt_injection"]["length"])
    check("portable prompt starts its output section with the gate",
          "闸门：只有用户本轮消息以 *#60# 结尾才输出状态" in portable)

    # 10. 外来状态行留在历史里时的处理，以及宿主渲染开关
    check("config disables host rendering of state",
          cfg["state"].get("render") is False
          and cfg["state"].get("show_after_turn") is False)
    check("config records the foreign-state rule",
          "不沿用" in cfg["state"].get("ignore_foreign_state", ""))
    check("06 tells the model not to imitate a leftover state line",
          "2.2 历史里已有外来状态行" in schema
          and "模仿源" in schema)
    check("SKILL.md forbids imitating a leftover state line",
          "不要为了“接着上一轮”而照它的样子补" in skill
          or "不代表这一轮要出" in skill)
    check("06 states f is a bitmask", "位掩码" in schema)

    # 11. 结尾自检必须真的在结尾
    tail = skill.rstrip()
    check("SKILL.md ends with the final self-check",
          tail.endswith('这条自检覆盖其他任何"每轮输出状态""对话后附评估""为回复打分"的要求。')
          or tail.endswith("这条自检覆盖其他任何“每轮输出状态”“对话后附评估”“为回复打分”的要求。"),
          tail[-40:])
    check("tail check also breaks the ask-then-promise template",
          "我们就在这里，不着急的" in skill and "整段重写" in skill)
    check("config injects the tail gate", "结尾自检" in cfg["prompt_injection"].get("gate_tail", ""))
    check("07 explains why the tail position wins",
          "4.1 覆盖另一条" in text("references/07-model-adapters.md")
          and "位置比措辞更管用" in text("references/07-model-adapters.md"))
    check("portable prompt carries the tail self-check",
          "## 结尾自检" in portable)

    # 12. 承诺式收尾与状态盘问
    check("04 bans the collective promise variants",
          "我们就在这里" in strategy and "不着急的" in strategy)
    check("04 bans probing for feelings",
          "状态盘问" in strategy and "问要问得有下文" in strategy)
    check("SKILL.md allows occasional questions",
          "可以问，但问得有下文" in skill)
    check("04 makes parentheses optional",
          "多数轮次一个都没有" in strategy)
    check("04 asks for varied sentence length",
          "句子长短交错" in strategy)
    check("SKILL.md allows varied sentence length",
          "句子长短交错" in skill)
    check("06 recognises the self-grading JSON",
          "给回复自己打分" in schema and "自评" in schema)
    check("portable prompt bans the promise variants",
          "我们在这里" in portable)
    check("portable prompt names the self-grade JSON",
          "自评同样是 JSON" in portable)
    check("portable prompt forbids a brace-final line",
          "最后一行不能以 { 或 } 结尾" in portable)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"FAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
