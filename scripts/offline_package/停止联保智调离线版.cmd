@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0停止联保智调离线版.ps1"
if errorlevel 1 (
    echo.
    echo Failed to stop the offline demonstration safely.
    pause
    exit /b 1
)

endlocal
