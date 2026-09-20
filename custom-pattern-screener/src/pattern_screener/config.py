"""形态分组配置加载与校验。

读取 pattern_config.json（支持多分组结构 + 旧格式自动迁移），
应用默认值并校验阈值/权重合法性。逻辑与原版 load_config 一致。
"""

from __future__ import annotations

import json
import sys
from typing import Any

# 默认配置值
DEFAULTS = {
    "similarity_threshold": 0.85,
    "max_results": 200,
    "lookback_days": 120,
    "price_weight": 0.35,
    "volume_weight": 0.15,
    "body_weight": 0.15,
    "shadow_weight": 0.15,
    "return_weight": 0.20,
}

WEIGHT_KEYS = ["price_weight", "volume_weight", "body_weight",
               "shadow_weight", "return_weight"]


def log(msg: str) -> None:
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _validate_group_config(cfg: dict, group_name: str):
    """校验单个分组的配置，返回 (是否有效, 配置dict或None)"""
    if "examples" not in cfg or not isinstance(cfg["examples"], list):
        log(f"错误: 分组 '{group_name}' 缺少 'examples' 列表")
        return False, None
    if len(cfg["examples"]) == 0:
        log(f"错误: 分组 '{group_name}' 的 'examples' 列表不能为空")
        return False, None

    for i, ex in enumerate(cfg["examples"]):
        for field in ("code", "start", "end"):
            if field not in ex:
                log(f"错误: 第 {i+1} 个示例缺少 '{field}' 字段")
                return False, None
        if ex["start"] >= ex["end"]:
            log(f"错误: 示例 {ex['code']} 的 start 必须早于 end")
            return False, None

    # 应用默认值
    for key in DEFAULTS:
        if key not in cfg:
            cfg[key] = DEFAULTS[key]

    threshold = cfg["similarity_threshold"]
    if not (0.0 <= threshold <= 1.0):
        log(f"错误: similarity_threshold 必须在 [0, 1] 之间，当前为 {threshold}")
        return False, None

    total_w = sum(cfg[k] for k in WEIGHT_KEYS)
    if abs(total_w - 1.0) > 0.01:
        log(f"警告: 五维权重之和 = {total_w:.2f}，已自动归一化")
        for k in WEIGHT_KEYS:
            cfg[k] /= total_w

    return True, cfg


def load_config(config_path: str, group_name: str | None = None) -> dict[str, Any]:
    """读取并校验 pattern_config.json，支持分组结构 + 向后兼容旧格式。

    Args:
        config_path: pattern_config.json 的完整路径。
        group_name: 指定分组；不指定则使用 active_group。
    """
    import os
    if not os.path.exists(config_path):
        log(f"配置文件不存在: {config_path}")
        log("请通过 `pattern-screener init-config` 或手动创建配置")
        sys.exit(1)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        log(f"配置文件 JSON 格式错误: {e}")
        sys.exit(1)

    # 向后兼容: 旧格式（顶层有 examples，无 groups）自动迁移
    if "groups" not in raw and "examples" in raw:
        log("检测到旧格式配置，自动迁移为分组格式...")
        group_cfg = {}
        for key in list(raw.keys()):
            group_cfg[key] = raw[key]
        raw = {
            "active_group": "默认形态",
            "groups": {"默认形态": group_cfg},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        log("已迁移为分组格式 (分组名: 默认形态)")

    if "groups" not in raw:
        log("错误: 配置文件缺少 'groups'")
        sys.exit(1)

    groups = raw["groups"]
    if not groups:
        log("错误: 'groups' 不能为空")
        sys.exit(1)

    # 确定使用哪个分组
    if group_name:
        if group_name not in groups:
            log(f"错误: 分组 '{group_name}' 不存在，可用分组: {', '.join(groups.keys())}")
            sys.exit(1)
        active = group_name
    else:
        active = raw.get("active_group", list(groups.keys())[0])
        if active not in groups:
            active = list(groups.keys())[0]
            log(f"警告: active_group 无效，使用 '{active}'")

    ok, cfg = _validate_group_config(groups[active], active)
    if not ok:
        sys.exit(1)

    cfg["_group_name"] = active
    log(f"使用分组: {active}")
    return cfg
