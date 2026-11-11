@echo off
setlocal
cd /d "%~dp0"
set "NO_PAUSE="
if /i "%~1"=="--ci" set "NO_PAUSE=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows.ps1"
set "BUILD_RESULT=%ERRORLEVEL%"
if not defined NO_PAUSE pause
exit /b %BUILD_RESULT%
