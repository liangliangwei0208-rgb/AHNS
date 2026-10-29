"""独立行情读取进程：只读网络，不导入估算器、不写业务缓存。

父进程控制硬超时；即使 HTTP 慢速传输或 OpenD 阻塞，也能整体终止。
"""
from __future__ import annotations

import csv
import io
import json
import math
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("非有效正数")
    return number


def fetch_http(source, timeout):
    if source.startswith("yahoo"):
        host = "query1" if source == "yahoo1" else "query2"
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/%5EVIX?" + urlencode(
            {"range": "1d", "interval": "1m", "includePrePost": "true"})
    else:
        url = {"cboe": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
               "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"}[source]
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as response:
        body = response.read().decode("utf-8-sig")
    if source.startswith("yahoo"):
        data = json.loads(body)["chart"]["result"][0]
        meta = data.get("meta") or {}
        points = zip(data.get("timestamp") or [], ((data.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        valid = [(stamp, value) for stamp, value in points if value is not None]
        if not valid:
            valid = [(meta.get("regularMarketTime"), meta.get("regularMarketPrice"))]
        stamp, value = valid[-1]
        at = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        return {"value": positive(value), "trade_date": at.astimezone(ZoneInfo("America/Chicago")).date().isoformat(),
                "quote_time_bj": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()}
    values = []
    for row in csv.DictReader(io.StringIO(body)):
        row = {str(k).upper(): v for k, v in row.items()}
        try:
            day = row.get("DATE") or row.get("OBSERVATION_DATE")
            day = datetime.strptime(day, "%m/%d/%Y").date().isoformat() if "/" in day else datetime.fromisoformat(day).date().isoformat()
            values.append({"value": positive(row.get("CLOSE") or row.get("VIXCLS")), "trade_date": day})
        except (ValueError, TypeError):
            continue
    if not values:
        raise ValueError("VIX CSV 无有效收盘点位")
    return max(values, key=lambda item: item["trade_date"])


def fetch_futu(args):
    import socket
    # 连接探测先行，避免 SDK 在 OpenD 离线时长时间自动重连。
    host, port = args["host"], args["port"]
    with socket.create_connection((host, port), timeout=0.5):
        pass
    from futu import OpenQuoteContext, RET_OK, KLType, AuType
    ctx = OpenQuoteContext(host=host, port=port)
    try:
        ctx.set_sync_query_connect_timeout(2)
        code = "US." + args["ticker"]
        if args["mode"] == "daily":
            ret, data, _ = ctx.request_history_kline(code, start=args["start"], end=args["day"],
                ktype=KLType.K_DAY, autype=AuType.NONE, max_count=30, extended_time=False)
            if ret != RET_OK:
                raise RuntimeError(str(data))
            return {"rows": [{"time_key":str(row["time_key"]), "close":positive(row["close"])}
                             for _, row in data.iterrows()]}
        ret, data = ctx.get_market_snapshot([code])
        if ret != RET_OK or data is None or data.empty:
            raise RuntimeError(str(data))
        # SDK 会给 ETF 补齐大量无关的期权/窝轮 NaN 列；只传需要的字段。
        row = data.iloc[0]
        snapshot = {"update_time":str(row.get("update_time", ""))}
        for key in ("pre_price", "last_price", "after_price", "prev_close_price", "after_change_val", "after_change_rate"):
            try:
                value = float(row.get(key))
                snapshot[key] = value if math.isfinite(value) else None
            except (ValueError, TypeError):
                snapshot[key] = None
        return {"snapshot": snapshot}
    finally:
        ctx.close()


def main():
    args = json.loads(sys.argv[1])
    try:
        result = fetch_futu(args) if args["source"] == "futu" else fetch_http(args["source"], args["timeout"])
    except HTTPError as exc:
        result = {"error": str(exc), "http_status": exc.code, "retry_after": exc.headers.get("Retry-After", "")}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, default=str, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
