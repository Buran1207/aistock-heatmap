# Windows 解压文件名乱码修复说明

上一版包中包含中文 `.bat` 文件名，部分 Windows 解压环境会因为 ZIP 编码识别问题显示乱码。

本版已将所有批处理文件改为英文文件名：

| 用途 | 新文件名 |
|---|---|
| 首次安装依赖 | `00_setup_env.bat` |
| 16:30 低额度日度更新 | `run_daily_update_low_quota.bat` |
| 模拟运行，不耗额度 | `run_daily_update_dry_run.bat` |
| 处理 iFind 导出文件，不耗额度 | `process_ifind_exports_offline.bat` |
| 从导出文件反推 iFind 字段映射 | `build_ifind_field_mapping_offline.bat` |

后续统一使用英文文件名，避免 Windows Explorer 显示乱码。
