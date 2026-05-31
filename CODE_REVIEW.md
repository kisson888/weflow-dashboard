# WeFlow Monitor — 代码审查标准与流程

## 一、审查原则

1. **安全优先**：所有用户输入必须转义，API Token、密码哈希等敏感信息绝不硬编码
2. **功能正确**：不引入逻辑错误，边界条件有处理
3. **可移植**：零外部依赖，路径相对化，cross-platform 意识
4. **可维护**：命名清晰，单一职责，避免深层嵌套
5. **可测试**：核心逻辑可独立调用验证

## 二、审查分级

| 标记 | 级别 | 含义 | 合并门禁 |
|------|------|------|---------|
| 🔴 Blocker | 阻塞 | 安全漏洞、数据丢失、功能不可用 | ❌ 必须修复 |
| 🟡 Suggestion | 建议 | 潜在 bug、性能问题、代码异味 | ⚠️ 强烈建议修复 |
| 💭 Nit | 微调 | 命名风格、注释、格式 | 💡 可商议 |

## 三、Python 代码规范

### 3.1 安全

```python
# ✅ GOOD: 使用参数化路径
path = BASE_DIR / "data" / safe_filename

# ❌ BAD: 拼接用户输入到路径
path = os.path.join(BASE_DIR, user_input)
```

- 配置文件中的敏感字段（Token、密码哈希）通过 `.gitignore` 排除 `config.json`
- 外部依赖只用 Python 标准库，不引入第三方包
- 对用户提供的消息内容做 HTML 转义：`html.escape(content)`
- 不使用 `eval()`, `exec()`, `pickle.loads()` 处理不可信数据

### 3.2 路径与可移植

```python
# ✅ GOOD: 使用 __file__ 相对路径
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"

# ❌ BAD: 硬编码绝对路径
DATA_DIR = Path("E:/WorkBuddy/weflow-monitor/data")
DATA_DIR = Path("C:/Users/xxx/data")
```

- 所有路径基于 `Path(__file__).parent` 相对计算
- 不硬含用户名、盘符、特殊系统路径
- `sys.executable` 用于 subprocess 调用，而非硬编码 Python 路径

### 3.3 函数设计

- 单一职责：一个函数只做一件事
- 参数不超过 5 个（超出则用 `**kwargs` 或数据类）
- 错误处理：`try/except` 包裹可预见的异常，避免裸 `except:`

```python
# ✅ GOOD: 精确捕获
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    return None

# ❌ BAD: 吞掉所有异常
try:
    data = json.loads(raw)
except:
    pass
```

### 3.4 导入规范

```python
# 标准库优先，按分组排序
import json
import re
import sys
from datetime import datetime
from pathlib import Path
```

- 不使用 `from module import *`
- 内部模块间导入尽量使用 `importlib`（动态加载）或明确路径

## 四、JavaScript / HTML 规范

### 4.1 安全

```html
<!-- ✅ GOOD: 使用 textContent 显示用户数据 -->
<div id="msg-content"></div>
<script>el.textContent = userData;</script>

<!-- ❌ BAD: 用户数据直接插入 innerHTML -->
<script>el.innerHTML = userData;</script>
```

- 所有用户生成的内容使用 `textContent` 而非 `innerHTML`
- 必须用 `innerHTML` 的场合提前做 `html.escape()`
- API 路径使用编码后的参数：`encodeURIComponent()`
- 仅在本页面显示内容，iframe 加载外部 HTML 时用最严格 sandbox：`sandbox=""`

### 4.2 错误处理

```javascript
// ✅ GOOD: 每个 fetch 都要 catch
async function api(method, path, body) {
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  }
  return resp.json();
}

// ❌ BAD: 不检查 HTTP 状态码
async function api(method, path, body) {
  return (await fetch(path, opts)).json();
}
```

- 所有 `fetch` 调用必须检查 `resp.ok`
- `api()` 工具函数对 `401` 自动跳转登录
- `catch` 中显示友好的 Toast 提示

### 4.3 DOM 操作

- 避免大量 DOM 操作字符串拼接 → 使用模板字符串 + `join('')`
- 事件绑定用 `onclick` 属性保持在可接受范围（小型项目），大型项目用事件委托

### 4.4 代码组织

- 所有全局函数和变量应当有注释说明用途
- 异步操作用 `async/await` 而非 `.then().catch()`

## 五、HTML 模板规范

- 所有内联样式使用 `<style>` 块统一管理
- 字体、颜色、间距从 CSS 变量或 class 派生
- 响应式设计：移动端最小宽度 320px

## 六、审查流程

### 6.1 日常开发流程

```
[开发] → [自测] → [提交 PR] → [自动化检查] → [代码审查] → [合并]
```

### 6.2 审查清单

每次审查逐项检核：

- [ ] 是否有硬编码的用户路径或 Token？
- [ ] 是否有 Unicode/编码问题（GBK vs UTF-8）？
- [ ] 是否有未处理的 `except:` 裸异常？
- [ ] 异步 `fetch` 是否有 `.catch()` 或 `try/catch`？
- [ ] 用户消息内容是否用 `textContent` 显示？
- [ ] 文件路径是否基于 `__file__` 相对计算？
- [ ] 代码中是否有中文 hardcode 需外部化？
- [ ] HTML 生成的用户内容是否 `html.escape()`？
- [ ] 新功能是否影响现有导出文件格式兼容性？

### 6.3 自动化检查（预提交）

```bash
# Python 语法检查
python -m py_compile dashboard_server.py
python -m py_compile weflow_monitor.py
python -m py_compile chat_html.py

# JS 大括号平衡检查
grep -c '{' dashboard.html | xargs -I{} sh -c 'test "$(grep -o "{" dashboard.html | wc -l)" = "$(grep -o "}" dashboard.html | wc -l)"'

# 硬编码路径扫描
grep -n "C:\\\\|/home/|/Users/" *.py *.html *.bat 2>/dev/null

# 敏感信息扫描
grep -n "access_token\|password_hash\|secret\|token" config.json 2>/dev/null
```

## 七、Git 提交规范

### 7.1 提交信息格式

```
<type>: <简短描述>

<详细说明（可选）>
```

| type | 用途 | 示例 |
|------|------|------|
| `fix` | Bug 修复 | `fix: 全量导出排除今天消息导致0条` |
| `feat` | 新功能 | `feat: 新增群成员昵称解析` |
| `refactor` | 重构 | `refactor: 路径统一使用 Path(__file__)` |
| `docs` | 文档 | `docs: 更新 API 端点文档` |
| `style` | 格式 | `style: 格式化 chat_html.py` |
| `chore` | 杂项 | `chore: 添加 .gitignore` |

### 7.2 禁止提交的文件

- `config.json`（含 API Token 和密码哈希）
- `checkpoint.json`（含会话状态）
- `data/` 目录（运行时数据）
- `reports/` 目录（运行时数据）
- `__pycache__/` 目录
- `*.pyc` 文件

## 八、发布前检查清单

- [ ] `git status` 确认无意外修改
- [ ] `.gitignore` 排除了所有运行时文件
- [ ] `config.example.json` 是最新结构（无敏感信息）
- [ ] 启动脚本使用 `%(~dp0)` 或 `sys.executable` 相对路径
- [ ] `README.md` 有安装步骤
- [ ] 审查清单中的所有 [🔴 Blocker] 已修复
