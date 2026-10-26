@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto check
echo Creating a local Python environment. Python 3.12 or newer is required.
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
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from PySide6.QtWidgets import QApplication; import PIL, numpy, psd_tools, pypdfium2, scipy, aggdraw"
if errorlevel 1 goto failed
goto run
:check
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] < (3,16) else 'Python 3.12 - 3.15 is required.')"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from PySide6.QtWidgets import QApplication; import PIL, numpy, psd_tools, pypdfium2, scipy, aggdraw" >nul 2>nul
if errorlevel 1 goto install
:run
".venv\Scripts\python.exe" -m image_tools %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Startup failed. Check the message above. Use Python 3.12 - 3.15 with PATH enabled.
pause
exit /b 1
