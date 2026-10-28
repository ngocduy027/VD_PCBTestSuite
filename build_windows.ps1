$ErrorActionPreference = "Stop"

py -3.14 -m pip install -e .[dev]
py -3.14 -m PyInstaller pcbt_app.spec --noconfirm --clean
