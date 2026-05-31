@echo off
chcp 65001 >nul
title WeFlow Dashboard Server

setlocal
set "SCRIPT_DIR=%~dp0"
set PYTHONIOENCODING=utf-8

echo ========================================
echo   WeFlow Monitor Dashboard Server
echo ========================================
echo.
echo  Starting server silently...
echo  Open http://127.0.0.1:8765 in browser
echo.
echo  To stop: use web dashboard "Stop" button
echo ========================================
echo.

:: Try python (should be in PATH if Python is installed)
"%SCRIPT_DIR%dashboard_server.py" 2>nul
if %errorlevel% equ 0 goto :launched

:: Fallback: python3
python3 "%SCRIPT_DIR%dashboard_server.py" 2>nul
if %errorlevel% equ 0 goto :launched

:: Fallback: python
python "%SCRIPT_DIR%dashboard_server.py"
if %errorlevel% neq 0 (
  echo [ERROR] Python not found. Please install Python 3.8+ and add to PATH.
  pause
  exit /b 1
)

:launched
timeout /t 3 >nul
start "" "http://127.0.0.1:8765"
timeout /t 2 >nul
exit
