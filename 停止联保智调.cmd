@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-project.ps1"
if errorlevel 1 (
    echo.
    echo Failed to stop Joint Assurance Dashboard safely.
    pause
    exit /b 1
)

endlocal
