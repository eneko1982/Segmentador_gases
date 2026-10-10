#!/usr/bin/env bash
# Abre el panel del Segmentador de gases (Linux / macOS).
# La primera vez crea un entorno propio (.venv) e instala las dependencias.
set -e
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then echo "No se encuentra Python 3.10 o superior."; exit 1; fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv
if [ ! -f .venv/instalado.txt ]; then
  echo "Instalando las dependencias (solo la primera vez)..."
  .venv/bin/python -m pip install -r requirements.txt
  echo ok > .venv/instalado.txt
fi
echo "Abriendo el panel en http://localhost:8501"
exec .venv/bin/python -m streamlit run app.py "$@"
