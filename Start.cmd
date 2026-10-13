@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "NodeHarbor" ".venv\Scripts\pythonw.exe" "%~dp0NodeHarbor.pyw"
  exit /b 0
)
python -c "import yaml, requests, tkinter" >nul 2>&1
if errorlevel 1 (
  echo Please run scripts\windows\setup.cmd first.
  pause
  exit /b 1
)
start "NodeHarbor" pythonw "%~dp0NodeHarbor.pyw"
