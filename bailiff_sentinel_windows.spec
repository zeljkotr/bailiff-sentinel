# PyInstaller build configuration for Windows.
a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
        ("paycheck_sentinel/fonts", "paycheck_sentinel/fonts"),
    ],
    hiddenimports=[
        "psycopg",
        "psycopg.pq",
        "flask_session.filesystem",
        "cachelib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gunicorn"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BailiffSentinel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BailiffSentinel",
)
