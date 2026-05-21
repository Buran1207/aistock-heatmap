# 16:30 日度更新引擎使用说明

## 目标

系统统一按 **港股收盘后 16:30 日度更新** 设计。长期目标是：

```text
本地 iFind API / iFind 导出文件
        ↓
本地缓存与增量更新
        ↓
技术指标、解禁风险、评分重算
        ↓
deploy_data 输出
        ↓
上传或推送 GitHub
        ↓
Streamlit Cloud 自动刷新
```

## 不用 PyCharm 的用法

### 第一次使用

双击：

```text
00_setup_env.bat
```

### 日常 16:30 更新

双击：

```text
run_daily_update_low_quota.bat
```

### 先模拟、不消耗 iFind 额度

双击：

```text
run_daily_update_dry_run.bat
```

### iFind 手工导出文件兜底

如果某个 API 暂时不能跑，把 iFind 导出的 Excel/CSV 放进：

```text
ifind_exports/
```

然后双击：

```text
process_ifind_exports_offline.bat
```

## 低额度策略

1. 静态表不每天全量重拉，默认只拉近端窗口并与本地缓存合并。
2. 日行情按本地缓存增量更新，默认补最近 5 天，避免从 2024-01-01 每天全量重拉。
3. 收盘快照只拉 `p05310` 识别出的 2024 年后 IPO 股票池，不拉全港股主板。
4. 解禁只拉“过去 180 天到未来 540 天”的窗口，用于近期事件研究和未来供给压力。
5. API 失败不会无限重试，默认最多重试 1 次，失败后保留昨日数据并记录日志。

## 本地账号配置

复制：

```text
config/local_ifind_credentials.example.ini
```

改名为：

```text
config/local_ifind_credentials.ini
```

填写 iFind 账号密码。该文件已被 `.gitignore` 忽略，不会上传 GitHub。

也可以设置环境变量：

```text
IFIND_USERNAME
IFIND_PASSWORD
```

## 输出文件

核心输出：

```text
deploy_data/data_update_status.csv       # 每次更新状态
deploy_data/ipo_technical_signals.csv    # 专业技术指标和买卖触发条件
deploy_data/ifind_daily_quotes_raw.csv   # iFind日行情原始缓存副本
deploy_data/ifind_close_snapshot_raw.csv # iFind收盘快照原始缓存副本
```

日志：

```text
logs/update_YYYYMMDD.log
```

## 仍待补充的 API

当前包已纳入你提供的上市申请、首发信息、打新中签、孖展、基石、日行情、收盘快照、解禁、承销团、账簿管理人命令。暗盘行情、指数行情、行业分类、财务摘要、估值、股权结构、南向持股、公告目录、业绩日历等仍可后续补入。

## 离线字段映射

如果 iFind API 命令里只有字段代码，例如：

```text
p05310_f001
p03764_f001
p03412_f001
```

可以不再消耗额度去查询字段名。把对应 iFind 页面导出的原始 Excel/CSV 放入：

```text
ifind_exports/
```

然后双击：

```text
build_ifind_field_mapping_offline.bat
```

系统会按导出文件中文表头顺序，反推 API 字段含义，并输出：

```text
config/ifind_field_mapping_auto.csv
deploy_data/ifind_field_mapping_auto.csv
```

如果导出列数和 API 字段数一致，置信度为“高”；如果列数不一致，置信度为“中/低”，后续进入字段复核。
