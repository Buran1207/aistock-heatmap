from __future__ import annotations

"""离线导出文件处理入口：不调用 iFind API，不消耗额度。"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("RUN:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="ifind_exports")
    ap.add_argument("--outdir", default="deploy_data")
    args = ap.parse_args()
    # 先从导出文件表头反推 iFind API 字段映射，不调用 API、不消耗额度。
    run([sys.executable, "scripts/build_ifind_field_mapping_from_exports.py", "--input-dir", args.input_dir])
    # 兼容旧的GUI导出处理脚本；如果字段/文件名匹配，会生成主表。
    run([sys.executable, "scripts/build_deploy_data_from_ifind_exports.py", "--input-dir", args.input_dir, "--outdir", args.outdir])
    # 生成投资数据和技术指标。
    run([sys.executable, "scripts/build_investment_dataset.py"])
    run([sys.executable, "scripts/build_technical_signals.py"])
    print("离线处理完成。")


if __name__ == "__main__":
    main()
