#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块-大盘分时共振分析（当日最强共振板块 Top5）。

数据源（东方财富免费接口，无需鉴权；各接口均配置多个等价节点自动降级）：
  - 分时走势:  push2his / push2delay 的 /api/qt/stock/trends2/get
               (push2his 支持 ndays 回补约 5 个交易日历史分时；push2delay 为延时
                节点，仅返回最近 1 个交易日。两者均支持板块指数 90.BKxxxx)
  - 涨停池:    push2ex.eastmoney.com /getTopicZTPool  (date 参数支持任意历史交易日，
               每条记录带行业板块 hybk 字段)
  - 行业板块列表: push2 / push2delay 的 /api/qt/clist/get  (m:90+t:2)
  - 日线(昨收): push2his / push2delay 的 /api/qt/stock/kline/get

东财单个节点在不同网络环境下的可用性差异很大（实测：本机上 push2his 的分时路径被
路径级切断、而 push2delay 正常；云服务器上 push2 返回 502、push2delay 正常），
故每个接口按候选节点列表逐一下探，取第一个成功的响应。

共振定义（与需求一一对应）：
  1) 大盘指数大跌后回升至上涨（V 型反转）：
     - 大跌: 当日分时最低点相对昨收跌幅 <= -d_down（默认 -0.8%）
     - 回升至上涨: 最低点之后存在某一分钟，指数涨幅重新 >= 0（翻红）
     下跌阶段 = [开盘, 最低点]；回升阶段 = [最低点, 首次翻红]
  2) 下跌阶段板块与大盘方向一致：
     - 板块在下跌阶段的涨跌幅 < 0，且该窗口内分钟收益与大盘分钟收益的
       Pearson 相关系数 > corr_thresh（默认 0.2）
  3) 大盘反转时板块反弹幅度更大 + 明显放量 + 涨停股 > 3：
     - 反弹超额: 板块回升阶段反弹幅度(板块) > 大盘反弹幅度(大盘)（同一时间窗）
     - 放量: 回升阶段每分钟平均成交量 / 下跌阶段每分钟平均成交量 > vol_ratio（默认 1.2）
     - 涨停股数: 当日该板块涨停股票数 >= min_zt（默认 4，即“>3”）
  4) 共振强度打分（对全部合格板块做 z-score 后加权）：
     score = 0.60 * z(反弹超额) + 0.25 * z(放量比) + 0.15 * z(涨停数)
     取 Top5 输出。

用法:
  sector-resonance --date 2026-08-31
  sector-resonance --date 2026-09-01          # 盘中实时（数据到当前分钟）
  sector-resonance --index 1.000001           # 默认上证指数，可换深成指 0.399001 等
  sector-resonance-ui                         # 启动 Streamlit 交互界面

说明:
  - 板块口径为东方财富行业板块（BKxxxx）；通达信 880xxx 板块指数分时在免费公开源
    中不可得（腾讯/新浪均无）。
  - 东财会按 TLS 指纹屏蔽 python-requests，故本工具统一走 curl 子进程拉取。
  - 输出: 控制台表格 + resonance_output/{date}_market_resonance.json
         + resonance_output/{date}_resonance_chart.png
    输出目录默认为当前工作目录下 resonance_output/，可用环境变量
    RESONANCE_OUTPUT_DIR 覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ─────────────────────────────── 常量 ───────────────────────────────
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
EM_REFERER = "https://quote.eastmoney.com/"
EMEX_REFERER = "https://quote.eastmoney.com/ztb/detail"

# 各接口的候选节点（按优先级排序）。curl_json_any() 逐一下探，取第一个成功的响应，
# 使工具在某个东财节点被阻断/限流时仍能工作。
TRENDS_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/get",
    # /get 被路径级封锁时，/sse 变体通常仍可用（返回 "data: {...}" 前缀，
    # curl_json() 已做兼容剥离）
    "https://push2his.eastmoney.com/api/qt/stock/trends2/sse",
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/sse",
)
KLINE_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2delay.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/sse",
    "https://push2delay.eastmoney.com/api/qt/stock/kline/sse",
)
BOARD_LIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)
ZT_URLS = ("https://push2ex.eastmoney.com/getTopicZTPool",)
ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"

# ── 配色：遵循 A 股惯例「涨=红、跌=绿」（与欧美相反）──
COLOR_UP = "#d32f2f"      # 红 —— 上涨 / 回升阶段
COLOR_DOWN = "#2e7d32"    # 绿 —— 下跌 / 回落阶段
COLOR_FLAT = "#888888"    # 灰 —— 零轴等辅助线
COLOR_INDEX = "#111111"   # 黑 —— 大盘指数主线
# 板块对比曲线配色：刻意避开红/绿，避免被误读为「涨跌」标记
CURVE_COLORS = ("#2c7fb8", "#8e44ad", "#e67e22", "#5d6d7e", "#6c5ce7", "#795548")

DEFAULT_INDEX = "1.000001"
INDEX_ALIAS = {
    "1.000001": "上证指数",
    "0.399001": "深证成指",
    "0.399006": "创业板指",
    "1.000300": "沪深300",
    "0.399905": "中证500",
}

def _default_out_dir() -> Path:
    """输出目录: 环境变量 RESONANCE_OUTPUT_DIR > 当前工作目录/resonance_output。"""
    env = os.environ.get("RESONANCE_OUTPUT_DIR")
    return Path(env) if env else Path.cwd() / "resonance_output"


OUT_DIR = _default_out_dir()
CACHE_DIR = OUT_DIR / "_cache"


# ─────────────────────────────── 网络层（curl 子进程） ───────────────────────────────

def curl_json(url: str, params: dict, referer: str = EM_REFERER, retries: int = 4) -> Optional[dict]:
    """经 curl 拉取 JSON（绕开东财对 python-requests 的 TLS 指纹封锁）。

    东财对并发/高频请求敏感（rc=102 限流），重试时对 rc=102 采用更长退避。
    """
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}"
    for attempt in range(retries):
        limited = False
        try:
            # 不用 text=True：中文 Windows 默认 GBK 解码，东财返回 UTF-8 会直接
            # UnicodeDecodeError。手动按 UTF-8 解码并容忍个别坏字节。
            proc = subprocess.run(
                ["curl", "-s", "-m", "15", full, "-H", f"User-Agent: {UA}", "-H", f"Referer: {referer}"],
                capture_output=True, timeout=25,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            # 兼容 SSE 变体端点：响应体形如 "data: {...}"，剥掉前缀再解析
            if stdout.startswith("data:"):
                stdout = stdout[stdout.index("{"):]
            if proc.returncode != 0 or not stdout.strip():
                raise RuntimeError(f"curl rc={proc.returncode} empty")
            d = json.loads(stdout)
            if d is None:
                raise RuntimeError("null json")
            if isinstance(d, dict) and d.get("rc") not in (None, 0):
                limited = d.get("rc") == 102
                raise RuntimeError(f"rc={d.get('rc')}")
            return d
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                return None
            time.sleep(3.0 * (attempt + 1) if limited else 1.0 * (attempt + 1))
    return None


def curl_json_any(urls, params: dict, referer: str = EM_REFERER,
                  retries: int = 3) -> Optional[dict]:
    """按顺序下探候选节点，返回第一个成功的响应；全部失败返回 None。

    东财各节点互为等价入口，但可用性随网络环境变化，单个节点被阻断或限流时
    不应导致整次分析失败。
    """
    for url in urls:
        d = curl_json(url, params, referer=referer, retries=retries)
        if d is not None:
            return d
    return None


# 会话级标志：trends2 一旦全灭并成功落到 1 分钟 kline 兜底，后续直接走 kline，
# 避免对已死路径逐条空枪（每条 4 节点×3 重试的失败突发会反过来触发 IP 限流）。
_TRENDS_KLINE_FIRST = False


def _fetch_trends_via_1min_kline(secid: str, ndays: int) -> Optional[dict]:
    """trends2 全节点失败时的兜底：用 1 分钟 kline（klt=1）拼出同构分时序列。

    路径级封锁可能单独掐 trends2（/get 与 /sse 全挂）而放过 kline 路径
    （2026-09-24 实测：push2delay clist 通、trends2 全灭、1 分钟 kline rc=0）。
    与 trends2 的差异：时间戳为分钟收盘时刻（09:31 起，trends2 为 09:30 起）；
    vol 单位同为手，仅用于同日内的放量比值，绝对口径差异无影响。
    返回与 trends2 原始响应同构的 dict：{"data": {"name", "trends": [...]}}。
    """
    def _once(lmt: int) -> Optional[dict]:
        return curl_json_any(KLINE_URLS, {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "1", "fqt": "1", "end": "20500101", "lmt": str(lmt),
        }, retries=2)

    # 先按窗口全量取；被掐时降为小请求（大响应更易被重置），仅保当天+昨尾
    d = _once(ndays * 241 + 10) or _once(260)
    if not d or not d.get("data"):
        return None
    data = d["data"]
    lines = []
    for k in data.get("klines", []):
        parts = k.split(",")
        if len(parts) < 7:
            continue
        ts, o, c, h, low, vol, amt = parts[:7]
        try:
            volf, amtf = float(vol), float(amt)
        except ValueError:
            continue
        avg = f"{amtf / volf:.2f}" if volf > 0 else c
        lines.append(f"{ts},{o},{c},{h},{low},{vol},{amt},{avg}")
    if not lines:
        return None
    return {"data": {"name": data.get("name", secid), "trends": lines}}


def _fetch_trends_raw(secid: str, ndays: int) -> Optional[dict]:
    """拉取分时原始响应（不经缓存）。trends2 全节点失败时回退 1 分钟 kline。"""
    global _TRENDS_KLINE_FIRST
    raw = None
    if not _TRENDS_KLINE_FIRST:
        # retries=1：多节点已提供冗余；对被掐路径堆重试只会制造失败突发，
        # 把 IP 打进限流窗口，反而连可用的 kline 兜底一起拖死。
        raw = curl_json_any(TRENDS_URLS, {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": str(ndays), "iscr": "0",
        }, retries=1)
    if raw and raw.get("data") and raw["data"].get("trends"):
        return raw
    fb = _fetch_trends_via_1min_kline(secid, ndays)
    if fb is not None:
        _TRENDS_KLINE_FIRST = True
        return fb
    return raw


def fetch_kline_preclose(secid: str, date: str) -> Optional[float]:
    """经日线 kline 接口取目标日的前一交易日收盘价（缓存）。date 形如 2026-08-31。"""
    safe = secid.replace(".", "_")
    cache_file = CACHE_DIR / date / f"{safe}_preclose.txt"
    if cache_file.exists():
        return float(cache_file.read_text(encoding="utf-8").strip())
    d = curl_json_any(KLINE_URLS, {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "end": date.replace("-", ""), "lmt": "8",
    })
    if not d or not d.get("data"):
        return None
    prev = None
    for k in d["data"].get("klines", []):
        parts = k.split(",")
        if parts[0] < date:  # 早于目标日的最后一根
            prev = float(parts[2])
    if prev is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(str(prev), encoding="utf-8")
    return prev


def fetch_trends(secid: str, date: str, ndays: int = 5) -> Optional[dict]:
    """拉取指定日期的分时，带本地缓存（同日重复分析不再请求接口）。

    ⚠️ 东财 trends2 的 ndays 上限为 5（>5 返回 rc=102），且仅能回补到约 5 个
    交易日之前；更早日期需先在通达信客户端下载分时或用其他数据源。
    ⚠️ trends2 返回的 prePrice 是窗口内最近交易日的昨收，对历史日期无效，
    故昨收从前一交易日分时末价推导（首日则用日线 kline 接口兜底）。
    返回 {'name','preClose','trends':[仅当日行]} 或 None。
    """
    safe = secid.replace(".", "_")
    cache_file = CACHE_DIR / date / f"{safe}.csv"
    if cache_file.exists():
        lines = cache_file.read_text(encoding="utf-8").splitlines()
        if lines:
            return {"secid": secid, "name": lines[0], "preClose": float(lines[1]), "trends": lines[2:]}

    raw = _fetch_trends_raw(secid, ndays)
    if not raw or not raw.get("data"):
        return None
    data = raw["data"]
    name = data.get("name", secid)

    by_date = {}
    date_order = []
    for t in data.get("trends", []):
        d0 = t[:10]
        if d0 not in by_date:
            by_date[d0] = []
            date_order.append(d0)
        by_date[d0].append(t)
    if date not in by_date:
        return None
    di = date_order.index(date)
    if di > 0:
        prev_close = float(by_date[date_order[di - 1]][-1].split(",")[2])
    else:
        prev_close = fetch_kline_preclose(secid, date)
    if not prev_close:
        return None

    lines = by_date[date]
    # 盘中数据不写缓存（避免当日部分数据被复用）
    is_today = date == datetime.now().strftime("%Y-%m-%d")
    last_time = lines[-1][11:16] if len(lines[-1]) >= 16 else ""
    if not (is_today and last_time < "15:00"):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("\n".join([name, str(prev_close)] + lines), encoding="utf-8")
    return {"secid": secid, "name": name, "preClose": prev_close, "trends": lines}


def fetch_board_list(use_cache: bool = True) -> dict[str, str]:
    """行业板块全列表 {BK代码: 名称}（带缓存）。"""
    cache_file = CACHE_DIR / "board_list.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    out = {}
    pn = 1
    while True:
        d = curl_json_any(BOARD_LIST_URLS, {
            "pn": pn, "pz": 500, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2", "fields": "f12,f14",
        })
        if not d or not d.get("data"):
            break
        diff = d["data"].get("diff") or []
        for it in diff:
            out[it["f12"]] = it["f14"]
        if len(out) >= int(d["data"].get("total", 0)) or not diff:
            break
        pn += 1
    if out and use_cache:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def fetch_zt_pool(date: str) -> list[dict]:
    """拉取指定交易日涨停池（自动翻页，带缓存）。date 形如 20260831。"""
    cache_file = CACHE_DIR / date / "zt_pool.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    pool, tc, page = [], None, 0
    while True:
        d = curl_json_any(ZT_URLS, {
            "ut": ZT_UT, "dpt": "wz.ztzt", "Pageindex": page, "pagesize": 100,
            "sort": "fbt:asc", "date": date,
        }, referer=EMEX_REFERER)
        if not d or not d.get("data"):
            break
        tc = d["data"].get("tc", 0)
        batch = d["data"].get("pool") or []
        pool.extend(batch)
        if len(pool) >= tc or len(batch) == 0:
            break
        page += 1
        time.sleep(0.15)
    if pool:
        is_today = date == datetime.now().strftime("%Y%m%d")
        if not is_today:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    return pool


# ─────────────────────────────── 分时解析 ───────────────────────────────

def parse_trends(raw: dict, date: str) -> pd.DataFrame:
    """trends 行: '2026-08-31 09:30,开,收,高,低,量(手),额(元),均价' -> DataFrame[time,price,vol,amount,pct]。"""
    rows = []
    for line in raw.get("trends", []):
        parts = line.split(",")
        if len(parts) < 8:
            continue
        ts = parts[0]
        if not ts.startswith(date):
            continue
        try:
            rows.append({
                "time": ts[11:16],
                "price": float(parts[2]),
                "vol": float(parts[5]),
                "amount": float(parts[6]),
            })
        except ValueError:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    pre = raw.get("preClose", 0)
    df["pct"] = (df["price"] / pre - 1) * 100 if pre else 0.0
    return df.reset_index(drop=True)


def detect_v_reversal(idx: pd.DataFrame, d_down: float, min_decline_min: int = 15) -> Optional[dict]:
    """检测大盘 V 型反转。

    规则：
      - 全天最低点相对昨收跌幅 <= -d_down（"大跌状态"）
      - 最低点之后存在某分钟涨幅 >= 0（"回升至上涨"）
      - 最低点不早于开盘后 min_decline_min 分钟（排除低开高走、开盘即最低）
    下跌阶段 = [最低点前最近局部高点, 最低点]；回升阶段 = [最低点, 首次翻红]。
    """
    if len(idx) < 60:
        return None
    low_i = int(idx["pct"].idxmin())
    min_pct = float(idx["pct"].iloc[low_i])
    if min_pct > -abs(d_down):
        return None
    if low_i < min_decline_min:
        return None  # 最低点在开盘初期（低开型），非盘中 V 型
    cross = idx.index[idx["pct"] >= 0]
    cross = cross[cross > low_i]
    if len(cross) == 0:
        return None
    rec_i = int(cross[0])
    # 下跌起点 = 最低点前的局部最高点（覆盖"先涨后跌再反转"形态）
    seg = idx["pct"].iloc[:low_i + 1]
    peak_i = int(seg.idxmax())
    return {
        "low_idx": low_i, "peak_idx": peak_i, "rec_idx": rec_i,
        "min_pct": round(min_pct, 3),
        "peak_time": str(idx["time"].iloc[peak_i]),
        "low_time": str(idx["time"].iloc[low_i]),
        "rec_time": str(idx["time"].iloc[rec_i]),
        "decl_depth_pp": round(float(idx["pct"].iloc[peak_i] - min_pct), 3),  # 峰→谷跌幅(百分点)
        "end_pct": round(float(idx["pct"].iloc[-1]), 3),
        "open_pct": round(float(idx["pct"].iloc[0]), 3),
    }


# ─────────────────────────────── 板块指标 ───────────────────────────────

def board_metrics(secid: str, name: str, zt_count: int, idx: pd.DataFrame,
                  bd: pd.DataFrame, v: dict, corr_thresh: float, vol_ratio_th: float) -> Optional[dict]:
    """计算单板块共振指标；任一条件不满足返回 {'pass': False, 'reason': ...}。"""
    if bd.empty or len(bd) != len(idx):
        return {"pass": False, "reason": "分时长度与大盘不一致"}
    pk, lo, rc = v["peak_idx"], v["low_idx"], v["rec_idx"]

    # 条件2: 下跌阶段方向一致（下跌窗口 = [局部高点, 最低点]）
    decl_bd = float(bd["pct"].iloc[lo] - bd["pct"].iloc[pk])   # 板块下跌阶段涨跌(百分点)
    bd_pct_at_low = float(bd["pct"].iloc[lo])
    if decl_bd >= 0:
        return {"pass": False, "reason": f"下跌阶段未跟随下跌({decl_bd:+.2f}pp)"}
    if bd_pct_at_low >= 0:
        return {"pass": False, "reason": f"下跌阶段未处于下跌状态(最低点{bd_pct_at_low:+.2f}%)"}
    idx_ret = idx["pct"].iloc[pk:lo + 1].diff().fillna(0.0)
    bd_ret = bd["pct"].iloc[pk:lo + 1].diff().fillna(0.0)
    std_prod = float(idx_ret.std() * bd_ret.std())
    corr = float(idx_ret.cov(bd_ret) / std_prod) if std_prod > 0 else 0.0
    if corr <= corr_thresh:
        return {"pass": False, "reason": f"下跌阶段相关性不足({corr:.2f}<={corr_thresh})"}

    # 条件3a: 反弹幅度更大（同一时间窗 [最低点, 首次翻红]）
    idx_amp = float(idx["pct"].iloc[rc] - idx["pct"].iloc[lo])
    bd_amp = float(bd["pct"].iloc[rc] - bd["pct"].iloc[lo])
    if bd_amp <= idx_amp:
        return {"pass": False, "reason": f"反弹幅度未超大盘({bd_amp:.2f}<={idx_amp:.2f})"}

    # 条件3b: 明显放量
    vol_decl = float(bd["vol"].iloc[pk:lo + 1].mean())
    vol_rec = float(bd["vol"].iloc[lo:rc + 1].mean())
    vol_ratio = vol_rec / vol_decl if vol_decl > 0 else float("inf")
    if vol_ratio <= vol_ratio_th:
        return {"pass": False, "reason": f"放量不足({vol_ratio:.2f}<={vol_ratio_th})"}

    # 条件3c: 涨停股数 > 3
    if zt_count < 4:
        return {"pass": False, "reason": f"涨停数不足({zt_count})"}

    full_corr = float(idx["pct"].diff().cov(bd["pct"].diff()) /
                      (idx["pct"].diff().std() * bd["pct"].diff().std())) \
        if idx["pct"].diff().std() > 0 and bd["pct"].diff().std() > 0 else 0.0

    return {
        "pass": True,
        "secid": secid, "name": name, "zt_count": zt_count,
        "decl_ret_pct": round(decl_bd, 3),
        "bd_pct_at_low": round(float(bd["pct"].iloc[lo]), 3),
        "decl_corr": round(corr, 3),
        "idx_amp_pct": round(idx_amp, 3),
        "bd_amp_pct": round(bd_amp, 3),
        "amp_excess_pct": round(bd_amp - idx_amp, 3),
        "vol_ratio": round(vol_ratio, 3),
        "full_corr": round(full_corr, 3),
    }


# ─────────────────────────────── 图表 ───────────────────────────────

def plot_chart(idx: pd.DataFrame, top: list[dict], curves: dict[str, pd.Series],
               date: str, index_name: str, v: dict,
               save_path: Optional[Path] = None):
    """绘制 Top 共振板块与大盘分时对比图。save_path=None 时返回 fig（供 Streamlit 使用）。"""
    import matplotlib
    if save_path is not None:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    pk, lo, rc = v["peak_idx"], v["low_idx"], v["rec_idx"]
    x = np.arange(len(idx))
    fig, ax = plt.subplots(figsize=(13.5, 7.2))

    ax.axvspan(pk, lo, color=COLOR_DOWN, alpha=0.07, label="大盘下跌阶段")
    ax.axvspan(lo, rc, color=COLOR_UP, alpha=0.07, label="大盘回升阶段")
    ax.axhline(0, color=COLOR_FLAT, lw=0.8, ls="--")
    ax.axvline(lo, color=COLOR_DOWN, lw=1.0, ls=":", alpha=0.8)
    ax.axvline(rc, color=COLOR_UP, lw=1.0, ls=":", alpha=0.8)
    ax.annotate(f"最低 {v['low_time']} ({v['min_pct']:.2f}%)", (lo, idx["pct"].iloc[lo]),
                xytext=(lo + 4, idx["pct"].iloc[lo] - 0.25), fontsize=9, color=COLOR_DOWN)
    ax.annotate(f"翻红 {v['rec_time']}", (rc, idx["pct"].iloc[rc]),
                xytext=(rc - 30, idx["pct"].iloc[rc] + 0.15), fontsize=9, color=COLOR_UP)

    ax.plot(x, idx["pct"], color=COLOR_INDEX, lw=2.6, label=f"{index_name}（大盘）", zorder=5)
    colors = CURVE_COLORS
    for m, c in zip(top, colors):
        s = curves.get(m["name"])
        if s is None:
            continue
        n = min(len(s), len(x))
        ax.plot(x[:n], s.values[:n], color=c, lw=1.6, alpha=0.95,
                label=f"{m['name']}（涨停{m['zt_count']}·反弹{m['bd_amp_pct']:.2f}%·放量{m['vol_ratio']:.2f}x）")

    ax.set_xlabel("时间（分钟）")
    ax.set_ylabel("涨跌幅 %（相对昨收）")
    ax.set_title(f"{date} 大盘-板块分时共振 Top{len(top)}  |  {index_name}  "
                 f"最低 {v['low_time']}（{v['min_pct']:.2f}%）→ 翻红 {v['rec_time']}", fontsize=13)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    if save_path is not None:
        fig.savefig(save_path, dpi=150)
        plt.close(fig)
        return None
    return fig


def plot_candidates_chart(idx: pd.DataFrame, cand_items: list, curves: dict[str, pd.Series],
                          fail_reasons: dict[str, str], date: str, index_name: str,
                          v: dict, save_path: Optional[Path] = None):
    """严格口径无合格板块时，画大盘与全部候选板块分时对比（标注淘汰原因）。
    save_path=None 时返回 fig（供 Streamlit 使用）。"""
    import matplotlib
    if save_path is not None:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    pk, lo, rc = v["peak_idx"], v["low_idx"], v["rec_idx"]
    x = np.arange(len(idx))
    fig, ax = plt.subplots(figsize=(13.5, 7.2))

    ax.axvspan(pk, lo, color=COLOR_DOWN, alpha=0.07, label="大盘下跌阶段")
    ax.axvspan(lo, rc, color=COLOR_UP, alpha=0.07, label="大盘回升阶段")
    ax.axhline(0, color=COLOR_FLAT, lw=0.8, ls="--")
    ax.axvline(lo, color=COLOR_DOWN, lw=1.0, ls=":", alpha=0.8)
    ax.annotate(f"最低 {v['low_time']} ({v['min_pct']:.2f}%)", (lo, idx["pct"].iloc[lo]),
                xytext=(lo + 4, idx["pct"].iloc[lo] - 0.25), fontsize=9, color=COLOR_DOWN)

    ax.plot(x, idx["pct"], color=COLOR_INDEX, lw=2.6, label=f"{index_name}（大盘）", zorder=5)
    colors = CURVE_COLORS
    for i, item in enumerate(cand_items):
        _, zt_n, code, full = item
        s = curves.get(full)
        if s is None:
            continue
        n = min(len(s), len(x))
        reason = fail_reasons.get(full, "")
        ax.plot(x[:n], s.values[:n], color=colors[i % len(colors)], lw=1.3, alpha=0.85,
                label=f"{full}（涨停{zt_n}·淘汰:{reason}）")

    ax.set_xlabel("时间（分钟）")
    ax.set_ylabel("涨跌幅 %（相对昨收）")
    ax.set_title(f"{date} 候选板块概览（无合格共振） | {index_name}  "
                 f"最低 {v['low_time']}（{v['min_pct']:.2f}%）→ 翻红 {v['rec_time']}", fontsize=13)
    ax.legend(loc="upper left", fontsize=8.5)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    if save_path is not None:
        fig.savefig(save_path, dpi=150)
        plt.close(fig)
        return None
    return fig


# ─────────────────────────────── 分析主函数（CLI 与 Streamlit 共用） ───────────────────────────────

def analyze(date: str | None = None, index: str = DEFAULT_INDEX,
            d_down: float = 0.8, vol_ratio: float = 1.2, corr: float = 0.2,
            min_zt: int = 4, top_n: int = 5, min_decline_min: int = 15,
            workers: int = 1, log_fn=None) -> dict:
    """板块-大盘分时共振分析。返回结构化结果供 CLI / Streamlit 消费。

    Returns:
        dict: date/index/index_name, v(V型反转信息或None), zt_total,
              candidates[(hybk,zt,code,full)], failed[(full,reason)],
              qualified[metrics], top[metrics], idx(DataFrame), curves{name:pct},
              raw_map{secid:raw}, pool
    """
    if log_fn is None:
        log_fn = print
    OUT_DIR.mkdir(exist_ok=True)
    index_name = INDEX_ALIAS.get(index, index)

    # 1) 确定分析日期
    if date is None:
        today = datetime.now().strftime("%Y%m%d")
        if fetch_zt_pool(today):
            date = today
        else:
            # 兼容已有的 zt_pool 快照目录（可选）：先找当前目录下的
            # hotspot_output/zt_pool_*.json，便于复用已下载的涨停池数据
            files = sorted(Path.cwd().glob("hotspot_output/zt_pool_*.json"))
            if files:
                date = files[-1].stem.replace("zt_pool_", "").replace("-", "")
            else:
                raise RuntimeError("无法自动确定交易日，请指定 --date")
    date = str(date).replace("/", "-")
    date_compact = date.replace("-", "")
    log_fn(f"[1/5] 分析日期: {date}  大盘: {index_name}({index})")

    # 2) 涨停池 -> 候选板块（涨停 >= min_zt）
    pool = fetch_zt_pool(date_compact)
    if not pool:
        raise RuntimeError(f"日期 {date} 无涨停池数据（可能非交易日或接口失败）")
    zt_by_board = Counter(p["hybk"] for p in pool if p.get("hybk"))
    candidates = {k: v for k, v in zt_by_board.items() if v >= min_zt}
    log_fn(f"[2/5] 涨停池 {len(pool)} 只；涨停≥{min_zt} 候选板块 {len(candidates)} 个: "
           f"{dict(sorted(candidates.items(), key=lambda kv: -kv[1]))}")

    # 3) 板块名称 -> BK 代码（兼容 4 字截断名）
    board_list = fetch_board_list()
    if not board_list:
        raise RuntimeError(
            "行业板块列表拉取失败（push2 / push2delay 节点均无响应）。"
            "常见原因：东财 rc=102 限流、该节点被本机网络阻断，或网络异常；"
            "请稍后重试。"
        )
    name_to_code = {}
    for code, nm in board_list.items():
        name_to_code.setdefault(nm, code)
        name_to_code.setdefault(nm[:4], code)

    cand_items = []  # (hybk, zt_count, code, full_name)
    unmatched = []
    for hybk, cnt in candidates.items():
        code = name_to_code.get(hybk)
        if not code:
            unmatched.append(hybk)
            continue
        cand_items.append((hybk, cnt, code, board_list.get(code, hybk)))
    if unmatched:
        log_fn(f"  !! {len(unmatched)} 个板块名未匹配到 BK 代码（东财板块改名或列表不全）: {unmatched}")

    secids = {index: index_name}
    for _, _, code, full in cand_items:
        secids[f"90.{code}"] = full

    def _fetch(item, delay: float = 1.5):
        secid, nm = item
        time.sleep(delay)  # 串行错峰，降低限流概率
        return secid, nm, fetch_trends(secid, date, ndays=5)

    log_fn(f"[3/5] 拉取 {len(secids)} 条分时（并发 {workers}）...")
    raw_map = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_fetch, it, 1.5 * i) for i, it in enumerate(secids.items())]
        for fu in as_completed(futs):
            secid, nm, raw = fu.result()
            raw_map[secid] = raw
            if raw is None:
                log_fn(f"  !! 分时拉取失败: {nm} ({secid})")

    idx_raw = raw_map.get(index)
    if not idx_raw:
        failed_n = sum(1 for v in raw_map.values() if v is None)
        raise RuntimeError(
            f"大盘分时拉取失败（本次共 {failed_n}/{len(raw_map)} 条分时未取到）。"
            "常见原因：东财 rc=102 限流或网络异常，请稍后重试；"
            f"另请确认 {date} 处于 trends2 的 5 个交易日回补窗口内。"
        )
    idx = parse_trends(idx_raw, date)
    if idx.empty:
        raise RuntimeError(f"大盘 {date} 无分时数据（可能超出东财 5 个交易日回补范围）")

    # 4) V 型反转检测
    v = detect_v_reversal(idx, d_down, min_decline_min)
    if v is None:
        log_fn(f"当日大盘无 V 型反转（需最低跌幅 ≥{d_down}% 且回升翻红、最低点距开盘 ≥{min_decline_min} 分钟）: "
               f"开 {idx['pct'].iloc[0]:+.2f}% / 最低 {idx['pct'].min():+.2f}% / 收 {idx['pct'].iloc[-1]:+.2f}%")
    else:
        log_fn(f"[4/5] 大盘 V 型反转: 高点 {v['peak_time']} → 最低 {v['low_time']}（{v['min_pct']:.2f}%）"
               f"→ 翻红 {v['rec_time']}（收盘 {v['end_pct']:.2f}%）")

    # 5) 逐板块计算
    results, failed = [], []
    if v is not None:
        for hybk, zt_n, code, full in cand_items:
            raw = raw_map.get(f"90.{code}")
            if raw is None:
                failed.append((full, "分时拉取失败"))
                continue
            bd = parse_trends(raw, date)
            m = board_metrics(f"90.{code}", full, zt_n, idx, bd, v, corr, vol_ratio)
            if m.get("pass"):
                results.append(m)
            else:
                failed.append((full, m.get("reason", "?")))
        for nm, reason in failed:
            log_fn(f"  - 淘汰 {nm}: {reason}")

        if results:
            arr_ex = np.array([m["amp_excess_pct"] for m in results], dtype=float)
            arr_vol = np.array([m["vol_ratio"] for m in results], dtype=float)
            arr_zt = np.array([m["zt_count"] for m in results], dtype=float)

            def zs(a):
                s = a.std()
                return (a - a.mean()) / s if s > 0 else np.zeros_like(a)

            scores = 0.60 * zs(arr_ex) + 0.25 * zs(arr_vol) + 0.15 * zs(arr_zt)
            for m, sc in zip(results, scores):
                m["score"] = round(float(sc), 3)
            results.sort(key=lambda m: -m["score"])
    top = results[: top_n]

    # 注意：拉取失败的 secid 在 raw_map 中值为 None，需按键取值后判空，
    # 否则任一板块分时拉取失败（如东财 rc=102 限流）都会导致整次分析崩溃。
    curves = {}
    for hybk, _, code, full in cand_items:
        raw = raw_map.get(f"90.{code}")
        if raw is None:
            continue
        bd = parse_trends(raw, date)
        if not bd.empty and "pct" in bd.columns:
            curves[full] = bd["pct"]
    return {
        "date": date, "index": index, "index_name": index_name,
        "v_reversal": v, "zt_total": len(pool), "pool": pool,
        "candidates": cand_items, "failed": failed,
        "qualified": results, "top": top,
        "idx": idx, "curves": curves, "raw_map": raw_map,
        "thresholds": {"d_down": d_down, "vol_ratio": vol_ratio,
                       "corr": corr, "min_zt": min_zt, "min_decline_min": min_decline_min},
    }


# ─────────────────────────────── CLI 入口 ───────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="板块-大盘分时共振分析")
    ap.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认: 最近有涨停池数据的交易日）")
    ap.add_argument("--index", default=DEFAULT_INDEX, help="大盘指数 secid（默认上证 1.000001）")
    ap.add_argument("--d-down", type=float, default=0.8, help="大盘大跌阈值%%（默认 0.8）")
    ap.add_argument("--vol-ratio", type=float, default=1.2, help="放量阈值（默认 1.2）")
    ap.add_argument("--corr", type=float, default=0.2, help="下跌阶段相关性阈值（默认 0.2）")
    ap.add_argument("--min-zt", type=int, default=4, help="板块涨停股数下限（默认 4，即>3）")
    ap.add_argument("--top", type=int, default=5, help="输出板块数（默认 5）")
    ap.add_argument("--min-decline-min", type=int, default=15,
                    help="最低点距开盘最少分钟数（默认 15，排除低开高走）")
    ap.add_argument("--workers", type=int, default=1, help="并发拉取数（默认 1 串行，东财对并发敏感易限流 rc=102）")
    args = ap.parse_args()

    try:
        res = analyze(
            date=args.date, index=args.index,
            d_down=args.d_down, vol_ratio=args.vol_ratio, corr=args.corr,
            min_zt=args.min_zt, top_n=args.top, min_decline_min=args.min_decline_min,
            workers=args.workers,
        )
    except RuntimeError as e:
        print(f"!! {e}")
        return 1

    date, index_name, v = res["date"], res["index_name"], res["v_reversal"]
    top, failed, idx = res["top"], res["failed"], res["idx"]

    # 6) 输出
    print("\n" + "=" * 112)
    print(f"当日与大盘共振最强板块 Top{len(top)}  |  {date}  {index_name}")
    print("=" * 112)
    hdr = (f"{'#':<3}{'板块':<12}{'涨停':<5}{'下跌段%':<8}{'方向相关':<9}"
           f"{'大盘反弹%':<10}{'板块反弹%':<10}{'反弹超额%':<10}{'放量比':<8}{'全天相关':<9}{'评分':<7}")
    print(hdr)
    print("-" * 112)
    for i, m in enumerate(top, 1):
        print(f"{i:<3}{m['name']:<12}{m['zt_count']:<5}{m['decl_ret_pct']:<8}{m['decl_corr']:<9}"
              f"{m['idx_amp_pct']:<10}{m['bd_amp_pct']:<10}{m['amp_excess_pct']:<10}"
              f"{m['vol_ratio']:<8}{m['full_corr']:<9}{m['score']:<7}")
    print("-" * 112)
    for i, m in enumerate(top, 1):
        print(f"  {i}. {m['name']} — 涨停{m['zt_count']}家，下跌段{m['decl_ret_pct']:.2f}%，"
              f"反弹{m['bd_amp_pct']:.2f}%（超大盘{m['amp_excess_pct']:.2f}pp），放量{m['vol_ratio']:.2f}x")

    report = {
        "date": date, "index": res["index"], "index_name": index_name,
        "v_reversal": v, "zt_total": res["zt_total"],
        "thresholds": res["thresholds"],
        "qualified": res["qualified"], "top": top,
    }
    json_path = OUT_DIR / f"{date}_market_resonance.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {json_path}")

    if top:
        curves = {m["name"]: res["curves"].get(m["name"]) for m in top}
        png_path = OUT_DIR / f"{date}_resonance_chart.png"
        plot_chart(idx, top, curves, date, index_name, v, png_path)
        print(f"图表: {png_path}")
    elif failed and v is not None:
        cand_curves = res["curves"]
        cand_metrics = {full: reason for full, reason in failed}
        png_path = OUT_DIR / f"{date}_candidates_overview.png"
        plot_candidates_chart(idx, res["candidates"], cand_curves, cand_metrics,
                              date, index_name, v, png_path)
        print(f"候选概览图(无合格共振板块): {png_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
