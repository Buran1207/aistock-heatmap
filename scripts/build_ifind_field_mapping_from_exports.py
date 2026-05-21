from __future__ import annotations

"""
从 iFind 导出的 Excel/CSV 表头，反推超级命令里的字段代码含义。

用途：
- 不调用 iFind API，不消耗额度。
- 解决 p05310_f001、p03764_f001、p03412_f001 这类字段代码含义不清的问题。
- 只要导出文件来自同一个 iFind 页面、列顺序未被手动打乱，就可以按字段顺序生成映射。

运行示例：
    python scripts/build_ifind_field_mapping_from_exports.py --input-dir ifind_exports

输出：
    config/ifind_field_mapping_auto.csv
    deploy_data/ifind_field_mapping_auto.csv
"""

import argparse
import ast
import re
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMMANDS_PATH = ROOT / "config" / "ifind_api_commands.txt"
OUT_CONFIG = ROOT / "config" / "ifind_field_mapping_auto.csv"
OUT_DEPLOY = ROOT / "deploy_data" / "ifind_field_mapping_auto.csv"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "cp950")

SOURCE_RULES = [
    {
        "source": "listing_applications",
        "section_hint": "1 上市申请一览",
        "filename_keywords": ["上市申请", "申请一览", "A1"],
    },
    {
        "source": "application_history",
        "section_hint": "申请明细",
        "filename_keywords": ["申请明细", "申请记录"],
    },
    {
        "source": "ipo_master",
        "section_hint": "2 首发信息一览",
        "filename_keywords": ["首发信息", "发行资料", "新股发行"],
    },
    {
        "source": "ballot_results",
        "section_hint": "3 打新中签结果",
        "filename_keywords": ["打新", "中签", "申购结果"],
    },
    {
        "source": "margin_data",
        "section_hint": "4 孖展数据",
        "filename_keywords": ["孖展", "融资认购"],
    },
    {
        "source": "cornerstones",
        "section_hint": "5 基石投资者",
        "filename_keywords": ["基石"],
    },
    {
        "source": "gray_market",
        "section_hint": "6 暗盘行情",
        "filename_keywords": ["暗盘"],
    },
    {
        "source": "daily_quotes",
        "section_hint": "7 港股日行情",
        "filename_keywords": ["日行情", "历史行情", "日K", "后复权"],
    },
    {
        "source": "close_snapshot",
        "section_hint": "8 港股收盘行情快照",
        "filename_keywords": ["快照", "收盘行情", "实时行情"],
    },
    {
        "source": "lockup_events",
        "section_hint": "9 限售股解禁明细",
        "filename_keywords": ["解禁", "限售", "禁售"],
    },
    {
        "source": "underwriters",
        "section_hint": "10 承销团",
        "filename_keywords": ["承销团", "承销商"],
    },
    {
        "source": "bookrunners",
        "section_hint": "11 账簿管理人",
        "filename_keywords": ["账簿管理", "整体协调人", "Bookrunner"],
    },
]


def read_text(path: Path) -> str:
    for enc in ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def read_table_header(path: Path) -> list[str]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, nrows=0)
        return [str(c).strip() for c in df.columns if not str(c).startswith("Unnamed")]
    last_error: Exception | None = None
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(path, nrows=0, encoding=enc)
            return [str(c).strip() for c in df.columns if not str(c).startswith("Unnamed")]
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"无法读取表头 {path}: {last_error}")


def split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"【([^】]+)】", text)
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[title] = body.strip()
    return sections


def find_section(sections: dict[str, str], hint: str) -> tuple[str, str] | None:
    for title, body in sections.items():
        if hint in title or title in hint:
            return title, body
    # 兼容带编号标题，如 hint='9 限售股解禁明细'
    hint_digits = "".join(ch for ch in hint if ch.isdigit())
    for title, body in sections.items():
        if hint_digits and title.startswith(hint_digits):
            return title, body
    # 兼容关键词
    hint_text = re.sub(r"^\d+\s*", "", hint)
    for title, body in sections.items():
        if hint_text and hint_text in title:
            return title, body
    return None


def literal_eval_call_args(body: str) -> tuple[str, tuple[Any, ...]] | None:
    m = re.search(r"(THS_[A-Z]+)\((.*)\)", body, flags=re.S)
    if not m:
        return None
    func = m.group(1)
    args_text = m.group(2).strip()
    # 防止复制文本里混入下一节
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


def extract_fields_from_call(func: str, args: tuple[Any, ...]) -> list[str]:
    if not args:
        return []
    if func == "THS_DR" and len(args) >= 3 and isinstance(args[2], str):
        # 形如 p05310_f001:Y,p05310_f002:Y
        fields = []
        for part in args[2].split(","):
            token = part.strip()
            if not token:
                continue
            fields.append(token.split(":", 1)[0].strip())
        return fields
    if func in {"THS_HQ", "THS_RQ"} and len(args) >= 2 and isinstance(args[1], str):
        # 形如 preClose;open;high;...
        return [x.strip() for x in re.split(r"[;,]", args[1]) if x.strip()]
    return []


def find_export_file(input_dir: Path, keywords: list[str]) -> Path | None:
    files = [p for p in input_dir.glob("**/*") if p.suffix.lower() in {".csv", ".xlsx", ".xls"}]
    scored: list[tuple[int, Path]] = []
    for p in files:
        name = p.name.lower()
        score = 0
        for kw in keywords:
            if kw.lower() in name:
                score += 1
        if score:
            scored.append((score, p))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1].stat().st_mtime), reverse=True)
    return scored[0][1]


def confidence_for(n_fields: int, n_headers: int, pos: int) -> tuple[str, str]:
    if n_fields == 0:
        return "低", "命令中未识别到字段列表"
    if n_headers == 0:
        return "低", "未找到导出文件表头"
    if n_fields == n_headers:
        return "高", "字段数量与导出表头数量一致，按顺序映射"
    if n_headers >= n_fields and pos <= n_fields:
        return "中", "导出表头多于API字段，按前N列顺序映射，需抽样复核"
    if n_fields > n_headers and pos <= n_headers:
        return "中", "API字段多于导出表头，已映射现有列，后续字段缺中文名"
    return "低", "字段数量不匹配，无法映射该位置"


def build_mapping(input_dir: Path) -> pd.DataFrame:
    sections = split_sections(read_text(COMMANDS_PATH)) if COMMANDS_PATH.exists() else {}
    rows: list[dict[str, Any]] = []
    for rule in SOURCE_RULES:
        src = rule["source"]
        sec = find_section(sections, rule["section_hint"])
        title, body = sec if sec else ("", "")
        call = literal_eval_call_args(body) if body else None
        func, args = call if call else ("", tuple())
        fields = extract_fields_from_call(func, args) if func else []
        export_file = find_export_file(input_dir, rule["filename_keywords"])
        headers: list[str] = []
        if export_file:
            try:
                headers = read_table_header(export_file)
            except Exception as exc:
                rows.append({
                    "source": src,
                    "section": title or rule["section_hint"],
                    "ifind_field": "",
                    "export_column": "",
                    "position": "",
                    "confidence": "低",
                    "export_file": str(export_file.relative_to(ROOT)) if export_file.is_relative_to(ROOT) else str(export_file),
                    "note": f"读取导出文件失败：{exc}",
                })
        if not fields:
            rows.append({
                "source": src,
                "section": title or rule["section_hint"],
                "ifind_field": "",
                "export_column": "",
                "position": "",
                "confidence": "低",
                "export_file": str(export_file.relative_to(ROOT)) if export_file else "",
                "note": "未从 ifind_api_commands.txt 中解析到字段列表",
            })
            continue
        max_len = max(len(fields), len(headers))
        for i in range(max_len):
            field = fields[i] if i < len(fields) else ""
            header = headers[i] if i < len(headers) else ""
            conf, note = confidence_for(len(fields), len(headers), i + 1)
            rows.append({
                "source": src,
                "section": title or rule["section_hint"],
                "ifind_field": field,
                "export_column": header,
                "position": i + 1,
                "confidence": conf,
                "export_file": str(export_file.relative_to(ROOT)) if export_file and export_file.is_relative_to(ROOT) else (str(export_file) if export_file else ""),
                "note": note,
            })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="ifind_exports")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    input_dir = ROOT / args.input_dir
    input_dir.mkdir(parents=True, exist_ok=True)
    df = build_mapping(input_dir)
    OUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OUT_DEPLOY.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CONFIG, index=False, encoding="utf-8-sig")
    df.to_csv(OUT_DEPLOY, index=False, encoding="utf-8-sig")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"已生成字段映射：{OUT_CONFIG}")
    print(f"同时输出到页面数据目录：{OUT_DEPLOY}")
    if not df.empty:
        summary = df.groupby(["source", "confidence"], dropna=False).size().reset_index(name="rows")
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
