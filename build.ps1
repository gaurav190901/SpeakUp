# SpeakUp — Windows build script
# Usage:  .\build.ps1
# Output: dist\SpeakUp\SpeakUp.exe

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== SpeakUp Build ===" -ForegroundColor Cyan

# Verify we are in the project root
if (-not (Test-Path "app.py")) {
    Write-Error "Run this script from the project root (where app.py lives)."
    exit 1
}

# Clean previous build artifacts
Write-Host "Cleaning previous build..." -ForegroundColor Yellow
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

# Run PyInstaller
Write-Host "Running PyInstaller..." -ForegroundColor Yellow
python -m PyInstaller speakup.spec --clean --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
    exit 1
}

# Verify output
$exe = "dist\SpeakUp\SpeakUp.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "Build succeeded: $exe ($size MB)" -ForegroundColor Green
} else {
    Write-Error "Expected executable not found: $exe"
    exit 1
}

Write-Host "Done. Run: dist\SpeakUp\SpeakUp.exe" -ForegroundColor Cyan
