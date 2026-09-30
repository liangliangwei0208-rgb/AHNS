"""股债利差图的数据缓存与按缺口补齐；不参与基金估算。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import numpy as np
import pandas as pd
import requests

from tools.configs.market_calendar_configs import MARKET_CALENDAR_NAMES, MARKET_CLOSE_BUFFER_HOURS


BEIJING = ZoneInfo("Asia/Shanghai")
STATE_FILE = "gu_zhai_xi_refresh_state.json"
INDEX_FILE = "sz399001_daily.csv"
PE_FILE = "gu_zhai_xi_pe.csv"
BOND_FILE = "gu_zhai_xi_cn10y.csv"

# 三条指数使用各自的日线缓存；仅共用股债利差的状态判定，不混用点位。
INDEX_SPECS = {
    "shenzhen": {"file": INDEX_FILE, "column": "index_close", "futu": "SZ.399001", "quote": "sz399001", "secid": "0.399001", "state": "index"},
    "csi2000": {"file": "gu_zhai_xi_csi2000_daily.csv", "column": "csi2000_close", "futu": "SH.932000", "quote": "sh932000", "secid": "1.932000", "state": "csi2000_index"},
    "shanghai": {"file": "gu_zhai_xi_shanghai_daily.csv", "column": "shanghai_close", "futu": "SH.000001", "quote": "sh000001", "secid": "1.000001", "state": "shanghai_index"},
}


def required_trade_dates(years: int, window: int, now: datetime | None = None) -> pd.DatetimeIndex:
    """只把收盘缓冲期已过的上交所交易日作为正式日线目标。"""
    import pandas_market_calendars as mcal

    current = pd.Timestamp(now or datetime.now(BEIJING))
    if current.tzinfo is None:
        current = current.tz_localize(BEIJING)
    else:
        current = current.tz_convert(BEIJING)
    start = current - pd.DateOffset(years=years) - pd.Timedelta(days=max(2 * window, 400))
    calendar = mcal.get_calendar(MARKET_CALENDAR_NAMES["CN"])
    schedule = calendar.schedule(start_date=start.date(), end_date=current.date())
    complete = schedule.loc[
        schedule["market_close"] + pd.Timedelta(hours=MARKET_CLOSE_BUFFER_HOURS)
        <= current.tz_convert("UTC")
    ]
    if complete.empty:
        raise RuntimeError("无法确定最近一个完整的 A 股交易日。")
    dates = pd.DatetimeIndex(complete.index).tz_localize(None).normalize()
    display_start = dates[-1] - pd.DateOffset(years=years)
    first_visible = int(dates.searchsorted(display_start))
    return dates[max(0, first_visible - window + 1):]


def _atomic_save(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件与目标放在同一目录，替换时不会留下半写入缓存。
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _normalise(frame: pd.DataFrame | None, value_column: str, *, positive: bool = False) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), value_column: pd.Series(dtype="float64")})
    if not {"date", value_column}.issubset(frame.columns):
        raise ValueError(f"数据缺少 date/{value_column}: {frame.columns.tolist()}")
    output = frame[["date", value_column]].copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    output[value_column] = pd.to_numeric(output[value_column], errors="coerce")
    output = output.dropna(subset=["date", value_column])
    output = output.loc[np.isfinite(output[value_column])]
    if positive:
        output = output.loc[output[value_column] > 0]
    return output.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


class MarketDataCache:
    """每次绘图共用一个实例，富途连接和三项缓存均在此管理。"""

    MAX_BATCH_DAYS = 200
    MAX_BATCHES_PER_RUN = 12
    RETRY_AFTER = pd.Timedelta(hours=1)

    def __init__(self, cache_dir: str | Path, required_dates: pd.DatetimeIndex, *, now: datetime | None = None,
                 pe_columns: tuple[str, ...] | list[str] = ("averagePETTM", "middlePETTM")):
        self.cache_dir = Path(cache_dir)
        dates = pd.DatetimeIndex(pd.to_datetime(required_dates)).tz_localize(None).normalize()
        self.dates = dates.drop_duplicates().sort_values()
        if self.dates.empty:
            raise ValueError("required_dates 不能为空")
        self.target = self.dates[-1]
        self.now = pd.Timestamp(now or datetime.now(BEIJING))
        self.now = self.now.tz_localize(BEIJING) if self.now.tzinfo is None else self.now.tz_convert(BEIJING)
        self.state_path = self.cache_dir / STATE_FILE
        try:
            parsed = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state = parsed if isinstance(parsed, dict) else {}
        except (OSError, ValueError):
            self.state = {}
        self._futu_ctx = None
        self._sina_history = {}
        self.pe_columns = tuple(pe_columns)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._futu_ctx is not None:
            self._futu_ctx.close()
            self._futu_ctx = None

    def _cache(self, filename: str, column: str) -> pd.DataFrame:
        path = self.cache_dir / filename
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except FileNotFoundError:
            return _normalise(None, column)
        except (OSError, pd.errors.ParserError) as exc:
            print(f"[{column}] 缓存读取失败: {exc}")
            return _normalise(None, column)
        try:
            return _normalise(frame, column, positive=column != "cn10y")
        except ValueError as exc:
            print(f"[{column}] 缓存结构无效，尝试重新获取: {exc}")
            return _normalise(None, column)

    def _save(self, filename: str, column: str, frame: pd.DataFrame) -> None:
        clean = _normalise(frame, column, positive=column != "cn10y")
        _atomic_save(self.cache_dir / filename, clean.to_csv(index=False, float_format="%.10g"))

    def _save_state(self) -> None:
        _atomic_save(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")

    def _record(self, key: str, status: str) -> None:
        item = self.state.setdefault(key, {})
        item["last_checked_at"] = self.now.isoformat()
        item[f"last_{status}_at"] = self.now.isoformat()
        self._save_state()

    def _on_backoff(self, key: str) -> bool:
        item = self.state.get(key, {})
        if not isinstance(item, dict):
            return False
        for name in ("last_failure_at", "last_no_new_at"):
            try:
                when = pd.Timestamp(item[name])
                if pd.Timedelta(0) <= self.now - when < self.RETRY_AFTER:
                    return True
            except (KeyError, TypeError, ValueError):
                pass
        return False

    def _attempt(self, key: str, fn, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
        if self._on_backoff(key):
            return pd.DataFrame()
        try:
            output = fn() if start is None else fn(start, end)
            if output is None or output.empty:
                self._record(key, "no_new")
                return pd.DataFrame()
            self._record(key, "success")
            return output
        except Exception as exc:
            print(f"[{key}] 请求失败: {exc}")
            self._record(key, "failure")
            return pd.DataFrame()

    def _missing(self, frame: pd.DataFrame) -> list[pd.Timestamp]:
        present = set(pd.DatetimeIndex(frame["date"]))
        return [day for day in self.dates if day not in present]

    def _index_missing(self, frame: pd.DataFrame, *, symbol: str = "shenzhen") -> list[pd.Timestamp]:
        spec = INDEX_SPECS[symbol]
        state_key = spec["state"]
        missing = self._missing(frame)
        if self.target.date() == self.now.date() or self.state.get(f"{state_key}_pending_date") == self.target.date().isoformat():
            # 共享指数 CSV 只有日期和收盘价，盘中写入的当日值不能凭日期认作正式收盘。
            final = self.state.get(f"{state_key}_final") or {}
            today = frame.loc[frame["date"] == self.target, spec["column"]]
            cache_path = self.cache_dir / spec["file"]
            try:
                written_at = pd.Timestamp.fromtimestamp(cache_path.stat().st_mtime, tz=BEIJING)
                ready_at = self.target.tz_localize(BEIJING) + pd.Timedelta(hours=15 + MARKET_CLOSE_BUFFER_HOURS)
                written_after_close = ready_at <= written_at <= self.now
            except OSError:
                written_after_close = False
            pending = self.state.get(f"{state_key}_pending_date") == self.target.date().isoformat()
            verified = (
                len(today) == 1 and (
                    (written_after_close and not pending) or (
                        final.get("date") == self.target.date().isoformat()
                        and float(today.iloc[0]) == final.get("close")
                    )
                )
            )
            if not verified and self.target not in missing:
                missing.append(self.target)
        return sorted(missing)

    def _segments(self, missing: list[pd.Timestamp]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """按交易日连续性分段，先处理最新缺口。"""
        wanted = set(missing)
        groups: list[list[pd.Timestamp]] = []
        for day in self.dates:
            if day in wanted:
                if not groups or len(groups[-1]) >= self.MAX_BATCH_DAYS:
                    groups.append([])
                groups[-1].append(day)
            elif groups and groups[-1]:
                groups.append([])
        return [(group[0], group[-1]) for group in reversed(groups) if group]

    def _status(self, name: str, frame: pd.DataFrame) -> None:
        latest = frame["date"].max().date().isoformat() if not frame.empty else "无"
        gaps = self._missing(frame)
        description = f"{len(gaps)} 个交易日缺口"
        if gaps:
            description += f"（{gaps[0].date()} ~ {gaps[-1].date()}）"
        print(f"[{name}] 最近有效日={latest}，目标日={self.target.date()}，{description}")

    def load_index(self, symbol: str = "shenzhen") -> pd.DataFrame:
        """按指数独立缓存补缺口，富途优先；三个指数复用同一 OpenD 连接。"""
        spec = INDEX_SPECS[symbol]
        filename, column, state_key = spec["file"], spec["column"], spec["state"]
        whole = self._cache(filename, "close")
        if self.target.date() < self.now.date() and any(whole["date"].dt.date == self.now.date()):
            today = self.now.date().isoformat()
            if self.state.get(f"{state_key}_pending_date") != today:
                self.state[f"{state_key}_pending_date"] = today
                self._save_state()
        current = whole.loc[whole["date"] <= self.target].rename(columns={"close": column}).copy()
        if symbol == "shenzhen":
            fetchers = (self._fetch_index_futu, self._fetch_index_tencent, self._fetch_index_eastmoney, self._fetch_index_sina)
        else:
            fetchers = tuple((lambda start, end, fn=fn: fn(start, end, symbol=symbol)) for fn in (
                self._fetch_index_futu, self._fetch_index_tencent, self._fetch_index_eastmoney, self._fetch_index_sina,
            ))
        sources = (
            (f"{state_key}_futu", fetchers[0]),
            (f"{state_key}_tencent", fetchers[1]),
            (f"{state_key}_eastmoney", fetchers[2]),
            (f"{state_key}_sina", fetchers[3]),
        )
        for start, end in self._segments(self._index_missing(current, symbol=symbol))[: self.MAX_BATCHES_PER_RUN]:
            remaining = {day for day in self.dates if start <= day <= end and day in self._index_missing(current, symbol=symbol)}
            for key, fetch in sources:
                if not remaining:
                    break
                if key.endswith("_sina") and symbol in self._sina_history:
                    part = self._sina_history[symbol]
                else:
                    part = self._attempt(key, fetch, min(remaining), max(remaining))
                    if key.endswith("_sina") and not part.empty:
                        self._sina_history[symbol] = part
                if part.empty:
                    continue
                try:
                    part = _normalise(part, "index_close", positive=True)
                    part = part.loc[part["date"].isin(remaining)]
                except (ValueError, KeyError) as exc:
                    print(f"[{key}] 数据不合格: {exc}")
                    self._record(key, "failure")
                    continue
                if part.empty:
                    self._record(key, "no_new")
                    continue
                fresh = part.rename(columns={"index_close": "close"})
                whole = _normalise(pd.concat([whole, fresh], ignore_index=True), "close", positive=True)
                self._save(filename, "close", whole)
                current = whole.loc[whole["date"] <= self.target].rename(columns={"close": column}).copy()
                if self.target in set(part["date"]) and (
                    self.target.date() == self.now.date()
                    or self.state.get(f"{state_key}_pending_date") == self.target.date().isoformat()
                ):
                    self.state[f"{state_key}_final"] = {
                        "date": self.target.date().isoformat(),
                        "close": float(part.loc[part["date"] == self.target, "index_close"].iloc[-1]),
                        "verified_at": self.now.isoformat(),
                    }
                    self.state.pop(f"{state_key}_pending_date", None)
                    self._save_state()
                remaining -= set(part["date"])
        if self.target in self._index_missing(current, symbol=symbol):
            # 已知来源均未核实当日收盘值时，旧缓存仍留在磁盘供后续重试，但不进入正式图。
            current = current.loc[current["date"] != self.target].copy()
        self._status(symbol.upper(), current)
        return current.reset_index(drop=True)

    def load_pe(self) -> pd.DataFrame:
        whole = self._cache(PE_FILE, "pe_ttm")
        current = whole.loc[whole["date"] <= self.target].copy()
        if self._missing(current):
            raw = self._attempt("pe_legu", self._fetch_pe_full)
            if not raw.empty:
                selected = self.state.get("pe_field")
                if selected is None:
                    selected = next((column for column in self.pe_columns if column in raw.columns), None)
                if selected in raw.columns and "date" in raw.columns:
                    fresh = _normalise(raw.rename(columns={selected: "pe_ttm"}), "pe_ttm", positive=True)
                    fresh = fresh.loc[fresh["date"] <= self.target]
                    if not fresh.empty:
                        whole = _normalise(pd.concat([whole, fresh], ignore_index=True), "pe_ttm", positive=True)
                        self._save(PE_FILE, "pe_ttm", whole)
                        self.state["pe_field"] = selected
                        self._save_state()
                        current = whole.loc[whole["date"] <= self.target].copy()
                        print(f"[PE] 使用字段: {selected}")
                else:
                    print(f"[PE] 全量响应缺少既有口径字段 {selected}，保留旧缓存。")
            if self._missing(current) and not raw.empty:
                # PE 源只有整段历史响应；本轮未给齐日期时，避免短时间反复下载。
                self._record("pe_legu", "no_new")
        self._status("PE", current)
        return current.reset_index(drop=True)

    def load_bond(self) -> pd.DataFrame:
        whole = self._cache(BOND_FILE, "cn10y")
        current = whole.loc[whole["date"] <= self.target].copy()
        for start, end in self._segments(self._missing(current))[: self.MAX_BATCHES_PER_RUN]:
            part = self._attempt("bond_eastmoney", self._fetch_bond_range, start, end)
            if part.empty:
                continue
            try:
                fresh = _normalise(part, "cn10y")
                fresh = fresh.loc[(fresh["date"] >= start) & (fresh["date"] <= end)]
            except (ValueError, KeyError) as exc:
                print(f"[BOND] 数据不合格: {exc}")
                self._record("bond_eastmoney", "failure")
                continue
            if fresh.empty:
                self._record("bond_eastmoney", "no_new")
                continue
            whole = _normalise(pd.concat([whole, fresh], ignore_index=True), "cn10y")
            self._save(BOND_FILE, "cn10y", whole)
            current = whole.loc[whole["date"] <= self.target].copy()
        self._status("BOND", current)
        return current.reset_index(drop=True)

    def load_all(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        return self.load_index(), self.load_pe(), self.load_bond()

    def _fetch_index_futu(self, start: pd.Timestamp, end: pd.Timestamp, *, symbol: str = "shenzhen") -> pd.DataFrame:
        import futu

        if self._futu_ctx is None:
            host = os.environ.get("AHNS_BREADTH_FUTU_HOST", "127.0.0.1")
            port = int(os.environ.get("AHNS_BREADTH_FUTU_PORT", "11111"))
            self._futu_ctx = futu.OpenQuoteContext(host=host, port=port, is_async_connect=True)
            self._futu_ctx.set_sync_query_connect_timeout(3)
        all_pages = []
        page = None
        for _ in range(4):
            ret, data, page = self._futu_ctx.request_history_kline(
                INDEX_SPECS[symbol]["futu"], start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                ktype=futu.KLType.K_DAY, autype=futu.AuType.NONE,
                fields=[futu.KL_FIELD.DATE_TIME, futu.KL_FIELD.CLOSE],
                max_count=self.MAX_BATCH_DAYS, page_req_key=page,
            )
            if ret != futu.RET_OK:
                raise RuntimeError(str(data))
            all_pages.append(data[["time_key", "close"]].rename(columns={"time_key": "date", "close": "index_close"}))
            if page is None:
                break
        if page is not None:
            raise RuntimeError("富途分页未取完，拒绝截断结果")
        return pd.concat(all_pages, ignore_index=True) if all_pages else pd.DataFrame()

    def _fetch_index_tencent(self, start: pd.Timestamp, end: pd.Timestamp, *, symbol: str = "shenzhen") -> pd.DataFrame:
        quote = INDEX_SPECS[symbol]["quote"]
        count = min(320, max(2, len(self.dates[(self.dates >= start) & (self.dates <= end)]) + 10))
        response = requests.get(
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            params={"_var": "kline_dayqfq", "param": f"{quote},day,,{end:%Y-%m-%d},{count},qfq"}, timeout=8,
        )
        response.raise_for_status()
        text = response.text
        data = json.loads(text[text.index("{"):].rstrip("; \n"))["data"][quote]
        rows = data.get("day") or data.get("qfqday") or []
        return pd.DataFrame({"date": [row[0] for row in rows], "index_close": [row[2] for row in rows]})

    def _fetch_index_eastmoney(self, start: pd.Timestamp, end: pd.Timestamp, *, symbol: str = "shenzhen") -> pd.DataFrame:
        response = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={"secid": INDEX_SPECS[symbol]["secid"], "fields1": "f1,f2,f3,f4,f5", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "klt": "101", "fqt": "0", "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")},
            timeout=8,
        )
        response.raise_for_status()
        rows = (response.json().get("data") or {}).get("klines") or []
        return pd.DataFrame({"date": [row.split(",")[0] for row in rows], "index_close": [row.split(",")[2] for row in rows]})

    def _fetch_index_sina(self, start: pd.Timestamp, end: pd.Timestamp, *, symbol: str = "shenzhen") -> pd.DataFrame:
        """新浪只公开完整压缩日线，最后回退时下载一次并仅合并缺口。"""
        from akshare.index.cons import zh_sina_index_stock_hist_url
        from akshare.stock.cons import hk_js_decode
        import py_mini_racer

        response = requests.get(zh_sina_index_stock_hist_url.format(INDEX_SPECS[symbol]["quote"]), params={"d": "2020_2_4"}, timeout=8)
        response.raise_for_status()
        encoded = response.text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
        decoder = py_mini_racer.MiniRacer()
        decoder.eval(hk_js_decode)
        rows = decoder.call("d", encoded)
        frame = pd.DataFrame(rows)
        if not {"date", "close"}.issubset(frame.columns):
            raise ValueError("新浪日线字段变化")
        frame = frame.rename(columns={"close": "index_close"})
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        return frame[["date", "index_close"]]

    def _fetch_pe_full(self) -> pd.DataFrame:
        return ak.stock_a_ttm_lyr()

    def _fetch_bond_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        rows = []
        page = 1
        while True:
            response = requests.get(
                "https://datacenter.eastmoney.com/api/data/get",
                params={"type": "RPTA_WEB_TREASURYYIELD", "sty": "SOLAR_DATE,EMM00166466", "st": "SOLAR_DATE", "sr": "1", "p": page, "ps": "500", "filter": f"(SOLAR_DATE>='{start:%Y-%m-%d}')(SOLAR_DATE<='{end:%Y-%m-%d}')"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                raise RuntimeError("东方财富国债响应未成功")
            result = payload.get("result") or {}
            rows.extend(result.get("data") or [])
            if page >= int(result.get("pages") or 0):
                break
            page += 1
            if page > 4:
                raise RuntimeError("国债分页超出单批上限")
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame()
        if not {"SOLAR_DATE", "EMM00166466"}.issubset(frame.columns):
            raise ValueError("国债响应字段变化")
        return frame.rename(columns={"SOLAR_DATE": "date", "EMM00166466": "cn10y"})[["date", "cn10y"]]

    def live_spread_if_verified(self, *, index_quote=None, pe_quote=None, bond_quote=None):
        """只有三项同口径盘中值齐备时，才给出不写正式缓存的临时计算。"""
        if not all(isinstance(q, dict) for q in (index_quote, pe_quote, bond_quote)):
            return None
        if (index_quote.get("basis") != "sz399001" or pe_quote.get("basis") != "all_a_equal_weight_pe_ttm" or bond_quote.get("basis") != "cn10y_yield_pct"):
            return None
        session_time = self.now.time()
        from datetime import time
        if not (time(9, 30) <= session_time <= time(11, 30) or time(13) <= session_time <= time(15)):
            return None
        for quote in (index_quote, pe_quote, bond_quote):
            try:
                stamp = pd.Timestamp(quote["observed_at"])
                stamp = stamp.tz_convert(BEIJING)
                value = float(quote["value"])
            except (KeyError, TypeError, ValueError):
                return None
            if stamp.date() != self.now.date() or not np.isfinite(value) or not pd.Timedelta(0) <= self.now - stamp <= pd.Timedelta(minutes=15):
                return None
        if float(index_quote["value"]) <= 0 or float(pe_quote["value"]) <= 0:
            return None
        return {"index_close": float(index_quote["value"]), "pe_ttm": float(pe_quote["value"]), "cn10y": float(bond_quote["value"]), "spread": 100 / float(pe_quote["value"]) - float(bond_quote["value"]), "status": "盘中估算"}


__all__ = ["MarketDataCache", "required_trade_dates"]
