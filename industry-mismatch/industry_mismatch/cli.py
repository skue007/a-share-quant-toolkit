"""行业错配检测 — 命令行入口。

用法:
    industry-mismatch scan [options]        # 全市场扫描，输出报告
    industry-mismatch ui                    # 启动 Streamlit 网页界面
    industry-mismatch version               # 版本信息

示例:
    industry-mismatch scan --out report.json
    industry-mismatch scan --lookback 60 --min-return 0.2 --max-return 1.2 \\
        --max-candidates 20 --format md --out report.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .core import (
    SIGNAL_NORMAL_TEXT,
    SIGNAL_STRONG_TEXT,
    SIGNAL_WATCH_TEXT,
    DetectionConfig,
    run_detection,
)


# ============================================================
# 报告渲染
# ============================================================

def _signal_text(signal: str) -> str:
    """emoji 信号 → 纯文本信号。"""
    mapping = {"🔴 强错配": SIGNAL_STRONG_TEXT, "🟡 关注": SIGNAL_WATCH_TEXT, "—": SIGNAL_NORMAL_TEXT}
    return mapping.get(signal, signal)


def render_markdown(result: dict) -> str:
    """结果 → Markdown 报告。"""
    cfg = result["config"]
    stats = result["stats"]

    lines = [
        "# 🔄 行业错配检测报告",
        "",
        f"> 生成时间: {result['generated_at']}",
        f"> 数据源: baostock (CSRC 行业分类)",
        "",
        "## 检测参数",
        "",
        "| 参数 | 值 |",
        "|------|-----|",
        f"| 回看天数 | {cfg['lookback_days']} 交易日 |",
        f"| 涨幅区间 | {cfg['min_return']:.0%} ~ {cfg['max_return']:.0%} |",
        f"| 最多分析候选 | {cfg['max_candidates']} 只 |",
        f"| 联动返回数量 | {cfg['top_n_linkage']} |",
        f"| 联动对齐天数 | {cfg['linkage_lookback']} 交易日 |",
        f"| 时间权重半衰期 | {cfg['half_life']} 天 |",
        f"| 偏离度阈值 | {cfg['divergence_threshold']:.2f} |",
        f"| 逃逸率阈值 | {cfg['escape_threshold']:.0%} |",
        "",
        "## 扫描结果总览",
        "",
        f"- 候选股票: {stats['total_candidates']} 只",
        f"- 完成分析: {stats['analyzed']} 只",
        f"- **强错配**: {stats['strong']} 只",
        f"- 关注: {stats['watch']} 只",
        f"- 行业匹配: {stats['normal']} 只",
        f"- 耗时: {result['elapsed']['total_seconds']}s",
        "",
        "## 📊 错配结果（按错配得分降序）",
        "",
        "| 排名 | 代码 | 名称 | 信号 | 涨幅 | 官方行业 | 偏离度 | 逃逸率 | 替代行业 | 错配得分 |",
        "|------|------|------|------|------|----------|--------|--------|----------|----------|",
    ]

    for i, r in enumerate(result["results"], start=1):
        m = r["metrics"]
        lines.append(
            f"| {i} | {r['code']} | {r['name']} | {_signal_text(r['signal'])} | "
            f"{r['return_pct']:.1f}% | {r['industry'] or '未知'} | "
            f"{m['divergence_ratio']:.2f}x | {m['escape_rate']:.0%} | "
            f"{m['alt_industry'] or '-'} | {m['mismatch_score']:.1f} |"
        )

    lines.append("")

    # 明细
    lines.append("## 🔬 错配详情")
    lines.append("")
    for r in result["results"]:
        if r["signal"] == "—":
            continue
        m = r["metrics"]
        lines.append(f"### {r['code']} {r['name']} — {_signal_text(r['signal'])}")
        lines.append("")
        lines.append(f"- **错配得分**: {m['mismatch_score']:.1f}")
        lines.append(f"- **偏离度**: {m['divergence_ratio']:.2f}x "
                     f"(同行业Top5均分 {m['avg_same_score']:.4f} / 全市场Top5均分 {m['avg_full_score']:.4f})")
        lines.append(f"- **逃逸率**: {m['escape_rate']:.0%} "
                     f"(同行业 {m['same_industry_count']} 只 / 其他行业 {m['other_industry_count']} 只)")
        lines.append(f"- **官方行业**: {r['industry'] or '未知'} | "
                     f"**替代行业**: {m['alt_industry'] or '无'}")
        lines.append("")
        if r["full_market_top"]:
            lines.append("**全市场联动 Top5:**")
            lines.append("")
            lines.append("| 代码 | 联动得分 | 方向一致率 | 收益相关 | 联动R² |")
            lines.append("|------|----------|------------|----------|--------|")
            for rec in r["full_market_top"][:5]:
                lines.append(
                    f"| {rec.get('code','')} | {rec.get('total_score',0):.4f} | "
                    f"{rec.get('direction_match',0):.1%} | {rec.get('return_corr',0):.4f} | "
                    f"{rec.get('r_squared',0):.4f} |"
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def _print_progress(stage: str, pct: float, message: str) -> None:
    bar_width = 30
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)
    sys.stdout.write(f"\r[{bar}] {pct:5.1%} {message}")
    sys.stdout.flush()
    if pct >= 1.0:
        sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="industry-mismatch",
        description="A股行业错配检测 — 发现官方行业分类与真实业务脱节的股票",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="全市场扫描并输出错配报告")
    p_scan.add_argument("--lookback", type=int, default=60, help="暴涨回看交易日 (默认 60)")
    p_scan.add_argument("--min-return", type=float, default=0.20, help="涨幅下限 (默认 0.20=20%%)")
    p_scan.add_argument("--max-return", type=float, default=1.20, help="涨幅上限 (默认 1.20=120%%)")
    p_scan.add_argument("--max-candidates", type=int, default=50, help="最多分析候选数 (默认 50)")
    p_scan.add_argument("--top-n", type=int, default=10, help="每次联动返回数量 (默认 10)")
    p_scan.add_argument("--linkage-lookback", type=int, default=120, help="联动对齐交易日 (默认 120)")
    p_scan.add_argument("--half-life", type=int, default=60, help="时间权重半衰期 (默认 60)")
    p_scan.add_argument("--divergence-threshold", type=float, default=1.15, help="偏离度阈值 (默认 1.15)")
    p_scan.add_argument("--escape-threshold", type=float, default=0.40, help="逃逸率阈值 (默认 0.40)")
    p_scan.add_argument("--format", choices=["json", "md", "both"], default="json", help="输出格式")
    p_scan.add_argument("--out", type=str, default="", help="输出文件路径（默认写入 ~/.industry_mismatch/output/）")
    p_scan.add_argument("--quiet", action="store_true", help="不打印进度条")

    # ui
    p_ui = sub.add_parser("ui", help="启动 Streamlit 网页界面")
    p_ui.add_argument("--port", type=int, default=8501, help="端口 (默认 8501)")
    p_ui.add_argument("--host", type=str, default="localhost", help="监听地址 (默认 localhost)")

    # version
    sub.add_parser("version", help="显示版本")

    return parser


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = DetectionConfig(
        lookback_days=args.lookback,
        min_return=args.min_return,
        max_return=args.max_return,
        max_candidates=args.max_candidates,
        top_n_linkage=args.top_n,
        linkage_lookback=args.linkage_lookback,
        half_life=args.half_life,
        divergence_threshold=args.divergence_threshold,
        escape_threshold=args.escape_threshold,
    )

    def progress(stage, pct, message):
        if not args.quiet:
            _print_progress(stage, pct, message)

    print("正在初始化数据源 (baostock)...")
    result = run_detection(cfg, progress_callback=progress)

    stats = result["stats"]
    print(f"\n完成: 候选 {stats['total_candidates']} | 分析 {stats['analyzed']} | "
          f"强错配 {stats['strong']} | 关注 {stats['watch']} | 匹配 {stats['normal']} | "
          f"耗时 {result['elapsed']['total_seconds']}s")

    # 输出
    if args.format in ("json", "both"):
        json_path = _resolve_out_path(args.out, "industry_mismatch_report.json")
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 报告: {json_path}")
    if args.format in ("md", "both"):
        md_path = _resolve_out_path(args.out, "industry_mismatch_report.md", force_suffix=".md")
        md_path.write_text(render_markdown(result), encoding="utf-8")
        print(f"Markdown 报告: {md_path}")

    return 0


def _resolve_out_path(out: str, default_name: str, force_suffix: str | None = None) -> Path:
    from .config import OUTPUT_DIR
    if out:
        p = Path(out).expanduser()
        if force_suffix and p.suffix == "":
            p = p.with_suffix(force_suffix)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / default_name


def cmd_ui(args: argparse.Namespace) -> int:
    """启动 Streamlit 界面。"""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print("缺少 streamlit，请先安装: pip install industry-mismatch[ui] 或 pip install streamlit")
        return 1

    app_path = Path(__file__).resolve().parent / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path),
           "--server.port", str(args.port), "--server.address", args.host]
    print("启动中: " + " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"industry-mismatch {__version__}")
        return 0
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "ui":
        return cmd_ui(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
