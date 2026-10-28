import sys

from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, SPECPATH)
from pcbt_app import APP_TITLE  # noqa: E402

hiddenimports = collect_submodules("can.interfaces") + [
    "serial",
    "serial.tools.list_ports",
    "serial.tools.list_ports_windows",
]

a = Analysis(
    ["pcbt_app/app.py"],
    pathex=[],
    binaries=[("pcbt_app/transport/ControlCANFD.dll", ".")],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Do not package DLLs discovered through Codex's document/media runtime PATH.
# In particular, its app-local UCRT/API-set DLLs override Windows system DLLs
# and prevent PySide6.QtWidgets from loading in the frozen application.
a.binaries = [
    entry for entry in a.binaries
    if "codex-runtimes" not in entry[1].lower()
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_TITLE,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
