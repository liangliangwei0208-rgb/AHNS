"""市场广度纯计算与可合并缓存；不在本模块联网。"""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
import uuid
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib import patheffects
from matplotlib.font_manager import FontProperties
from tools.configs.market_breadth_configs import (BREADTH_HISTORY_ROWS, BREADTH_BAND_LOW_COLOR, BREADTH_BAND_HIGH_COLOR)

RIGHT_METRIC_LABEL_X = .90


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


def calculate_segmented_history(prices, store, key, sessions, min_coverage=.95, calendar_sessions=None):
    """按交易日有效名单分段计算；成员入指前的真实价格仍用于均线预热。"""
    pit_start=store.read("members",key).get("pit_start")
    requested = [str(day)[:10] for day in sessions if not pit_start or str(day)[:10]>=pit_start]
    if not requested:
        return pd.DataFrame()
    all_dates = ([str(day)[:10] for day in calendar_sessions] if calendar_sessions is not None else
                 sorted(set(requested).union(
                     str(day)[:10] for frame in prices.values() for day in frame.get("date", []))))
    versions = {}
    for day in requested:
        member = store.members(key, day)
        if member:
            versions[member["version"]] = member
    calculated = {}
    for version, member in versions.items():
        frame = calculate_history(prices, member["symbols"], min_coverage, all_dates)
        calculated[version] = frame.set_index("date")
    rows = []
    for day in requested:
        member = store.members(key, day)
        if not member:
            continue
        row = calculated[member["version"]].loc[day].to_dict()
        rows.append(dict(date=day, **row, membership_version=member["version"],
                         membership_policy="point_in_time_membership"))
    return pd.DataFrame(rows)


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
    if kind=="direct_indicators" and any(a.get(field)!=b.get(field) for field in
                                          ("source_id","universe","metric")):
        # 不同授权源或成分口径的逐日值不可在跨主机同步时拼接。
        return newer
    if kind=="prices":
        left={r["date"]:r for r in a.get("rows",[])};right={r["date"]:r for r in b.get("rows",[])}
        overlap=set(left)&set(right)
        provisional_a=set(a.get("provisional_dates",[]));provisional_b=set(b.get("provisional_dates",[]))
        compatible=(a.get("basis")==b.get("basis") and bool(overlap) and all(
            math.isclose(left[d]["close"],right[d]["close"],rel_tol=1e-6,abs_tol=1e-8)
            or d in provisional_a or d in provisional_b for d in overlap))
        if not compatible:return newer
        rows={};provisional=set()
        for day in sorted(set(left)|set(right)):
            if day in left and day in right:
                # 同日正式日线优先于临时快照，即使临时文件更新时间更晚。
                chosen=b if day in provisional_a and day not in provisional_b else a if day in provisional_b and day not in provisional_a else newer
            else:chosen=a if day in left else b
            rows[day]=(left if chosen is a else right)[day]
            if day in (provisional_a if chosen is a else provisional_b):provisional.add(day)
        kept=sorted(rows)[-BREADTH_HISTORY_ROWS:]
        return dict(newer,rows=[rows[d] for d in kept],provisional_dates=sorted(provisional.intersection(kept)),
                    bootstrap_complete=bool(a.get("bootstrap_complete") or b.get("bootstrap_complete")))
    if kind=="membership_events":
        combined={}
        for row in a.get("rows",[])+b.get("rows",[]):
            ident=row["event_id"]
            combined[ident]=max((row,combined[ident]),key=lambda r:(r.get("updated_at",""),json.dumps(r,sort_keys=True))) if ident in combined else row
        return dict(newer,rows=[combined[k] for k in sorted(combined)])
    if kind=="members":
        # 同一版本跨主机只合并验证时间，不产生重复的每日成分记录。
        by_version={}
        for row in a.get("rows",[])+b.get("rows",[]):
            version=row["version"]
            prior=by_version.get(version)
            if prior is None:
                by_version[version]=row
            else:
                older=min((prior,row),key=lambda r:(r.get("effective_date") or r["date"],r["date"]))
                latest=max((prior,row),key=lambda r:r.get("last_verified_at",r.get("updated_at","")))
                by_version[version]=dict(older,last_verified_at=latest.get("last_verified_at",latest.get("updated_at")),
                                          verified_date=latest.get("verified_date",latest["date"]))
        pending=max([d.get("pending_membership") for d in (a,b) if d.get("pending_membership")],
                    key=lambda r:r.get("last_verified_at",""),default=None)
        out=dict(newer,rows=sorted(by_version.values(),key=lambda r:(r.get("effective_date") or r["date"],r["version"])))
        if pending and any(r.get("symbols")==pending.get("symbols") for r in out["rows"]):pending=None
        starts=[d.get("pit_start") for d in (a,b) if d.get("pit_start")]
        if starts:out["pit_start"]=min(starts)
        if pending:out["pending_membership"]=pending
        else:out.pop("pending_membership",None)
        return out
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
            if old.get("kind")==row.get("kind")=="close" and old.get("finality")!=row.get("finality"):
                # 官方日线核验结果覆盖快照临时收盘值，包括覆盖率不足时的空值。
                rows[key]=old if old.get("finality")!="snapshot_provisional" else row
                continue
            if {old.get("kind"),row.get("kind")}=={"close","intraday"}:
                rows[key]=old if old.get("kind")=="close" else row
                continue
            if old.get("kind")==row.get("kind")=="close" and old.get("percent") is not None and row.get("percent") is not None:
                # 已发布正式值不可由未来名单或重复运行改写；快照可被正式日线核对替换。
                if old.get("finality") != "snapshot_provisional" and row.get("finality") != "snapshot_provisional":
                    previous_revision=int(old.get("repair_revision") or 0)
                    incoming_revision=int(row.get("repair_revision") or 0)
                    if previous_revision!=incoming_revision:
                        rows[key]=old if previous_revision>incoming_revision else row
                    elif (old.get("percent")==row.get("percent") and
                          bool(old.get("membership_policy"))!=bool(row.get("membership_policy"))):
                        rows[key]=old if old.get("membership_policy") else row
                    else:
                        rows[key]=min([old,row],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
                    continue
            rows[key]=max([old,row],key=_rank)
        else:
            rows[key]=max([old,row],key=lambda x:(x.get("updated_at",""),json.dumps(x,sort_keys=True)))
    out=dict(newer,rows=[rows[k] for k in sorted(rows)])
    if kind in {"results","direct_indicators"}:out["rows"]=out["rows"][-BREADTH_HISTORY_ROWS:]
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
    def save_prices(self,key,frame,basis,updated_at=None,provisional=False):
        frame=clean_prices(frame)
        incoming_dates=set(frame.date)
        old=self.read("prices",key)
        provisional_dates=set(old.get("provisional_dates",[]))
        if old:
            previous=self.prices(key)
            overlap=previous.merge(frame,on="date",suffixes=("_old","_new"))
            official_overlap=overlap.loc[~overlap.date.isin(provisional_dates)] if not provisional else overlap.iloc[0:0]
            changed=old.get("basis")!=basis or (not official_overlap.empty and not np.allclose(
                official_overlap.close_old,official_overlap.close_new,rtol=1e-6,atol=1e-8))
            if changed and len(frame)<50:raise ValueError("复权或来源变化，必须重新获取完整窗口")
            if not changed and not previous.empty:frame=clean_prices(pd.concat([previous,frame]))
            else:provisional_dates.clear()
        if provisional:provisional_dates.update(incoming_dates)
        else:provisional_dates.difference_update(incoming_dates)
        rows=json.loads(frame.tail(BREADTH_HISTORY_ROWS).to_json(orient="records"))
        self.write("prices",key,dict(type="prices",basis=basis,updated_at=updated_at or utc_now(),rows=rows,
                                      provisional_dates=sorted(provisional_dates.intersection(r["date"] for r in rows)),
                                      bootstrap_complete=old.get("bootstrap_complete",False)))
    def establish_pit_start(self,key,day):
        doc=self.read("members",key)
        if doc and not doc.get("pit_start"):
            self.write("members",key,dict(doc,pit_start=day,updated_at=utc_now()))
        # 旧缓存尚无逐日名单证据，迁移标签即可，绝不重算或改动原数值。
        results=self.read("results",key)
        if results and any("membership_policy" not in row for row in results.get("rows",[])):
            rows=[dict(row,membership_policy=row.get("membership_policy","current_members_backcast"))
                  for row in results.get("rows",[])]
            self.write("results",key,dict(results,rows=rows,updated_at=utc_now()))
    def activate_pending(self,key,effective_date,evidence_url):
        """仅凭可审计的官方公告地址人工确认无法自动解析的调样生效日。"""
        allowed={"dow":("spglobal.com",),"nasdaq":("nasdaq.com","nasdaqtrader.com"),
                 "nasdaq100":("nasdaq.com",),
                 "dividend":("csindex.com.cn",),"csi2000":("csindex.com.cn",),
                 "shenzhen":("cnindex.com.cn","szse.cn")}
        host=(urlparse(evidence_url).hostname or "").lower()
        if urlparse(evidence_url).scheme!="https" or not any(host==d or host.endswith("."+d) for d in allowed[key]):
            raise ValueError("请提供相应指数公司的 HTTPS 官方公告地址")
        date=pd.Timestamp(effective_date).strftime("%Y-%m-%d")
        doc=self.read("members",key)
        pending=doc.get("pending_membership")
        if not pending:raise ValueError("没有待确认的成分名单")
        current=self.members(key,date)
        if current and (current.get("effective_date") or current["date"])>=date:
            raise ValueError("生效日期必须晚于当前名单的生效日期")
        row=self.save_members(key,pending["symbols"],pending["source"],pending["date"],
                              pending.get("universe"),effective_date=date,evidence_url=evidence_url)
        doc=self.read("members",key);doc.pop("pending_membership",None)
        doc["effective_evidence_url"]=evidence_url
        self.write("members",key,doc)
        return row
    def save_members(self,key,members,source,day,universe=None,effective_date="auto",evidence_url=None):
        # 源适配器通常已校验，但手动调用和模拟响应也不得绕过名单完整性底线。
        if not members:raise ValueError("成分名单为空")
        if len(members)!=len(set(members)):raise ValueError("成分名单含重复证券代码")
        symbols=sorted(members)
        version=hashlib.sha256(((universe or key)+"|"+"|".join(symbols)).encode()).hexdigest()[:16]
        now=utc_now()
        if effective_date=="auto":effective_date=day
        row=dict(date=day,symbols=symbols,source=source,universe=universe or key,version=version,
                 discovered_at=now,last_verified_at=now,verified_date=day,effective_date=effective_date,updated_at=now)
        if evidence_url:row["effective_evidence_url"]=evidence_url
        old=self.read("members",key)
        # 预告多次未来调样时，增删事件必须相对上一生效版本，而非发现当日版本。
        before=(pd.Timestamp(effective_date)-pd.Timedelta(days=1)).strftime("%Y-%m-%d") if effective_date else day
        previous=self.members(key,before)
        existing=next((r for r in old.get("rows",[]) if r["symbols"]==symbols),None)
        if existing:
            existing=dict(existing,last_verified_at=now,verified_date=day,updated_at=now,source=source)
            if evidence_url:existing["effective_evidence_url"]=evidence_url
            doc=dict(old,rows=[existing if r["symbols"]==symbols else r for r in old["rows"]],updated_at=now)
            if doc.get("pending_membership") and doc["pending_membership"].get("symbols")==symbols:
                doc.pop("pending_membership",None)
            self.write("members",key,doc)
            return existing
        if effective_date is None:
            # 官方文件先披露未来名单但无可验证生效日时，保留现行版本。
            pending=old.get("pending_membership")
            if pending and pending.get("symbols")==symbols:row["discovered_at"]=pending.get("discovered_at",now)
            self.write("members",key,dict(old,type="members",updated_at=now,pending_membership=row,
                                          rows=old.get("rows",[]),pit_start=old.get("pit_start",day)))
            return row
        self.write("members",key,merge_document(old,dict(type="members",updated_at=now,rows=[row],
                                                      pit_start=old.get("pit_start") or effective_date)))
        if previous and previous["version"]!=version:
            event=dict(event_id=f"{effective_date}:{version}",date=effective_date,old_version=previous["version"],
                       new_version=version,added=sorted(set(symbols)-set(previous["symbols"])),
                       removed=sorted(set(previous["symbols"])-set(symbols)),source=source,updated_at=now)
            if evidence_url:event["effective_evidence_url"]=evidence_url
            prior=self.read("membership_events",key)
            self.write("membership_events",key,merge_document(prior,dict(type="membership_events",rows=[event],updated_at=now)))
        return row
    def members(self,key,day=None):
        rows=self.read("members",key).get("rows",[])
        day=day or str(pd.Timestamp.now(tz="UTC").date())
        rows=[r for r in rows if (r.get("effective_date") or r["date"])<=day]
        return max(rows,key=lambda r:(r.get("effective_date") or r["date"],r["date"],r.get("updated_at",""))) if rows else {}
    def save_results(self,key,rows,membership_version,source="self_calculated",finality="official",repair=False):
        old=self.read("results",key)
        previous={r.get("date"):r for r in old.get("rows",[]) if r.get("kind")=="close"}
        stamped=[]
        for row in rows:
            # 可选字段经DataFrame往返后可能成为NaN，统一转为JSON null。
            row={k: (None if isinstance(v,(float,np.floating)) and not math.isfinite(v) else v) for k,v in row.items()}
            if row.get("percent") is not None and not math.isfinite(float(row["percent"])):row["percent"]=None
            row.update(membership_version=row.get("membership_version",membership_version),source=source,
                       finality="intraday_snapshot" if row.get("kind")=="intraday" else finality,
                       updated_at=utc_now())
            row.setdefault("membership_policy","point_in_time_membership")
            prior=previous.get(row.get("date")) if row.get("kind")=="close" else None
            if repair and prior and all(prior.get(field)==row.get(field) for field in
                    ("kind","percent","valid","total","coverage","membership_version","membership_policy","source","finality")):
                continue
            if repair and prior and finality=="official" and row.get("kind")=="close" and row.get("percent") is not None:
                row["repair_revision"]=int(prior.get("repair_revision") or 0)+1
            stamped.append(row)
        if not stamped:return
        if old:
            old=dict(old,rows=[dict(r,membership_policy=r.get("membership_policy","current_members_backcast"))
                               for r in old.get("rows",[])])
            if repair and finality=="official":
                corrected={r["date"] for r in stamped if r.get("kind")=="close" and r.get("percent") is not None}
                old["rows"]=[r for r in old["rows"] if not (r.get("date") in corrected and r.get("kind")=="close")]
        policy="equal_weight_strict_above_inclusive_SMA50; point_in_time_from_activation; published_close_immutable; min_coverage_95pct"
        self.write("results",key,merge_document(old,dict(type="results",updated_at=utc_now(),rows=stamped,calculation_policy=policy)))
    def results(self,key):
        return pd.DataFrame(self.read("results",key).get("rows",[]),columns=["date","percent","valid","total","coverage","kind","membership_version","membership_policy","source","finality","repair_revision","updated_at","observed_at"])

    def save_benchmark(self,key,rows,source):
        now=utc_now()
        stamped=[dict({field:(None if isinstance(value,(float,np.floating)) and not math.isfinite(value)
                              else value) for field,value in row.items()},source=source,updated_at=now)
                 for row in rows]
        prior=self.read("benchmarks",key)
        doc=merge_document(prior,dict(type="benchmarks",updated_at=now,rows=stamped))
        doc["rows"]=doc.get("rows",[])[-BREADTH_HISTORY_ROWS:]
        self.write("benchmarks",key,doc)

    def benchmark(self,key):
        return pd.DataFrame(self.read("benchmarks",key).get("rows",[]))


def price_band_layout(ax, output_dpi=None, *, include_ebs=False, include_ene=False):
    """按导出像素统一排布；默认两层位置保持不变，可追加 EBS/ENE。"""
    dpi = float(output_dpi or ax.figure.dpi)
    axes_height_px = ax.get_position().height * ax.figure.get_size_inches()[1] * dpi
    unit = 1.0 / max(axes_height_px, 1.0)
    height, gap, edge = 16 * unit, 2 * unit, 5 * unit
    layout = {
        "height": height,
        "vix_negative": edge,
        "breadth_low": edge + height + gap,
        "breadth_high": 1 - edge - height,
        "vix_positive": 1 - edge - 2 * height - gap,
    }
    if include_ebs:
        layout.update(ebs_high=edge + 2*(height+gap), ebs_low=1-edge-height,
                      breadth_high=1-edge-2*height-gap, vix_positive=1-edge-3*height-2*gap)
    if include_ene:
        # 顶部由外向内 ENE/EBS/50D/V；底部由外向内 V/50D/EBS/ENE。
        bottom = ["vix_negative", "breadth_low"] + (["ebs_high"] if include_ebs else []) + ["ene_low"]
        top = ["ene_high"] + (["ebs_low"] if include_ebs else []) + ["breadth_high", "vix_positive"]
        layout.update({key: edge + i*(height+gap) for i, key in enumerate(bottom)})
        layout.update({key: 1-edge-height-i*(height+gap) for i, key in enumerate(top)})
    return layout


def add_state_band_label(ax, patch, label, *, output_dpi=None, fontsize=6, force=False):
    """默认仅宽段标字；force 可左伸出短段，但文字仍限制在绘图区内。"""
    fig = ax.figure
    dpi = float(output_dpi or fig.dpi)
    transform = ax.get_xaxis_transform()
    y_center = patch.get_y() + patch.get_height() / 2
    x_left = transform.transform((patch.get_x(), y_center))[0]
    x_right = transform.transform((patch.get_x() + patch.get_width(), y_center))[0]
    visible_left = max(x_left, ax.bbox.x0)
    visible_right = min(x_right, ax.bbox.x1)
    inset = 2 * fig.dpi / dpi
    renderer = fig.canvas.get_renderer()
    font = FontProperties(size=fontsize, weight="bold")
    label_width = renderer.get_text_width_height_descent(label, font, ismath=False)[0]
    if visible_right <= visible_left or ax.bbox.width < label_width + 2*inset:
        return None
    if not force and visible_right - visible_left < label_width + 2 * inset:
        return None
    y_px = transform.transform((patch.get_x(), y_center))[1]
    right_anchor = max(visible_right-inset, ax.bbox.x0+label_width+inset) if force else visible_right-inset
    label_x = transform.inverted().transform((right_anchor, y_px))[0]
    return ax.text(label_x, y_center, label, transform=transform, ha="right", va="center",
                   fontsize=fontsize, color="white", fontweight="bold", zorder=3, clip_on=True,
                   path_effects=[patheffects.withStroke(linewidth=1.2, foreground="#263247")])


def draw_breadth_state_band(ax, price_df, breadth_df, low_threshold, high_threshold, *, output_dpi=None, include_ebs=False, include_ene=False):
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

    # 上下极端各占一侧，VIX在同侧紧邻；位置由导出尺寸换算。
    layout = price_band_layout(ax, output_dpi, include_ebs=include_ebs, include_ene=include_ene)
    bands = {"low": (BREADTH_BAND_LOW_COLOR, layout["breadth_low"]),
             "high": (BREADTH_BAND_HIGH_COLOR, layout["breadth_high"])}
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
            patch = Rectangle((left[start], bottom), right[end-1]-left[start], layout["height"],
                              transform=ax.get_xaxis_transform(), facecolor=color, edgecolor="none",
                              alpha=.88, zorder=2.5, clip_on=True)
            ax.add_artist(patch)
            patches.append(patch)
            add_state_band_label(ax, patch, "50D", output_dpi=output_dpi)
        start = end
    return patches


def draw_breadth(ax,frame):
    """只负责叠加；NaN自然断线，不把数据缺口画成0。"""
    metric_label="50D估" if frame is not None and frame.attrs.get("breadth_display",{}).get("approximate") else "50D"
    if frame is None or frame.empty or frame.percent.notna().sum()==0:
        ax.text(RIGHT_METRIC_LABEL_X,.97,f"{metric_label}：数据不足",transform=ax.transAxes,ha="left",va="top",fontsize=8,color="#7652a0")
        return
    dates=pd.to_datetime(frame.date)
    ax.plot(dates,frame.percent,color="#8e44ad",linestyle="--",linewidth=1.6,label="50D",zorder=5)
    latest=frame.dropna(subset=["percent"]).iloc[-1]
    if latest.kind!="intraday":
        ax.scatter([pd.Timestamp(latest.date)],[latest.percent],color="#8e44ad",s=15,zorder=6)
    display=frame.attrs.get("breadth_display",{})
    if not display or display.get("current"):
        # 最新盘中点失效时，仍可展示最近已完成交易日的正式收盘值。
        text=f"{metric_label}: {latest.percent:.1f}%"
    elif display.get("age_sessions",float("inf"))<=display.get("max_age_sessions",5) and latest.kind=="close":
        # 旧收盘值只在右侧注明日期，不补画到今天，也不复用过期盘中值。
        text=f"{metric_label}: {latest.percent:.1f}% · {pd.Timestamp(latest.date):%m-%d}收"
    else:
        text=f"{metric_label}：数据不足"
    ax.text(RIGHT_METRIC_LABEL_X,.97,text,transform=ax.transAxes,ha="left",va="top",fontsize=8,color="#7652a0")
    if display.get("backcast_end") and display.get("backcast_basis_date"):
        # 回算只用于补足旧图曲线；明确标注采用首次核验名单，避免误认为逐日正式成分。
        ax.text(.02,.02,f"历史50D按{display['backcast_basis_date']}成分回算",
                transform=ax.transAxes,ha="left",va="bottom",fontsize=6.5,color="#7652a0")
    live=frame.loc[(frame.kind=="intraday") & frame.percent.notna()]
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
