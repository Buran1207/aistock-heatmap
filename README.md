# AI Stock Heatmap

US / H / A 三市场 AI 股票产业链热力图。

## 文件说明

- `index.html`：前端热力图页面，支持亮/暗模式、市场筛选、搜索、细分赛道涨跌分布。
- `universe.json`：唯一股票池来源。`market` 使用 `US` / `H` / `A`，其中 `H` 代表香港市场。
- `fetch_prices.py`：读取 `universe.json`，按 `市场:代码` 去重抓取行情，并生成 `prices.json`。
- `prices.json`：前端读取的行情文件，由 GitHub Actions 自动更新。
- `.github/workflows/update-prices.yml`：每 5 分钟运行一次行情抓取并提交 `prices.json`。

## 部署步骤

1. 把这些文件覆盖/新增到 GitHub 仓库根目录。
2. 在 GitHub 仓库启用 GitHub Pages。
3. 在仓库的 **Settings → Actions → General → Workflow permissions** 中选择 **Read and write permissions**，否则 Action 无法提交 `prices.json`。
4. 在 Actions 页面手动运行一次 **Update AI stock heatmap prices**，确认生成首个有效 `prices.json`。
5. 打开 GitHub Pages 页面，前端会每 30 秒自动拉取新的 `prices.json`。

## 股票池维护

只改 `universe.json`。同一股票可以出现在多个赛道，抓取脚本会自动去重，不会重复请求行情。

```json
{"t":"NVDA","n":"NVIDIA","m":"US"}
{"t":"0700","n":"腾讯控股","m":"H"}
{"t":"300308","n":"中际旭创","m":"A"}
```

## 颜色约定

- 红色：上涨
- 绿色：下跌
- 灰色：暂无任何可用行情
- `stale=true`：本轮抓取失败，页面保留上一轮有效行情，避免临时接口失败导致大片变灰
