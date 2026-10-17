@echo off
call "%~dp0Start-WebUI.bat" -Mock %*
exit /b %errorlevel%
