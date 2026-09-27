"""离线生成四类排版样图；明确使用示例数据，不写业务缓存或发送邮件。"""
import unittest
from pathlib import Path

from PIL import Image

from tools.fund_limit_change_image import save_change_image


class LimitImageTests(unittest.TestCase):
    def test_render_four_layout_scenarios(self):
        root = Path(__file__).resolve().parents[1] / "output/fund_limit_change/preview"
        names = ["英伟达", "苹果", "微软", "亚马逊", "谷歌", "博通", "台积电", "Meta Platforms", "特斯拉", "阿斯麦"]
        codes = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "TSM", "META", "TSLA", "ASML"]
        rows = [{"股票名称": name, "股票代码": code, "占净值比例": 9.7-i*.63}
                for i, (name, code) in enumerate(zip(names, codes))]
        cases = [
            ("amount", "全球成长精选混合（排版示例）", "1000元", "100元", rows),
            ("status", "海外精选基金（排版示例）", "暂停申购", "不限额度", rows),
            ("long_name", "全球高端制造与科技创新精选混合型证券投资基金（QDII）人民币C类（排版示例）", "100万元", "1000元",
             [dict(rows[0], 股票名称="超长证券名称换行验证：全球半导体及人工智能科技集团", 股票代码="VERY.LONG.TICKER")] + rows[1:]),
            ("missing_holdings", "海外精选基金（排版示例）", "不限额度", "暂停申购", []),
        ]
        for filename, name, old, new, holdings in cases:
            with self.subTest(filename=filename):
                path = root / f"{filename}.png"
                event = {"old_value": old, "new_value": new, "detected_at": "2026-09-27T12:00:00+08:00"}
                save_change_image(fund_code="000000", fund_name=name, event=event,
                                  period="2026年2季度股票投资明细" if holdings else "",
                                  holdings=holdings, output_file=path)
                with Image.open(path) as im:
                    self.assertEqual(im.width, 1080)
                    self.assertGreater(im.height, 1080)
                    self.assertEqual(im.format, "PNG")
                    self.assertAlmostEqual(im.info["dpi"][0], 300, delta=1)
                    im.verify()


if __name__ == "__main__":
    unittest.main()
