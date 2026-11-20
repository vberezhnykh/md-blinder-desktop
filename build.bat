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
rem Версии из uv.lock, как в CI; на Windows там onnxruntime-directml вместо onnxruntime.
.venv\Scripts\python -m pip install --disable-pip-version-check -q uv==0.11.32 || exit /b 1
.venv\Scripts\uv export --frozen --extra dev --no-emit-project --no-hashes -o requirements-ci.txt || exit /b 1
.venv\Scripts\uv pip install --python .venv\Scripts\python.exe -r requirements-ci.txt || exit /b 1
.venv\Scripts\uv pip install --python .venv\Scripts\python.exe --no-deps -e . || exit /b 1

echo Запускаю тесты...
.venv\Scripts\python -m pytest -q || exit /b 1

echo Собираю приложение...
.venv\Scripts\pyinstaller --noconfirm --clean md_blinder.spec || exit /b 1

echo.
echo Готово: dist\MD Blinder\MD Blinder.exe
echo Папку dist\MD Blinder можно скопировать куда угодно целиком.
