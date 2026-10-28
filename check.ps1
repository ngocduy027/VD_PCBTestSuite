# Pre-release checks: unit/GUI tests, lint and type check. Exits non-zero on failure.
# Uses .venv if present (note: "py -3.14" ignores an activated venv).
$ErrorActionPreference = "Stop"
$env:QT_QPA_PLATFORM = "offscreen"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "py"; $pyArgs = @("-3.14") } else { $pyArgs = @() }

& $python @pyArgs -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python @pyArgs -m ruff check pcbt_app tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python @pyArgs -m mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "All checks passed."
