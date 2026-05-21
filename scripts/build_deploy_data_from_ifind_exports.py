from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

import pandas as pd

NA_VALUES = {"--", "---", "", "nan", "NaN", "None", "null", "NULL", "不适用", "-", "　"}


def read_csv_smart(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    last_error: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception as exc:  # pragma: no cover
            last_error = exc
    raise RuntimeError(f"无法读取文件：{path}，最后错误：{last_error}")


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    for c in df.columns:
        df[c] = df[c].astype("string").str.strip()
        df[c] = df[c].replace(list(NA_VALUES), pd.NA)
    # iFind 有些导出会把中文二级表头作为第一行重复出现，这里自动删掉。
    if len(df) and any(str(x).strip() in {"代码", "序号", "同花顺代码"} for x in df.iloc[0].astype(str).tolist()[:5]):
        df = df.iloc[1:].reset_index(drop=True)
    return df


def pick(df: pd.DataFrame, col: str, default=pd.NA) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index, dtype="object")


def pick_first(df: pd.DataFrame, candidates: list[str], default=pd.NA) -> pd.Series:
    out = pd.Series([default] * len(df), index=df.index, dtype="object")
    for col in candidates:
        if col in df.columns:
            out = out.fillna(df[col])
    return out


def to_num(s: pd.Series) -> pd.Series:
    x = s.astype("string").fillna("")
    x = x.str.replace(",", "", regex=False)
    x = x.str.replace("倍", "", regex=False)
    x = x.str.replace("%", "", regex=False)
    x = x.str.replace("超购于", "", regex=False)
    x = x.str.extract(r"([-+]?\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(x, errors="coerce")


def to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")


def normalize_code(s: pd.Series) -> pd.Series:
    def one(v):
        if pd.isna(v):
            return pd.NA
        t = str(v).strip().upper()
        if not t:
            return pd.NA
        t = t.replace(" ", "")
        if t.endswith(".HK"):
            base = t[:-3]
            if base.startswith("H"):
                return f"{base}.HK"
            digits = re.sub(r"\D", "", base)
            if digits:
                return f"{digits.zfill(4)}.HK"
            return t
        if t.startswith("H") and t[1:].isdigit():
            return f"{t}.HK"
        digits = re.sub(r"\D", "", t)
        if digits:
            return f"{digits.zfill(4)}.HK"
        return t
    return s.map(one)


def normalize_master(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick(df, "p05310_f001"))
    out["name"] = pick(df, "p05310_f002")
    out["listing_date"] = to_date(pick(df, "p05310_f034")).fillna(to_date(pick(df, "p05310_f033")))
    out["prospectus_date"] = to_date(pick(df, "p05310_f003")).fillna(to_date(pick(df, "p05310_f029")))
    out["offer_start_date"] = to_date(pick(df, "p05310_f029"))
    out["offer_end_date"] = to_date(pick(df, "p05310_f030"))
    out["pricing_date"] = to_date(pick(df, "p05310_f031"))
    out["allotment_date"] = to_date(pick(df, "p05310_f032"))
    out["board"] = pick(df, "p05310_f053")
    out["offering_type"] = pick(df, "p05310_f004")
    out["is_latest"] = pick(df, "p05310_f054")
    out["offer_price_low"] = to_num(pick(df, "p05310_f009"))
    out["offer_price_high"] = to_num(pick(df, "p05310_f008"))
    out["issue_price"] = to_num(pick(df, "p05310_f010"))
    out["board_lot"] = to_num(pick(df, "p05310_f011"))
    out["market_cap_hkdm"] = to_num(pick(df, "p05310_f012"))
    out["offer_shares"] = to_num(pick(df, "p05310_f013"))
    out["public_offer_shares"] = to_num(pick(df, "p05310_f015"))
    out["placing_shares"] = to_num(pick(df, "p05310_f017"))
    out["gross_proceeds_hkd"] = to_num(pick(df, "p05310_f023"))
    out["net_proceeds_hkd"] = to_num(pick(df, "p05310_f025"))
    out["proceeds_currency"] = pick(df, "p05310_f039")
    out["use_of_proceeds"] = pick(df, "p05310_f049")
    out["public_subscription_multiple"] = to_num(pick(df, "p05310_f027"))
    out["international_subscription_multiple"] = to_num(pick(df, "p05310_f052"))
    out["valuation_metric"] = to_num(pick(df, "p05310_f050"))
    out["source_table"] = "首发信息一览"
    out = out.dropna(how="all")
    out = out[out["code"].notna() | out["name"].notna()]
    return out


def normalize_listing_application(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick_first(df, ["p04920_f001", "同花顺代码", "代码"]))
    out["temp_code"] = out["code"]
    out["name"] = pick_first(df, ["p04920_f002", "证券简称", "名称", "发行人"])
    out["application_date"] = to_date(pick_first(df, ["p04920_f003", "申请日期"]))
    out["application_status"] = pick_first(df, ["p04920_f004", "申请状态"])
    out["status_update_date"] = to_date(pick_first(df, ["p04920_f005", "申请状态更新日期"]))
    out["hearing_date"] = to_date(pick_first(df, ["p04920_f006", "通过聆讯日期"]))
    out["listing_date"] = to_date(pick_first(df, ["p04920_f037", "上市日期"]))
    out["first_application_date"] = to_date(pick_first(df, ["p04920_f007", "首次申请日期"]))
    out["board"] = pick_first(df, ["p04920_f008", "拟上市板块"])
    out["sponsor"] = pick_first(df, ["p04920_f042", "保荐人"])
    out["overall_coordinator"] = pick_first(df, ["p04920_f011", "整体协调人"])
    out["company_chinese_name"] = pick_first(df, ["p04920_f021", "公司中文名"])
    out["company_english_name"] = pick_first(df, ["p04920_f022", "公司英文名"])
    out["website"] = pick_first(df, ["p04920_f026", "网址"])
    out["fiscal_year_end"] = pick_first(df, ["p04920_f030", "财政年度截止"])
    out["business_scope"] = pick_first(df, ["p04920_f032", "经营范围"])
    out["company_profile"] = pick_first(df, ["p04920_f033", "公司简介"])
    # 上市申请导出中 p04920_f034 通常是行业名称；p04920_f038 更像内部ID，不作为行业展示。
    out["industry_level_1"] = pick_first(df, ["p04920_f034", "行业", "一级行业"])
    out["industry_level_2"] = pick_first(df, ["p04920_f039", "二级行业"])
    out["source_table"] = "上市申请一览"
    out = out[out["code"].notna() | out["name"].notna()]
    return out


def normalize_ballot(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick(df, "p04477_f001"))
    out["name"] = pick(df, "p04477_f002")
    out["listing_date"] = to_date(pick(df, "p04477_f003"))
    out["total_applicants"] = to_num(pick(df, "p04477_f004"))
    out["valid_applicants"] = to_num(pick(df, "p04477_f005"))
    out["subscribed_shares"] = to_num(pick(df, "p04477_f012"))
    out["public_subscription_multiple_ballot"] = to_num(pick(df, "p04477_f020"))
    out["one_lot_success_rate_pct"] = to_num(pick(df, "p04477_f021"))
    out["industry_level_1"] = pick(df, "p04477_f022")
    out["industry_level_2"] = pick(df, "p04477_f023")
    out["source_table"] = "IPO打新中签结果"
    out = out[out["code"].notna() | out["name"].notna()]
    return out


def normalize_cornerstone(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick(df, "p05309_f001"))
    out["name"] = pick(df, "p05309_f002")
    out["listing_date"] = to_date(pick(df, "p05309_f003"))
    out["prospectus_date"] = to_date(pick(df, "p05309_f004"))
    out["cornerstone_flag"] = pick(df, "p05309_f017")
    out["investor_name"] = pick(df, "p05309_f005")
    out["investor_description"] = pick(df, "p05309_f018")
    out["ultimate_owner"] = pick(df, "p05309_f006")
    out["ultimate_owner_description"] = pick(df, "p05309_f019")
    out["invested_amount_hkd"] = to_num(pick(df, "p05309_f008"))
    out["currency"] = pick(df, "p05309_f011")
    out["allocation_pct"] = to_num(pick(df, "p05309_f014"))
    out["lockup_months"] = to_num(pick(df, "p05309_f010"))
    out["lockup_end_date"] = to_date(pick(df, "p05309_f015"))
    out["industry"] = pick(df, "p05309_f012")
    out["sub_industry"] = pick(df, "p05309_f013")
    out["source_table"] = "基石投资者"
    out = out[out["code"].notna() | out["name"].notna()]
    if out.empty:
        summary = pd.DataFrame(columns=["code", "cornerstone_count", "cornerstone_amount_hkd", "cornerstone_top_names", "cornerstone_quality_score"])
    else:
        def top_names(s: pd.Series) -> str:
            return "；".join([str(x) for x in s.dropna().unique().tolist()[:5]])
        def quality(names: pd.Series) -> float:
            text = " ".join(names.dropna().astype(str).tolist()).lower()
            score = 50.0
            strong_words = ["腾讯", "阿里", "京东", "美团", "高瓴", "淡马锡", "temasek", "qatar", "gic", "blackrock", "国资", "政府", "产业", "保险", "银行", "基金"]
            for w in strong_words:
                if w.lower() in text:
                    score += 5
            return min(score, 95.0)
        summary = out.groupby("code", dropna=False).agg(
            name=("name", "first"),
            cornerstone_count=("investor_name", "nunique"),
            cornerstone_amount_hkd=("invested_amount_hkd", "sum"),
            cornerstone_top_names=("investor_name", top_names),
            cornerstone_quality_score=("investor_name", quality),
            lockup_end_date=("lockup_end_date", "max"),
        ).reset_index()
    return out, summary


def normalize_margin(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick(df, "p05551_f001"))
    out["name"] = pick(df, "jydm_mc")
    out["listing_date"] = to_date(pick(df, "p05551_f002"))
    out["record_date"] = to_date(pick(df, "p05551_f003"))
    out["margin_amount_hkd"] = to_num(pick(df, "p05551_f004"))
    out["public_offer_amount_hkd"] = to_num(pick(df, "p05551_f005"))
    out["margin_multiple"] = to_num(pick(df, "p05551_f006"))
    out["margin_over_text"] = pick(df, "p05551_f007")
    out["currency"] = pick(df, "p05551_f008")
    out["source_table"] = "孖展数据"
    out = out[out["code"].notna() | out["name"].notna()]
    return out


def normalize_dark_pool(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["code"] = normalize_code(pick_first(df, ["代码", "stock_code"]))
    out["name"] = pick_first(df, ["名称", "name"])
    out["allotment_date"] = to_date(pick_first(df, ["中签公布日期"]))
    out["gray_date"] = to_date(pick_first(df, ["暗盘日期"]))
    out["listing_date"] = to_date(pick_first(df, ["上市日期"]))
    out["issue_price"] = to_num(pick_first(df, ["发行价格"]))
    out["gray_open"] = to_num(pick_first(df, ["暗盘行情"]))
    out["gray_open_ret_pct"] = to_num(pick_first(df, ["暗盘行情.1"]))
    out["gray_close"] = to_num(pick_first(df, ["暗盘行情.2"]))
    out["gray_close_ret_pct"] = to_num(pick_first(df, ["暗盘行情.3"]))
    out["gray_high"] = to_num(pick_first(df, ["暗盘行情.4"]))
    out["gray_low"] = to_num(pick_first(df, ["暗盘行情.5"]))
    out["gray_avg"] = to_num(pick_first(df, ["暗盘行情.6"]))
    out["gray_trade_count"] = to_num(pick_first(df, ["暗盘行情.7"]))
    out["gray_volume_10k_shares"] = to_num(pick_first(df, ["暗盘行情.8"]))
    out["gray_amount_10k_hkd"] = to_num(pick_first(df, ["暗盘行情.9"]))
    out["d1_open"] = to_num(pick_first(df, ["首日行情"]))
    out["d1_open_ret_pct"] = to_num(pick_first(df, ["首日行情.1"]))
    out["d1_close"] = to_num(pick_first(df, ["首日行情.2"]))
    out["d1_close_ret_pct"] = to_num(pick_first(df, ["首日行情.3"]))
    out["d1_avg"] = to_num(pick_first(df, ["首日行情.4"]))
    out["currency"] = pick_first(df, ["交易货币"])
    out["source_table"] = "IPO暗盘行情"
    out = out[out["code"].notna() | out["name"].notna()]
    return out


def normalize_underwriters(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["institution"] = pick(df, "p03412_f001")
    out["code"] = normalize_code(pick(df, "p03412_f002"))
    out["name"] = pick(df, "p03412_f003")
    out["role"] = pick(df, "p03412_f004")
    out["listing_date"] = to_date(pick(df, "p03412_f005"))
    out["participation_pct"] = to_num(pick(df, "p03412_f006"))
    out["industry_level_1"] = pick(df, "p03412_f007")
    out["industry_level_2"] = pick(df, "p03412_f008")
    out["source_table"] = "承销团参与度"
    out = out[out["code"].notna() | out["institution"].notna()]
    return out


def normalize_bookrunners(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_frame(df)
    out = pd.DataFrame(index=df.index)
    out["institution"] = pick(df, "p03414_f001")
    out["code"] = normalize_code(pick(df, "p03414_f002"))
    out["name"] = pick(df, "p03414_f003")
    out["listing_date"] = to_date(pick(df, "p03414_f004"))
    out["issue_price_or_score"] = to_num(pick(df, "p03414_f005"))
    out["industry_level_1"] = pick(df, "p03414_f006")
    out["industry_level_2"] = pick(df, "p03414_f007")
    out["source_table"] = "账簿管理人"
    out = out[out["code"].notna() | out["institution"].notna()]
    return out


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def find_file(input_dir: Path, keywords: list[str]) -> Optional[Path]:
    files = list(input_dir.glob("*.csv"))
    for p in files:
        name = p.name
        if all(k in name for k in keywords):
            return p
    return None



def coalesce_suffix_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Merge pandas _x/_y columns back into canonical names after left joins."""
    df = df.copy()
    bases = sorted({c[:-2] for c in df.columns if c.endswith('_x') or c.endswith('_y')})
    for b in bases:
        cols = [c for c in [b, f'{b}_x', f'{b}_y'] if c in df.columns]
        if not cols:
            continue
        out = df[cols[0]]
        for c in cols[1:]:
            out = out.fillna(df[c])
        df[b] = out
        for c in cols:
            if c != b and c in df.columns:
                df = df.drop(columns=[c])
    return df

def merge_first(pool: pd.DataFrame, addon: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    keep = [c for c in cols if c in addon.columns]
    if not keep or "code" not in addon.columns:
        return pool
    return pool.merge(addon[keep].drop_duplicates("code"), on="code", how="left")


def build(input_dir: str | Path, outdir: str | Path) -> None:
    input_dir = Path(input_dir)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    inventory: list[list[object]] = []

    paths = {
        "application": find_file(input_dir, ["上市申请"]),
        "master": find_file(input_dir, ["首发信息"]),
        "ballot": find_file(input_dir, ["打新", "中签"]),
        "cornerstone": find_file(input_dir, ["基石"]),
        "margin": find_file(input_dir, ["孖展"]),
        "dark": find_file(input_dir, ["暗盘"]),
        "underwriters": find_file(input_dir, ["承销团"]),
        "bookrunners": find_file(input_dir, ["账簿管理"]),
    }

    app = master = ballot = cornerstone = cs_summary = margin = dark = underwriters = bookrunners = pd.DataFrame()

    if paths["application"]:
        raw = read_csv_smart(paths["application"])
        app = normalize_listing_application(raw)
        write_csv(app, outdir / "ipo_listing_applications.csv")
        inventory.append(["上市申请一览", paths["application"].name, len(raw), len(app), "已接入"])

    if paths["master"]:
        raw = read_csv_smart(paths["master"])
        master = normalize_master(raw)
        write_csv(master, outdir / "ipo_master_ifind_normalized.csv")
        inventory.append(["首发信息一览", paths["master"].name, len(raw), len(master), "已接入"])

    if paths["ballot"]:
        raw = read_csv_smart(paths["ballot"])
        ballot = normalize_ballot(raw)
        write_csv(ballot, outdir / "ipo_ballot_results.csv")
        inventory.append(["IPO打新中签结果", paths["ballot"].name, len(raw), len(ballot), "已接入"])

    if paths["cornerstone"]:
        raw = read_csv_smart(paths["cornerstone"])
        cornerstone, cs_summary = normalize_cornerstone(raw)
        write_csv(cornerstone, outdir / "ipo_cornerstone_investors.csv")
        write_csv(cs_summary, outdir / "ipo_cornerstone_summary.csv")
        inventory.append(["基石投资者", paths["cornerstone"].name, len(raw), len(cornerstone), "已接入"])

    if paths["margin"]:
        raw = read_csv_smart(paths["margin"])
        margin = normalize_margin(raw)
        write_csv(margin, outdir / "ipo_margin_data.csv")
        inventory.append(["孖展数据", paths["margin"].name, len(raw), len(margin), "已接入"])

    if paths["dark"]:
        raw = read_csv_smart(paths["dark"])
        dark = normalize_dark_pool(raw)
        write_csv(dark, outdir / "ipo_dark_pool.csv")
        inventory.append(["IPO暗盘行情", paths["dark"].name, len(raw), len(dark), "已接入"])

    if paths["underwriters"]:
        raw = read_csv_smart(paths["underwriters"])
        underwriters = normalize_underwriters(raw)
        write_csv(underwriters, outdir / "ipo_underwriter_participation.csv")
        inventory.append(["承销团参与度", paths["underwriters"].name, len(raw), len(underwriters), "已接入"])

    if paths["bookrunners"]:
        raw = read_csv_smart(paths["bookrunners"])
        bookrunners = normalize_bookrunners(raw)
        write_csv(bookrunners, outdir / "ipo_bookrunner_details.csv")
        inventory.append(["账簿管理人", paths["bookrunners"].name, len(raw), len(bookrunners), "已接入"])

    # 主表：先用首发信息，再追加上市申请中但还没进入首发信息的临时代码公司。
    if not master.empty:
        pool = master.copy()
    elif not app.empty:
        pool = pd.DataFrame(columns=["code", "name"])
    else:
        pool = pd.DataFrame()

    if not app.empty:
        app_pool = app.copy()
        app_pool["lifecycle_stage"] = "A1/上市申请中"
        if not pool.empty:
            missing = app_pool[~app_pool["code"].astype(str).isin(pool["code"].astype(str))]
            # 补齐首发主表中没有的字段。
            for c in pool.columns:
                if c not in missing.columns:
                    missing[c] = pd.NA
            for c in missing.columns:
                if c not in pool.columns:
                    pool[c] = pd.NA
            pool = pd.concat([pool, missing[pool.columns]], ignore_index=True)
        else:
            pool = app_pool
        # 上市申请字段合并到主表。
        merge_cols = ["code", "temp_code", "application_date", "application_status", "status_update_date", "hearing_date", "first_application_date", "sponsor", "overall_coordinator", "business_scope", "company_profile", "industry_level_1", "industry_level_2"]
        pool = merge_first(pool, app[merge_cols], merge_cols)
        pool = coalesce_suffix_columns(pool)

    if not pool.empty:
        if not ballot.empty:
            keep = ["code", "public_subscription_multiple_ballot", "one_lot_success_rate_pct", "industry_level_1", "industry_level_2", "total_applicants"]
            pool = merge_first(pool, ballot, keep)
            if "public_subscription_multiple" in pool.columns and "public_subscription_multiple_ballot" in pool.columns:
                pool["public_subscription_multiple"] = pd.to_numeric(pool["public_subscription_multiple"], errors="coerce").fillna(pd.to_numeric(pool["public_subscription_multiple_ballot"], errors="coerce"))
        if not cs_summary.empty:
            pool = pool.merge(cs_summary, on="code", how="left", suffixes=("", "_cs"))
        if not margin.empty:
            mg = margin.sort_values("record_date").groupby("code", as_index=False).tail(1)
            pool = pool.merge(mg[["code", "record_date", "margin_amount_hkd", "margin_multiple", "margin_over_text"]], on="code", how="left")
        if not dark.empty:
            dk_cols = ["code", "gray_date", "gray_open_ret_pct", "gray_close_ret_pct", "gray_amount_10k_hkd", "d1_open_ret_pct", "d1_close_ret_pct"]
            pool = pool.merge(dark[[c for c in dk_cols if c in dark.columns]].drop_duplicates("code"), on="code", how="left")
        if not underwriters.empty:
            uw = underwriters.groupby("code").agg(
                underwriter_count=("institution", "nunique"),
                top_underwriters=("institution", lambda s: "；".join(s.dropna().astype(str).unique().tolist()[:5])),
            ).reset_index()
            pool = pool.merge(uw, on="code", how="left")
        if not bookrunners.empty:
            br = bookrunners.groupby("code").agg(
                bookrunner_count=("institution", "nunique"),
                top_bookrunners=("institution", lambda s: "；".join(s.dropna().astype(str).unique().tolist()[:5])),
            ).reset_index()
            pool = pool.merge(br, on="code", how="left")

        pool = coalesce_suffix_columns(pool)
        today = pd.Timestamp.today().normalize()
        ld = pd.to_datetime(pool.get("listing_date"), errors="coerce")
        pool["lifecycle_stage"] = pool.get("lifecycle_stage", pd.Series([pd.NA] * len(pool))).fillna("已上市")
        pool.loc[ld.isna() & pool.get("application_status", pd.Series([pd.NA] * len(pool))).notna(), "lifecycle_stage"] = "A1/上市申请中"
        pool.loc[ld.notna() & (ld > today), "lifecycle_stage"] = "招股/待上市"
        pool.loc[ld.notna() & (ld <= today), "lifecycle_stage"] = "已上市"

        sub = pd.to_numeric(pool.get("public_subscription_multiple"), errors="coerce")
        one = pd.to_numeric(pool.get("one_lot_success_rate_pct"), errors="coerce")
        margin_mult = pd.to_numeric(pool.get("margin_multiple"), errors="coerce")
        cs_quality = pd.to_numeric(pool.get("cornerstone_quality_score"), errors="coerce")
        cs_count = pd.to_numeric(pool.get("cornerstone_count"), errors="coerce")
        book_count = pd.to_numeric(pool.get("bookrunner_count"), errors="coerce")
        under_count = pd.to_numeric(pool.get("underwriter_count"), errors="coerce")
        inter_mult = pd.to_numeric(pool.get("international_subscription_multiple"), errors="coerce")
        gray_ret = pd.to_numeric(pool.get("gray_close_ret_pct"), errors="coerce")
        d1_ret = pd.to_numeric(pool.get("d1_close_ret_pct"), errors="coerce")

        score = pd.Series(42.0, index=pool.index)
        score += sub.clip(0, 2000).fillna(0) / 2000 * 16
        score += margin_mult.clip(0, 1000).fillna(0) / 1000 * 8
        score += inter_mult.clip(0, 30).fillna(0) / 30 * 10
        score += cs_quality.fillna(50).sub(50).clip(0, 45) / 45 * 10
        score += cs_count.clip(0, 8).fillna(0) / 8 * 5
        score += book_count.clip(0, 8).fillna(0) / 8 * 4
        score += under_count.clip(0, 12).fillna(0) / 12 * 3
        score += gray_ret.clip(-20, 80).fillna(0) / 80 * 8
        score += d1_ret.clip(-20, 80).fillna(0) / 80 * 5
        score -= one.fillna(20).rsub(20).clip(0, 20) / 20 * 3
        # A1阶段数据少，分数只代表预筛，不与已定价IPO完全可比。
        score = score.where(pool["lifecycle_stage"].ne("A1/上市申请中"), score.clip(0, 65))
        pool["pre_listing_score"] = score.clip(0, 100).round(1)

        def tier(x: float, stage: str) -> str:
            if stage == "A1/上市申请中":
                return "A1 预研"
            if pd.isna(x):
                return "C 等待补数据"
            if x >= 75:
                return "A 高优先"
            if x >= 62:
                return "B 重点观察"
            if x >= 50:
                return "C 等待触发"
            return "D 回避/仅跟踪"
        pool["decision_tier"] = [tier(x, stg) for x, stg in zip(pool["pre_listing_score"], pool["lifecycle_stage"])]

        def rec(row) -> str:
            stg = row.get("lifecycle_stage", "")
            tier_ = row.get("decision_tier", "")
            if stg == "A1/上市申请中":
                return "建立预研档案；重点看行业景气、A/H估值锚、保荐人质量，等待聆讯与招股结构。"
            if "A" in tier_:
                return "一级重点研究；若估值与配售结构合理，可争取额度/锚定，并准备二级回踩买点。"
            if "B" in tier_:
                return "保持交易观察；关注定价、配发结果、暗盘与首日承接，不建议无条件追高。"
            if "C" in tier_:
                return "等待补充字段或价格触发；更适合二级深V/回踩确认后再行动。"
            return "暂不主动参与；仅在明显低估或上市后资金重新进场时复盘。"
        pool["model_recommendation"] = pool.apply(rec, axis=1)

        def tags(row) -> str:
            res = []
            if row.get("lifecycle_stage") == "A1/上市申请中":
                res.append("A1预研")
            if pd.notna(row.get("public_subscription_multiple")) and row.get("public_subscription_multiple") >= 1000:
                res.append("公开超购极热")
            if pd.notna(row.get("margin_multiple")) and row.get("margin_multiple") >= 500:
                res.append("孖展拥挤")
            if pd.notna(row.get("one_lot_success_rate_pct")) and row.get("one_lot_success_rate_pct") <= 5:
                res.append("一手中签率低")
            if pd.notna(row.get("cornerstone_count")) and row.get("cornerstone_count") >= 5:
                res.append("基石阵容较多")
            if pd.notna(row.get("gray_close_ret_pct")) and row.get("gray_close_ret_pct") < 0:
                res.append("暗盘转弱")
            if pd.notna(row.get("gray_close_ret_pct")) and row.get("gray_close_ret_pct") >= 30:
                res.append("暗盘强势")
            if not res:
                res.append("等待更多结构数据")
            return "；".join(res)
        pool["risk_tags"] = pool.apply(tags, axis=1)
        write_csv(pool, outdir / "ipo_decision_pool.csv")

        # 兼容旧版App的A1表/offer表。
        a1_cols = ["code", "temp_code", "name", "application_date", "application_status", "hearing_date", "listing_date", "prospectus_date", "board", "sponsor", "overall_coordinator", "lifecycle_stage", "decision_tier", "pre_listing_score", "model_recommendation", "risk_tags", "business_scope", "company_profile"]
        a1 = pool[[c for c in a1_cols if c in pool.columns]].copy()
        a1 = a1.rename(columns={"code": "stock_code", "name": "issuer_name"})
        write_csv(a1, outdir / "a1_ipo_pipeline.csv")
        offer_cols = ["code", "name", "listing_date", "issue_price", "offer_price_low", "offer_price_high", "public_subscription_multiple", "international_subscription_multiple", "one_lot_success_rate_pct", "cornerstone_count", "cornerstone_amount_hkd", "margin_multiple", "gray_close_ret_pct", "d1_close_ret_pct", "decision_tier", "pre_listing_score"]
        write_csv(pool[[c for c in offer_cols if c in pool.columns]], outdir / "ipo_offer_results.csv")

    # 数据完整度
    expected = [
        "上市申请一览", "首发信息一览", "IPO打新中签结果", "IPO回拨统计", "基石投资者", "首发中介机构/承销团", "账簿管理人", "孖展数据", "IPO暗盘行情", "上市后0-180D行情"
    ]
    inv = pd.DataFrame(inventory, columns=["source_name", "file_name", "raw_rows", "normalized_rows", "status"])
    for e in expected:
        if inv.empty or not inv["source_name"].str.contains(e.split("/")[0], na=False).any():
            inv.loc[len(inv)] = [e, "", 0, 0, "未接入"]
    write_csv(inv, outdir / "data_inventory.csv")
    print(f"Done. Output dir: {outdir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize iFind GUI CSV exports into deploy_data/*.csv")
    parser.add_argument("--input-dir", default="ifind_exports", help="Directory containing iFind CSV exports")
    parser.add_argument("--outdir", default="deploy_data", help="Output directory")
    args = parser.parse_args()
    build(args.input_dir, args.outdir)
