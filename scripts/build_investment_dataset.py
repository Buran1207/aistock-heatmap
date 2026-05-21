from __future__ import annotations

"""Rebuild final decision tables from latest derived data.

v11 fixes the data-chain bug where API raw data was updated but final page tables still
kept old Yahoo/Stooq / stale derived scores. This script always rebuilds:
- ipo_investment_decision_scored.csv
- secondary_market_decision.csv
- primary_market_decision.csv
- today_action_list.csv
- data_lineage_last_run.csv

The script is deliberately deterministic and file-based so Streamlit Cloud and local
Streamlit read the same final tables after deploy_data is pushed.
"""

from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy_data"
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950")


def read_csv(path: str | Path) -> pd.DataFrame:
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
    if pd.isna(x): return None
    s = str(x).strip().upper().replace(" ", "")
    if not s or s.startswith("H"): return None
    base = s[:-3] if s.endswith(".HK") else s
    if "_" in base: return None
    digits = "".join(ch for ch in base if ch.isdigit())
    if not digits or len(digits) > 4: return None
    return f"{digits.zfill(4)}.HK"


def pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df.empty: return None
    exact = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns: return cand
        if cand.lower() in exact: return exact[cand.lower()]
    for col in df.columns:
        low = str(col).lower()
        for cand in candidates:
            if cand.lower() in low: return col
    return None


def to_num(s):
    return pd.to_numeric(pd.Series(s).astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False), errors="coerce")


def clean_1970_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        name = str(c).lower()
        if any(k in name for k in ["date", "日期", "_at"]):
            parsed = pd.to_datetime(out[c], errors="coerce")
            mask_1970 = parsed.dt.strftime("%Y-%m-%d").eq("1970-01-01")
            out.loc[mask_1970, c] = pd.NA
    return out


def load_base() -> pd.DataFrame:
    # Start from ipo_decision_pool if available; it is the stable universe. Otherwise use existing scored table.
    for name in ["ipo_decision_pool.csv", "ipo_investment_decision_scored.csv"]:
        df = read_csv(DEPLOY / name)
        if not df.empty:
            return clean_1970_dates(df)
    raise SystemExit("Missing deploy_data/ipo_decision_pool.csv and ipo_investment_decision_scored.csv")


def merge_by_code(base: pd.DataFrame, extra: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    if base.empty or extra.empty:
        return base
    c_base = pick_col(base, ["code", "股票代码", "证券代码"])
    c_extra = pick_col(extra, ["code", "股票代码", "证券代码"])
    if not c_base or not c_extra:
        return base
    left = base.copy()
    right = extra.copy()
    left["_code_norm"] = left[c_base].map(norm_code)
    right["_code_norm"] = right[c_extra].map(norm_code)
    right = right.dropna(subset=["_code_norm"]).drop_duplicates("_code_norm", keep="last")
    # Do not let stale base columns win over freshly derived columns.
    fresh_cols = [c for c in right.columns if c not in {c_extra, "_code_norm"}]
    if prefix:
        rename = {c: f"{prefix}{c}" for c in fresh_cols if c in left.columns}
        right = right.rename(columns=rename)
        fresh_cols = [rename.get(c, c) for c in fresh_cols]
    else:
        left = left.drop(columns=[c for c in fresh_cols if c in left.columns], errors="ignore")
    merged = left.merge(right[["_code_norm"] + fresh_cols], on="_code_norm", how="left")
    return merged.drop(columns=["_code_norm"], errors="ignore")


def add_listing_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    listing_col = pick_col(out, ["listing_date", "上市日期", "上市日"])
    if listing_col:
        ld = pd.to_datetime(out[listing_col], errors="coerce")
    else:
        ld = pd.Series(pd.NaT, index=out.index)
    today = pd.Timestamp.today().normalize()
    out["listing_date"] = ld.dt.strftime("%Y-%m-%d").replace("NaT", pd.NA)
    out["listed_days"] = (today - ld).dt.days
    out.loc[out["listed_days"].lt(0), "listed_days"] = np.nan
    out["listed_age_bucket_cn"] = np.select(
        [out["listed_days"].isna(), out["listed_days"].le(30), out["listed_days"].le(180), out["listed_days"].gt(180)],
        ["未上市", "0-30D", "31-180D", "180D+"], default="未上市")
    out["listed_age_bucket_en"] = np.select(
        [out["listed_days"].isna(), out["listed_days"].le(30), out["listed_days"].le(180), out["listed_days"].gt(180)],
        ["Unlisted", "0-30D", "31-180D", "180D+"], default="Unlisted")
    return out


def add_secondary_score(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    n = len(out)
    tech = to_num(out.get("technical_score", pd.Series(np.nan, index=out.index))).fillna(50).clip(0, 100)
    issue = to_num(out.get("issue_price", pd.Series(np.nan, index=out.index)))
    close = to_num(out.get("latest_close", out.get("last_close", pd.Series(np.nan, index=out.index))))
    rel_issue = close / issue.replace(0, np.nan) - 1
    out["relative_to_issue_pct"] = rel_issue
    anchor = pd.Series(50.0, index=out.index)
    anchor = anchor.where(rel_issue.isna(), np.select(
        [rel_issue >= 0.30, rel_issue >= 0.10, rel_issue >= 0.00, rel_issue >= -0.05, rel_issue < -0.05],
        [85, 75, 65, 45, 25], default=50))
    ret20 = to_num(out.get("ret_20d", out.get("ret20_current", pd.Series(np.nan, index=out.index)))).fillna(0)
    ret60 = to_num(out.get("ret_60d", out.get("ret60_current", pd.Series(np.nan, index=out.index)))).fillna(0)
    rel_strength = (50 + ret20 * 80 + ret60 * 40).clip(0, 100)
    qrows = to_num(out.get("quote_rows_for_ta", out.get("quote_rows", pd.Series(np.nan, index=out.index)))).fillna(0)
    liquidity = pd.Series(np.select([qrows >= 60, qrows >= 20, qrows >= 5, qrows < 5], [80, 65, 45, 20], default=50), index=out.index).astype(float)
    days = to_num(out.get("listed_days", pd.Series(np.nan, index=out.index)))
    bucket = out.get("listed_age_bucket_cn", pd.Series("", index=out.index)).astype(str)
    base_score = pd.Series(50.0, index=out.index)
    m0 = bucket.eq("0-30D")
    m1 = bucket.eq("31-180D")
    m2 = bucket.eq("180D+")
    base_score.loc[m0] = anchor.loc[m0] * 0.30 + tech.loc[m0] * 0.30 + rel_strength.loc[m0] * 0.20 + liquidity.loc[m0] * 0.20
    base_score.loc[m1] = tech.loc[m1] * 0.35 + anchor.loc[m1] * 0.25 + rel_strength.loc[m1] * 0.25 + liquidity.loc[m1] * 0.15
    base_score.loc[m2] = tech.loc[m2] * 0.50 + rel_strength.loc[m2] * 0.30 + liquidity.loc[m2] * 0.20
    penalty = pd.Series(0.0, index=out.index)
    lock = out.get("lockup_risk_level", out.get("lockup_pressure_cn", pd.Series("", index=out.index))).astype(str)
    days_unlock = to_num(out.get("days_to_unlock", pd.Series(np.nan, index=out.index)))
    penalty += np.where(lock.str.contains("高", na=False) & days_unlock.le(30), 18, 0)
    penalty += np.where(lock.str.contains("中高", na=False) & days_unlock.le(90), 10, 0)
    source = out.get("quote_source", pd.Series("", index=out.index)).astype(str)
    penalty += np.where(source.str.contains("free|yahoo|stooq", case=False, regex=True, na=False), 15, 0)
    state = out.get("technical_state", pd.Series("", index=out.index)).astype(str)
    penalty += np.where(state.str.contains("过热|滞涨|破位|高位回撤|低流动性", regex=True, na=False), 8, 0)
    final = (base_score - penalty).clip(0, 100).round(1)
    out["secondary_score_rebuilt"] = final
    out["current_stage_score"] = np.where(days.notna(), final, to_num(out.get("current_stage_score", pd.Series(np.nan, index=out.index))))
    def rating(sc, qsource):
        if pd.isna(sc): return "信息不足", "补数据"
        if "free" in str(qsource).lower() or "yahoo" in str(qsource).lower():
            cap = "（免费源兜底，需复核）"
        else:
            cap = ""
        sc = float(sc)
        if sc >= 80: return "A 二级趋势确认" + cap, "可参与；优先等回踩或成交确认"
        if sc >= 70: return "B 二级交易观察" + cap, "小仓或等待确认"
        if sc >= 60: return "C4 等待二级买点" + cap, "等待回踩、深V或趋势确认"
        if sc >= 45: return "C5 等待风险释放" + cap, "不追高，先看风险释放"
        return "D 破发/弱势回避" + cap, "原则上回避"
    ratings = [rating(sc, src) for sc, src in zip(out["secondary_score_rebuilt"], source)]
    out["secondary_rating_cn"] = [x[0] for x in ratings]
    out["secondary_action_cn"] = [x[1] for x in ratings]
    out["secondary_rating_en"] = out["secondary_rating_cn"]
    out["secondary_action_en"] = out["secondary_action_cn"]
    out["risk_penalty_points"] = penalty.round(1)
    out["secondary_score_explain_cn"] = [f"技术{tech.iloc[i]:.1f}；IPO锚点{anchor.iloc[i]:.1f}；相对强弱{rel_strength.iloc[i]:.1f}；流动性{liquidity.iloc[i]:.1f}；风险扣分-{penalty.iloc[i]:.1f}" for i in range(n)]
    out["secondary_score_explain_en"] = out["secondary_score_explain_cn"]
    out["why_not_higher_cn"] = out.apply(lambda r: why_not_higher(r), axis=1)
    out["why_not_higher_en"] = out["why_not_higher_cn"]
    return out


def why_not_higher(r: pd.Series) -> str:
    parts = []
    qsrc = str(r.get("quote_source", ""))
    if any(x in qsrc.lower() for x in ["free", "yahoo", "stooq"]):
        parts.append("行情仍为免费源兜底")
    if pd.to_numeric(r.get("quote_rows_for_ta", r.get("quote_rows", np.nan)), errors="coerce") < 20:
        parts.append("有效行情历史不足")
    if pd.to_numeric(r.get("risk_penalty_points", 0), errors="coerce") > 0:
        parts.append(f"风险扣分{r.get('risk_penalty_points')}")
    state = str(r.get("technical_state", ""))
    if any(k in state for k in ["过热", "滞涨", "破位", "高位回撤", "低流动性"]):
        parts.append("技术状态含风险信号")
    if not parts:
        parts.append("等待更强成交确认或回踩买点")
    return "；".join(parts)


def add_quote_status(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = "latest_quote_date" if "latest_quote_date" in out.columns else pick_col(out, ["last_quote_date", "quote_last_update"])
    source = out.get("quote_source", pd.Series("unknown", index=out.index)).astype(str)
    if date_col:
        d = pd.to_datetime(out[date_col], errors="coerce")
    else:
        d = pd.Series(pd.NaT, index=out.index)
    today = pd.Timestamp.today().normalize()
    lag = (today - d.dt.normalize()).dt.days
    status = np.select([d.isna(), lag <= 3, lag <= 10, lag > 10], ["无行情", "已更新", "需复核", "未更新"], default="需复核")
    reason = []
    for s, l, src in zip(status, lag, source):
        if s == "无行情": reason.append("未找到有效行情或代码映射失败")
        elif "free" in src.lower() or "yahoo" in src.lower() or "stooq" in src.lower(): reason.append("免费源兜底，不作为正式评分主源")
        elif s == "已更新": reason.append("iFind行情/快照在可接受窗口内")
        else: reason.append("最近行情日距今天较远，需检查停牌、权限或更新批次")
    out["quote_update_status_cn"] = status
    out["quote_update_status_en"] = status
    out["quote_update_reason_cn"] = reason
    out["quote_update_reason_en"] = reason
    if date_col and date_col != "last_quote_date":
        out["last_quote_date"] = d.dt.strftime("%Y-%m-%d")
    return out


def build_action_list(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return pd.DataFrame()
    out = df.copy()
    listed = pd.to_numeric(out.get("listed_days", pd.Series(np.nan, index=out.index)), errors="coerce").notna()
    score = to_num(out.get("current_stage_score", pd.Series(np.nan, index=out.index)))
    rows = []
    for _, r in out.iterrows():
        code = r.get("code", "")
        name = r.get("name", r.get("简称", ""))
        sc = pd.to_numeric(pd.Series([r.get("current_stage_score")]), errors="coerce").iloc[0]
        if pd.notna(r.get("listed_days")):
            if pd.notna(sc) and sc >= 75:
                cat = "二级趋势确认"
                action = r.get("secondary_action_cn", "可参与；等回踩或成交确认")
                priority = "高"
            elif pd.notna(sc) and sc >= 60:
                cat = "等待二级买点"
                action = r.get("secondary_action_cn", "等待买点")
                priority = "中"
            else:
                cat = "破发/弱势回避"
                action = r.get("secondary_action_cn", "回避或仅观察")
                priority = "低"
        else:
            cat = "一级项目跟踪"
            action = r.get("primary_action_cn", r.get("a1_action_cn", "建档/等待关键资料"))
            priority = "中"
        rows.append({"priority_cn": priority, "action_category_cn": cat, "code": code, "name": name, "current_stage_score": sc, "trigger_reason_cn": r.get("secondary_score_explain_cn", r.get("a1_score_sources", "")), "next_action_cn": action, "quote_source": r.get("quote_source", ""), "last_quote_date": r.get("last_quote_date", r.get("latest_quote_date", ""))})
    return pd.DataFrame(rows).sort_values(["priority_cn", "current_stage_score"], ascending=[True, False])


def build_primary_secondary(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.to_numeric(df.get("listed_days", pd.Series(np.nan, index=df.index)), errors="coerce")
    primary = df[days.isna()].copy()
    secondary = df[days.notna()].copy()
    return primary, secondary


def main() -> None:
    base = load_base()
    base = add_listing_fields(base)
    # Merge all freshly derived outputs, replacing stale columns.
    for fname in ["ipo_post_listing_paths.csv", "ipo_technical_signals.csv", "lockup_risk_model.csv"]:
        base = merge_by_code(base, read_csv(DEPLOY / fname))
    base = clean_1970_dates(base)
    base = add_listing_fields(base)
    base = add_quote_status(base)
    base = add_secondary_score(base)
    base["data_pipeline_version"] = "v11_ifind_dag"
    base["final_table_rebuilt_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    primary, secondary = build_primary_secondary(base)
    actions = build_action_list(base)
    write_csv(base, DEPLOY / "ipo_investment_decision_scored.csv")
    write_csv(primary, DEPLOY / "primary_market_decision.csv")
    write_csv(secondary, DEPLOY / "secondary_market_decision.csv")
    write_csv(actions, DEPLOY / "today_action_list.csv")
    lineage = pd.DataFrame([
        {"table": "ifind_daily_quotes_raw.csv", "rows": len(read_csv(DEPLOY / "ifind_daily_quotes_raw.csv"))},
        {"table": "ifind_close_snapshot_raw.csv", "rows": len(read_csv(DEPLOY / "ifind_close_snapshot_raw.csv"))},
        {"table": "ipo_post_listing_paths.csv", "rows": len(read_csv(DEPLOY / "ipo_post_listing_paths.csv"))},
        {"table": "ipo_technical_signals.csv", "rows": len(read_csv(DEPLOY / "ipo_technical_signals.csv"))},
        {"table": "lockup_risk_model.csv", "rows": len(read_csv(DEPLOY / "lockup_risk_model.csv"))},
        {"table": "ipo_investment_decision_scored.csv", "rows": len(base)},
        {"table": "secondary_market_decision.csv", "rows": len(secondary)},
        {"table": "today_action_list.csv", "rows": len(actions)},
    ])
    lineage["rebuilt_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_csv(lineage, DEPLOY / "data_lineage_last_run.csv")
    print(f"Rebuilt final tables: scored={len(base)}, primary={len(primary)}, secondary={len(secondary)}, actions={len(actions)}")


if __name__ == "__main__":
    main()
