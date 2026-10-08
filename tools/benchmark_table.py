"""基准表公共绘制；调用方保留列结构、列宽和点位/收益率语义。"""
import pandas as pd


def draw_benchmark_table(
    ax,
    benchmark_df,
    raw_values,
    bbox,
    *,
    fontsize,
    header_bg,
    header_text_color,
    grid_color,
    up_color,
    down_color,
    neutral_color,
    column_widths=None,
    column_width_by_name=None,
    body_bg="white",
    scale_x=1.0,
    scale_y=1.18,
    default_column_width_by_name=None,
    allow_level_values=True,
):
    table = ax.table(
        cellText=benchmark_df.values,
        colLabels=benchmark_df.columns,
        cellLoc="center",
        colLoc="center",
        bbox=bbox,
        zorder=2,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(fontsize)
    table.scale(scale_x, scale_y)

    value_col_idx = None
    for candidate in ("模型观察", "区间模型观察"):
        if candidate in benchmark_df.columns:
            value_col_idx = list(benchmark_df.columns).index(candidate)
            break
    default_width_by_name = {
        "序号": 0.08,
        "指数名称": 0.34,
        "模型观察": 0.20,
        "基准日或区间": 0.38,
    }
    if default_column_width_by_name is not None:
        default_width_by_name = dict(default_column_width_by_name)
    if isinstance(column_width_by_name, dict):
        default_width_by_name.update(column_width_by_name)
    if column_widths is not None and len(column_widths) != len(benchmark_df.columns):
        column_widths = None

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)

        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor(body_bg)
            if value_col_idx is not None and col == value_col_idx:
                raw_val = raw_values[row - 1] if row - 1 < len(raw_values) else None
                if allow_level_values and isinstance(raw_val, dict) and raw_val.get("value_type") == "level":
                    cell.get_text().set_color(neutral_color)
                    if raw_val.get("value") is not None:
                        cell.get_text().set_weight("bold")
                elif raw_val is None or pd.isna(raw_val):
                    cell.get_text().set_color(neutral_color)
                elif float(raw_val) > 0:
                    cell.get_text().set_color(up_color)
                    cell.get_text().set_weight("bold")
                elif float(raw_val) < 0:
                    cell.get_text().set_color(down_color)
                    cell.get_text().set_weight("bold")
                else:
                    cell.get_text().set_color(neutral_color)

        if col < len(benchmark_df.columns):
            col_name = benchmark_df.columns[col]
            if column_widths is not None:
                cell.set_width(column_widths[col])
            elif col_name in default_width_by_name:
                cell.set_width(default_width_by_name[col_name])

    return table
