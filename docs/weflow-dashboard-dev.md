---
summary: "WeFlow Dashboard 开发技能 - 从 v1 到 v34 的教训总结"
read_when:
  - 修改 WeFlow Dashboard 代码时
  - 打包发布新版本时
  - 处理导出/报告/日志相关 Bug 时
---

# WeFlow Dashboard 开发技能

## 一、开发流程

1. **改代码前先读全** — 必须完整阅读目标函数及所有调用链，理解数据流向再动手
2. **改完验证语法** — `python -c "import py_compile; py_compile.compile('file.py', doraise=True)"`
3. **打包前更新版本号** — 先改 `VERSION`，再 `pyinstaller --clean`
4. **打包后验证** — `diff -q` 对比源文件和 `_internal/` 中文件的一致性

## 二、历任 Bug 列表（避免重犯）

### 数据方向类
| Bug | 根因 | 教训 |
|-----|------|------|
| 最后消息时间取了最早消息 | WeFlow API 返回消息**最新在前**，`msgs[0]`=最新 `msgs[-1]`=最旧 | 不要假设 API 返回顺序，先确认数据方向 |
| 最早日期取了最新消息 | `limit=1&offset=0` 返回最新消息（API 逆序） | 获取第一条消息需要用 `limit=N` 取末尾 |
| 时间区间未显示 | 数据写了但日报格式化没拼接 | 改显示层时确认数据通路末端也改了 |

### 路径/文件类
| Bug | 根因 | 教训 |
|-----|------|------|
| setup_wizard.html 未找到 | 打包后文件路径变了，`_read_html` 用的 `BASE_DIR` | PyInstaller 打包后文件在 `_internal/` 下 |
| 清空日志被删 | 日志写入 `session_dir/export_log.json` 后 `shutil.rmtree` 删了整个目录 | 跨会话操作日志必须写独立文件 |
| 清空日志读不到 | 服务器写 `BASE_DIR/_ops_log`，日报去 `data/_ops_log` 读 | 两处路径必须使用同一个 `get_data_dir()` |

### 编码/格式类
| Bug | 根因 | 教训 |
|-----|------|------|
| bat/vbs 乱码 | Write 工具保存为 UTF-8，Windows CMD 需要 GBK/ANSI | ⚠️ 用 `Set-Content -Encoding Default` 转 CRLF + ANSI |
| bat/vbs 语法错误 | 行尾 LF 而非 CRLF | Windows 脚本必须用 `Set-Content` 保存 |
| bat 启动慢 | 每轮循环启动一次 `powershell.exe`（1~2秒/次） | 单次 PowerShell 进程内循环，不要重复启进程 |
| 版本号退化 | 改版本号在打包之后 | **先改 VERSION，再 pyinstaller**。打包后用 `--clean` |

### 同步/状态类
| Bug | 根因 | 教训 |
|-----|------|------|
| 导出转圈不消失 | `run_export_threaded()` 返回 dict，`/api/run/quick` 又包了一层 dict | 检查 `export_id` 传递链路是否为纯字符串 |
| 开关状态不同步 | `toggleSessionMonitor()` 只调了 `loadConfigSessions()` 没调 `refreshAll()` | 修改配置后必须同时刷新所有相关页面 |
| 切标签不刷新 | `switchTab()` 只切 UI 不加载数据 | 每个标签切换时调用对应数据加载函数 |

## 三、关键架构知识

### 数据文件体系
```
data/
├── 会话名/              # 每个会话一个目录
│   ├── 全量导出_日期.json   # 全量导出数据
│   ├── 增量导出_日期.json   # 增量导出数据
│   ├── 区间导出_日期.json   # 区间导出数据
│   ├── export_log.json    # 本会话的操作日志（被清空时删除）
│   └── 日期.html          # HTML 聊天记录
├── _operations_log.json  # 中心操作日志（清空等跨会话操作）
├── checkpoint.json       # 增量同步状态
└── reports/              # 日报
    └── 日期_日报.md
```

### 消息顺序约定
⚠️ **WeFlow API 返回消息最新在前**，各处理环节的索引约定：
- `msgs[0]` = 最新消息
- `msgs[-1]` = 最早消息
- 全量导出文件：最新在前
- 增量导出文件：最新在前

### 关键函数调用链
```
用户操作 → 前端 API 调用 → do_GET()
  ├── /api/run/quick → run_export_threaded() → do_manual_export()
  │     → fetch_all_messages() → save_manual_export() → _log_export()
  │
  ├── /api/run/full → run_export_threaded() → do_full_export_for_sessions()
  │     → do_manual_export() × N → generate_summary()
  │
  └── /api/data/clean → clean_session_data()
        → 写 _operations_log.json → shutil.rmtree()
```

### 版本号管理
```python
APP_VERSION = "2.0"    # 外部版本：手动递增，大版本更新
VERSION = "v34"        # 内部版本：每次构建前递增
```

## 四、常见操作

### 构建打包
```bash
# 1. 更新版本号
sed -i 's/VERSION = "v[0-9]*"/VERSION = "v新版本号"/' dashboard_server.py

# 2. 更新使用说明版本号

# 3. 清理缓存并打包
pyinstaller --clean --distpath dist_v新版本号 WeFlowDashboard.spec

# 4. 复制辅助文件（bat/vbs/txt）
cp 启动看板.bat start.vbs 使用说明.txt 停止服务.bat dist_v新版本号/WeFlowDashboard/

# 5. 验证
diff -q dashboard.html dist_v新版本号/WeFlowDashboard/_internal/dashboard.html
diff -q 使用说明.txt dist_v新版本号/WeFlowDashboard/使用说明.txt
```

### 快速定位问题
- 启动慢 → 检查 `启动看板.bat` 和 `start.vbs` 的 PowerShell 调用方式（单次进程内循环）
- 页面白屏 → 检查 `build_status()` 中 WeFlow ping 超时（已设为 0.5s）
- 数据不对 → 检查消息顺序约定（`msgs[0]`=最新）
- 版本显示错误 → 检查打包顺序（先改 VERSION 再 pyinstaller）
- 日志丢失 → 检查路径一致性（`get_data_dir()` vs `BASE_DIR`）

## 五、自动化部署建议

如需自动化 CI/CD：
1. GitHub Actions 构建：`pyinstaller --clean --distpath dist WeFlowDashboard.spec`
2. 构建产物保存为 Release Artifact
3. 注意：`session_token` 只在内存中，每次重启需重新登录
