@echo off
setlocal
cd /d "%~dp0..\.."
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install Python with Tcl/Tk enabled.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B NodeHarbor.py init
if errorlevel 1 exit /b 1
echo Setup completed. Open Start.cmd to launch NodeHarbor.
pause
