"""红利低波周线 BOLL 试验图的绘制规则回归测试。"""

from __future__ import annotations

import unittest

import matplotlib.pyplot as plt
import pandas as pd

from strategy import plan1


class HongliWeeklyBollTests(unittest.TestCase):
    def test_weekly_boll_draws_only_distinct_upper_and_lower_bands(self):
        """周线试验图只叠加上下轨，且配色不复用日线 BOLL。"""
        self.assertTrue(hasattr(plan1, "get_weekly_boll_line_specs"))
        specs = plan1.get_weekly_boll_line_specs()

        self.assertEqual([column for column, _color, _style in specs], ["BOLL_UPPER", "BOLL_LOWER"])
        self.assertEqual(len({color for _column, color, _style in specs}), 2)
        self.assertTrue(
            {color for _column, color, _style in specs}.isdisjoint(
                {plan1.DAILY_BOLL_UPPER_COLOR, plan1.DAILY_BOLL_LOWER_COLOR}
            )
        )

    def test_weekly_boll_uses_right_continuous_step_lines(self):
        """周 BOLL 在日线坐标中必须保持到下一次周更新，不能斜线插值。"""
        self.assertTrue(hasattr(plan1, "draw_weekly_boll_bands"))

        weekly_boll_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-08-21", "2026-08-28", "2026-09-03"]),
                "BOLL_UPPER": [1.22, 1.23, 1.21],
                "BOLL_LOWER": [1.10, 1.09, 1.08],
            }
        )
        figure, axis = plt.subplots()
        try:
            plan1.draw_weekly_boll_bands(axis, weekly_boll_df)
            lines = axis.lines
            self.assertEqual(len(lines), 2)
            self.assertEqual(
                [line.get_drawstyle() for line in lines],
                ["steps-post", "steps-post"],
            )
            self.assertEqual(
                [line.get_color() for line in lines],
                [plan1.WEEKLY_BOLL_UPPER_COLOR, plan1.WEEKLY_BOLL_LOWER_COLOR],
            )
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()
