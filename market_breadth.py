"""50DMA独立建库/增量/诊断入口。默认只读状态；不发邮件。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pandas as pd
from tools.market_breadth import BreadthStore
from tools.breadth_sources import FutuBreadth, SourceHealth
from tools.configs.market_breadth_configs import BREADTH_MARKETS, BREADTH_DEFAULT_MARKETS, BREADTH_RUNTIME_BUDGET_SECONDS


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    actions=p.add_mutually_exclusive_group()
    actions.add_argument("--bootstrap",action="store_true",help="首次完整建库，可中断后重跑")
    actions.add_argument("--update",action="store_true",help="只更新已有证券及盘中结果")
    actions.add_argument("--repair",action="store_true",help="只修复当前成分的历史不足或最新日期缺口")
    actions.add_argument("--status",action="store_true",help="只读覆盖率与状态")
    actions.add_argument("--activate-pending",action="store_true",help="凭官方公告确认待生效成分名单")
    p.add_argument("--market",nargs="+",choices=list(BREADTH_MARKETS),default=list(BREADTH_DEFAULT_MARKETS))
    p.add_argument("--cache-root",type=Path,default=Path(__file__).resolve().parent/"cache"/"market_breadth")
    p.add_argument("--budget",type=int,default=BREADTH_RUNTIME_BUDGET_SECONDS)
    p.add_argument("--no-futu",action="store_true")
    p.add_argument("--report-path",type=Path,default=Path(__file__).resolve().parent/"output"/"market_breadth_diagnostics.json")
    p.add_argument("--effective-date",help="确认待生效名单的 YYYY-MM-DD 生效日")
    p.add_argument("--evidence-url",help="相应指数公司的官方公告 HTTPS 地址")
    p.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    store=BreadthStore(a.cache_root)
    if a.activate_pending:
        if len(a.market)!=1 or not a.effective_date or not a.evidence_url:
            p.error("--activate-pending 须指定单一 --market、--effective-date 和 --evidence-url")
        row=store.activate_pending(a.market[0],a.effective_date,a.evidence_url)
        print(json.dumps(dict(market=a.market[0],version=row["version"],effective_date=row["effective_date"]),ensure_ascii=False))
        return 0
    if not a.bootstrap and not a.update and not a.repair:
        from tools.breadth_engine import market_clock, chart_data
        for key in a.market:
            doc=store.read("members",key)
            try:clock=market_clock(BREADTH_MARKETS[key]["market"])
            except Exception:clock={}
            m=store.members(key,clock.get("day"));symbols=m.get("symbols",[])
            frame=store.results(key)
            prices={code:store.prices(code) for code in symbols}
            complete_day=clock.get("complete_day")
            missing=[code for code,rows in prices.items() if rows.empty or (complete_day and rows.iloc[-1].date<complete_day)]
            short=[code for code,rows in prices.items() if 0<len(rows)<50]
            pending=doc.get("pending_membership")
            future=[row for row in doc.get("rows",[]) if (row.get("effective_date") or row["date"])>clock.get("day","9999-12-31")]
            future.sort(key=lambda row:row.get("effective_date") or row["date"])
            waiting=set(pending.get("symbols",[]) if pending else [])
            for row in future:waiting.update(row.get("symbols",[]))
            prewarm=sorted(waiting-set(symbols))
            prewarm_cached=sum(not store.prices(code).empty for code in prewarm)
            events=store.read("membership_events",key).get("rows",[])
            latest=json.loads(frame.tail(1).to_json(orient="records"))
            benchmark={}
            if key in {"nasdaq","nasdaq100"} and latest:
                indicator=store.benchmark(key+"_stockcharts")
                match=indicator.loc[(indicator.date==latest[-1].get("date")) & (indicator.kind=="close")] if not indicator.empty else indicator
                if not match.empty and latest[-1].get("percent") is not None:
                    value=float(match.iloc[-1].percent)
                    benchmark={"symbol":BREADTH_MARKETS[key]["external"],"stockcharts":value,
                               "self_calculated":latest[-1]["percent"],
                               "difference_pp":latest[-1]["percent"]-value}
            chart_breadth={}
            if complete_day:
                try:
                    chart_start=pd.Timestamp(complete_day)-pd.Timedelta(days=220)
                    displayed=chart_data(key,[chart_start,pd.Timestamp(complete_day)],a.cache_root)
                    display=displayed.attrs.get("breadth_display",{})
                    chart_breadth={field:display.get(field) for field in
                                   ("latest_source","source_counts","difference_pp","backcast_start",
                                    "backcast_end","backcast_valid_rows","backcast_latest_coverage")}
                except Exception as error:chart_breadth={"error":str(error)[:160]}
            summary=dict(market=key,members=len(symbols),membership_version=m.get("version"),
                         effective_date=m.get("effective_date",m.get("date")),
                         source=m.get("source"),effective_evidence_url=m.get("effective_evidence_url"),
                         last_verified_at=m.get("last_verified_at"),last_verified_date=doc.get("last_verified_date") or m.get("verified_date") or m.get("date"),
                         last_member_check_date=doc.get("last_member_check_date"),
                         last_member_check_error=doc.get("last_member_check_error"),
                         pending_membership=({"version":pending.get("version"),"count":len(pending.get("symbols",[])),
                                              "discovered_at":pending.get("discovered_at"),"source":pending.get("source"),
                                              "added":sorted(set(pending["symbols"])-set(symbols)),
                                              "removed":sorted(set(symbols)-set(pending["symbols"]))} if pending else None),
                         future_memberships=[{"version":row["version"],"effective_date":row.get("effective_date"),
                                              "source":row.get("source"),"effective_evidence_url":row.get("effective_evidence_url"),
                                              "count":len(row.get("symbols",[]))} for row in future],
                         latest_event=events[-1] if events else None,
                         prewarm={"symbols":prewarm,"cached":prewarm_cached,"missing":len(prewarm)-prewarm_cached},
                         benchmark=benchmark,
                         chart_breadth=chart_breadth,
                         point_in_time_start=doc.get("pit_start"),complete_day=complete_day,
                         cached=sum(not rows.empty for rows in prices.values()),
                         missing_symbols=missing,insufficient_listing_history=short,
                         provisional_prices={code:store.read("prices",code).get("provisional_dates") for code in symbols
                                             if store.read("prices",code).get("provisional_dates")},
                         latest=latest)
            print(json.dumps(summary,ensure_ascii=False))
        return 0
    if a.budget<5:p.error("budget至少5秒")
    if not a.worker:
        # 锁只保存在本机，不随Git同步。父进程持锁，超时仅终止自己的worker。
        local=a.cache_root.parent/"market_breadth_local";local.mkdir(parents=True,exist_ok=True)
        with (local/"refresh.lock").open("a+b") as lock:
            try:
                lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                print("已有广度采集进程，跳过本次刷新。");return 0
            args=list(argv if argv is not None else sys.argv[1:])+["--worker"]
            env=os.environ.copy();env["PYTHONIOENCODING"]="utf-8"
            try:return subprocess.run([sys.executable,__file__,*args],env=env,timeout=a.budget).returncode
            except subprocess.TimeoutExpired:
                print("广度预算已用完，已保存的证券可供下次续跑。",flush=True);return 2
    from tools.breadth_engine import refresh_market,market_clock,price_download_plan
    deadline=time.monotonic()+a.budget-2;reports=[]
    source_health=SourceHealth();shared_quotes={};futu=FutuBreadth()
    report_path=a.report_path
    report_path.parent.mkdir(parents=True,exist_ok=True)
    def save_progress(report):
        # 批次原子保存；父进程硬超时仍能读到最后完成批次及剩余任务。
        data=reports+[report]
        temp=report_path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        os.replace(temp,report_path)
    weights={}
    for key in a.market:
        clock=market_clock(BREADTH_MARKETS[key]['market']);member=store.members(key,clock['day'])
        plan=price_download_plan(store,key,member,clock['day'],clock['complete_day'],a.bootstrap,a.repair,True) if member else {'pending':[]}
        weights[key]=min(100,len(plan['pending']))
        if clock['regular'] or not member:weights[key]=max(1,weights[key])
    try:
        for index,key in enumerate(a.market):
            if time.monotonic()>=deadline:break
            remaining=a.market[index:];left=max(0,deadline-time.monotonic())
            active=[k for k in remaining if weights[k]]
            floor=min(20,left/max(1,len(active)))
            share=(floor+max(0,left-floor*len(active))*weights[key]/max(1,sum(weights[k] for k in active))) if weights[key] else min(5,left)
            market_deadline=min(deadline,time.monotonic()+share)
            try:report=refresh_market(store,key,bootstrap=a.bootstrap,repair=a.repair,deadline=market_deadline,use_futu=not a.no_futu,
                                      source_health=source_health,shared_quotes=shared_quotes,futu=futu,progress_callback=save_progress)
            except Exception as e:report=dict(key=key,status='stale',errors=[str(e)])
            save_progress(report);reports.append(report)
            # 逐证券缺口写诊断文件，终端只给摘要，避免数千只股票刷屏。
            concise={k:v for k,v in report.items() if k not in
                     {"missing_symbols","remaining_missing_symbols","updated_symbols","insufficient_listing_history","snapshot","pending_membership","errors"}}
            concise.update(missing_count=len(report.get("missing_symbols",[])),
                           insufficient_count=len(report.get("insufficient_listing_history",[])),
                           error_count=len(report.get("errors",[])),errors=report.get("errors",[])[:5],
                           diagnostics=str(report_path))
            print(json.dumps(concise,ensure_ascii=False),flush=True)
    finally:futu.close()
    return 0 if len(reports)==len(a.market) and all(r.get('status')=='complete' for r in reports) else 1

if __name__=="__main__":raise SystemExit(main())
