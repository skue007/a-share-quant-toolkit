# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_industry_mismatch.py'],
    pathex=['industry-mismatch'],
    binaries=[],
    datas=[('D:/AI/OPEN/a-share-quant-toolkit/industry-mismatch/industry_mismatch/data/industry_map.json', 'industry_mismatch/data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['streamlit', 'plotly', 'altair', 'pyarrow', 'tkinter', 'matplotlib'],
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
    name='industry-mismatch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
