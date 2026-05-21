# AI Stock Heatmap v7 - 双收盘批次更新

本版把行情更新从每 5 分钟改成每天 2 个收盘批次：

- ASIA_CLOSE：UTC 08:25，约北京时间 16:25，覆盖 A/H/TW/JP/KR 收盘后
- US_CLOSE：UTC 22:20，约北京时间次日 06:20，覆盖美股收盘后

新增文件：

- prices_manifest.json：告诉前端当前应该读取哪个版本化行情文件
- prices_archive/prices_*.json：每次更新生成一个新 URL，降低 GitHub Pages 同名 JSON 缓存影响

替换/新增：

- index.html
- fetch_prices.py
- .github/workflows/update-prices.yml
- prices_manifest.json
- prices_archive/.gitkeep

universe.json 不变，本包内附带只是为了完整备份。
