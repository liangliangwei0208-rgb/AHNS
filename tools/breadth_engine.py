"""建库/增量协调。由外层子进程预算隔离慢接口，不阻塞日常出图。"""
from __future__ import annotations
import concurrent.futures
import functools
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd
import pandas_market_calendars as mcal
from tools.market_breadth import BreadthStore, calculate_history, calculate_segmented_history, calculate_intraday, eligible_quote, utc_now
from tools.breadth_sources import fetch_members, fetch_prices, fetch_quotes, FutuBreadth, SourceHealth, fetch_stockcharts
from tools.configs.market_breadth_configs import *

@functools.lru_cache(maxsize=16)
def _schedule(market,day):
    cal=mcal.get_calendar("NYSE" if market=="US" else "SSE")
    return cal.schedule(start_date=str((pd.Timestamp(day)-pd.Timedelta(days=900)).date()),end_date=day)


def market_clock(market,now=None):
    now=pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    if now.tzinfo is None:raise ValueError("时间必须含时区")
    zone="America/New_York" if market=="US" else "Asia/Shanghai"
    local=now.tz_convert(zone);day=str(local.date());schedule=_schedule(market,day)
    completed=schedule.loc[schedule.market_close<=now-pd.Timedelta(minutes=15)]
    if completed.empty:raise ValueError("没有已完成交易日")
    regular=False
    if pd.Timestamp(day) in schedule.index:
        row=schedule.loc[day]
        regular=row.market_open<=now<row.market_close
        if market=="CN" and 690<=local.hour*60+local.minute<780:regular=False
    return dict(day=day,complete_day=str(completed.index[-1].date()),regular=bool(regular),
                sessions=[str(x.date()) for x in completed.index],now=local)


def prepare_quotes(store,members,quotes,now,market,previous_day):
    out={}
    for code in members:
        q=quotes.get(code,{})
        if not eligible_quote(q.get("time"),now,market,BREADTH_MAX_QUOTE_AGE_MINUTES):continue
        history=store.prices(code)
        if history.empty or history.iloc[-1].date!=previous_day:continue
        try:
            price=float(q["price"]);previous=float(q["prev_close"])
            if price>0 and previous>0 and math.isfinite(price) and math.isfinite(previous):
                out[code]=price/previous*float(history.iloc[-1].close)
        except (KeyError,ValueError,TypeError):continue
    return out


def prepare_close_quotes(store,members,quotes,now,day,previous_day):
    """只接受沪深交易日15:00的最终快照，并接续已有前复权价格基准。"""
    local=pd.Timestamp(now)
    local=local.tz_localize("Asia/Shanghai") if local.tzinfo is None else local.tz_convert("Asia/Shanghai")
    completed_at=pd.Timestamp(day,tz="Asia/Shanghai")+pd.Timedelta(hours=15,minutes=15)
    if local<completed_at:return {}
    out={}
    for code in members:
        if not code.startswith(("SH.","SZ.")):continue
        quote=quotes.get(code,{})
        try:
            stamp=pd.Timestamp(quote["time"])
            stamp=stamp.tz_localize("Asia/Shanghai") if stamp.tzinfo is None else stamp.tz_convert("Asia/Shanghai")
            if str(stamp.date())!=day or (stamp.hour,stamp.minute)!=(15,0):continue
            price=float(quote["price"]);previous=float(quote["prev_close"])
            if not all(math.isfinite(x) and x>0 for x in (price,previous)):continue
            history=store.prices(code)
            if history.empty or history.iloc[-1].date!=previous_day:continue
            out[code]=float(history.iloc[-1].close)*price/previous
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return out


def prepare_market_close_quotes(store,members,quotes,now,day,previous_day,market):
    """按实际交易日历验证收盘快照，再续接证券原有复权价格。"""
    if market=="CN":return prepare_close_quotes(store,members,quotes,now,day,previous_day)
    schedule=_schedule(market,day)
    if pd.Timestamp(day) not in schedule.index:return {}
    session=schedule.loc[day]
    opened=pd.Timestamp(session.market_open).tz_convert("America/New_York")
    closed=pd.Timestamp(session.market_close).tz_convert("America/New_York")
    now=pd.Timestamp(now)
    now=now.tz_localize("America/New_York") if now.tzinfo is None else now.tz_convert("America/New_York")
    if now<closed+pd.Timedelta(minutes=15):return {}
    out={}
    for code in members:
        quote=quotes.get(code,{})
        try:
            stamp=pd.Timestamp(quote["time"])
            stamp=stamp.tz_localize("America/New_York") if stamp.tzinfo is None else stamp.tz_convert("America/New_York")
            # 只认收市前最后几分钟的常规时段报价；午间旧价不能冒充收盘。
            if not max(opened,closed-pd.Timedelta(minutes=5))<=stamp<=closed or str(stamp.date())!=day:continue
            price=float(quote["price"]);previous=float(quote["prev_close"])
            if not all(math.isfinite(x) and x>0 for x in (price,previous)):continue
            history=store.prices(code)
            if history.empty or history.iloc[-1].date!=previous_day:continue
            out[code]=float(history.iloc[-1].close)*price/previous
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return out


def missing_price_sessions(store,code,sessions):
    """只检查真实交易日，上市前不补造价格；50日窗口内临时值也须正式核验。"""
    entry=store.price_state(code)
    frame=entry['frame']
    if frame.empty:return list(sessions[-50:])
    window=tuple(sessions[-50:])
    if window not in entry['gaps']:
        present=set(frame.date)-set(entry['metadata'].get('provisional_dates',[]))
        entry['gaps'][window]=tuple(day for day in window if day>=frame.iloc[0].date and day not in present)
    return list(entry['gaps'][window])


def save_downloaded_prices(store,code,frame,basis):
    """换源不拼接复权价格，也不能用更旧的完整窗口丢掉已补齐的真实日期。"""
    old=store.prices(code)
    if not frame.empty and not old.empty and store.price_metadata(code).get('basis')!=basis \
            and frame.iloc[-1].date<old.iloc[-1].date:
        raise RuntimeError(f'stale different-source window: {basis} ends {frame.iloc[-1].date}; keep {old.iloc[-1].date}')
    store.save_prices(code,frame,basis)


def _price_scope(function):
    @functools.wraps(function)
    def scoped(store,*args,**kwargs):
        with store.read_scope():return function(store,*args,**kwargs)
    return scoped


@_price_scope
def close_completion(store,key,clock,*,calculation=None):
    """正式收盘状态与目标日覆盖率分开检查，不拿旧100%覆盖率掩盖缺口。"""
    rows=store.read('results',key).get('rows',[])
    official=[r for r in rows if r.get('kind')=='close' and r.get('finality') not in
              {'snapshot_provisional','chart_only'} and r.get('date','')<=clock['complete_day']
              and r.get('percent') is not None and math.isfinite(float(r['percent']))
              and float(r.get('coverage') or 0)>=BREADTH_MIN_COVERAGE]
    latest_record=max(official,key=lambda r:r['date'],default={})
    latest=latest_record.get('date')
    member=store.members(key,clock['complete_day']);symbols=member.get('symbols',[])
    if (calculation and calculation.get('day')==clock['complete_day']
            and calculation.get('version')==member.get('version')
            and calculation.get('prices_token')==store.price_token(symbols)):
        row=calculation['row']
    else:
        calculated=calculate_history({c:store.prices(c) for c in symbols},symbols,BREADTH_MIN_COVERAGE,clock['sessions'][-50:])
        row=calculated.iloc[-1] if not calculated.empty else {}
    missing=[c for c in symbols if missing_price_sessions(store,c,clock['sessions'])]
    return dict(expected_complete_day=clock['complete_day'],latest_valid_close_date=latest,
                member_count=len(symbols),valid_count=int(row.get('valid',0)),coverage=float(row.get('coverage',0)),
                missing_count=len(missing),remaining_missing_symbols=missing,
                missing_sessions=sum((latest or '')<d<=clock['complete_day'] for d in clock['sessions']),
                status='complete' if latest==clock['complete_day'] and float(row.get('coverage',0))>=BREADTH_MIN_COVERAGE
                       and latest_record.get('membership_version')==member.get('version')
                       and bool(symbols) else 'stale')


@_price_scope
def price_download_plan(store,key,member,day,complete_day,bootstrap,repair,initialized):
    """调入证券先缓存真实日线；仅当日有效名单进入广度分母。"""
    symbols=member["symbols"]
    doc=store.read("members",key)
    future=set()
    for row in doc.get("rows",[]):
        if (row.get("effective_date") or row["date"])>day:
            future.update(row.get("symbols",[]))
    future.update(doc.get("pending_membership",{}).get("symbols",[]))
    prewarm=sorted(future-set(symbols))
    sessions=[str(d.date()) for d in _schedule(BREADTH_MARKETS[key]['market'],complete_day).index]
    pending=[];needs_bootstrap=0;missing_added=0
    for code in symbols:
        frame=store.prices(code)
        if frame.empty:
            needs_bootstrap+=1
            if bootstrap or repair or (initialized and (BREADTH_MARKETS[key]['market']=='US' or missing_added<10)):
                pending.append(code);missing_added+=1
        elif (missing_price_sessions(store,code,sessions) or frame.iloc[-1].date<complete_day or
              (bootstrap and len(frame)<400 and len(frame)>=50) or
              (repair and (len(frame)<50 or bool(store.price_metadata(code).get("provisional_dates"))))):
            pending.append(code)
    future_added=0
    for code in prewarm:
        frame=store.prices(code)
        if frame.empty:
            if bootstrap or repair or future_added<10:
                pending.append(code);future_added+=1
        elif frame.iloc[-1].date<complete_day or (bootstrap and 50<=len(frame)<400):
            pending.append(code)
    if bootstrap:
        pending=[code for code in pending if not (store.price_metadata(code).get("bootstrap_complete")
                 and not store.prices(code).empty and store.prices(code).iloc[-1].date>=complete_day)]
        pending.sort(reverse=True)
    return dict(pending=pending,needs_bootstrap=needs_bootstrap,prewarm_symbols=prewarm,
                prewarm_cached=sum(not store.prices(code).empty for code in prewarm))


def match_official_notice(key,before,after,notices):
    """公告必须同时精确解释调入、调出及生效日，不能仅凭标题或 ETF 差异猜测。"""
    hosts={"nasdaq100":("nasdaq.com",),"dow":("spglobal.com",),
           "dividend":("csindex.com.cn",),"csi2000":("csindex.com.cn",),
           "shenzhen":("cnindex.com.cn","szse.cn")}
    added=set(after)-set(before);removed=set(before)-set(after)
    matches=[]
    for notice in notices:
        url=str(notice.get("url") or "")
        parsed=urlparse(url);host=(parsed.hostname or "").lower()
        if parsed.scheme!="https" or not any(host==suffix or host.endswith("."+suffix)
                                                  for suffix in hosts.get(key,())):continue
        if set(notice.get("added",[]))!=added or set(notice.get("removed",[]))!=removed:continue
        if len(notice.get("added",[]))!=len(added) or len(notice.get("removed",[]))!=len(removed):continue
        date=str(notice.get("effective_date") or "")
        try:
            if pd.Timestamp(date).strftime("%Y-%m-%d")!=date:continue
        except (ValueError,TypeError):continue
        matches.append((date,url))
    if len({date for date,_ in matches})>1:raise ValueError("官方调样公告生效日相互矛盾")
    return matches[0] if matches else None


def stage_future_memberships(store,key,current,day,versions,source):
    """公告已核实的未来版本提前入库；价格预热由统一下载计划处理。"""
    previous=list(current);staged=[]
    for item in sorted(versions,key=lambda row:row["effective_date"]):
        symbols=item["symbols"];date=item["effective_date"]
        if date<=day or (staged and date<=staged[-1]["effective_date"]):
            raise ValueError("未来调样生效日重复或不晚于发现日")
        expected=BREADTH_MARKETS[key].get("expected")
        if not symbols or len(symbols)!=len(set(symbols)) or (expected and len(symbols)!=expected):
            raise ValueError("未来调样名单为空、重复或数量不完整")
        match=match_official_notice(key,previous,symbols,[item])
        if match!=(date,item["url"]):raise ValueError("未来调样公告未逐代码核对")
        ratio=len(set(symbols)^set(previous))/len(previous)
        if ratio>BREADTH_MAX_MEMBER_CHANGE_RATIO.get(key,1):raise ValueError("未来调样异常大幅")
        row=store.save_members(key,symbols,source,day,BREADTH_MARKETS[key]["universe"],
                               effective_date=date,evidence_url=item["url"])
        staged.append(row);previous=symbols
    return staged


def validate_membership_candidate(key,symbols,member,meta,day):
    """严格拦截残缺或异常调样；没有官方日期证据的新版本只进 pending。"""
    if not symbols or len(symbols)!=len(set(symbols)):
        raise ValueError(f"{key} 成分名单为空或重复")
    expected=BREADTH_MARKETS[key].get("expected")
    if expected and len(symbols)!=expected:
        raise ValueError(f"{key} 成分不完整: {len(symbols)}/{expected}")
    if key=="nasdaq100":
        count=meta.get("official_count")
        tolerance=5 if meta.get("futu_verified") else 0
        if not isinstance(count,int) or abs(count-len(symbols))>tolerance:
            raise ValueError(f"NDX 官方总览数量与名单不符: {len(symbols)}/{count}")
        if not (meta.get("official_current") or meta.get("futu_verified") or
                meta.get("announcement_coverage_complete")):
            raise ValueError("NDX 官方完整名单已过期，缺少基线至今全部调样公告核验")
    if member and set(symbols)!=set(member["symbols"]):
        ratio=len(set(symbols)^set(member["symbols"]))/len(member["symbols"])
        limit=BREADTH_MAX_MEMBER_CHANGE_RATIO.get(key)
        if limit is not None and ratio>limit:
            raise ValueError(f"{key} 异常大幅调样: {ratio:.1%} > {limit:.0%}")
        if not meta.get("effective_date"):
            match=match_official_notice(key,member["symbols"],symbols,meta.get("official_notices",[]))
            if match:meta["effective_date"],meta["effective_evidence_url"]=match
        return meta.get("effective_date")
    if member:return member.get("effective_date")
    # 首次建库可使用官方名单自身的 as-of 日，避免将已核实的最近收盘日错标为发现日。
    return meta.get("source_date") if meta.get("official_current") else day


def _reusable_close(store,key,clock,member,plan):
    """正式结果与维护分别验收；无法证明行情未变化时仍走正常计算。"""
    if not member or plan['pending'] or plan['needs_bootstrap'] or plan['prewarm_cached']<len(plan['prewarm_symbols']):return None
    doc=store.read('members',key)
    completed={r['date']:r for r in store.read('results',key).get('rows',[])
               if r.get('kind')=='close' and r.get('finality') not in {'snapshot_provisional','chart_only'}
               and r.get('percent') is not None and math.isfinite(float(r['percent']))
               and float(r.get('coverage') or 0)>=BREADTH_MIN_COVERAGE}
    required=[day for day in clock['sessions'][-400:] if day>=(doc.get('pit_start') or '')]
    if any(day not in completed for day in required):return None
    row=completed.get(clock['complete_day'],{})
    if row.get('membership_version')!=member.get('version') or row.get('total')!=len(member['symbols']):return None
    published=pd.to_datetime(row.get('updated_at'),utc=True,errors='coerce')
    if pd.isna(published):return None
    valid=0;tokens=[]
    for code in member['symbols']:
        state=store.price_state(code)
        tokens.append((code,state['signature']))
        stamp=pd.to_datetime(state['metadata'].get('updated_at'),utc=True,errors='coerce')
        if pd.isna(stamp) or stamp>published or not state['metadata'].get('basis'):return None
        if state['metadata'].get('provisional_dates'):return None
        window=state['frame'].loc[state['frame'].date.isin(clock['sessions'][-50:])]
        values=pd.to_numeric(window.close,errors='coerce')
        # 与正常矩阵一致：重复/坏价不能借已有结果绕过完整性检查。
        if window.date.duplicated().any():return None
        good=values.notna() & values.gt(0) & values.abs().lt(float('inf'))
        valid+=int(len(window)==50 and good.all())
    if valid!=row.get('valid') or valid/max(1,len(member['symbols']))!=row.get('coverage'):return None
    return dict(day=clock['complete_day'],version=member['version'],
                prices_token=tuple(sorted(tokens)),row=row)


def _cached_benchmark_comparison(store,key,clock,report):
    """只读对照缓存；快路径和实际刷新保持同一诊断，不增加接口请求。"""
    if key not in {'nasdaq','nasdaq100'}:return
    benchmark=store.benchmark(key+'_stockcharts')
    same=benchmark.loc[(benchmark.date==clock['complete_day']) & (benchmark.kind=='close')] if not benchmark.empty else pd.DataFrame()
    latest=report.get('latest',{})
    if not same.empty and latest.get('date')==clock['complete_day'] and latest.get('percent') is not None:
        report['benchmark']={'self_calculated':latest['percent'],'stockcharts':float(same.iloc[-1].percent),
                             'difference_pp':float(latest['percent'])-float(same.iloc[-1].percent)}


@_price_scope
def refresh_market(store,key,now=None,bootstrap=False,repair=False,refresh_members=True,deadline=None,use_futu=True,
                   source_health=None,shared_quotes=None,futu=None,progress_callback=None):
    spec=BREADTH_MARKETS[key];clock=market_clock(spec["market"],now)
    deadline=deadline or (time.monotonic()+BREADTH_RUNTIME_BUDGET_SECONDS)
    report=dict(key=key,time=utc_now(),complete_day=clock["complete_day"],regular_session=clock["regular"],downloaded=0,needs_bootstrap=0,errors=[])
    report.update(market=key,status='running',expected_complete_day=clock['complete_day'],updated_symbols=[],
                  ordinary_source_success_count=0,futu_history_success_count=0,futu_snapshot_success_count=0)
    def checkpoint():
        if progress_callback:
            report['futu_connection_status']=getattr(futu,'connection_status','not_attempted')
            report['futu_history_request_count']=getattr(futu,'history_calls',0)-futu_calls_before
            checked=getattr(futu,'quota_checked_at',None)
            report['futu_quota_remaining']=getattr(futu,'remaining',None) if checked else None
            report['futu_quota']=(dict(checked_at=checked,remaining=futu.remaining,used_count=len(futu.used),reserve=BREADTH_FUTU_RESERVE)
                                  if checked else {'checked':False})
            report['source_health']={'unreachable':sorted(source_health.unreachable),'errors':dict(getattr(source_health,'errors',{}))}
            # 列表须快照，否则调用方后续写日志时会看到被下一批修改的数据。
            progress_callback(json.loads(json.dumps(report,ensure_ascii=False)))
    source_health=source_health or SourceHealth()
    shared_quotes=shared_quotes if shared_quotes is not None else {}
    own_futu=futu is None
    futu=futu or FutuBreadth()
    futu_calls_before=getattr(futu,'history_calls',0)
    # StockCharts 不允许未经批准的脚本自动取数；历史对照缓存仍可只读查看。
    member=store.members(key,clock["day"])
    member_doc=store.read("members",key)
    last_verified=member_doc.get("last_verified_date") or member.get("verified_date") or member.get("date")
    last_checked=member_doc.get("last_member_check_date") or last_verified
    check_due=(not last_checked or (pd.Timestamp(clock["day"])-pd.Timestamp(last_checked)).days>=BREADTH_MEMBER_RECHECK_DAYS)
    if refresh_members and (not member or check_due):
        member_check_success=False
        try:
            fetched=fetch_members(key,futu=futu if use_futu else None)
            symbols,source=fetched[:2]
            source_meta=fetched[2] if len(fetched)>2 else {}
            report["membership_source"]={k:v for k,v in source_meta.items()
                                         if k not in {"official_notices","future_memberships"}}
            if key=="nasdaq":
                count=len(symbols)
                if not BREADTH_NASDAQ_PROXY_MIN_MEMBERS<=count<=BREADTH_NASDAQ_PROXY_MAX_MEMBERS:
                    raise ValueError(f"membership_anomaly: 纳指近似池数量 {count} 超出安全范围")
                official=source_meta.get("official_comp_count")
                if official and abs(count-official)/official>.05:
                    raise ValueError(f"membership_anomaly: 近似池 {count} 与官方 COMP 数量 {official} 相差超过5%")
                if member and len(set(symbols)^set(member["symbols"]))>len(member["symbols"])*BREADTH_NASDAQ_MAX_DAILY_CHANGE_RATIO:
                    raise ValueError("membership_anomaly: 纳指近似池单日变化异常")
                report["membership_source"]={k:v for k,v in source_meta.items() if k!="symbols"}
            if key=="nasdaq" and member and symbols!=member["symbols"]:
                effective=source_meta.get("source_date") if source_meta.get("source_date")==clock["day"] else None
            else:
                effective=validate_membership_candidate(key,symbols,member,source_meta,clock["day"])
            staged=store.save_members(key,symbols,source,clock["day"],spec["universe"],effective_date=effective,
                                      evidence_url=source_meta.get("effective_evidence_url") or
                                      (source if source_meta.get("effective_date") else None))
            future=(stage_future_memberships(store,key,symbols,clock["day"],
                                              source_meta.get("future_memberships",[]),source)
                    if effective is not None else [])
            if future:report["future_memberships_staged"]=[{"version":row["version"],
                 "effective_date":row["effective_date"],"count":len(row["symbols"])} for row in future]
            if effective is None and member:report["errors"].append("pending_membership: 新名单生效日期未核实")
            # 待确认名单不等于现行版本已复核，不能借此延长旧版本有效期。
            if effective is not None or not member or symbols==member["symbols"]:
                store.write("members",key,dict(store.read("members",key),last_verified_date=clock["day"]))
            member=store.members(key,clock["day"])
            member_check_success=True
        except Exception as e:
            report["errors"].append(str(e))
            if member:
                store.write("members",key,dict(store.read("members",key),
                                               last_member_check_error=str(e)[:160]))
        finally:
            if member:
                doc=dict(store.read("members",key),last_member_check_date=clock["day"])
                if member_check_success:doc.pop("last_member_check_error",None)
                store.write("members",key,doc)
    if not member:
        report["errors"].append("缺少经验证的完整成分名单")
        report['status']='stale';checkpoint()
        if own_futu:futu.close()
        return report
    store.establish_pit_start(key,clock["day"])
    last_verified=store.read("members",key).get("last_verified_date") or member.get("verified_date") or member["date"]
    report["membership_stale_days"]=max(0,(pd.Timestamp(clock["day"])-pd.Timestamp(last_verified)).days)
    if report["membership_stale_days"]>BREADTH_MEMBER_RECHECK_DAYS:
        # 官方来源暂不可达时沿用最后一版已验证名单，并在诊断中明确标示过期天数。
        report["errors"].append(f"成分名单已 {report['membership_stale_days']} 天未验证，沿用最后一版")
    symbols=member["symbols"]
    initialized=any(r.get("percent") is not None for r in store.read("results",key).get("rows",[]))
    plan=price_download_plan(store,key,member,clock["day"],clock["complete_day"],bootstrap,repair,initialized)
    pending=plan["pending"]
    report["needs_bootstrap"]=plan["needs_bootstrap"]
    report["prewarm"]={"symbols":plan["prewarm_symbols"],"cached":plan["prewarm_cached"],
                       "missing":len(plan["prewarm_symbols"])-plan["prewarm_cached"]}
    reusable=(_reusable_close(store,key,clock,store.members(key,clock['complete_day']),plan)
              if not bootstrap and not repair and not clock['regular'] else None)
    if reusable:
        latest_frame=store.results(key)
        report.update(close_completion(store,key,clock,calculation=reusable))
        report.update(latest=json.loads(latest_frame.tail(1).to_json(orient='records'))[0],
                      maintenance_complete=True,missing_symbols=[],insufficient_listing_history=[],
                      membership={k:member.get(k) for k in ('date','effective_date','last_verified_at','version','source','universe')},
                      pending_membership=store.read('members',key).get('pending_membership'))
        report.update(futu_connection_status=getattr(futu,'connection_status','not_attempted'),
                      futu_history_request_count=0,futu_quota_remaining=getattr(futu,'remaining',None)
                      if getattr(futu,'quota_checked_at',None) else None,
                      source_health={'unreachable':sorted(source_health.unreachable),
                                     'errors':dict(getattr(source_health,'errors',{}))})
        _cached_benchmark_comparison(store,key,clock,report)
        checkpoint()
        if own_futu:futu.close()
        return report
    snapshot_codes=set()
    local=store.root.parent/'market_breadth_local';local.mkdir(parents=True,exist_ok=True)
    attempt_path=local/(key+'_attempts.json')
    try:attempts=json.loads(attempt_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):attempts={}
    # 最近缺口优先，同一缺口层按最久未尝试顺序续跑，防止失败证券永久占据队首。
    pending.sort(key=lambda c:(-pd.Timestamp(max(missing_price_sessions(store,c,clock['sessions']) or [clock['complete_day']])).value,attempts.get(c,0),c))
    report['remaining_missing_symbols']=list(pending);checkpoint()
    if key=="nasdaq" and use_futu and time.monotonic()<deadline:
        try:
            report["membership_crosscheck"]=futu.crosscheck_nasdaq(symbols)
            if len(report["membership_crosscheck"]["non_nasdaq"])>len(symbols)*.05:
                report["errors"].append("membership_anomaly: 富途交易所交叉核验差异超过5%")
        except Exception as e:report["errors"].append("富途成员交叉核验不可用: "+str(e)[:160])
    dates=clock["sessions"][-400:]
    # 收盘后优先按实际交易日历使用富途快照，正式日线日后仍可核对复权基准。
    if (not bootstrap and not clock["regular"] and use_futu and len(clock["sessions"])>=2
            and time.monotonic()<deadline):
        previous_day=clock["sessions"][-2]
        supported=("US.",) if spec["market"]=="US" else ("SH.","SZ.")
        missing=[c for c in symbols if c.startswith(supported)
                 and not store.prices(c).empty and store.prices(c).iloc[-1].date==previous_day]
        to_fetch=[c for c in missing if c not in shared_quotes]
        quote_errors_start=len(getattr(futu,"quote_errors",[]))
        try:shared_quotes.update(futu.quotes(to_fetch))
        except Exception as e:report["errors"].append("富途收盘快照: "+str(e)[:160])
        quotes={c:shared_quotes[c] for c in missing if c in shared_quotes}
        values=prepare_market_close_quotes(store,missing,quotes,clock["now"],clock["complete_day"],previous_day,spec["market"])
        for code,value in values.items():
            try:
                basis=store.price_metadata(code).get("basis")
                if not basis:continue
                store.save_prices(code,pd.DataFrame([{"date":clock["complete_day"],"close":value}]),basis,
                                  provisional=True)
                snapshot_codes.add(code)
            except Exception as e:report["errors"].append(f"富途收盘价 {code}: {str(e)[:160]}")
        report["close_snapshot"]={"received":len(quotes),"eligible":len(snapshot_codes),"total":len(symbols),
                                  "source":"futu_regular_close_snapshot",
                                  "errors":list(getattr(futu,"quote_errors",[]))[quote_errors_start:]}
        if snapshot_codes:
            interim=calculate_segmented_history({c:store.prices(c) for c in symbols},store,key,
                                                 [clock["complete_day"]],BREADTH_MIN_COVERAGE,
                                                 calendar_sessions=clock["sessions"][-400:])
            if not interim.empty:
                row=interim.iloc[-1].to_dict()
                report["close_snapshot"]["coverage"]=row.get("coverage")
                if pd.notna(row["percent"]):
                    store.save_results(key,[row],member["version"],source="futu_close_snapshot",finality="snapshot_provisional")
        report['futu_snapshot_success_count']=len(snapshot_codes)
    # 先给正式日线一个有界窗口；收盘快照和计算仍有时间完成。
    if not bootstrap and spec["market"]=="US" and clock["regular"]:
        # 日常优先留下批量盘中快照的时间，不让少数难取日线耗尽市场份额。
        daily_deadline=min(deadline-25,time.monotonic()+15)
    elif spec["market"]=="CN" and not bootstrap:
        daily_deadline=min(deadline,time.monotonic()+45)
    else:daily_deadline=deadline
    network_deadline=deadline-min(10,max(1,(deadline-time.monotonic())*.08))
    if spec['market']=='US':daily_deadline=min(daily_deadline,time.monotonic()+max(0,network_deadline-time.monotonic())*.60)
    def download(code):
        if time.monotonic()>=deadline:return None
        prior=store.price_metadata(code);old=store.prices(code)
        gaps=missing_price_sessions(store,code,clock['sessions'])
        first=min([old.iloc[-1].date,*gaps]) if not old.empty else None
        # 长尾从旧末日续接，避免只请求最近50日而跳过中间的数月交易日。
        if first:first=max(first,clock['sessions'][-min(400,len(clock['sessions']))])
        begin=None if bootstrap or old.empty else str((pd.Timestamp(first)-pd.Timedelta(days=10)).date())
        request_deadline=min(daily_deadline,time.monotonic()+BREADTH_US_SYMBOL_TIMEOUT_SECONDS)
        kwargs={'deadline':request_deadline} if spec['market']=='US' else {}
        frame,basis=fetch_prices(code,clock["complete_day"],begin,prior.get("basis"),health=source_health,**kwargs)
        try:save_downloaded_prices(store,code,frame,basis)
        except ValueError:
            frame,basis=fetch_prices(code,clock["complete_day"],None,prior.get("basis"),health=source_health,**kwargs)
            save_downloaded_prices(store,code,frame,basis)
        if bootstrap:
            doc=store.read("prices",code);doc["bootstrap_complete"]=True;store.write("prices",code,doc)
        return code, not missing_price_sessions(store,code,clock['sessions'])
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # 每批只安排4只，达到预算不再排队；外层仍有硬超时。
            for offset in range(0,len(pending),4):
                if time.monotonic()>=daily_deadline:break
                if spec["market"]=="US" and not any(source_health.available(source) for source in
                        ("yahoo_adjclose","sina_us_qfq","eastmoney_us_qfq")):
                    report["errors"].append(f"美股日线源本轮均不可达，剩余 {len(pending)-offset} 只留待下次续跑")
                    break
                jobs={executor.submit(download,c):c for c in pending[offset:offset+4]}
                for code in jobs.values():attempts[code]=time.time()
                for future in concurrent.futures.as_completed(jobs):
                    try:
                        outcome=future.result()
                        if outcome and outcome[1]:
                            report["downloaded"]+=1
                            report['ordinary_source_success_count']+=1;report['updated_symbols'].append(outcome[0])
                        elif outcome:report['errors'].append(f'{outcome[0]}: ordinary response incomplete; missing {missing_price_sessions(store,outcome[0],clock["sessions"])}')
                    except Exception as e:report["errors"].append(f"{jobs[future]}: {str(e)[:180]}")
                if bootstrap and (offset==0 or offset%100==0):print(f"[BREADTH] {key} 已处理 {min(offset+4,len(pending))}/{len(pending)}",flush=True)
                attempt_path.write_text(json.dumps(attempts),encoding='utf-8')
                report['remaining_missing_symbols']=[c for c in pending if missing_price_sessions(store,c,clock['sessions'])]
                checkpoint()
        # 全部真实缺口都进入回退；能否请求由剩余预算和账户预留额度决定。
        history_failures=[c for c in pending if FutuBreadth.supports_code(c) and missing_price_sessions(store,c,clock['sessions'])]
        for code in history_failures if use_futu and (spec["market"]=="US" or bootstrap or repair or clock["regular"]) else []:
            if time.monotonic()>=network_deadline:
                report['errors'].append('Runtime budget exhausted');break
            try:
                prior=store.price_metadata(code);gaps=missing_price_sessions(store,code,clock['sessions'])
                old=store.prices(code)
                first=min([old.iloc[-1].date,*gaps]) if not old.empty else min(gaps)
                first=max(first,clock['sessions'][-min(400,len(clock['sessions']))])
                begin=str((pd.Timestamp(first)-pd.Timedelta(days=10)).date()) if prior.get('basis')=='futu_qfq' else None
                attempts[code]=time.time()
                frame,basis=futu.history(code,clock["complete_day"],start=begin,deadline=network_deadline)
                try:save_downloaded_prices(store,code,frame,basis)
                except ValueError:
                    frame,basis=futu.history(code,clock['complete_day'],deadline=network_deadline)
                    save_downloaded_prices(store,code,frame,basis)
                if not missing_price_sessions(store,code,clock['sessions']):
                    report['downloaded']+=1;report['futu_history_success_count']+=1;report['updated_symbols'].append(code)
                else:report['errors'].append(f'Futu {code}: history response incomplete; missing {missing_price_sessions(store,code,clock["sessions"])}')
            except Exception as e:
                report["errors"].append(f"Futu {code}: "+str(e)[:160])
                # 预留线不能阻止本轮已经占额度证券继续修复；连接失败则不反复重连。
                if 'OpenD not running' in str(e) or 'Runtime budget exhausted' in str(e):break
            attempt_path.write_text(json.dumps(attempts),encoding='utf-8')
            report['remaining_missing_symbols']=[c for c in pending if missing_price_sessions(store,c,clock['sessions'])];checkpoint()
        if getattr(futu,'history_calls',0)>futu_calls_before and time.monotonic()+14<deadline:
            try:futu.refresh_quota()
            except Exception as e:report['errors'].append('富途额度复核: '+str(e)[:160])
        report["membership"]={k:member.get(k) for k in ("date","effective_date","last_verified_at","version","source","universe")}
        report["pending_membership"]=store.read("members",key).get("pending_membership")
        report["historical_policy"]="旧结果保留并标记回算；启用后按当日有效名单计算"
        published={r["date"] for r in store.read("results",key).get("rows",[])
                   if r.get("kind")=="close" and r.get("percent") is not None and r.get("finality")!="snapshot_provisional"}
        target_dates=[day for day in dates if repair or day not in published]
        calculation_dates=sorted(set(target_dates)|{clock['complete_day']})
        # 旧版本中的退指证券仍可能用于未发布日期的计算；其价格缓存不删除。
        needed=set(symbols)
        for day in calculation_dates:
            previous=store.members(key,day)
            needed.update(previous.get("symbols",[]))
        prices,prices_token=store.price_snapshot(needed)
        out=calculate_segmented_history(prices,store,key,calculation_dates,BREADTH_MIN_COVERAGE,
                                        calendar_sessions=dates)
        # 预热空值可保留，绘图会自然断开。
        target_row=out.loc[out.date==clock['complete_day']] if not out.empty else pd.DataFrame()
        completion_member=store.members(key,clock['complete_day'])
        completion_symbols=set(completion_member.get('symbols',[]))
        calculation=(dict(day=clock['complete_day'],version=completion_member.get('version'),
                          prices_token=tuple(pair for pair in prices_token if pair[0] in completion_symbols),
                          row=target_row.iloc[-1].to_dict()) if not target_row.empty else None)
        records=out.loc[out.date.isin(target_dates)].to_dict("records") if not out.empty else []
        pit_start=store.read("members",key).get("pit_start")
        if repair and pit_start and clock["complete_day"]<pit_start:
            # 显式修复可补最近旧日期，但必须明确标成当前名单回算，不能冒称逐日成分。
            backcast=calculate_history(prices,symbols,BREADTH_MIN_COVERAGE,dates)
            match=backcast.loc[backcast.date==clock["complete_day"]]
            if not match.empty:
                record=match.iloc[-1].to_dict()
                record.update(membership_version=member["version"],membership_policy="current_members_backcast")
                records.append(record)
        if records:
            # 临时价格即便跨日留在价格缓存，也不能误升级为正式收盘广度。
            provisional_days={day for code in needed for day in store.price_metadata(code).get("provisional_dates",[])}
            affected_days=set()
            for index,day in enumerate(dates):
                if day in provisional_days:affected_days.update(dates[index:index+50])
            official=[r for r in records if r["date"] not in affected_days]
            provisional=[r for r in records if r not in official]
            if official:store.save_results(key,official,member["version"],repair=repair)
            if provisional:store.save_results(key,provisional,member["version"],source="futu_close_snapshot",finality="snapshot_provisional")
        if clock["regular"] and time.monotonic()<deadline:
            snapshot=store.read("snapshots",key)
            fetched=pd.to_datetime(snapshot.get("updated_at"),utc=True,errors="coerce")
            age=(clock["now"]-fetched).total_seconds()/60 if pd.notna(fetched) else float("inf")
            quotes=snapshot.get("quotes",{}) if 0<=age<=max(15,min(60,BREADTH_SNAPSHOT_TTL_MINUTES)) else {}
            updated=False
            if not quotes:
                try:quotes=fetch_quotes(symbols,clock["now"],health=source_health,errors=report["errors"])
                except Exception as e:report["errors"].append("快照: "+str(e)[:160]);quotes={}
                updated=bool(quotes)
            # 缓存文件新鲜不代表逐证券报价新鲜；过期/缺失报价仍交富途补。
            missing=[c for c in symbols if c not in quotes or not eligible_quote(quotes[c].get("time"),clock["now"],spec["market"],60)]
            if missing and use_futu and time.monotonic()<deadline:
                try:
                    to_fetch=[c for c in missing if c not in shared_quotes]
                    quote_errors_start=len(getattr(futu,"quote_errors",[]))
                    shared_quotes.update(futu.quotes(to_fetch))
                    supplemental={c:shared_quotes[c] for c in missing if c in shared_quotes}
                    quotes.update(supplemental);updated=updated or bool(supplemental)
                    report["errors"].extend("Futu快照批次: "+error for error in
                                            list(getattr(futu,"quote_errors",[]))[quote_errors_start:])
                except Exception as e:report["errors"].append("Futu快照: "+str(e)[:160])
            if updated:
                store.write("snapshots",key,dict(type="snapshots",updated_at=utc_now(),quotes=quotes))
            values=prepare_quotes(store,symbols,quotes,clock["now"],spec["market"],clock["complete_day"])
            report["snapshot"]={"collected_at":store.read("snapshots",key).get("updated_at"),"received":len(quotes),"eligible":len(values),"sources":sorted(set(q.get("source","") for q in quotes.values())),"quote_times":{c:quotes[c].get("time") for c in quotes}}
            live=calculate_intraday(prices,symbols,clock["day"],values,BREADTH_MIN_COVERAGE,dates)
            stamps=[pd.Timestamp(quotes[c]["time"]) for c in values]
            if stamps:
                # 时效以报价本身为准，而不是本次计算或读取缓存的时间。
                zone="America/New_York" if spec["market"]=="US" else "Asia/Shanghai"
                normalized=[t.tz_localize(zone) if t.tzinfo is None else t for t in stamps]
                live["observed_at"]=min(normalized).isoformat()
            store.save_results(key,[live],member["version"])
        latest_frame=store.results(key)
        report["latest"]=json.loads(latest_frame.tail(1).to_json(orient="records"))[0] if not latest_frame.empty else {}
        report["missing_symbols"]=[c for c in symbols if store.prices(c).empty or store.prices(c).iloc[-1].date<clock["complete_day"]]
        report["insufficient_listing_history"]=[c for c in symbols if 0<len(store.prices(c))<50]
        report["source_health"]={"unreachable":sorted(source_health.unreachable)}
        report['source_health']['errors']=dict(getattr(source_health,'errors',{}))
        report["futu_quota"]=(dict(checked_at=futu.quota_checked_at,remaining=futu.remaining,
                                   used_count=len(futu.used),reserve=BREADTH_FUTU_RESERVE)
                              if getattr(futu,"quota_checked_at",None) else {"checked":False})
        report.update(close_completion(store,key,clock,calculation=calculation))
        report['futu_connection_status']=getattr(futu,'connection_status','not_attempted')
        report['futu_history_request_count']=getattr(futu,'history_calls',0)-futu_calls_before
        report['futu_quota_remaining']=getattr(futu,'remaining',None) if getattr(futu,'quota_checked_at',None) else None
        if report['status']!='complete':
            report['errors'].append('Insufficient coverage' if report['coverage']<BREADTH_MIN_COVERAGE else 'Formal close pending')
            if time.monotonic()>=network_deadline:report['errors'].append('Runtime budget exhausted')
        checkpoint()
        _cached_benchmark_comparison(store,key,clock,report)
    finally:
        if own_futu:futu.close()
    report["prewarm"]["cached"]=sum(not store.prices(code).empty for code in plan["prewarm_symbols"])
    report["prewarm"]["missing"]=len(plan["prewarm_symbols"])-report["prewarm"]["cached"]
    report["needs_bootstrap"]=sum(store.prices(c).empty for c in symbols)
    # 本轮可能刚完成调入预热，必须使用最终统计，不能复用下载前的 missing。
    report['maintenance_complete']=not bool(report.get('remaining_missing_symbols') or report['prewarm']['missing'])
    return report


def refresh_for_charts(root=None):
    """子进程硬超时，网络接口即使卡死也不延误现有业务。"""
    if not BREADTH_ENABLED:return
    project=Path(__file__).resolve().parents[1]
    root=Path(root or project/"cache"/"market_breadth")
    env=os.environ.copy();env["PYTHONIOENCODING"]="utf-8"
    try:
        # 300秒是包装器总上限；给CLI父进程先终止自己的worker留下启动/退出余量。
        result=subprocess.run([sys.executable,str(project/"market_breadth.py"),"--update","--budget",str(max(5,BREADTH_RUNTIME_BUDGET_SECONDS-5)),"--cache-root",str(root)],cwd=project,env=env,timeout=BREADTH_RUNTIME_BUDGET_SECONDS,capture_output=True,encoding="utf-8",errors="replace")
        if result.stdout:
            for line in result.stdout.splitlines():
                if 'expected_complete_day' in line or line.startswith('[BREADTH]'):print(line)
        if result.returncode:print("[WARN] 广度未全部完成；原有RSI继续生成。"+result.stderr[-600:])
    except subprocess.TimeoutExpired:print("[WARN] 广度采集已到时间预算，继续使用已保存数据。")


@_price_scope
def _ndx_chart_backcast(store,dates,clock):
    """绘图时按首次核验名单回算早期历史，不向正式 results 写入数据。"""
    doc=store.read("members","nasdaq100")
    pit_start=doc.get("pit_start")
    versions=doc.get("rows",[])
    sessions=clock.get("sessions",[])[-BREADTH_HISTORY_ROWS:]
    if not pit_start or not versions or not sessions:return pd.DataFrame()
    baseline=min(versions,key=lambda r:(r.get("effective_date") or r["date"],r["date"],r["version"]))
    prices={code:store.prices(code) for code in baseline["symbols"]}
    frame=calculate_history(prices,baseline["symbols"],BREADTH_MIN_COVERAGE,sessions)
    start=str(pd.to_datetime(dates).min().date())
    frame=frame.loc[frame.date.between(start,str(pd.Timestamp(pit_start)-pd.Timedelta(days=1))[:10])].copy()
    if frame.empty:return frame
    # 临时收盘价会影响其后 50 根均线；这段历史只能留空，不能冒充真实日线。
    provisional={day for code in baseline["symbols"] for day in store.price_metadata(code).get("provisional_dates",[])}
    affected=set()
    for index,day in enumerate(sessions):
        if day in provisional:affected.update(sessions[index:index+50])
    frame.loc[frame.date.isin(affected),"percent"]=float("nan")
    frame["membership_version"]=baseline["version"]
    frame["membership_policy"]="current_members_backcast"
    frame["source"]="self_calculated_backcast"
    frame["finality"]="chart_only"
    return frame


def _approved_direct_rows(store,key,clock):
    """配置登记即表示来源口径与访问许可已核验；缓存自身仍须逐行校验。"""
    approved=BREADTH_DIRECT_INDICATOR_SOURCES.get(key)
    if not approved or approved.get("access_approved") is not True or \
            approved.get("universe")!=BREADTH_MARKETS[key]["universe"]:return []
    try:doc=store.read("direct_indicators",key)
    except (OSError,ValueError,TypeError):return []
    source=approved.get("source_id")
    if not source or doc.get("type")!="direct_indicators" or doc.get("source_id")!=source or \
            doc.get("universe")!=approved["universe"] or doc.get("metric")!="percent_members_above_sma50":return []
    if not isinstance(doc.get("rows"),list):return []
    rows=[];seen=set()
    for row in doc.get("rows",[]):
        try:
            day=row["date"];kind=row["kind"];percent=float(row["percent"])
            if str(pd.Timestamp(day).date())!=day or kind not in {"close","intraday"} or \
                    not math.isfinite(percent) or not 0<=percent<=100 or row.get("source")!=source:continue
            if kind=="close":
                if day>clock["complete_day"] or day not in clock.get("sessions",[]):continue
            else:
                raw_time=row.get("observed_at")
                if not raw_time or pd.Timestamp(raw_time).tzinfo is None:continue
                observed=pd.to_datetime(raw_time,utc=True,errors="coerce")
                age=(clock["now"]-observed).total_seconds() if pd.notna(observed) else float("inf")
                if not clock.get("regular") or day!=clock["day"] or not -60<=age<=3600:continue
            if day in seen:continue
            seen.add(day)
            rows.append(dict(row,percent=percent,membership_policy="verified_direct_indicator",
                             finality="chart_only"))
        except (KeyError,ValueError,TypeError,OverflowError):continue
    return sorted(rows,key=lambda row:row["date"])


def chart_data(key,dates,root=None):
    project=Path(__file__).resolve().parents[1]
    store=BreadthStore(root or project/"cache"/"market_breadth")
    frame=store.results(key)
    clock=market_clock(BREADTH_MARKETS[key]["market"])
    start=str(pd.to_datetime(dates).min().date());end=max(str(pd.to_datetime(dates).max().date()),clock["day"])
    frame=frame.loc[frame.date.between(start,end)].copy()
    # 隔夜/过期临时记录留在诊断中，但不能伪装成正式历史或新鲜盘中值。
    for index,row in frame.loc[frame.kind=="intraday"].iterrows():
        observed=pd.to_datetime(row.get("observed_at"),utc=True,errors="coerce")
        age=(clock["now"]-observed).total_seconds() if pd.notna(observed) else float("inf")
        if row.date!=clock["day"] or not -60<=age<=3600:frame.loc[index,"percent"]=None
    self_values={row.date:row.percent for row in frame.itertuples() if pd.notna(row.percent)}
    if key=="nasdaq100":
        backcast=_ndx_chart_backcast(store,dates,clock)
        if not backcast.empty:
            added=backcast.loc[~backcast.date.isin(frame.date)]
            frame=added if frame.empty else pd.concat([added,frame],ignore_index=True)
    direct=_approved_direct_rows(store,key,clock)
    if direct:
        direct_frame=pd.DataFrame(direct)
        frame=pd.concat([frame.loc[~frame.date.isin(direct_frame.date)],direct_frame],ignore_index=True)
    frame=frame.loc[frame.date.between(start,end)].sort_values("date").reset_index(drop=True)
    source_counts=frame.loc[frame.percent.notna(),"source"].fillna("self_calculated").value_counts().to_dict() if not frame.empty else {}
    backcast_rows=frame.loc[(frame.get("membership_policy")=="current_members_backcast") & frame.percent.notna()] if not frame.empty else frame
    direct_difference=None
    for row in reversed(direct):
        if row["date"] in self_values:
            direct_difference=row["percent"]-float(self_values[row["date"]]);break
    common=dict(approximate=key=="nasdaq",source_counts=source_counts,
                backcast_start=backcast_rows.iloc[0].date if not backcast_rows.empty else None,
                backcast_end=backcast_rows.iloc[-1].date if not backcast_rows.empty else None,
                backcast_basis_date=store.read("members","nasdaq100").get("pit_start") if key=="nasdaq100" and not backcast_rows.empty else None,
                backcast_valid_rows=len(backcast_rows),
                backcast_latest_coverage=float(backcast_rows.iloc[-1].coverage) if not backcast_rows.empty else None,
                difference_pp=direct_difference)
    valid=frame.loc[frame.percent.notna()]
    if not valid.empty:
        latest=valid.iloc[-1]
        current=(latest.kind=="intraday" and latest.date==clock["day"] or
                 latest.kind=="close" and latest.date==clock["complete_day"])
        sessions=clock.get("sessions",[])
        age=sum(latest.date<day<=clock.get("complete_day",clock["day"]) for day in sessions)
        frame.attrs["breadth_display"]=dict(common,current=current,age_sessions=age,
                                          last_valid_date=latest.date,latest_source=latest.get("source"),
                                          max_age_sessions=BREADTH_FALLBACK_MAX_SESSIONS)
    else:frame.attrs["breadth_display"]=common
    return frame
