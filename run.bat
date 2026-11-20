@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Сначала запустите build.bat
  exit /b 1
)
.venv\Scripts\python -m md_blinder
