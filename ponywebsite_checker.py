# -*- coding: utf-8 -*-
"""
ponywebsite_checker.py — \\@ponywebsite 小马网站连通性检测工具

做什么：
    读取 data/ponywebsite.txt（每行「名称<Tab 或空格>网址」，# 开头为注释），
    对每个站点并行做连通性检测：
      1) ICMP ping（默认 4 包），解析丢包率与最小/平均/最大往返延迟；
      2) ICMP 不通（被防火墙拦掉）时降级为 TCP 握手（443 → 80）测连接延迟；
      3) 站点较少时顺带做一次 HTTP(S) 状态码探测，确认服务是否真的活着。

设计要点：
    - 纯数据/IO，不含任何 QWidget，可在子线程执行；
    - 跨平台：Windows / Linux / macOS 的 ping 输出都能解析（含中文/英文回显）；
    - 命令失败、DNS 失败、超时都不会抛异常，只体现在结果里。

用法：
    \\@ponywebsite                全部站点
    \\@ponywebsite 呆站           只测名字/网址含「呆站」的站点
    \\@ponywebsite derpibooru.org 临时测一个网址（不用写进列表）
    \\@ponywebsite -n 10          ping 10 包再统计丢包（默认 4 包）
"""

from __future__ import annotations

import html
import locale
import os
import platform
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from config_loader import project_root

SITE_FILE_NAME = "ponywebsite.txt"
DEFAULT_COUNT = 4        # 每个站点 ping 几包
DEFAULT_TIMEOUT = 2.0    # 单包超时（秒）
MAX_WORKERS = 8          # 并行站点数
HTTP_TIMEOUT = 6.0       # HTTP 探测超时（秒）
MAX_COUNT = 20           # ping 包数上限，避免用户填 999 卡死
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

STATUS_OK = "在线"
STATUS_LOSS = "丢包"
STATUS_DOWN = "不通"
STATUS_UNKNOWN = "未知"

# ------------------------------------------------------------------ 站点清单
_URL_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s]*)?")
_HOST_ONLY_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s]*)?$")


def site_file() -> Path:
    """站点清单文件（优先使用配置里的数据目录）。"""
    try:
        from config_loader import ConfigLoader
        p = ConfigLoader.instance().data_dir
    except Exception:  # noqa: BLE001
        p = project_root() / "data"
    return Path(p) / SITE_FILE_NAME


def _split_line(line: str) -> Optional[Tuple[str, str]]:
    """把一行拆成 (名称, 网址)，支持多种分隔：Tab / 逗号 / 空格 / 直接写网址。"""
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if "\t" in line:
        head, tail = line.split("\t", 1)
    elif "," in line:
        head, tail = line.split(",", 1)
    else:
        m = _URL_RE.search(line)
        if m:
            head, tail = line[:m.start()], m.group(0)
        else:
            parts = line.split(None, 1)
            head = parts[0]
            tail = parts[1] if len(parts) > 1 else ""
    name = (head or "").strip().strip("*").replace("：", "").rstrip(":：")
    url = (tail or "").strip().strip("|,;，；")
    if not url:
        # 整行就是网址
        if _HOST_ONLY_RE.match(line):
            url, name = line, ""
        else:
            return None
    return name or "", url


def host_of(url: str) -> str:
    """取网址里的主机部分（小写、去端口）。"""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        host = (urlsplit(u).hostname or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    return host.lower()


def make_site(name: str, url: str) -> Dict[str, str]:
    host = host_of(url)
    return {"name": (name or "").strip() or host, "url": url.strip(), "host": host}


def load_sites(path: Optional[Path] = None) -> List[Dict[str, str]]:
    """读取站点清单。返回 [{"name","url","host"}, ...]（去重保序）。"""
    p = Path(path) if path else site_file()
    try:
        text = p.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for line in text.splitlines():
        pair = _split_line(line)
        if not pair:
            continue
        site = make_site(pair[0], pair[1])
        if not site["host"]:
            continue
        key = site["host"]
        if key in seen:
            continue
        seen.add(key)
        out.append(site)
    return out


# ------------------------------------------------------------------ 目标解析
def parse_target(text: str, sites: List[Dict[str, str]]
                 ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """解析 \\@ponywebsite 后面的附加文本。

    :return: (待检测站点列表, 选项)。选项目前只有 count（ping 包数）。
    """
    raw = (text or "").strip()
    opts: Dict[str, Any] = {"count": DEFAULT_COUNT, "keyword": ""}
    m = re.search(r"(?:-n|-c|次数|包数|ping)\s*[:= ]\s*(\d{1,3})", raw, flags=re.I)
    if m:
        opts["count"] = max(1, min(MAX_COUNT, int(m.group(1))))
        raw = re.sub(r"(?:-n|-c|次数|包数|ping)\s*[:= ]\s*(\d{1,3})", "", raw,
                     count=1, flags=re.I)
    kw = raw.strip().strip("，,。.")
    opts["keyword"] = kw
    if not kw or kw.lower() in ("all", "全部", "列表", "all all"):
        return list(sites), opts
    # 直接写了网址 → 临时检测，不进清单文件
    if _HOST_ONLY_RE.match(kw):
        return [make_site("", kw)], opts
    hit = [s for s in sites
           if kw.lower() in str(s.get("name") or "").lower()
           or kw.lower() in str(s.get("url") or "").lower()
           or kw.lower() in str(s.get("host") or "").lower()]
    return hit, opts


# ------------------------------------------------------------------ ping 解析
_TIME_RE = re.compile(r"(?:time|时间|時間)\s*[=<>]\s*([\d.,]+)\s*ms", re.I)
_AVG_RE = re.compile(r"(?:Average|平均)\s*=\s*([\d.,]+)\s*ms", re.I)
_MIN_RE = re.compile(r"(?:Minimum|最短)\s*=\s*([\d.,]+)\s*ms", re.I)
_MAX_RE = re.compile(r"(?:Maximum|最长|最長)\s*=\s*([\d.,]+)\s*ms", re.I)
_RTT_RE = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/mdev)?\s*=\s*"
    r"([\d.,]+)/([\d.,]+)/([\d.,]+)(?:/([\d.,]+))?\s*ms", re.I)
_SENT_RE = re.compile(
    r"(?:Sent|已发送|已發送)\s*=\s*(\d+)\s*[,，]\s*(?:Received|已接收)\s*=\s*(\d+)", re.I)
_TX_RE = re.compile(
    r"(\d+)\s+packets\s+transmitted[^\n]*?,\s*(\d+)\s+(?:packets\s+)?received", re.I)
_LOSS_RE = re.compile(r"([\d.,]+)\s*%\s*(?:packet\s*loss|loss|丢包|丟包|丢失|遺失)", re.I)


def _num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).strip().replace(",", ".").replace("ms", "").strip())
    except Exception:  # noqa: BLE001
        return None


def _decode(raw: bytes) -> str:
    """ping 回显解码：Windows 中文环境下是 GBK，Linux/macOS 一般是 UTF-8。"""
    if not raw:
        return ""
    cands: List[str] = ["utf-8"]
    try:
        enc = locale.getpreferredencoding(False)
        if enc:
            cands.append(enc)
    except Exception:  # noqa: BLE001
        pass
    cands += ["gbk", "cp936", "cp950", "shift_jis", "latin-1"]
    seen = set()
    for enc in cands:
        key = (enc or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return raw.decode("utf-8", "ignore")


def _parse_ping_text(text: str) -> Dict[str, Any]:
    """从 ping 回显里抠出 发送/接收/丢包率/延迟。"""
    info: Dict[str, Any] = {"sent": 0, "recv": 0, "loss_pct": None,
                            "min_ms": None, "avg_ms": None, "max_ms": None,
                            "mdev_ms": None, "samples": []}
    t = text or ""
    for m in _TIME_RE.finditer(t):
        v = _num(m.group(1))
        if v is not None:
            info["samples"].append(v)

    sent = recv = 0
    m = _SENT_RE.search(t) or _TX_RE.search(t)
    if m:
        sent, recv = int(m.group(1)), int(m.group(2))
    elif info["samples"]:
        sent = recv = len(info["samples"])
    info["sent"], info["recv"] = sent, recv

    loss = None
    m = _LOSS_RE.search(t)
    if m:
        loss = _num(m.group(1))
    if loss is None and sent:
        loss = round((sent - recv) * 100.0 / sent, 1)
    info["loss_pct"] = loss

    m = _RTT_RE.search(t)
    if m:
        info["min_ms"], info["avg_ms"], info["max_ms"] = _num(m.group(1)), _num(m.group(2)), _num(m.group(3))
        info["mdev_ms"] = _num(m.group(4))
    else:
        for reg, key in ((_MIN_RE, "min_ms"), (_MAX_RE, "max_ms"), (_AVG_RE, "avg_ms")):
            mm = reg.search(t)
            if mm:
                info[key] = _num(mm.group(1))
    # 没有汇总行（部分系统 / 被截断）时，用逐包延迟兜底算统计值
    if info["avg_ms"] is None and info["samples"]:
        info["avg_ms"] = round(sum(info["samples"]) / len(info["samples"]), 1)
    if info["min_ms"] is None and info["samples"]:
        info["min_ms"] = round(min(info["samples"]), 1)
    if info["max_ms"] is None and info["samples"]:
        info["max_ms"] = round(max(info["samples"]), 1)
    return info


def _ping_args(host: str, count: int, timeout: float) -> List[str]:
    sysname = platform.system().lower()
    if sysname == "windows":
        return ["ping", "-n", str(count), "-w", str(max(1, int(timeout * 1000))), host]
    if sysname == "darwin":
        # macOS 的 -W 单位毫秒且部分版本需 root，这里只控「发包间隔 + 总数」
        return ["ping", "-c", str(count), "-n", "-i", "0.5", host]
    return ["ping", "-c", str(count), "-n", "-i", "0.4",
            "-W", str(max(1, int(round(timeout)))), host]


def ping_host(host: str, count: int = DEFAULT_COUNT,
              timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """执行一次系统 ping。返回解析结果 + raw 回显（失败也不抛异常）。"""
    out: Dict[str, Any] = {"ok": False, "raw": "", "error": "", "timed_out": False}
    if not host:
        out["error"] = "主机名为空"
        return out
    args = _ping_args(host, count, timeout)
    overall = count * (timeout + 0.9) + 3.0
    try:
        flags = 0
        if os.name == "nt":
            flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, timeout=overall,
            creationflags=flags)
        raw, rc = proc.stdout or b"", proc.returncode
    except subprocess.TimeoutExpired as exc:  # noqa: BLE001
        raw, rc, out["timed_out"] = exc.stdout or b"", -1, True
    except FileNotFoundError:
        out["error"] = "系统没有 ping 命令"
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    text = _decode(raw)
    out["raw"] = text
    out["rc"] = rc
    out.update(_parse_ping_text(text))
    out["ok"] = bool(out.get("recv"))
    return out


# ------------------------------------------------------------------ 降级探测
def tcp_probe(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> Optional[float]:
    """TCP 握手延迟（毫秒），失败返回 None。"""
    try:
        t0 = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return round((time.perf_counter() - t0) * 1000, 1)
    except Exception:  # noqa: BLE001
        return None


def http_probe(url: str, timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    """HTTP(S) 状态码探测（走 GET，失败也不抛异常）。"""
    res: Dict[str, Any] = {"http_code": 0, "http_ms": None, "http_error": ""}
    u = (url or "").strip()
    if not u:
        return res
    if "://" not in u:
        u = "https://" + u.lstrip("/")
    try:
        req = urllib.request.Request(u, method="GET", headers={"User-Agent": UA})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res["http_code"] = int(getattr(resp, "status", 0) or 0)
        except urllib.error.HTTPError as he:  # 4xx/5xx 也算「服务活着」
            res["http_code"] = int(getattr(he, "code", 0) or 0)
        res["http_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        res["http_error"] = str(exc)[:120]
    return res


# ------------------------------------------------------------------ 单站点检测
def _blank_result(site: Dict[str, str]) -> Dict[str, Any]:
    return {
        "name": str(site.get("name") or ""),
        "url": str(site.get("url") or ""),
        "host": str(site.get("host") or ""),
        "ip": "", "status": STATUS_UNKNOWN, "mode": "", "sent": 0, "recv": 0,
        "loss_pct": None, "min_ms": None, "avg_ms": None, "max_ms": None,
        "mdev_ms": None, "samples": [], "tcp_port": 0, "tcp_ms": None,
        "http_code": 0, "http_ms": None, "note": "", "error": "",
    }


def check_site(site: Dict[str, str], count: int = DEFAULT_COUNT,
               timeout: float = DEFAULT_TIMEOUT, do_http: bool = False) -> Dict[str, Any]:
    """检测单个站点：ICMP 优先，失败降级 TCP 握手。"""
    res = _blank_result(site)
    host = res["host"]
    if not host:
        res["error"] = "网址为空或无法解析主机名"
        res["status"] = STATUS_UNKNOWN
        return res

    try:
        res["ip"] = socket.gethostbyname(host)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"DNS 解析失败：{exc}"
        res["status"] = STATUS_DOWN
        return res

    ping = ping_host(host, count, timeout)
    if ping.get("recv"):
        res.update({k: ping.get(k) for k in
                    ("samples", "sent", "recv", "loss_pct",
                     "min_ms", "avg_ms", "max_ms", "mdev_ms")})
        res["mode"] = "icmp"
    else:
        # ICMP 被拦（很常见：云服务器禁 ping / 本地防火墙）
        scheme = (urlsplit(res["url"]).scheme or "").lower() if res["url"] else ""
        ports = [80, 443] if scheme == "http" else [443, 80]
        for port in ports:
            ms = tcp_probe(host, port, max(timeout, 3.0))
            if ms is not None:
                res.update({"mode": "tcp", "tcp_port": port, "tcp_ms": ms,
                            "sent": 1, "recv": 1, "loss_pct": 0.0, "avg_ms": ms})
                res["note"] = "ICMP 不通，已降级为 TCP 握手延迟（不代表 ICMP 延迟）"
                break
        if res["mode"] != "tcp":
            res["status"] = STATUS_DOWN
            res["error"] = ping.get("error") or "ping 全部超时且 TCP 不可连"
            if ping.get("timed_out"):
                res["note"] = "ping 整体超时（可能被限速或防火墙拦截）"
            res["sent"] = int(ping.get("sent") or count)
            res["recv"] = 0
            if res["loss_pct"] is None:
                res["loss_pct"] = 100.0
            return res

    if do_http:
        res.update(http_probe(res["url"] or res["host"]))

    loss = res.get("loss_pct")
    loss = 100.0 if loss is None else float(loss)
    if loss >= 100.0:
        res["status"] = STATUS_DOWN
    elif loss > 0:
        res["status"] = STATUS_LOSS
    else:
        res["status"] = STATUS_OK
    return res


def check_all(sites: List[Dict[str, str]], count: int = DEFAULT_COUNT,
              timeout: float = DEFAULT_TIMEOUT, workers: int = MAX_WORKERS,
              do_http: Optional[bool] = None) -> Dict[str, Any]:
    """并行检测一批站点。"""
    sites = list(sites or [])
    if do_http is None:
        do_http = len(sites) <= 3
    if not sites:
        return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": 0, "results": [], "summary": {"ok": 0, "loss": 0, "down": 0,
                                                       "unknown": 0, "avg_ms": None},
                "elapsed_ms": 0, "options": {"count": count, "timeout": timeout}}

    t0 = time.time()
    n = max(1, min(int(workers or MAX_WORKERS), len(sites)))
    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(lambda s: check_site(s, count, timeout, bool(do_http)), sites))
    elapsed = int(round((time.time() - t0) * 1000))

    summary = {"ok": 0, "loss": 0, "down": 0, "unknown": 0, "avg_ms": None}
    lats: List[float] = []
    for r in results:
        st = r.get("status")
        if st == STATUS_OK:
            summary["ok"] += 1
        elif st == STATUS_LOSS:
            summary["loss"] += 1
        elif st == STATUS_DOWN:
            summary["down"] += 1
        else:
            summary["unknown"] += 1
        v = r.get("avg_ms")
        if isinstance(v, (int, float)) and r.get("mode") == "icmp":
            lats.append(float(v))
    if lats:
        summary["avg_ms"] = round(sum(lats) / len(lats), 1)

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(results),
        "results": results,
        "summary": summary,
        "elapsed_ms": elapsed,
        "options": {"count": count, "timeout": timeout, "http": bool(do_http)},
    }


# ------------------------------------------------------------------ 输出渲染
def _fmt_ms(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{float(v):.1f}"


def _fmt_loss(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{float(v):.0f}%"


def status_color(res: Dict[str, Any]) -> str:
    st = res.get("status")
    if st == STATUS_DOWN or st == STATUS_UNKNOWN:
        return "#ef4444"
    if st == STATUS_LOSS:
        return "#f59e0b"
    avg = res.get("avg_ms")
    if isinstance(avg, (int, float)):
        if float(avg) >= 300:
            return "#f97316"
        if float(avg) >= 120:
            return "#f59e0b"
    return "#22c55e"


def status_dot(res: Dict[str, Any]) -> str:
    # 用 GBK 也编得出来的符号，方便 Python 控制台/Terminal 直接打印
    return {STATUS_OK: "●", STATUS_LOSS: "▲", STATUS_DOWN: "×"}.get(
        str(res.get("status") or ""), "?")


def format_report_md(payload: Dict[str, Any]) -> str:
    """纯文本/终端用报告（GUI 走 render_result_html）。"""
    results = list(payload.get("results") or [])
    s = payload.get("summary") or {}
    lines: List[str] = [
        f"小马网站连通性检测 · {payload.get('generated') or ''}",
        f"共 {len(results)} 个站点：● 在线 {s.get('ok', 0)} ｜ ▲ 丢包 {s.get('loss', 0)}"
        f" ｜ × 不通 {s.get('down', 0)} ｜ 耗时 {payload.get('elapsed_ms', 0)} ms",
        "",
    ]
    if not results:
        lines.append("没有可检测的站点。")
        return "\n".join(lines)
    for r in results:
        detail = []
        if r.get("loss_pct") is not None:
            detail.append(f"丢包 {_fmt_loss(r.get('loss_pct'))}")
        if r.get("avg_ms") is not None:
            detail.append(f"平均 {_fmt_ms(r.get('avg_ms'))} ms")
        if r.get("min_ms") is not None and r.get("max_ms") is not None:
            detail.append(f"最小/最大 {_fmt_ms(r.get('min_ms'))}/{_fmt_ms(r.get('max_ms'))} ms")
        if r.get("ip"):
            detail.append(f"IP {r.get('ip')}")
        if r.get("http_code"):
            detail.append(f"HTTP {r.get('http_code')}（{_fmt_ms(r.get('http_ms'))} ms）")
        if r.get("error"):
            detail.append(str(r.get("error")))
        lines.append(f"{status_dot(r)} {r.get('name') or r.get('host')}"
                     f"  {r.get('url') or r.get('host')}  —— {' ｜ '.join(detail)}")
        if r.get("note"):
            lines.append(f"    · {r.get('note')}")
    return "\n".join(lines)


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def render_result_html(payload: Dict[str, Any], accent: str = "#6c8ef5") -> str:
    """渲染 HTML5 结果卡片（Qt 富文本子集可直接显示）。"""
    results = list(payload.get("results") or [])
    s = payload.get("summary") or {}
    opts = payload.get("options") or {}
    out: List[str] = ['<div style="font-family:\'Microsoft YaHei UI\',sans-serif;">']

    out.append(
        f'<div style="background:{accent};color:#fff;border-radius:12px;'
        f'padding:10px 14px;">'
        f'<div style="font-size:15px;font-weight:600;">小马网站连通性检测</div>'
        f'<div style="font-size:11px;opacity:0.85;">{_esc(payload.get("generated"))}'
        f' · 每站 {_esc(opts.get("count"))} 包 / 单包超时 {_esc(opts.get("timeout"))} s'
        f' · 总耗时 {_esc(payload.get("elapsed_ms"))} ms</div></div>')

    # 概览
    out.append('<table border="0" cellspacing="6" cellpadding="0" width="100%"'
               ' style="margin-top:8px;"><tr>'
               + _cell(str(s.get("ok", 0)), "在线", "#22c55e")
               + _cell(str(s.get("loss", 0)), "丢包", "#f59e0b")
               + _cell(str(s.get("down", 0)), "不通", "#ef4444")
               + _cell(_fmt_ms(s.get("avg_ms")) + " ms", "在线站点平均延迟", accent)
               + "</tr></table>")

    if not results:
        out.append('<div style="margin-top:10px;color:#6b7280;font-size:12px;">'
                   '没有可检测的站点。</div></div>')
        return "".join(out)

    # 明细表
    out.append('<div style="margin-top:10px;color:#6b7280;font-size:12px;">'
               f'共 {len(results)} 个站点</div>')
    out.append('<table border="0" cellspacing="0" cellpadding="6" width="100%"'
               ' style="margin-top:4px;">')
    out.append(
        '<tr style="background:#f2f4fb;color:#6b7280;font-size:11px;">'
        '<td align="left">站点</td><td align="right">丢包</td>'
        '<td align="right">平均延迟</td><td align="right">最小/最大</td>'
        '<td align="right">状态</td></tr>')
    for i, r in enumerate(results):
        bg = "#ffffff" if i % 2 == 0 else "#f9fafd"
        col = status_color(r)
        name = _esc(r.get("name") or r.get("host"))
        url = str(r.get("url") or f"https://{r.get('host')}")
        extra = f' · HTTP {_esc(r.get("http_code"))}' if r.get("http_code") else ""
        note = f'<div style="color:#9aa0ac;font-size:10px;">{_esc(r.get("note") or "")}' \
               f'{_esc(r.get("error") or "")}</div>' if (r.get("note") or r.get("error")) else ""
        rng = "—"
        if r.get("min_ms") is not None or r.get("max_ms") is not None:
            rng = f'{_fmt_ms(r.get("min_ms"))}/{_fmt_ms(r.get("max_ms"))}'
        out.append(
            f'<tr style="background:{bg};">'
            f'<td align="left" style="font-size:12px;color:#2b2b33;">'
            f'<b>{name}</b>'
            f'<div style="color:#9aa0ac;font-size:10px;">{_esc(r.get("host"))}'
            f' · {_esc(r.get("ip") or "—")}</div>'
            f'<a href="{_esc(url)}" style="color:{accent};text-decoration:none;'
            f'font-size:11px;">{_esc(url)}</a>{extra}{note}</td>'
            f'<td align="right" style="font-size:12px;color:#2b2b33;">'
            f'{_esc(_fmt_loss(r.get("loss_pct")))}</td>'
            f'<td align="right" style="font-size:12px;color:#2b2b33;">'
            f'{_esc(_fmt_ms(r.get("avg_ms")))} ms</td>'
            f'<td align="right" style="font-size:12px;color:#6b7280;">{_esc(rng)} ms</td>'
            f'<td align="right" style="font-size:12px;font-weight:600;color:{col};">'
            f'{status_dot(r)} {_esc(r.get("status"))}</td></tr>')
    out.append("</table>")

    out.append('<div style="margin-top:8px;color:#9aa0ac;font-size:11px;">'
               '延迟为 ICMP 往返时间；若站点禁 ping，会降级显示 TCP 握手延迟。</div>')
    out.append("</div>")
    return "".join(out)


def _cell(value: str, label: str, color: str) -> str:
    return (f'<td width="25%" style="background:#f7f8fd;border-radius:10px;padding:8px;'
            f'text-align:center;">'
            f'<div style="font-size:18px;font-weight:600;color:{color};">{_esc(value)}</div>'
            f'<div style="color:#6b7280;font-size:11px;">{_esc(label)}</div></td>')


def run(text: str = "", path: Optional[Path] = None,
        **kwargs: Any) -> Dict[str, Any]:
    """一步到位：读清单 → 选站点 → 检测 → 返回 payload。"""
    sites = load_sites(path)
    targets, opts = parse_target(text, sites)
    count = int(kwargs.pop("count", opts.get("count") or DEFAULT_COUNT))
    return check_all(targets, count=count, **kwargs)


if __name__ == "__main__":  # 独立调试：python ponywebsite_checker.py
    import sys
    # Windows 控制台可能是 GBK，符号（✕ 等）会编码失败
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    print(format_report_md(run(" ".join(sys.argv[1:]))))
