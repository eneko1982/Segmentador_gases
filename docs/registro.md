# Registro de cambios y decisiones

Entradas de la más reciente a la más antigua.

## Abrir el proyecto sin instalar nada

- `.devcontainer/devcontainer.json`: GitHub Codespaces instala las dependencias y arranca la interfaz.
- `notebooks/colab.ipynb`: cuaderno de Colab con instalación, simulación, detector y comparación de umbrales (las celdas 1 a 5 se comprobaron en local; la celda 6, interfaz con túnel, no se pudo probar en Colab y puede fallar).
- `ogi_sim.half_resolution` sustituye al código equivalente que había en `app.py`.
- `make_figure.py` ya no fija el backend de matplotlib al importarse (impedía mostrar figuras en un cuaderno).
- No probado: Codespaces y Colab desde fuera de este entorno; las instrucciones siguen la configuración estándar de ambos.

## Interfaz (paso A7)

- `app.py` (Streamlit) con cuatro pestañas: Simulador, Detector, Benchmark y Comparar umbrales. Guía y capturas en [`interfaz.md`](interfaz.md).
- Vista previa rápida por defecto (½ resolución, mismo campo de visión) y resolución completa opcional.
- Solo carga secuencias `.npz` simuladas (decisión del usuario); los vídeos reales se añadirán con el programa de captura.
- Refactor para reutilizar el código: `gas_detect.preview_frames` (los contornos, ahora separados de la escritura del GIF) y `make_figure.build_figure`.
- Tests de humo en `tests/test_app.py` (AppTest, sin navegador). Se comprobó además en un navegador real con las cuatro pestañas.
- Error de la primera versión corregido: el deslizador «La fuga empieza en» cambiaba de rango al mover la duración y Streamlit lo reiniciaba a 0. Ahora los rangos de los deslizadores son fijos y la validación avisa si la fuga empieza después del final.
- `requirements.txt` incluía solo numpy, scipy y pillow; faltaban matplotlib (ya usado por `make_figure.py`), pandas y streamlit.
- Pendiente de decidir: dónde se publicará (por ahora, local; pasos de publicación en `interfaz.md`).

## Simulador totalmente parametrizable

**Qué cambió**
- Todas las constantes numéricas del simulador están en `Config` (80 parámetros, en 6 grupos) con unidad, rango y descripción. Referencia completa en [`parametros.md`](parametros.md), generada con `python ogi_sim.py --params-md docs/parametros.md`.
- Nuevos parámetros: geometría y temperaturas de la escena, tablas de gas con sustitución de masa molar y densidad, componente vertical del viento, tiempos de correlación de ráfagas y turbulencia, número y longitudes de onda de los modos de turbulencia, resolución de la pluma (`puffs_per_s`, `substep_s`), banda espectral, márgenes de la ventana de 8 bit, deriva (`drift_period_s`) y salidas opcionales (`store_cl`, `store_temp`).
- Línea de comandos: `--preset`, `--config`, `--set clave=valor`, `--dump-config`, `--list-params`, `--params-md`. Orden de precedencia: valores por defecto < preset < `--config` < `--scene/--flow/--wind/--duration/--seed` < `--set`.
- Cada secuencia guardada lleva un `.json` al lado con todos los parámetros usados y la semilla (también va dentro del `.npz`).
- Validación de todos los parámetros (rangos, coherencia entre ellos) con mensajes claros.
- Presets en `configs/`: las tres escenas del benchmark, un vídeo largo y turbulento (`long_turbulent`) y una fuga intermitente (`intermittent_leak`). `benchmark.py` lee ahora sus escenas de esos ficheros.
- `random_config` usa una tabla `RANDOM_RANGES` visible en el código.
- Tests en `tests/` (`python -m pytest tests`).

**Garantía de compatibilidad**: con los valores por defecto, los vídeos son idénticos bit a bit a los del simulador anterior (comparado con `u8`, `temp_ck`, `cl`, `contrast` y `mask`, en las dos escenas, con semillas distintas y fuga que se cierra) y `random_config` genera las mismas configuraciones. El benchmark reproduce los mismos números.

**Sigue fijo en el código** (listado también en `parametros.md`): forma de la falta de uniformidad suave de la escena, percentiles de la ventana automática, rangos de frecuencia y fase de los modos de turbulencia, rejilla de la tabla de Planck y el modelo de ráfagas (solo horizontal).

**Por qué**: es la base de la interfaz (los deslizadores controlarán estos mismos parámetros), de generar conjuntos de datos con variaciones controladas y de ajustar el simulador a la cámara real (paso 7).

## Detector y verdad con el mismo umbral

- El detector trabaja en unidades de ruido (2,5σ ≈ 6 mK tras el promediado), y la verdad del simulador se definía a 35 mK. Eso explicaba que el contorno naranja sobresaliera del azul.
- Se añadió la comparación a umbral propio del detector (`iou_det`) y `make_figure.py`.
- Resultado: con ΔT = 5 K el detector funciona hasta 0,003 g/s (IoU medio 0,77) y falla a 0,001 g/s (0,24).

## Primera versión

- Simulador, detector clásico, benchmark y resultados (36 vídeos × 2 entradas).
