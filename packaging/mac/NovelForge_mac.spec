# -*- mode: python ; coding: utf-8 -*-
# NovelForge macOS 打包 spec（PyInstaller 6.x）
# 仅用于 macOS：PyInstaller 不支持跨平台交叉编译，此 spec 必须在 Mac 上执行。
# 路径全部基于本文件位置解析，在任何工作目录下执行均可。

import os

SPEC_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, "..", ".."))

SRC = os.path.join(PROJECT_ROOT, "NovelForge.py")
ICO = os.path.join(SPEC_DIR, "app.icns")
APP_ICO = os.path.join(PROJECT_ROOT, "app.ico")

a = Analysis(
    [SRC],
    pathex=[],
    binaries=[],
    datas=[(APP_ICO, ".")],          # app.ico 打入 bundle，运行时窗口/任务栏图标用
    hiddenimports=['yaml'],
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
    name='NovelForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # macOS 下 UPX 无意义且易引入兼容问题
    console=False,                   # 窗口程序（无控制台）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,                # 默认跟随本机架构；要通用包可改为 'universal2'
    codesign_identity=None,          # None = ad-hoc 签名
    entitlements_file=None,
    icon=[ICO],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='NovelForge',
)

app = BUNDLE(
    coll,
    name='NovelForge.app',
    icon=ICO,
    bundle_identifier='com.novelforge.app',
    version='1.0.0',
    info_plist={
        'CFBundleName': 'NovelForge',
        'CFBundleDisplayName': 'NovelForge 小说生产工坊',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,      # Retina 高分屏
        'LSMinimumSystemVersion': '11.0',     # macOS Big Sur 及以上
        'NSPrincipalClass': 'NSApplication',
    },
)
