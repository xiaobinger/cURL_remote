# -*- mode: python ; coding: utf-8 -*-

import os

a = Analysis(
    ['curl_generator.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('curlgen/web', 'curlgen/web'),
        ('logo.png', '.'),
    ],
    hiddenimports=[
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtWebChannel',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='CurlGenerator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['logo.png'],
)
