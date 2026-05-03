"""自动生成 A 股休市且海外有新估值时的海外基金累计估算表。"""

from tools.fund_estimate_history_overseas import (
    build_cumulative_estimate_table,
    detect_overseas_holiday_estimate_window,
)


OUTPUT_FILE = "output/haiwai_holidays.png"
TITLE_SUFFIX = "海外基金累计预估收益率"


def main() -> None:
    window = detect_overseas_holiday_estimate_window()
    if not window.should_generate:
        print(window.reason)
        return

    output_file = OUTPUT_FILE
    title = f"{window.date_label}{TITLE_SUFFIX}"
    print(window.reason)

    summary_df, daily_df = build_cumulative_estimate_table(
        start_date=window.start_date,
        end_date=window.end_date,
        date_field=window.date_field,
        output_file=output_file,
        title=title,
        print_table=True,
        save_table=True,
        footnote_text=(
            "依据基金季度报告前十大持仓股及指数估算，仅供学习记录，"
            "不构成投资建议；最终以基金公司更新为准。"
        ),
    )

    print(
        f"海外节假日累计图已生成: {output_file}；"
        f"汇总 {len(summary_df)} 只基金，原始缓存记录 {len(daily_df)} 条。"
    )


if __name__ == "__main__":
    main()
