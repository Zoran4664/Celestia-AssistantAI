#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 4 份原始维度字典无损合并成 assets/dimensions.json。

原则
----
1. 文本逐字取自源 JSON：名称、解释、用户情绪、干预结果全部照抄。
2. 只补充三类派生信息：编号（F1/D1/A1/SS1）、评分锚点、策略标签。
3. 源文件的两处已知问题已于 2026-10-27 就地修正，修正前后的原文记入 corrections，可追溯。

用法：python build_dimensions.py [--source-dir ..] [--out ../assets/dimensions.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = SKILL_ROOT.parent
DEFAULT_OUT = SKILL_ROOT / "assets" / "dimensions.json"

SOURCES = {
    "F": ("社交倦怠评价维度.json", "SocialFatigueFactors",
          "SocialFatigueFactorName", "SocialFatigueFactorExp",
          "UserEmotion", "SFEmotionResult"),
    "D": ("抑郁评价维度.json", "DepressionFactors",
          "DepressionFactorName", "DepressionFactorExp",
          "UserEmotion", "DeEmotionResult"),
    "A": ("焦虑评价维度.json", "AnxietyFactors",
          "AnxietyFactorName", "AnxietyFactorExp",
          "UserEmotion", "AnEmotionResult"),
    "SS": ("社会支持评价 .json", "ASocialSupportFactors",
           "ASocialSupportFactorName", "ASocialSupportFactorExp",
           "UserEmotion", "SSEmotionResult"),
}

EXPLAIN_KEYS = {
    "F": "SFEvaluationMechanisms",
    "D": "DeEvaluationMechanisms",
    "A": "AnEvaluationMechanisms",
    "SS": "SSEvaluationMechanisms",
}

GROUPS = {
    "F": {"label": "社交媒体倦怠", "en": "social media fatigue", "scale": [1, 10],
          "direction": "higher_is_worse", "source_scale": "5 个因子 × 1-10 分，总分 5-50"},
    "D": {"label": "抑郁", "en": "depression (PHQ-8 aligned)", "scale": [1, 5],
          "direction": "higher_is_worse", "source_scale": "9 个维度 × 1-5 分，总分 9-45"},
    "A": {"label": "焦虑", "en": "anxiety (GAD-7 aligned)", "scale": [1, 5],
          "direction": "higher_is_worse", "source_scale": "7 个维度 × 1-5 分，总分 7-35"},
    "SS": {"label": "社会支持", "en": "social support (SSRS aligned)", "scale": [1, 10],
           "direction": "higher_is_better", "source_scale": "3 个维度 × 1-10 分，总分 3-30"},
}

ANCHORS = {
    "F1": {1: "不在意有没有反馈", 5: "发完会反复看有没有人回应", 10: "被忽视感压垮，反复删改"},
    "F2": {1: "认可这个社区的价值", 5: "觉得氛围变差、内容没营养", 10: "觉得这里毫无意义、彻底失望"},
    "F3": {1: "使用起来很自如", 5: "消息太多，常被打断", 10: "边界被反复侵犯，疲于应对"},
    "F4": {1: "没想过离开", 5: "想少上一点、屏蔽一些人", 10: "想注销、卸载、彻底断联"},
    "F5": {1: "愿意表达和互动", 5: "不太想说，减少参与", 10: "完全封闭，拒绝交流"},

    "D1": {1: "精力如常，能投入", 3: "提不起劲，做事很费力", 5: "几乎没力气，起身都难"},
    "D2": {1: "情绪平稳", 3: "闷闷的，开心不起来", 5: "绝望，觉得没有意义"},
    "D3": {1: "睡眠规律，白天有精神", 3: "入睡难或早醒，白天困", 5: "几乎整夜睡不着或长时间嗜睡"},
    "D4": {1: "不容易累", 3: "容易累，想躺着", 5: "持续疲惫，什么都动不了"},
    "D5": {1: "食欲正常", 3: "食量明显增减", 5: "几乎不吃或暴食，体重快速变化"},
    "D6": {1: "认可自己", 3: "觉得自己总让人失望", 5: "觉得自己是废物、不配"},
    "D7": {1: "能专注", 3: "看不进去，容易走神", 5: "完全无法集中，读一段话都难"},
    "D8": {1: "活动如常", 3: "坐立不安或动作变慢", 5: "明显迟滞或激越，难以自控"},
    "D9": {1: "没有相关念头", 3: "有过念头，但没有计划", 5: "有具体计划/准备或已实施"},

    "A1": {1: "不紧张", 3: "时常紧张、心慌", 5: "持续紧绷，伴躯体不适"},
    "A2": {1: "担心有度", 3: "常担心还没发生的事", 5: "控制不住地担忧"},
    "A3": {1: "不纠结琐事", 3: "反复想琐事、患得患失", 5: "反复检查，停不下来"},
    "A4": {1: "能放松下来", 3: "休息也放松不下来", 5: "一直绷着，无法休息"},
    "A5": {1: "坐得住", 3: "坐不住，总想动", 5: "明显躯体不安，来回走动"},
    "A6": {1: "情绪平稳", 3: "容易烦躁", 5: "频繁发火，难以自控"},
    "A7": {1: "没有莫名的恐惧", 3: "偶尔莫名害怕", 5: "强烈恐惧/被害感"},

    "SS1": {1: "完全没人真正懂我", 5: "有几个能说上话的人", 10: "被稳稳接住、被理解"},
    "SS2": {1: "现实中无人可求助", 5: "有家人朋友，但联系很少", 10: "有稳定、可求助的关系网"},
    "SS3": {1: "从不求助，也不接受帮助", 5: "偶尔会找人帮忙", 10: "会主动使用身边的支持"},
}

STRATEGY = {
    "F1": "S4_anxiety_feedback", "F2": "S4_lowvalue", "F3": "S4_invasion",
    "F4": "S4_escape", "F5": "S4_avoidance",
    "D1": "S5_dep_energy", "D2": "S5_dep_mood", "D3": "S5_dep_sleep",
    "D4": "S5_dep_fatigue", "D5": "S5_dep_appetite", "D6": "S5_dep_selfworth",
    "D7": "S5_dep_attention", "D8": "S5_dep_motor", "D9": "S5_dep_suicide",
    "A1": "S5_anx_tension", "A2": "S5_anx_worry", "A3": "S5_anx_trivia",
    "A4": "S5_anx_relax", "A5": "S5_anx_restless", "A6": "S5_anx_irritable",
    "A7": "S5_anx_fear",
    "SS1": "S3_sssub_accept", "SS2": "S3_sssub_only", "SS3": "S3_ssuti_model",
}

EXPECTED_COUNTS = {"F": 5, "D": 9, "A": 7, "SS": 3}


def build(source: Path) -> dict:
    groups: dict = {}
    for key, (fname, list_key, name_k, exp_k, emo_k, resp_k) in SOURCES.items():
        path = source / fname
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        factors = raw[list_key]
        if len(factors) != EXPECTED_COUNTS[key]:
            raise SystemExit(f"{fname}: 期望 {EXPECTED_COUNTS[key]} 个维度，实际 {len(factors)}")

        items = []
        for i, fac in enumerate(factors, 1):
            ident = f"{key}{i}"
            lo, hi = GROUPS[key]["scale"]
            anchors = ANCHORS[ident]
            items.append({
                "id": ident,
                "index": i,
                "name": fac[name_k],
                "explain": fac[exp_k],
                "user_emotion": fac[emo_k],
                "response": fac[resp_k],
                "strategy_tag": STRATEGY[ident],
                "anchors": [{"score": s, "behaviour": anchors[s]} for s in sorted(anchors)],
                "range": [lo, hi],
            })

        groups[key] = {
            "id": key,
            "label": GROUPS[key]["label"],
            "label_en": GROUPS[key]["en"],
            "scale": GROUPS[key]["scale"],
            "direction": GROUPS[key]["direction"],
            "source_scale": GROUPS[key]["source_scale"],
            "count": len(items),
            "source_file": fname,
            "source_evaluation": raw.get(EXPLAIN_KEYS[key], {}),
            "factors": items,
        }

    return {
        "schema": "v1",
        "generated_by": "scripts/build_dimensions.py",
        "note": "字段文本与源 JSON 逐字一致；源文件的 2 处已知问题已于 2026-10-27 修正，修改前后原文见 corrections。",
        "groups": groups,
        "corrections": [
            {
                "id": "FIX-01",
                "date": "2026-10-27",
                "where": "抑郁评价维度.json / D1 精力下降 / UserEmotion",
                "before": "疏离集体、失落沮丧、情绪低落、被忽视感增强、不被认同、认为自身的言论和行为缺乏正向反馈、过度纠结个人的印象管理。",
                "after": "精力与耐力下降、做事费力、容易疲惫、难以启动任务、想躺着不动、对原本感兴趣的事也提不起劲。",
                "reason": "原文本与倦怠组 F1 焦虑感完全相同，属复制串行；改为描述精力下降本身的表述。",
            },
            {
                "id": "FIX-02",
                "date": "2026-10-27",
                "where": "社会支持评价 .json / ASocialSupportGradeResults",
                "before": "分数越低代表该维度的状态越强烈/越差……（低于前40%或得分≥7分）",
                "after": "分数越高代表该维度的支持越充分/越好……（低于前40%，即主观支持≤4分；或得分≤3分）",
                "reason": "原文本与 1-10 分制及触发条件方向相反，会导致『支持越好越报警』；已统一为越高越好。",
            },
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default=str(DEFAULT_SOURCE))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    data = build(Path(args.source_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    total = sum(g["count"] for g in data["groups"].values())
    print(f"wrote {total} dimensions -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
