"""RSI 关注 ETF 配置回归测试。"""

from __future__ import annotations

import unittest

from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS


class ConfiguredEtfTests(unittest.TestCase):
    def test_shenzhen_component_and_csi_2000_are_rsi_outputs(self):
        """新增的深证成指和中证 2000 ETF 必须进入全天 RSI 图片流程。"""
        configs_by_symbol = {
            str(config["kwargs"]["symbol"]): config
            for config in RSI_ANALYSIS_CONFIGS
        }
        expected = {
            "159943": ("深证成指ETF：159943", "output/shenzhen_component_analysis.png"),
            "560220": ("中证2000ETF：560220", "output/csi_2000_analysis.png"),
        }

        for symbol, (name, image) in expected.items():
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, configs_by_symbol)
                config = configs_by_symbol[symbol]
                self.assertEqual(config["name"], name)
                self.assertEqual(config["image"], image)
                self.assertTrue(config["use_realtime_param"])
                self.assertTrue(config["kwargs"]["show_boll"])
                self.assertTrue(config["kwargs"]["show_weekly_boll"])


if __name__ == "__main__":
    unittest.main()
