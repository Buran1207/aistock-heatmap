from __future__ import annotations

"""Build post-listing path labels from the latest quote source.

v11 fixes the old data-chain bug: the script no longer defaults to the legacy
Yahoo/Stooq file. Quote priority is:
1) deploy_data/ifind_daily_quotes_raw.csv
2) data/raw_ifind/daily_quotes.csv
3) deploy_data/ipo_daily_quotes_180d.csv, as fallback only
Then iFind snapshot is appended as a same-day temporary bar if available.
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy_data"
RAW = ROOT / "data" / "raw_ifind"
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950")


def read_csv_smart(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception:
            pass
    try:
        return pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame()


def write_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def norm_code(x):
    if pd.isna(x):
        return None
    s = str(x).strip().upper().replace(" ", "")
    if not s or s.startswith("H"):
        return None
    base = s[:-3] if s.endswith(".HK") else s
    if "_" in base:
        return None
    digits = "".join(ch for ch in base if ch.isdigit())
    if not digits or len(digits) > 4:
        return None
    return f"{digits.zfill(4)}.HK"


def pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df.empty:
        return None
    exact = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in exact:
            return exact[cand.lower()]
    for col in df.columns:
        low = str(col).lower()
        for cand in candidates:
            if cand.lower() in low:
                return col
    return None


def to_num(s):
    return pd.to_numeric(pd.Series(s).astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False), errors="coerce")


def normalize_quotes(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume", "amount", "quote_source"])
    c_code = pick_col(df, ["code", "code_raw", "thscode", "THSCODE", "jydm", "股票代码", "证券代码", "同花顺代码", "代码"])
    c_date = pick_col(df, ["date", "time", "tradeDate", "交易日期", "日期"])
    c_open = pick_col(df, ["open", "开盘价"])
    c_high = pick_col(df, ["high", "最高价"])
    c_low = pick_col(df, ["low", "最低价"])
    c_close = pick_col(df, ["close", "latest", "最新价", "收盘价"])
    c_vol = pick_col(df, ["volume", "成交量"])
    c_amt = pick_col(df, ["amount", "成交额", "amount_est_hkd"])
    if not c_code or not c_date or not c_close:
        return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume", "amount", "quote_source"])
    out = pd.DataFrame()
    out["code"] = df[c_code].map(norm_code)
    out["date"] = pd.to_datetime(df[c_date], errors="coerce")
    out["open"] = to_num(df[c_open]) if c_open else np.nan
    out["high"] = to_num(df[c_high]) if c_high else np.nan
    out["low"] = to_num(df[c_low]) if c_low else np.nan
    out["close"] = to_num(df[c_close])
    out["volume"] = to_num(df[c_vol]) if c_vol else np.nan
    out["amount"] = to_num(df[c_amt]) if c_amt else np.nan
    out["quote_source"] = df["quote_source"] if "quote_source" in df.columns else source_name
    # For snapshot-only rows where open/high/low are missing, fall back to close.
    for col in ["open", "high", "low"]:
        out[col] = out[col].fillna(out["close"])
    out = out.dropna(subset=["code", "date", "close"]).drop_duplicates(["code", "date"], keep="last")
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def load_quotes(explicit_quotes: str | None = None) -> pd.DataFrame:
    sources: list[tuple[Path, str]] = []
    if explicit_quotes:
        p = Path(explicit_quotes)
        sources.append((p if p.is_absolute() else ROOT / p, "explicit"))
    sources += [
        (DEPLOY / "ifind_daily_quotes_raw.csv", "ifind"),
        (RAW / "daily_quotes.csv", "ifind_cache"),
        (DEPLOY / "ipo_daily_quotes_180d.csv", "free_fallback"),
    ]
    q = pd.DataFrame()
    for path, src in sources:
        q = normalize_quotes(read_csv_smart(path), src)
        if not q.empty:
            break
    snap = normalize_quotes(read_csv_smart(DEPLOY / "ifind_close_snapshot_raw.csv"), "ifind_snapshot")
    if not snap.empty:
        q = pd.concat([q, snap], ignore_index=True) if not q.empty else snap
        q = q.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")
    return q


def load_pool(path: str | Path) -> pd.DataFrame:
    pool = read_csv_smart(path)
    if pool.empty and Path(path).name != "ipo_investment_decision_scored.csv":
        pool = read_csv_smart(DEPLOY / "ipo_investment_decision_scored.csv")
    return pool


def issue_price_map(pool: pd.DataFrame) -> dict[str, float]:
    if pool.empty:
        return {}
    c_code = pick_col(pool, ["code", "股票代码", "证券代码"])
    c_issue = pick_col(pool, ["issue_price", "发行价", "发售价", "offer_price"])
    if not c_code or not c_issue:
        return {}
    temp = pd.DataFrame({"code": pool[c_code].map(norm_code), "issue": to_num(pool[c_issue])}).dropna(subset=["code", "issue"])
    return temp.drop_duplicates("code", keep="last").set_index("code")["issue"].to_dict()


def path_for_group(code: str, g: pd.DataFrame, issue_price: float | None) -> dict:
    g = g.sort_values("date").reset_index(drop=True)
    last = g.iloc[-1]
    quote_source = str(last.get("quote_source", "unknown"))
    quote_rows = len(g)
    latest_quote_date = last["date"].strftime("%Y-%m-%d") if pd.notna(last.get("date")) else ""
    if not issue_price or pd.isna(issue_price) or issue_price <= 0:
        return {
            "code": code, "quote_source": quote_source, "quote_rows": quote_rows, "latest_quote_date": latest_quote_date,
            "d1_close_ret": np.nan, "max_20_ret": np.nan, "min_20_ret": np.nan,
            "max_60_ret": np.nan, "min_60_ret": np.nan, "max_180_ret": np.nan, "min_180_ret": np.nan,
            "path_label": "missing_issue_price", "quant_path_label_cn": "发行价缺失", "quant_path_label_en": "Missing issue price",
            "secondary_signal": "发行价缺失，无法计算IPO锚点路径。", "path_data_source": quote_source,
        }
    g["ret"] = g["close"] / float(issue_price) - 1
    d1 = g.head(1)
    w20, w60, w180 = g.head(20), g.head(60), g.head(180)
    d1_ret = d1["ret"].iloc[0] if len(d1) else np.nan
    max20, min20 = w20["ret"].max(), w20["ret"].min()
    max60, min60 = w60["ret"].max(), w60["ret"].min()
    max180, min180 = w180["ret"].max(), w180["ret"].min()
    current_rel = g["ret"].iloc[-1]
    label, cn, en, signal = "moderate_trade", "温和交易型", "Moderate trading", "等待催化或成交确认。"
    if pd.notna(d1_ret) and pd.notna(min20) and pd.notna(max20) and d1_ret >= 0.10 and min20 >= -0.05 and max20 >= 0.20:
        label, cn, en = "strong_open", "上市即强势", "Strong from listing"
        signal = "上市初期强势；不追极端高开，优先等回踩或趋势确认。"
    elif ((pd.notna(min20) and min20 <= -0.10) or (pd.notna(min60) and min60 <= -0.15)) and pd.notna(max60) and max60 >= 0.25 and current_rel >= 0:
        label, cn, en = "deep_v_rebound", "深V反弹", "Deep-V rebound"
        signal = "深V路径；重点观察重新站回发行价和成交放大。"
    elif pd.notna(max20) and max20 >= 0.15 and ((pd.notna(current_rel) and current_rel < 0) or (pd.notna(min60) and min60 <= -0.30)):
        label, cn, en = "pop_then_fade", "升后破发", "Pump then break"
        signal = "升后回落；放量滞涨或跌破发行价应减仓/回避。"
    elif ((pd.notna(d1_ret) and d1_ret < 0) or (pd.notna(min20) and min20 <= -0.05)) and current_rel < 0 and (pd.isna(max60) or max60 < 0.15):
        label, cn, en = "persistent_break", "一路破发", "Persistent break issue price"
        signal = "破发弱势；除非出现基本面变化和放量站回发行价，否则回避。"
    elif pd.notna(max20) and pd.notna(min20) and -0.10 <= (d1_ret if pd.notna(d1_ret) else 0) <= 0.15 and min20 >= -0.15 and max20 <= 0.20:
        label, cn, en = "moderate_trade", "温和交易型", "Moderate trading"
        signal = "温和交易；等待行业催化或放量方向选择。"
    return {
        "code": code, "quote_source": quote_source, "quote_rows": quote_rows, "latest_quote_date": latest_quote_date,
        "d1_close_ret": d1_ret, "d1_close_ret_pct": round(d1_ret * 100, 2) if pd.notna(d1_ret) else np.nan,
        "max_20_ret": max20, "min_20_ret": min20, "max_60_ret": max60, "min_60_ret": min60, "max_180_ret": max180, "min_180_ret": min180,
        "max_20_ret_pct": round(max20 * 100, 2) if pd.notna(max20) else np.nan,
        "min_20_ret_pct": round(min20 * 100, 2) if pd.notna(min20) else np.nan,
        "max_60_ret_pct": round(max60 * 100, 2) if pd.notna(max60) else np.nan,
        "min_60_ret_pct": round(min60 * 100, 2) if pd.notna(min60) else np.nan,
        "max_180_ret_pct": round(max180 * 100, 2) if pd.notna(max180) else np.nan,
        "min_180_ret_pct": round(min180 * 100, 2) if pd.notna(min180) else np.nan,
        "path_label": label, "quant_path_label_cn": cn, "quant_path_label_en": en, "secondary_signal": signal, "path_data_source": quote_source,
    }


def build(pool_path: str | Path, quotes_path: str | None) -> pd.DataFrame:
    pool = load_pool(pool_path)
    q = load_quotes(quotes_path)
    if pool.empty or q.empty:
        return pd.DataFrame(columns=["code", "quote_source", "quote_rows", "latest_quote_date", "path_label", "secondary_signal"])
    issue_map = issue_price_map(pool)
    rows = [path_for_group(code, g, issue_map.get(code)) for code, g in q.groupby("code", sort=False)]
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="根据最新 iFind 优先行情生成上市后路径标签。")
    ap.add_argument("--pool", default=str(DEPLOY / "ipo_decision_pool.csv"))
    ap.add_argument("--quotes", default=None, help="可选。缺省时使用 iFind优先行情链路。")
    ap.add_argument("--out", default=str(DEPLOY / "ipo_post_listing_paths.csv"))
    ap.add_argument("--update-pool", action="store_true")
    args = ap.parse_args()
    out = build(args.pool, args.quotes)
    write_csv(out, args.out)
    if args.update_pool and not out.empty:
        pool = load_pool(args.pool)
        c_code = pick_col(pool, ["code", "股票代码", "证券代码"])
        if c_code:
            pool["_code_norm"] = pool[c_code].map(norm_code)
            old_cols = [c for c in out.columns if c in pool.columns and c != "code"]
            pool = pool.drop(columns=old_cols, errors="ignore")
            merged = pool.merge(out, left_on="_code_norm", right_on="code", how="left", suffixes=("", "_path"))
            if "code_path" in merged.columns:
                merged = merged.drop(columns=["code_path"])
            merged = merged.drop(columns=["_code_norm"], errors="ignore")
            write_csv(merged, args.pool)
    print(f"Saved {args.out} rows={len(out)}; source={(out['quote_source'].dropna().iloc[0] if not out.empty and 'quote_source' in out else 'none')}")


if __name__ == "__main__":
    main()
