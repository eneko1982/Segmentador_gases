@echo off
rem Abre el panel del Segmentador de gases (Windows). Doble clic para ejecutar.
rem La primera vez crea un entorno propio (.venv) e instala las dependencias.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo No se encuentra Python. Instala Python 3.10 o superior desde el Centro de software de la empresa
  echo o pidelo a TI, y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creando el entorno virtual...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)
if not exist ".venv\instalado.txt" (
  echo Instalando las dependencias ^(solo la primera vez, puede tardar unos minutos^)...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo La instalacion ha fallado. Si la red usa un proxy o un repositorio interno de paquetes,
    echo consulta con TI como configurar pip. Detalles en docs\interfaz.md.
    pause
    exit /b 1
  )
  echo ok> ".venv\instalado.txt"
)
echo.
echo Abriendo el panel. Si el navegador no se abre solo, entra en http://localhost:8501
echo Para cerrarlo, cierra esta ventana o pulsa Ctrl+C.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
