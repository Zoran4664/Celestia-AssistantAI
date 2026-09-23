"""sky_service.py — 观星 / 天象条件（7timer 晴天钟 + 和风天文）

需求（用户反馈）：`#tothemoon 查询天象` **不该甩一个「查询地址/链接」让用户自己去看**，
应当像 `\\@weather` 一样：**直接读本机 IP → 用 IP 归属地的坐标**把观测条件查出来并给结论。

定位复用 `weather_service`（本机 IP → 归属地 → 静态城市表 → 经纬度；用户明确写了
城市才用该城市）。观测条件来自 7timer（`https://www.7timer.info/bin/astro.php`）的
JSON 接口；月相 / 日出日落尽力而为地用和风天文接口补上（没配 KEY 就跳过）。

对外：
- ``resolve_sky_site(query)`` → 观测点（本机 IP 定位优先）
- ``fetch_sky(lat, lon, product, tzshift)`` → 逐 3 小时序列（含本地时间）
- ``sun_times(lat, lon, tzshift)`` → 日出/日落/晨光始/昏影终/上中天/昼长（**本地推算**）
- ``fetch_astro_image(lat, lon, tzshift)`` → 下载晴天钟 PNG 到本地缓存（供卡片内嵌）
- ``summarize(rows, hours)`` → 结论（适合/勉强/不适合）+ 最佳时段
- ``build_sky_bundle(query, client)`` → 定位 + 7timer + 太阳时刻 + 晴天钟图 +（可选）月相
- ``render_sky_html(bundle, accent)`` → HTML5 卡片

需求（用户 2026-09-22）：「不要把 GitHub 链接当答案，而是**学习那个 wiki 的内容**
并把**晨光始 / 昏影终、天文晴天钟、日升日落时间**直接显示出来」。
核查结论：7timer 官方 wiki（Yeqzids/7timer-issues）**只**说明了 ASTRO 图 7 行要素与
API 数值图例，**完全没有**日出日落 / 晨昏蒙影的记载 —— 所以原先「读图说明（含晨光始 /
昏影终）」的链接是**误导**。现在改为：太阳时刻本地推算、晴天钟图直接内嵌进卡片。
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import weather_service as ws

logger = logging.getLogger("AI_DeskMate.Sky")

SEVEN_TIMER = "https://www.7timer.info/bin/{product}.php"
_UA = "Celestia-AssistantAI/1.0"
DEFAULT_TZSHIFT = 8          # 北京时间；其它时区可在配置里改（见 SKY_TZSHIFT_KEY）
SKY_TZSHIFT_KEY = "sky_tzshift"

#: 7timer 官方图例：云量 1~9 → 覆盖百分比
CLOUD_LEGEND = {1: "0-6%", 2: "6-19%", 3: "19-31%", 4: "31-44%", 5: "44-56%",
                6: "56-69%", 7: "69-81%", 8: "81-94%", 9: "94-100%"}
#: 视宁度（seeing）1~8，单位角秒
SEEING_LEGEND = {1: "<0.5″", 2: "0.5-0.75″", 3: "0.75-1″", 4: "1-1.25″",
                 5: "1.25-1.5″", 6: "1.5-2″", 7: "2-2.5″", 8: ">2.5″"}
#: 大气透明度（transparency）1~8，数值越小越通透
TRANSP_LEGEND = {1: "<0.3", 2: "0.3-0.4", 3: "0.4-0.5", 4: "0.5-0.6",
                 5: "0.6-0.7", 6: "0.7-0.85", 7: "0.85-1.0", 8: ">1.0"}


def _http_json(url: str, timeout: float = 15.0) -> Any:
    """GET JSON（7timer 恒返回 gzip，这里按头/魔数统一解压）。失败抛可读异常。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept-Encoding": "gzip, identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
            enc = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络不可达 {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"请求失败：{exc}") from exc
    if enc == "gzip" or blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except Exception:  # noqa: BLE001
            pass
    try:
        return json.loads(blob.decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("7timer 返回的不是 JSON（稍后重试）") from exc


# ================================================================ 定位
def resolve_sky_site(query: str = "", timeout: float = 8.0) -> Dict[str, Any]:
    """定位观测点。

    需求：**默认本机 IP 定位**（用户反馈：不要输出「查询地址」让用户自己去看）。
      1. 没写地名，或写的是「本地/本机/我这里…」（`is_local_intent`）→ 本机 IP 归属地；
      2. 明确写了城市 → 内置静态城市表（`weather_service.local_city_lookup`）；
      3. IP 只给到城市名、没给经纬度 → 用城市表把经纬度补齐。

    返回 ``{"name","adm","lat","lon","source","ip","query"}``，
    ``source ∈ {"ip", "table"}``（卡片副标题据此标注定位来源）。
    """
    q = ws.parse_city(query or "")
    if q and not ws.is_local_intent(q):
        hits = ws.local_city_lookup(q) or []
        if hits:
            h = hits[0]
            return {"name": str(h.get("name") or q),
                    "adm": " ".join(x for x in (h.get("adm2"), h.get("adm1")) if x),
                    "lat": str(h.get("lat") or ""), "lon": str(h.get("lon") or ""),
                    "source": "table", "ip": "", "query": q}
        # 城市表没收录：当作没写地名，退回本机 IP（不给用户一个「未收录」的死胡同）
        logger.info("城市表未收录 %r，观星定位退回本机 IP", q)

    info = ws.ip_info(timeout=timeout)
    lat, lon = str(info.get("lat") or ""), str(info.get("lon") or "")
    name, adm = str(info.get("city") or ""), str(info.get("region") or "")
    if (not lat or not lon) and name:
        hits = ws.local_city_lookup(name) or []
        if hits:
            lat = lat or str(hits[0].get("lat") or "")
            lon = lon or str(hits[0].get("lon") or "")
    return {"name": name or "本机 IP", "adm": adm, "lat": lat, "lon": lon,
            "source": "ip", "ip": str(info.get("ip") or ""), "query": ""}


# ================================================================ 7timer
def tzshift() -> int:
    """对外用的时区偏移（内部 ``_tzshift`` 的公开别名，供 sky_events 等复用）。"""
    return _tzshift()


def _tzshift() -> int:
    """时区偏移（小时）：配置 `api.sky_tzshift` 优先，默认 8（北京时间）。"""
    try:
        from config_loader import ConfigLoader
        return int(ConfigLoader.instance().get("api", SKY_TZSHIFT_KEY,
                                              default=DEFAULT_TZSHIFT))
    except Exception:  # noqa: BLE001
        return DEFAULT_TZSHIFT


def _parse_init(init: str, tzshift: int) -> Optional[datetime]:
    """7timer 的 ``init`` 是模式起报时刻（UTC，形如 2026092012）→ 换成**本地时间**。"""
    t = str(init or "").strip()
    if len(t) != 10 or not t.isdigit():
        return None
    try:
        return datetime.strptime(t, "%Y%m%d%H") + timedelta(hours=tzshift)
    except Exception:  # noqa: BLE001
        return None


def fetch_sky(lat: str, lon: str, product: str = "astro",
              tzshift: Optional[int] = None, timeout: float = 15.0) -> Dict[str, Any]:
    """取 7timer JSON：``{"init","rows":[{time,cloud,seeing,transp,wind,...}]}``。

    每个 ``timepoint`` 是**相对起报的小时数**（3 小时一档），这里换算成当地时间字符串。
    """
    tz = DEFAULT_TZSHIFT if tzshift is None else int(tzshift)
    url = (f"{SEVEN_TIMER.format(product=product)}?"
           + urllib.parse.urlencode({
               "lon": str(lon), "lat": str(lat), "ac": 0, "lang": "zh-CN",
               "unit": "metric", "tzshift": tz, "output": "json"}))
    data = _http_json(url, timeout=timeout)
    if not isinstance(data, dict):
        raise RuntimeError("7timer 返回结构异常")
    rows: List[Dict[str, Any]] = []
    t0 = _parse_init(str(data.get("init") or ""), tz)
    for s in (data.get("dataseries") or []):
        if not isinstance(s, dict):
            continue
        try:
            tp = int(s.get("timepoint") or 0)
        except Exception:  # noqa: BLE001
            tp = 0
        wind = s.get("wind10m") if isinstance(s.get("wind10m"), dict) else {}
        rows.append({
            "hours": tp,
            "time": (t0 + timedelta(hours=tp)).strftime("%m-%d %H:%M") if t0 else f"起报+{tp}h",
            "clock": (t0 + timedelta(hours=tp)).strftime("%H:%M") if t0 else f"+{tp}h",
            "cloud": int(s.get("cloudcover") or 0),
            "seeing": int(s.get("seeing") or 0),
            "transp": int(s.get("transparency") or 0),
            "wind": int(wind.get("speed") or 0),
            "wind_dir": str(wind.get("direction") or ""),
            "temp": s.get("temp2m"),
            "rh": s.get("rh2m"),
        })
    if not rows:
        raise RuntimeError("7timer 没有返回预报数据")
    return {"product": product, "init": str(data.get("init") or ""),
            "tzshift": tz, "lat": str(lat), "lon": str(lon), "rows": rows,
            "image_url": (f"{SEVEN_TIMER.format(product=product)}?"
                          + urllib.parse.urlencode({
                              "lon": str(lon), "lat": str(lat), "ac": 0,
                              "lang": "zh-CN", "unit": "metric", "tzshift": tz})),
            "url": url}


def _jd_utc(dt: datetime) -> float:
    """UTC 时刻 → 儒略日（Julian Day）。"""
    y, m = dt.year, dt.month
    d = (dt.day + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0)
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (int(365.25 * (y + 4716)) + int(30.6001 * (m + 1))
            + d + b - 1524.5)


def _fmt_min(utc_min: Optional[float], tzshift: int) -> str:
    """UTC 分钟数 → 当地 ``HH:MM``（跨日自动回绕）；None → 空串。"""
    if utc_min is None:
        return ""
    total = int(round(utc_min + tzshift * 60)) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def sun_times(lat: Any, lon: Any, tzshift: Optional[int] = None,
              date: Optional[datetime] = None) -> Dict[str, str]:
    """太阳时刻**本地推算**：日出 / 日落 / 晨光始 / 昏影终 / 上中天 / 昼长。

    需求（用户 2026-09-22）：「日升日落时间」「晨光始 / 昏影终」必须**直接显示**，
    不能只给链接 —— 而 7timer 的 JSON 里根本没有这些字段（其官方 wiki 也没有），
    和风天文接口又需要额外 KEY。所以这里用 NOAA 简化算法自己算，**零依赖、必有值**。

    口径（与天文年历一致）：
      * 日出 / 日落：太阳**上边缘**与地平线相切 → 天顶距 90.833°（含大气折射与日面半径）；
      * 晨光始 / 昏影终：**民用晨昏蒙影**（太阳中心高度 −6°）→ 天顶距 96°；
      * 上中天：真太阳时正午（不是升起/落下的中点）；
      * 精度：分级别（NOAA 简化式，中低纬误差 ≲1 分钟）。

    返回 ``{"sunrise","sunset","civil_dawn","civil_dusk","transit","day_length","polar"}``；
    极端纬度出现极昼 / 极夜时 ``sunrise``/``sunset`` 为空串、``polar`` 为「极昼」/「极夜」。
    """
    try:
        la = float(lat)
        lo = float(lon)
    except Exception:  # noqa: BLE001
        return {}
    tz = DEFAULT_TZSHIFT if tzshift is None else int(tzshift)
    day = date or datetime.now()
    # NOAA 的简化级数里 γ 以「**年内日序**」为自变量（不是 J2000 天数）：
    #   γ = 2π/365 · (day_of_year − 1)
    # 用错基准会让赤纬不随日期变化（夏至与冬至算出同一组时刻）。
    # 注意：2π/365·(doy−1) **本身就是弧度**，不能再套 math.radians()——
    # 套了会把 γ 压成 ~0.05 rad，赤纬恒等于冬值（夏至也算出 9 小时白昼）。
    doy = int(day.timetuple().tm_yday)
    g = 2.0 * math.pi / 365.0 * (doy - 1)
    # 均时差（分钟）
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g)
                       - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g)
                       - 0.040849 * math.sin(2 * g))
    # 太阳赤纬（弧度）
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la_r = math.radians(la)

    def _event(zenith_deg: float, rising: bool) -> Optional[float]:
        try:
            cos_ha = ((math.cos(math.radians(zenith_deg))
                       - math.sin(la_r) * math.sin(decl))
                      / (math.cos(la_r) * math.cos(decl)))
        except ZeroDivisionError:
            return None
        if cos_ha > 1.0 or cos_ha < -1.0:
            return None              # 该事件当日不发生（极昼 / 极夜 / 高纬）
        ha = math.degrees(math.acos(cos_ha))
        # NOAA 口径：日出 = 720 − 4·(经度 + H) − 均时差；日落取 −H
        if not rising:
            ha = -ha
        return 720.0 - 4.0 * (lo + ha) - eqtime      # UTC 分钟

    out: Dict[str, str] = {
        "sunrise": _fmt_min(_event(90.833, True), tz),
        "sunset": _fmt_min(_event(90.833, False), tz),
        "civil_dawn": _fmt_min(_event(96.0, True), tz),
        "civil_dusk": _fmt_min(_event(96.0, False), tz),
        "transit": _fmt_min(720.0 - 4.0 * lo - eqtime, tz),
        "day_length": "",
        "polar": "",
    }
    # 昼长（有日出日落才算；跨日回绕）
    if out["sunrise"] and out["sunset"]:
        r = int(out["sunrise"][:2]) * 60 + int(out["sunrise"][3:5])
        s = int(out["sunset"][:2]) * 60 + int(out["sunset"][3:5])
        mins = (s - r) % 1440
        out["day_length"] = f"{mins // 60} 小时 {mins % 60:02d} 分"
    elif not out["sunrise"] and not out["sunset"]:
        noon_alt = 90.0 - abs(la - math.degrees(decl))
        out["polar"] = "极昼" if noon_alt > 0 else "极夜"
    return out


# ================================================================ 晴天钟图（内嵌）
def _sky_image_dir() -> Path:
    """晴天钟图缓存目录（Qt 富文本只认本地 ``file:///`` 图片，必须先落盘）。"""
    try:
        from config_loader import project_root
        base = Path(project_root())
    except Exception:  # noqa: BLE001
        base = Path(__file__).resolve().parent
    d = base / "data" / "sky_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _http_bytes(url: str, timeout: float = 25.0) -> bytes:
    """GET 原始字节（图片等）；失败抛可读异常。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept-Encoding": "gzip, identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
            enc = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络不可达：{exc.reason}") from exc
    if "gzip" in enc:
        try:
            blob = gzip.decompress(blob)
        except Exception:  # noqa: BLE001
            pass
    return blob


def _png_size(blob: bytes) -> Tuple[int, int]:
    """读 PNG 的 IHDR 取宽高（非 PNG 返回 0,0）。"""
    if len(blob) < 24 or blob[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return (int.from_bytes(blob[16:20], "big"),
            int.from_bytes(blob[20:24], "big"))


def astro_image_url(lat: str, lon: str, tzshift: Optional[int] = None,
                    product: str = "astro") -> str:
    """7timer 晴天钟**图片**地址（不带 ``output=json``）。"""
    tz = DEFAULT_TZSHIFT if tzshift is None else int(tzshift)
    return (f"{SEVEN_TIMER.format(product=product)}?"
            + urllib.parse.urlencode({
                "lon": str(lon), "lat": str(lat), "ac": 0, "lang": "zh-CN",
                "unit": "metric", "tzshift": tz}))


def fetch_astro_image(lat: str, lon: str, tzshift: Optional[int] = None,
                      product: str = "astro",
                      timeout: float = 25.0) -> Dict[str, Any]:
    """下载晴天钟 PNG 到本地缓存，返回 ``{"path","width","height","url"}``。

    需求（用户 2026-09-22）：**天文晴天钟要直接显示**，不能只放链接。
    Qt 富文本不会去拉远程图片，所以这里先落盘到 ``data/sky_images/``，
    卡片里再用 ``file:///`` 内嵌。下载失败时退回上一次的缓存（离线仍可看图），
    两者都没有则返回空 dict（不影响其余内容）。

    文件名带经纬度与时区 → 同一观测点复用同一张缓存。
    """
    tz = DEFAULT_TZSHIFT if tzshift is None else int(tzshift)
    url = astro_image_url(lat, lon, tz, product)
    safe = f"{product}_{str(lat)}_{str(lon)}_{tz}".replace("/", "_")
    safe = "".join(ch for ch in safe if ch not in '\\:*?"<>|')
    path = _sky_image_dir() / f"{safe}.png"
    size = (0, 0)
    try:
        blob = _http_bytes(url, timeout=timeout)
        size = _png_size(blob)
        if size == (0, 0):
            raise RuntimeError("晴天钟返回的不是 PNG")
        path.write_bytes(blob)
    except Exception as exc:  # noqa: BLE001
        logger.info("晴天钟图下载失败（尝试用缓存）：%s", exc)
        if not path.exists():
            return {}
        try:
            size = _png_size(path.read_bytes())
        except Exception:  # noqa: BLE001
            size = (0, 0)
    return {"path": str(path), "width": size[0], "height": size[1], "url": url}


def _median(values: List[int]) -> float:
    vs = sorted(v for v in values if v > 0)
    if not vs:
        return 0.0
    mid = len(vs) // 2
    return float(vs[mid]) if len(vs) % 2 else (vs[mid - 1] + vs[mid]) / 2.0


def _good(row: Dict[str, Any]) -> bool:
    """单格是否可用：云量 ≤3（0-31%）且视宁度 ≤4（≤1.25″）。"""
    return row["cloud"] <= 3 and row["seeing"] <= 4


def summarize(rows: List[Dict[str, Any]], hours: int = 24) -> Dict[str, Any]:
    """按 7timer 图例给结论：``适合`` / ``勉强`` / ``不适合`` + 最佳时段。

    判定口径（写死在这里，卡片与模型都按它说话）：
      * 窗口取最近 ``hours`` 小时（3 小时一档）；
      * 云量中位数 ≥7（69% 以上）或最佳一格云量 ≥6 → **不适合**；
      * 存在连续两格可用（云量 ≤3 且视宁度 ≤4）且云量中位数 ≤4 → **适合**；
      * 其余 → **勉强**。
    """
    window = list(rows[: max(1, hours // 3)]) or list(rows)
    clouds = [r["cloud"] for r in window if r["cloud"] > 0]
    seeings = [r["seeing"] for r in window if r["seeing"] > 0]
    cloud_med = _median(clouds)
    seeing_med = _median(seeings)
    best_cloud = min(clouds) if clouds else 0
    # 最佳连续窗口：两格内 (云量, 视宁度) 之和最小
    # 取「平均云量 + 平均视宁度」最小的连续两格（窗口只剩 1 格时才用单格）
    # 注意必须比**平均**：直接比总和会让末尾的单格（总和天然更小）永远胜出，
    # 于是「最佳时段」总是落在最后那 3 小时上（实际数据里就是这样露的馅）。
    best: List[Dict[str, Any]] = []
    best_key: Optional[Tuple[float, float]] = None
    for i in range(len(window)):
        seg = window[i:i + 2] or window[i:i + 1]
        key = (sum(r["cloud"] for r in seg) / len(seg),
               sum(r["seeing"] for r in seg) / len(seg))
        if best_key is None or key < best_key:
            best_key, best = key, seg
    pair_ok = any(
        _good(window[i]) and _good(window[i + 1])
        for i in range(len(window) - 1))
    if cloud_med >= 7 or (clouds and best_cloud >= 6):
        verdict, reason = "不适合", f"云量偏大（中位数 {cloud_med:g}/9，最好一格 {best_cloud}/9）"
    elif pair_ok and cloud_med <= 4:
        verdict = "适合"
        reason = f"有连续可用时段（云量中位数 {cloud_med:g}/9，视宁度中位数 {seeing_med:g}/8）"
    else:
        verdict = "勉强"
        reason = f"云量中位数 {cloud_med:g}/9、视宁度中位数 {seeing_med:g}/8，只能碰运气"
    span = ""
    if best:
        span = (f"{best[0].get('time', '')} — {best[-1].get('time', '')}"
                if len(best) > 1
                else f"{best[0].get('time', '')} 前后 3 小时")
    return {
        "verdict": verdict, "reason": reason, "hours": hours,
        "cloud_median": cloud_med, "seeing_median": seeing_med,
        "best_cloud": best_cloud, "best": best, "best_span": span,
        "best_clouds": [r["cloud"] for r in best],
        "best_seeing": [r["seeing"] for r in best],
    }


# ================================================================ 定位 + 取数
def build_sky_bundle(query: str = "", client: Any = None,
                     timeout: float = 15.0) -> Dict[str, Any]:
    """定位 → 7timer 取数 → 结论 + 太阳时刻 + 晴天钟图（+ 有 KEY 时补月相）。

    需求（用户 2026-09-22）：日升日落 / 晨光始 / 昏影终 / 天文晴天钟都要**直接给**。
      * ``bundle["sun"]``：本地推算的太阳时刻（**总是有值**，不依赖任何 KEY）；
      * ``bundle["chart"]``：下载到本地的晴天钟 PNG（供卡片 ``file:///`` 内嵌）；
      * ``bundle["astro"]``：和风天文（月相 / 月出月落；配了 KEY 才有）。
    """
    site = resolve_sky_site(query or "")
    if not (site.get("lat") and site.get("lon")):
        raise RuntimeError(
            "拿不到观测点坐标：本机 IP 归属地接口未返回经纬度，"
            "可在指令后写上城市名（如 #tothemoon 包头）重试。")
    tz = _tzshift()
    sky = fetch_sky(site["lat"], site["lon"], tzshift=tz, timeout=timeout)
    bundle: Dict[str, Any] = {"location": site, "sky": sky,
                              "summary": summarize(sky["rows"]), "astro": {}}
    # 太阳时刻：本地推算（零依赖，必有值）
    try:
        bundle["sun"] = sun_times(site["lat"], site["lon"], tz)
    except Exception as exc:  # noqa: BLE001
        logger.warning("太阳时刻推算失败（忽略）：%s", exc)
        bundle["sun"] = {}
    # 晴天钟图：尽力下载（失败不影响结论；离线时用上次缓存）
    try:
        bundle["chart"] = fetch_astro_image(site["lat"], site["lon"], tz)
    except Exception as exc:  # noqa: BLE001
        logger.info("晴天钟图准备失败（忽略）：%s", exc)
        bundle["chart"] = {}
    if client is not None:
        bundle["astro"] = _fetch_astro(client, site)
    return bundle


def _hhmm(value: Any) -> str:
    """和风时间字段 ``2026-09-21T06:27+08:00`` → ``06:27``（去掉日期与时区）。"""
    t = str(value or "").strip()
    if "T" in t:
        t = t.split("T", 1)[1]
    return t[:5] if len(t) >= 5 and t[2] == ":" else t


def _moon_phase(moon: Dict[str, Any]) -> Tuple[str, str]:
    """月相：新版返回**逐小时列表**，取首条的名字与照度百分比。"""
    mp = moon.get("moonPhase")
    if isinstance(mp, list) and mp:
        first = mp[0] if isinstance(mp[0], dict) else {}
        return (str(first.get("name") or ""),
                str(first.get("illumination") or ""))
    return str(mp or ""), ""


def _transit(rise: str, set_: str) -> str:
    """**上中天时间估算** = 升起与落下的中点（落下在次日时自动回绕）。

    和风只给出没时间，中天时间这里按中点估算，卡片里标注「估算」以免被当成实测值。
    """
    def _minutes(t: str) -> Optional[int]:
        try:
            hh, mm = str(t or "").split(":", 1)
            return int(hh) * 60 + int(mm)
        except Exception:  # noqa: BLE001
            return None

    a, b = _minutes(rise), _minutes(set_)
    if a is None or b is None:
        return ""
    if b < a:
        b += 24 * 60
    mid = ((a + b) // 2) % (24 * 60)
    return f"{mid // 60:02d}:{mid % 60:02d}"


def moon_magnitude(illum_pct: Any) -> str:
    """月亮视星等估算：``m ≈ -12.73 − 2.5·log10(照度比例)``（满月 -12.7）。"""
    try:
        k = float(str(illum_pct or "").strip().rstrip("%")) / 100.0
    except Exception:  # noqa: BLE001
        return ""
    if not 0 < k <= 1:
        return ""
    return f"{-12.73 - 2.5 * math.log10(k):+.1f}"


def _fetch_astro(client: Any, site: Dict[str, Any]) -> Dict[str, Any]:
    """月相 / 日出日落 / 月出月落 + 中天与亮度（和风天文；失败不影响观星结论）。"""
    loc = f"{site.get('lon')},{site.get('lat')}"
    date = datetime.now().strftime("%Y%m%d")
    out: Dict[str, Any] = {}
    try:
        sun = client.astronomy_sun(loc, date) or {}
        out["sunrise"] = _hhmm(sun.get("sunrise"))
        out["sunset"] = _hhmm(sun.get("sunset"))
        out["sun_transit"] = _transit(out["sunrise"], out["sunset"])
        out["sun_mag"] = "-26.7"          # 太阳视星等（常数）
    except Exception as exc:  # noqa: BLE001
        logger.info("日出日落获取失败（忽略）：%s", exc)
    try:
        moon = client.astronomy_moon(loc, date) or {}
        name, illum = _moon_phase(moon)
        out["moon_phase"] = name
        out["moon_illum"] = f"{illum}%" if illum else ""
        out["moonrise"] = _hhmm(moon.get("moonrise"))
        out["moonset"] = _hhmm(moon.get("moonset"))
        out["moon_transit"] = _transit(out["moonrise"], out["moonset"])
        out["moon_mag"] = moon_magnitude(illum)
    except Exception as exc:  # noqa: BLE001
        logger.info("月相获取失败（忽略）：%s", exc)
    return out


# ================================================================ 渲染
def _esc(s: Any) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _cell(text: str, sub: str = "") -> str:
    base = ("padding:6px 4px;text-align:center;color:#2b2b33;"
            "font-size:12px;background:#f7f8fd;")
    return (f'<td style="{base}">{text}'
            f'<div style="color:#9aa0ac;font-size:10px;margin-top:2px;">{sub}</div></td>')


def _solar_html(bundle: Dict[str, Any], accent: str) -> str:
    """「太阳时刻」块：日出 / 日落 / 晨光始 / 昏影终 / 上中天 / 昼长。

    需求（用户 2026-09-22）：「不要放 GitHub 链接当答案，要真的显示晨光始 /
    昏影终、天文晴天钟、日升日落时间」。
    - 日出日落：优先和风实测（配了 KEY 才有），否则用本地推算；
    - 晨光始 / 昏影终：**民用晨昏蒙影**（太阳高度 −6°），和风不提供，一律本地推算；
    - 上中天：真太阳时正午（不是「升起/落下中点」的估算）。
    """
    sun = bundle.get("sun") or {}
    astro = bundle.get("astro") or {}
    sunrise = str(astro.get("sunrise") or sun.get("sunrise") or "")
    sunset = str(astro.get("sunset") or sun.get("sunset") or "")
    dawn = str(sun.get("civil_dawn") or "")
    dusk = str(sun.get("civil_dusk") or "")
    transit = str(sun.get("transit") or "")
    day_len = str(sun.get("day_length") or "")
    polar = str(sun.get("polar") or "")
    if not any((sunrise, sunset, dawn, dusk, transit, polar)):
        return ""
    rows: List[str] = []
    if polar:
        rows.append(f'昼夜：{_esc(polar)}（当日无日出 / 日落）')
    elif sunrise or sunset:
        rows.append(f'日出 {_esc(sunrise or "—")} ｜ 日落 {_esc(sunset or "—")}'
                    + (f' ｜ 昼长 {_esc(day_len)}' if day_len else ""))
    if dawn or dusk:
        rows.append(f'晨光始 {_esc(dawn or "—")} ｜ 昏影终 {_esc(dusk or "—")}'
                    '<span style="color:#9aa0ac;">（民用晨昏蒙影，太阳高度 −6°）</span>')
    if transit:
        rows.append(f'太阳上中天 {_esc(transit)}')
    src = "和风实测" if astro.get("sunrise") else "本地推算"
    return (
        '<div style="margin-top:8px;background:#eef3ff;border-radius:10px;'
        'padding:10px 12px;">'
        '<div style="color:#3b5bdb;font-size:12px;font-weight:600;">太阳时刻</div>'
        + "".join(
            f'<div style="color:#2b2b33;font-size:12px;margin-top:3px;">{r}</div>'
            for r in rows)
        + f'<div style="color:#9aa0ac;font-size:10px;margin-top:4px;">'
          f'日出日落：{src}；晨光始 / 昏影终为本地推算'
          f'（NOAA 简化式，误差 ≲1 分钟）。</div></div>')


def _chart_html(bundle: Dict[str, Any], accent: str) -> str:
    """把 7timer 晴天钟图**内嵌**进卡片（Qt 富文本只认本地 ``file:///`` 图片）。

    需求（用户 2026-09-22）：天文晴天钟要**直接看到**，不能只放一个链接。
    图由 :func:`fetch_astro_image` 预先下载到 ``data/sky_images/``；
    读图口径按官方 wiki 的 ASTRO 7 行要素写成一行说明（wiki 本身没有日月出没内容，
    所以这里**不**再把链接当作晨昏蒙影的答案）。
    """
    chart = bundle.get("chart") or {}
    sky = bundle.get("sky") or {}
    path = str(chart.get("path") or "")
    url = str(chart.get("url") or sky.get("image_url") or "")
    img = ""
    p = Path(path) if path else None
    if p is not None and p.exists():
        w = int(chart.get("width") or 0)
        h = int(chart.get("height") or 0)
        if w <= 0 or h <= 0:
            try:
                w, h = _png_size(p.read_bytes())
            except Exception:  # noqa: BLE001
                w = h = 0
        if w > 0 and h > 0:
            tw = 660
            th = max(1, int(round(tw * h / float(w))))
            img = (f'<div style="margin-top:6px;">'
                   f'<img src="{_esc(p.as_uri())}" width="{tw}" height="{th}">'
                   f'</div>')
    if not img and not url:
        return ""
    head = ('<div style="color:#6b7280;font-size:11px;margin-top:8px;">'
            '天文晴天钟（7timer · 72 小时 · 3 小时一档）：</div>')
    foot = ("读图（官方 wiki 口径）：图上依次为 云量（越蓝越晴）· 视宁度 · 透明度 · "
            "降水概率 · 大气不稳定度 · 潮湿警告 · 大风警告，数值越小越好。")
    tail = ""
    if url:
        tail = (f'<div style="font-size:11px;color:#6b7280;margin-top:2px;">'
                f'原图（可点开看大图）：'
                f'<a href="{_esc(url)}" style="color:{accent};">7timer astro 图</a>'
                f'</div>')
    return (head + img
            + f'<div style="color:#9aa0ac;font-size:10px;margin-top:4px;">'
              f'{foot}</div>' + tail)


def render_sky_html(bundle: Dict[str, Any], accent: str = "#6c8ef5") -> str:
    """渲染「观星 / 天象条件」卡片（表格式布局，Qt 富文本子集可显示）。"""
    site = bundle.get("location") or {}
    sky = bundle.get("sky") or {}
    rows = sky.get("rows") or []
    summ = bundle.get("summary") or {}
    astro = bundle.get("astro") or {}
    name = _esc(site.get("name") or "本机")
    adm = _esc(site.get("adm") or "")
    src = {"ip": "本机 IP 定位", "table": "指定城市"}.get(
        str(site.get("source") or ""), "")
    sub = " · ".join(x for x in (adm, src, "数据来源 7timer") if x)
    verdict = str(summ.get("verdict") or "—")
    color = {"适合": "#15803d", "勉强": "#b45309", "不适合": "#b42318"}.get(
        verdict, "#4b5563")

    out: List[str] = ['<div style="font-family:\'Microsoft YaHei UI\',sans-serif;">']
    # 标题
    out.append(
        f'<div style="background:{accent};color:#fff;border-radius:12px;padding:10px 14px;">'
        f'<div style="font-size:15px;font-weight:600;">{name} · 观星 / 天象条件</div>'
        f'<div style="font-size:11px;opacity:0.85;">{sub}'
        f' · 坐标 {_esc(sky.get("lat"))},{_esc(sky.get("lon"))}</div></div>')
    # 结论
    out.append(
        f'<div style="margin-top:8px;background:#f7f8fd;border-radius:10px;padding:10px 12px;">'
        f'<div style="font-size:22px;font-weight:600;color:{color};">{_esc(verdict)}</div>'
        f'<div style="color:#4b5563;font-size:12px;margin-top:2px;">{_esc(summ.get("reason"))}</div>')
    if summ.get("best_span"):
        out.append(
            f'<div style="color:#374151;font-size:12px;margin-top:6px;">'
            f'最佳时段：{_esc(summ.get("best_span"))}'
            f'（云量 {"-".join(str(c) for c in summ.get("best_clouds") or [])}/9，'
            f'视宁度 {"-".join(str(c) for c in summ.get("best_seeing") or [])}/8）</div>')
    out.append("</div>")
    # 太阳时刻：日出 / 日落 / 晨光始 / 昏影终 / 上中天 / 昼长
    # 需求（用户 2026-09-22）：这些必须**直接显示**，不能只给链接
    out.append(_solar_html(bundle, accent))
    # 月相 / 月出月落（太阳相关已在上面独立成块，这里不再重复）
    if any(astro.values()):
        bits = []
        if astro.get("moonrise") or astro.get("moonset"):
            bits.append(f'月出 {_esc(astro.get("moonrise") or "—")} / '
                        f'月落 {_esc(astro.get("moonset") or "—")}')
        if astro.get("moon_transit"):
            # 中天按「升起/落下中点」估算，明确标注（不冒充实测值）
            bits.append('月上中天（估算） ' + _esc(astro.get("moon_transit")))
        if astro.get("moon_mag"):
            bits.append(f'月亮视星等（估算） {_esc(astro.get("moon_mag"))}')
        if astro.get("moon_phase"):
            bits.append(f'月相 {_esc(astro.get("moon_phase"))}'
                        + (f'（{_esc(astro.get("moon_illum"))}）'
                           if astro.get("moon_illum") else ""))
        out.append(
            '<div style="margin-top:6px;background:#f7f8fd;border-radius:10px;'
            'padding:8px 12px;color:#374151;font-size:12px;">'
            + " · ".join(bits) + "</div>")
    # 逐 3 小时表（未来 24 小时）
    show = rows[:8]
    if show:
        head = "".join(f'<td style="padding:4px 2px;text-align:center;color:#6b7280;'
                       f'font-size:10px;">{_esc(r.get("clock"))}</td>' for r in show)
        line_cloud = "".join(
            _cell(str(r["cloud"]), _esc(CLOUD_LEGEND.get(r["cloud"], "")))
            for r in show)
        line_see = "".join(
            _cell(str(r["seeing"]), _esc(SEEING_LEGEND.get(r["seeing"], "")))
            for r in show)
        line_tr = "".join(
            _cell(str(r["transp"]), _esc(TRANSP_LEGEND.get(r["transp"], "")))
            for r in show)
        line_wind = "".join(
            _cell(str(r["wind"]), _esc(f'{r.get("wind_dir") or ""} m/s'))
            for r in show)
        out.append(
            '<table border="0" cellspacing="3" cellpadding="0" width="100%"'
            ' style="margin-top:8px;">'
            f"<tr><td style='color:#6b7280;font-size:10px;'>时刻</td>{head}</tr>"
            f"<tr><td style='color:#6b7280;font-size:10px;'>云量</td>{line_cloud}</tr>"
            f"<tr><td style='color:#6b7280;font-size:10px;'>视宁</td>{line_see}</tr>"
            f"<tr><td style='color:#6b7280;font-size:10px;'>透明</td>{line_tr}</tr>"
            f"<tr><td style='color:#6b7280;font-size:10px;'>风速</td>{line_wind}</tr>"
            "</table>")
        out.append(
            '<div style="color:#9aa0ac;font-size:10px;margin-top:4px;">'
            "云量 1~9（数字越小越晴）、视宁度 1~8（越小越稳）、透明度 1~8（越小越通透）；"
            f"时间为 UTC+{int(sky.get('tzshift') or DEFAULT_TZSHIFT)}。</div>")
    # 天文晴天钟图：**直接内嵌显示**（用户 2026-09-22 需求：不要只放链接）
    out.append(_chart_html(bundle, accent))
    out.append("</div>")
    return "".join(out)
