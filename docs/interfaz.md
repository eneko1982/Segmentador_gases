# Interfaz (`app.py`)

Aplicación en Streamlit para usar el simulador y el detector sin la línea de comandos.

## Cómo abrirla en tu ordenador

Necesitas Python 3.10 o superior.

```bash
git clone https://github.com/eneko1982/Segmentador_gases
cd Segmentador_gases
git checkout claude/loving-allen-jv3c71        # la rama de trabajo
python -m venv .venv
source .venv/bin/activate                       # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Se abre en el navegador (por defecto, http://localhost:8501).

## Pestañas

| Pestaña | Qué hace |
|---|---|
| **Simulador** | Preset + 10 controles principales (escena, gas, caudal, viento, ráfagas, turbulencia, temperatura del fondo, NETD, duración, inicio de la fuga) + los 80 parámetros agrupados. Vista previa rápida (½ resolución, ~4 veces más rápida) o completa. Muestra el vídeo con el contorno de la verdad, el mapa del cambio causado por el gas y los píxeles con gas en el tiempo. Descarga `.npz`, `.json` y `.gif`. Enseña el comando equivalente. |
| **Detector** | Corre `gas_detect.py` sobre la última secuencia simulada o sobre un `.npz` subido. Parámetros: entrada (u8/temp), periodo sin gas, `z_low`, `z_high`, suavizado, promedio temporal, área mínima. Muestra ruido tras el filtrado, retardo, falsa alarma, % de píxeles sin gas marcados y una tabla de IoU, recall y precisión. Las líneas naranja (detector) y azul (verdad) se dibujan con el mismo umbral, a elegir. |
| **Benchmark** | Gráfica y tabla de `results/results.csv` por caudal y escena, con la métrica y la entrada a elegir. Botón para relanzar el barrido (≈2 min con 4 procesos). |
| **Comparar umbrales** | La figura de detector frente a verdad con el mismo umbral, para la escena y los caudales que elijas. |

## Capturas

![Simulador](figures/app_simulador.png)
![Detector](figures/app_detector.png)

## Limitaciones conocidas

- Solo carga secuencias `.npz` de `ogi_sim.py`. Los vídeos reales (`.mp4`, cámara en directo) llegarán con el programa de captura (paso 6 de la hoja de ruta).
- La secuencia simulada vive en la memoria de la sesión del navegador: al recargar la página se pierde (descárgala si quieres conservarla).
- Con resolución completa, una secuencia de 5 s ocupa unos 140 MB en memoria; las muy largas necesitan la escritura por trozos (paso A3).

## Publicarla en la web (opcional, no hecho)

Hay dos caminos habituales:

1. **Streamlit Community Cloud**: conecta tu cuenta de GitHub, elige el repositorio, la rama y `app.py`. Es gratuito, pero los recursos son limitados (poca memoria y CPU), así que usa la vista previa rápida y secuencias cortas. El repositorio debe poder leerlo ese servicio.
2. **Un servidor propio** (por ejemplo una máquina de la empresa o de la universidad): `streamlit run app.py --server.port 8501 --server.address 0.0.0.0`. Sin protección, cualquiera con acceso a la red podría abrirla; pónla detrás de una contraseña o una VPN.

La interfaz no guarda ni envía datos fuera de la sesión.
