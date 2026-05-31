# WeFlow Dashboard v19 — 修复总结

## 修改内容

### 1. 修复 JS 游离代码 Bug (dashboard.html)
**问题**：旧版会话管理重构时，删掉了 `renderSessions` 的函数声明但忘了删函数体（约 146 行）。代码引用未定义的 `weflowSessions` 变量，导致全局作用域的 `ReferenceError`，中断了 JS 执行（尽管 `switchTab` 函数定义存在且被 hoisted）。

**修复**：
- 删除第 846-929 行游离函数体（旧版 `renderSessions` 的完整代码）
- 同时删除不再使用的 `toggleWeflowSession` 函数（死代码）

### 2. 修复搜索关键词过长问题
**问题**：候选会话搜索框输入过长关键词时，WeFlow API 返回 `400 input length too long`。

**修复**：
- 前端 `pickerSearch()`：输入时自动截断 50 字符
- 前端 `pickerLoadSessions()`：传递前截断并 Toast 提示
- 后端 `_fetch_weflow_sessions()`：关键词过长时截断

### 3. 版本号更新
- `dashboard_server.py`: VERSION = "v19"

## 打包输出
- 输出目录：`dist_v19_2/WeFlowDashboard/`
- 文件：`WeFlowDashboard.exe` (2.1MB)
- 启动文件：`start_dashboard.bat` / `start_dashboard.vbs`
