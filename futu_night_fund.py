"""
Manual Futu overnight overseas fund observation entrypoint.

Default window: Beijing time 11:30-16:30, and the target US overnight
session must be open. Use --force only to bypass the Beijing time window.
This script does not write cache/fund_estimate_return_cache.json.
"""

from __future__ import annotations

import argparse

from tools.futu_night_observation import run_futu_night_observation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成富途海外基金夜盘观察图")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略北京时间 11:30-16:30 窗口限制；仍会检查美股夜盘是否开市",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="用于测试的北京时间，例如 2026-05-14T11:30:00 或 2026-05-14 11:30:00",
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
    run_futu_night_observation(
        force=bool(args.force),
        current_time=args.now,
        top_n=int(args.top_n),
    )


if __name__ == "__main__":
    main()
