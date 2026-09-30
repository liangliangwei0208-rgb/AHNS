"""纳指100图只接受足够长的真实指数日线。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools import rsi_data


class NdxChartTests(unittest.TestCase):
    def test_220_real_ndx_rows_are_usable_even_when_rsi_requests_longer_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "dot_NDX_index_daily.csv"
            dates=pd.bdate_range(end="2026-09-29",periods=220)
            pd.DataFrame({"date":dates,"close":[100.] * 220}).to_csv(cache,index=False)
            with patch.object(rsi_data,"_latest_complete_rsi_trade_date",return_value="2026-09-29"):
                result=rsi_data._read_usable_index_cache(cache,symbol=".NDX",days=1200,
                                                         include_realtime=False)
        self.assertIsNotNone(result)
        self.assertEqual(len(result),220)

    def test_fresh_but_short_ndx_cache_is_not_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "dot_NDX_index_daily.csv"
            pd.DataFrame({"date": pd.bdate_range("2026-09-09", periods=15),
                          "close": [100.] * 15}).to_csv(cache, index=False)
            with patch.object(rsi_data, "_latest_complete_rsi_trade_date", return_value="2026-09-29"):
                result = rsi_data._read_usable_index_cache(cache, symbol=".NDX", days=220,
                                                           include_realtime=False)
        self.assertIsNone(result)

    def test_short_ndx_network_response_cannot_replace_history(self):
        short = pd.DataFrame({"date": pd.bdate_range("2026-09-09", periods=15),
                              "open": [100.] * 15, "high": [101.] * 15,
                              "low": [99.] * 15, "close": [100.] * 15,
                              "volume": [1000] * 15})
        with tempfile.TemporaryDirectory() as tmp, patch.object(rsi_data.ak, "index_us_stock_sina", return_value=short):
            with self.assertRaisesRegex(RuntimeError, "NDX.*历史"):
                rsi_data.get_index_akshare(symbol=".NDX", days=220, cache_dir=tmp,
                                           retry=1, use_cache=True)


if __name__ == "__main__":
    unittest.main()
