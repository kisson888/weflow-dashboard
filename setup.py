#!/usr/bin/env python3
"""
WeFlow Monitor — 快速安装脚本
===============================
用于在另一台电脑上首次部署。
用法： python setup.py

步骤：
1. 检测 Python 版本
2. 创建 config.json（从 config.example.json 复制）
3. 创建空 data/ 和 reports/ 目录
4. 提示用户设置看板密码
5. 提供一键启动命令
"""

import os, sys, shutil, json
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
EXAMPLE = BASE_DIR / "config.example.json"
CONFIG = BASE_DIR / "config.json"
DATA = BASE_DIR / "data"
REPORTS = BASE_DIR / "reports"

def check_python():
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 8):
        print(f"❌ 需要 Python 3.8+，当前: {v.major}.{v.minor}.{v.micro}")
        sys.exit(1)
    print(f"✅ Python {v.major}.{v.minor}.{v.micro}")

def setup_dirs():
    DATA.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    print(f"✅ 数据目录: {DATA}")
    print(f"✅ 报告目录: {REPORTS}")

def setup_config():
    if CONFIG.exists():
        print(f"⚠️  config.json 已存在，跳过")
        return
    if not EXAMPLE.exists():
        print(f"❌ 找不到 config.example.json")
        sys.exit(1)
    shutil.copy2(EXAMPLE, CONFIG)
    print(f"✅ 已创建 config.json（请编辑填入 WeFlow API Token）")
    print(f"   编辑命令: notepad {CONFIG}")

def main():
    print("=" * 50)
    print("  WeFlow Monitor — 安装助手")
    print("=" * 50)
    print()
    check_python()
    setup_dirs()
    setup_config()
    print()
    print("=" * 50)
    print("  ✅ 安装完成！")
    print()
    print("  接下来：")
    print("  1. 编辑 config.json 填入 WeFlow API Token")
    print("  2. 设置看板密码：")
    print(f"     python {BASE_DIR / 'dashboard_server.py'} --set-password <你的密码>")
    print("  3. 启动看板：")
    print(f"     python {BASE_DIR / 'dashboard_server.py'}")
    print("  4. 浏览器打开 http://127.0.0.1:8765")
    print("=" * 50)

if __name__ == "__main__":
    main()
