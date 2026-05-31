# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['dashboard_server.py'],
    pathex=[],
    binaries=[],
    datas=[('dashboard.html', '.'), ('config.example.json', '.'), ('setup_wizard.html', '.'), ('weflow_monitor.py', '.'), ('chat_html.py', '.'), ('verify_hello.py', '.')],
    hiddenimports=['weflow_monitor', 'chat_html'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WeFlowDashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WeFlowDashboard',
)
