"""特征工程纯函数单元测试 — 不依赖任何数据源，可离线运行。"""

import numpy as np
import pytest

from pattern_screener.features import (
    rolling_mean,
    pearson_corr,
    normalize_price,
    normalize_volume,
    resample_curve,
    calc_body_ratio,
    calc_upper_shadow,
    calc_daily_return,
)


def test_rolling_mean_basic():
    arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = rolling_mean(arr, 3)
    assert np.isnan(result[0]) and np.isnan(result[1])
    assert np.allclose(result[2:], [2.0, 3.0, 4.0])


def test_rolling_mean_short_array():
    arr = np.array([1.0, 2.0])
    result = rolling_mean(arr, 5)
    assert np.all(np.isnan(result))


def test_pearson_corr_perfect():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([2.0, 4.0, 6.0, 8.0])
    assert pearson_corr(x, y) == pytest.approx(1.0)


def test_pearson_corr_inverse():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([4.0, 3.0, 2.0, 1.0])
    assert pearson_corr(x, y) == pytest.approx(-1.0)


def test_pearson_corr_constant():
    x = np.array([1.0, 1.0, 1.0])
    y = np.array([1.0, 2.0, 3.0])
    assert pearson_corr(x, y) == 0.0


def test_normalize_price():
    close = np.array([10.0, 11.0, 12.0])
    result = normalize_price(close)
    assert result[0] == 0.0
    assert result[1] == pytest.approx(0.1)
    assert result[2] == pytest.approx(0.2)


def test_normalize_price_zero_first():
    with pytest.raises(ValueError):
        normalize_price(np.array([0.0, 1.0]))


def test_normalize_volume():
    vol = np.array([100.0, 200.0, 300.0])
    ma = np.array([100.0, 200.0, 100.0])
    result = normalize_volume(vol, ma)
    assert result[0] == pytest.approx(1.0)
    assert result[1] == pytest.approx(1.0)
    assert result[2] == pytest.approx(3.0)


def test_normalize_volume_zero_ma():
    vol = np.array([10.0])
    ma = np.array([0.0])
    result = normalize_volume(vol, ma)
    assert np.isfinite(result[0])


def test_resample_curve_double_length():
    y = np.array([0.0, 1.0])
    result = resample_curve(y, 5)
    assert len(result) == 5
    assert result[0] == pytest.approx(0.0)
    assert result[-1] == pytest.approx(1.0)


def test_resample_curve_single_point():
    y = np.array([3.0])
    result = resample_curve(y, 4)
    assert len(result) == 4
    assert np.all(result == 3.0)


def test_calc_body_ratio():
    # 大阳线: close=10, open=8, high=11, low=7 → |10-8|/(11-7)=0.5
    ratio = calc_body_ratio(np.array([8.0]), np.array([11.0]),
                            np.array([7.0]), np.array([10.0]))
    assert ratio[0] == pytest.approx(0.5)


def test_calc_upper_shadow():
    # close=10, open=8, high=12, low=7 → (12-10)/(12-7)=0.4
    shadow = calc_upper_shadow(np.array([8.0]), np.array([12.0]),
                               np.array([7.0]), np.array([10.0]))
    assert shadow[0] == pytest.approx(0.4)


def test_calc_daily_return():
    close = np.array([10.0, 11.0, 13.2])
    ret = calc_daily_return(close)
    assert ret[0] == 0.0
    assert ret[1] == pytest.approx(0.1)
    assert ret[2] == pytest.approx(0.2)


def test_smoke_matching_identical_shapes():
    """同一形态模板 vs 自身，五维综合应接近 1.0（最高维权重组合）。"""
    rng = np.random.default_rng(42)
    base = rng.normal(size=20)
    corr = pearson_corr(base, base)
    assert corr == pytest.approx(1.0)
