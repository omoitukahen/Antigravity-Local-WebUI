@echo off
setlocal
chcp 65001 >nul
set "webuiPowerShell=%ProgramFiles%\PowerShell\7\pwsh.exe"
if exist "%webuiPowerShell%" goto run
where pwsh.exe >nul 2>&1
if errorlevel 1 (
    echo PowerShell 7 is required: https://aka.ms/powershell-release?tag=stable
    pause
    exit /b 1
)
set "webuiPowerShell=pwsh.exe"
:run
"%webuiPowerShell%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1" %*
set "webuiExit=%errorlevel%"
if not "%webuiExit%"=="0" pause
exit /b %webuiExit%
