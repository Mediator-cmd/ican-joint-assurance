@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configure-ai.ps1"
if errorlevel 1 (
    echo.
    echo Failed to save AI configuration.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1"
if errorlevel 1 (
    echo.
    echo AI configuration was saved, but the project could not start.
    pause
    exit /b 1
)

endlocal
