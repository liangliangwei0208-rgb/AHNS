"""
safe_holidays.py

只读取缓存，自动识别“A股休市、海外有新估值”的北京时间运行区间，
统计海外基金节假日期间模型估算观察，并输出公开展示版表格图片。

表格只展示：
- 序号
- 基金名称
- 区间模型估算观察
- 有效估值日数
- 起始估值日
- 结束估值日

不展示基金代码。
基金名称默认不隐藏；如需用星号隐藏后半段，修改 tools.safe_display.MASK_FUND_NAME_WITH_STAR。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from tools.safe_display import WATERMARK_ALPHA, add_risk_watermark, mask_fund_name

from tools.fund_estimate_history_overseas import (
    build_benchmark_cumulative_dataframe,
    build_cumulative_dataframe,
    detect_overseas_holiday_estimate_window,
    get_benchmark_estimate_records,
    get_fund_estimate_records,
    print_cumulative_estimate_table,
    save_cumulative_estimate_table_image,
)


OUTPUT_FILE = "output/safe_holidays.png"
TITLE_SUFFIX = "海外基金模型估算观察"
CUMULATIVE_INTERNAL_COLUMN = "区间累计预估收益率"
CUMULATIVE_DISPLAY_COLUMN = "区间模型估算观察"

SAFE_COLUMNS = [
    "序号",
    "基金名称",
    CUMULATIVE_DISPLAY_COLUMN,
    "有效估值日数",
    "起始估值日",
    "结束估值日",
]
BRAND_WATERMARK_TEXT = "鱼师AHNS"
# 是否用星号隐藏 safe_holidays 图片里的基金名称后半段；默认不隐藏。
MASK_FUND_NAMES_WITH_STAR = True


Path("output").mkdir(parents=True, exist_ok=True)

def build_safe_summary_df(
    summary_df: pd.DataFrame,
    *,
    mask_names: bool = MASK_FUND_NAMES_WITH_STAR,
) -> pd.DataFrame:
    """
    生成公开展示版累计收益表。

    明确不展示基金代码；记录状态只保留在原始 summary_df 中，不进入图片表格。
    """
    safe_df = summary_df.copy()

    if safe_df.empty:
        raise RuntimeError(
            "未找到海外基金节假日预估收益缓存。请先运行 main.py 生成对应日期缓存。"
        )

    internal_columns = [
        "序号",
        "基金名称",
        CUMULATIVE_INTERNAL_COLUMN,
        "有效估值日数",
        "起始估值日",
        "结束估值日",
    ]

    for col in internal_columns:
        if col not in safe_df.columns:
            safe_df[col] = None

    safe_df = safe_df[internal_columns].copy()
    safe_df = safe_df.rename(columns={CUMULATIVE_INTERNAL_COLUMN: CUMULATIVE_DISPLAY_COLUMN})
    safe_df["序号"] = range(1, len(safe_df) + 1)
    safe_df["基金名称"] = safe_df["基金名称"].map(
        lambda name: mask_fund_name(name, enabled=mask_names)
    )

    return safe_df


def main() -> None:
    window = detect_overseas_holiday_estimate_window()
    if not window.should_generate:
        print(window.reason)
        return

    output_file = OUTPUT_FILE
    title = f"{window.date_label}{TITLE_SUFFIX}"
    print(window.reason)

    daily_df = get_fund_estimate_records(
        start_date=window.start_date,
        end_date=window.end_date,
        market_group="overseas",
        date_field=window.date_field,
        include_intraday=True,
        require_final=False,
    )
    summary_df = build_cumulative_dataframe(daily_df)
    safe_summary_df = build_safe_summary_df(summary_df)

    benchmark_daily_df = get_benchmark_estimate_records(
        start_date=window.start_date,
        end_date=window.end_date,
        market_group="overseas",
        date_field=window.date_field,
        include_intraday=True,
        require_final=False,
    )
    benchmark_summary_df = build_benchmark_cumulative_dataframe(benchmark_daily_df)
    # 底层累计绘图函数依赖原始列名；图片展示时再映射为“区间模型估算观察”。
    image_summary_df = safe_summary_df.rename(
        columns={CUMULATIVE_DISPLAY_COLUMN: CUMULATIVE_INTERNAL_COLUMN}
    )

    print_cumulative_estimate_table(
        summary_df=image_summary_df,
        title=title,
        pct_digits=2,
        benchmark_summary_df=benchmark_summary_df,
        display_column_names={CUMULATIVE_INTERNAL_COLUMN: CUMULATIVE_DISPLAY_COLUMN},
    )

    save_cumulative_estimate_table_image(
        summary_df=image_summary_df,
        output_file=output_file,
        title=title,
        pct_digits=2,
        display_column_names={CUMULATIVE_INTERNAL_COLUMN: CUMULATIVE_DISPLAY_COLUMN},
        benchmark_summary_df=benchmark_summary_df,
        hide_status_column=True,
        footnote_text=(
            "依据基金季度报告前十大持仓股及指数估算，仅供学习记录，"
            "不构成投资建议；最终以基金公司更新为准。"
        ),
        watermark_text=BRAND_WATERMARK_TEXT,
        watermark_alpha=WATERMARK_ALPHA,
        watermark_fontsize=32,
        up_color="red",
        down_color="green",
        row_height=0.55,
    )
    add_risk_watermark(output_file)

    print(f"\n安全版海外节假日累计预估收益表已生成: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
        raise SystemExit(1)
