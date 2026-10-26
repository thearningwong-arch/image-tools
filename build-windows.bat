@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:python_fallback
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] < (3,16) else 'Python 3.12 - 3.15 is required.')"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name ImageTools --paths . --collect-all psd_tools --collect-all pypdfium2 --collect-all pypdfium2_raw scripts\desktop_entry.py
if errorlevel 1 goto failed
echo.
echo Built: dist\ImageTools\ImageTools.exe
echo Keep the entire dist\ImageTools folder together when copying it to another PC.
pause
exit /b 0
:failed
echo.
echo Build failed. See the error above.
pause
exit /b 1
