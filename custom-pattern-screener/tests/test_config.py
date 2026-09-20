"""配置加载与校验单元测试。"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def valid_config_path():
    """生成一个最小合法配置。"""
    cfg = {
        "active_group": "测试形态",
        "groups": {
            "测试形态": {
                "examples": [
                    {"code": "600519", "start": "2024-01-01", "end": "2024-01-15"}
                ],
            }
        },
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pattern_config.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        yield str(p)


def test_load_config_applies_defaults(valid_config_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from pattern_screener.config import load_config

    cfg = load_config(valid_config_path)
    assert cfg["_group_name"] == "测试形态"
    assert cfg["similarity_threshold"] == 0.85          # 默认值
    assert cfg["max_results"] == 200
    assert cfg["lookback_days"] == 120
    # 权重默认值
    assert cfg["price_weight"] == 0.35


def test_load_config_specific_group(valid_config_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from pattern_screener.config import load_config

    cfg = load_config(valid_config_path, group_name="测试形态")
    assert cfg["_group_name"] == "测试形态"


def test_load_config_missing_file():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from pattern_screener.config import load_config

    with pytest.raises(SystemExit):
        load_config("/nonexistent/path.json")


def test_load_config_invalid_examples():
    cfg = {
        "groups": {
            "g": {"examples": [{"code": "600519"}]}   # 缺 start/end
        }
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        from pattern_screener.config import load_config
        with pytest.raises(SystemExit):
            load_config(str(p))
