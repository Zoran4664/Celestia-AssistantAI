"""sky_events.py — 天象计算（纯 Python，无第三方依赖）

需求（用户反馈）：模型回答「#tothemoon 查询天象」时**全部写「未取得」** —— 因为
celescan.net 是 JS 单页应用（抓下来只有「正在计算…」占位），模型读不到正文。
本模块把能**确定性计算**的天象在本地算出来（月相 / 蛾眉月 / 超级月亮 / 亮星合月 /
日月食初判 / 流星雨 / 黄道光 / 银河之眼），只有真正需要实时观测数据的项目
（彗星、精确到分的交食时刻）才标注「需核对」并给出处。

算法：低精度日月位置（Astronomical Almanac 简化级数）
  · 黄经精度 ≈ 0.1°（对应月相时刻误差 ≲ 1 小时，足够给「日期 + 大致时段」）
  · 月地距离精度 ≈ 数百 km（足够判断「近地点满月 / 超级月亮」）
所有结果在输出里按「实测 / 估算」标注，不冒充实测值。

对外：``build_event_report(site, days)`` → ``render_event_text(report)``（给模型的纯文本）
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

_J2000_JD = 2451545.0
_OBLIQ = 23.4392911          # 黄赤交角（度，J2000）
_SYNODIC = 29.530588853      # 朔望月（天）
_MEAN_DIST = 385000.6        # 月地平均距离（km）

#: 逐月可算的天象参考：亮星（J2000 赤经赤纬，度）
BRIGHT_STARS: Tuple[Tuple[str, str, float, float], ...] = (
    # 中文名, 通用名, RA(度), Dec(度)
    ("角宿一", "Spica", 201.2983, -11.1614),
    ("毕宿五", "Aldebaran", 68.9801, 16.5093),
    ("心宿二", "Antares", 247.3519, -26.4320),
    ("轩辕十四", "Regulus", 152.0928, 11.9672),
)

#: 主要流星雨极大（日期为每年大致日期，±1 天；ZHR 供参考）
METEOR_SHOWERS: Tuple[Tuple[int, int, str, int, str], ...] = (
    # 月, 日, 名称, ZHR, 备注
    (1, 3, "象限仪座流星雨", 110, "极大短暂，辐射点偏北，后半夜更佳"),
    (4, 22, "天琴座流星雨", 18, "中等，偶有爆发"),
    (5, 6, "宝瓶座η流星雨", 50, "黎明前，南半球更佳"),
    (8, 12, "英仙座流星雨", 100, "夏季最稳，今年需看月相干扰"),
    (10, 21, "猎户座流星雨", 20, "速度慢，适合拍照"),
    (11, 5, "金牛座南流星雨", 5, "火流星比例高"),
    (11, 17, "狮子座流星雨", 15, "平常年份偏弱，有爆发历史"),
    (12, 14, "双子座流星雨", 150, "全年最稳，辐射点天黑后即升起"),
    (12, 22, "小熊座流星雨", 10, "后半夜，辐射点偏北"),
)

#: 需要外部核对的出处（程序算不了 / 不保证精度）
CHECK_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("天象快搜（按地区/日期）", "https://celescan.net/"),
    ("交食年历（NASA）", "https://eclipse.gsfc.nasa.gov/eclipse/eclipse.html"),
    ("近期可观测彗星", "https://starwalk.space/zh-Hans/news/upcoming-comets"),
    ("晴天钟（观测条件）", "https://github.com/Yeqzids/7timer-issues/wiki/Wiki"),
)


# ---------------------------------------------------------------- 基础工具
def _rad(d: float) -> float:
    return math.radians(d)


def _deg(r: float) -> float:
    return math.degrees(r)


def _norm360(x: float) -> float:
    return x % 360.0


def _julian_day(dt: datetime) -> float:
    """UTC datetime → 儒略日（标准公式，精度足够）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d + b - 1524.5)


def _days_since_j2000(dt: datetime) -> float:
    return _julian_day(dt) - _J2000_JD


def sun_longitude(dt: datetime) -> float:
    """太阳视黄经（度）。"""
    d = _days_since_j2000(dt)
    L = _norm360(280.460 + 0.9856474 * d)
    g = _rad(_norm360(357.528 + 0.9856003 * d))
    return _norm360(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))


def moon_position(dt: datetime) -> Tuple[float, float, float]:
    """月亮视位置 → ``(黄经°, 黄纬°, 距离km)``（Almanac 简化级数）。"""
    d = _days_since_j2000(dt)
    Lp = _rad(_norm360(218.316 + 13.176396 * d))     # 平黄经
    D = _rad(_norm360(297.850 + 12.190749 * d))      # 平距角
    M = _rad(_norm360(357.529 + 0.9856003 * d))      # 太阳平近点角
    Mp = _rad(_norm360(134.963 + 13.064993 * d))     # 月亮平近点角
    F = _rad(_norm360(93.272 + 13.229350 * d))       # 升交点角距
    lam = _norm360(_deg(Lp)
                   + 6.289 * math.sin(Mp)
                   - 1.274 * math.sin(Mp - 2 * D)
                   + 0.658 * math.sin(2 * D)
                   + 0.214 * math.sin(2 * Mp)
                   - 0.186 * math.sin(M)
                   - 0.114 * math.sin(2 * F))
    bet = (5.128 * math.sin(F)
           + 0.281 * math.sin(Mp + F)
           - 0.281 * math.sin(F - Mp)
           - 0.173 * math.sin(F - 2 * D))
    dist = (_MEAN_DIST
            - 20905.4 * math.cos(Mp)
            - 3699.1 * math.cos(2 * D)
            - 2955.9 * math.cos(2 * D - Mp)
            - 569.9 * math.cos(2 * Mp))
    return lam, bet, dist


def elongation(dt: datetime) -> float:
    """月日角距（度）：0=朔，90=上弦，180=望，270=下弦。"""
    return _norm360(moon_position(dt)[0] - sun_longitude(dt))


def illuminated_fraction(dt: datetime) -> float:
    """月面照度比例 0~1。"""
    return (1 - math.cos(_rad(elongation(dt)))) / 2.0


def phase_name(elong: float) -> str:
    """按角距给中文月相名。

    阈值按「照度占比」习惯定（实测锚点：角距 110° / 照度 67% 应叫**盈凸月**，
    所以上弦只保留 75°~105° 这一段，不能把 112° 之前都算上弦）。
    """
    if elong < 15.0 or elong >= 345.0:
        return "新月"
    if elong < 75.0:
        return "蛾眉月"
    if elong < 105.0:
        return "上弦月"
    if elong < 165.0:
        return "盈凸月"
    if elong < 195.0:
        return "满月"
    if elong < 255.0:
        return "亏凸月"
    if elong < 285.0:
        return "下弦月"
    return "残月"


# ---------------------------------------------------------------- 交点时刻
def _phase_series(t0: datetime, t1: datetime,
                  step_h: float = 1.0) -> Tuple[List[datetime], List[float]]:
    """窗口内的**连续**月相角（不取模，单调递增 ≈12.2°/天）。

    坑：直接用「与目标角的带符号差」判定过零会在**目标角对面（180°）处出现假穿越**
    —— 旧实现因此把满月当成新月（两者算出相差 22 分钟）。这里先把角距展开成连续
    递增序列，再找 `target + 360k` 的穿越点。
    """
    times: List[datetime] = [t0]
    phases: List[float] = [elongation(t0)]
    cur = t0 + timedelta(hours=step_h)
    while cur <= t1:
        e = elongation(cur)
        delta = ((e - phases[-1] + 180.0) % 360.0) - 180.0
        phases.append(phases[-1] + delta)
        times.append(cur)
        cur += timedelta(hours=step_h)
    return times, phases


def _crossings(target: float, t0: datetime, t1: datetime,
               step_h: float = 1.0) -> List[datetime]:
    """找相位角穿过 target（含 +360k）的所有时刻。"""
    times, phases = _phase_series(t0, t1, step_h)
    out: List[datetime] = []
    lo, hi = phases[0], phases[-1]
    k = math.ceil((lo - target) / 360.0)
    while target + k * 360.0 <= hi:
        want = target + k * 360.0
        for i in range(1, len(times)):
            if (phases[i - 1] - want) * (phases[i] - want) <= 0 \
                    and phases[i] != phases[i - 1]:
                frac = ((want - phases[i - 1])
                        / (phases[i] - phases[i - 1]))
                out.append(times[i - 1] + (times[i] - times[i - 1]) * frac)
                break
        k += 1
    return out


def syzygies(start: datetime, days: int = 30,
             tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """窗口内的朔/上弦/望/下弦时刻（本地时间，误差 ≲ 半小时）。"""
    tz = timezone(timedelta(hours=tz_hours))
    end = start + timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for target, name in ((0.0, "新月"), (90.0, "上弦月"),
                         (180.0, "满月"), (270.0, "下弦月")):
        for t in _crossings(target, start, end):
            lam, bet, dist = moon_position(t)
            out.append({"name": name, "time": t.astimezone(tz),
                        "distance_km": dist, "lat": bet})
    out.sort(key=lambda x: x["time"])
    return out


# ---------------------------------------------------------------- 各类天象
def super_moon(start: datetime, days: int = 30,
               tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """窗口内的满月 + 是否「超级月亮」（近地点附近满月，≤ 360000 km 视为超级）。"""
    out: List[Dict[str, Any]] = []
    for s in syzygies(start, days, tz_hours):
        if s["name"] != "满月":
            continue
        d = s["distance_km"]
        out.append({
            "time": s["time"],
            "distance_km": d,
            "is_super": d <= 360000.0,
            "note": ("近地点附近满月（视直径偏大，俗称超级月亮）"
                     if d <= 360000.0 else "普通满月"),
        })
    return out


def crescent_earthshine(start: datetime, days: int = 30,
                        tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """「新月抱旧月」（地照）：新月后日落后西方低空，角距 12°~28° 时最容易看见。"""
    tz = timezone(timedelta(hours=tz_hours))
    out: List[Dict[str, Any]] = []
    for s in syzygies(start, days, tz_hours):
        if s["name"] != "新月":
            continue
        for k in (1, 2, 3):
            day = (s["time"].astimezone(tz) + timedelta(days=k)).replace(
                hour=18, minute=30, second=0)
            e = elongation(day.astimezone(timezone.utc))
            if 10.0 <= e <= 30.0:
                out.append({"date": day.date().isoformat(),
                            "elongation": round(e, 1),
                            "illumination": round(illuminated_fraction(
                                day.astimezone(timezone.utc)) * 100),
                            "note": "日落后西方低空，地照（旧月）最易见"})
    return out


def star_conjunctions(start: datetime, days: int = 30,
                      tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """窗口内月亮与四颗亮星的合月（黄经相同最近的一天 + 角距）。"""
    tz = timezone(timedelta(hours=tz_hours))
    eps = _rad(_OBLIQ)
    out: List[Dict[str, Any]] = []
    for name, en, ra, dec in BRIGHT_STARS:
        ar, dr = _rad(ra), _rad(dec)
        lam_s = _norm360(_deg(math.atan2(
            math.sin(ar) * math.cos(eps) + math.tan(dr) * math.sin(eps),
            math.cos(ar))))
        bet_s = _deg(math.asin(
            math.sin(dr) * math.cos(eps) - math.cos(dr) * math.sin(eps) * math.sin(ar)))
        best: Optional[Tuple[float, datetime, float]] = None
        cur = start
        while cur < start + timedelta(days=days):
            lam_m, bet_m, _ = moon_position(cur)
            sep = math.hypot(
                ((lam_m - lam_s + 180) % 360) - 180, bet_m - bet_s)
            if best is None or sep < best[0]:
                best = (sep, cur, lam_m)
            cur += timedelta(hours=3)
        if best is None or best[0] > 12.0:      # 一个月内最近也超过 12° 就不报
            continue
        out.append({
            "star": name, "star_en": en,
            "date": best[1].astimezone(tz).date().isoformat(),
            "separation_deg": round(best[0], 1),
            "note": ("角距 < 5°，肉眼同框明显" if best[0] < 5
                     else "角距偏大，需双筒/广角"),
        })
    return out


def eclipse_hints(start: datetime, days: int = 30,
                  tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """日月食**初判**：朔/望时月球黄纬 |β| 小于阈值才可能交食（需以年历核对）。"""
    tz = timezone(timedelta(hours=tz_hours))
    out: List[Dict[str, Any]] = []
    for s in syzygies(start, days, tz_hours):
        if s["name"] == "新月":
            kind, thr = "日食", 1.6
        elif s["name"] == "满月":
            kind, thr = "月食", 1.2
        else:
            continue
        bet = abs(s["lat"])
        out.append({
            "kind": kind,
            "date": s["time"].astimezone(tz).date().isoformat(),
            "moon_lat_deg": round(bet, 2),
            "possible": bet <= thr,
            "note": ("可能交食（初判，请以 NASA / 紫台年历核对，"
                     "本程序不保证见食地区与时刻）" if bet <= thr
                     else f"黄纬 {bet:.1f}° 超出阈值，该次不交食"),
        })
    return out


def meteor_showers(start: datetime, days: int = 30,
                   tz_hours: float = 8.0) -> List[Dict[str, Any]]:
    """窗口内的主要流星雨极大（年表，±1 天）。"""
    tz = timezone(timedelta(hours=tz_hours))
    day0 = start.astimezone(tz).date()
    day1 = (start + timedelta(days=days)).astimezone(tz).date()
    out: List[Dict[str, Any]] = []
    for m, d, name, zhr, note in METEOR_SHOWERS:
        # 跨年：当年窗口内找不到就看次年（1 月的象限仪座常需这样）
        for year in (day0.year, day0.year + 1):
            try:
                peak = datetime(year, m, d).date()
            except ValueError:
                continue
            if day0 <= peak <= day1:
                out.append({"name": name, "peak": peak.isoformat(),
                            "zhr": zhr, "note": note})
                break
    return out


def seasonal_windows(start: datetime) -> List[Dict[str, Any]]:
    """黄道光 / 银河之眼的季节窗口（按当月给，属常识性规律，非精确预报）。"""
    m = start.month
    zodiac = ("春分前后（2-4 月）：**日落后**西方低空；"
              "秋分前后（8-10 月）：**日出前**东方低空。"
              f"当前 {m} 月" + ("在窗口内" if m in (2, 3, 4, 8, 9, 10) else "不在主要窗口")
              + "；需无月（新月前后）与无光害。")
    galaxy = ("「银河之眼」指夏夜天鹅座—北美洲星云一带的银河核心区"
              "（天津四附近），**6-8 月**午夜前后南下最高；"
              f"当前 {m} 月" + ("仍在可见季" if m in (5, 6, 7, 8, 9) else "银河核心段已偏西/偏低")
              + "（9 月后逐渐移到西天，观测窗口变短）。")
    return [{"name": "黄道光", "window": zodiac, "kind": "季节规律"},
            {"name": "银河之眼", "window": galaxy, "kind": "季节规律"}]


# ---------------------------------------------------------------- 汇总
def build_event_report(site: Dict[str, Any], days: int = 30,
                       start: Optional[datetime] = None,
                       tz_hours: float = 8.0) -> Dict[str, Any]:
    """算出窗口内的天象清单（供程序卡片与模型上下文共用）。"""
    tz = timezone(timedelta(hours=tz_hours))
    now = (start or datetime.now(timezone.utc)).astimezone(timezone.utc)
    syz = syzygies(now, days, tz_hours)
    return {
        "site": site,
        "days": days,
        "start": now.astimezone(tz),
        "tz_hours": tz_hours,
        "moon_now": {
            "phase": phase_name(elongation(now)),
            "illumination_pct": round(illuminated_fraction(now) * 100),
        },
        "phases": syz,
        "super_moon": super_moon(now, days, tz_hours),
        "earthshine": crescent_earthshine(now, days, tz_hours),
        "conjunctions": star_conjunctions(now, days, tz_hours),
        "eclipses": eclipse_hints(now, days, tz_hours),
        "meteors": meteor_showers(now, days, tz_hours),
        "seasonal": seasonal_windows(now.astimezone(tz)),
        "need_check": ["彗星（实时亮度与位置）", "交食的见食地区与精确时刻"],
        "sources": [{"name": n, "url": u} for n, u in CHECK_SOURCES],
    }


def render_event_text(report: Dict[str, Any]) -> str:
    """天象清单 → 纯文本（注入给模型；也用于卡片）。"""
    tz = report.get("tz_hours", 8.0)
    site = report.get("site") or {}
    lines: List[str] = []
    lines.append(f"【天象数据（程序计算，{report.get('days', 30)} 天内）】"
                 f"地点：{site.get('name') or '本机位置'}"
                 f"（{site.get('lat')},{site.get('lon')}），时间按 UTC+{tz:g}。")
    mn = report.get("moon_now") or {}
    lines.append(f"· 当前月相：{mn.get('phase')}，照度 {mn.get('illumination_pct')}%")
    if report.get("phases"):
        lines.append("· 朔望（时刻为估算，误差 ≲1 小时）：")
        for s in report["phases"]:
            extra = ""
            if s["name"] == "满月":
                extra = f"，月地距离 {s['distance_km']/10000:.2f} 万 km"
            lines.append(f"    - {s['name']}：{s['time']:%m-%d %H:%M}{extra}")
    else:
        lines.append("· 朔望：未计算出（窗口太短）")
    for s in report.get("super_moon") or []:
        if s["is_super"]:
            lines.append(f"· 超级月亮：{s['time']:%m-%d} 的满月"
                         f"（{s['distance_km']/10000:.2f} 万 km）——{s['note']}")
    for c in report.get("earthshine") or []:
        lines.append(f"· 新月抱旧月（地照）：{c['date']} 日落后西方低空，"
                     f"角距 {c['elongation']}°，照度 {c['illumination']}%")
    for c in report.get("conjunctions") or []:
        lines.append(f"· 合月：{c['date']} 月亮与{c['star']}（{c['star_en']}）"
                     f"角距 {c['separation_deg']}° —— {c['note']}")
    for e in report.get("eclipses") or []:
        flag = "可能" if e["possible"] else "不交食"
        lines.append(f"· {e['kind']}初判：{e['date']} → {flag}（月球黄纬 "
                     f"{e['moon_lat_deg']}°）；{e['note']}")
    for m in report.get("meteors") or []:
        lines.append(f"· 流星雨：{m['name']} 极大 {m['peak']}（ZHR≈{m['zhr']}，"
                     f"{m['note']}，±1 天）")
    for s in report.get("seasonal") or []:
        lines.append(f"· {s['name']}：{s['window']}")
    # 措辞固定为「需核对」：SKILL.md 让模型只在看到这个词时才写「需核对」
    lines.append("· 以下**需核对**（程序不编造，请给出处让用户自己核对）：" + "、".join(
        report.get("need_check") or []))
    lines.append("· 出处：" + "；".join(
        f"{s['name']} {s['url']}" for s in (report.get("sources") or [])))
    return "\n".join(lines)
