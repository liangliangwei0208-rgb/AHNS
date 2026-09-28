"""广度数据源适配。免费公开数据优先，富途仅补缺口。"""
from __future__ import annotations
import io
import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote
import pandas as pd
import requests
from tools.market_breadth import clean_prices, eligible_quote
from tools.configs.market_breadth_configs import BREADTH_MARKETS, BREADTH_FUTU_RESERVE, BREADTH_FUTU_BATCH_SIZE


class SourceHealth:
    """本轮网络故障熔断；单只证券的空数据不连坐同源其他证券。"""
    def __init__(self):
        self.unreachable=set();self.lock=threading.Lock()

    def available(self,source):
        with self.lock:return source not in self.unreachable

    def failed(self,source,error):
        if isinstance(error,(requests.ConnectionError,requests.Timeout)):
            with self.lock:self.unreachable.add(source)


def get(url,**kwargs):
    # 所有网络调用有超时；继承本机已有代理，不写入任何凭据。
    headers=kwargs.pop("headers", {"User-Agent":"Mozilla/5.0"})
    r=requests.get(url,timeout=(10,15),headers=headers,**kwargs)
    r.raise_for_status()
    if not r.content:raise ValueError("数据源返回空内容")
    return r


def parse_members(key,frame):
    spec=BREADTH_MARKETS[key]
    if key=="dow":
        d=frame.dropna(subset=["Ticker","Name"])
        d=d.loc[~d.Name.astype(str).str.contains("DOLLAR|CASH",case=False,regex=True)]
        symbols=["US."+str(s).strip() for s in d.Ticker if re.fullmatch(r"[A-Z0-9.-]+",str(s))]
    else:
        col=next((c for c in frame.columns if "Constituent Code" in str(c) or "样本代码" in str(c) or "证券代码" in str(c) or "成分券代码" in str(c)),None)
        if col is None:raise ValueError("成分文件没有证券代码列")
        codes=[str(v).split(".")[0].zfill(6) for v in frame[col].dropna()]
        symbols=[("BJ." if s.startswith(("4","8","9")) else "SH." if s.startswith("6") else "SZ.")+s for s in codes if re.fullmatch(r"[034689]\d{5}",s)]
    symbols=sorted(set(symbols))
    if len(symbols)!=spec["expected"]:raise ValueError(f"{key} 成分不完整：{len(symbols)}/{spec['expected']}")
    return symbols


def fetch_members(key):
    if key=="nasdaq":
        # 上市目录包含ETF、权证等，不能冒充纳斯达克综合指数完整成分。
        raise ValueError("纳斯达克完整成分尚未验证；需可用现成广度数据或核实的完整成分文件")
    if key=="dow":
        url="https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-dia.xlsx"
        raw=pd.read_excel(io.BytesIO(get(url).content),header=None)
        headers=raw.index[raw.iloc[:,0].eq("Name")]
        if len(headers)!=1:raise ValueError("DIA持仓文件格式变化")
        index=int(headers[0]);frame=raw.iloc[index+1:].copy();frame.columns=raw.iloc[index]
    elif key=="shenzhen":
        url="https://www.cnindex.com.cn/sample-detail/download?indexcode=399001"
        # 国证官网在本机直连稳定，代理链路偶发超时；失败后才使用环境代理。
        try:
            with requests.Session() as session:
                session.trust_env=False
                response=session.get(url,timeout=(5,12));response.raise_for_status()
                content=response.content
        except requests.RequestException:content=get(url).content
        frame=pd.read_excel(io.BytesIO(content),dtype=str)
    else:
        index=BREADTH_MARKETS[key]["index"]
        url=f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{index}cons.xls"
        frame=pd.read_excel(io.BytesIO(get(url).content),dtype=str)
    return parse_members(key,frame),url


def validate_external_breadth(rows):
    d=pd.DataFrame(rows)
    if not {"date","percent"}.issubset(d.columns):raise ValueError("广度文件缺少日期或百分比")
    dates=pd.to_datetime(d.date,errors="coerce")
    value=pd.to_numeric(d.percent,errors="coerce")
    if dates.isna().any() or value.isna().any() or not value.between(0,100).all():raise ValueError("广度日期或数值无效")
    d["date"]=dates.dt.strftime("%Y-%m-%d");d["percent"]=value
    if d.date.duplicated().any():raise ValueError("广度日期重复")
    return d.sort_values("date")


def parse_yahoo_history(payload,complete_day):
    result=payload["chart"]["result"]
    if not result:raise ValueError(str(payload["chart"].get("error")))
    data=result[0];zone=data["meta"].get("exchangeTimezoneName","America/New_York")
    dates=pd.to_datetime(data["timestamp"],unit="s",utc=True).tz_convert(zone).strftime("%Y-%m-%d")
    # 采用同一个adjclose序列；最新完整收盘与当日原价之间的复权转换由quotes适配层处理。
    close=data["indicators"]["adjclose"][0]["adjclose"]
    d=clean_prices(pd.DataFrame({"date":dates,"close":close}))
    return d.loc[d.date<=complete_day].tail(400)


def yahoo_symbol(code):
    market,ticker=code.split(".",1)
    return ticker.replace(".","-") if market=="US" else ticker+(".SS" if market=="SH" else ".BJ" if market=="BJ" else ".SZ")


def fetch_yahoo_prices(code,complete_day,start=None):
    params={"interval":"1d","events":"div,splits"}
    if start:
        params.update(period1=int(pd.Timestamp(start,tz="UTC").timestamp()),period2=int(pd.Timestamp(complete_day,tz="UTC").timestamp())+172800)
    else:params["range"]="2y"
    data=get("https://query1.finance.yahoo.com/v8/finance/chart/"+quote(yahoo_symbol(code),safe=""),params=params).json()
    return parse_yahoo_history(data,complete_day),"yahoo_adjclose"


def parse_tencent_history(payload,symbol,complete_day):
    data=payload["data"][symbol]
    rows=data.get("qfqday",data.get("day",[]))
    if not rows:raise ValueError("腾讯日线为空")
    frame=clean_prices(pd.DataFrame([{"date":r[0],"close":r[2]} for r in rows]))
    return frame.loc[frame.date<=complete_day].tail(400)


def fetch_prices(code,complete_day,start=None,preferred=None,health=None):
    errors=[]
    health=health or SourceHealth()
    # 同源优先复用；失败后仍试国内其他源，换源必须取完整窗口核对复权基准。
    if not code.startswith("US."):
        import akshare as ak
        symbol=code.replace(".","").lower()
        sources=["tencent_qfq","eastmoney_qfq"] if code.startswith("BJ.") else ["tencent_qfq","eastmoney_qfq","sina_qfq"]
        if preferred in sources:sources.remove(preferred);sources.insert(0,preferred)
        for source in sources:
            if not health.available(source):continue
            try:
                begin=(start if preferred==source and start else str((pd.Timestamp(complete_day)-pd.Timedelta(days=700)).date())).replace("-","")
                if source=="tencent_qfq":
                    # 一次640根覆盖建库窗口；增量请求保留重叠区用于发现复权变化。
                    param=f"{symbol},day,{start if preferred==source and start else ''},{complete_day},640,qfq"
                    payload=get("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",params={"param":param}).json()
                    d=parse_tencent_history(payload,symbol,complete_day)
                elif source=="eastmoney_qfq":
                    d=ak.stock_zh_a_hist(symbol=code.split(".")[1],start_date=begin,end_date=complete_day.replace("-",""),adjust="qfq",timeout=12)
                    d=d.rename(columns={"日期":"date","收盘":"close"})
                else:
                    # 新浪底层无独立超时参数；外层worker有硬超时。
                    d=ak.stock_zh_a_daily(symbol=symbol,start_date=begin,end_date=complete_day.replace("-",""),adjust="qfq")
                d=clean_prices(d)
                if d.empty:raise ValueError("空日线")
                return d.loc[d.date<=complete_day].tail(400),source
            except Exception as e:
                health.failed(source,e);errors.append(f"{source}: {str(e)[:120]}")
    if health.available("yahoo_adjclose"):
        try:return fetch_yahoo_prices(code,complete_day,start if preferred=="yahoo_adjclose" else None)
        except Exception as e:
            health.failed("yahoo_adjclose",e);errors.append("yahoo_adjclose: "+str(e)[:120])
    raise RuntimeError("; ".join(errors))


def fetch_quotes(codes,now,health=None,errors=None):
    """批量A股报价；返回原始价格/昨收，后续按缓存复权比例换算。"""
    quotes={};health=health or SourceHealth();errors=errors if errors is not None else []
    cn=[c for c in codes if not c.startswith("US.")]
    for start in range(0,len(cn),80):
        if not health.available("tencent_snapshot"):break
        batch=cn[start:start+80]
        url="https://qt.gtimg.cn/q="+",".join(c.replace(".","").lower() for c in batch)
        try:text=get(url).content.decode("gbk",errors="replace")
        except Exception as e:
            health.failed("tencent_snapshot",e);errors.append("腾讯快照: "+str(e)[:160]);continue
        for line in text.splitlines():
            m=re.search(r'v_(sh|sz|bj)(\d{6})="(.*)"',line)
            if not m:continue
            fields=m[3].split("~")
            if len(fields)<31:continue
            stamp=pd.to_datetime(fields[30],format="%Y%m%d%H%M%S",errors="coerce")
            if pd.isna(stamp):continue
            code=m[1].upper()+"."+m[2]
            try:quotes[code]=dict(price=float(fields[3]),prev_close=float(fields[4]),time=stamp.isoformat(),source="tencent_snapshot")
            except ValueError:continue
    # 新浪报价带有逐只日期与时间，可补腾讯失败的沪深批次；北交所仍走腾讯日线。
    missing=[c for c in cn if c not in quotes and c.startswith(("SH.","SZ."))]
    for start in range(0,len(missing),80):
        if not health.available("sina_snapshot_cn"):break
        batch=missing[start:start+80]
        url="https://hq.sinajs.cn/list="+",".join(c.replace(".","").lower() for c in batch)
        try:
            response=get(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://finance.sina.com.cn/"})
            quotes.update(parse_sina_cn(response.content.decode("gbk",errors="replace")))
        except Exception as e:
            health.failed("sina_snapshot_cn",e);errors.append("新浪A股快照: "+str(e)[:160])
    us=[c for c in codes if c.startswith("US.")]
    for start in range(0,len(us),80):
        if not health.available("sina_snapshot_us"):break
        url="https://hq.sinajs.cn/list="+",".join("gb_"+c[3:].lower() for c in us[start:start+80])
        try:
            response=get(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://finance.sina.com.cn/"})
            quotes.update(parse_sina_us(response.content.decode("gbk",errors="replace")))
        except Exception as e:
            health.failed("sina_snapshot_us",e);errors.append("新浪美股快照: "+str(e)[:160])
    return quotes


def parse_sina_cn(text):
    out={}
    for market,symbol,record in re.findall(r'hq_str_(sh|sz)(\d{6})="([^"\n]*)"',text):
        fields=record.split(",")
        if len(fields)<32:continue
        try:
            stamp=pd.Timestamp(fields[30].strip()+" "+fields[31].strip())
            out[market.upper()+"."+symbol]=dict(price=float(fields[3]),prev_close=float(fields[2]),
                                                  time=stamp.isoformat(),source="sina_snapshot_cn")
        except (ValueError,TypeError):continue
    return out


def parse_sina_us(text):
    out={}
    for symbol,record in re.findall(r'hq_str_gb_([A-Za-z0-9._-]+)="([^"\n]*)"',text):
        fields=record.split(",")
        if len(fields)<30:continue
        try:
            # 新浪同时带盘前盘后字段；这里只读取第25项常规交易时间。
            stamp=re.sub(r" (EDT|EST)$","",fields[25])
            parsed=datetime.strptime(fields[29]+" "+stamp,"%Y %b %d %I:%M%p")
            out["US."+symbol.upper()]=dict(price=float(fields[1]),prev_close=float(fields[26]),time=parsed.isoformat(),source="sina_regular")
        except (ValueError,IndexError):continue
    return out


def parse_stockcharts(payload,symbol,complete_day,regular,now=None):
    if not payload.get("success"):raise ValueError("StockCharts返回失败")
    data=next((r for r in payload.get("symbols",[]) if r.get("symbol")==symbol),None)
    if data is None:raise ValueError("StockCharts证券代码不匹配")
    end=data.get("perfSummaryQuote",{}).get("endOfDay",{})
    rows=[]
    if end.get("date") and str(end["date"])[:10]<=complete_day:
        rows.append(dict(date=end["date"],percent=end.get("price"),kind="close",valid=None,total=None,coverage=None))
    if regular and now is not None and eligible_quote(data.get("intradayDate"),now,"US",60):
        rows.append(dict(date=str(data["intradayDate"])[:10],percent=data.get("quoteClose"),kind="intraday",valid=None,total=None,coverage=None,observed_at=pd.Timestamp(data["intradayDate"],tz="America/New_York").isoformat()))
    if not rows:raise ValueError("StockCharts没有匹配交易日期的数值")
    return validate_external_breadth(rows).to_dict("records")


def fetch_stockcharts(symbol,clock):
    # 此接口来自免费symbolsummary页的公开请求；不使用登录历史下载接口。
    payload=get("https://stockcharts.com/json/data",params={"cmd":"get-symbol-data","symbols":symbol,"optionalFields":"symbolsummary","src":"freecharts-symbol-summary"},headers={"User-Agent":"Mozilla/5.0","Referer":"https://stockcharts.com/freecharts/symbolsummary.html?sym="+quote(symbol)}).json()
    return parse_stockcharts(payload,symbol,clock["complete_day"],clock["regular"],clock["now"])


class FutuBreadth:
    def __init__(self):
        self.ctx=None;self.remaining=0;self.used=set();self.last_call=None;self.quote_errors=[]
    @staticmethod
    def supports_code(code):return code.startswith(("US.","HK.","SH.","SZ."))
    def connect(self):
        if self.ctx is None:
            host=os.environ.get("AHNS_BREADTH_FUTU_HOST","127.0.0.1");port=int(os.environ.get("AHNS_BREADTH_FUTU_PORT","11111"))
            with socket.create_connection((host,port),timeout=1):pass
            from futu import OpenQuoteContext
            self.ctx=OpenQuoteContext(host=host,port=port)
        return self.ctx
    def close(self):
        if self.ctx is not None:self.ctx.close();self.ctx=None
    def throttle(self):
        now=time.monotonic()
        if self.last_call is not None and now-self.last_call<1:time.sleep(1-(now-self.last_call))
        self.last_call=time.monotonic() if self.last_call is not None else now
    def batches(self,codes):
        for i in range(0,len(codes),BREADTH_FUTU_BATCH_SIZE):yield codes[i:i+BREADTH_FUTU_BATCH_SIZE]
    def refresh_quota(self):
        self.throttle();ret,data=self.connect().get_history_kl_quota(get_detail=True)
        if ret!=0:raise RuntimeError(str(data))
        used,remaining,details=data
        self.remaining=int(remaining);self.used={d["code"] for d in details or []}
        return dict(used=used,remaining=remaining)
    def can_history(self,code):return self.supports_code(code) and (code in self.used or self.remaining>BREADTH_FUTU_RESERVE)
    def history(self,code,complete_day):
        self.refresh_quota()
        if not self.can_history(code):raise RuntimeError("富途历史额度已达预留线")
        from futu import KLType,AuType
        self.throttle()
        ret,data,page=self.connect().request_history_kline(code,start=str((pd.Timestamp(complete_day)-pd.Timedelta(days=700)).date()),end=complete_day,ktype=KLType.K_DAY,autype=AuType.QFQ,max_count=1000)
        if ret!=0:raise RuntimeError(str(data))
        if page is not None:raise RuntimeError("历史窗口返回未完成分页")
        return clean_prices(data.rename(columns={"time_key":"date"})).tail(400),"futu_qfq"
    def quotes(self,codes):
        out={}
        supported=[c for c in dict.fromkeys(codes) if self.supports_code(c)]
        for batch in self.batches(supported):
            try:
                self.throttle();ret,data=self.connect().get_market_snapshot(batch)
                if ret!=0:raise RuntimeError(str(data))
                for row in data.to_dict("records"):
                    out[row["code"]]=dict(price=row.get("last_price"),prev_close=row.get("prev_close_price"),time=row.get("update_time"),source="futu_snapshot")
            except Exception as e:
                # 一个批次失败不能丢掉其他批次；诊断保留错误供下次增量重试。
                self.quote_errors.append(str(e)[:160])
        return out
