@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Нужен Python 3.14: для onnxruntime пока нет сборок под 3.15.
py -3.14 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.14 не найден. Установите его командой: py install 3.14
  exit /b 1
)

if not exist .venv (
  echo Создаю виртуальное окружение...
  py -3.14 -m venv .venv || exit /b 1
)

echo Устанавливаю зависимости...
.venv\Scripts\python -m pip install --disable-pip-version-check -q --upgrade pip || exit /b 1
.venv\Scripts\python -m pip install --disable-pip-version-check -q -e ".[dev]" || exit /b 1

echo Запускаю тесты...
.venv\Scripts\python -m pytest -q || exit /b 1

echo Собираю приложение...
.venv\Scripts\pyinstaller --noconfirm --clean md_blinder.spec || exit /b 1

echo.
echo Готово: dist\MD Blinder\MD Blinder.exe
echo Папку dist\MD Blinder можно скопировать куда угодно целиком.
