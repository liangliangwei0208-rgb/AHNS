"""市场广度纯计算与可合并缓存；不在本模块联网。"""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib import patheffects
from tools.configs.market_breadth_configs import (BREADTH_HISTORY_ROWS, BREADTH_BAND_LOW_COLOR, BREADTH_BAND_HIGH_COLOR)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean_prices(frame):
    out=frame[["date","close"]].copy()
    out["date"]=pd.to_datetime(out.date,errors="coerce").dt.strftime("%Y-%m-%d")
    out["close"]=pd.to_numeric(out.close,errors="coerce")
    out=out.dropna().loc[lambda x: np.isfinite(x.close)&(x.close>0)]
    return out.drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)


def price_matrix(prices, members, sessions=None):
    columns={key:clean_prices(prices[key]).set_index("date").close for key in members if key in prices and not prices[key].empty}
    frame=pd.DataFrame(columns).sort_index().reindex(columns=members)
    if sessions is not None:
        frame=frame.reindex([str(x)[:10] for x in sessions])
    return frame


def calculate_history(prices, members, min_coverage=.95, sessions=None):
    members=sorted(set(members))
    frame=price_matrix(prices,members,sessions)
    averages=frame.rolling(50,min_periods=50).mean()
    available=frame.notna()&averages.notna()
    valid=available.sum(axis=1)
    above=((frame>averages)&available).sum(axis=1)
    coverage=valid/max(1,len(members))
    percent=(above/valid.replace(0,np.nan)*100).where(coverage>=min_coverage)
    return pd.DataFrame({"date":frame.index,"percent":percent.values,"valid":valid.values,
                         "total":len(members),"coverage":coverage.values,"kind":"close"})


def calculate_intraday(prices,members,day,quotes,min_coverage=.95,sessions=None):
    # 当天只放一次临时价格；不允许用过期日线向前填充停牌/缺失交易日。
    matrix=price_matrix(prices,sorted(set(members)),sessions)
    prior=matrix.loc[matrix.index<day].tail(49)
    current=pd.Series({k:float(v) for k,v in quotes.items() if v is not None and math.isfinite(float(v)) and float(v)>0}).reindex(matrix.columns)
    valid_mask=(prior.count()==49)&current.notna()
    ma=(prior.sum()+current)/50
    valid=int(valid_mask.sum());total=len(set(members));coverage=valid/max(total,1)
    pct=float(((current>ma)&valid_mask).sum()/valid*100) if valid and coverage>=min_coverage else None
    return dict(date=day,percent=pct,valid=valid,total=total,coverage=coverage,kind="intraday")


def eligible_quote(stamp,now,market,max_age_minutes=60):
    zone="America/New_York" if market=="US" else "Asia/Shanghai"
    try:
        now=pd.Timestamp(now).tz_convert(zone)
        t=pd.Timestamp(stamp)
        t=t.tz_localize(zone) if t.tzinfo is None else t.tz_convert(zone)
        age=(now-t).total_seconds()
        hm=t.hour*60+t.minute
        regular=(570<=hm<=960) if market=="US" else (570<=hm<=690 or 780<=hm<=900)
        return bool(t.date()==now.date() and t.weekday()<5 and -60<=age<=max_age_minutes*60 and regular)
    except (ValueError,TypeError): return False


def _rank(row):
    # 有效收盘优先；同一日期已正式记录的成分版本保持稳定。
    value=row.get("percent")
    valid=isinstance(value,(int,float)) and math.isfinite(value) and 0<=value<=100
    return (valid,row.get("kind")=="close",str(row.get("updated_at","")),json.dumps(row,sort_keys=True))


def merge_document(a,b):
    if not a: return b
    if not b: return a
    if a.get("type")!=b.get("type"): raise ValueError("缓存类型不匹配")
    kind=a["type"]
    newer=max([a,b],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
    if kind=="snapshots":return newer
    if kind=="prices":
        left={r["date"]:r for r in a.get("rows",[])};right={r["date"]:r for r in b.get("rows",[])}
        overlap=set(left)&set(right)
        same=a.get("basis")==b.get("basis") and bool(overlap) and all(math.isclose(left[d]["close"],right[d]["close"],rel_tol=1e-6,abs_tol=1e-8) for d in overlap)
        if not same:return newer
        rows={**left,**right};rows.update({r["date"]:r for r in newer["rows"]})
        return dict(newer,rows=[rows[d] for d in sorted(rows)[-BREADTH_HISTORY_ROWS:]])
    rows={}
    for row in a.get("rows",[])+b.get("rows",[]):
        key=row["date"]
        old=rows.get(key)
        if old is None: rows[key]=row;continue
        if kind=="results":
            if old.get("kind")==row.get("kind")=="intraday":
                # 新快照覆盖率下降时留空，不继续展示旧的有效盘中点。
                rows[key]=max([old,row],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
                continue
            if old.get("kind")==row.get("kind")=="close" and old.get("percent") is not None and row.get("percent") is not None:
                # 成分版本不同：保留最早已发布正式值，保证后续成分调整不会改写过去。
                if old.get("membership_version")!=row.get("membership_version"):
                    rows[key]=min([old,row],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
                    continue
            rows[key]=max([old,row],key=_rank)
        else:
            rows[key]=max([old,row],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
    out=dict(newer,rows=[rows[k] for k in sorted(rows)])
    if kind=="results":out["rows"]=out["rows"][-BREADTH_HISTORY_ROWS:]
    return out


class BreadthStore:
    def __init__(self,root): self.root=Path(root)
    def path(self,kind,key):
        if not re.fullmatch(r"[A-Za-z0-9_.^-]+",key): raise ValueError("非法缓存键")
        return self.root/kind/(key+".json")
    def read(self,kind,key):
        path=self.path(kind,key)
        if not path.exists():return {}
        return json.loads(path.read_text(encoding="utf-8"))
    def write(self,kind,key,doc):
        path=self.path(kind,key);path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix("."+uuid.uuid4().hex+".tmp")
        tmp.write_text(json.dumps(doc,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
        os.replace(tmp,path)
    def prices(self,key):
        return pd.DataFrame(self.read("prices",key).get("rows",[]),columns=["date","close"])
    def save_prices(self,key,frame,basis,updated_at=None):
        frame=clean_prices(frame)
        old=self.read("prices",key)
        if old:
            previous=self.prices(key)
            overlap=previous.merge(frame,on="date",suffixes=("_old","_new"))
            changed=old.get("basis")!=basis or (not overlap.empty and not np.allclose(overlap.close_old,overlap.close_new,rtol=1e-6,atol=1e-8))
            if changed and len(frame)<50:raise ValueError("复权或来源变化，必须重新获取完整窗口")
            if not changed:frame=clean_prices(pd.concat([previous,frame]))
        rows=json.loads(frame.tail(BREADTH_HISTORY_ROWS).to_json(orient="records"))
        self.write("prices",key,dict(type="prices",basis=basis,updated_at=updated_at or utc_now(),rows=rows))
    def save_members(self,key,members,source,day,universe=None):
        symbols=sorted(set(members))
        version=hashlib.sha256((source+"|"+"|".join(symbols)).encode()).hexdigest()[:16]
        row=dict(date=day,symbols=symbols,source=source,universe=universe or key,version=version,updated_at=utc_now())
        old=self.read("members",key)
        self.write("members",key,merge_document(old,dict(type="members",updated_at=utc_now(),rows=[row])))
        return row
    def members(self,key,day=None):
        rows=self.read("members",key).get("rows",[])
        rows=[r for r in rows if day is None or r["date"]<=day]
        return max(rows,key=lambda r:r["date"]) if rows else {}
    def save_results(self,key,rows,membership_version,source="self_calculated"):
        stamped=[]
        for row in rows:
            # 可选字段经DataFrame往返后可能成为NaN，统一转为JSON null。
            row={k: (None if isinstance(v,(float,np.floating)) and not math.isfinite(v) else v) for k,v in row.items()}
            if row.get("percent") is not None and not math.isfinite(float(row["percent"])):row["percent"]=None
            row.update(membership_version=membership_version,source=source,updated_at=utc_now())
            stamped.append(row)
        old=self.read("results",key)
        self.write("results",key,merge_document(old,dict(type="results",updated_at=utc_now(),rows=stamped,calculation_policy="equal_weight_strict_above_inclusive_SMA50; initial_history_uses_current_members; published_dates_keep_membership_version; min_coverage_95pct")))
    def results(self,key):
        return pd.DataFrame(self.read("results",key).get("rows",[]),columns=["date","percent","valid","total","coverage","kind","membership_version","source","updated_at","observed_at"])


def draw_breadth_state_band(ax, price_df, breadth_df, low_threshold, high_threshold):
    """只在同日广度达到极端阈值时，在价格图底部或顶部画提示带。"""
    if (price_df is None or price_df.empty or breadth_df is None or breadth_df.empty
            or "date" not in price_df or not {"date", "percent"}.issubset(breadth_df.columns)):
        return []
    try:
        low, high = float(low_threshold), float(high_threshold)
        if not (0 <= low < high <= 100):
            return []
    except (TypeError, ValueError):
        return []
    dates = pd.to_datetime(price_df["date"], errors="coerce").dt.normalize().dropna().drop_duplicates().sort_values()
    if dates.empty:
        return []
    breadth = breadth_df[["date", "percent"]].copy()
    breadth["date"] = pd.to_datetime(breadth["date"], errors="coerce").dt.normalize()
    breadth["percent"] = pd.to_numeric(breadth["percent"], errors="coerce")
    breadth = breadth.dropna(subset=["date"]).drop_duplicates("date", keep="last")
    aligned = pd.DataFrame({"date": dates}).merge(breadth, on="date", how="left").sort_values("date")
    x = mdates.date2num(aligned["date"].to_numpy())
    if len(x) == 1:
        left, right = x - .5, x + .5
    else:
        middle = (x[:-1] + x[1:]) / 2
        left = np.r_[x[0] - (middle[0]-x[0]), middle]
        right = np.r_[middle, x[-1] + (x[-1]-middle[-1])]

    # 低广度带紧邻底部VIX带；高广度带靠近价格图上沿。
    bands = {"low": (BREADTH_BAND_LOW_COLOR, .091), "high": (BREADTH_BAND_HIGH_COLOR, .958)}
    states = ["low" if pd.notna(v) and v <= low else "high" if pd.notna(v) and v >= high else None
              for v in aligned["percent"]]
    patches = []
    start = 0
    for end in range(1, len(states)+1):
        if end < len(states) and states[end] == states[start]:
            continue
        state = states[start]
        if state is not None:
            color, bottom = bands[state]
            patch = Rectangle((left[start], bottom), right[end-1]-left[start], .026,
                              transform=ax.get_xaxis_transform(), facecolor=color, edgecolor="none",
                              alpha=.72, zorder=2.5, clip_on=True)
            ax.add_artist(patch)
            patches.append(patch)
            # 每段都居中标注；过窄色段缩写为D，避免相邻的50D文字挤成一串。
            center = patch.get_x() + patch.get_width() / 2
            x_transform = ax.get_xaxis_transform()
            pixel_width = x_transform.transform((right[end-1], bottom))[0] - x_transform.transform((left[start], bottom))[0]
            label = "50D" if pixel_width >= 25 else "D"
            ax.text(center, bottom+.013, label, transform=x_transform, ha="center", va="center",
                    fontsize=7 if label == "50D" else 6, color="white", fontweight="bold", zorder=3, clip_on=True,
                    path_effects=[patheffects.withStroke(linewidth=1.2, foreground="#263247")])
        start = end
    return patches


def draw_breadth(ax,frame):
    """只负责叠加；NaN自然断线，不把数据缺口画成0。"""
    if frame is None or frame.empty or frame.percent.notna().sum()==0:
        ax.text(.99,.97,"50D：数据不足",transform=ax.transAxes,ha="right",va="top",fontsize=8,color="#7652a0")
        return
    dates=pd.to_datetime(frame.date)
    ax.plot(dates,frame.percent,color="#8e44ad",linestyle="--",linewidth=1.6,label="50D",zorder=5)
    latest=frame.dropna(subset=["percent"]).iloc[-1]
    if latest.kind!="intraday":
        ax.scatter([pd.Timestamp(latest.date)],[latest.percent],color="#8e44ad",s=15,zorder=6)
    text=f"50D: {latest.percent:.1f}%" if pd.notna(frame.iloc[-1].percent) else "50D：当前数据不足"
    ax.text(.99,.97,text,transform=ax.transAxes,ha="right",va="top",fontsize=8,color="#7652a0")
    live=frame.loc[frame.kind=="intraday"]
    if not live.empty:
        ax.scatter(pd.to_datetime(live.date),live.percent,facecolors="none",edgecolors="#8e44ad",s=34,zorder=6,label="盘中估算")
    ax.legend(loc="upper left",fontsize=8)


def audit_store(root):
    """只读检查分片容量、损坏文件和最新广度覆盖率，不清理缓存。"""
    root=Path(root);report=dict(files=0,bytes=0,warnings=[],markets={})
    if not root.exists():return report
    for path in root.glob("*/*.json"):
        report["files"]+=1;report["bytes"]+=path.stat().st_size
        try:
            data=json.loads(path.read_text(encoding="utf-8"))
            if data.get("type") in {"prices","results"} and len(data.get("rows",[]))>400:
                report["warnings"].append(str(path.relative_to(root))+" 超过400行")
            if data.get("type")=="results" and data.get("rows"):
                latest=data["rows"][-1];report["markets"][path.stem]=latest
                if latest.get("percent") is None:report["warnings"].append(path.stem+" 最新覆盖率不足")
        except (OSError,ValueError,TypeError) as e:report["warnings"].append(path.name+": "+str(e))
    return report
