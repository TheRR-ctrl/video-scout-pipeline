@echo off
rem Doble clic aqui para abrir el panel en Windows.
rem La primera vez crea el entorno de Python (.venv); lo demas (actualizar,
rem librerias, ffmpeg, restaurar un respaldo, abrir el navegador) lo hace
rem iniciar_windows.py.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Video Scout - Mesa de Revision

if exist ".venv\Scripts\python.exe" goto arrancar

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Falta Python. Instalandolo con winget...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo   Listo. Cierra esta ventana y vuelve a abrir iniciar_windows.bat.
  pause
  exit /b 0
)
echo.
echo   Preparando el entorno de Python (solo la primera vez)...
%PY% -m venv .venv
if errorlevel 1 (
  echo   No se pudo crear el entorno. Revisa el mensaje de arriba.
  pause
  exit /b 1
)

:arrancar
".venv\Scripts\python.exe" iniciar_windows.py
if errorlevel 1 pause
