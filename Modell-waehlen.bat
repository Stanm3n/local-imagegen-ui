@echo off
setlocal
cd /d "%~dp0"
py -3.11 -c "import sys" >nul 2>&1
if errorlevel 1 (
  python launch.py --choose
) else (
  py -3.11 launch.py --choose
)
if errorlevel 1 pause
