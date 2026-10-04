@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Control Horario
echo ============================================
echo   CONTROL HORARIO - Instalacion y arranque
echo ============================================
echo.

rem --- Buscar Python instalado ---
set "PY="
where python >nul 2>nul
if %errorlevel%==0 ( set "PY=python" )
if not defined PY (
    where py >nul 2>nul
    if !errorlevel!==0 ( set "PY=py -3" )
)
if not defined PY (
    echo No se encontro Python instalado.
    echo.
    echo Pasos:
    echo   1. Abre https://www.python.org/downloads/
    echo   2. Descarga la ultima version 3.12 o 3.11
    echo   3. Al instalarlo MARCA la casilla "Add Python to PATH"
    echo   4. Vuelve a ejecutar este archivo.
    echo.
    pause
    exit /b 1
)

rem --- Comprobar version minima (3.9) ---
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not %errorlevel%==0 (
    echo Tu version de Python es antigua (se necesita 3.9 o superior).
    echo Actualizalo desde https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

rem --- Crear entorno virtual si no existe ---
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creando el entorno de la aplicacion...
    %PY% -m venv .venv
)

rem --- Instalar dependencias (rapido si ya estan) ---
echo [2/3] Instalando dependencias (solo consume tiempo la primera vez)...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not %errorlevel%==0 (
    echo.
    echo Error al instalar las dependencias. Revisa tu conexion a internet e intentalo de nuevo.
    echo.
    pause
    exit /b 1
)

rem --- Buscar puerto libre (8000 a 8010) ---
echo [3/3] Arrancando la aplicacion...
set "PORT=8000"
for /L %%i in (8000,1,8010) do (
    set "PORT=%%i"
    ".venv\Scripts\python.exe" -c "import socket,sys; s=socket.socket(); s.settimeout(0.4); sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))!=0 else 1)" "!PORT!" >nul 2>nul
    if !errorlevel!==0 goto :port_free
)
echo No hay puerto libre (8000-8010). Cierra otra instancia de la aplicacion e intenta de nuevo.
echo.
pause
exit /b 1

:port_free
echo.
echo Control Horario en http://127.0.0.1:!PORT!
echo PIN por defecto: 1234
echo.
echo IMPORTANTE: no cierres esta ventana mientras uses la aplicacion.
echo Para detenerla, cierra esta ventana o pulsa Ctrl+C en ella.
echo.
echo El navegador se abrira solo dentro de unos segundos...
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:!PORT!"

rem --- Lanzar servidor (en primer plano) ---
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port "!PORT!"
echo.
echo La aplicacion se ha detenido.
pause