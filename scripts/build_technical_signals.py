from __future__ import annotations

"""计算港股 IPO/上市后交易池专业技术指标和交易触发条件。

输入优先级：
1. deploy_data/ifind_daily_quotes_raw.csv iFind THS_HQ 原始表
2. data/raw_ifind/daily_quotes.csv iFind 缓存表
3. deploy_data/ipo_daily_quotes_180d.csv 旧免费行情标准表（仅兜底，不作为主评分优先源）
4. deploy_data/ifind_close_snapshot_raw.csv 当日快照，用于临时补充当日日K

输出：
- deploy_data/ipo_technical_signals.csv
- 尽力把主要技术字段合并进 deploy_data/ipo_investment_decision_scored.csv
"""

import math
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
    return pd.read_csv(path, dtype=str)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def norm_code(x):
    if pd.isna(x):
        return None
    s = str(x).strip().upper().replace(" ", "")
    if not s or s.startswith("H"):
        return None
    if s.endswith(".HK"):
        base = s[:-3]
    else:
        base = s
    if "_" in base:
        return None
    digits = "".join(ch for ch in base if ch.isdigit())
    if not digits or len(digits) > 4:
        return None
    return f"{digits.zfill(4)}.HK"


def pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    exact = {str(c).lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in exact:
            return exact[c.lower()]
    for col in df.columns:
        low = str(col).lower()
        for c in candidates:
            if c.lower() in low:
                return col
    return None


def normalize_quotes(df: pd.DataFrame, source_name: str = "unknown") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["code","date","open","high","low","close","volume","amount","source"])
    c_code = pick_col(df, ["code", "code_raw", "thscode", "THSCODE", "jydm", "股票代码", "证券代码", "同花顺代码", "p05310_f001", "p03764_f001"])
    c_date = pick_col(df, ["date", "time", "tradeDate", "交易日期", "日期"])
    c_open = pick_col(df, ["open", "开盘价"])
    c_high = pick_col(df, ["high", "最高价"])
    c_low = pick_col(df, ["low", "最低价"])
    c_close = pick_col(df, ["close", "latest", "最新价", "收盘价"])
    c_vol = pick_col(df, ["volume", "成交量"])
    c_amt = pick_col(df, ["amount", "成交额", "amount_est_hkd"])
    out = pd.DataFrame()
    out["code"] = df[c_code].map(norm_code) if c_code else pd.NA
    out["date"] = pd.to_datetime(df[c_date], errors="coerce") if c_date else pd.NaT
    for name, col in [("open", c_open), ("high", c_high), ("low", c_low), ("close", c_close), ("volume", c_vol), ("amount", c_amt)]:
        out[name] = pd.to_numeric(df[col], errors="coerce") if col else np.nan
    out["source"] = df["source"] if "source" in df.columns else source_name
    out = out.dropna(subset=["code", "date", "close"]).drop_duplicates(["code", "date"], keep="last")
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def rsi(series: pd.Series, n=14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    avg_loss = down.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def max_drawdown_from_high(close: pd.Series) -> pd.Series:
    roll_high = close.cummax()
    return close / roll_high - 1


def compute_one(g: pd.DataFrame) -> dict:
    g = g.sort_values("date").copy()
    for w in [5, 10, 20, 60, 120]:
        g[f"ma{w}"] = g["close"].rolling(w, min_periods=max(3, min(w, 10))).mean()
    g["ema12"] = g["close"].ewm(span=12, adjust=False).mean()
    g["ema26"] = g["close"].ewm(span=26, adjust=False).mean()
    g["macd_dif"] = g["ema12"] - g["ema26"]
    g["macd_dea"] = g["macd_dif"].ewm(span=9, adjust=False).mean()
    g["macd_hist"] = 2 * (g["macd_dif"] - g["macd_dea"])
    g["rsi14"] = rsi(g["close"], 14)
    g["boll_mid"] = g["close"].rolling(20, min_periods=10).mean()
    boll_std = g["close"].rolling(20, min_periods=10).std()
    g["boll_upper"] = g["boll_mid"] + 2 * boll_std
    g["boll_lower"] = g["boll_mid"] - 2 * boll_std
    # KDJ: short-term momentum and reversal confirmation.
    low9 = g["low"].rolling(9, min_periods=5).min()
    high9 = g["high"].rolling(9, min_periods=5).max()
    rsv = (g["close"] - low9) / (high9 - low9).replace(0, np.nan) * 100
    g["kdj_k"] = rsv.ewm(alpha=1/3, adjust=False).mean()
    g["kdj_d"] = g["kdj_k"].ewm(alpha=1/3, adjust=False).mean()
    g["kdj_j"] = 3 * g["kdj_k"] - 2 * g["kdj_d"]

    # OBV and MFI: money-flow confirmation / divergence.
    direction = np.sign(g["close"].diff()).fillna(0)
    g["obv"] = (direction * g["volume"].fillna(0)).cumsum()
    typical = (g["high"] + g["low"] + g["close"]) / 3
    raw_money = typical * g["volume"].fillna(0)
    pos_flow = raw_money.where(typical.diff() > 0, 0).rolling(14, min_periods=5).sum()
    neg_flow = raw_money.where(typical.diff() < 0, 0).rolling(14, min_periods=5).sum().abs()
    money_ratio = pos_flow / neg_flow.replace(0, np.nan)
    g["mfi14"] = 100 - 100/(1 + money_ratio)

    prev_close = g["close"].shift(1)
    tr = pd.concat([(g["high"] - g["low"]).abs(), (g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()], axis=1).max(axis=1)
    g["atr14"] = tr.rolling(14, min_periods=5).mean()
    for w in [5, 20, 60]:
        g[f"ret_{w}d"] = g["close"].pct_change(w)
        g[f"amount_ma{w}"] = g["amount"].rolling(w, min_periods=max(3, min(w, 10))).mean()
    g["vol_ratio_20d"] = g["amount"] / g["amount_ma20"].replace(0, np.nan)
    g["drawdown_from_high"] = max_drawdown_from_high(g["close"])
    row = g.iloc[-1]
    def val(x):
        try:
            return float(x) if pd.notna(x) else np.nan
        except Exception:
            return np.nan
    close = val(row.get("close"))
    ma5, ma10, ma20, ma60, ma120 = [val(row.get(f"ma{w}")) for w in [5,10,20,60,120]]
    rsi14 = val(row.get("rsi14"))
    vr20 = val(row.get("vol_ratio_20d"))
    ret20 = val(row.get("ret_20d"))
    ret60 = val(row.get("ret_60d"))
    dd = val(row.get("drawdown_from_high"))
    macd_hist = val(row.get("macd_hist"))
    prev_hist = val(g["macd_hist"].iloc[-2]) if len(g) >= 2 else np.nan
    kdj_k, kdj_d, kdj_j = val(row.get("kdj_k")), val(row.get("kdj_d")), val(row.get("kdj_j"))
    prev_k, prev_d = (val(g["kdj_k"].iloc[-2]), val(g["kdj_d"].iloc[-2])) if len(g) >= 2 else (np.nan, np.nan)
    mfi14 = val(row.get("mfi14"))
    boll_upper, boll_mid, boll_lower = val(row.get("boll_upper")), val(row.get("boll_mid")), val(row.get("boll_lower"))
    obv = val(row.get("obv"))
    obv20 = val(g["obv"].rolling(20, min_periods=5).mean().iloc[-1]) if "obv" in g else np.nan
    kdj_signal = "中性"
    if not math.isnan(prev_k) and not math.isnan(prev_d) and not math.isnan(kdj_k) and not math.isnan(kdj_d):
        if prev_k < prev_d and kdj_k >= kdj_d and kdj_k < 60:
            kdj_signal = "KDJ金叉修复"
        elif prev_k > prev_d and kdj_k <= kdj_d and kdj_k > 60:
            kdj_signal = "KDJ高位死叉"
        elif kdj_k >= 80 and kdj_d >= 80:
            kdj_signal = "KDJ高位钝化"
        elif kdj_k <= 30 and kdj_d <= 30:
            kdj_signal = "KDJ低位观察"
    boll_signal = "中性"
    if not math.isnan(close) and not math.isnan(boll_upper) and not math.isnan(boll_mid) and not math.isnan(boll_lower):
        if close > boll_upper:
            boll_signal = "BOLL上轨突破"
        elif close > boll_mid:
            boll_signal = "BOLL中轨上方"
        elif close < boll_lower:
            boll_signal = "BOLL下轨弱势/反弹观察"
        elif close < boll_mid:
            boll_signal = "BOLL中轨下方"
    obv_signal = "中性"
    if not math.isnan(obv) and not math.isnan(obv20):
        obv_signal = "OBV资金确认" if obv >= obv20 else "OBV未确认"
    # 技术状态：优先做风险，再做机会。
    status = "中性震荡"
    buy_trigger = "等待价格/成交确认"
    sell_trigger = "跌破关键均线或放量破位"
    tech_score = 50
    if not math.isnan(close) and not math.isnan(ma20) and not math.isnan(ma60):
        if close > ma20 and ma20 > ma60 and (math.isnan(ret60) or ret60 >= 0.15):
            status = "趋势延续"
            tech_score += 20
            buy_trigger = "回踩20日线缩量企稳，或放量突破20日高点"
            sell_trigger = "放量跌破20日线，或从高点回撤≥15%"
        elif close > ma20 and (math.isnan(ma10) or ma10 >= ma20):
            status = "趋势转强"
            tech_score += 12
            buy_trigger = "连续2日站上20日线且成交额≥20日均值1.3倍"
        elif close < ma20 and not math.isnan(vr20) and vr20 >= 1.5:
            status = "技术破位"
            tech_score -= 18
            buy_trigger = "暂不买入，等待重新站回20日线"
            sell_trigger = "破位后反抽不过20日线，继续减仓/回避"
    if not math.isnan(dd) and dd <= -0.30 and (math.isnan(ma60) or close < ma60):
        status = "高位回撤预警"
        tech_score = min(tech_score, 35)
        buy_trigger = "避免抄底，等待60日线修复"
        sell_trigger = "若反弹缩量且不过60日线，继续降低仓位"
    if not math.isnan(rsi14) and rsi14 >= 75 and not math.isnan(vr20) and vr20 >= 1.5:
        if close >= g["close"].tail(60).quantile(0.85):
            status = "放量过热/止盈观察"
            tech_score = min(tech_score, 62)
            buy_trigger = "不追高，等待回踩20日线或前高回踩确认"
            sell_trigger = "放量滞涨或跌回5日线下方，优先止盈"
    if not math.isnan(macd_hist) and not math.isnan(prev_hist) and prev_hist < 0 <= macd_hist and close > ma20:
        tech_score += 5
    if kdj_signal == "KDJ金叉修复":
        tech_score += 5
    elif kdj_signal in ["KDJ高位死叉"]:
        tech_score -= 6
    if boll_signal == "BOLL上轨突破" and not math.isnan(vr20) and vr20 >= 1.3:
        tech_score += 5
    elif boll_signal == "BOLL中轨下方":
        tech_score -= 4
    if obv_signal == "OBV资金确认" and close > ma20:
        tech_score += 4
    if not math.isnan(mfi14) and mfi14 >= 80:
        tech_score -= 5
    elif not math.isnan(mfi14) and mfi14 <= 20 and close >= ma20:
        tech_score += 3
    if not math.isnan(vr20) and vr20 >= 1.5 and close > ma20:
        tech_score += 5
    if not math.isnan(rsi14) and rsi14 >= 80:
        tech_score -= 6
    if not math.isnan(dd) and dd <= -0.25:
        tech_score -= 8
    if not math.isnan(row.get("amount_ma20", np.nan)):
        amt20 = val(row.get("amount_ma20"))
        if amt20 < 5_000_000:
            tech_score -= 12
            status = status + " / 低流动性"
        elif amt20 < 10_000_000:
            tech_score -= 5
    tech_score = max(0, min(100, tech_score))
    return {
        "code": row["code"],
        "latest_quote_date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
        "latest_close": close,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "rsi14": rsi14,
        "macd_dif": val(row.get("macd_dif")),
        "macd_dea": val(row.get("macd_dea")),
        "macd_hist": macd_hist,
        "boll_upper": val(row.get("boll_upper")),
        "boll_mid": val(row.get("boll_mid")),
        "boll_lower": val(row.get("boll_lower")),
        "kdj_k": kdj_k,
        "kdj_d": kdj_d,
        "kdj_j": kdj_j,
        "kdj_signal": kdj_signal,
        "boll_signal": boll_signal,
        "obv": obv,
        "obv_signal": obv_signal,
        "mfi14": mfi14,
        "atr14": val(row.get("atr14")),
        "ret_20d": ret20,
        "ret_60d": ret60,
        "vol_ratio_20d": vr20,
        "drawdown_from_listing_high": dd,
        "technical_score": tech_score,
        "technical_state": status,
        "buy_trigger": buy_trigger,
        "sell_trigger": sell_trigger,
        "quote_rows_for_ta": len(g),
        "quote_source": str(row.get("source", "unknown")),
    }


def build() -> pd.DataFrame:
    # v9: iFind is the primary source. Free/Yahoo/Stooq historical file is only a fallback.
    sources = [
        (DEPLOY / "ifind_daily_quotes_raw.csv", "ifind"),
        (RAW / "daily_quotes.csv", "ifind_cache"),
        (DEPLOY / "ipo_daily_quotes_180d.csv", "free_fallback"),
    ]
    q = pd.DataFrame()
    for p, source_name in sources:
        q = normalize_quotes(read_csv_smart(p), source_name)
        if not q.empty:
            break
    # Add same-day iFind snapshot as a temporary daily bar if THS_HQ has not yet written today's daily K.
    snap = normalize_quotes(read_csv_smart(DEPLOY / "ifind_close_snapshot_raw.csv"), "ifind_snapshot")
    if not snap.empty:
        q = pd.concat([q, snap], ignore_index=True) if not q.empty else snap
        q = q.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")
    if q.empty:
        return pd.DataFrame(columns=["code","technical_score","technical_state","buy_trigger","sell_trigger"])
    rows = [compute_one(g) for _, g in q.groupby("code", sort=False) if len(g.dropna(subset=["close"])) >= 3]
    return pd.DataFrame(rows)


def merge_to_pool(tech: pd.DataFrame) -> None:
    if tech.empty:
        return
    for name in ["ipo_investment_decision_scored.csv", "ipo_decision_pool.csv"]:
        p = DEPLOY / name
        df = read_csv_smart(p)
        if df.empty or "code" not in df.columns:
            continue
        df["code_norm_for_merge"] = df["code"].map(norm_code)
        old_cols = [c for c in tech.columns if c in df.columns and c != "code"]
        df = df.drop(columns=old_cols, errors="ignore")
        merged = df.merge(tech, left_on="code_norm_for_merge", right_on="code", how="left", suffixes=("", "_tech"))
        # 保留原 code 列
        if "code_tech" in merged.columns:
            merged = merged.drop(columns=["code_tech"])
        merged = merged.drop(columns=["code_norm_for_merge"], errors="ignore")
        write_csv(merged, p)


def main():
    tech = build()
    write_csv(tech, DEPLOY / "ipo_technical_signals.csv")
    merge_to_pool(tech)
    print(f"Saved deploy_data/ipo_technical_signals.csv rows={len(tech)}")


if __name__ == "__main__":
    main()
