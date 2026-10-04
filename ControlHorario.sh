#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

echo "============================================"
echo "  CONTROL HORARIO - Instalacion y arranque"
echo "============================================"
echo

PYTHON=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        PYTHON="$c"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "No se encontro Python instalado."
    echo
    echo "Pasos:"
    echo "  1. Abre https://www.python.org/downloads/"
    echo "  2. Descarga la ultima version 3.12 o 3.11"
    echo "  3. Instalalo (en macOS acepta el instalador; en Linux usa el instalador oficial o tu gestor de paquetes)"
    echo "  4. Vuelve a ejecutar este archivo."
    echo
    echo "Alternativa en Linux:"
    echo "  sudo apt install python3 python3-venv"
    echo
    read -r -p "Pulsa Enter para salir..."
    exit 1
fi

if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >/dev/null 2>&1; then
    echo "Tu version de Python es antigua (se necesita 3.9 o superior)."
    echo "Actualizalo desde https://www.python.org/downloads/"
    echo
    read -r -p "Pulsa Enter para salir..."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[1/3] Creando el entorno de la aplicacion..."
    "$PYTHON" -m venv .venv || { echo "No se pudo crear el entorno virtual."; read -r -p "Enter para salir..."; exit 1; }
fi

echo "[2/3] Instalando dependencias (solo consume tiempo la primera vez)..."
.venv/bin/python -m pip install -q -r requirements.txt || {
    echo
    echo "Error al instalar las dependencias. Revisa tu conexion a internet e intentalo de nuevo."
    read -r -p "Pulsa Enter para salir..."
    exit 1
}

echo "[3/3] Arrancando la aplicacion..."
PORT=""
for p in 8000 8001 8002 8003 8004 8005 8006 8007 8008 8009 8010; do
    if .venv/bin/python -c "import socket,sys; s=socket.socket(); s.settimeout(0.4); sys.exit(0 if s.connect_ex(('127.0.0.1',$p))!=0 else 1)" >/dev/null 2>&1; then
        PORT=$p
        break
    fi
done

if [ -z "$PORT" ]; then
    echo "No hay puerto libre (8000-8010). Cierra otra instancia de la aplicacion e intenta de nuevo."
    read -r -p "Pulsa Enter para salir..."
    exit 1
fi

echo
echo "Control Horario en http://127.0.0.1:$PORT"
echo "PIN por defecto: 1234"
echo
echo "IMPORTANTE: no cierres esta ventana mientras uses la aplicacion."
echo "Para detenerla cierra esta ventana o pulsa Ctrl+C en ella."
echo
echo "El navegador se abrira solo dentro de unos segundos..."

( sleep 3
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$PORT"
  elif command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT"
  fi
) &

exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"