import unittest
import tempfile
import sys
import types
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from tools.breadth_sources import parse_members, parse_nasdaq_directory, parse_comp_component_count, parse_ndx_official_list, parse_ndx_api_list, parse_ndx_component_count, parse_ndx_pdf_baseline, parse_ndx_release, apply_ndx_notices, parse_ndx_archive_page, fetch_ndx_notice_archive, parse_spglobal_dow_list, parse_szse_shenzhen_notice, extract_effective_date, parse_yahoo_history, FutuBreadth, SourceHealth, validate_external_breadth, parse_sina_us, parse_stockcharts, parse_tencent_history, fetch_prices, fetch_quotes, fetch_eastmoney_us_prices, fetch_sina_us_prices
import requests

class SourceTests(unittest.TestCase):
    def test_ndx_notice_archive_reports_oldest_page_date(self):
        page=("<table><tr><td>Sep 29, 2026</td><td><a href='/node/111'>Other news</a></td></tr>"
              "<tr><td>Jun 11, 2026</td><td><a href='/node/110541'>Nasdaq-100 Index June 2026 Quarterly Changes</a></td></tr>"
              "<tr><td>Apr 30, 2026</td><td><a href='/node/100'>Other older news</a></td></tr></table>")
        parsed=parse_ndx_archive_page(page)
        self.assertEqual(parsed["oldest_date"],"2026-04-30")
        self.assertEqual(parsed["release_urls"],["https://ir.nasdaq.com/node/110541"])

    def test_ndx_archive_does_not_reapply_notices_before_pdf_baseline(self):
        page=("<table><tr><td>Jun 11, 2026</td><td><a href='/node/new'>Nasdaq-100 June Changes</a></td></tr>"
              "<tr><td>Apr 10, 2026</td><td><a href='/node/old'>Nasdaq-100 April Changes</a></td></tr></table>")
        class Reply:
            def __init__(self,body):self.text=body
        with patch("tools.breadth_sources.get",side_effect=[Reply(page),Reply("release")]) as get, \
             patch("tools.breadth_sources.parse_ndx_release",return_value={"added":[],"removed":[],
                 "effective_date":"2026-06-22","url":"https://ir.nasdaq.com/node/new"}):
            archive=fetch_ndx_notice_archive("2026-05-01")
        self.assertEqual(archive["archive_pages"],1)
        self.assertEqual(get.call_args_list[1].args[0],"https://ir.nasdaq.com/node/new")

    def test_ndx_pdf_baseline_and_dated_official_notices(self):
        rows="\n".join(f"ISSUER {i} T{i:03d} 0.99" for i in range(101))
        baseline=parse_ndx_pdf_baseline("Nasdaq 100\nTicker : NDX\nData as of: 05/01/2026\nName Symbol Weight (%)\n"+rows)
        self.assertEqual(baseline["source_date"],"2026-05-01")
        self.assertEqual(len(baseline["symbols"]),101)
        release=("Nasdaq-100 Index® June 2026 Quarterly Changes. The following five companies "
                 "will be added to the Index: A (Nasdaq: NEW). The following five companies "
                 "will be removed from the Index: B (Nasdaq: T000). These changes become "
                 "effective prior to market open on Monday, June 22, 2026.")
        notice=parse_ndx_release(release,"https://ir.nasdaq.com/n")
        self.assertEqual(notice["effective_date"],"2026-06-22")
        self.assertEqual(notice["added"],["US.NEW"])
        self.assertEqual(notice["removed"],["US.T000"])
        updated=apply_ndx_notices(baseline["symbols"],[notice],"2026-09-30")
        self.assertEqual(len(updated),101)
        self.assertIn("US.NEW",updated)
        self.assertNotIn("US.T000",updated)

    def test_ndx_single_replacement_and_add_only_notices_ignore_corporate_ticker(self):
        first=("Nasdaq-100 Index. Nasdaq (Nasdaq: NDAQ) today announced that Lumentum Holdings "
               "(Nasdaq: LITE) will become a component of the Nasdaq-100 Index replacing CoStar "
               "(Nasdaq: CSGP) prior to market open on Monday, May 18, 2026.")
        changed=parse_ndx_release(first,"https://ir.nasdaq.com/one")
        self.assertEqual(changed["added"],["US.LITE"])
        self.assertEqual(changed["removed"],["US.CSGP"])
        second=("Nasdaq-100 Index. Nasdaq (Nasdaq: NDAQ) today announced that Space Exploration "
                "(Nasdaq: SPCX) will become a component of the Nasdaq-100 Index prior to market open "
                "on Tuesday, July 7, 2026.")
        added=parse_ndx_release(second,"https://ir.nasdaq.com/two")
        self.assertEqual(added["added"],["US.SPCX"])
        self.assertEqual(added["removed"],[])

    def test_shenzhen_notice_extracts_date_and_exact_adjustment(self):
        page=("深圳证券交易所和深圳证券信息有限公司决定于2026年10月8日对深证成指实施样本股定期调整。"
              "<h2>深证成份指数样本股调整名单</h2><table>"
              "<tr><th>调入名单</th><th>简称</th><th>调出名单</th><th>简称</th></tr>"
              "<tr><td>000969</td><td>新增</td><td>000401</td><td>剔除</td></tr></table>")
        notice=parse_szse_shenzhen_notice(page,"https://www.szse.cn/disclosure/notice/t20260929_123.html")
        self.assertEqual(notice["effective_date"],"2026-10-08")
        self.assertEqual(notice["added"],["SZ.000969"])
        self.assertEqual(notice["removed"],["SZ.000401"])

    def test_dow_official_list_must_have_all_30_symbols(self):
        rows="".join(f"<tr><td>Issuer {i}</td><td>T{i:02d}</td></tr>" for i in range(30))
        page="<h1>Dow Jones Industrial Average</h1><table><tr><th>Constituent</th><th>Symbol</th></tr>"+rows+"</table>"
        self.assertEqual(len(parse_spglobal_dow_list(page)),30)
        with self.assertRaisesRegex(ValueError,"不完整"):
            parse_spglobal_dow_list(page.replace(rows,rows[:rows.find("<tr>",10)]))

    def test_ndx_official_article_requires_complete_unique_symbol_table(self):
        rows="".join(f"<tr><td>Issuer {i}</td><td>T{i:03d}</td></tr>" for i in range(101))
        page=("<h1>Understanding the Nasdaq-100 Index</h1><time datetime='2026-09-29'>"
              "Sep 29, 2026</time><h2>Nasdaq-100 Company Breakdown</h2>"
              "<table><tr><th>Company Name</th><th>Security Symbol</th></tr>"+rows+"</table>")
        parsed=parse_ndx_official_list(page)
        self.assertEqual(len(parsed["symbols"]),101)
        self.assertEqual(parsed["source_date"],"2026-09-29")
        self.assertIn("US.T000",parsed["symbols"])
        self.assertEqual(parse_ndx_component_count("<dt># of Components</dt><dd>101</dd>"),101)
        with self.assertRaisesRegex(ValueError,"重复"):
            parse_ndx_official_list(page.replace("T100","T000"))
        with self.assertRaisesRegex(ValueError,"不完整"):
            parse_ndx_official_list(page.replace(rows,rows[:rows.find("<tr>",10)]))

    def test_ndx_json_requires_all_reported_unique_securities(self):
        rows=[{"symbol":f"T{i:03d}","companyName":f"Issuer {i}"} for i in range(101)]
        body={"data":{"totalrecords":101,"limit":101,"offset":0,"date":"Sep 29, 2026",
                      "data":{"rows":rows}}}
        parsed=parse_ndx_api_list(body)
        self.assertEqual(len(parsed["symbols"]),101)
        self.assertEqual(parsed["source_date"],"2026-09-29")
        with self.assertRaisesRegex(ValueError,"截断"):
            parse_ndx_api_list({**body,"data":{**body["data"],"data":{"rows":rows[:-1]}}})
        with self.assertRaisesRegex(ValueError,"重复"):
            parse_ndx_api_list({**body,"data":{**body["data"],"data":{"rows":rows[:-1]+[rows[0]]}}})

    def test_ndx_fetch_uses_official_json_and_rejects_count_mismatch(self):
        class FrozenTimestamp(pd.Timestamp):
            @classmethod
            def now(cls,tz=None):return pd.Timestamp('2026-09-30',tz=tz)
        class Reply:
            def __init__(self,body):self.text=body
            def json(self):return {"data":{"totalrecords":101,"limit":101,"offset":0,
                "date":"Sep 29, 2026","data":{"rows":[{"symbol":f"T{i:03d}",
                "companyName":f"Issuer {i}"} for i in range(101)]}}}
        with patch("tools.breadth_sources.pd.Timestamp",FrozenTimestamp), \
             patch("tools.breadth_sources.get",side_effect=[Reply("api"),Reply("<dt># of Components</dt><dd>101</dd>")]):
            from tools.breadth_sources import fetch_members
            symbols,source,meta=fetch_members("nasdaq100",futu=None)
        self.assertEqual(len(symbols),101)
        self.assertEqual(source,"https://api.nasdaq.com/api/quote/list-type/nasdaq100")
        self.assertTrue(meta["official_current"])
        with patch("tools.breadth_sources.get",side_effect=[Reply("api"),Reply("<dt># of Components</dt><dd>102</dd>")]):
            with self.assertRaisesRegex(ValueError,"不符"):
                fetch_members("nasdaq100",futu=None)

    def test_ndx_futu_list_survives_official_network_outage(self):
        class Futu:
            def index_members(self,key):
                self_test.assertEqual(key,"nasdaq100")
                return [f"US.T{i:03d}" for i in range(101)]
        self_test=self
        from tools.breadth_sources import fetch_members
        with patch("tools.breadth_sources.get",side_effect=requests.ConnectionError("offline")):
            symbols,source,meta=fetch_members("nasdaq100",futu=Futu())
        self.assertEqual(len(symbols),101)
        self.assertEqual(source,"futu_opend:US.NDX")
        self.assertTrue(meta["futu_verified"])

    def test_ndx_future_official_notice_is_staged_from_current_api_list(self):
        today=pd.Timestamp.now(tz="America/New_York")
        effective=str((today+pd.Timedelta(days=7)).date())
        class Reply:
            text="<dt># of Components</dt><dd>101</dd>"
            def json(self):return {"data":{"totalrecords":101,"limit":101,"offset":0,
                "date":today.strftime("%b %d, %Y"),"data":{"rows":[{"symbol":f"T{i:03d}",
                "companyName":f"Issuer {i}"} for i in range(101)]}}}
        notice={"effective_date":effective,"added":["US.NEW"],"removed":["US.T000"],
                "url":"https://ir.nasdaq.com/notice"}
        archive={"notices":[notice],"archive_oldest_date":str((today-pd.Timedelta(days=45)).date())}
        from tools.breadth_sources import fetch_members
        with patch("tools.breadth_sources.get",return_value=Reply()), \
             patch("tools.breadth_sources.fetch_ndx_notice_archive",return_value=archive):
            symbols,_,meta=fetch_members("nasdaq100")
        self.assertEqual(len(symbols),101)
        self.assertEqual(meta["future_memberships"][0]["effective_date"],effective)
        self.assertIn("US.NEW",meta["future_memberships"][0]["symbols"])

    def test_domestic_futu_list_survives_official_file_outage(self):
        class Futu:
            def index_members(self,key):
                return [f"SZ.{i:06d}" for i in range(500)]
        from tools.breadth_sources import fetch_members
        with patch("tools.breadth_sources.requests.Session",side_effect=requests.ConnectionError("offline")), \
             patch("tools.breadth_sources.get",side_effect=requests.ConnectionError("offline")):
            symbols,source,meta=fetch_members("shenzhen",futu=Futu())
        self.assertEqual(len(symbols),500)
        self.assertEqual(source,"futu_opend:SZ.399001")

    def test_domestic_official_file_wins_when_futu_members_disagree(self):
        class Futu:
            def index_members(self,key):return [f"SZ.{i:06d}" for i in range(500)]
        class Reply:
            content=b"xls"
        official=[f"SZ.{i:06d}" for i in range(1,501)]
        from tools.breadth_sources import fetch_members
        with patch("tools.breadth_sources.requests.Session",side_effect=requests.ConnectionError("direct offline")), \
             patch("tools.breadth_sources.get",return_value=Reply()), \
             patch("tools.breadth_sources.pd.read_excel",return_value=pd.DataFrame({"证券代码":["000001"]})), \
             patch("tools.breadth_sources.parse_members",return_value=official), \
             patch("tools.breadth_sources.fetch_shenzhen_notices",return_value=[]):
            symbols,source,meta=fetch_members("shenzhen",futu=Futu())
        self.assertEqual(symbols,official)
        self.assertTrue(source.startswith("https://www.cnindex.com.cn/"))
        self.assertEqual(meta["futu_difference"],2)

    def test_futu_new_index_valuation_api_paginates_complete_list(self):
        class Context:
            def get_plate_stock(self,code):return -1,"unknown plate"
            def get_valuation_plate_stock_list(self,code,next_key=None,num=None):
                self_test.assertEqual(code,"US.NDX")
                self_test.assertEqual(num,50)
                offset=int(next_key or 0)
                rows=[{"symbol":f"US.T{i:03d}"} for i in range(offset,min(offset+50,101))]
                return 0,{"count":101,"stock_list":rows,
                          "next_key":"-1" if offset+50>=101 else str(offset+50)}
        self_test=self
        futu=FutuBreadth()
        with patch.object(futu,"connect",return_value=Context()),patch.object(futu,"throttle"):
            self.assertEqual(len(futu.index_members("nasdaq100")),101)

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
