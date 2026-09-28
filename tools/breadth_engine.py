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
import pandas as pd
import pandas_market_calendars as mcal
from tools.market_breadth import BreadthStore, calculate_history, calculate_intraday, eligible_quote, utc_now
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


def refresh_market(store,key,now=None,bootstrap=False,refresh_members=True,deadline=None,use_futu=True,
                   source_health=None,shared_quotes=None,futu=None):
    spec=BREADTH_MARKETS[key];clock=market_clock(spec["market"],now)
    deadline=deadline or (time.monotonic()+180)
    report=dict(key=key,time=utc_now(),complete_day=clock["complete_day"],regular_session=clock["regular"],downloaded=0,needs_bootstrap=0,errors=[])
    if key=="nasdaq":
        # 用户批准：纳指官方广度只积累真实日期，不按稀疏参考点插值。
        result_key="nasdaq_stockcharts"
        old=store.read("results",result_key)
        stamp=pd.to_datetime(old.get("updated_at"),utc=True,errors="coerce")
        age=(clock["now"]-stamp).total_seconds()/60 if pd.notna(stamp) else float("inf")
        if not (0<=age<=max(15,min(60,BREADTH_SNAPSHOT_TTL_MINUTES))):
            try:
                rows=fetch_stockcharts(spec["external"],clock)
                store.save_results(result_key,rows,"stockcharts_NAA50R","stockcharts_NAA50R")
            except Exception as e:report["errors"].append(str(e))
        frame=store.results(result_key)
        report["latest"]=json.loads(frame.tail(1).to_json(orient="records"))[0] if not frame.empty else {}
        report["historical_policy"]="自2026-09-28启用后积累；不插值，不与综合指数自算口径拼接"
        return report
    member=store.members(key)
    if refresh_members and (not member or member["date"]!=clock["day"]):
        try:
            symbols,source=fetch_members(key)
            member=store.save_members(key,symbols,source,clock["day"],spec["universe"])
        except Exception as e:report["errors"].append(str(e))
    if not member:
        report["errors"].append("缺少经验证的完整成分名单")
        return report
    if (pd.Timestamp(clock["day"])-pd.Timestamp(member["date"])).days>7:
        report["errors"].append("成分名单超过7天未验证，停止发布新值")
        return report
    symbols=member["symbols"];pending=[]
    source_health=source_health or SourceHealth()
    shared_quotes=shared_quotes if shared_quotes is not None else {}
    initialized=any(r.get("percent") is not None for r in store.read("results",key).get("rows",[]))
    missing_added=0
    for code in symbols:
        frame=store.prices(code)
        if frame.empty:
            report["needs_bootstrap"]+=1
            if bootstrap or (initialized and missing_added<10):
                pending.append(code);missing_added+=1
        elif frame.iloc[-1].date<clock["complete_day"] or (bootstrap and len(frame)<400 and len(frame)>=50):pending.append(code)
    # 已下载上市不足400天的证券也算建库完成，避免每天无限重拉。
    if bootstrap:
        pending=[c for c in pending if not (store.read("prices",c).get("bootstrap_complete") and not store.prices(c).empty and store.prices(c).iloc[-1].date>=clock["complete_day"])]
    own_futu=futu is None
    futu=futu or FutuBreadth();failures=[];snapshot_codes=set();official_current=set()
    dates=clock["sessions"][-400:]
    # 先落盘合规的15:00快照，随后仍优先核验正式日线；慢接口触发外层超时时也有可信收盘值。
    if (spec["market"]=="CN" and not bootstrap and
            clock["now"]>=pd.Timestamp(clock["complete_day"],tz="Asia/Shanghai")+pd.Timedelta(hours=15,minutes=15)
            and use_futu and len(clock["sessions"])>=2
            and time.monotonic()<deadline):
        previous_day=clock["sessions"][-2]
        missing=[c for c in symbols if c.startswith(("SH.","SZ."))
                 and not store.prices(c).empty and store.prices(c).iloc[-1].date==previous_day]
        to_fetch=[c for c in missing if c not in shared_quotes]
        quote_errors_start=len(getattr(futu,"quote_errors",[]))
        try:shared_quotes.update(futu.quotes(to_fetch))
        except Exception as e:report["errors"].append("富途收盘快照: "+str(e)[:160])
        quotes={c:shared_quotes[c] for c in missing if c in shared_quotes}
        values=prepare_close_quotes(store,missing,quotes,clock["now"],clock["complete_day"],previous_day)
        for code,value in values.items():
            try:
                basis=store.read("prices",code).get("basis")
                if not basis:continue
                store.save_prices(code,pd.DataFrame([{"date":clock["complete_day"],"close":value}]),basis)
                snapshot_codes.add(code)
            except Exception as e:report["errors"].append(f"富途收盘价 {code}: {str(e)[:160]}")
        report["close_snapshot"]={"received":len(quotes),"eligible":len(snapshot_codes),"total":len(symbols),
                                  "source":"futu_snapshot_15_clock",
                                  "errors":list(getattr(futu,"quote_errors",[]))[quote_errors_start:]}
        if snapshot_codes:
            interim=calculate_history({c:store.prices(c) for c in symbols},symbols,
                                      BREADTH_MIN_COVERAGE,dates).iloc[-1].to_dict()
            report["close_snapshot"]["coverage"]=interim.get("coverage")
            if pd.notna(interim["percent"]):
                store.save_results(key,[interim],member["version"],source="futu_close_snapshot")
    # 先给正式日线一个有界窗口；收盘快照和计算仍有时间完成。
    daily_deadline=min(deadline,time.monotonic()+45) if spec["market"]=="CN" and not bootstrap else deadline
    def download(code):
        if time.monotonic()>=deadline:return None
        prior=store.read("prices",code);old=store.prices(code)
        begin=None if bootstrap or old.empty else str((pd.Timestamp(old.iloc[-1].date)-pd.Timedelta(days=10)).date())
        frame,basis=fetch_prices(code,clock["complete_day"],begin,prior.get("basis"),health=source_health)
        try:store.save_prices(code,frame,basis)
        except ValueError:
            frame,basis=fetch_prices(code,clock["complete_day"],None,prior.get("basis"),health=source_health)
            store.save_prices(code,frame,basis)
        if bootstrap:
            doc=store.read("prices",code);doc["bootstrap_complete"]=True;store.write("prices",code,doc)
        return code, (not frame.empty and frame.iloc[-1].date==clock["complete_day"])
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # 每批只安排4只，达到预算不再排队；外层仍有硬超时。
            for offset in range(0,len(pending),4):
                if time.monotonic()>=daily_deadline:break
                jobs={executor.submit(download,c):c for c in pending[offset:offset+4]}
                for future in concurrent.futures.as_completed(jobs):
                    try:
                        outcome=future.result()
                        if outcome:
                            report["downloaded"]+=1
                            if outcome[1]:official_current.add(outcome[0])
                    except Exception as e:failures.append(jobs[future]);report["errors"].append(f"{jobs[future]}: {str(e)[:180]}")
                if bootstrap and (offset==0 or offset%100==0):print(f"[BREADTH] {key} 已处理 {min(offset+4,len(pending))}/{len(pending)}",flush=True)
        # 只为少量缺口使用富途，避免建库耗尽整账户额度。
        history_failures=[c for c in failures if FutuBreadth.supports_code(c)]
        for code in history_failures[:10] if use_futu and (spec["market"]=="US" or bootstrap or clock["regular"]) else []:
            if time.monotonic()>=deadline:break
            try:
                frame,basis=futu.history(code,clock["complete_day"]);store.save_prices(code,frame,basis);report["downloaded"]+=1
            except Exception as e:
                report["errors"].append("Futu: "+str(e)[:160]);break
        report["membership"]={k:member[k] for k in ("date","version","source","universe")}
        report["historical_policy"]="初始历史按当期完整名单回算；既有正式日期保留原成分版本"
        prices={c:store.prices(c) for c in symbols}
        out=calculate_history(prices,symbols,BREADTH_MIN_COVERAGE,dates)
        # 预热空值可保留，绘图会自然断开。
        records=out.to_dict("records")
        if snapshot_codes and snapshot_codes-official_current:
            store.save_results(key,records[:-1],member["version"])
            store.save_results(key,records[-1:],member["version"],source="futu_close_snapshot")
        else:store.save_results(key,records,member["version"])
        if clock["regular"] and time.monotonic()<deadline:
            snapshot=store.read("snapshots",key)
            fetched=pd.to_datetime(snapshot.get("updated_at"),utc=True,errors="coerce")
            age=(clock["now"]-fetched).total_seconds()/60 if pd.notna(fetched) else float("inf")
            quotes=snapshot.get("quotes",{}) if 0<=age<=max(15,min(60,BREADTH_SNAPSHOT_TTL_MINUTES)) else {}
            if not quotes:
                try:quotes=fetch_quotes(symbols,clock["now"],health=source_health,errors=report["errors"])
                except Exception as e:report["errors"].append("快照: "+str(e)[:160]);quotes={}
                missing=[c for c in symbols if c not in quotes or not eligible_quote(quotes[c].get("time"),clock["now"],spec["market"],60)]
                if missing and use_futu and time.monotonic()<deadline:
                    try:
                        to_fetch=[c for c in missing if c not in shared_quotes]
                        shared_quotes.update(futu.quotes(to_fetch))
                        quotes.update({c:shared_quotes[c] for c in missing if c in shared_quotes})
                    except Exception as e:report["errors"].append("Futu快照: "+str(e)[:160])
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
        report["latest"]=json.loads(store.results(key).tail(1).to_json(orient="records"))[0]
    finally:
        if own_futu:futu.close()
    report["needs_bootstrap"]=sum(store.prices(c).empty for c in symbols)
    return report


def refresh_for_charts(root=None):
    """子进程硬超时，网络接口即使卡死也不延误现有业务。"""
    if not BREADTH_ENABLED:return
    project=Path(__file__).resolve().parents[1]
    root=Path(root or project/"cache"/"market_breadth")
    env=os.environ.copy();env["PYTHONIOENCODING"]="utf-8"
    try:
        result=subprocess.run([sys.executable,str(project/"market_breadth.py"),"--update","--budget",str(max(5,BREADTH_RUNTIME_BUDGET_SECONDS-5)),"--cache-root",str(root)],cwd=project,env=env,timeout=BREADTH_RUNTIME_BUDGET_SECONDS,capture_output=True,encoding="utf-8",errors="replace")
        if result.stdout:print(result.stdout[-4000:])
        if result.returncode:print("[WARN] 广度未全部完成；原有RSI继续生成。"+result.stderr[-600:])
    except subprocess.TimeoutExpired:print("[WARN] 广度采集已到时间预算，继续使用已保存数据。")


def chart_data(key,dates,root=None):
    project=Path(__file__).resolve().parents[1]
    store=BreadthStore(root or project/"cache"/"market_breadth")
    frame=store.results("nasdaq_stockcharts" if key=="nasdaq" else key)
    if frame.empty:return frame
    clock=market_clock(BREADTH_MARKETS[key]["market"])
    start=str(pd.to_datetime(dates).min().date());end=max(str(pd.to_datetime(dates).max().date()),clock["day"])
    frame=frame.loc[frame.date.between(start,end)].copy()
    # 隔夜/过期临时记录留在诊断中，但不能伪装成正式历史或新鲜盘中值。
    for index,row in frame.loc[frame.kind=="intraday"].iterrows():
        observed=pd.to_datetime(row.get("observed_at"),utc=True,errors="coerce")
        age=(clock["now"]-observed).total_seconds() if pd.notna(observed) else float("inf")
        if row.date!=clock["day"] or not -60<=age<=3600:frame.loc[index,"percent"]=None
    valid=frame.loc[frame.percent.notna()]
    if not valid.empty:
        latest=valid.iloc[-1]
        current=(latest.kind=="intraday" and latest.date==clock["day"] or
                 latest.kind=="close" and latest.date==clock["complete_day"])
        sessions=clock.get("sessions",[])
        age=sum(latest.date<day<=clock.get("complete_day",clock["day"]) for day in sessions)
        frame.attrs["breadth_display"]={"current":current,"age_sessions":age,
                                          "last_valid_date":latest.date,
                                          "max_age_sessions":BREADTH_FALLBACK_MAX_SESSIONS}
    return frame
