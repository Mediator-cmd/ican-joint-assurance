@echo off
setlocal
pushd "%~dp0app\joint_assurance_offline" || (
    echo Unable to locate the offline application files.
    pause
    exit /b 1
)

start "Joint Assurance Offline" ".\joint_assurance_offline.exe"
popd
endlocal
