"""广度数据源适配。免费公开数据优先，富途仅补缺口。"""
from __future__ import annotations
import io
import csv
import html
import json
import os
import re
import socket
import threading
import time
import warnings
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlparse
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
        status=getattr(getattr(error,"response",None),"status_code",None)
        if isinstance(error,(requests.ConnectionError,requests.Timeout)) or status in {403,429,503}:
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
    if len(symbols)!=len(set(symbols)):
        raise ValueError(f"{key} 成分文件含重复证券代码")
    symbols=sorted(symbols)
    if len(symbols)!=spec["expected"]:raise ValueError(f"{key} 成分不完整：{len(symbols)}/{spec['expected']}")
    return symbols


def extract_effective_date(frame):
    """官方文件只有给出唯一、可解析的生效日期时才自动启用新名单。"""
    column=next((c for c in frame.columns if any(label in str(c).lower()
        for label in ("生效日期","实施日期","effective date"))),None)
    if column is None:return None
    dates=pd.to_datetime(frame[column].dropna(),errors="coerce")
    if dates.empty or dates.isna().any():return None
    unique=sorted(set(dates.dt.strftime("%Y-%m-%d")))
    return unique[0] if len(unique)==1 else None


def parse_nasdaq_directory(content):
    """将纳斯达克官方上市目录筛成 COMP 合资格证券的近似池。

    目录不直接给出 COMP 成分标记，故来源和结果必须明确标为估算口径。
    """
    lines=content.splitlines()
    if not lines or not lines[0].startswith("Symbol|Security Name|Market Category|"):
        raise ValueError("Nasdaq 上市目录表头异常")
    stamp=next((line for line in reversed(lines) if line.startswith("File Creation Time:")),None)
    if not stamp:raise ValueError("Nasdaq 上市目录缺少文件生成时间")
    match=re.search(r"File Creation Time:\s*(\d{2})(\d{2})(\d{4})",stamp)
    if not match:raise ValueError("Nasdaq 上市目录时间无法解析")
    month,day,year=match.groups()
    source_date=f"{year}-{month}-{day}"
    try:pd.Timestamp(source_date)
    except ValueError as error:raise ValueError("Nasdaq 上市目录日期无效") from error
    required={"Symbol","Security Name","Market Category","Test Issue","ETF","NextShares"}
    reader=csv.DictReader((line for line in lines if not line.startswith("File Creation Time:")),delimiter="|")
    if not required.issubset(reader.fieldnames or []):raise ValueError("Nasdaq 上市目录缺少关键字段")
    excluded={};symbols=[];seen=set()
    disallowed=re.compile(r"\b(warrants?|rights?|preferred|preference|depositary preferred|"
                          r"convertible debentures?|notes?|etns?|exchange.traded|closed.end|"
                          r"structured products?|units?)\b",re.I)
    eligible_beneficial=re.compile(r"units? of beneficial interest",re.I)
    for row in reader:
        symbol=(row.get("Symbol") or "").strip().upper()
        name=(row.get("Security Name") or "").strip()
        reason=None
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}",symbol):reason="bad_symbol"
        elif symbol in seen:reason="duplicate"
        elif row.get("Market Category") not in {"Q","G","S"}:reason="other_market"
        elif row.get("Test Issue")!="N":reason="test_issue"
        elif row.get("ETF")!="N" or row.get("NextShares")!="N":reason="fund_flag"
        elif disallowed.search(name) and not eligible_beneficial.search(name):reason="security_type"
        if reason:excluded[reason]=excluded.get(reason,0)+1
        else:symbols.append("US."+symbol);seen.add(symbol)
    if not symbols:raise ValueError("Nasdaq 上市目录过滤后为空")
    return dict(symbols=sorted(symbols),source_date=source_date,excluded=excluded,raw_count=sum(excluded.values())+len(symbols))


def parse_comp_component_count(content):
    """从 Nasdaq 官方 COMP 总览的公开统计中读取成分总数。"""
    plain=html.unescape(re.sub(r"<[^>]+>"," ",content))
    plain=re.sub(r"\s+"," ",plain)
    match=re.search(r"#\s*of\s*Components\s*:?(\s*[\d,]+)",plain,re.I)
    if not match:raise ValueError("Nasdaq COMP 官方总览未给出成分数量")
    count=int(match[1].replace(",",""))
    if not 1000<=count<=10000:raise ValueError("Nasdaq COMP 官方成分数量异常")
    return count


def parse_ndx_component_count(content):
    """只接受 Nasdaq 指数总览公布的证券数，不能把 100 家公司误当证券数。"""
    plain=html.unescape(re.sub(r"<[^>]+>"," ",content))
    match=re.search(r"#\s*of\s*Components\s*:?\s*([\d,]+)",re.sub(r"\s+"," ",plain),re.I)
    if not match:raise ValueError("NDX 官方总览未给出成分证券数")
    count=int(match[1].replace(",",""))
    if not 100<=count<=110:raise ValueError(f"NDX 官方成分证券数异常: {count}")
    return count


def parse_ndx_api_list(payload):
    """官方 JSON 必须一次返回全部证券；totalrecords 是完整性校验。"""
    data=payload.get("data") if isinstance(payload,dict) else None
    if not isinstance(data,dict):raise ValueError("NDX 官方 JSON 缺少 data")
    rows=data.get("data",{}).get("rows") if isinstance(data.get("data"),dict) else None
    count=data.get("totalrecords")
    if not isinstance(count,int) or not 100<=count<=110 or not isinstance(rows,list):
        raise ValueError("NDX 官方 JSON 成分数量或 rows 异常")
    if data.get("offset")!=0 or data.get("limit",0)<count or len(rows)!=count:
        raise ValueError(f"NDX 官方 JSON 名单截断: {len(rows) if rows is not None else 0}/{count}")
    codes=[]
    for row in rows:
        code=row.get("symbol") if isinstance(row,dict) else None
        if not isinstance(code,str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}",code):
            raise ValueError("NDX 官方 JSON 含无效证券代码")
        codes.append(code)
    if len(codes)!=len(set(codes)):raise ValueError("NDX 官方 JSON 含重复证券代码")
    try:source_date=pd.Timestamp(data["date"]).strftime("%Y-%m-%d")
    except (KeyError,ValueError,TypeError) as error:
        raise ValueError("NDX 官方 JSON 缺少有效名单日期") from error
    return {"symbols":sorted("US."+code for code in codes),"source_date":source_date,
            "official_count":count}


def parse_ndx_official_list(content):
    """解析 Nasdaq 自有网站公开的完整证券表；ETF 持仓不进入此入口。"""
    if "Nasdaq-100 Company Breakdown" not in content:
        raise ValueError("NDX 官方文章缺少完整成分表标题")
    stamp=re.search(r"<time\b[^>]*datetime=[\"'](\d{4}-\d{2}-\d{2})",content,re.I)
    if not stamp:raise ValueError("NDX 官方名单没有可核验发布日期")
    source_date=stamp[1]
    try:pd.Timestamp(source_date)
    except ValueError as error:raise ValueError("NDX 官方名单日期无效") from error
    try:tables=pd.read_html(io.StringIO(content),match="Security Symbol")
    except ValueError as error:raise ValueError("NDX 官方名单没有证券代码表") from error
    matches=[table for table in tables if {"Company Name","Security Symbol"}.issubset(map(str,table.columns))]
    if len(matches)!=1:raise ValueError("NDX 官方成分表不唯一或字段异常")
    raw=matches[0]["Security Symbol"].astype(str).str.strip().str.upper().tolist()
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}",code) for code in raw):
        raise ValueError("NDX 官方成分表含无效证券代码")
    if len(set(raw))!=len(raw):raise ValueError("NDX 官方成分表含重复证券代码")
    if not 100<=len(raw)<=110:raise ValueError(f"NDX 官方成分表不完整: {len(raw)}")
    return {"symbols":sorted("US."+code for code in raw),"source_date":source_date}


def parse_ndx_pdf_baseline(content):
    """从 Nasdaq 官方名单 PDF 的文本提取有日期的证券基线。"""
    if not re.search(r"Ticker\s*:\s*NDX\b",content):raise ValueError("不是 Nasdaq NDX 官方名单")
    stamp=re.search(r"Data\s+as\s+of\s*:\s*(\d{1,2}/\d{1,2}/\d{4})",content,re.I)
    if not stamp:raise ValueError("NDX PDF 缺少 as-of 日期")
    source_date=pd.Timestamp(stamp[1]).strftime("%Y-%m-%d")
    entries=[]
    for line in content.splitlines():
        match=re.match(r"^.+\s+([A-Z][A-Z0-9.\-]{0,11})\s+(\d{1,2}(?:\.\d{1,4})?)\s*$",line.strip())
        if match:entries.append((match[1],float(match[2])))
    codes=[code for code,_ in entries]
    if len(codes)!=len(set(codes)):raise ValueError("NDX PDF 成分代码重复")
    if not 100<=len(codes)<=110:raise ValueError(f"NDX PDF 名单不完整: {len(codes)}")
    weight=sum(value for _,value in entries)
    if not 95<=weight<=105:raise ValueError(f"NDX PDF 权重合计异常: {weight:.2f}")
    return {"symbols":sorted("US."+code for code in codes),"source_date":source_date,
            "weight_sum":round(weight,4)}


def parse_ndx_release(content,url):
    """官方公告只解析明确写出代码和开市前生效日的调样。"""
    plain=html.unescape(re.sub(r"<[^>]+>"," ",content))
    plain=re.sub(r"\s+"," ",plain)
    if not re.search(r"Nasdaq.100 Index",plain,re.I):raise ValueError("不是 NDX 调样公告")
    stamp=re.search(r"prior to market open on \w+,\s+([A-Z][a-z]+ \d{1,2}, \d{4})",plain,re.I)
    if not stamp:raise ValueError("NDX 公告没有明确生效日")
    effective=pd.Timestamp(stamp[1]).strftime("%Y-%m-%d")
    add_section=re.search(r"following\s+\w+\s+companies will be added to the Index:\s*(.*?)(?:The following|For additional|$)",plain,re.I)
    remove_section=re.search(r"following\s+\w+\s+companies will be removed from the Index:\s*(.*?)(?:For additional|$)",plain,re.I)
    ticker_pattern=r"\(Nasdaq:\s*([A-Z][A-Z0-9.\-]{0,11})\)"
    if add_section:
        added=re.findall(ticker_pattern,add_section[1],re.I)
        removed=re.findall(ticker_pattern,remove_section[1],re.I) if remove_section else []
    else:
        entry=re.search(r"\(Nasdaq:\s*([A-Z][A-Z0-9.\-]{0,11})\)\s+will become a component of the Nasdaq.100",plain,re.I)
        if not entry:raise ValueError("NDX 公告未列调入代码")
        added=[entry[1]]
        replacement=re.search(r"\breplacing\s+.{0,120}?\(Nasdaq:\s*([A-Z][A-Z0-9.\-]{0,11})\)",plain[entry.end():stamp.start()],re.I)
        removed=[replacement[1]] if replacement else []
    if not added or len(set(added))!=len(added) or len(set(removed))!=len(removed):
        raise ValueError("NDX 公告代码缺失或重复")
    return {"added":sorted("US."+code.upper() for code in added),
            "removed":sorted("US."+code.upper() for code in removed),
            "effective_date":effective,"url":url}


def apply_ndx_notices(baseline,notices,day):
    """只把已生效且逐项可核对的官方公告叠加到历史基线。"""
    symbols=set(baseline)
    for notice in sorted(notices,key=lambda row:(row["effective_date"],row["url"])):
        if notice["effective_date"]>day:continue
        added=set(notice["added"]);removed=set(notice["removed"])
        if not removed.issubset(symbols) or added.intersection(symbols-removed):
            raise ValueError(f"NDX 公告增删与基线不一致: {notice['url']}")
        symbols=(symbols-removed)|added
        if not 100<=len(symbols)<=110:raise ValueError("NDX 公告更新后成分数量异常")
    return sorted(symbols)


def parse_ndx_archive_page(content):
    """IR 归档每页必须给出日期；扫描到基线日以前才算公告覆盖完整。"""
    dates=[];urls=[];release_dates={}
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>",content,re.I|re.S):
        plain=html.unescape(re.sub(r"<[^>]+>"," ",row))
        stamp=re.search(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})\b",plain)
        if not stamp:continue
        published=pd.Timestamp(stamp[1]).strftime("%Y-%m-%d")
        dates.append(published)
        for href,title in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",row,re.I|re.S):
            label=html.unescape(re.sub(r"<[^>]+>"," ",title))
            if not re.search(r"Nasdaq.100",label,re.I) or not re.search(r"join|changes|remove|add|component|replac",label,re.I):
                continue
            url=urljoin("https://ir.nasdaq.com",href)
            host=(urlparse(url).hostname or "").lower()
            if host=="ir.nasdaq.com":
                urls.append(url);release_dates[url]=published
    if not dates:raise ValueError("Nasdaq IR 公告归档缺少可解析日期")
    return {"oldest_date":min(dates),"release_urls":list(dict.fromkeys(urls)),
            "release_dates":release_dates}


def fetch_ndx_notice_archive(source_date,max_pages=30):
    """遍历 Nasdaq IR 官方归档直到基线日前；任何缺页都拒绝声称覆盖完整。"""
    urls=[];oldest=None
    for page in range(max_pages):
        archive=f"https://ir.nasdaq.com/news-and-events/press-releases?NumberPerPage=100&mobile=1&page={page}"
        parsed=parse_ndx_archive_page(get(archive).text)
        if oldest and parsed["oldest_date"]>=oldest:
            raise ValueError("Nasdaq IR 公告归档分页未推进")
        oldest=parsed["oldest_date"]
        urls.extend(url for url in parsed["release_urls"]
                    if parsed["release_dates"][url]>=source_date)
        if oldest<=source_date:break
    else:raise ValueError("Nasdaq IR 公告归档未覆盖 PDF 基线日期")
    notices=[]
    for url in dict.fromkeys(urls):
        notices.append(parse_ndx_release(get(url).text,url))
    return {"notices":notices,"archive_oldest_date":oldest,"archive_pages":page+1}


def extract_ndx_pdf_text(payload):
    try:from pypdf import PdfReader
    except ImportError as error:raise RuntimeError("NDX 官方 PDF 基线解析需要 pypdf 依赖") from error
    reader=PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_spglobal_dow_list(content):
    """只认 S&P DJI 页面完整的30只正式成分；前十展示或 DIA 持仓均不够。"""
    if "Dow Jones Industrial Average" not in content:
        raise ValueError("道指官方页面标题异常")
    try:tables=pd.read_html(io.StringIO(content),match="Symbol")
    except ValueError as error:raise ValueError("道指官方页面未公开完整成分表") from error
    matches=[table for table in tables if {"Constituent","Symbol"}.issubset(map(str,table.columns))]
    if len(matches)!=1:raise ValueError("道指官方成分表不唯一或缺失")
    raw=matches[0]["Symbol"].astype(str).str.strip().str.upper().tolist()
    if len(raw)!=30:raise ValueError(f"道指官方成分不完整: {len(raw)}/30")
    if len(set(raw))!=30 or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}",code) for code in raw):
        raise ValueError("道指官方成分代码重复或无效")
    return sorted("US."+code for code in raw)


def parse_szse_shenzhen_notice(content,url):
    """深交所公告须同时列出逐只调入调出和明确实施日。"""
    plain=html.unescape(re.sub(r"<[^>]+>"," ",content))
    match=re.search(r"决定于\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",plain)
    if not match:raise ValueError("深证成指公告未给出明确实施日")
    effective=f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
    pd.Timestamp(effective)
    heading=re.search(r"深证成份?指数样本股调整名单",content)
    if not heading:raise ValueError("公告没有深证成指专属调整表")
    table=re.search(r"<table\b[^>]*>.*?</table>",content[heading.end():],re.I|re.S)
    if not table:raise ValueError("深证成指公告缺少调整表")
    added=[];removed=[]
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>",table[0],re.I|re.S):
        cells=[html.unescape(re.sub(r"<[^>]+>","",cell)).strip() for cell in
               re.findall(r"<t[dh]\b[^>]*>.*?</t[dh]>",row,re.I|re.S)]
        if len(cells)<3:continue
        if re.fullmatch(r"\d{6}",cells[0]) and re.fullmatch(r"\d{6}",cells[2]):
            added.append("SZ."+cells[0]);removed.append("SZ."+cells[2])
    if not added or len(added)!=len(removed) or len(added)!=len(set(added)) or len(removed)!=len(set(removed)):
        raise ValueError("深证成指公告调样代码不完整或重复")
    return {"added":sorted(added),"removed":sorted(removed),"effective_date":effective,"url":url}


def fetch_shenzhen_notices():
    """从深交所公开指数动态中寻找可逐代码核对的成分调整公告。"""
    index="https://www.szse.cn/marketServices/message/index/dynamic/"
    page=get(index).text
    candidates=[]
    for href,label in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",page,re.I|re.S):
        title=html.unescape(re.sub(r"<[^>]+>","",label))
        url=urljoin(index,href)
        host=(urlparse(url).hostname or "").lower()
        if "调整深证成指" in title and (host=="szse.cn" or host.endswith(".szse.cn")) and url.endswith(".html"):
            candidates.append(url)
    notices=[]
    for url in list(dict.fromkeys(candidates))[:3]:
        try:notices.append(parse_szse_shenzhen_notice(get(url).text,url))
        except (ValueError,requests.RequestException):continue
    return notices


def fetch_members(key,futu=None):
    if key=="nasdaq100":
        # 先试本机富途；官方接口不可达时，经数量校验的富途名单仍可独立使用。
        futu_symbols=None;futu_error=None
        if futu is not None:
            try:futu_symbols=futu.index_members("nasdaq100")
            except Exception as error:futu_error=str(error)[:160]
        url="https://api.nasdaq.com/api/quote/list-type/nasdaq100"
        official=None;official_error=None
        try:official=parse_ndx_api_list(get(url).json())
        except Exception as error:official_error=str(error)[:160]
        overview_url="https://indexes.nasdaq.com/Index/Overview/NDX"
        count_error=None
        try:overview_count=parse_ndx_component_count(get(overview_url).text)
        except Exception as error:overview_count=None;count_error=str(error)[:160]
        if official is not None and overview_count is not None and len(official["symbols"])!=overview_count:
            # 两个 Nasdaq 官方入口互相矛盾时，不能将其中一方冒充已核实名单。
            official_error=f"NDX 官方 JSON 名单 {len(official['symbols'])} 与总览 {overview_count} 不符"
            official=None
        today=str(pd.Timestamp.now(tz="America/New_York").date())
        if futu_symbols is not None:
            difference=len(set(futu_symbols)^set(official["symbols"])) if official else None
            reference=overview_count or (official or {}).get("official_count") or 101
            if (len(futu_symbols)!=len(set(futu_symbols)) or
                    any(not re.fullmatch(r"US\.[A-Z][A-Z0-9.\-]{0,11}",code) for code in futu_symbols) or
                    abs(len(futu_symbols)-reference)>5 or not 95<=len(futu_symbols)<=110):
                futu_error="富途 NDX 名单数量或代码异常";futu_symbols=None
            elif difference is not None and difference>5:
                futu_error=f"富途与 Nasdaq 官方名单差异 {difference} 只，超过 5 只";futu_symbols=None
        def attach_ndx_notices(symbols,meta):
            # 公告归档只负责可逐代码验证的生效日；不可达时不撤销已核实的现行名单。
            lookback=str((pd.Timestamp(today)-pd.Timedelta(days=45)).date())
            try:
                archive=fetch_ndx_notice_archive(lookback)
                meta["official_notices"]=archive["notices"]
                meta["archive_oldest_date"]=archive["archive_oldest_date"]
                future=[];current=symbols
                for notice in sorted((item for item in archive["notices"]
                                      if item["effective_date"]>today),
                                     key=lambda item:item["effective_date"]):
                    current=apply_ndx_notices(current,[notice],notice["effective_date"])
                    future.append(dict(notice,symbols=current))
                meta["future_memberships"]=future
            except Exception as error:meta["notice_error"]=str(error)[:160]
            return meta
        if futu_symbols is not None:
            meta={"source_date":today,"official_count":overview_count or (official or {}).get("official_count") or 101,
                  "futu_verified":True,"futu_official_difference":difference,
                  "overview_url":overview_url,"official_api_url":url,
                  "official_error":official_error,"overview_error":count_error}
            return sorted(futu_symbols),"futu_opend:US.NDX",attach_ndx_notices(sorted(futu_symbols),meta)
        if official is None:
            raise ValueError(f"NDX 成分来源不可用：富途={futu_error}; Nasdaq={official_error}")
        age=(pd.Timestamp(today)-pd.Timestamp(official["source_date"])).days
        official["official_current"]=0<=age<=7
        official.update(overview_url=overview_url,official_api_url=url,effective_evidence_url=url,
                        futu_error=futu_error,overview_error=count_error)
        return official["symbols"],url,attach_ndx_notices(official["symbols"],official)
    futu_candidate=None
    if key in {"dow","dividend","csi2000","shenzhen"} and futu is not None:
        # 先读富途，再与同轮官方文件核对；官网不可达时保留完整富途名单。
        try:
            symbols=futu.index_members(key)
            expected=BREADTH_MARKETS[key]["expected"]
            if len(symbols)!=expected or len(symbols)!=len(set(symbols)):
                raise ValueError(f"富途 {key} 成分不完整: {len(symbols)}/{expected}")
            futu_candidate=sorted(symbols)
        except Exception:
            pass
    def futu_result(extra=None):
        code=FutuBreadth.INDEX_CODES[key]
        meta={"source_date":str(pd.Timestamp.now().date()),"futu_verified":True,
              "effective_date":None}
        meta.update(extra or {})
        return futu_candidate,f"futu_opend:{code}",meta
    if key=="nasdaq":
        url="https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        parsed=parse_nasdaq_directory(get(url).text)
        try:
            overview=get("https://indexes.nasdaq.com/Index/Overview/COMP")
            parsed["official_comp_count"]=parse_comp_component_count(overview.text)
        except Exception as error:
            parsed["official_count_error"]=str(error)[:160]
        return parsed["symbols"],url,parsed
    if key=="dow":
        url="https://www.spglobal.com/spdji/en/indices/equity/dow-jones-industrial-average/"
        candidate_url="https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-dia.xlsx"
        symbols=None;candidate_error=None
        try:
            raw=pd.read_excel(io.BytesIO(get(candidate_url).content),header=None)
            headers=raw.index[raw.iloc[:,0].eq("Name")]
            if len(headers)!=1:raise ValueError("DIA持仓文件格式变化")
            index=int(headers[0]);frame=raw.iloc[index+1:].copy();frame.columns=raw.iloc[index]
            symbols=parse_members("dow",frame)
        except Exception as error:candidate_error=str(error)[:160]
        meta={"effective_date":None,"official_url":url,"candidate_url":candidate_url,
              "source_date":str(pd.Timestamp.now(tz="America/New_York").date()),
              "candidate_error":candidate_error}
        try:
            official=parse_spglobal_dow_list(get(url).text)
            meta["official_match"]=symbols==official if symbols else None
            if symbols!=official:symbols=official;candidate_url=url
        except Exception as error:meta["official_check_error"]=str(error)[:160]
        if futu_candidate is not None:
            if symbols is None:return futu_result(meta)
            meta["futu_difference"]=len(set(futu_candidate)^set(symbols))
            if futu_candidate==symbols:return futu_result(meta)
        if symbols is None:raise ValueError(f"道指成分来源不可用: DIA={candidate_error}; S&P={meta.get('official_check_error')}")
        return symbols,candidate_url,meta
    elif key=="shenzhen":
        url="https://www.cnindex.com.cn/sample-detail/download?indexcode=399001"
        # 国证官网在本机直连稳定，代理链路偶发超时；失败后才使用环境代理。
        try:
            try:
                with requests.Session() as session:
                    session.trust_env=False
                    response=session.get(url,timeout=(5,12));response.raise_for_status()
                    content=response.content
            except requests.RequestException:content=get(url).content
            frame=pd.read_excel(io.BytesIO(content),dtype=str)
        except Exception:
            if futu_candidate is not None:return futu_result()
            raise
    else:
        index=BREADTH_MARKETS[key]["index"]
        url=f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{index}cons.xls"
        try:frame=pd.read_excel(io.BytesIO(get(url).content),dtype=str)
        except Exception:
            if futu_candidate is not None:return futu_result()
            raise
    meta={"effective_date":extract_effective_date(frame)}
    if meta["effective_date"]:meta["effective_evidence_url"]=url
    if key=="shenzhen" and not meta["effective_date"]:
        try:meta["official_notices"]=fetch_shenzhen_notices()
        except Exception as error:meta["notice_error"]=str(error)[:160]
    official_symbols=parse_members(key,frame)
    if futu_candidate is not None:
        meta["futu_difference"]=len(set(futu_candidate)^set(official_symbols))
        if futu_candidate==official_symbols:return futu_result(meta)
    return official_symbols,url,meta


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


def fetch_eastmoney_us_prices(code,complete_day,start=None):
    """东方财富美股前复权日线；直连避免继承本机失效的境外代理。"""
    ticker=code.split(".",1)[1]
    url="https://63.push2his.eastmoney.com/api/qt/stock/kline/get"
    base={"fields1":"f1,f2,f3,f4,f5,f6","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
          "klt":"101","fqt":"1","end":"20500000","lmt":"500"}
    with requests.Session() as session:
        session.trust_env=False
        for market in ("105","106","107"):
            response=session.get(url,params=dict(base,secid=f"{market}.{ticker}"),timeout=(5,10))
            response.raise_for_status()
            payload=response.json().get("data") or {}
            raw=payload.get("klines") or []
            if not raw:continue
            rows=[]
            for item in raw:
                fields=item.split(",")
                if len(fields)>=3:rows.append({"date":fields[0],"close":fields[2]})
            frame=clean_prices(pd.DataFrame(rows,columns=["date","close"]))
            frame=frame.loc[frame.date<=complete_day]
            if start:frame=frame.loc[frame.date>=start]
            if not frame.empty:return frame.tail(400),"eastmoney_us_qfq"
    raise ValueError(f"东方财富美股日线无有效数据: {code}")


def fetch_sina_us_prices(code,complete_day,start=None):
    """新浪美股前复权日线作为国内可访问回退；每只证券只保留计算窗口。"""
    import akshare as ak
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore",category=FutureWarning,module=r"akshare\.stock\.stock_us_sina")
        frame=ak.stock_us_daily(symbol=code.split(".",1)[1],adjust="qfq")
    if frame.empty or not {"date","close"}.issubset(frame.columns):
        raise ValueError(f"新浪美股日线为空或缺少字段: {code}")
    frame=clean_prices(frame)
    frame=frame.loc[frame.date<=complete_day]
    if start:frame=frame.loc[frame.date>=start]
    if frame.empty:raise ValueError(f"新浪美股日线没有目标日期: {code}")
    return frame.tail(400),"sina_us_qfq"


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
    us_sources=["yahoo_adjclose","sina_us_qfq","eastmoney_us_qfq"] if code.startswith("US.") else ["yahoo_adjclose"]
    if preferred in us_sources:us_sources.remove(preferred);us_sources.insert(0,preferred)
    for source in us_sources:
        if not health.available(source):continue
        try:
            begin=start if preferred==source else None
            return (fetch_yahoo_prices(code,complete_day,begin) if source=="yahoo_adjclose"
                    else fetch_sina_us_prices(code,complete_day,begin) if source=="sina_us_qfq"
                    else fetch_eastmoney_us_prices(code,complete_day,begin))
        except Exception as e:
            health.failed(source,e);errors.append(source+": "+str(e)[:120])
    raise RuntimeError("; ".join(errors) or "本轮可用日线源已熔断")


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
    INDEX_CODES={"nasdaq100":"US.NDX","dow":"US.DJI","dividend":"SH.H30269",
                 "csi2000":"SH.932000","shenzhen":"SZ.399001"}
    def __init__(self):
        self.ctx=None;self.remaining=0;self.used=set();self.last_call=None;self.quote_errors=[];self.quota_checked_at=None
    @staticmethod
    def supports_code(code):return code.startswith(("US.","HK.","SH.","SZ."))
    def connect(self):
        if self.ctx is None:
            host=os.environ.get("AHNS_BREADTH_FUTU_HOST","127.0.0.1");port=int(os.environ.get("AHNS_BREADTH_FUTU_PORT","11111"))
            with socket.create_connection((host,port),timeout=1):pass
            from futu import OpenQuoteContext
            self.ctx=OpenQuoteContext(host=host,port=port)
        return self.ctx
    def index_members(self,key):
        """读取富途指数板块，校验行数后才作为成分来源。"""
        code=self.INDEX_CODES[key]
        ctx=self.connect()
        self.throttle();ret,data=ctx.get_plate_stock(code)
        if ret==0 and isinstance(data,pd.DataFrame):
            if "code" not in data.columns:raise ValueError("富途成分缺少 code 字段")
            symbols=[str(value).strip().upper() for value in data["code"]]
        elif hasattr(ctx,"get_valuation_plate_stock_list"):
            # 新版 OpenAPI 可按指数代码分页获取成分；旧版 SDK 没有该方法。
            symbols=[];next_key=None;seen_keys=set();total=None
            for _ in range(100):
                if next_key in seen_keys:raise ValueError("富途指数成分分页未推进")
                seen_keys.add(next_key)
                self.throttle();ret,page=ctx.get_valuation_plate_stock_list(code,next_key=next_key,num=50)
                if ret!=0 or not isinstance(page,dict):
                    raise RuntimeError(f"富途指数成分 {code} 分页不可用: {str(page)[:100]}")
                if total is None:total=page.get("count")
                elif total!=page.get("count"):raise ValueError("富途指数成分分页总数不一致")
                symbols.extend(str(row.get("symbol","")).strip().upper()
                               for row in page.get("stock_list",[]))
                next_key=page.get("next_key")
                if next_key=="-1":break
                if not next_key:raise ValueError("富途指数成分分页截断")
            else:raise ValueError("富途指数成分分页超过限制")
            if not isinstance(total,int) or len(symbols)!=total:
                raise ValueError("富途指数成分分页总数与证券列表不符")
        else:raise RuntimeError(f"富途指数成分 {code} 不可用: {str(data)[:100]}")
        prefix="US." if key in {"nasdaq100","dow"} else ("SZ.","SH.","BJ.")
        if any(not value.startswith(prefix) for value in symbols):
            raise ValueError("富途指数成分市场代码异常")
        if not symbols or len(symbols)!=len(set(symbols)):
            raise ValueError("富途指数成分为空或重复")
        return sorted(symbols)
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
        self.quota_checked_at=datetime.now(timezone.utc).isoformat()
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

    def crosscheck_nasdaq(self,symbols):
        """富途静态证券表只作交易所交叉核验，不替代官方目录。"""
        from futu import Market,SecurityType
        self.throttle()
        ret,data=self.connect().get_stock_basicinfo(Market.US,SecurityType.STOCK)
        if ret!=0:raise RuntimeError(str(data))
        if not {"code","exchange_type"}.issubset(data.columns):raise ValueError("富途静态数据缺少交易所字段")
        nasdaq={str(r["code"]) for r in data.to_dict("records")
                if r["exchange_type"]==5 or "NASDAQ" in str(r["exchange_type"]).upper()}
        proposed=set(symbols)
        return dict(futu_nasdaq_count=len(nasdaq),nasdaq_overlap=len(proposed&nasdaq),
                    non_nasdaq=sorted(proposed-nasdaq))
