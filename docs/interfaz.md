# Interfaz (`app.py`)

Panel de control en Streamlit para ajustar el vídeo, la simulación y los cálculos, ver el vídeo con las máscaras superpuestas y exportar resultados e imágenes, sin la línea de comandos.

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

## Cómo está organizada

**Barra lateral (los ajustes)** — tres bloques, más un preset y los botones de ejecución:

| Bloque | Qué ajusta |
|---|---|
| **1 · Vídeo y cámara** | Escena (placa caliente / cielo frío), temperatura del fondo, ruido NETD, duración, resolución (rápida ½ o completa). Con «Más parámetros» se abren los demás: fps, tamaño del píxel, ruido de patrón fijo y de columna, deriva, desenfoque, ventana de 8 bit, geometría de la placa, etc. |
| **2 · Simulación del gas** | Gas, caudal, inicio de la fuga, viento, ráfagas y turbulencia. Con «Más parámetros»: cierre de la fuga, velocidad y ángulo de salida, flotabilidad, crecimiento de la pluma, resolución de la pluma, modos de turbulencia… |
| **3 · Cálculos del detector** | Entrada (u8 / temp), periodo sin gas (automático o manual), umbrales `z_low` y `z_high`, suavizado espacial, promedio temporal y área mínima. |

Botones: **▶ Simular y calcular** (simula y, si está marcado, calcula el detector), **Recalcular detector** (cuando solo cambias el bloque 3, sin volver a simular), **Cargar una secuencia .npz**, **Restablecer parámetros** y el comando equivalente para repetirlo desde la terminal.

**Pestañas**

| Pestaña | Qué hace |
|---|---|
| **🎬 Vídeo** | Reproductor con una, dos o tres vistas sincronizadas: *Cámara* (8 bit), *Estimación del detector* y *Cambio real* (mapas en mK). Sobre cada vista se superponen la **máscara real** (azul) y la **máscara detectada** (naranja), que se activan y desactivan con casillas, sin recalcular. Estilos: contorno, relleno y **acierto/error** (verde = acierto, naranja = falso positivo, azul = falso negativo). También: opacidad, grosor, tamaño, velocidad, bucle, fotograma a fotograma (←/→) y espacio para reproducir. **Qué es «gas real» y qué es «detectado»** se elige encima del vídeo: la verdad del simulador, el umbral propio del detector, 1×/2×/3× NETD o un valor en mK; y la máscara del detector o la estimación con un umbral. El botón **Descargar fotograma (PNG)** guarda las vistas con las máscaras activas. |
| **📊 Resultados** | Ruido, umbral propio, retardo, falsa alarma, IoU/recall/precisión (para las máscaras elegidas y la ventana de tiempo que elijas), tabla a distintos umbrales, píxeles reales y detectados en el tiempo, IoU por fotograma, evidencia del detector (|z| máximo) y una galería de fotogramas con las máscaras. |
| **📦 Exportar** | Estilo de las imágenes, vistas y fotogramas a elegir, y qué incluir: informe HTML, GIF con las máscaras, todos los fotogramas en PNG, y los datos (`.npz`) de la detección y de la secuencia. Genera por separado `resumen.json`, `serie_temporal.csv`, `series.png`, `galeria.png` y un `paquete.zip` con todo. |
| **📈 Benchmark** | Resultados de `results/results.csv` por caudal y escena; botón para relanzar el barrido. |
| **⚖️ Comparar umbrales** | La figura de detector frente a verdad con el mismo umbral, para la escena y los caudales que elijas. |

## Capturas

![Vídeo con tres vistas](figures/app_video.png)
![Modo acierto/error](figures/app_video_error.png)
![Resultados](figures/app_resultados.png)
![Exportar](figures/app_exportar.png)

## Contenido del paquete exportado

```
resumen.json                 métricas, parámetros del detector y del simulador
serie_temporal.csv           por fotograma: píxeles detectados/reales, acierto, FP, FN, IoU, recall, precisión, |z| máx, alarma
informe.html                 informe autónomo con tablas e imágenes
figuras/series.png           píxeles, IoU y |z| en el tiempo
figuras/galeria.png          fotogramas elegidos con las máscaras
figuras/fotogramas/*.png     un PNG por vista y fotograma elegido
video/superposicion.gif      vídeo con las máscaras
fotogramas_png/*.png         (opcional) todos los fotogramas
datos/deteccion.npz          máscara detectada, máscara real y estimación en mK
datos/secuencia.npz          (opcional) la secuencia simulada completa
```

## Limitaciones conocidas

- Solo carga secuencias `.npz` de `ogi_sim.py`. Los vídeos reales (`.mp4`, cámara en directo) llegarán con el programa de captura (paso 6 de la hoja de ruta).
- La secuencia simulada vive en la memoria de la sesión del navegador: al recargar la página se pierde (exporta el `.npz` o el paquete si quieres conservarla).
- El reproductor carga todos los fotogramas en el navegador: con vídeos largos o a resolución completa usa «Fotogramas mostrados: 1 de cada N» y pocas vistas.
- «Superponer» las máscaras es una visualización: no se pueden dibujar ni editar máscaras a mano.

## Abrirla sin instalar nada

El repositorio es público y su rama por defecto es `claude/loving-allen-jv3c71`. Hay tres vías, de la más cómoda a la menos.

### 1. GitHub Codespaces (recomendada: la interfaz completa, desde el navegador)

Abre https://codespaces.new/eneko1982/Segmentador_gases?quickstart=1 o, en la página del repositorio, **Code → Codespaces → Create codespace**. Necesitas una cuenta de GitHub.

- Se crea una máquina en la nube con Python, instala las dependencias (`.devcontainer/devcontainer.json`) y arranca la interfaz sola. Tarda unos minutos la primera vez.
- Si no se abre la vista previa, ve a la pestaña **Ports**, busca el puerto 8501 y pulsa el icono del globo (*Open in Browser*).
- Las cuentas gratuitas tienen una cuota mensual de horas (consúltala en tu configuración de GitHub). Detén o borra el codespace cuando termines, para no gastarla.
- Es una máquina completa: se pueden relanzar el benchmark y los tests.

### 2. Google Colab (simular, detectar y ver las figuras; la interfaz es opcional)

Cuaderno: `notebooks/colab.ipynb`. Para abrirlo:

- Entra en https://colab.research.google.com → **Archivo → Abrir cuaderno → pestaña GitHub**, escribe `eneko1982/Segmentador_gases`, elige la rama `claude/loving-allen-jv3c71` y el cuaderno `notebooks/colab.ipynb`. (El enlace directo `colab.research.google.com/github/...` puede fallar con ramas que llevan `/` en el nombre; con una rama `main` sí funciona.)
- Ejecuta las celdas en orden. Las celdas 1 a 5 instalan el proyecto, simulan una fuga, muestran la secuencia, ejecutan el detector y comparan los umbrales; los parámetros se cambian editando un diccionario.
- La celda 6 es una **interfaz con deslizadores dentro del propio cuaderno** (preset, gas, caudal, viento, ráfagas, turbulencia, duración, NETD…). No necesita servidor web ni túnel. Es la versión reducida de la interfaz: simula y detecta; no incluye el reproductor con máscaras superpuestas ni la exportación: para eso usa el panel completo (`streamlit run app.py`, Codespaces o Streamlit Cloud).
- Se comprobó ejecutando el cuaderno completo en un núcleo de Jupyter local. En Colab mismo no se ha podido probar.

> **Redes corporativas.** Los servicios de túnel (`localtunnel`, `ngrok`…) suelen estar bloqueados por los cortafuegos de empresa: por eso el cuaderno ya no los usa. Si tu red también bloquea Codespaces o Streamlit Cloud, las opciones son pedir una revisión a Ciberseguridad (el propio aviso de bloqueo explica cómo) o ejecutar la interfaz en local (`streamlit run app.py`), que no sale a Internet.

### 3. Streamlit Community Cloud (una dirección web fija que compartes con otras personas)

1. Entra en https://share.streamlit.io con tu cuenta de GitHub.
2. **Create app → Deploy a public app from GitHub**.
3. Repositorio `eneko1982/Segmentador_gases`, rama `claude/loving-allen-jv3c71`, archivo principal `app.py`. Despliega.

Es gratuito, pero los recursos son limitados (poca memoria y CPU): usa la vista previa rápida y secuencias cortas, y no relances el benchmark. Cualquiera con el enlace podrá usar la aplicación; no guarda nada fuera de la sesión.

## Servidor propio

`streamlit run app.py --server.port 8501 --server.address 0.0.0.0`. Sin protección, cualquiera con acceso a la red podría abrirla: ponla detrás de una contraseña o una VPN.
