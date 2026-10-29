"""真实解析器在离线响应上工作，覆盖 CSV 和富途调用参数。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tools import market_benchmark_worker as worker


@pytest.mark.parametrize("source,text,day,value", [
    ("cboe","DATE,OPEN,HIGH,LOW,CLOSE\n10/27/2026,1,1,1,18.5\n10/28/2026,1,1,1,17.5\n","2026-10-28",17.5),
    ("fred","observation_date,VIXCLS\n2026-10-28,18.0\n2026-10-29,.\n","2026-10-28",18.0),
])
def test_csv_parses_actual_source_formats(source,text,day,value):
    response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.read.return_value=text.encode()
    with patch.object(worker,"urlopen",return_value=response):
        item=worker.fetch_http(source,5)
    assert item=={"value":value,"trade_date":day}


def test_yahoo_timestamp_and_price_are_paired():
    response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.read.return_value=json.dumps({"chart":{"result":[{"timestamp":[1793304000,1793304060],"indicators":{"quote":[{"close":[18.0,None]}]}}]}}).encode()
    with patch.object(worker,"urlopen",return_value=response):
        item=worker.fetch_http("yahoo1",5)
    assert item["value"]==18
    assert item["quote_time_bj"]


def test_futu_daily_requests_unadjusted_regular_bars_and_closes_context():
    ctx=Mock();ctx.request_history_kline.return_value=(0,pd.DataFrame({"time_key":["2026-10-27","2026-10-28"],"close":[100,101]}),None)
    sdk=SimpleNamespace(OpenQuoteContext=Mock(return_value=ctx),RET_OK=0,KLType=SimpleNamespace(K_DAY="daily"),AuType=SimpleNamespace(NONE="raw"))
    with patch.dict("sys.modules",{"futu":sdk}),patch("socket.create_connection"):
        payload=worker.fetch_futu({"ticker":"XOP","mode":"daily","start":"2026-10-27","day":"2026-10-28","host":"127.0.0.1","port":11111})
    assert ctx.request_history_kline.call_args.kwargs["autype"]=="raw"
    assert ctx.request_history_kline.call_args.kwargs["extended_time"] is False
    assert len(payload["rows"])==2
    ctx.close.assert_called_once()


def test_full_futu_worker_serializes_normal_etf_with_unused_nan(capsys):
    ctx=Mock();ctx.get_market_snapshot.return_value=(0,pd.DataFrame([{"update_time":"2026-10-29 10:44:00","last_price":101.,"prev_close_price":100.,"option_delta":float("nan")}]))
    sdk=SimpleNamespace(OpenQuoteContext=Mock(return_value=ctx),RET_OK=0,KLType=SimpleNamespace(K_DAY="daily"),AuType=SimpleNamespace(NONE="raw"))
    args={"source":"futu","ticker":"QQQ","mode":"intraday","host":"127.0.0.1","port":11111}
    with patch.dict("sys.modules",{"futu":sdk}),patch("socket.create_connection"),patch("sys.argv",["worker",json.dumps(args)]):
        worker.main()
    result=json.loads(capsys.readouterr().out)
    assert result["snapshot"]["last_price"]==101
    assert "option_delta" not in result["snapshot"]
