from __future__ import annotations

"""精确解禁事件标准化与条件化风险评级初版。

输入优先级：
1. deploy_data/ifind_lockup_events_raw.csv / data/raw_ifind/lockup_events.csv
2. deploy_data/ipo_technical_signals.csv
3. deploy_data/ifind_daily_quotes_raw.csv / data/raw_ifind/daily_quotes.csv
4. deploy_data/ipo_decision_pool.csv / ipo_investment_decision_scored.csv

输出：
- deploy_data/accurate_lockup_events.csv
- deploy_data/lockup_risk_model.csv

说明：
- 只有 iFind 精确解禁明细进入主表；规则估算不作为主数据。
- 字段映射不完整时，保留 raw 字段并把 confidence 标记为低，不伪造精确结论。
"""

from pathlib import Path
import math
import re
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


def pick_col(df: pd.DataFrame, candidates: list[str], fuzzy: bool = True) -> str | None:
    if df.empty:
        return None
    exact = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in exact:
            return exact[c.lower()]
    if fuzzy:
        for col in df.columns:
            low = str(col).lower()
            for c in candidates:
                if c.lower() in low:
                    return col
    return None


def to_num(s):
    return pd.to_numeric(pd.Series(s).astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False), errors="coerce")


def load_lockup_raw() -> pd.DataFrame:
    for p in [DEPLOY / "ifind_lockup_events_raw.csv", RAW / "lockup_events.csv"]:
        df = read_csv_smart(p)
        if not df.empty:
            return df
    return pd.DataFrame()


def load_quotes_amount_ma20() -> pd.DataFrame:
    tech = read_csv_smart(DEPLOY / "ipo_technical_signals.csv")
    if not tech.empty and "code" in tech.columns:
        # 技术表当前未输出amount_ma20，回退到日行情计算。
        pass
    for p in [DEPLOY / "ifind_daily_quotes_raw.csv", RAW / "daily_quotes.csv", DEPLOY / "ipo_daily_quotes_180d.csv"]:
        q = read_csv_smart(p)
        if q.empty:
            continue
        c_code = pick_col(q, ["code", "jydm", "THSCODE", "thscode", "股票代码", "证券代码"])
        c_date = pick_col(q, ["date", "交易日期", "tradeDate", "日期", "time"])
        c_amt = pick_col(q, ["amount", "成交额", "amount_est_hkd"])
        if not c_code or not c_date or not c_amt:
            continue
        out = pd.DataFrame({
            "code": q[c_code].map(norm_code),
            "date": pd.to_datetime(q[c_date], errors="coerce"),
            "amount": pd.to_numeric(q[c_amt], errors="coerce"),
        }).dropna(subset=["code", "date"])
        out = out.sort_values(["code", "date"])
        out["amount_ma20"] = out.groupby("code")["amount"].transform(lambda x: x.rolling(20, min_periods=5).mean())
        latest = out.dropna(subset=["amount_ma20"]).groupby("code", as_index=False).tail(1)
        return latest[["code", "amount_ma20"]]
    return pd.DataFrame(columns=["code", "amount_ma20"])


def load_issue_and_price() -> pd.DataFrame:
    frames = []
    for p in [DEPLOY / "ipo_decision_pool.csv", DEPLOY / "ipo_investment_decision_scored.csv"]:
        df = read_csv_smart(p)
        if df.empty:
            continue
        c = pick_col(df, ["code", "股票代码", "证券代码"])
        if not c:
            continue
        out = pd.DataFrame({"code": df[c].map(norm_code)})
        for name, candidates in {
            "name": ["name", "简称", "公司名称", "jydm_mc"],
            "issue_price": ["issue_price", "发行价", "p05310_f003"],
            "last_close": ["last_close", "latest_close", "最近收盘", "收盘价"],
            "relative_to_issue_pct": ["relative_to_issue_pct", "较发行价涨幅", "头发行价"],
        }.items():
            col = pick_col(df, candidates)
            out[name] = df[col] if col else pd.NA
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=["code", "issue_price", "last_close", "relative_to_issue_pct"])
    merged = pd.concat(frames, ignore_index=True).dropna(subset=["code"]).drop_duplicates("code", keep="last")
    return merged


def normalize_lockup(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    c_code = pick_col(df, ["jydm", "code", "股票代码", "证券代码", "p03764_f001"])
    c_name = pick_col(df, ["jydm_mc", "name", "公司名称", "简称", "p03764_f002"])
    c_date = pick_col(df, ["解禁日期", "上市流通日期", "锁定结束日", "p03764_f001", "p03764_f011", "日期"])
    # 避免把股票代码列误判成日期。
    if c_date == c_code:
        c_date = None
        for col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().sum() >= max(3, len(df) * 0.3):
                c_date = col
                break
    c_type = pick_col(df, ["解禁类型", "股份性质", "股东类型", "holder_held_shares_nature", "p03764_f011", "p03764_f010"])
    c_holder = pick_col(df, ["股东名称", "解禁股东", "持有人", "holder_name", "p03764_f002", "p03764_f003"])
    c_shares = pick_col(df, ["解禁股数", "上市流通股数", "解除限售数量", "股数", "p03764_f003", "p03764_f004"])
    c_value = pick_col(df, ["解禁市值", "上市流通市值", "市值", "p03764_f004", "p03764_f005"])
    c_total_pct = pick_col(df, ["占总股本比例", "占总股本", "总股本比例", "p03764_f006", "p03764_f007"])
    c_float_pct = pick_col(df, ["占流通股比例", "流通股比例", "p03764_f008", "p03764_f009"])
    out = pd.DataFrame()
    out["code"] = df[c_code].map(norm_code) if c_code else pd.NA
    out["name"] = df[c_name] if c_name else pd.NA
    out["unlock_date"] = pd.to_datetime(df[c_date], errors="coerce") if c_date else pd.NaT
    out["unlock_type"] = df[c_type] if c_type else pd.NA
    out["holder_name"] = df[c_holder] if c_holder else pd.NA
    out["unlock_shares"] = to_num(df[c_shares]) if c_shares else np.nan
    out["unlock_value_hkd"] = to_num(df[c_value]) if c_value else np.nan
    out["pct_total_shares"] = to_num(df[c_total_pct]) if c_total_pct else np.nan
    out["pct_float_shares"] = to_num(df[c_float_pct]) if c_float_pct else np.nan
    out["data_confidence"] = "iFind精确/字段待映射"
    if c_code and c_date:
        out["data_confidence"] = "iFind精确"
    out = out.dropna(subset=["code"], how="any").drop_duplicates()
    return out


def classify_type_risk(t: str) -> int:
    s = str(t or "")
    if any(k in s for k in ["Pre", "pre", "IPO前", "上市前", "老股东", "财务", "VC", "PE"]):
        return 20
    if any(k in s for k in ["基石", "cornerstone"]):
        return 14
    if any(k in s for k in ["控股", "大股东", "一致行动"]):
        return 12
    if any(k in s for k in ["员工", "激励", "期权"]):
        return 8
    return 10


def build() -> pd.DataFrame:
    raw = load_lockup_raw()
    lock = normalize_lockup(raw)
    if lock.empty:
        return pd.DataFrame(columns=["code", "unlock_date", "lockup_risk_score", "lockup_risk_level"])
    amt = load_quotes_amount_ma20()
    base = load_issue_and_price()
    out = lock.merge(amt, on="code", how="left").merge(base, on="code", how="left", suffixes=("", "_base"))
    if "name_base" in out.columns:
        out["name"] = out["name"].fillna(out["name_base"])
    today = pd.Timestamp.today().normalize()
    out["days_to_unlock"] = (out["unlock_date"] - today).dt.days
    out["unlock_value_to_20d_amount"] = out["unlock_value_hkd"] / pd.to_numeric(out.get("amount_ma20"), errors="coerce").replace(0, np.nan)
    rel = pd.to_numeric(out.get("relative_to_issue_pct"), errors="coerce")
    # 兼容相对发行价为 1.2 或 120 两种口径。
    rel = rel.where(rel.abs() <= 5, rel / 100)
    out["price_gain_vs_issue"] = rel
    score = pd.Series(0, index=out.index, dtype="float")
    ratio = out["unlock_value_to_20d_amount"]
    score += np.select([ratio >= 10, ratio >= 5, ratio >= 2, ratio < 2], [30, 24, 14, 5], default=12)
    gain = out["price_gain_vs_issue"]
    score += np.select([gain >= 0.5, gain >= 0.2, gain >= 0, gain < 0], [20, 14, 8, 4], default=8)
    score += out["unlock_type"].map(classify_type_risk).fillna(10)
    days = out["days_to_unlock"]
    score += np.select([(days >= 0) & (days <= 30), (days > 30) & (days <= 90), (days > 90) & (days <= 180), days > 180], [15, 10, 5, 1], default=6)
    # 数据可信度不是抬高风险，而是降低结论置信度；风险分小幅增加提醒复核。
    score += np.where(out["data_confidence"].astype(str).str.contains("待映射"), 5, 0)
    out["lockup_risk_score"] = np.clip(score, 0, 100).round(1)
    out["lockup_risk_level"] = pd.cut(out["lockup_risk_score"], bins=[-1, 20, 40, 60, 80, 101], labels=["影响较小", "低风险", "中性观察", "中高风险", "高风险"])
    out["lockup_risk_reason"] = out.apply(lambda r: f"解禁规模/20日成交额={r.get('unlock_value_to_20d_amount', np.nan):.1f}倍；较发行价涨幅={r.get('price_gain_vs_issue', np.nan):.1%}；类型={r.get('unlock_type','')}; 距离={r.get('days_to_unlock','')}天" if pd.notna(r.get("unlock_value_to_20d_amount")) else "缺少解禁市值或成交额，风险评级置信度较低", axis=1)
    keep = [
        "code", "name", "unlock_date", "days_to_unlock", "unlock_type", "holder_name",
        "unlock_shares", "unlock_value_hkd", "pct_total_shares", "pct_float_shares",
        "amount_ma20", "unlock_value_to_20d_amount", "price_gain_vs_issue",
        "lockup_risk_score", "lockup_risk_level", "lockup_risk_reason", "data_confidence"
    ]
    return out[[c for c in keep if c in out.columns]].sort_values(["unlock_date", "lockup_risk_score"], ascending=[True, False])


def merge_to_pool(risk: pd.DataFrame) -> None:
    if risk.empty or "code" not in risk.columns:
        return
    future = risk[pd.to_datetime(risk.get("unlock_date"), errors="coerce") >= pd.Timestamp.today().normalize()].copy()
    if future.empty:
        return
    future = future.sort_values(["code", "unlock_date"]).groupby("code", as_index=False).first()
    cols = ["code", "unlock_date", "days_to_unlock", "lockup_risk_score", "lockup_risk_level", "lockup_risk_reason"]
    future = future[[c for c in cols if c in future.columns]]
    for fname in ["ipo_investment_decision_scored.csv", "ipo_decision_pool.csv"]:
        p = DEPLOY / fname
        df = read_csv_smart(p)
        if df.empty or "code" not in df.columns:
            continue
        df["_code_norm"] = df["code"].map(norm_code)
        old_cols = [c for c in future.columns if c in df.columns and c != "code"]
        df = df.drop(columns=old_cols, errors="ignore")
        merged = df.merge(future, left_on="_code_norm", right_on="code", how="left", suffixes=("", "_lockup"))
        if "code_lockup" in merged.columns:
            merged = merged.drop(columns=["code_lockup"])
        merged = merged.drop(columns=["_code_norm"], errors="ignore")
        write_csv(merged, p)


def main():
    risk = build()
    write_csv(risk, DEPLOY / "accurate_lockup_events.csv")
    write_csv(risk, DEPLOY / "lockup_risk_model.csv")
    merge_to_pool(risk)
    print(f"Saved lockup risk rows={len(risk)}")


if __name__ == "__main__":
    main()
