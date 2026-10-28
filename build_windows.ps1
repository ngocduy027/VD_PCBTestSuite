# Build the single-file Windows executable into dist\ (runs check.ps1 first).
# Uses .venv if present (note: "py -3.14" ignores an activated venv).
$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "py"; $pyArgs = @("-3.14") } else { $pyArgs = @() }

& $python @pyArgs -m pip install -e .[dev]
& "$PSScriptRoot\check.ps1"
if ($LASTEXITCODE -ne 0) { throw "Checks failed; not building." }
& $python @pyArgs -m PyInstaller pcbt_app.spec --noconfirm --clean
