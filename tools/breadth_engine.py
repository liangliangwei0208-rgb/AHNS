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


def refresh_market(store,key,now=None,bootstrap=False,repair=False,refresh_members=True,deadline=None,use_futu=True,
                   source_health=None,shared_quotes=None,futu=None):
    spec=BREADTH_MARKETS[key];clock=market_clock(spec["market"],now)
    deadline=deadline or (time.monotonic()+180)
    report=dict(key=key,time=utc_now(),complete_day=clock["complete_day"],regular_session=clock["regular"],downloaded=0,needs_bootstrap=0,errors=[])
    source_health=source_health or SourceHealth()
    shared_quotes=shared_quotes if shared_quotes is not None else {}
    if key=="nasdaq":
        # 外部广度只作对照，失败不得阻止自算链路。
        old_benchmark=store.read("benchmarks","nasdaq_stockcharts")
        stamp=pd.to_datetime(old_benchmark.get("updated_at"),utc=True,errors="coerce")
        age=(clock["now"]-stamp).total_seconds()/60 if pd.notna(stamp) else float("inf")
        if not (0<=age<=max(15,min(60,BREADTH_SNAPSHOT_TTL_MINUTES))):
            try:store.save_benchmark("nasdaq_stockcharts",fetch_stockcharts(spec["external"],clock),"stockcharts_NAA50R")
            except Exception as e:report["errors"].append("StockCharts benchmark: "+str(e)[:160])
    member=store.members(key,clock["day"])
    member_doc=store.read("members",key)
    last_verified=member_doc.get("last_verified_date") or member.get("verified_date") or member.get("date")
    if refresh_members and (not member or last_verified!=clock["day"]):
        try:
            fetched=fetch_members(key)
            symbols,source=fetched[:2]
            source_meta=fetched[2] if len(fetched)>2 else {}
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
            if not member or symbols==member["symbols"]:
                effective=member.get("effective_date") if member else clock["day"]
            elif key=="nasdaq":
                effective=source_meta.get("source_date") if source_meta.get("source_date")==clock["day"] else None
            else:
                # 成分文件可能提前披露调整，未知正式生效日时不得猜测。
                effective=source_meta.get("effective_date")
            staged=store.save_members(key,symbols,source,clock["day"],spec["universe"],effective_date=effective)
            if effective is None and member:report["errors"].append("pending_membership: 新名单生效日期未核实")
            # 待确认名单不等于现行版本已复核，不能借此延长旧版本有效期。
            if effective is not None or not member or symbols==member["symbols"]:
                store.write("members",key,dict(store.read("members",key),last_verified_date=clock["day"]))
            member=store.members(key,clock["day"])
        except Exception as e:report["errors"].append(str(e))
    if not member:
        report["errors"].append("缺少经验证的完整成分名单")
        return report
    store.establish_pit_start(key,clock["day"])
    last_verified=store.read("members",key).get("last_verified_date") or member.get("verified_date") or member["date"]
    if (pd.Timestamp(clock["day"])-pd.Timestamp(last_verified)).days>7:
        report["errors"].append("成分名单超过7天未验证，停止发布新值")
        return report
    symbols=member["symbols"];pending=[]
    initialized=any(r.get("percent") is not None for r in store.read("results",key).get("rows",[]))
    missing_added=0
    for code in symbols:
        frame=store.prices(code)
        if frame.empty:
            report["needs_bootstrap"]+=1
            if bootstrap or repair or (initialized and missing_added<10):
                pending.append(code);missing_added+=1
        elif (frame.iloc[-1].date<clock["complete_day"] or
              (bootstrap and len(frame)<400 and len(frame)>=50) or
              (repair and (len(frame)<50 or bool(store.read("prices",code).get("provisional_dates"))))):
            pending.append(code)
    # 已下载上市不足400天的证券也算建库完成，避免每天无限重拉。
    if bootstrap:
        pending=[c for c in pending if not (store.read("prices",c).get("bootstrap_complete") and not store.prices(c).empty and store.prices(c).iloc[-1].date>=clock["complete_day"])]
        # 重跑时从另一端开始，避免少数永久失败的早序代码反复占用预算。
        pending.sort(reverse=True)
    own_futu=futu is None
    futu=futu or FutuBreadth();failures=[];snapshot_codes=set()
    if use_futu and hasattr(futu,"refresh_quota") and not getattr(futu,"quota_checked_at",None) and time.monotonic()+5<deadline:
        try:futu.refresh_quota()
        except Exception as e:report["errors"].append("富途额度查询不可用: "+str(e)[:160])
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
                basis=store.read("prices",code).get("basis")
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
        if spec["market"]=="US":
            # 已有快照的股票只抽少量正式日线核对，避免数千只每日重复下载。
            verify=set(sorted(snapshot_codes)[:3])
            pending=[c for c in pending if c not in snapshot_codes or c in verify]
    # 先给正式日线一个有界窗口；收盘快照和计算仍有时间完成。
    if not bootstrap and spec["market"]=="US" and clock["regular"]:
        # 日常优先留下批量盘中快照的时间，不让少数难取日线耗尽市场份额。
        daily_deadline=min(deadline-25,time.monotonic()+15)
    elif spec["market"]=="CN" and not bootstrap:
        daily_deadline=min(deadline,time.monotonic()+45)
    else:daily_deadline=deadline
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
                if spec["market"]=="US" and not any(source_health.available(source) for source in
                        ("yahoo_adjclose","sina_us_qfq","eastmoney_us_qfq")):
                    report["errors"].append(f"美股日线源本轮均不可达，剩余 {len(pending)-offset} 只留待下次续跑")
                    break
                jobs={executor.submit(download,c):c for c in pending[offset:offset+4]}
                for future in concurrent.futures.as_completed(jobs):
                    try:
                        outcome=future.result()
                        if outcome:
                            report["downloaded"]+=1
                    except Exception as e:failures.append(jobs[future]);report["errors"].append(f"{jobs[future]}: {str(e)[:180]}")
                if bootstrap and (offset==0 or offset%100==0):print(f"[BREADTH] {key} 已处理 {min(offset+4,len(pending))}/{len(pending)}",flush=True)
        # 只为少量缺口使用富途，避免建库耗尽整账户额度。
        history_failures=[c for c in failures if FutuBreadth.supports_code(c)]
        for code in history_failures[:10] if use_futu and (spec["market"]=="US" or bootstrap or repair or clock["regular"]) else []:
            if time.monotonic()>=deadline:break
            try:
                frame,basis=futu.history(code,clock["complete_day"]);store.save_prices(code,frame,basis);report["downloaded"]+=1
            except Exception as e:
                report["errors"].append(f"Futu {code}: "+str(e)[:160])
                if "额度" in str(e):break
        report["membership"]={k:member.get(k) for k in ("date","effective_date","last_verified_at","version","source","universe")}
        report["pending_membership"]=store.read("members",key).get("pending_membership")
        report["historical_policy"]="旧结果保留并标记回算；启用后按当日有效名单计算"
        published={r["date"] for r in store.read("results",key).get("rows",[])
                   if r.get("kind")=="close" and r.get("percent") is not None and r.get("finality")!="snapshot_provisional"}
        target_dates=[day for day in dates if repair or day not in published]
        # 旧版本中的退指证券仍可能用于未发布日期的计算；其价格缓存不删除。
        needed=set(symbols)
        for day in target_dates:
            previous=store.members(key,day)
            needed.update(previous.get("symbols",[]))
        prices={c:store.prices(c) for c in needed}
        out=calculate_segmented_history(prices,store,key,target_dates,BREADTH_MIN_COVERAGE,
                                        calendar_sessions=dates)
        # 预热空值可保留，绘图会自然断开。
        records=out.to_dict("records")
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
            provisional_days={day for code in needed for day in store.read("prices",code).get("provisional_dates",[])}
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
        report["futu_quota"]=(dict(checked_at=futu.quota_checked_at,remaining=futu.remaining,
                                   used_count=len(futu.used),reserve=BREADTH_FUTU_RESERVE)
                              if getattr(futu,"quota_checked_at",None) else {"checked":False})
        if key=="nasdaq":
            benchmark=store.benchmark("nasdaq_stockcharts")
            same=benchmark.loc[(benchmark.date==clock["complete_day"]) & (benchmark.kind=="close")] if not benchmark.empty else pd.DataFrame()
            latest=report["latest"]
            if not same.empty and latest.get("date")==clock["complete_day"] and latest.get("percent") is not None:
                report["benchmark"]={"self_calculated":latest["percent"],"stockcharts":float(same.iloc[-1].percent),
                                     "difference_pp":float(latest["percent"])-float(same.iloc[-1].percent)}
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
    frame=store.results(key)
    if frame.empty:
        frame.attrs["breadth_display"]={"approximate":key=="nasdaq"}
        return frame
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
        frame.attrs["breadth_display"]={"current":current,"age_sessions":age,"approximate":key=="nasdaq",
                                          "last_valid_date":latest.date,
                                          "max_age_sessions":BREADTH_FALLBACK_MAX_SESSIONS}
    else:frame.attrs["breadth_display"]={"approximate":key=="nasdaq"}
    return frame
