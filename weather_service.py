# -*- coding: utf-8 -*-
"""
weather_service.py — \\@weather 天气工具（和风天气 QWeather）

数据来源：和风天气开发服务 https://dev.qweather.com/docs/
本模块只负责「取数据 + 渲染 HTML5 展示卡片」，不含任何 QWidget：
    QWeatherClient       接口封装（实况 / 预报 / 指数 / 预警 / 空气 / 分钟降水 /
                         天文 / 太阳辐射 / 时光机 / 热带气旋 / 海洋）
    fetch_bundle()        按用户提示词决定要取哪些数据（一次打包）
    render_weather_html() 渲染成主流天气 App 风格的 HTML5 卡片（Qt 富文本可显示）
    parse_city() / parse_options()  提示词解析（城市 + 要额外展示的模块）

鉴权：KEY 取自「常用接口类 API 管理器」（data/api_vault.json，名称含「和风天气」的条目），
也可由调用方直接传入。所有付费/不可用接口失败时只降级提示，不影响主卡片显示。
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_HOST = "https://devapi.qweather.com"
DEFAULT_GEO_HOST = "https://geoapi.qweather.com"

#: 需求：和风天气已给每个帐号分配**独立的 API Host**（形如 xxxx.xy.qweatherapi.com），
#: 公共地址（devapi / api / geoapi）自 2026 年起逐步停服，继续用会返回
#: 403 invalid-host（"An invalid or unauthorized API Host"）。
#: 因此：地址里必须填控制台分配的独立 Host（GeoAPI 已不再被 \@weather 使用）。
PUBLIC_HOSTS = ("devapi.qweather.com", "api.qweather.com", "geoapi.qweather.com")


def is_custom_host(host: str) -> bool:
    """地址是否为「帐号独立 API Host」（不是已停服的公共地址）。"""
    h = str(host or "").lower()
    return bool(h) and not any(p in h for p in PUBLIC_HOSTS)


#: 公共地址停服后的可操作指引（403 invalid-host / 404 空响应都会命中）。
HOST_DEPRECATED_HINT = (
    "和风天气已停用公共 API 地址，必须填写控制台分配给本帐号的专属 API Host"
    "（形如 xxxx.xy.qweatherapi.com）。\n"
    "获取：https://console.qweather.com → 设置（或项目管理）→ 查看「API Host」，\n"
    "复制后填到：设置 → 常用接口类 API 管理器 →「和风天气」→「地址」→ 保存修改。\n"
    "天气接口会走该 Host（定位改用内置静态城市表，不再依赖 GeoAPI）。"
)

#: 403 Security Restriction：请求违反了该凭据在控制台设置的限制
#:（官方不返回具体是哪一项）。控制台里限制分两类，入口都在
#: 项目管理 → 选项目 → 选凭据：
#:   · 应用限制（白名单，每个凭据仅能设置一种）：网址限制 / IP 限制 /
#:     iOS 应用限制 / Android 应用限制，留空才表示允许所有来源。
#:   · API 限制：只有勾选的接口才允许请求。
#: 桌面程序既不带 Referer，也没有 iOS Bundle ID / Android 包名与签名，
#: 因此被设成「网址限制」或「iOS/Android 限制」的凭据必然 403。
SECURITY_RESTRICTION_HINT = (
    "接口返回 403 Security Restriction：当前请求违反了该凭据在控制台设置的限制"
    "（官方不说明具体是哪一项）。\n"
    "请到 https://console.qweather.com → 项目管理 → 选中项目 → 点击该凭据，检查两处：\n"
    "【应用限制】每个凭据只能设置一种，桌面端建议改成「IP 限制」并填入本机公网 IP，"
    "或直接清空（留空 = 允许所有来源）：\n"
    "  · 网址限制：桌面程序不带 Referer，必定被拒，请勿使用；\n"
    "  · iOS / Android 限制：需额外请求头（Bundle ID / 包名+签名），桌面端无法满足；\n"
    "【API 限制】确认已勾选「天气预报」等要用的接口"
    "（本项目的 \\@weather 定位已改用内置静态城市表，无需 GeoAPI）。"
)

# 提示词 → 额外模块
OPT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "minutely": ("分钟", "降水", "下雨", "降雨", "还要下多久", "雨什么时候停"),
    "astronomy": ("天文", "日出", "日落", "月出", "月升", "月落", "月相", "月亮"),
    "solar": ("辐射", "太阳辐射", "光伏", "dni", "dhi", "ghi"),
    "historical": ("时光机", "历史天气", "过去", "昨天", "前天", "前几天"),
    "tropical": ("台风", "热带气旋", "气旋", "风暴"),
    "ocean": ("海洋", "潮汐", "潮水", "满潮", "干潮", "海浪", "浪高"),
}

OPT_TITLES: Dict[str, str] = {
    "minutely": "分钟级降水",
    "astronomy": "天文（日月）",
    "solar": "太阳辐射",
    "historical": "时光机（历史天气）",
    "tropical": "热带气旋（台风）",
    "ocean": "海洋数据（潮汐）",
}

def icon_for(text: str) -> str:
    """天气文字 → 图标。

    本项目界面不使用 emoji，这里恒返回空串；保留此函数仅为兼容旧调用方
    （历史上它返回 emoji 图标）。天气状况一律用文字描述。
    """
    return ""


def parse_options(text: str) -> List[str]:
    """按提示词判断需要额外展示的模块（返回模块名列表）。"""
    low = str(text or "").lower()
    return [k for k, kws in OPT_KEYWORDS.items() if any(w in low for w in kws)]


#: 「本地意图词」：用户想查的是**自己所在位置**，不是地名。
#: 需求（用户反馈）：`\@weather 查询本地天气` 应当读取本机 IP → 匹配 IP 归属地，
#: 而不是把剥剩下的「本地」当成**地名关键词**去查（那样只会得到「未收录」失败）。
LOCAL_INTENT_WORDS: Tuple[str, ...] = (
    "本机", "本地", "当地", "当前", "当前位置", "所在城市", "所在地区",
    "我这里", "我这儿", "我这", "这里", "这边", "我的位置", "自动定位",
    "ip归属地", "ip 归属地", "归属地", "定位",
)


def parse_city(text: str) -> str:
    """从提示词里提取城市/区县名。

    剥离：1) 全部 \\@技能 标签（含中文别名，如 \\@天气）；2) 天气类套话；
    3) 额外模块关键词（「台风」「分钟」等，它们只用于决定展示哪些模块）；
    4) 「本地意图词」（本地/本机/我这里/当前位置…，见 :data:`LOCAL_INTENT_WORDS`）。
    剩下的即为地名（如「上海」「杭州 西湖区」）；**返回空串表示「用本机 IP 定位」**。

    第 4 条是用户反馈「`\\@weather` 说查询本地天气…应该读取本地 IP」的修复：
    以前「查询本地天气」剥掉「查询/天气」后只剩「本地」，被当成**地名关键词**去
    静态城市表里查 →「内置城市表未收录「本地」」直接失败，而不是走 IP 定位。
    """
    import re
    s = str(text or "").strip()
    # 1) 剥离所有 \@技能 标签（中英文触发词 / 别名都覆盖）
    s = re.sub(r"\\@[A-Za-z0-9_\u4e00-\u9fa5-]+", " ", s)
    # 2) 天气类套话
    for w in ("天气预报", "天气怎么样", "天气", "怎么样", "如何", "查询", "查一下",
              "看看", "今天", "明天", "后天", "预报", "气温", "多少度", "请问",
              "帮我", "please"):
        s = s.replace(w, " ")
    # 3) 额外模块关键词（降水/台风/天文…只是展示开关，不是地名）
    for kws in OPT_KEYWORDS.values():
        for w in kws:
            s = s.replace(w, " ")
    # 4) 本地意图词（不是地名，剥离后为空即走本机 IP 定位）
    for w in LOCAL_INTENT_WORDS:
        s = s.replace(w, " ")
    s = s.replace("位置", " ").replace("地方的", " ").replace("本地", " ")
    # 5) 英文写法（`\@weather my location weather` 之类）
    s = re.sub(r"\b(my location|current location|local weather|the weather|"
               r"weather|forecast|local|here)\b", " ", s, flags=re.I)
    s = re.sub(r"[，。！？,.!?、；;：:\s]+", " ", s).strip()
    s = re.sub(r"^[的了呢吗啊]+|[的了呢吗啊]+$", "", s).strip()
    return s


def is_local_intent(text: str) -> bool:
    """这段文字是不是在表达「就查我这里」（没有给出具体地名）。

    与 :func:`parse_city` 配套：`resolve_location` / `fetch_bundle` 收到「本地」
    「本机」「我这里」这类**意图词**时要当作「未指定地名」→ 走本机 IP 定位，
    而不是拿它去静态城市表里当地名查（用户反馈的「未收录「本地」」就是这样来的）。
    """
    import re
    s = _norm_city(text)
    if not s:
        return False
    if any(w in s for w in LOCAL_INTENT_WORDS) or "位置" in s:
        return True
    return bool(re.search(r"\b(my location|current location|local|here)\b",
                          s, flags=re.I))


# ================================================================ HTTP
def _http_json(url: str, timeout: float = 8.0,
               headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """GET JSON。失败抛异常（HTTPError 会带上服务端返回的正文，便于诊断）。

    和风天气接口**始终返回 Gzip**（即使请求 identity），因此这里按
    Content-Encoding / 文件头嗅探统一解压，避免拿到压缩二进制导致解析失败。
    """
    hdr = {"User-Agent": "Celestia-AssistantAI/1.0",
           "Accept-Encoding": "gzip, identity"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
            enc = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:  # 4xx/5xx：带上服务端正文再抛
        try:
            body = exc.read()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            detail = body.decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        msg = f"HTTP {exc.code}：{detail or exc.reason}"
        # 公共地址停服的两个典型表现：403 invalid-host、404 空响应
        low = (detail or "").lower()
        if exc.code in (401, 403, 404) and (
                "invalid-host" in low or "invalid host" in low or not detail):
            msg += "\n" + HOST_DEPRECATED_HINT
        if "security-restriction" in low or "security restriction" in low:
            msg += "\n" + SECURITY_RESTRICTION_HINT
            ip = public_ip()
            if ip:
                msg += f"\n当前本机公网 IP：{ip}（若启用了 IP 白名单需把它加进去）。"
        raise RuntimeError(msg) from exc
    if enc == "gzip" or blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except Exception:  # noqa: BLE001
            pass
    return json.loads(blob.decode("utf-8", errors="replace"))


#: IP 归属地结果缓存（同一轮对话里 public_ip/ip_info 会被多次调用，避免重复请求）
_ip_cache: Dict[str, Any] = {}
_ip_cache_ts: float = 0.0
_IP_CACHE_TTL = 120.0


def ip_info(timeout: float = 6.0, use_cache: bool = True) -> Dict[str, str]:
    """本机公网 IP + 归属地（**不使用和风 GeoAPI**，只查第三方 IP 库）。

    需求：\\@weather 定位顺序 = 先取本机 IP → 判断 IP 归属地 → 静态城市表 → 天气。
    数据源：pconline（国内稳定，返回 ip/pro/city，GBK）→ ip-api.com 补经纬度。

    返回 {"ip", "city", "region", "lat", "lon"}，任一项拿不到即为空串。
    """
    global _ip_cache, _ip_cache_ts
    import time as _time
    if (use_cache and _ip_cache
            and _time.time() - _ip_cache_ts < _IP_CACHE_TTL):
        return dict(_ip_cache)
    info = {"ip": "", "city": "", "region": "", "lat": "", "lon": ""}
    try:
        req = urllib.request.Request(
            "https://whois.pconline.com.cn/ipJson.jsp?json=true",
            headers={"User-Agent": "Celestia-AssistantAI/1.0"})
        with urllib.request.urlopen(req, timeout=min(timeout, 5.0)) as resp:
            data = json.loads(resp.read().decode("gbk", errors="replace"))
        info["ip"] = str(data.get("ip") or "")
        info["city"] = str(data.get("city") or "")
        info["region"] = str(data.get("pro") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _http_json(
            "https://ip-api.com/json/?lang=zh-CN&fields=status,regionName,city,lat,lon",
            timeout=min(timeout, 4.0))
        if data.get("status") == "success":
            info["lat"] = str(data.get("lat") or "")
            info["lon"] = str(data.get("lon") or "")
            info["city"] = info["city"] or str(data.get("city") or "")
            info["region"] = info["region"] or str(data.get("regionName") or "")
    except Exception:  # noqa: BLE001
        pass
    if not info["city"] and not info["lat"]:
        # 两个源都失败：不写缓存，下次重试（网络恢复后能自愈）
        return info
    _ip_cache, _ip_cache_ts = dict(info), _time.time()
    return info


def ip_city(timeout: float = 6.0) -> Dict[str, str]:
    """兼容旧调用：本机 IP 归属地 {city, region, lat, lon}（内部走 `ip_info`）。"""
    info = ip_info(timeout=timeout)
    return {"city": info["city"], "region": info["region"],
            "lat": info["lat"], "lon": info["lon"]}


def public_ip(timeout: float = 5.0) -> str:
    """本机出口公网 IP（报错提示与 IP 定位共用，内部走 `ip_info` 缓存）。"""
    return ip_info(timeout=timeout).get("ip", "")


def _fmt_coord(v: str) -> str:
    """经纬度格式化：新版接口要求十进制且最多两位小数。"""
    try:
        return f"{float(str(v).strip()):.2f}"
    except (TypeError, ValueError):
        return str(v or "").strip()


def norm_host(host: str) -> str:
    """规范化 API Host：控制台复制出来的 Host 常不带 https://，这里自动补上。"""
    h = str(host or "").strip().rstrip("/")
    if h and not h.lower().startswith(("http://", "https://")):
        h = "https://" + h
    return h


# ================================================================ 客户端
class QWeatherClient:
    """和风天气 v7 接口封装。"""

    def __init__(self, key: str, base: str = "", geo_base: str = "",
                 headers: Optional[Dict[str, str]] = None) -> None:
        self.key = str(key or "").strip()
        self.base = norm_host(str(base or "")) or DEFAULT_HOST
        # GeoAPI：独立 API Host 时与天气共用同一 host（路径 /geo/v2/...）；
        # 仍是公共地址（旧配置）时才回落到 geoapi.qweather.com。
        self.geo_base = norm_host(str(geo_base or "")) or (
            self.base if is_custom_host(self.base) else DEFAULT_GEO_HOST)
        hdrs = dict(headers or {})
        has_auth = any(k.lower() in ("authorization", "x-qw-api-key") for k in hdrs)
        if self.key and not has_auth:
            # 官方推荐：请求标头 X-QW-Api-Key。注意官方明确要求
            # 「请不要同时使用多种身份认证方式」，故不再拼 key= 查询参数。
            hdrs["X-QW-Api-Key"] = self.key
        self.headers = hdrs

    # ------------------------------------------------------------ 基础
    def _get(self, path: str, params: Dict[str, Any],
             host: str = "", timeout: float = 8.0) -> Dict[str, Any]:
        if not self.key:
            raise RuntimeError("未配置接口 KEY")
        if not is_custom_host(self.base):
            raise RuntimeError(
                f"当前接口地址「{self.base}」是已停服的公共地址。\n"
                f"{HOST_DEPRECATED_HINT}")
        # 只走请求标头鉴权（X-QW-Api-Key）。官方要求不得同时用多种认证方式，
        # 否则可能触发 403 Security Restriction。
        qs = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        url = f"{host or self.base}{path}"
        if qs:
            url += "?" + urllib.parse.urlencode(qs)
        return _http_json(url, timeout=timeout, headers=self.headers)

    @staticmethod
    def _ok(data: Dict[str, Any]) -> bool:
        # 新版接口（/airquality/v1、/weatheralert/v1）不返回 code 字段，视为成功
        return str(data.get("code") or "200") == "200"

    def city_lookup(self, query: str, timeout: float = 8.0) -> List[Dict[str, Any]]:
        """城市搜索（GeoAPI）→ [{id, name, adm2, adm1, country, lat, lon}]。

        **注意：\\@weather 已不再调用本方法**（需求：定位改为「本机 IP + 内置静态
        城市表」，见 `resolve_location`）。此处保留仅为兼容旧调用方/手工脚本。

        路径：独立 API Host 为 /geo/v2/city/lookup，公共地址为 /v2/city/lookup。
        """
        if not is_custom_host(self.geo_base):
            raise RuntimeError(
                f"当前城市检索地址「{self.geo_base}」是已停服的公共地址。\n"
                f"{HOST_DEPRECATED_HINT}")
        path = "/geo/v2/city/lookup"
        data = _http_json(
            f"{self.geo_base}{path}?"
            + urllib.parse.urlencode({"location": query, "range": "cn",
                                      "number": 5}),
            timeout=timeout, headers=self.headers)
        return list(data.get("location") or []) if self._ok(data) else []

    # ------------------------------------------------------------ 各类数据
    def now(self, loc: str) -> Dict[str, Any]:
        return self._get("/v7/weather/now", {"location": loc})

    def daily(self, loc: str, days: int = 3) -> Dict[str, Any]:
        path = "/v7/weather/7d" if days >= 7 else "/v7/weather/3d"
        return self._get(path, {"location": loc})

    def hourly(self, loc: str) -> Dict[str, Any]:
        return self._get("/v7/weather/24h", {"location": loc})

    def indices(self, loc: str) -> Dict[str, Any]:
        return self._get("/v7/indices/1d", {"location": loc, "type": 0})

    def warning(self, lat: str, lon: str) -> Dict[str, Any]:
        """实时天气预警：新版 /weatheralert/v1/current/{lat}/{lon}。

        v7 的 /v7/warning/now 已废弃（返回 403 Deprecated），新版只接受经纬度。
        返回 {"metadata": {...}, "alerts": [...]}，无预警时 alerts 为空。
        """
        return self._get(f"/weatheralert/v1/current/{_fmt_coord(lat)}"
                         f"/{_fmt_coord(lon)}", {"lang": "zh"})

    def air(self, lat: str, lon: str) -> Dict[str, Any]:
        """实时空气质量：新版 /airquality/v1/current/{lat}/{lon}（v7 已废弃）。

        返回 {"indexes": [...], "pollutants": [...]}，中国标准代码为 cn-mee。
        """
        return self._get(f"/airquality/v1/current/{_fmt_coord(lat)}"
                         f"/{_fmt_coord(lon)}", {"lang": "zh"})

    def minutely(self, latlon: str) -> Dict[str, Any]:
        return self._get("/v7/minutely/5m", {"location": latlon})

    def astronomy_sun(self, loc: str, date: str) -> Dict[str, Any]:
        return self._get("/v7/astronomy/sun", {"location": loc, "date": date})

    def astronomy_moon(self, loc: str, date: str) -> Dict[str, Any]:
        return self._get("/v7/astronomy/moon", {"location": loc, "date": date})

    def solar(self, lat: str, lon: str, hours: int = 12) -> Dict[str, Any]:
        return self._get(f"/solarradiation/v1/forecast/{lat}/{lon}",
                         {"hours": hours, "interval": 60, "extra": "weather",
                          "localTime": "true"})

    def historical(self, loc: str, date: str) -> Dict[str, Any]:
        return self._get("/v7/historical/weather", {"location": loc, "date": date})

    def storm_list(self, year: int) -> Dict[str, Any]:
        return self._get("/v7/tropical/storm-list", {"basin": "NP", "year": year})

    def storm_track(self, storm_id: str) -> Dict[str, Any]:
        return self._get("/v7/tropical/storm-track",
                         {"basin": "NP", "stormid": storm_id})

    def ocean_tide(self, loc: str, date: str) -> Dict[str, Any]:
        return self._get("/v7/ocean/tide", {"location": loc, "date": date})


# ================================================================ 内置城市表
#: 和风官方城市列表（qwd/LocationList）精简版：3183 个中国城市，含 LocationID 与经纬度。
#: **\\@weather 的主定位来源**（需求：不用 GeoAPI 动态检索，城市名/IP 归属地都查这张静态表）。
CITY_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "qweather_cities.csv")
_city_rows_cache: Optional[List[Dict[str, str]]] = None

#: 行政后缀：匹配前剥离，让「包头市」↔「包头」、「青山区」↔「青山」互通
_ADMIN_SUFFIX = ("特别行政区", "自治州", "自治区", "自治县", "地区", "盟",
                 "市", "县", "区", "省", "镇", "乡", "村")


def _norm_city(s: str) -> str:
    """地名归一化：去空白 + 剥离行政后缀，便于模糊匹配。"""
    t = str(s or "").strip().replace(" ", "")
    for suf in _ADMIN_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf) + 1:
            t = t[: -len(suf)]
            break
    return t


def _load_city_rows() -> List[Dict[str, str]]:
    """懒加载内置城市表（只读一次；读不到返回空表，不影响主流程）。"""
    global _city_rows_cache
    if _city_rows_cache is None:
        rows: List[Dict[str, str]] = []
        try:
            with open(CITY_TABLE_PATH, encoding="utf-8", newline="") as f:
                rows = [r for r in csv.DictReader(f) if r.get("id")]
        except Exception:  # noqa: BLE001
            rows = []
        _city_rows_cache = rows
    return _city_rows_cache


def local_city_lookup(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """内置城市表检索 → 与城市检索接口同结构：[{id, name, adm2, adm1, country, lat, lon}]。

    命中优先级：完全相等 → 前缀互含 → 子串包含。整串无结果时逐级去掉末尾一字重试，
    因此「包头市青山区」这类带区县的名称也能落到「包头市」。
    """
    q = _norm_city(query)
    if not q:
        return []
    rows = _load_city_rows()
    if not rows:
        return []

    def _match(key: str) -> List[Dict[str, Any]]:
        exact: List[Dict[str, Any]] = []
        starts: List[Dict[str, Any]] = []
        contains: List[Dict[str, Any]] = []
        for r in rows:
            name = str(r.get("name") or "").strip()
            n = _norm_city(name)
            if not n:
                continue
            if n == key:
                bucket = exact
            elif n.startswith(key) or key.startswith(n):
                bucket = starts
            elif key in n:
                bucket = contains
            else:
                continue
            bucket.append({"id": str(r.get("id") or ""), "name": name,
                           "adm2": str(r.get("adm2") or ""),
                           "adm1": str(r.get("adm1") or ""),
                           "country": "中国",
                           "lat": str(r.get("lat") or ""),
                           "lon": str(r.get("lon") or "")})
        return (exact + starts + contains)[:limit]

    tried = q
    for _ in range(4):
        hit = _match(tried)
        if hit:
            return hit
        if len(tried) <= 2:
            break
        tried = tried[:-1]
    return []


def resolve_location(query: str = "", timeout: float = 8.0) -> Dict[str, Any]:
    """静态定位：**全程不调用和风 GeoAPI**。

    需求：\\@weather 不再用 GeoAPI 动态检索地址，改为
      1) 先取本机公网 IP（`ip_info`）→ 判断 IP 归属地（省 / 市）；
      2) 用**内置静态城市表** `data/qweather_cities.csv`（含官方 LocationID 与
         经纬度）匹配出所在地；
      3) 静态表未收录时退回 IP 给的经纬度（和风 v7 允许 location=经度,纬度）。

    返回::

        {"query": "查询地名", "source": "table"|"ip-table"|"ip-coord"|"",
         "ip": "1.2.3.4", "city": "北京", "region": "北京市",
         "lat": "39.90", "lon": "116.40", "match": {内置城市表条目}|None}
    """
    out: Dict[str, Any] = {"query": "", "source": "", "ip": "", "city": "",
                          "region": "", "lat": "", "lon": "", "match": None}
    q_user = str(query or "").strip()
    # 需求：地名位置写的是「本地 / 本机 / 我这里」这类**意图词**时，
    # 当作「未指定地名」→ 走本机 IP 定位（别拿它去城市表里查）
    if q_user and is_local_intent(q_user):
        q_user = ""
    if q_user:
        # 用户指定了地名 → 只走内置静态城市表（多段地名逐级退词）
        out["query"] = q_user
        hits = local_city_lookup(q_user)
        if not hits and " " in q_user:
            for part in q_user.split():
                hits = local_city_lookup(part.strip())
                if hits:
                    break
        if hits:
            out["match"] = hits[0]
            out["source"] = "table"
        return out

    # 未指定地名 → 本机 IP 定位
    info = ip_info(timeout=timeout)
    out["ip"] = info.get("ip", "")
    out["city"] = info.get("city", "")
    out["region"] = info.get("region", "")
    out["lat"] = info.get("lat", "")
    out["lon"] = info.get("lon", "")
    for name in (info.get("city") or "", info.get("region") or ""):
        if not name:
            continue
        hits = local_city_lookup(name)
        if hits:
            out["query"] = name
            out["match"] = hits[0]
            out["source"] = "ip-table"
            out["lat"] = out["lat"] or str(hits[0].get("lat") or "")
            out["lon"] = out["lon"] or str(hits[0].get("lon") or "")
            return out
    if out["lat"] and out["lon"]:
        out["source"] = "ip-coord"
    return out


# ================================================================ 打包取数
def _yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def fetch_bundle(client: QWeatherClient, query: str = "",
                 options: Optional[List[str]] = None,
                 timeout: float = 8.0) -> Dict[str, Any]:
    """打包取数。定位**全程静态**（不调用和风 GeoAPI）：

      1) 传了城市名 → 直接查内置城市表 `data/qweather_cities.csv` 取 LocationID；
      2) 未传城市名 → 本机 IP → IP 归属地（IP 库）→ 内置城市表取 LocationID；
      3) 静态表未收录时退回 IP 经纬度（location=经度,纬度）。

    返回结构：{"location": {...}, "now": {...}, "daily": {...}, "indices": [...],
             "warning": [...], "air": {...}, "extras": {模块: {...}},
             "errors": [提示...]}
    """
    opts = list(options or [])
    bundle: Dict[str, Any] = {"location": {}, "extras": {}, "errors": []}
    q_user = (query or "").strip()      # 用户手写的城市（空 = 用本机 IP 定位）
    # 需求：「本地 / 本机 / 我这里」这类**意图词**不算地名 → 同样走本机 IP 定位
    if q_user and is_local_intent(q_user):
        q_user = ""
    # 需求：定位**全程不用和风 GeoAPI** —— 先取本机 IP → 判断 IP 归属地 →
    # 内置静态城市表（含官方 LocationID/经纬度）匹配 → 用该地查询天气。
    loc_info = resolve_location(q_user, timeout=timeout)
    q = str(loc_info.get("query") or "")
    lat = str(loc_info.get("lat") or "")
    lon = str(loc_info.get("lon") or "")
    hit = loc_info.get("match") or None
    source = str(loc_info.get("source") or "")
    if loc_info.get("ip"):
        bundle["ip"] = {"ip": loc_info.get("ip"), "city": loc_info.get("city"),
                        "region": loc_info.get("region"),
                        "lat": lat, "lon": lon}
    loc_id = ""
    if hit:
        loc_id = str(hit.get("id") or "")
        bundle["location"] = dict(hit)
        bundle["location"]["source"] = source
        lat = lat or str(hit.get("lat") or "")
        lon = lon or str(hit.get("lon") or "")
    elif lat and lon:
        # IP 只给到经纬度（静态表未收录该地名）：和风 v7 允许 location=经度,纬度
        loc_id = f"{_fmt_coord(lon)},{_fmt_coord(lat)}"
        bundle["location"] = {
            "name": q or "本机 IP 定位",
            "adm1": str(loc_info.get("region") or ""),
            "adm2": "",
            "country": "中国",
            "lat": _fmt_coord(lat),
            "lon": _fmt_coord(lon),
            "source": "ip-coord",
        }
        bundle["errors"].append(
            "本机 IP 所在地未收录进内置城市表，已用 IP 经纬度直接查询天气。")
    elif q:
        bundle["errors"].append(
            f"内置城市表未收录「{q}」，可换更完整的名称（如「杭州市」）重试。")
        bundle["location"] = {"name": q}
    else:
        bundle["errors"].append(
            "未能通过本机 IP 识别所在城市（IP 归属地接口不可用），"
            "可直接输入城市名，如 \\@weather 上海")
    if not loc_id:
        bundle["errors"].append("未取到 LocationID，也没有可用的经纬度，无法读取天气数据。")
        return bundle

    def _safe(key: str, fn) -> None:
        try:
            data = fn()
            if isinstance(data, dict) and not QWeatherClient._ok(data):
                bundle["errors"].append(
                    f"{key}：接口返回 {data.get('code')}（可能未订阅该数据或 KEY 权限不足）")
                return
            bundle[key] = data
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"{key} 获取失败：{exc}")

    _safe("now", lambda: client.now(loc_id))
    _safe("daily", lambda: client.daily(loc_id, 3))
    _safe("indices", lambda: client.indices(loc_id))
    # 预警与空气质量：v7 接口已废弃，只能用新版「经纬度」接口
    if lat and lon:
        _safe("warning", lambda: client.warning(lat, lon))
        _safe("air", lambda: client.air(lat, lon))

    today = datetime.now()
    if "minutely" in opts and lat and lon:
        _safe("minutely", lambda: client.minutely(f"{lon},{lat}"))
        bundle["extras"]["minutely"] = bundle.pop("minutely", None)
    if "astronomy" in opts:
        d = _yyyymmdd(today)
        sun = moon = None
        try:
            sun = client.astronomy_sun(loc_id, d)
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"天文：{exc}")
        try:
            moon = client.astronomy_moon(loc_id, d)
        except Exception:  # noqa: BLE001
            moon = None
        bundle["extras"]["astronomy"] = {"sun": sun, "moon": moon}
    if "solar" in opts and lat and lon:
        try:
            bundle["extras"]["solar"] = client.solar(lat, lon)
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"太阳辐射：{exc}")
    if "historical" in opts:
        d = _yyyymmdd(today - timedelta(days=1))
        try:
            bundle["extras"]["historical"] = client.historical(loc_id, d)
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"时光机：{exc}")
    if "tropical" in opts:
        try:
            storms = client.storm_list(today.year)
            active = [s for s in (storms.get("storm") or [])
                      if str(s.get("isActive")) == "1"]
            pick = active or (storms.get("storm") or [])[:3]
            tracks = []
            for s in pick[:2]:
                try:
                    tracks.append(client.storm_track(str(s.get("id") or "")))
                except Exception:  # noqa: BLE001
                    pass
            bundle["extras"]["tropical"] = {"list": pick, "tracks": tracks}
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"热带气旋：{exc}")
    if "ocean" in opts:
        try:
            bundle["extras"]["ocean"] = client.ocean_tide(loc_id, _yyyymmdd(today))
        except Exception as exc:  # noqa: BLE001
            bundle["errors"].append(f"海洋数据：{exc}")
    return bundle


# ================================================================ 渲染
def _esc(s: Any) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _cell(text: str, sub: str = "", style: str = "") -> str:
    base = ("padding:6px 8px;text-align:center;color:#2b2b33;"
            "font-size:12px;background:#f7f8fd;")
    return (f'<td style="{base}{style}">{text}'
            f'<div style="color:#9aa0ac;font-size:11px;margin-top:2px;">{sub}</div></td>')


def _air_view(air_block: Dict[str, Any]) -> Dict[str, Any]:
    """把空气质量数据规整为统一视图，兼容新版 v1 与旧版 v7。

    新版：{"indexes": [{code, aqiDisplay, category, primaryPollutant}...],
           "pollutants": [{code, concentration:{value}}...]}
    旧版：{"now": {"aqi", "category", "pm2p5", "pm10", "primary"}}
    """
    view: Dict[str, Any] = {}
    indexes = air_block.get("indexes") or []
    if indexes:
        # 优先中国标准（code 形如 cn-mee），否则退到第一个
        pick = next((i for i in indexes
                     if str(i.get("code") or "").lower().startswith("cn")),
                    indexes[0])
        view["aqi"] = pick.get("aqiDisplay") or pick.get("aqi") or ""
        view["category"] = pick.get("category") or ""
        view["primary"] = ((pick.get("primaryPollutant") or {}).get("name")) or ""
        for p in air_block.get("pollutants") or []:
            code = str(p.get("code") or "")
            val = (p.get("concentration") or {}).get("value")
            if code == "pm2p5":
                view["pm25"] = val
            elif code == "pm10":
                view["pm10"] = val
        return view
    now = air_block.get("now") or {}
    if now:
        view["aqi"] = now.get("aqi") or ""
        view["category"] = now.get("category") or ""
        view["primary"] = now.get("primary") or ""
        view["pm25"] = now.get("pm2p5") or ""
        view["pm10"] = now.get("pm10") or ""
    return view


def render_weather_html(bundle: Dict[str, Any], accent: str = "#6c8ef5") -> str:
    """渲染 HTML5 天气卡片（表格式布局，Qt 富文本子集可正常显示）。"""
    loc = bundle.get("location") or {}
    name = _esc(loc.get("name") or "本机")
    adm = _esc(" ".join(x for x in (loc.get("adm2"), loc.get("adm1")) if x))
    now = (bundle.get("now") or {}).get("now") or {}
    daily = (bundle.get("daily") or {}).get("daily") or []
    air = _air_view(bundle.get("air") or {})
    warn_block = bundle.get("warning") or {}
    # 新版结构 {"alerts": [...]}，旧版 v7 为 {"warning": [...]}
    warns = (warn_block.get("alerts") if "alerts" in warn_block
             else warn_block.get("warning")) or []
    indices = bundle.get("indices") or {}
    if isinstance(indices, dict):
        indices = indices.get("daily") or []
    extras = bundle.get("extras") or {}

    out: List[str] = []
    out.append('<div style="font-family:\'Microsoft YaHei UI\',sans-serif;">')
    # 标题（定位来源在副标题里标出来，方便确认查的是哪儿）
    _loc = bundle.get("location") or {}
    _src = {"ip-table": "本机 IP 定位",
            "ip-coord": "本机 IP 经纬度",
            "table": ""}.get(str(_loc.get("source") or ""), "")
    _sub = " · ".join(x for x in (adm, _src) if x)
    out.append(
        f'<div style="background:{accent};color:#fff;border-radius:12px;'
        f'padding:10px 14px;">'
        f'<div style="font-size:15px;font-weight:600;">{name} · '
        f'{_esc(now.get("text") or "—")}</div>'
        f'<div style="font-size:11px;opacity:0.85;">{_sub} · 更新 {_esc(now.get("obsTime", "")[:16].replace("T", " "))}'
        f' · 数据来源 和风天气</div></div>')

    # 实况
    temp = _esc(now.get("temp") or "—")
    feels = _esc(now.get("feelsLike") or "—")
    wind = f'{_esc(now.get("windDir") or "")} {_esc(now.get("windScale") or "")}级'
    out.append(
        '<table border="0" cellspacing="4" cellpadding="0" width="100%"'
        ' style="margin-top:8px;"><tr>'
        f'<td width="38%" style="background:#f7f8fd;border-radius:10px;padding:10px;'
        f'text-align:center;"><div style="font-size:30px;color:{accent};'
        f'font-weight:600;">{temp}°</div>'
        f'<div style="color:#6b7280;font-size:12px;">{_esc(now.get("text") or "")}</div></td>'
        '<td style="padding-left:8px;">'
        '<table border="0" cellspacing="4" cellpadding="0" width="100%"><tr>'
        + _cell(f"体感 {feels}°", "Feels like")
        + _cell(f'{_esc(now.get("humidity") or "—")}%', "相对湿度")
        + "</tr><tr>"
        + _cell(_esc(wind.strip() or "—"), f'{_esc(now.get("windSpeed") or "")} km/h')
        + _cell(f'{_esc(now.get("pressure") or "—")}', "气压 hPa")
        + "</tr></table></td></tr></table>")

    # 预警
    if warns:
        out.append('<div style="margin-top:8px;background:#fff1f2;border-radius:10px;'
                   'padding:8px 10px;color:#b42318;font-size:12px;">')
        for w in warns[:3]:
            # 新版字段 headline/description/eventType.name/severity，兼容旧版 title/text
            title = _esc(w.get("headline") or w.get("title") or "预警")
            text = _esc((w.get("description") or w.get("text") or "")[:60])
            etype = _esc((w.get("eventType") or {}).get("name")
                         or w.get("typeName") or "")
            level = _esc(w.get("level") or w.get("severity") or "")
            out.append(
                f'<div><b>{title}</b> · {text}'
                f'<span style="color:#9aa0ac;">（{etype} {level}）</span></div>')
        out.append("</div>")

    # 空气质量
    if air:
        out.append('<div style="margin-top:8px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;color:#2b2b33;">')
        primary = str(air.get("primary") or "").strip()
        out.append(
            f'空气质量 AQI <b style="color:{accent};">{_esc(air.get("aqi") or "—")}</b>'
            f' · {_esc(air.get("category") or "")} · PM2.5 {_esc(air.get("pm25") or "—")}'
            f' · PM10 {_esc(air.get("pm10") or "—")}'
            + (f' · {_esc(primary)}' if primary else ""))
        out.append("</div>")

    # 预报
    if daily:
        out.append('<div style="margin-top:10px;color:#6b7280;font-size:12px;">'
                   f'未来 {len(daily)} 天预报</div>')
        out.append('<table border="0" cellspacing="4" cellpadding="0" width="100%"><tr>')
        for d in daily[:7]:
            out.append(_cell(
                f'{_esc(d.get("textDay"))}',
                f'{_esc(d.get("tempMin"))}°~{_esc(d.get("tempMax"))}°'))
        out.append("</tr><tr>")
        for d in daily[:7]:
            out.append(f'<td style="padding:2px 8px;text-align:center;color:#9aa0ac;'
                       f'font-size:11px;">{_esc(str(d.get("fxDate") or "")[5:])}</td>')
        out.append("</tr></table>")

    # 生活指数
    if indices:
        chips = "".join(
            f'<span style="display:inline-block;background:#f7f8fd;border-radius:8px;'
            f'padding:4px 8px;margin:2px 4px 2px 0;color:#3a4a80;font-size:11px;">'
            f'{_esc(i.get("name"))} {_esc(i.get("level"))} · {_esc(i.get("category"))}</span>'
            for i in indices[:8])
        out.append('<div style="margin-top:10px;color:#6b7280;font-size:12px;">'
                   '生活指数</div>')
        out.append(f'<div style="margin-top:2px;">{chips}</div>')

    # 额外模块
    if extras.get("minutely"):
        mm = extras["minutely"] or {}
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["minutely"]}</b>：{_esc(mm.get("summary") or "")}')
        for m in (mm.get("minutely") or [])[:6]:
            out.append(f'<div style="color:#6b7280;">{_esc(str(m.get("fxTime"))[11:16])} '
                       f'降水 {_esc(m.get("precip"))} mm · {_esc(m.get("type"))}</div>')
        out.append("</div>")
    if extras.get("astronomy"):
        astro = extras["astronomy"] or {}
        sun = astro.get("sun") or {}
        moon = astro.get("moon") or {}
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["astronomy"]}</b><br>'
                   f'日出 {_esc(sun.get("sunrise"))} · 日落 {_esc(sun.get("sunset"))} · '
                   f'正午太阳高度 {_esc(sun.get("solarElevation"))}°')
        if moon:
            out.append(f'<br>月出 {_esc(moon.get("moonrise"))} · 月落 '
                       f'{_esc(moon.get("moonset"))} · 月相 {_esc(moon.get("moonPhase"))}')
        out.append("</div>")
    if extras.get("solar"):
        fc = (extras["solar"] or {}).get("forecasts") or []
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["solar"]}</b>（逐小时 GHI 总水平面辐照）')
        for f in fc[:8]:
            ghi = (f.get("ghi") or {}).get("value")
            out.append(f'<div style="color:#6b7280;">{_esc(str(f.get("forecastTime"))[11:16])} '
                       f'GHI {_esc(ghi)} W/m² · 高度角 '
                       f'{_esc((f.get("solarAngle") or {}).get("elevation"))}°</div>')
        out.append("</div>")
    if extras.get("historical"):
        wd = (extras["historical"] or {}).get("weatherDaily") or {}
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["historical"]}</b> {_esc(wd.get("date"))}：'
                   f'{_esc(wd.get("tempMin"))}°~{_esc(wd.get("tempMax"))}° · '
                   f'降水 {_esc(wd.get("precip"))} mm · 湿度 {_esc(wd.get("humidity"))}%')
        out.append("</div>")
    if extras.get("tropical"):
        tr = extras["tropical"] or {}
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["tropical"]}</b>')
        storms = tr.get("list") or []
        if not storms:
            out.append('<div style="color:#6b7280;">当前没有活跃台风。</div>')
        for s in storms[:5]:
            out.append(f'<div style="color:#2b2b33;">{_esc(s.get("name"))}'
                       f'（{_esc(s.get("id"))}）'
                       f'{"活跃" if str(s.get("isActive")) == "1" else "已停编"}</div>')
        for t in (tr.get("tracks") or []):
            for p in ((t or {}).get("track") or [])[:4]:
                out.append(f'<div style="color:#6b7280;">{_esc(str(p.get("time"))[5:16])} '
                           f'{_esc(p.get("type"))} · 风速 {_esc(p.get("windSpeed"))} km/h · '
                           f'气压 {_esc(p.get("pressure"))} hPa</div>')
        out.append("</div>")
    if extras.get("ocean"):
        tide = extras["ocean"] or {}
        out.append('<div style="margin-top:10px;background:#f7f8fd;border-radius:10px;'
                   'padding:8px 10px;font-size:12px;">')
        out.append(f'<b>{OPT_TITLES["ocean"]}</b>')
        tb = tide.get("tideTable") or []
        if not tb:
            out.append('<div style="color:#9aa0ac;">该地区没有潮汐观测站数据'
                       '（潮汐需传观测站 LocationID）。</div>')
        for t in tb[:6]:
            out.append(f'<div style="color:#6b7280;">{_esc(str(t.get("fxTime"))[5:16])} '
                       f'{"满潮" if str(t.get("type")) == "H" else "干潮"} '
                       f'{_esc(t.get("height"))} m</div>')
        out.append("</div>")

    # Windy
    lat = str(loc.get("lat") or "")
    lon = str(loc.get("lon") or "")
    windy = (f"https://www.windy.com/?{lat},{lon},8" if lat and lon
             else "https://www.windy.com/")
    out.append(
        f'<div style="margin-top:10px;font-size:12px;">'
        f'<a href="{windy}" style="color:{accent};text-decoration:none;">'
        f'在 Windy.com 查看动态天气图（{name}）</a></div>')

    # 错误/降级提示
    for err in (bundle.get("errors") or [])[:5]:
        out.append(f'<div style="margin-top:6px;color:#9aa0ac;font-size:11px;">'
                   f'{_esc(err)}</div>')

    out.append("</div>")
    return "".join(out)
