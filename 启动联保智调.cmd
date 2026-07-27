@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1"
if errorlevel 1 (
    echo.
    echo Failed to start Joint Assurance Dashboard.
    pause
    exit /b 1
)

endlocal
