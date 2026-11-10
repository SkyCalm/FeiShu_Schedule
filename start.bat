@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app.py
) else (
  python app.py
)
if errorlevel 1 (
  echo.
  echo Please install Python 3.10+ from https://www.python.org/downloads/
  echo Enable "Add Python to PATH" during installation.
  pause
)
