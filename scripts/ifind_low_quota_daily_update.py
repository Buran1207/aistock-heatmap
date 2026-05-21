from __future__ import annotations

"""
iFind 16:30 低额度日度更新引擎。

设计目标：
1) 不需要 PyCharm；双击 bat 即可运行。
2) 尽量少消耗 iFind 额度：所有股票类API统一先生成2024+ IPO股票池，再分批取数；行情走增量，静态表走近端窗口。
3) 原始 iFind 结果先落地缓存；任何失败都保留昨日数据并写日志。
4) 字段映射不确定时不强行覆盖主数据，先输出 raw inventory/status，避免误写。

常用命令：
    python scripts/ifind_low_quota_daily_update.py --mode api --low-quota --build-signals
    python scripts/ifind_low_quota_daily_update.py --mode dry-run
    python scripts/ifind_low_quota_daily_update.py --mode offline --input-dir ifind_exports --build-signals
"""

import argparse
import ast
import configparser
import json
import os
import re
import subprocess
import sys
import traceback
from io import StringIO
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "ifind_update_config.json"
COMMANDS_PATH = ROOT / "config" / "ifind_api_commands.txt"
CREDENTIALS_PATH = ROOT / "config" / "local_ifind_credentials.ini"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950")
NA_VALUES = {"", "--", "---", "nan", "NaN", "None", "null", "NULL", "不适用", "-", "　"}


@dataclass
class RunContext:
    mode: str
    today: date
    low_quota: bool
    dry_run: bool
    config: dict[str, Any]
    cache_dir: Path
    deploy_dir: Path
    export_dir: Path
    log_dir: Path
    log_path: Path


def read_csv_smart(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    last_error: Exception | None = None
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
        except Exception as exc:
            last_error = exc
    # 某些 dry-run/中断场景可能留下不可解析的空壳文件；不让它阻断全流程。
    try:
        if path.stat().st_size < 10:
            return pd.DataFrame()
    except Exception:
        pass
    raise RuntimeError(f"无法读取 {path}: {last_error}")


def read_table_smart(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    return read_csv_smart(path)


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    for c in df.columns:
        try:
            df[c] = df[c].astype("string").str.strip().replace(list(NA_VALUES), pd.NA)
        except Exception:
            pass
    return df


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(ctx: RunContext, msg: str) -> None:
    line = f"[{now_str()}] {msg}"
    print(line)
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ctx.log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def load_sections() -> dict[str, str]:
    if not COMMANDS_PATH.exists():
        return {}
    text = COMMANDS_PATH.read_text(encoding="utf-8", errors="ignore")
    parts = re.split(r"【([^】]+)】", text)
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[title] = body.strip()
    return sections


def find_section_body(sections: dict[str, str], section_key: str) -> str:
    if not section_key:
        return ""
    normalized = section_key.strip()
    for title, body in sections.items():
        if normalized in title or title in normalized:
            return body
    # 兼容“【9 限售股解禁明细】”这类编号标题
    key_digits = "".join(ch for ch in normalized if ch.isdigit())
    for title, body in sections.items():
        if key_digits and title.startswith(key_digits):
            return body
    return ""


def extract_first_ths_call(section_body: str) -> tuple[str, tuple[Any, ...]] | None:
    """从超级命令文本中提取第一个 THS_* 调用。"""
    if not section_body:
        return None
    m = re.search(r"(THS_[A-Z]+)\((.*)\)", section_body, flags=re.S)
    if not m:
        return None
    func = m.group(1)
    args_text = m.group(2).strip()
    # 去掉命令后可能混入的下一节文本
    cut = re.search(r"\n【", args_text)
    if cut:
        args_text = args_text[: cut.start()].strip()
    try:
        args = ast.literal_eval("(" + args_text + ")")
        if not isinstance(args, tuple):
            args = (args,)
        return func, args
    except Exception:
        return None


def load_ifind_module():
    try:
        import iFinDPy  # type: ignore
        return iFinDPy
    except Exception:
        try:
            from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_DR, THS_HQ, THS_RQ, THS_BD  # type: ignore
            class M:
                pass
            m = M()
            m.THS_iFinDLogin = THS_iFinDLogin
            m.THS_iFinDLogout = THS_iFinDLogout
            m.THS_DR = THS_DR
            m.THS_HQ = THS_HQ
            m.THS_RQ = THS_RQ
            m.THS_BD = THS_BD
            return m
        except Exception:
            return None


def credentials() -> tuple[str | None, str | None]:
    user = os.environ.get("IFIND_USERNAME")
    pwd = os.environ.get("IFIND_PASSWORD")
    if user and pwd:
        return user, pwd
    if CREDENTIALS_PATH.exists():
        cfg = configparser.ConfigParser()
        cfg.read(CREDENTIALS_PATH, encoding="utf-8")
        user = cfg.get("ifind", "username", fallback=None)
        pwd = cfg.get("ifind", "password", fallback=None)
        if user and pwd and not user.startswith("YOUR_"):
            return user, pwd
    return None, None


def normalize_code(x: Any) -> str | None:
    if pd.isna(x):
        return None
    s = str(x).strip().upper().replace(" ", "")
    if not s or s in {"NAN", "NONE"}:
        return None
    if s.startswith("H"):
        # H开头多为临时代码，二级行情阶段不使用
        return None
    if s.endswith(".HK"):
        base = s[:-3]
    else:
        base = s
    # 0008_1.HK / 0090_2.HK 等为iFind衍生/特殊条目，不能把数字拼成0081.HK。
    if "_" in base:
        return None
    digits = "".join(ch for ch in base if ch.isdigit())
    if not digits or len(digits) > 4:
        return None
    return f"{digits.zfill(4)}.HK"


def guess_code_column(df: pd.DataFrame) -> str | None:
    candidates = ["code", "股票代码", "证券代码", "同花顺代码", "jydm", "jydm_mc", "thscode", "THSCODE", "代码", "p05310_f001", "p03764_f001", "p04477_f001", "p05551_f001"]
    for c in candidates:
        if c in df.columns:
            return c
    for c in df.columns:
        cs = str(c).lower()
        if "code" in cs or "jydm" in cs or "thscode" in cs:
            return c
    return None


def guess_date_column(df: pd.DataFrame, preferred: list[str] | None = None) -> str | None:
    preferred = preferred or []
    for c in preferred + ["listing_date", "上市日期", "交易日期", "date", "time", "tradeDate", "解禁日期"]:
        if c in df.columns:
            return c
    for c in df.columns:
        name = str(c).lower()
        if "date" in name or "日期" in str(c):
            return c
    return None


def get_ipo_code_pool(ctx: RunContext) -> list[str]:
    """优先从已有 deploy_data 读取 2024+ IPO 已上市股票池。"""
    paths = [
        ctx.deploy_dir / "ipo_investment_decision_scored.csv",
        ctx.deploy_dir / "ipo_decision_pool.csv",
        ctx.cache_dir / "ipo_master.csv",
    ]
    codes: set[str] = set()
    for p in paths:
        df = read_csv_smart(p)
        if df.empty:
            continue
        ccol = guess_code_column(df)
        if not ccol:
            continue
        temp = df.copy()
        lcol = guess_date_column(temp, ["listing_date", "上市日期"])
        if lcol:
            d = pd.to_datetime(temp[lcol], errors="coerce")
            temp = temp[d >= pd.Timestamp(ctx.config.get("ipo_start_date", "2024-01-01"))]
        for code in temp[ccol].dropna().map(normalize_code).dropna().tolist():
            codes.add(code)
    return sorted(codes)


def replace_date_param(param: str, key: str, value: str) -> str:
    if not param:
        return param
    if re.search(rf"{re.escape(key)}=\d{{8}}", param):
        return re.sub(rf"{re.escape(key)}=\d{{8}}", f"{key}={value}", param)
    if re.search(rf"{re.escape(key)}=\d{{4}}-\d{{2}}-\d{{2}}", param):
        return re.sub(rf"{re.escape(key)}=\d{{4}}-\d{{2}}-\d{{2}}", f"{key}={value}", param)
    sep = ";" if param and not param.endswith(";") else ""
    return param + sep + f"{key}={value}"


def update_dr_args_for_window(args: tuple[Any, ...], start_yyyymmdd: str, end_yyyymmdd: str) -> tuple[Any, ...]:
    if len(args) < 2 or not isinstance(args[1], str):
        return args
    param = args[1]
    for k in ("sdate", "iv_sdate"):
        param = replace_date_param(param, k, start_yyyymmdd)
    for k in ("edate", "iv_edate"):
        param = replace_date_param(param, k, end_yyyymmdd)
    return (args[0], param, *args[2:])


def _listlike_to_df(data: Any, columns: list[str] | None = None) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame()
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, dict):
        return pd.DataFrame(data)
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return pd.DataFrame()
        for sep in [",", "\t", "|"]:
            try:
                df = pd.read_csv(StringIO(text), sep=sep)
                if df.shape[1] > 1:
                    return df
            except Exception:
                pass
        return pd.DataFrame({"value": [text]})
    try:
        df = pd.DataFrame(data)
        if columns and len(columns) == df.shape[1]:
            df.columns = columns
        return df
    except Exception:
        return pd.DataFrame()


def _parse_ths_object(res: Any) -> pd.DataFrame:
    """Robust parser for iFinDPy.THSData.

    iFind functions are inconsistent across THS_DR/HQ/RQ/BD and versions: `.data` may be
    a DataFrame, list, dict, matrix or empty while useful arrays live in other attributes.
    This parser tries common attribute names and falls back to an attribute dump rather than
    treating a valid THSData object as failure.
    """
    if isinstance(res, pd.DataFrame):
        return res
    if isinstance(res, dict):
        data = res.get("data", res)
        return _listlike_to_df(data)

    # Common data payloads.
    data = getattr(res, "data", None)
    cols = None
    for attr in ["fields", "indicators", "indicator", "columns", "col_name", "headers"]:
        v = getattr(res, attr, None)
        if v is not None:
            try:
                cols = list(v)
                break
            except Exception:
                pass
    df = _listlike_to_df(data, cols)
    if not df.empty:
        return df

    # Some THSData objects store tabular payload in `.tables` or `.table`.
    for attr in ["tables", "table", "Data", "dataset", "result"]:
        v = getattr(res, attr, None)
        df = _listlike_to_df(v, cols)
        if not df.empty:
            return df

    # Try reconstructing from code/time/indicator vectors if present.
    code_vec = None
    for attr in ["codes", "thscode", "thsCode", "code", "securityCode"]:
        v = getattr(res, attr, None)
        if v is not None:
            try:
                code_vec = list(v) if not isinstance(v, str) else [v]
                break
            except Exception:
                pass
    time_vec = None
    for attr in ["time", "times", "date", "dates"]:
        v = getattr(res, attr, None)
        if v is not None:
            try:
                time_vec = list(v) if not isinstance(v, str) else [v]
                break
            except Exception:
                pass
    if data is not None:
        try:
            arr = pd.DataFrame(data)
            if not arr.empty:
                if cols and len(cols) == arr.shape[1]:
                    arr.columns = cols
                if code_vec and len(code_vec) == len(arr):
                    arr.insert(0, "thscode", code_vec)
                if time_vec and len(time_vec) == len(arr):
                    arr.insert(0, "time", time_vec)
                return arr
        except Exception:
            pass

    # Last resort: expose scalar/list attributes for debugging instead of failing hard.
    attrs = {}
    for k in dir(res):
        if k.startswith("_") or k in {"data"}:
            continue
        try:
            v = getattr(res, k)
            if callable(v):
                continue
            if isinstance(v, (str, int, float, bool, list, tuple)):
                attrs[k] = str(v)[:500]
        except Exception:
            continue
    if attrs:
        return pd.DataFrame([attrs])
    return pd.DataFrame()


def call_ifind(ctx: RunContext, ifind: Any, func: str, args: tuple[Any, ...]) -> pd.DataFrame:
    if ctx.dry_run:
        return pd.DataFrame()
    f = getattr(ifind, func, None)
    if f is None:
        raise RuntimeError(f"iFind 模块中找不到 {func}")
    res = f(*args)
    df = _parse_ths_object(res)
    if df is None or df.empty:
        # include error fields if iFind returned them
        err = []
        for attr in ["errorcode", "errcode", "errmsg", "message"]:
            try:
                if hasattr(res, attr):
                    err.append(f"{attr}={getattr(res, attr)}")
            except Exception:
                pass
        raise RuntimeError(f"iFind返回空表或无法解析：{type(res)} {'; '.join(err)}")
    return clean_frame(df)


def merge_cache(old: pd.DataFrame, new: pd.DataFrame, policy: str) -> pd.DataFrame:
    if new.empty:
        return old
    if old.empty or policy in {"replace", "replace_daily", "replace_window"}:
        return new
    df = pd.concat([old, new], ignore_index=True)
    subset = []
    ccol = guess_code_column(df)
    dcol = guess_date_column(df)
    if ccol:
        subset.append(ccol)
    if dcol:
        subset.append(dcol)
    if subset:
        return df.drop_duplicates(subset=subset, keep="last")
    return df.drop_duplicates(keep="last")


def save_status(ctx: RunContext, rows: list[dict[str, Any]]) -> None:
    status = pd.DataFrame(rows)
    if not status.empty:
        status["updated_at"] = now_str()
    write_csv(status, ctx.deploy_dir / "data_update_status.csv")
    write_csv(status, ctx.log_dir / f"data_update_status_{ctx.today:%Y%m%d}.csv")


def fetch_static_source(ctx: RunContext, ifind: Any, name: str, section: str, policy: str, rows: list[dict[str, Any]], start: date, end: date) -> None:
    sections = load_sections()
    body = find_section_body(sections, section)
    call = extract_first_ths_call(body)
    out_path = ctx.cache_dir / f"{name}.csv"
    old = read_csv_smart(out_path)
    if call is None:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未找到API命令或暂缺"})
        return
    func, args = call
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    if func == "THS_DR":
        args = update_dr_args_for_window(args, start_s, end_s)
    if ctx.dry_run:
        rows.append({"source": name, "status": "dry_run", "rows": len(old), "new_rows": 0, "message": f"将调用{func}，窗口{start_s}至{end_s}；不写缓存"})
        return
    try:
        log(ctx, f"更新 {name}: {func}")
        new = call_ifind(ctx, ifind, func, args)
        merged = merge_cache(old, new, policy)
        write_csv(merged, out_path)
        deploy_raw_copy(ctx, name, merged)
        rows.append({"source": name, "status": "ok", "rows": len(merged), "new_rows": len(new), "message": "已更新缓存"})
    except Exception as exc:
        log(ctx, f"更新 {name} 失败：{exc}")
        rows.append({"source": name, "status": "failed_keep_old", "rows": len(old), "new_rows": 0, "message": str(exc)[:250]})


def fetch_quotes(ctx: RunContext, ifind: Any, name: str, section: str, rows: list[dict[str, Any]]) -> None:
    sections = load_sections()
    body = find_section_body(sections, section)
    call = extract_first_ths_call(body)
    out_path = ctx.cache_dir / f"{name}.csv"
    old = read_csv_smart(out_path)
    codes = get_ipo_code_pool(ctx)
    if not codes:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未识别到2024+ IPO股票池"})
        return
    if call is None:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未找到行情API命令"})
        return
    func, args = call
    batch = int(ctx.config.get("batch_size_quotes", 120))
    today = ctx.today
    backfill_days = int(ctx.config.get("default_quote_backfill_days", 5))
    start_date = today - timedelta(days=backfill_days)
    if not old.empty:
        dcol = guess_date_column(old)
        if dcol:
            max_d = pd.to_datetime(old[dcol], errors="coerce").max()
            if pd.notna(max_d):
                start_date = min(today, max_d.date() + timedelta(days=1))
                # 周末/节假日或数据滞后时，保守补最近几天
                start_date = min(start_date, today - timedelta(days=backfill_days))
    all_new = []
    if ctx.dry_run:
        rows.append({"source": name, "status": "dry_run", "rows": len(old), "new_rows": 0, "message": f"将拉取{len(codes)}只，{start_date}至{today}"})
        return
    for i in range(0, len(codes), batch):
        part = codes[i:i+batch]
        part_args = list(args)
        # THS_HQ(codes, fields, '', start, end)
        part_args[0] = ",".join(part)
        if len(part_args) >= 5:
            part_args[-2] = start_date.strftime("%Y-%m-%d")
            part_args[-1] = today.strftime("%Y-%m-%d")
        try:
            log(ctx, f"行情批次 {i//batch+1}: {len(part)}只")
            all_new.append(call_ifind(ctx, ifind, func, tuple(part_args)))
        except Exception as exc:
            log(ctx, f"行情批次失败：{exc}")
            if ctx.config.get("max_retry", 1):
                try:
                    all_new.append(call_ifind(ctx, ifind, func, tuple(part_args)))
                except Exception:
                    pass
    new = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame()
    merged = merge_cache(old, new, "append_by_code_date")
    write_csv(merged, out_path)
    # 同步一份给 deploy_data，供技术指标脚本使用；如果字段名未标准化，技术脚本会尽力识别。
    write_csv(merged, ctx.deploy_dir / "ifind_daily_quotes_raw.csv")
    rows.append({"source": name, "status": "ok" if not new.empty else "no_new_data", "rows": len(merged), "new_rows": len(new), "message": f"股票池{len(codes)}只；{start_date}至{today}"})


def fetch_snapshot(ctx: RunContext, ifind: Any, name: str, section: str, rows: list[dict[str, Any]]) -> None:
    sections = load_sections()
    body = find_section_body(sections, section)
    call = extract_first_ths_call(body)
    out_path = ctx.cache_dir / f"{name}_{ctx.today:%Y%m%d}.csv"
    codes = get_ipo_code_pool(ctx)
    if not codes:
        rows.append({"source": name, "status": "skipped", "rows": 0, "message": "未识别到2024+ IPO股票池"})
        return
    if call is None:
        rows.append({"source": name, "status": "skipped", "rows": 0, "message": "未找到快照API命令"})
        return
    func, args = call
    batch = int(ctx.config.get("batch_size_snapshot", 200))
    all_new = []
    if ctx.dry_run:
        rows.append({"source": name, "status": "dry_run", "rows": 0, "new_rows": 0, "message": f"将拉取{len(codes)}只收盘快照"})
        return
    for i in range(0, len(codes), batch):
        part = codes[i:i+batch]
        part_args = list(args)
        part_args[0] = ",".join(part)
        try:
            log(ctx, f"快照批次 {i//batch+1}: {len(part)}只")
            all_new.append(call_ifind(ctx, ifind, func, tuple(part_args)))
        except Exception as exc:
            log(ctx, f"快照批次失败：{exc}")
    new = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame()
    write_csv(new, out_path)
    write_csv(new, ctx.deploy_dir / "ifind_close_snapshot_raw.csv")
    rows.append({"source": name, "status": "ok" if not new.empty else "no_new_data", "rows": len(new), "new_rows": len(new), "message": f"股票池{len(codes)}只"})



def replace_dates_in_string(value: str, today: date) -> str:
    """把超级命令样例参数里的固定日期替换为本次更新日。

    适用于 THS_BD 第三个参数，例如：2026-05-15 或 20260515。
    不改变空参数和非日期参数。
    """
    if not isinstance(value, str) or not value:
        return value
    ymd_dash = today.strftime("%Y-%m-%d")
    ymd = today.strftime("%Y%m%d")
    value = re.sub(r"\d{4}-\d{2}-\d{2}", ymd_dash, value)
    value = re.sub(r"(?<!\d)20\d{6}(?!\d)", ymd, value)
    return value


def update_hq_args_for_window(args: tuple[Any, ...], start: date, end: date) -> tuple[Any, ...]:
    part_args = list(args)
    if len(part_args) >= 5:
        part_args[-2] = start.strftime("%Y-%m-%d")
        part_args[-1] = end.strftime("%Y-%m-%d")
    return tuple(part_args)


def deploy_raw_copy(ctx: RunContext, name: str, df: pd.DataFrame) -> None:
    if df is None:
        return
    write_csv(df, ctx.deploy_dir / f"ifind_{name}_raw.csv")


def fetch_index_quotes(ctx: RunContext, ifind: Any, name: str, section: str, rows: list[dict[str, Any]]) -> None:
    """指数行情：固定指数代码，不使用IPO股票池。"""
    sections = load_sections()
    body = find_section_body(sections, section)
    call = extract_first_ths_call(body)
    out_path = ctx.cache_dir / f"{name}.csv"
    old = read_csv_smart(out_path)
    if call is None:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未找到指数行情API命令"})
        return
    func, args = call
    today = ctx.today
    backfill_days = int(ctx.config.get("default_quote_backfill_days", 5))
    start_date = today - timedelta(days=backfill_days)
    if not old.empty:
        dcol = guess_date_column(old)
        if dcol:
            max_d = pd.to_datetime(old[dcol], errors="coerce").max()
            if pd.notna(max_d):
                start_date = min(max_d.date() + timedelta(days=1), today - timedelta(days=backfill_days))
    args = update_hq_args_for_window(args, start_date, today)
    if ctx.dry_run:
        rows.append({"source": name, "status": "dry_run", "rows": len(old), "new_rows": 0, "message": f"将更新固定指数，{start_date}至{today}"})
        return
    try:
        log(ctx, f"更新指数行情 {name}: {func}")
        new = call_ifind(ctx, ifind, func, args)
        merged = merge_cache(old, new, "append_by_code_date")
        write_csv(merged, out_path)
        deploy_raw_copy(ctx, name, merged)
        rows.append({"source": name, "status": "ok" if not new.empty else "no_new_data", "rows": len(merged), "new_rows": len(new), "message": f"固定指数；{start_date}至{today}"})
    except Exception as exc:
        log(ctx, f"更新指数行情失败：{exc}")
        rows.append({"source": name, "status": "failed_keep_old", "rows": len(old), "new_rows": 0, "message": str(exc)[:250]})


def fetch_bd_source(ctx: RunContext, ifind: Any, name: str, section: str, rows: list[dict[str, Any]]) -> None:
    """基础数据/特色数据类：统一使用2024+ IPO股票池替换样例代码，再分批调用 THS_BD。"""
    sections = load_sections()
    body = find_section_body(sections, section)
    call = extract_first_ths_call(body)
    out_path = ctx.cache_dir / f"{name}.csv"
    old = read_csv_smart(out_path)
    codes = get_ipo_code_pool(ctx)
    if not codes:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未识别到2024+ IPO股票池"})
        return
    if call is None:
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": "未找到API命令，保留空接口"})
        return
    func, args = call
    if func != "THS_BD":
        rows.append({"source": name, "status": "skipped", "rows": len(old), "message": f"该源预期THS_BD，实际为{func}"})
        return
    batch = int(ctx.config.get("batch_size_bd", 80))
    if ctx.dry_run:
        rows.append({"source": name, "status": "dry_run", "rows": len(old), "new_rows": 0, "message": f"将按2024+ IPO股票池分批拉取{len(codes)}只；batch={batch}"})
        return
    all_new = []
    for i in range(0, len(codes), batch):
        part = codes[i:i+batch]
        part_args = list(args)
        part_args[0] = ",".join(part)
        if len(part_args) >= 3 and isinstance(part_args[2], str):
            part_args[2] = replace_dates_in_string(part_args[2], ctx.today)
        try:
            log(ctx, f"BD批次 {name} {i//batch+1}: {len(part)}只")
            all_new.append(call_ifind(ctx, ifind, func, tuple(part_args)))
        except Exception as exc:
            log(ctx, f"BD批次失败 {name}: {exc}")
            if ctx.config.get("max_retry", 1):
                try:
                    all_new.append(call_ifind(ctx, ifind, func, tuple(part_args)))
                except Exception:
                    pass
    new = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame()
    merged = merge_cache(old, new, "merge_by_code")
    write_csv(merged, out_path)
    deploy_raw_copy(ctx, name, merged)
    rows.append({"source": name, "status": "ok" if not new.empty else "no_new_data", "rows": len(merged), "new_rows": len(new), "message": f"股票池{len(codes)}只；统一分批THS_BD"})

def import_offline_exports(ctx: RunContext, rows: list[dict[str, Any]]) -> None:
    ctx.export_dir.mkdir(exist_ok=True)
    files = [p for p in ctx.export_dir.glob("*") if p.suffix.lower() in {".csv", ".xlsx", ".xls"}]
    if not files:
        rows.append({"source": "offline_exports", "status": "skipped", "rows": 0, "message": f"{ctx.export_dir} 下没有Excel/CSV"})
        return
    raw_dir = ctx.cache_dir / "offline_exports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for p in files:
        try:
            df = clean_frame(read_table_smart(p))
            total += len(df)
            write_csv(df, raw_dir / (p.stem + ".csv"))
        except Exception as exc:
            log(ctx, f"导入离线文件失败 {p.name}: {exc}")
    rows.append({"source": "offline_exports", "status": "ok", "rows": total, "message": f"导入{len(files)}个文件，不消耗API额度"})


def run_subprocess(ctx: RunContext, cmd: list[str], label: str, rows: list[dict[str, Any]]) -> None:
    try:
        log(ctx, f"运行 {label}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=600)
        with open(ctx.log_path, "a", encoding="utf-8") as f:
            if proc.stdout:
                f.write(proc.stdout + "\n")
            if proc.stderr:
                f.write(proc.stderr + "\n")
        rows.append({"source": label, "status": "ok" if proc.returncode == 0 else "failed", "rows": 0, "message": (proc.stdout or proc.stderr)[-250:]})
    except Exception as exc:
        rows.append({"source": label, "status": "failed", "rows": 0, "message": str(exc)[:250]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["api", "offline", "dry-run"], default="dry-run")
    ap.add_argument("--low-quota", action="store_true", help="启用低额度策略：静态表近端窗口、行情增量、快照仅2024+ IPO池。")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD，默认当天。")
    ap.add_argument("--input-dir", default=None, help="离线导出文件目录，默认 ifind_exports。")
    ap.add_argument("--build-signals", action="store_true", help="更新后运行技术指标和投资数据生成脚本。")
    args = ap.parse_args()

    cfg = load_config()
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    cache_dir = ROOT / cfg.get("cache_dir", "data/raw_ifind")
    deploy_dir = ROOT / cfg.get("deploy_dir", "deploy_data")
    export_dir = ROOT / (args.input_dir or cfg.get("export_dir", "ifind_exports"))
    log_dir = ROOT / cfg.get("log_dir", "logs")
    ctx = RunContext(
        mode=args.mode,
        today=today,
        low_quota=args.low_quota,
        dry_run=args.mode == "dry-run",
        config=cfg,
        cache_dir=cache_dir,
        deploy_dir=deploy_dir,
        export_dir=export_dir,
        log_dir=log_dir,
        log_path=log_dir / f"update_{today:%Y%m%d}.log",
    )
    for d in (cache_dir, deploy_dir, export_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    log(ctx, f"启动 iFind 日度更新：mode={args.mode}, low_quota={args.low_quota}")

    if args.mode == "offline":
        import_offline_exports(ctx, rows)
    else:
        ifind = load_ifind_module()
        if ifind is None and not ctx.dry_run:
            log(ctx, "未找到 iFinDPy。请先在iFind超级命令工具里修复Python环境，或使用 offline 模式。")
            rows.append({"source": "ifind_login", "status": "failed", "rows": 0, "message": "未找到 iFinDPy"})
            save_status(ctx, rows)
            raise SystemExit(1)
        if ifind is not None and not ctx.dry_run:
            user, pwd = credentials()
            if user and pwd and hasattr(ifind, "THS_iFinDLogin"):
                ret = ifind.THS_iFinDLogin(user, pwd)
                log(ctx, f"iFind 登录返回：{ret}")
            else:
                log(ctx, "未配置账号密码；如果iFind环境已保持登录，继续尝试取数。")
        if ifind is None:
            class Dummy:
                pass
            ifind = Dummy()

        sources = cfg.get("sources", {})
        end = today
        static_lookback = int(cfg.get("default_static_lookback_days", 180))
        start_static = max(datetime.strptime(cfg.get("ipo_start_date", "2024-01-01"), "%Y-%m-%d").date(), today - timedelta(days=static_lookback)) if args.low_quota else datetime.strptime(cfg.get("ipo_start_date", "2024-01-01"), "%Y-%m-%d").date()
        # IPO 主表、A1、解禁仍要有足够窗口；行情单独增量。
        for name in ["listing_applications", "ipo_master", "ballot_results", "margin_data", "cornerstones", "lockup_events", "underwriters", "bookrunners"]:
            meta = sources.get(name, {})
            if not meta:
                continue
            if name == "lockup_events":
                start = today - timedelta(days=int(cfg.get("lockup_past_days", 180)))
                end2 = today + timedelta(days=int(cfg.get("lockup_future_days", 540)))
            elif name in {"listing_applications", "ipo_master"}:
                start = datetime.strptime(cfg.get("ipo_start_date", "2024-01-01"), "%Y-%m-%d").date()
                end2 = today
            else:
                start = start_static
                end2 = today
            fetch_static_source(ctx, ifind, name, meta.get("section", ""), meta.get("cache_policy", "merge_incremental"), rows, start, end2)
        # 指数行情：固定指数代码，不进入股票池替换。
        if "market_index_quotes" in sources:
            fetch_index_quotes(ctx, ifind, "market_index_quotes", sources["market_index_quotes"].get("section", ""), rows)

        # 所有股票类基础数据/特色数据：统一先生成2024+ IPO股票池，再分批替换样例代码。
        for name in ["company_profile", "financial_summary", "valuation_metrics", "shareholders", "southbound_holding"]:
            meta = sources.get(name, {})
            if meta:
                fetch_bd_source(ctx, ifind, name, meta.get("section", ""), rows)

        # 行情类同样使用2024+ IPO股票池分批替换全港股主板代码，但函数仍保持 THS_HQ / THS_RQ。
        if "daily_quotes" in sources:
            fetch_quotes(ctx, ifind, "daily_quotes", sources["daily_quotes"].get("section", ""), rows)
        if "close_snapshot" in sources:
            fetch_snapshot(ctx, ifind, "close_snapshot", sources["close_snapshot"].get("section", ""), rows)
        if ifind is not None and not ctx.dry_run and hasattr(ifind, "THS_iFinDLogout"):
            try:
                ifind.THS_iFinDLogout()
            except Exception:
                pass

    if args.build_signals:
        # v11 data DAG: raw iFind data is not enough. Rebuild every downstream table
        # so Streamlit never keeps showing stale Yahoo/Stooq paths or old scores.
        run_subprocess(ctx, [sys.executable, "scripts/build_post_listing_paths.py", "--update-pool"], "build_post_listing_paths", rows)
        run_subprocess(ctx, [sys.executable, "scripts/build_technical_signals.py"], "build_technical_signals", rows)
        run_subprocess(ctx, [sys.executable, "scripts/build_lockup_risk.py"], "build_lockup_risk", rows)
        run_subprocess(ctx, [sys.executable, "scripts/build_investment_dataset.py"], "build_investment_dataset", rows)

    save_status(ctx, rows)
    log(ctx, "更新完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
