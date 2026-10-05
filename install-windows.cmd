@echo off
setlocal
title Install QEMU ISO Lab
echo Installing QEMU ISO Lab. Windows may ask for administrator approval for WSL.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; try { $dir = Join-Path $env:LOCALAPPDATA 'QemuIsoLab'; New-Item -ItemType Directory -Force -Path $dir | Out-Null; $script = Join-Path $dir 'setup-windows.ps1'; Invoke-WebRequest -UseBasicParsing 'https://manzolo.github.io/qemu-iso-lab/install.ps1' -OutFile $script; & $script } catch { Write-Host $_ -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
    echo.
    echo Setup did not finish. Read the error above, then run this installer again.
    pause
    exit /b 1
)
echo.
pause
