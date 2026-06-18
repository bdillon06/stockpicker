# Build the Windows .exe and (if Inno Setup is present) a Setup installer.
# Run on Windows from the project root, in PowerShell:
#     powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

Write-Host "==> Setting up build venv (.venv-build)"
if (-not (Test-Path ".venv-build")) { & $py -m venv .venv-build }
& ".venv-build\Scripts\python.exe" -m pip install -q --upgrade pip
& ".venv-build\Scripts\python.exe" -m pip install -q -r requirements.txt -r packaging\requirements-build.txt

Write-Host "==> Cleaning previous build"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller"
& ".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean packaging\swingfinder.spec

$version = Get-Date -Format "yyyy.MM.dd"

# Build the Setup.exe if Inno Setup's compiler (ISCC) is installed.
$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                     "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if ($iscc) {
    Write-Host "==> Building installer with Inno Setup"
    & $iscc "/DMyAppVersion=$version" packaging\installer.iss
    Write-Host "==> Done"
    Write-Host "    Exe   : dist\SwingFinder.exe"
    Write-Host "    Share : dist\SwingFinder-Setup-$version.exe"
} else {
    Write-Host "==> Done (Inno Setup not found — produced the bare .exe only)"
    Write-Host "    Exe   : dist\SwingFinder.exe"
    Write-Host "    To build a Setup.exe installer, install Inno Setup 6"
    Write-Host "    (https://jrsoftware.org/isdl.php) and re-run this script."
}
