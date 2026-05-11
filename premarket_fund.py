"""
Manual pre-market overseas fund observation entrypoint.

Default window: Beijing time 17:00-20:30. Use --force for manual tests.
This script does not write cache/fund_estimate_return_cache.json.
"""

from __future__ import annotations

import argparse

from tools.premarket_estimator import run_premarket_observation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成海外基金盘前观察图")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略北京时间 17:00-20:30 窗口限制，强制生成盘前观察图",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="用于测试的北京时间，例如 2026-05-11T20:00:00 或 2026-05-11 20:00:00",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="每只基金使用前 N 大股票持仓，默认 10",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_premarket_observation(
        force=bool(args.force),
        current_time=args.now,
        top_n=int(args.top_n),
    )


if __name__ == "__main__":
    main()
