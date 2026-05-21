# v10 iFind主数据链路与未上市评分修复版

本版重点修复：未上市/A1评分过粗、B研究池过多、缺资料被误作为扣分、二级页面仍优先显示旧 Yahoo 行情、日期显示为 1970-01-01 等问题。

# 港股 IPO / 二级交易投资决策系统 v9

本版是「一级/二级决策重构与精细评分版」。

## 本版重点

- A1 与招股期合并为「一级市场 / IPO项目决策」工作区；A1与招股期仍保留两套评分。
- 已上市公司统一进入「已上市 / 二级交易决策」，当前二级评分不混入历史IPO评分。
- 二级评分拆为 0-30D、31-180D、180D+ 三套规则，页面内可调权重。
- 技术指标新增 KDJ、BOLL、OBV、MFI、ATR，并转化为交易状态、买入触发和卖出触发。
- 解禁不再单独评分，只作为二级交易风险扣分；正式评分只用 iFind 精确解禁。
- 修复 THSData 解析，技术信号优先使用 iFind 行情和快照。
- 日期字段显示到日，人工研究评分嵌入一级市场界面。

## 本地运行

先检查环境：

```bat
00_setup_env.bat
```

模拟运行，不消耗额度：

```bat
run_daily_update_dry_run.bat
```

收盘后真实更新：

```bat
run_daily_update_low_quota.bat
```

## iFind 接口

默认 bat 已加入：

```bat
C:\iFinD\THSDataInterface_Windows\bin\x64
```

账号密码只放本地：

```text
config/local_ifind_credentials.ini
```

不要上传 GitHub。

## 详细说明

见：

```text
docs/v9_primary_secondary_scoring_rebuild.md
```
