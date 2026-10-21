@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "autoqaExitCode=%errorlevel%"
if /I not "%~1"=="-NoBrowser" if /I not "%~2"=="-NoBrowser" pause
exit /b %autoqaExitCode%
