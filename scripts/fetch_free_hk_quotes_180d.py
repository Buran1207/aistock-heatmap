from __future__ import annotations

import argparse
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


def read_csv_smart(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception:
            pass
    raise RuntimeError(f"无法读取：{path}")


def norm_code(code: str) -> str | None:
    if pd.isna(code):
        return None
    s = str(code).strip().upper().replace(" ", "")
    if s.startswith("H"):
        return None  # 临时代码还没有二级行情
    if s.endswith(".HK"):
        base = s[:-3]
    else:
        base = s
    digits = "".join(ch for ch in base if ch.isdigit())
    if not digits:
        return None
    return f"{digits.zfill(4)}.HK"


def yahoo_ticker(code: str) -> str:
    # Yahoo Finance 港股一般使用 4位代码.HK，如 0700.HK、6610.HK。
    return code


def stooq_symbol(code: str) -> str:
    return code.lower()


def fetch_yfinance(code: str, start: str, end: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("未安装 yfinance，请先 pip install yfinance") from exc
    end_plus = (pd.to_datetime(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(yahoo_ticker(code), start=start, end=end_plus, progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    out = pd.DataFrame({
        "code": code,
        "date": pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df.get("Open"), errors="coerce"),
        "high": pd.to_numeric(df.get("High"), errors="coerce"),
        "low": pd.to_numeric(df.get("Low"), errors="coerce"),
        "close": pd.to_numeric(df.get("Close"), errors="coerce"),
        "adj_close": pd.to_numeric(df.get("Adj Close"), errors="coerce") if "Adj Close" in df.columns else pd.NA,
        "volume": pd.to_numeric(df.get("Volume"), errors="coerce"),
        "source": "yfinance",
    })
    return out.dropna(subset=["date", "close"])


def fetch_stooq(code: str, start: str, end: str) -> pd.DataFrame:
    d1 = pd.to_datetime(start).strftime("%Y%m%d")
    d2 = pd.to_datetime(end).strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol(code)}&d1={d1}&d2={d2}&i=d"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    text = r.text.strip()
    if not text or text.lower().startswith("no data"):
        return pd.DataFrame()
    from io import StringIO
    df = pd.read_csv(StringIO(text))
    if df.empty or "Date" not in df.columns:
        return pd.DataFrame()
    out = pd.DataFrame({
        "code": code,
        "date": pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df.get("Open"), errors="coerce"),
        "high": pd.to_numeric(df.get("High"), errors="coerce"),
        "low": pd.to_numeric(df.get("Low"), errors="coerce"),
        "close": pd.to_numeric(df.get("Close"), errors="coerce"),
        "adj_close": pd.NA,
        "volume": pd.to_numeric(df.get("Volume"), errors="coerce"),
        "source": "stooq",
    })
    return out.dropna(subset=["date", "close"])


def wanted_codes(pool: pd.DataFrame, max_names: int | None = None) -> list[tuple[str, str]]:
    if pool.empty or "code" not in pool.columns:
        return []
    rows = []
    for _, row in pool.iterrows():
        code = norm_code(row.get("code"))
        if not code:
            continue
        ld = pd.to_datetime(row.get("listing_date"), errors="coerce")
        if pd.isna(ld):
            continue
        rows.append((code, ld.strftime("%Y-%m-%d")))
    # 去重，保留最早上市日
    tmp = pd.DataFrame(rows, columns=["code", "listing_date"]).dropna()
    if tmp.empty:
        return []
    tmp = tmp.sort_values("listing_date").drop_duplicates("code")
    if max_names:
        tmp = tmp.head(max_names)
    return list(tmp.itertuples(index=False, name=None))


def main() -> None:
    parser = argparse.ArgumentParser(description="免费渠道抓取2024+港股IPO上市后至今的日行情：优先Yahoo/yfinance，失败后Stooq。")
    parser.add_argument("--pool", default="deploy_data/ipo_decision_pool.csv", help="IPO主表CSV")
    parser.add_argument("--out", default="deploy_data/ipo_daily_quotes_180d.csv", help="输出CSV")
    parser.add_argument("--days", type=int, default=9999, help="上市后抓取天数；默认9999表示从上市日至今天，兼容180D和180D+二级交易池")
    parser.add_argument("--sleep", type=float, default=0.8, help="每只股票间隔秒数")
    parser.add_argument("--max", type=int, default=0, help="测试用：最多抓多少只，0为不限")
    args = parser.parse_args()

    pool = read_csv_smart(args.pool)
    codes = wanted_codes(pool, args.max or None)
    all_rows: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    today = pd.Timestamp.today().normalize()

    for i, (code, listing_date) in enumerate(codes, start=1):
        start = pd.to_datetime(listing_date)
        end = min(start + pd.Timedelta(days=args.days), today)
        if end < start:
            continue
        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        print(f"[{i}/{len(codes)}] {code} {start_s} -> {end_s}")
        df = pd.DataFrame()
        err = ""
        for src_name, fn in [("yfinance", fetch_yfinance), ("stooq", fetch_stooq)]:
            try:
                df = fn(code, start_s, end_s)
                if not df.empty:
                    break
            except Exception as exc:
                err += f"{src_name}: {exc}; "
        if df.empty:
            failures.append({"code": code, "listing_date": listing_date, "error": err or "no data"})
        else:
            # 免费源通常没有成交额。这里给一个近似成交额，便于状态机使用，正式版本可用券商/iFind替换。
            df["amount_est_hkd"] = ((df["high"].fillna(df["close"]) + df["low"].fillna(df["close"]) + df["close"]) / 3) * df["volume"]
            all_rows.append(df)
        time.sleep(args.sleep)

    out = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "adj_close", "volume", "amount_est_hkd", "source"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    fail_path = Path(args.out).with_name("ipo_daily_quotes_180d_failures.csv")
    pd.DataFrame(failures).to_csv(fail_path, index=False, encoding="utf-8-sig")
    print(f"Saved {len(out)} quote rows -> {args.out}")
    if failures:
        print(f"Failures: {len(failures)} -> {fail_path}")


if __name__ == "__main__":
    main()
