"""transient_service.py — 巡天与天文科研（`#tothestars` 的程序侧取数）

需求（用户反馈「tothestars 也说查不到」）：Rochester 新星表与 VSX 变星表都是
**程序能抓 / 能查**的数据，之前却全交给模型 → 模型只能写「我核查不了」「无法判定」。
现在由程序：

  1. 解析用户坐标（时角式 / 十进制都支持）；
  2. 抓 **Rochester 新星表**（https://www.rochesterastronomy.org/novae.html），
     按 **±10 角秒**比对，命中即输出可能新星；
  3. 按 **10 角分**半径检索 **VSX**，且**双源互验**（用户 2026-09-23 规范：
     「VSX 网站和 VizieR 双向验证，两个存在一个即可认为命中」）：
     源① **VizieR asu-tsv**（``B/vsx``，``-sort=_r`` 服务端按角距升序 + 取值上限 1000
     条，修掉「坐标处那顆被截断」的老 bug）；源② **VSX 官网 VOTable**
     （``view=query.votable``，最新、多出 AUID 与星座）。两源合并去重后按角距升序
     展示最近的 30 条，每行标注来源；**两源都空**才算「未命中」；
  4. 生成 **TNS / ALeRCE / CDS** 三个「只给链接」的网址（模板拼接、URL 编码统一，
     不再让模型手写编码）。

TNS / ALeRCE 有反机器人机制，程序**不代查**（与 SKILL.md 约定一致）。
对外：``parse_coords`` / ``nova_hits`` / ``vsx_lookup`` / ``build_links`` /
``build_transient_report`` / ``render_transient_text``。
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AI_DeskMate.Transient")

NOVAE_URL = "https://www.rochesterastronomy.org/novae.html"
VSX_SEARCH_URL = "https://vsx.aavso.org/index.php?view=search.top"
VIZIER_TSV = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
_UA = "Celestia-AssistantAI/1.0"
#: Rochester 新星表：方圆 10 角秒内算命中（用户规范）
NOVA_MATCH_ARCSEC = 10.0
#: VSX 检索半径：10 角分（用户规范）
VSX_RADIUS_ARCMIN = 10.0


# ================================================================ 坐标
_RA_RE = re.compile(
    r"(\d{1,2})\s*[h:：\s]\s*(\d{1,2})\s*[m:：'′\s]\s*([\d.]+)\s*[s\"″]?")
_DEC_RE = re.compile(
    r"([+\-−])\s*(\d{1,3})\s*[d:：°\s]\s*(\d{1,2})\s*[m:：'′\s]\s*([\d.]+)"
    r"\s*[s\"″]?")


def parse_coords(text: str) -> Optional[Dict[str, Any]]:
    """从提示词里解析坐标 → ``{"ra_deg","dec_deg","ra_sex","dec_sex"}``。

    支持：`09 03 36.36 +02 24 20.6`、`09:03:36.36 +02:24:20.6`、
    `09h 03m 36.36s，+02° 24′ 20.6″`、`RA=09:03:36.36 DEC=+02:24:20.6`、
    十进制度 `135.9015 2.40572`。找不到返回 ``None``。
    """
    s = str(text or "")
    # ① 时角式：RA 与 DEC **分开找**（中间可能隔着 "Decl. =" / "，" / 空格）
    for m in _RA_RE.finditer(s):
        window = s[m.end():m.end() + 90]
        md = _DEC_RE.search(window)
        if not md:
            continue
        try:
            ra_deg = ((float(m.group(1)) + float(m.group(2)) / 60.0
                       + float(m.group(3)) / 3600.0) * 15.0)
            sign = -1.0 if md.group(1) in ("-", "−") else 1.0
            dec_deg = sign * (float(md.group(2)) + float(md.group(3)) / 60.0
                              + float(md.group(4)) / 3600.0)
            if 0 <= ra_deg < 360 and -90 <= dec_deg <= 90:
                return _coord_result(ra_deg, dec_deg)
        except Exception:  # noqa: BLE001
            continue
    # ② 十进制度：RA=… DEC=… 或裸的两个数字
    m2 = re.search(
        r"(?:ra|ra_j2000|α|alpha)\s*[=:]?\s*(\d{1,3}(?:\.\d+)?)\s*[,，\s]\s*"
        r"(?:dec|decl|δ|delta)\s*[=:]?\s*([+\-−]?\d{1,2}(?:\.\d+)?)", s, re.I)
    if not m2:
        m2 = re.search(r"\b(\d{1,3}\.\d{3,})\s+([+\-−]?\d{1,2}\.\d{3,})\b", s)
    if m2:
        try:
            ra_deg = float(m2.group(1))
            dec_deg = float(m2.group(2).replace("−", "-"))
            if 0 <= ra_deg < 360 and -90 <= dec_deg <= 90:
                return _coord_result(ra_deg, dec_deg)
        except Exception:  # noqa: BLE001
            pass
    return None


def _coord_result(ra_deg: float, dec_deg: float) -> Dict[str, Any]:
    ra_sex, dec_sex = sexagesimal(ra_deg, dec_deg)
    return {"ra_deg": ra_deg, "dec_deg": dec_deg,
            "ra_sex": ra_sex, "dec_sex": dec_sex}


def sexagesimal(ra_deg: float, dec_deg: float) -> Tuple[str, str]:
    """十进制度 → 时角式字符串（`09:03:36.36` / `+02:24:20.6`）。"""

    def _dms_hra(value: float, width: int) -> Tuple[int, int, float]:
        hh = value % (24.0 if width == 2 else 360.0) if width == 2 else value
        # RA 用小时制已在上层处理；这里通用拆分（对 RA 传小时、对 Dec 传度）
        h = int(hh)
        m_f = (hh - h) * 60.0
        m = int(m_f)
        s = (m_f - m) * 60.0
        if s >= 59.95:                 # 四舍五入进位
            s = 0.0
            m += 1
        if m >= 60:
            m -= 60
            h += 1
        return h, m, s

    ra_h = (ra_deg % 360.0) / 15.0
    rh, rm, rs = _dms_hra(ra_h, 2)
    sign = "-" if dec_deg < 0 else "+"
    dd, dm, ds = _dms_hra(abs(dec_deg), 0)
    return (f"{rh:02d}:{rm:02d}:{rs:05.2f}",
            f"{sign}{dd:02d}:{dm:02d}:{ds:04.1f}")


def separation_arcsec(ra1: float, dec1: float,
                      ra2: float, dec2: float) -> float:
    """球面角距（角秒）——Rochester 的 10″ 命中判定用它，不能用平面差。"""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2))
    cos_sep = max(-1.0, min(1.0, cos_sep))
    return math.degrees(math.acos(cos_sep)) * 3600.0


# ================================================================ HTTP
def _fetch_text(url: str, timeout: float = 30.0) -> str:
    """GET 网页 → 去标签纯文本（gzip 兜底）。失败抛可读异常。"""
    from llm_client import html_to_text
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Encoding": "gzip, identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
            enc = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络不可达 {exc.reason}") from exc
    if enc == "gzip" or blob[:2] == b"\x1f\x8b":
        try:
            import gzip as _gzip
            blob = _gzip.decompress(blob)
        except Exception:  # noqa: BLE001
            pass
    for ce in ("utf-8", "gb18030"):
        try:
            html = blob.decode(ce)
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        html = blob.decode("utf-8", errors="replace")
    return html_to_text(html)


# ================================================================ ① Rochester 新星
_RA_RE2 = re.compile(r"(\d{1,2})\s*h\s*(\d{1,2})\s*m\s*([\d.]+)\s*s?", re.I)
_DEC_RE2 = re.compile(
    r"Decl\.\s*=\s*([+\-−])\s*(\d{1,3})[°d]\s*(\d{1,2})['′m]\s*([\d.]+)[\"″s]?",
    re.I)
_NAME_RE = re.compile(r"(AT\s?\d{4}\s?[A-Za-z]{2,4}|SN\s?\d{4}[A-Za-z]{0,3}"
                      r"|PNV\s?J[\d+]+|[12]\d{3}\s\d{1,2})")
_MAG_RE = re.compile(r"Mag\s+([\d.>]+(?::[\d/]+)*)")


def nova_hits(ra_deg: float, dec_deg: float,
              max_sep_arcsec: float = NOVA_MATCH_ARCSEC,
              timeout: float = 60.0) -> Dict[str, Any]:
    """抓 Rochester 新星表，与目标坐标比对（±10″ 内算命中）。"""
    text = _fetch_text(NOVAE_URL, timeout=timeout)
    # 该站把秒写成「40s.938」（s 后面跟小数）→ 先归一成 40.938 再解析
    text = re.sub(r"(\d)\s*s\s*\.\s*(\d+)", r"\1.\2", text)
    hits: List[Dict[str, Any]] = []
    total = 0
    for m in _RA_RE2.finditer(text):
        window = text[m.end():m.end() + 120]
        md = _DEC_RE2.search(window)
        if not md:
            continue
        total += 1
        ra_h, ra_m, ra_s = float(m.group(1)), float(m.group(2)), float(m.group(3))
        sign = -1.0 if md.group(1) in ("-", "−") else 1.0
        d_d, d_m, d_s = float(md.group(2)), float(md.group(3)), float(md.group(4))
        n_ra = (ra_h + ra_m / 60.0 + ra_s / 3600.0) * 15.0
        n_dec = sign * (d_d + d_m / 60.0 + d_s / 3600.0)
        sep = separation_arcsec(ra_deg, dec_deg, n_ra, n_dec)
        # 名字：在位置片段**之前**找最近的编号（AT2026xxx / PNV J… / 年+序号）
        head = text[max(0, m.start() - 260):m.start()]
        names = _NAME_RE.findall(head)
        name = names[-1].strip() if names else "(未编号)"
        chunk = text[m.end() + md.end():m.end() + md.end() + 220]
        mag_m = _MAG_RE.search(chunk)
        if sep <= max_sep_arcsec:
            hits.append({
                "name": name, "sep_arcsec": round(sep, 1),
                "ra": f"{ra_h:02.0f}h{ra_m:02.0f}m{ra_s:g}s",
                "dec": f"{md.group(1)}{d_d:02.0f}°{d_m:02.0f}′{d_s:g}″",
                "mag": (mag_m.group(1) if mag_m else ""),
            })
    hits.sort(key=lambda h: h["sep_arcsec"])
    return {"found": bool(hits), "hits": hits, "total": total,
            "url": NOVAE_URL,
            "max_sep_arcsec": max_sep_arcsec}


# ================================================================ ② VSX（双源互验）
#: VizieR 单次取值上限：**不要**把展示条数（limit）直接当 ``-out.max`` 用 —— 见 _vsx_vizier_lookup。
VSX_MAX_FETCH = 1000
#: VSX 官网「按坐标检索」：返回 VOTable（字段见 vsx_site_lookup）
VSX_SITE_VOTABLE = "https://vsx.aavso.org/index.php?view=query.votable"
#: VSX 官网「单目标查询」：给官网独有条目补详情页 OID（见 _vsx_site_oid）
VSX_SITE_OBJECT = "https://vsx.aavso.org/index.php?view=api.object"
#: 详情页 OID 补查上限（官网独有条目最多补这么多个，避免请求风暴）
VSX_OID_MAX_LOOKUP = 5
#: 双源判定「同一颗星」的容许角距（角秒）／名称归一后相同也算（见 _merge_vsx_rows）
VSX_MERGE_ARCSEC = 2.0


def _http_text(url: str, timeout: float) -> str:
    """GET → 文本（gzip 兜底）；失败抛可读异常。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept-Encoding": "gzip, identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
            enc = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络不可达 {exc.reason}") from exc
    if enc == "gzip" or blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except Exception:  # noqa: BLE001
            pass
    return blob.decode("utf-8", errors="replace")


def _vsx_vizier_lookup(ra_deg: float, dec_deg: float,
                       radius_arcmin: float = VSX_RADIUS_ARCMIN,
                       limit: int = 30, timeout: float = 90.0) -> Dict[str, Any]:
    """**源① VizieR**（``B/vsx``）按 10 角分半径检索，按角距升序返回（取回条数见 ``count``）。

    返回每条：``Dist.'（角分）/ Name / Coords(J2000) / Var. type / Period (d) /
    Mag. range / 详情页网址（OID）``。**AUID / 星座名不在 VizieR 表内**（由源②补齐，
    见 :func:`vsx_lookup` 的双源合并）。

    需求（用户 2026-09-23 反馈「VSX 查变星查不到部分小项目命名的」）：
    实测（10 角分内共 65 条）根因**不是表缺数据**，而是取数方式 ——
      * VizieR 默认按**目录内部顺序（recno）**返回，**不是**按距离
        （实测前几条 ``_r`` = 0.1619 / 0.1640 / 0.1473 / 0.1424 度，乱序）；
      * 旧代码把**展示条数**当 ``-out.max``（30）→ 服务端先砍成 30 条
        （属于半径内"随便哪 30 条"），客户端再按距离排序 → 连**离目标最近的几颗**
        都被截掉了：坐标处 0.00′ 的 ``VSSP J212102.12+434137.4``（OID 2387576）
        就此消失，列表起点被推到 1.85′。
    修法（两条缺一不可）：
      ① ``-sort=_r``：让**服务端按距离升序**返回（实测有效，第一条即 0.00′ 那颗）；
      ② ``-out.max`` 放大到 :data:`VSX_MAX_FETCH`（不再等于展示条数）——
         即使 ① 哪天失效，客户端排序丢掉的也只是**最远**的那批，而非最近的。
    客户端仍会再排一次序（兜底）；另返回 ``count``（取回条数）与 ``truncated``
    （是否触到取值上限），由渲染层如实告知「更远的未列出」。
    """
    cap = max(int(limit), VSX_MAX_FETCH)
    params = {"-source": "B/vsx",
              "-c": f"{ra_deg:.6f} {dec_deg:+.6f}",
              "-c.geom": "r",
              "-c.bd": f"{radius_arcmin / 60.0:.7f}",
              "-out": "**",
              "-sort": "_r",           # ① 服务端按距离升序（关键）
              "-out.max": str(cap)}    # ② 取值上限放大，别用 limit（关键）
    url = VIZIER_TSV + "?" + urllib.parse.urlencode(params)
    body = _http_text(url, timeout)
    rows = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(rows) < 4:
        return {"found": False, "rows": [], "url": VSX_SEARCH_URL, "count": 0,
                "truncated": False, "max_fetch": cap,
                "radius_arcmin": radius_arcmin}
    header = rows[0].split("\t")
    idx = {name.strip(): i for i, name in enumerate(header)}

    def _cell(row: List[str], key: str) -> str:
        i = idx.get(key)
        return (row[i].strip() if i is not None and i < len(row) else "")

    out: List[Dict[str, Any]] = []
    for line in rows[3:]:                       # 0=表头 1=单位 2=分隔线
        cells = line.split("\t")
        if len(cells) < len(header) - 3:
            continue
        name = _cell(cells, "Name")
        if not name:
            continue
        try:
            r_deg = float(_cell(cells, "RAJ2000"))
            d_deg = float(_cell(cells, "DEJ2000"))
        except Exception:  # noqa: BLE001
            continue
        ra_sex, dec_sex = sexagesimal(r_deg, d_deg)
        dist_deg = float(_cell(cells, "_r") or 0) or separation_arcsec(
            ra_deg, dec_deg, r_deg, d_deg) / 3600.0
        oid = _cell(cells, "OID")
        mag_max, mag_min = _cell(cells, "max"), _cell(cells, "min")
        out.append({
            "dist_arcmin": round(dist_deg * 60.0, 2),
            "name": name,
            "coords": f"{ra_sex} {dec_sex}",
            "vtype": _cell(cells, "Type") or "—",
            "period_d": _fmt_period(_cell(cells, "Period")),
            "mag_range": f"{mag_max or '—'} ~ {mag_min or '—'}",
            "oid": oid,
            "url": (f"https://vsx.aavso.org/index.php?view=detail.top&oid={oid}"
                    if oid else VSX_SEARCH_URL),
        })
    # 兜底再排一次：服务端已按 -sort=_r 升序。这里返回**取回的全部行**，
    # 截断展示交给 vsx_lookup（先与源②合并，避免合并前就丢条目）。
    out.sort(key=lambda r: r["dist_arcmin"])
    return {"found": bool(out), "rows": out, "count": len(out),
            "truncated": len(out) >= cap, "max_fetch": cap,
            "url": VSX_SEARCH_URL,
            "radius_arcmin": radius_arcmin}


#: 十进制坐标串（官网 VOTable 在 ``format=d`` 下给的是 ``"320.25883,43.69372"``）
_DEC_COORD_RE = re.compile(
    r"^\s*([+\-−]?\d+(?:\.\d+)?)\s*[,;]\s*([+\-−]?\d+(?:\.\d+)?)\s*$")


def _parse_site_radec(text: str) -> Optional[Tuple[float, float]]:
    """坐标串 → 十进制度（**两种写法都认**）。

    * 十进制逗号式：``"320.25883000,43.69372000"`` —— 官网 VOTable 在 ``format=d``
      下的 ``radec2000`` 就是这个（踩过坑：只认时角式会让**每一行都被跳过**，
      表现为「官网取回 0 条」且不报错）；
    * 时角式：``"21 21 02.12 +43 41 37.4"``（``format=s``）或
      ``"21:21:02.12 +43:41:37.4"``（本模块 :func:`sexagesimal` 的产物）。
    """
    s = str(text or "")
    m_dec = _DEC_COORD_RE.match(s)
    if m_dec:
        try:
            ra = float(m_dec.group(1).replace("−", "-"))
            dec = float(m_dec.group(2).replace("−", "-"))
        except Exception:  # noqa: BLE001
            return None
        if 0 <= ra <= 360 and -90 <= dec <= 90:
            return (ra, dec)
        return None
    m = _RA_RE.search(s)
    if not m:
        return None
    md = _DEC_RE.search(s[m.end():m.end() + 40])
    if not md:
        return None
    try:
        ra = ((float(m.group(1)) + float(m.group(2)) / 60.0
               + float(m.group(3)) / 3600.0) * 15.0)
        sign = -1.0 if md.group(1) in ("-", "−") else 1.0
        dec = sign * (float(md.group(2)) + float(md.group(3)) / 60.0
                      + float(md.group(4)) / 3600.0)
    except Exception:  # noqa: BLE001
        return None
    return (ra, dec)


def _fmt_period(value: str) -> str:
    """周期格式化：``"4.264500000000"`` → ``"4.2645"``（去尾零；非数字原样保留）。"""
    t = str(value or "").strip()
    if not t:
        return "—"
    try:
        f = float(t)
    except Exception:  # noqa: BLE001
        return t
    if f <= 0:
        return t
    return f"{f:.8f}".rstrip("0").rstrip(".") or t


def _mag_with_band(row: Dict[str, str], mag_key: str, band_key: str) -> str:
    """官网的 ``maxMag`` + ``maxPass`` → ``"13.31 g"``（带号单独一列，需拼起来）。"""
    mag = (row.get(mag_key) or "").strip()
    band = (row.get(band_key) or "").strip()
    if not mag:
        return ""
    return f"{mag} {band}".strip()


def vsx_site_lookup(ra_deg: float, dec_deg: float,
                    radius_arcmin: float = VSX_RADIUS_ARCMIN,
                    timeout: float = 45.0) -> Dict[str, Any]:
    """**源② VSX 官网**（``vsx.aavso.org``）按 10 角分半径检索，按角距升序返回。

    需求（用户 2026-09-23）：「VSX 网站和 VizieR **双向验证**，两个存在一个即可认为
    命中」。VizieR 的 ``B/vsx`` 是**定期镜像**（可能落后官网），官网接口始终最新 ——
    两源都查、任一命中即为命中，见 :func:`vsx_lookup`。

    接口参数取自 AAVSO 论坛的官方规格帖
    （``archive.aavso.org/direct-web-query-vsxvsp``）：``coords``（``format=d``
    时按十进制度）、``geom=r``（圆）、``size`` + ``unit``（**1=度 / 2=角分 / 3=角秒**）、
    ``order=9``（按与中心的角距排序）、``filter=0,1``（0=确认变星，1=疑似变星）。
    ⚠️ 约束写错时服务端会**忽略约束返回整个目录**（实测 14 MB、十几分钟），
    因此坐标/半径必须写全；本函数只用于小半径检索。

    实测（2026-09-23，21 21 02.12 +43 41 37.4 / 10′）：1 秒返回 65 行，与 VizieR
    完全同数。VOTable 字段：``auid / name / const / radec2000 / varType /
    maxMag+maxPass / minMag+minPass / epoch / novaYr / period / specType / disc``。
    **没有 OID**（详情页网址需要 OID → 合并时从源①继承，或由 :func:`_vsx_site_oid`
    补查），也没有角距列 → 这里用 ``radec2000`` 自己算并排序。
    """
    # 注意：``VSX_SITE_VOTABLE`` 里已含 ``?view=query.votable``，参数里**不要**再写
    # ``view``（重复会出现两个 view 参数，官网解析失败 → 静默返回 0 条，实测踩过）。
    params = {
        # 赤纬**不要带正号**：实测十进制模式下 "+43.693722"（编码成 %2B43.693722）
        # 会被官网解析失败 → 静默返回 0 条；负号（南天）保留。
        "coords": f"{ra_deg:.6f} {dec_deg:.6f}",
        "format": "d",                     # 十进制坐标
              "geom": "r",                   # 圆
              "size": f"{radius_arcmin:g}",
              "unit": "2",                   # 2 = 角分
              "order": "9",                  # 9 = 按与中心角距排序
              "filter": "0,1"}               # 0 确认变星 + 1 疑似变星
    url = VSX_SITE_VOTABLE + "&" + urllib.parse.urlencode(params)
    body = _http_text(url, timeout)
    xml_text = body[body.find("<?xml"):] if "<?xml" in body else body
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"官网 VOTable 解析失败：{exc}") from exc
    fields = [f.get("id") or f.get("name") or "" for f in root.iter("FIELD")]
    out: List[Dict[str, Any]] = []
    for tr in root.iter("TR"):
        cells = ["".join(td.itertext()).strip() for td in tr.findall("TD")]
        if not cells or len(cells) != len(fields):
            continue
        raw = dict(zip(fields, cells))
        name = (raw.get("name") or "").strip()
        rd = _parse_site_radec(raw.get("radec2000") or "")
        if not name or not rd:
            continue
        r_deg, d_deg = rd
        ra_sex, dec_sex = sexagesimal(r_deg, d_deg)
        out.append({
            "dist_arcmin": round(
                separation_arcsec(ra_deg, dec_deg, r_deg, d_deg) / 60.0, 2),
            "name": name,
            "coords": f"{ra_sex} {dec_sex}",
            "vtype": (raw.get("varType") or "").strip() or "—",
            "period_d": _fmt_period(raw.get("period")),
            "mag_range": (f"{_mag_with_band(raw, 'maxMag', 'maxPass') or '—'}"
                          f" ~ {_mag_with_band(raw, 'minMag', 'minPass') or '—'}"),
            "auid": (raw.get("auid") or "").strip(),
            "const": (raw.get("const") or "").strip(),
            "oid": "",
            "url": VSX_SEARCH_URL,
        })
    out.sort(key=lambda r: r["dist_arcmin"])
    return {"found": bool(out), "rows": out, "count": len(out),
            "url": VSX_SEARCH_URL, "radius_arcmin": radius_arcmin, "api": url}


def _vsx_site_oid(name: str, timeout: float = 20.0) -> str:
    """官网单目标查询（``view=api.object`` + ``format=json``）取 OID。

    用途：官网独有条目（VizieR 镜像尚未收录，正是「小项目命名的」那类）也要给出
    可点的详情页网址。VOTable 里没有 OID，只能按名称回查；失败返回空串。
    """
    if not name:
        return ""
    url = VSX_SITE_OBJECT + "&ident=" + urllib.parse.quote(name) + "&format=json"
    try:
        obj = (json.loads(_http_text(url, timeout)) or {}).get("VSXObject") or {}
        return str(obj.get("OID") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("官网 OID 补查失败（%s）：%s", name, exc)
        return ""


def _norm_star_name(name: str) -> str:
    """星名归一（去空白 + 折叠大小写）——两源名称写法偶有空格差异。"""
    return re.sub(r"\s+", "", str(name or "")).casefold()


def _merge_vsx_rows(viz_rows: List[Dict[str, Any]],
                    site_rows: List[Dict[str, Any]],
                    timeout: float = 20.0) -> List[Dict[str, Any]]:
    """合并两源 → 统一行列表（**任一源命中即算命中**）。

    同一颗星的判定：**名称归一后相同**，或位置差 ≤ :data:`VSX_MERGE_ARCSEC`。
    以源①（VizieR，自带 OID 与角距）为骨架，用源②补 ``auid`` / ``const``；
    官网独有条目按名称回查 OID（最多 :data:`VSX_OID_MAX_LOOKUP` 个，避免请求风暴）。
    每行带 ``sources``（``["VizieR"]`` / ``["官网"]`` / 两者），便于结论里如实标注。
    """
    merged: List[Dict[str, Any]] = []
    used: set = set()
    site_pos = [_parse_site_radec(s.get("coords") or "") for s in site_rows]

    def _pick_site(row: Dict[str, Any]) -> Optional[int]:
        """在源②里找同一颗星（先按名称，再按 ≤2″ 的位置重合）。"""
        nm = _norm_star_name(row.get("name"))
        if nm:
            for i, s in enumerate(site_rows):
                if i not in used and _norm_star_name(s.get("name")) == nm:
                    return i
        rd = _parse_site_radec(row.get("coords") or "")
        if rd:
            for i, pos in enumerate(site_pos):
                if i in used or not pos:
                    continue
                if separation_arcsec(rd[0], rd[1], pos[0], pos[1]) <= VSX_MERGE_ARCSEC:
                    return i
        return None

    for v in viz_rows:
        row = dict(v)
        row.setdefault("auid", "")
        row.setdefault("const", "")
        row["sources"] = ["VizieR"]
        i = _pick_site(row)
        if i is not None:
            used.add(i)
            s = site_rows[i]
            row["auid"] = s.get("auid") or row["auid"]
            row["const"] = s.get("const") or row["const"]
            row["sources"] = ["VizieR", "官网"]
        merged.append(row)

    looked = 0
    for i, s in enumerate(site_rows):
        if i in used:
            continue
        row = dict(s)
        row["sources"] = ["官网"]
        if not row.get("oid") and looked < VSX_OID_MAX_LOOKUP:
            looked += 1
            oid = _vsx_site_oid(row.get("name") or "", timeout)
            if oid:
                row["oid"] = oid
                row["url"] = ("https://vsx.aavso.org/index.php?view=detail.top"
                              f"&oid={oid}")
        merged.append(row)
    merged.sort(key=lambda r: r["dist_arcmin"])
    return merged


def vsx_lookup(ra_deg: float, dec_deg: float,
               radius_arcmin: float = VSX_RADIUS_ARCMIN,
               limit: int = 30, timeout: float = 90.0) -> Dict[str, Any]:
    """:data:`#tothestars` 的 VSX 检索 —— **双源互验**，任一命中即算命中。

    需求（用户 2026-09-23）：
      ① 「VSX 查变星查不到部分小项目命名的」→ 根因是 VizieR 取数被截断
         （``-out.max`` 用展示条数，服务端按目录序先砍再客户端排序，最近的被丢），
         已由 :func:`_vsx_vizier_lookup` 的 ``-sort=_r`` + 取值上限放大修掉；
      ② 「VSX 网站和 VizieR 双向验证，两个存在一个即可认为命中」→ 本函数：
         源① :func:`_vsx_vizier_lookup`（VizieR ``B/vsx``，有 OID/角距）＋
         源② :func:`vsx_site_lookup`（官网 VOTable，**最新**，有 AUID/星座），
         合并去重后按角距升序返回最近的 ``limit`` 条；**两源都空**才报「未命中」。

    返回：``found`` / ``rows``（每行含 ``auid``/``const``/``oid``/``sources``）/
    ``count``（合并后总数）/ ``vizier_count`` / ``site_count``（各源取回条数）/
    ``errors``（某源失败说明；另一源仍可独立给结论）/ ``truncated`` / ``max_fetch``。
    """
    viz: Dict[str, Any] = {"found": False, "rows": [], "count": 0}
    site: Dict[str, Any] = {"found": False, "rows": [], "count": 0}
    errors: List[str] = []
    try:
        viz = _vsx_vizier_lookup(ra_deg, dec_deg, radius_arcmin, limit, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"VizieR B/vsx：{exc}")
        logger.warning("VSX 源① VizieR 检索失败：%s", exc)
    try:
        site = vsx_site_lookup(ra_deg, dec_deg, radius_arcmin, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"VSX 官网：{exc}")
        logger.warning("VSX 源② 官网检索失败：%s", exc)
    merged = _merge_vsx_rows(viz.get("rows") or [], site.get("rows") or [],
                             timeout=min(20.0, timeout))
    return {
        "found": bool(merged),
        "rows": merged[:limit],
        "count": len(merged),
        "vizier_count": int(viz.get("count") or 0),
        "site_count": int(site.get("count") or 0),
        "truncated": bool(viz.get("truncated")),
        "max_fetch": viz.get("max_fetch") or VSX_MAX_FETCH,
        "errors": errors,
        "url": VSX_SEARCH_URL,
        "radius_arcmin": radius_arcmin,
    }


# ================================================================ ③ 链接
def build_links(ra_deg: float, dec_deg: float,
                ra_sex: str, dec_sex: str) -> Dict[str, str]:
    """TNS / ALeRCE / CDS 的「只给链接」网址（URL 编码统一由程序生成）。"""
    tns = ("https://www.wis-tns.org/search?ra="
           + urllib.parse.quote(ra_sex, safe="")
           + "&decl=" + urllib.parse.quote(dec_sex, safe="")
           + "&radius=10&coords_unit=arcsec&include_frb=1")
    alerce = ("https://alerce.online/?ranking=1"
              f"&ra={ra_deg!r}&dec={dec_deg!r}"
              "&radius=50&count=false&page=1&perPage=20"
              "&sortBy=probability&sortDesc=true")
    cds = ("https://portal.cds.unistra.fr/?target="
           + urllib.parse.quote(ra_sex, safe="")
           + "%20" + urllib.parse.quote(dec_sex, safe=""))
    return {"tns": tns, "alerce": alerce, "cds": cds}


# ================================================================ 汇总
def build_transient_report(query: str) -> Dict[str, Any]:
    """`#tothestars` 程序侧取数：坐标解析 → Rochester 比对 → VSX 检索 → 链接。

    坐标解析失败时返回 ``{"coords": None}``（模型按原流程要坐标 / 走 SOHO 判断）。
    """
    coords = parse_coords(query)
    report: Dict[str, Any] = {"query": query, "coords": coords, "errors": []}
    if not coords:
        return report
    ra, dec = coords["ra_deg"], coords["dec_deg"]
    try:
        report["nova"] = nova_hits(ra, dec)
    except Exception as exc:  # noqa: BLE001
        report["nova"] = {"found": False, "hits": [], "url": NOVAE_URL,
                          "error": str(exc)}
        report["errors"].append(f"Rochester 新星表抓取失败：{exc}")
    try:
        report["vsx"] = vsx_lookup(ra, dec)
    except Exception as exc:  # noqa: BLE001
        report["vsx"] = {"found": False, "rows": [], "url": VSX_SEARCH_URL,
                         "error": str(exc)}
        report["errors"].append(f"VSX 检索失败：{exc}")
    report["links"] = build_links(ra, dec, coords["ra_sex"], coords["dec_sex"])
    return report


def render_transient_text(report: Dict[str, Any]) -> str:
    """核查结果 → 纯文本（注入给模型）。"""
    lines: List[str] = []
    coords = report.get("coords")
    if not coords:
        return ("【巡天核查（程序）】提示词里没有解析到坐标。"
                "若用户在问 SOHO/CCOR 彗星同星判断或 FTS 解析，请按技能说明处理；"
                "若是瞬变天体核查，请先向用户要坐标（时角式或十进制度）。")
    ra_sex, dec_sex = coords["ra_sex"], coords["dec_sex"]
    lines.append(f"【巡天核查（程序已查好，**直接采用**）】"
                 f"目标：RA {ra_sex} / Dec {dec_sex}"
                 f"（十进制 {coords['ra_deg']:.6f}°, {coords['dec_deg']:+.6f}°）")
    nova = report.get("nova") or {}
    if nova.get("error"):
        lines.append(f"① NOVA（Rochester）：抓取失败（{nova['error']}）→ 请写「需核对」"
                     f"并给出处 {NOVAE_URL}")
    elif nova.get("found"):
        lines.append(f"① NOVA（Rochester，±{nova['max_sep_arcsec']:g}″ 内比对）："
                     f"**命中 {len(nova['hits'])} 条** ——")
        for h in nova["hits"]:
            lines.append(f"    - {h['name']}：角距 {h['sep_arcsec']}″，"
                         f"位置 {h['ra']} {h['dec']}"
                         + (f"，星等 {h['mag']}" if h.get("mag") else ""))
        lines.append(f"    出处：{nova['url']}")
    else:
        lines.append(f"① NOVA（Rochester，±{nova['max_sep_arcsec']:g}″ 内比对）："
                     f"**未命中**（该表共解析 {nova.get('total', 0)} 条新星记录）"
                     f"——这是弱结论，如实说明即可。出处：{nova['url']}")
    vsx = report.get("vsx") or {}
    _verr = list(vsx.get("errors") or [])
    if vsx.get("error"):
        _verr.append(str(vsx["error"]))
    if vsx.get("found"):
        _shown = len(vsx["rows"])
        _head = (f"② VSX（10 角分半径，**双源互验**：VizieR B/vsx + VSX 官网，"
                 f"任一命中即算命中）：VizieR 取回 {vsx.get('vizier_count', '?')} 条 / "
                 f"官网取回 {vsx.get('site_count', '?')} 条，合并去重后 "
                 f"{vsx.get('count', _shown)} 条；下列为**按角距升序**的最近 {_shown} 条")
        if vsx.get("truncated"):
            _head += (f"（VizieR 侧已到单次取值上限 {vsx.get('max_fetch')}，"
                      "更远的未取回）")
        lines.append(_head + " —— 逐行原样输出，字段 Dist.' / Name / Coords(J2000) / "
                            "Var. type / Period (d) / Mag. range / AUID / 星座 / 来源 / "
                            "详情页：")
        lines.append("    注意：**第一行通常就是目标坐标处那颗（Dist.' ≈ 0.00）**，"
                     "必须把它写出来，不要只报后面角距更大的条目；"
                     "来源标「官网」的行是 VizieR 镜像尚未收录的条目，同样算命中。")
        for r in vsx["rows"]:
            _extra = ""
            if r.get("auid"):
                _extra += f" | AUID {r['auid']}"
            if r.get("const"):
                _extra += f" | {r['const']}"
            lines.append(f"    - {r['dist_arcmin']}′ | {r['name']} | "
                         f"{r['coords']} | {r['vtype']} | {r['period_d']} d | "
                         f"{r['mag_range']}{_extra} | "
                         f"[{'/'.join(r.get('sources') or [])}] | {r['url']}")
        if _verr:
            lines.append(f"    说明：本次 {'；'.join(_verr)} 失败，"
                         "本段结论仅来自另一个源（已如实标注）。")
    else:
        _why = "；".join(_verr) if _verr else "两个源都返回空"
        lines.append(f"② VSX（10 角分半径，VizieR + 官网**双源都查过**）：**未命中**"
                     f"（{_why}）——如实说明「VSX 两个源均未命中，可能是未编目的"
                     f"变星/瞬变」，不要猜类型。出处：{VSX_SEARCH_URL}")
    links = report.get("links") or {}
    if links:
        lines.append(f"③ TNS（只给链接，请点开自查）：{links['tns']}")
        lines.append(f"④ ZTF/ALeRCE（只给链接，请点开自查）：{links['alerce']}")
        lines.append(f"⑤ CDS 星图：{links['cds']}")
    lines.append("⑥ 以上 ①② 为程序实查结果**直接采用**；③④⑤ 为自查链接"
                 "（TNS/ALeRCE 有反机器人机制，程序不代查）。")
    return "\n".join(lines)
