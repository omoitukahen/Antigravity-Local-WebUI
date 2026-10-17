@echo off
setlocal
chcp 65001 >nul
set "webuiPowerShell=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%webuiPowerShell%" set "webuiPowerShell=pwsh.exe"
"%webuiPowerShell%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-WebUI.ps1"
set "webuiExit=%errorlevel%"
if not "%webuiExit%"=="0" (
    echo.
    echo WebUI failed to stop. Read the message above.
    pause
)
exit /b %webuiExit%
