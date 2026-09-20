"""核心逻辑单元测试 — 使用合成数据，无需网络。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from industry_mismatch.core import (
    analyze_industry_distribution,
    classify_signal,
    compute_divergence_metrics,
    normalize_daily_df,
    run_detection,
    scan_surge_stocks,
)
from industry_mismatch.core import DetectionConfig, SIGNAL_STRONG, SIGNAL_NORMAL
from industry_mismatch.linkage import (
    direction_match_rate,
    find_linked_stocks,
    weighted_return_correlation,
    compute_linkage_scores,
    cumulative_return_similarity,
    co_movement_beta_r2,
)


# ============================================================
# 合成数据工具
# ============================================================

def make_daily(code: str, n: int = 150, seed: int = 0, drift: float = 0.0,
               base: float = 10.0) -> pd.DataFrame:
    """生成合成日线数据（带日期索引）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-06-30", periods=n)
    rets = rng.normal(drift / n, 0.02, n)
    closes = base * np.cumprod(1 + rets)
    df = pd.DataFrame({
        "date": dates,
        "open": closes * 0.999,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": rng.integers(10000, 100000, n),
    })
    return df


def make_correlated_pair(n: int = 150, seed: int = 0, corr: float = 0.9,
                         base_a: float = 10.0, base_b: float = 20.0):
    """生成一对高度相关的股票日线。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-06-30", periods=n)
    common = rng.normal(0, 0.015, n)
    noise_a = rng.normal(0, 0.005, n)
    noise_b = rng.normal(0, 0.005 * np.sqrt(1 - corr**2) / np.sqrt(corr**2 + 1e-9), n)
    # 构造相关收益: 先归一化再合成
    def _scale(x):
        return x / np.std(x)
    rets_a = corr * _scale(common) + np.sqrt(1 - corr**2) * _scale(noise_a)
    rets_b = corr * _scale(common) + np.sqrt(1 - corr**2) * _scale(noise_b)
    rets_a *= 0.015
    rets_b *= 0.015
    closes_a = base_a * np.cumprod(1 + rets_a)
    closes_b = base_b * np.cumprod(1 + rets_b)
    df_a = pd.DataFrame({"date": dates, "open": closes_a * 0.999,
                         "high": closes_a * 1.01, "low": closes_a * 0.99,
                         "close": closes_a, "volume": 50000})
    df_b = pd.DataFrame({"date": dates, "open": closes_b * 0.999,
                         "high": closes_b * 1.01, "low": closes_b * 0.99,
                         "close": closes_b, "volume": 50000})
    return df_a, df_b


# ============================================================
# 基础数学
# ============================================================

class TestLinkageMetrics:
    def test_direction_match_perfect(self):
        a = np.array([0.01, -0.02, 0.03, -0.01, 0.02] * 10)
        b = a.copy()
        assert direction_match_rate(a, b) == pytest.approx(1.0)

    def test_direction_match_opposite(self):
        a = np.array([0.01, -0.02, 0.03, -0.01, 0.02] * 10)
        b = -a
        assert direction_match_rate(a, b) == pytest.approx(0.0)

    def test_weighted_corr_identity(self):
        a = np.random.default_rng(0).normal(0, 0.02, 100)
        assert weighted_return_correlation(a, a, half_life=60) > 0.99

    def test_cumulative_similarity_identity(self):
        closes = np.linspace(10, 30, 100)
        assert cumulative_return_similarity(closes, closes) > 0.99

    def test_beta_r2_perfect(self):
        x = np.random.default_rng(1).normal(0, 0.02, 100)
        y = 2 * x + 0.001
        beta, r2 = co_movement_beta_r2(x, y)
        assert beta == pytest.approx(2.0, abs=0.05)
        assert r2 > 0.9

    def test_linkage_scores_total_in_range(self):
        a = np.random.default_rng(2).normal(0, 0.02, 120)
        b = a + np.random.default_rng(3).normal(0, 0.005, 120)
        scores = compute_linkage_scores(a, b, a.cumsum() + 10, b.cumsum() + 20)
        assert 0.0 <= scores["total_score"] <= 1.0


# ============================================================
# 联动批量扫描
# ============================================================

class TestFindLinkedStocks:
    def test_correlated_pair_ranked_top(self):
        target_df, twin_df = make_correlated_pair(seed=7, corr=0.9)
        unrelated = make_daily("999999", n=150, seed=8)

        cache = {"600001": target_df, "600002": twin_df, "600003": unrelated}
        def get_daily(code):
            return cache.get(code, pd.DataFrame())

        df = find_linked_stocks(
            target_code="600001",
            candidate_codes=["600002", "600003"],
            get_daily_fn=get_daily,
            half_life=60,
            top_n=2,
            lookback_days=120,
        )
        assert not df.empty
        # 高度相关的股票应排第一
        assert df.iloc[0]["code"] == "600002"
        assert df.iloc[0]["total_score"] > df.iloc[1]["total_score"]

    def test_empty_on_bad_target(self):
        df = find_linked_stocks(
            target_code="600001",
            candidate_codes=["600002"],
            get_daily_fn=lambda c: pd.DataFrame(),
            top_n=5,
        )
        assert df.empty


# ============================================================
# 暴涨扫描 & 错配指标
# ============================================================

INDUSTRY_MAP = {
    "600001": {"name": "甲股份", "industry": "C01农业", "industry_csrc": "证监会"},
    "600002": {"name": "乙股份", "industry": "C01农业", "industry_csrc": "证监会"},
    "600003": {"name": "丙股份", "industry": "C01农业", "industry_csrc": "证监会"},
    "600004": {"name": "丁科技", "industry": "C35专用设备", "industry_csrc": "证监会"},
    "600005": {"name": "戊科技", "industry": "C35专用设备", "industry_csrc": "证监会"},
    "600006": {"name": "ST退市", "industry": "C01农业", "industry_csrc": "证监会"},
}


class TestSurgeScan:
    def test_filter_by_return_range(self):
        # 造三只不同涨幅的股票
        dates = pd.bdate_range(end="2026-06-30", periods=70)

        def _mk(code, final_close):
            closes = np.linspace(10, final_close, 70)
            return pd.DataFrame({"date": dates, "close": closes})

        cache = {
            "600001": _mk("600001", 14.0),    # +40%
            "600002": _mk("600002", 11.0),    # +10% (低于下限)
            "600003": _mk("600003", 30.0),    # +200% (超过上限)
        }
        results = scan_surge_stocks(
            get_daily_fn=lambda c: cache.get(c, pd.DataFrame()),
            all_codes=["600001", "600002", "600003"],
            lookback_days=60,
            min_return=0.2,
            max_return=1.2,
            industry_map=INDUSTRY_MAP,
        )
        codes = [r[0] for r in results]
        assert codes == ["600001"]
        assert results[0][1] == "甲股份"

    def test_insufficient_history_skipped(self):
        dates = pd.bdate_range(end="2026-06-30", periods=10)
        df = pd.DataFrame({"date": dates, "close": np.linspace(10, 12, 10)})
        results = scan_surge_stocks(
            get_daily_fn=lambda c: df,
            all_codes=["600001"],
            lookback_days=60,
            min_return=0.0,
            max_return=10.0,
            industry_map=INDUSTRY_MAP,
        )
        assert results == []


class TestDivergenceMetrics:
    def _mk_linkage_df(self, codes, scores):
        return pd.DataFrame({
            "code": codes,
            "total_score": scores,
            "direction_match": [0.8] * len(codes),
            "return_corr": [0.7] * len(codes),
            "r_squared": [0.6] * len(codes),
        })

    def test_strong_mismatch(self):
        # 同行业得分低，全市场得分高且集中在其他行业
        same_df = self._mk_linkage_df(["600002", "600003", "600001"], [0.30, 0.28, 0.25])
        full_df = self._mk_linkage_df(
            ["600004", "600005", "600002"], [0.75, 0.72, 0.30]
        )
        metrics = compute_divergence_metrics(
            same_ind_df=same_df,
            full_mkt_df=full_df,
            target_code="600001",
            target_industry="C01农业",
            industry_map=INDUSTRY_MAP,
        )
        assert metrics["divergence_ratio"] > 2.0          # 全市场远优于同行业
        assert metrics["escape_rate"] > 0.5               # 逃逸率高
        assert metrics["alt_industry"] == "C35专用设备"
        assert metrics["mismatch_score"] > 60
        assert classify_signal(metrics, 1.15, 0.40) == SIGNAL_STRONG

    def test_no_mismatch(self):
        same_df = self._mk_linkage_df(["600002", "600003", "600001"], [0.70, 0.68, 0.65])
        full_df = self._mk_linkage_df(
            ["600002", "600003", "600001"], [0.72, 0.70, 0.68]
        )
        metrics = compute_divergence_metrics(
            same_ind_df=same_df,
            full_mkt_df=full_df,
            target_code="600001",
            target_industry="C01农业",
            industry_map=INDUSTRY_MAP,
        )
        assert metrics["divergence_ratio"] < 1.15
        assert metrics["escape_rate"] < 0.40
        assert classify_signal(metrics, 1.15, 0.40) == SIGNAL_NORMAL


class TestIndustryDistribution:
    def test_empty(self):
        d = analyze_industry_distribution([], "C01农业", INDUSTRY_MAP)
        assert d["escape_rate"] == 0.0
        assert d["top_alt_industry"] is None

    def test_breakdown(self):
        d = analyze_industry_distribution(
            ["600002", "600004", "600005"], "C01农业", INDUSTRY_MAP
        )
        assert d["same_industry_count"] == 1
        assert d["other_industry_count"] == 2
        assert d["escape_rate"] == pytest.approx(2 / 3)
        assert d["top_alt_industry"] == "C35专用设备"


class TestNormalizeDaily:
    def test_aliases(self):
        df = pd.DataFrame({
            "trade_date": pd.bdate_range(end="2026-06-30", periods=30),
            "收盘": np.linspace(10, 20, 30),
        })
        out = normalize_daily_df(df)
        assert "date" in out.columns and "close" in out.columns


# ============================================================
# 端到端（合成数据源，无网络）
# ============================================================

class FakeSource:
    """内存数据源：4 只股票，其中一只与另一行业高度联动。"""

    def __init__(self):
        # 600001 甲股份（C01农业）——实际与 C35 的 600004 高度联动
        a, b = make_correlated_pair(seed=11, corr=0.95, base_a=10, base_b=30)
        unrelated = make_daily("600002", n=150, seed=12, drift=0.0)
        third = make_daily("600003", n=150, seed=13, drift=0.0)
        other = make_daily("600005", n=150, seed=14, drift=0.0)
        self._data = {
            "600001": a,
            "600002": unrelated,
            "600003": third,
            "600004": b,
            "600005": other,
        }
        self.industry_map = {
            "600001": {"name": "甲股份", "industry": "C01农业", "industry_csrc": "证监会"},
            "600002": {"name": "乙股份", "industry": "C01农业", "industry_csrc": "证监会"},
            "600003": {"name": "丙股份", "industry": "C01农业", "industry_csrc": "证监会"},
            "600004": {"name": "丁科技", "industry": "C35专用设备", "industry_csrc": "证监会"},
            "600005": {"name": "戊科技", "industry": "C35专用设备", "industry_csrc": "证监会"},
        }

    def get_daily(self, code):
        return self._data.get(code, pd.DataFrame())

    def get_stock_industry_map(self, force_refresh=False):
        return dict(self.industry_map)


class TestEndToEnd:
    def test_run_detection_returns_serializable(self):
        # 让 600001 近期暴涨（注入上涨）
        src = FakeSource()
        base = src._data["600001"].copy()
        n = len(base)
        dates = base["date"]
        closes = base["close"].values
        # 后 60 天翻倍
        new_closes = closes.copy()
        start, end = closes[-61], closes[-1]
        new_closes[-61:] = np.linspace(start, start * 2.0, 61)
        src._data["600001"] = pd.DataFrame({
            "date": dates, "open": new_closes * 0.999, "high": new_closes * 1.01,
            "low": new_closes * 0.99, "close": new_closes, "volume": 50000,
        })

        result = run_detection(
            DetectionConfig(
                lookback_days=60,
                min_return=0.5,      # 只保留 600001
                max_return=3.0,
                max_candidates=5,
                top_n_linkage=5,
                linkage_lookback=120,
                half_life=60,
            ),
            source=src,
            progress_callback=lambda s, p, m: None,
        )

        assert result["stats"]["analyzed"] >= 1
        codes = [r["code"] for r in result["results"]]
        assert "600001" in codes
        r600001 = next(r for r in result["results"] if r["code"] == "600001")
        # 与 C35 高度联动 → 逃逸率应为高值
        assert r600001["metrics"]["escape_rate"] > 0.4
        # 可 JSON 序列化
        import json
        json.dumps(result, ensure_ascii=False)
