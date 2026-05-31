# WeFlow Dashboard — 5个问题修复

## 修复列表

### ✅ 1. 手动执行页左侧会话列表同步
`loadActionsSessions()` 改为直接使用 `state.config.sessions`（当前配置中的监控会话），而非依赖 `/api/status` 返回的会话列表，确保与会话管理页完全同步。

### ✅ 2. 总览页状态显示修正
- 关闭状态显示改为"待监控"（原"已禁用"）
- 开关 Toast 提示改为"已启用"/"已关闭"

### ✅ 3. 全量导出转圈修复（关键Bug）
**根因**: `/api/run/quick` 中 `run_export_threaded()` 返回的是 dict（含 `export_id`），但代码又把它包了一层：`{"export_id": eid, ...}`，导致前端收到的 `export_id` 是对象而非字符串。进度轮询中 `prog.export_id !== exportId` 永远不匹配，弹窗永远不消失。

**修复**: 与其他导出端点（`/api/run/full`、`/api/run/manual-incr`）保持一致，直接返回 `run_export_threaded()` 的结果。

### ✅ 4. 区间导出为0 + 断点续传
- 新增 `run_range_export_threaded()` 线程导出函数（含进度轮询和0条自动重试）
- `/api/run-range` 改为异步线程模式
- 前端 `doQuickRange()` 和 `runRangeExport()` 都改为使用进度弹窗+轮询

### ✅ 5. 区间导出弹窗布局优化
开始日期和结束日期分开为两行，各自下方放置对应的快捷按钮：
- "最早" → 开始日期下方
- "今天" → 结束日期下方

## 修改的文件
- `e:\WorkBuddy\weflow-monitor\dashboard_server.py` — `/api/run/quick` 返回格式修复、新增 `run_range_export_threaded()`、`/api/run-range` 线程化
- `e:\WorkBuddy\weflow-monitor\dashboard.html` — `loadActionsSessions()` 改用 config、总览页状态文字修正、区间导出弹窗布局优化、`doQuickRange()`/`runRangeExport()` 改用线程+进度
