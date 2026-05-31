# WeFlow Monitor - 微信聊天记录导出与监控看板

纯 Python 3 标准库，**零外部依赖**。  
也可下载**打包版 exe**（无需安装 Python）。  
利用 WeFlow 桌面端的 HTTP API 定时增量导出微信聊天记录，并提供 Web 看板管理。

## 下载打包版（推荐）

从 [Releases](https://github.com/kisson888/to-do/releases) 下载 `WeFlowDashboard.zip`，解压后：

```
WeFlowDashboard/
├── 启动看板.bat    ← 双击启动
├── WeFlowDashboard.exe
├── config.example.json
├── dashboard.html
├── weflow_monitor.py
├── chat_html.py
└── _internal/
```

> **不需要安装 Python**，解压即用。  
> 首次运行请按提示设置密码并配置 WeFlow API Token。

## 功能

- 多会话微信聊天记录自动定时导出
- Web 看板：会话管理、手动导出、日报浏览、数据预览、HTML 聊天记录
- 发送者名称智能解析（备注 > 群昵称 > 微信昵称）
- 全量/增量/日期区间 三种导出模式
- 密码登录保护

## 安装与使用

### 前置条件

1. **Python 3.8+**（推荐 3.10+）
2. **WeFlow 桌面端 v4.5.1 稳定版** (https://github.com/hicccc77/WeFlow) — 已安装并运行，HTTP API 服务已开启（默认端口 5031）

### 快速开始

```bash
# 1. 克隆项目
git clone <your-repo-url> weflow-monitor
cd weflow-monitor

# 2. 一键安装（创建目录、生成 config.json）
python setup.py

# 3. 编辑配置填入 WeFlow API Token
#    Windows: notepad config.json
#    Linux/Mac: vim config.json

# 4. 设置看板登录密码
python dashboard_server.py --set-password <你的密码>

# 5. 启动看板
python dashboard_server.py
```

或使用安装脚本 `setup.py` 一键完成目录创建和配置生成。

首次启动会提示设置密码：
```bash
python dashboard_server.py --set-password <你的密码>
```

打开浏览器访问 http://127.0.0.1:8765

### 启动方式

| 方式 | 命令 | 说明 |
|------|------|------|
| 命令行 | `python dashboard_server.py` | 前台运行，有控制台窗口 |
| 静默启动 | `start_dashboard.vbs` (Windows) | 双击运行，无窗口 |
| 批量启动 | `start_dashboard.bat` (Windows) | 双击运行，短暂显示窗口 |

### 配置说明

编辑 `config.json`：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `api_base_url` | WeFlow API 地址 | `http://127.0.0.1:5031` |
| `access_token` | WeFlow API 认证令牌 | 从 WeFlow 设置获取 |
| `data_dir` | JSON/HTML 数据存储目录 | `data`（相对路径） |
| `report_dir` | 日报存储目录 | `reports`（相对路径） |
| `auth.enabled` | 是否启用密码认证 | `true` |

### 自行打包（高级）

如果你有 Python 环境，也可以自己打包成 exe：

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 打包
cd weflow-monitor
pyinstaller --onedir --name WeFlowDashboard \
  --add-data "dashboard.html;." \
  --add-data "config.example.json;." \
  --add-data "start_dashboard.bat;." \
  --add-data "weflow_monitor.py;." \
  --add-data "chat_html.py;." \
  --hidden-import weflow_monitor \
  --hidden-import chat_html \
  --console \
  dashboard_server.py

# 3. 输出在 dist/WeFlowDashboard/
```

## 项目结构

```
weflow-monitor/
├── dashboard_server.py      # Web 看板后端 (HTTP server, 端口 8765)
├── dashboard.html           # 前端看板页面
├── weflow_monitor.py        # 核心导出引擎
├── chat_html.py             # HTML 聊天记录生成器
├── config.json              # 配置文件（本地，不提交）
├── config.example.json      # 配置模板
├── start_dashboard.bat      # Windows 启动脚本
├── start_dashboard.vbs      # Windows 静默启动
├── data/                    # 聊天记录数据（运行时生成）
├── reports/                 # 日报文件（运行时生成）
└── README.md
```

## API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 系统状态（会话、在线状态、统计数据） |
| `GET /api/sessions/weflow` | 从 WeFlow 获取全量会话列表 |
| `POST /api/run` | 自动定时增量导出 |
| `POST /api/run/full` | 全量导出（选中会话） |
| `POST /api/run/manual-incr` | 手动增量导出（不影响自动 checkpoint） |
| `POST /api/run-range` | 指定日期区间导出 |
| `POST /api/run/quick` | 单个会话快捷导出（全量/增量） |
| `GET /api/data/<session>` | 获取会话的导出文件列表 |
| `GET /api/data-html/<session>/<file>` | 获取 HTML 聊天文件内容 |

## 许可证

MIT
