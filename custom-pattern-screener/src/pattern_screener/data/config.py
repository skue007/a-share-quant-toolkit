"""数据源配置 — 自包含，无需外部 .env 文件。

优先级（低 → 高）:
1. 内置默认值
2. 可选 config.yaml（如 config/config.yaml）
3. 环境变量 TDX_ROOT / CACHE_DIR / PREFER_LOCAL
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DataSourceConfig:
    """数据源配置。

    tdx_root:    通达信安装目录（含 vipdoc/），为空则纯在线模式。
    prefer_local: 优先本地 TDX，失败后回退 akshare 在线。
    cache_dir:   Parquet 缓存目录。
    cache_ttl:   缓存有效期（天），None 表示永不过期。
    """

    tdx_root: str = ""
    prefer_local: bool = True
    cache_dir: str = "data/cache"
    cache_ttl: dict = field(default_factory=lambda: {"daily": 1})

    @classmethod
    def load(cls, yaml_path: str | None = None) -> "DataSourceConfig":
        """从 config.yaml + 环境变量加载配置。"""
        raw = {}

        # 1. 可选 yaml 配置文件
        if yaml_path and os.path.exists(yaml_path):
            try:
                import yaml
                with open(yaml_path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
            except ImportError:
                pass
            except Exception:
                pass

        tdx_cfg = raw.get("tdx", {}) or {}
        data_cfg = raw.get("data", {}) or {}

        cfg = cls(
            tdx_root=str(tdx_cfg.get("root_dir", "") or ""),
            prefer_local=bool(data_cfg.get("prefer_local", True)),
            cache_dir=str(data_cfg.get("cache_dir", "data/cache")),
            cache_ttl=dict(data_cfg.get("cache_ttl", {"daily": 1})),
        )

        # 2. 环境变量覆盖
        if os.getenv("TDX_ROOT"):
            cfg.tdx_root = os.environ["TDX_ROOT"]
        if os.getenv("CACHE_DIR"):
            cfg.cache_dir = os.environ["CACHE_DIR"]
        if os.getenv("PREFER_LOCAL") is not None:
            cfg.prefer_local = os.environ["PREFER_LOCAL"].lower() in ("1", "true", "yes")

        return cfg

    def resolve_cache_dir(self, project_root: str | None = None) -> str:
        """缓存目录若为相对路径，则相对项目根目录解析。"""
        if os.path.isabs(self.cache_dir) or project_root is None:
            return self.cache_dir
        return str(Path(project_root) / self.cache_dir)
