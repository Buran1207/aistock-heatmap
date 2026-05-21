# v11 iFind 主数据链路与评分重算修复

本版修复两个核心问题：

1. **数据链路不通**：iFind 原始行情已经取到，但路径标签、技术信号、二级评分、今日决策清单仍可能读取旧 Yahoo/Stooq 缓存。
2. **评分结果不可信**：原始数据更新后，最终评分表没有强制重算，导致页面显示的行情来源、日期和评分不一致。

## 关键修复

### 1. build_post_listing_paths.py 改为 iFind 优先

旧版默认读取：

```python
ap.add_argument("--quotes", default="deploy_data/ipo_daily_quotes_180d.csv")
```

新版默认读取顺序：

1. `deploy_data/ifind_daily_quotes_raw.csv`
2. `data/raw_ifind/daily_quotes.csv`
3. `deploy_data/ipo_daily_quotes_180d.csv`，仅兜底
4. `deploy_data/ifind_close_snapshot_raw.csv` 可补当日临时行情

输出增加：

- `quote_source`
- `quote_rows`
- `latest_quote_date`
- `path_data_source`

### 2. 日更脚本强制重算派生表

`ifind_low_quota_daily_update.py --build-signals` 现在按顺序运行：

```text
build_post_listing_paths.py --update-pool
build_technical_signals.py
build_lockup_risk.py
build_investment_dataset.py
```

这保证“原始 iFind 数据更新后，最终页面表也同步重建”。

### 3. build_investment_dataset.py 不再是 no-op

新版会重建：

- `ipo_investment_decision_scored.csv`
- `primary_market_decision.csv`
- `secondary_market_decision.csv`
- `today_action_list.csv`
- `data_lineage_last_run.csv`

### 4. Streamlit 增加数据版本提示

页面顶部会显示：

- 原始行情行数
- 二级表行数
- 今日清单行数
- 最近派生重算时间
- 最近更新状态时间
- 行情源分布
- 最新行情日

如果原始数据更新了但派生表未重算，页面会提示，而不是继续静默显示旧数据。

## 明天收盘后验证重点

运行：

```powershell
python scripts/ifind_low_quota_daily_update.py --mode api --low-quota --build-signals
```

检查：

1. 页面顶部数据版本是否显示最新重算时间。
2. `data_lineage_last_run.csv` 是否存在并显示各派生表行数。
3. `ipo_post_listing_paths.csv` 的 `quote_source` 是否为 `ifind` 或 `ifind_snapshot`。
4. `ipo_technical_signals.csv` 的 `quote_source` 是否为 `ifind` 或 `ifind_snapshot`。
5. 二级页面是否不再显示旧 `yahoo / 5月12日`。
6. `secondary_market_decision.csv` 和 `today_action_list.csv` 是否更新。

