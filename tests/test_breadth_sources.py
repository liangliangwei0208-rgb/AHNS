import unittest
import tempfile
import sys
import types
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from tools.breadth_sources import parse_members, parse_nasdaq_directory, parse_comp_component_count, extract_effective_date, parse_yahoo_history, FutuBreadth, SourceHealth, validate_external_breadth, parse_sina_us, parse_stockcharts, parse_tencent_history, fetch_prices, fetch_quotes, fetch_eastmoney_us_prices, fetch_sina_us_prices
import requests

class SourceTests(unittest.TestCase):
    def test_eastmoney_us_daily_uses_direct_connection_and_adjusted_close(self):
        class Response:
            def raise_for_status(self):pass
            def json(self):return {"data":{"klines":["2026-09-25,9,10,11,8", "2026-09-28,10,12,13,9"]}}
        class Session:
            trust_env=True
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def get(self,url,params,timeout):
                self_test.assertFalse(self.trust_env)
                self_test.assertEqual(params["secid"],"105.AAPL")
                self_test.assertEqual(params["fqt"],"1")
                return Response()
        self_test=self
        with patch("tools.breadth_sources.requests.Session",return_value=Session()):
            frame,basis=fetch_eastmoney_us_prices("US.AAPL","2026-09-28")
        self.assertEqual(basis,"eastmoney_us_qfq")
        self.assertEqual(frame.iloc[-1].close,12.)

    def test_us_history_falls_back_to_eastmoney_when_yahoo_unreachable(self):
        expected=pd.DataFrame({"date":["2026-09-28"],"close":[12.]})
        with patch("tools.breadth_sources.fetch_yahoo_prices",side_effect=requests.ConnectionError("blocked")), \
             patch("tools.breadth_sources.fetch_sina_us_prices",side_effect=requests.ConnectionError("blocked")), \
             patch("tools.breadth_sources.fetch_eastmoney_us_prices",return_value=(expected,"eastmoney_us_qfq")) as fallback:
            frame,basis=fetch_prices("US.AAPL","2026-09-28")
        self.assertEqual(basis,"eastmoney_us_qfq")
        self.assertEqual(frame.iloc[-1].close,12.)
        self.assertTrue(fallback.called)

    def test_us_history_reports_circuit_breaker_reason(self):
        health=SourceHealth()
        health.unreachable.update({"yahoo_adjclose","eastmoney_us_qfq","sina_us_qfq"})
        with self.assertRaisesRegex(RuntimeError,"熔断"):
            fetch_prices("US.AAPL","2026-09-28",health=health)

    def test_yahoo_429_circuits_to_sina_without_repeating_per_symbol(self):
        response=requests.Response();response.status_code=429
        error=requests.HTTPError("too many requests",response=response)
        health=SourceHealth()
        expected=pd.DataFrame({"date":["2026-09-28"],"close":[12.]})
        with patch("tools.breadth_sources.fetch_yahoo_prices",side_effect=error) as yahoo, \
             patch("tools.breadth_sources.fetch_sina_us_prices",return_value=(expected,"sina_us_qfq")):
            first=fetch_prices("US.AAPL","2026-09-28",health=health)
            second=fetch_prices("US.MSFT","2026-09-28",health=health)
        self.assertEqual(first[1],"sina_us_qfq")
        self.assertEqual(second[1],"sina_us_qfq")
        self.assertEqual(yahoo.call_count,1)

    def test_sina_us_history_normalizes_and_filters_closed_day(self):
        raw=pd.DataFrame({"date":pd.to_datetime(["2026-09-25","2026-09-28","2026-09-29"]),
                          "close":[10.,12.,13.]})
        with patch("akshare.stock_us_daily",return_value=raw):
            frame,basis=fetch_sina_us_prices("US.AAPL","2026-09-28")
        self.assertEqual(basis,"sina_us_qfq")
        self.assertEqual(frame.date.tolist(),["2026-09-25","2026-09-28"])
    def test_official_member_file_effective_date_must_be_unambiguous(self):
        self.assertEqual(extract_effective_date(pd.DataFrame({"样本代码":["000001","000002"],
                                                              "生效日期":["2026-10-08","2026-10-08"]})),"2026-10-08")
        self.assertIsNone(extract_effective_date(pd.DataFrame({"样本代码":["000001","000002"],
                                                               "生效日期":["2026-10-08","2026-10-09"]})))

    def test_official_comp_count_parser_rejects_missing_number(self):
        self.assertEqual(parse_comp_component_count("<dt># of Components</dt><dd>3,396</dd>"),3396)
        with self.assertRaises(ValueError):parse_comp_component_count("<html>login required</html>")

    def test_futu_crosscheck_uses_exchange_field_not_all_us_stocks(self):
        f=FutuBreadth()
        class Context:
            def get_stock_basicinfo(self,market,stock_type):
                return 0,pd.DataFrame([{"code":"US.AAPL","exchange_type":"US_NASDAQ"},
                                       {"code":"US.IBM","exchange_type":"US_NYSE"}])
        fake_futu=types.SimpleNamespace(Market=types.SimpleNamespace(US="US"),
                                        SecurityType=types.SimpleNamespace(STOCK="STOCK"))
        with patch.dict(sys.modules,{"futu":fake_futu}),patch.object(f,"connect",return_value=Context()),patch.object(f,"throttle"):
            result=f.crosscheck_nasdaq(["US.AAPL","US.IBM"])
        self.assertEqual(result["nasdaq_overlap"],1)
        self.assertEqual(result["non_nasdaq"],["US.IBM"])

    def test_nasdaq_directory_excludes_non_comp_security_types(self):
        header="Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
        rows=["AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N",
              "ADRX|Sample ADR - American Depositary Shares|G|N|N|100|N|N",
              "FUND|Sample Fund ETF|G|N|N|100|Y|N",
              "WARRW|Sample Corp - Warrants|S|N|N|100|N|N",
              "UNITU|Sample Corp - Units|S|N|N|100|N|N",
              "PREF|Sample Corp - Preferred Stock|S|N|N|100|N|N",
              "TEST|NASDAQ TEST STOCK|G|Y|N|100|N|N",
              "File Creation Time: 0929202608:31|||||||",]
        result=parse_nasdaq_directory("\n".join([header,*rows]))
        self.assertEqual(result["symbols"],["US.AAPL","US.ADRX"])
        self.assertEqual(result["source_date"],"2026-09-29")

    def test_sina_uses_regular_time_not_afterhours(self):
        fields=["0"]*30;fields[1]="110";fields[25]="Sep 25 04:00PM EDT";fields[26]="100";fields[29]="2026";fields[24]="Sep 25 08:00PM EDT"
        out=parse_sina_us('var hq_str_gb_aapl="'+','.join(fields)+'";')
        self.assertEqual(out["US.AAPL"]["time"],"2026-09-25T16:00:00")
        self.assertEqual(out["US.AAPL"]["price"],110)
    def test_stockcharts_close_and_intraday_can_be_saved_as_strict_json(self):
        from tools.market_breadth import BreadthStore
        payload={"success":True,"symbols":[{"symbol":"$NAA50R","intradayDate":"2026-09-25 15:00","quoteClose":34.6,"perfSummaryQuote":{"endOfDay":{"date":"2026-09-24","price":33.7}}}]}
        rows=parse_stockcharts(payload,"$NAA50R","2026-09-24",True,pd.Timestamp("2026-09-25 15:10",tz="America/New_York"))
        with tempfile.TemporaryDirectory() as tmp:
            store=BreadthStore(Path(tmp));store.save_benchmark("nasdaq_stockcharts",rows,"stockcharts")
            self.assertEqual(len(store.benchmark("nasdaq_stockcharts")),2)
    def test_stockcharts_wrong_symbol_and_bad_percent_rejected(self):
        payload={"success":True,"symbols":[{"symbol":"$NAA50R","intradayDate":"2026-09-25 16:00","quoteClose":34.6,"perfSummaryQuote":{"endOfDay":{"date":"2026-09-25","price":34.6}}}]}
        rows=parse_stockcharts(payload,"$NAA50R","2026-09-25",False)
        self.assertEqual(rows[0]["percent"],34.6)
        with self.assertRaises(ValueError):parse_stockcharts(payload,"$DOWA50R","2026-09-25",False)
        payload["symbols"][0]["perfSummaryQuote"]["endOfDay"]["price"]=110
        with self.assertRaises(ValueError):parse_stockcharts(payload,"$NAA50R","2026-09-25",False)

    def test_tencent_single_request_history_accepts_beijing_and_filters_unclosed(self):
        payload={"data":{"bj920001":{"qfqday":[["2026-09-24","10","11","12","9","100"],["2026-09-28","11","12","13","10","200"]]}}}
        frame=parse_tencent_history(payload,"bj920001","2026-09-24")
        self.assertEqual(len(frame),1);self.assertEqual(frame.iloc[-1].close,11.)
    def test_beijing_eastmoney_fallback_normalizes_chinese_columns(self):
        from tools.breadth_sources import fetch_prices
        response=pd.DataFrame({"日期":["2026-09-24"],"收盘":[11.2]})
        with patch("tools.breadth_sources.get",side_effect=RuntimeError("Tencent unavailable")),patch("akshare.stock_zh_a_hist",return_value=response):
            frame,source=fetch_prices("BJ.920001","2026-09-24")
        self.assertEqual(source,"eastmoney_qfq")
        self.assertEqual(frame.iloc[0].close,11.2)

    def test_preferred_tencent_failure_tries_domestic_eastmoney(self):
        response=pd.DataFrame({"日期":["2026-09-28"],"收盘":[11.2]})
        with patch("tools.breadth_sources.get",side_effect=RuntimeError("Tencent unavailable")),patch("akshare.stock_zh_a_hist",return_value=response) as eastmoney:
            frame,source=fetch_prices("SH.600007","2026-09-28","2026-09-18","tencent_qfq")
        self.assertEqual(source,"eastmoney_qfq")
        self.assertEqual(frame.iloc[-1].date,"2026-09-28")
        self.assertTrue(eastmoney.called)

    def test_unreachable_domestic_domain_is_not_retried_per_stock(self):
        health=SourceHealth();response=pd.DataFrame({"日期":["2026-09-28"],"收盘":[11.2]})
        with patch("tools.breadth_sources.get",side_effect=requests.ConnectionError("blocked")) as get, \
             patch("akshare.stock_zh_a_hist",return_value=response):
            fetch_prices("SH.600007","2026-09-28",health=health)
            fetch_prices("SH.600015","2026-09-28",health=health)
        self.assertEqual(get.call_count,1)

    def test_tencent_snapshot_one_batch_error_preserves_other_batch(self):
        codes=[f"SH.{i:06d}" for i in range(81)]
        calls=[]
        class Response:
            content=b''
        def fake_get(*args,**kwargs):
            calls.append(args[0])
            if len(calls)==1:raise RuntimeError("first batch failed")
            if "qt.gtimg" in args[0]:
                line='v_sh000080="'+'~'.join(["0"]*30+["20260928150000"]+["0"]*4)+'";'
                return type("R",(),{"content":line.encode("gbk")})()
            return Response()
        with patch("tools.breadth_sources.get",side_effect=fake_get):
            out=fetch_quotes(codes,pd.Timestamp("2026-09-28 15:30",tz="Asia/Shanghai"))
        self.assertIn("SH.000080",out)

    def test_cni_sample_code_column_matches_official_file(self):
        frame=pd.DataFrame({"样本代码":[str(i).zfill(6) for i in range(500)]})
        self.assertEqual(len(parse_members("shenzhen",frame)),500)
    def test_component_count_prevents_truncated_list(self):
        frame=pd.DataFrame({"Constituent Code":["000001","600000"]})
        with self.assertRaises(ValueError):parse_members("csi2000",frame)
    def test_component_file_rejects_duplicate_or_extra_rows(self):
        codes=[str(i).zfill(6) for i in range(2000)]
        with self.assertRaises(ValueError):
            parse_members("csi2000",pd.DataFrame({"Constituent Code":codes+[codes[0]]}))
    def test_csi2000_keeps_beijing_constituents(self):
        codes=[str(i).zfill(6) for i in range(1961)]+[str(920000+i) for i in range(39)]
        rows=parse_members("csi2000",pd.DataFrame({"Constituent Code":codes}))
        self.assertEqual(len(rows),2000)
        self.assertIn("BJ.920001",rows)
    def test_dow_ignores_cash_and_demands_30(self):
        frame=pd.DataFrame({"Ticker":[f"T{i}" for i in range(30)]+["USD"],"Name":["Company"]*30+["US DOLLAR"]})
        rows=parse_members("dow",frame)
        self.assertEqual(len(rows),30);self.assertNotIn("US.USD",rows)
    def test_yahoo_adjustment_and_exclude_unclosed_session(self):
        payload={"chart":{"result":[{"meta":{"exchangeTimezoneName":"America/New_York"},"timestamp":[1757943000,1758029400],"indicators":{"quote":[{"close":[100,110]}],"adjclose":[{"adjclose":[50,55]}]}}],"error":None}}
        out=parse_yahoo_history(payload,"2025-09-15")
        self.assertEqual(len(out),1);self.assertEqual(out.iloc[0].close,50)
    def test_external_indicator_rejects_missing_dates_out_of_range(self):
        for rows in [[{"date":"bad","percent":50}],[{"date":"2026-01-01","percent":101}]]:
            with self.assertRaises(ValueError):validate_external_breadth(rows)
    def test_futu_reserve_only_blocks_new_symbols(self):
        f=FutuBreadth();f.used={"US.AAPL"};f.remaining=10
        self.assertTrue(f.can_history("US.AAPL"));self.assertFalse(f.can_history("US.MSFT"))
        f.remaining=11;self.assertTrue(f.can_history("US.MSFT"))
    def test_futu_batches_and_minimum_interval(self):
        f=FutuBreadth();times=[]
        with patch("tools.breadth_sources.time.monotonic",side_effect=[10,10.2,11.2]),patch("tools.breadth_sources.time.sleep",side_effect=lambda x: times.append(x)):
            f.throttle();f.throttle()
        self.assertAlmostEqual(times[0],.8)
        self.assertEqual([len(b) for b in f.batches(list(range(450)))],[200,200,50])

    def test_futu_skips_beijing_and_keeps_successful_batches_after_failure(self):
        f=FutuBreadth();calls=[]
        class Context:
            def get_market_snapshot(self,codes):
                calls.append(codes)
                if len(calls)==2:return 1,"batch failed"
                return 0,pd.DataFrame([{"code":code,"last_price":11.,"prev_close_price":10.,"update_time":"2026-09-28 15:00:00"} for code in codes])
        codes=["BJ.920001"]+[f"SH.{i:06d}" for i in range(401)]
        with patch.object(f,"connect",return_value=Context()),patch.object(f,"throttle"):
            out=f.quotes(codes)
        self.assertEqual([len(batch) for batch in calls],[200,200,1])
        self.assertTrue(all(not code.startswith("BJ.") for batch in calls for code in batch))
        self.assertEqual(len(out),201)

if __name__=="__main__":unittest.main()
