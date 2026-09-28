"""50DMA独立建库/增量/诊断入口。默认只读状态；不发邮件。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from tools.market_breadth import BreadthStore
from tools.breadth_sources import FutuBreadth, SourceHealth
from tools.configs.market_breadth_configs import BREADTH_MARKETS


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    actions=p.add_mutually_exclusive_group()
    actions.add_argument("--bootstrap",action="store_true",help="首次完整建库，可中断后重跑")
    actions.add_argument("--update",action="store_true",help="只更新已有证券及盘中结果")
    actions.add_argument("--status",action="store_true",help="只读覆盖率与状态")
    p.add_argument("--market",nargs="+",choices=list(BREADTH_MARKETS),default=list(BREADTH_MARKETS))
    p.add_argument("--cache-root",type=Path,default=Path(__file__).resolve().parent/"cache"/"market_breadth")
    p.add_argument("--budget",type=int,default=180)
    p.add_argument("--no-futu",action="store_true")
    p.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    store=BreadthStore(a.cache_root)
    if not a.bootstrap and not a.update:
        for key in a.market:
            m=store.members(key);symbols=m.get("symbols",[])
            summary=dict(market=key,members=len(symbols),cached=sum(not store.prices(c).empty for c in symbols),latest=json.loads(store.results("nasdaq_stockcharts" if key=="nasdaq" else key).tail(1).to_json(orient="records")))
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
    from tools.breadth_engine import refresh_market
    deadline=time.monotonic()+a.budget;reports=[]
    source_health=SourceHealth();shared_quotes={};futu=FutuBreadth()
    report_path=Path(__file__).resolve().parent/"output"/"market_breadth_diagnostics.json"
    report_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        for key in a.market:
            if time.monotonic()>=deadline:break
            try:report=refresh_market(store,key,bootstrap=a.bootstrap,deadline=deadline,use_futu=not a.no_futu,
                                      source_health=source_health,shared_quotes=shared_quotes,futu=futu)
            except Exception as e:report=dict(key=key,errors=[str(e)])
            reports.append(report);report_path.write_text(json.dumps(reports,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:futu.close()
    return 0 if len(reports)==len(a.market) and all(r.get("latest",{}).get("percent") is not None for r in reports) else 1

if __name__=="__main__":raise SystemExit(main())
