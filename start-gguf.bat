@echo off
setlocal
cd /d "%~dp0"
set "QWEN_PYTHON=.venv\Scripts\python.exe"
if not exist "%QWEN_PYTHON%" (
  echo Complete the Python environment setup from README.md first.
  pause
  exit /b 1
)
set QWEN_BACKEND=sdcpp
set QWEN_GPU_ID=0
"%QWEN_PYTHON%" server.py --model turbo
pause
