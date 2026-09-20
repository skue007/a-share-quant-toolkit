"""结果报告 — 通过新浪行情 API 补齐股票名称，并输出 CSV 报告。"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime

import requests

NAME_BLACKLIST = ["ST", "退"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def enrich_names(results: list[dict]) -> None:
    """通过新浪行情API补齐股票名称（就地修改 results）。"""
    needs = [r["code"] for r in results if not r.get("name") or r["name"] == ""]
    if not needs:
        return

    log(f"  正在从新浪获取 {len(needs)} 只股票名称...")
    name_map = {}
    for i in range(0, len(needs), 50):
        batch = needs[i:i + 50]
        symbols = ",".join(
            f"sh{c}" if c.startswith("6") else f"sz{c}" for c in batch
        )
        try:
            resp = requests.get(
                f"https://hq.sinajs.cn/list={symbols}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn",
                },
                timeout=15,
            )
            resp.encoding = "gbk"
            if resp.status_code == 200:
                for line in resp.text.split("\n"):
                    if '="' not in line:
                        continue
                    eq_idx = line.index('="')
                    sym_end = line.find("=")
                    # 取最后一个 _ 之前的位置 (格式: var hq_str_sh600519="名称,...)
                    sym_start = line.rfind("_", 0, sym_end) + 1
                    symbol = line[sym_start:sym_end]
                    if len(symbol) >= 8:
                        code = symbol[2:]
                        quoted = line[eq_idx + 2:]
                        first_comma = quoted.find(",")
                        if first_comma > 0:
                            name_map[code] = quoted[:first_comma]
        except Exception:
            pass
        time.sleep(0.3)

    for r in results:
        if r["code"] in name_map and name_map[r["code"]]:
            r["name"] = name_map[r["code"]]

    missing = sum(1 for r in results if not r.get("name") or r["name"] == "")
    log(f"  名称补齐完成，仍缺: {missing}")


def post_filter_st(results: list[dict]) -> list[dict]:
    """名称补齐后过滤 ST/退（新浪 API 可能补出 TDX 缺少的 ST 前缀）。"""
    filtered = [r for r in results
                if not any(kw in (r.get("name") or "") for kw in NAME_BLACKLIST)]
    if len(filtered) < len(results):
        log(f"  名称补齐后过滤 ST/退: {len(results)} → {len(filtered)}")
    return filtered


def dedup(results: list[dict]) -> list[dict]:
    """按 (code, match_date) 去重。"""
    seen = set()
    deduped = []
    for r in results:
        key = (r["code"], r["match_date"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def save_results(results: list[dict], config: dict, output_dir: str) -> str | None:
    """输出CSV到 output_dir/，返回实际保存的文件路径（或 None）。"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    time_suffix = datetime.now().strftime("%H%M%S")

    max_results = config["max_results"]
    results = sorted(results, key=lambda r: r["score"], reverse=True)
    results = results[:max_results]

    group_name = config.get("_group_name", "")
    fname = f"custom_pattern_{group_name}_{date_str}.csv" if group_name else f"custom_pattern_{date_str}.csv"
    path = os.path.join(output_dir, fname)

    def safe_open(path):
        try:
            return open(path, "w", newline="", encoding="utf-8-sig"), path
        except PermissionError:
            base, ext = os.path.splitext(path)
            alt = f"{base}_{time_suffix}{ext}"
            log(f"  文件被占用，改用: {os.path.basename(alt)}")
            return open(alt, "w", newline="", encoding="utf-8-sig"), alt

    fields = ["code", "name", "match_date", "score", "price_corr",
              "volume_corr", "body_corr", "shadow_corr", "return_corr",
              "current_price", "amount_亿", "group_name"]
    f, actual_path = safe_open(path)
    with f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    log(f"  结果已保存: {os.path.basename(actual_path)} ({len(results)} 条)")
    return actual_path
