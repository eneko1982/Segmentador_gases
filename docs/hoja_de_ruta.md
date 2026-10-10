# Hoja de ruta

Estado: ✅ hecho · 🔶 en curso · ⬜ pendiente. Se actualiza en cada avance (ver `registro.md`).

## Base

| # | Paso | Estado | Dónde |
|---|---|---|---|
| 1 | Definir la idea: detectar y delimitar una pluma de gas en imagen térmica con cámara fija, validada con la Hikmicro | ✅ | — |
| 2 | Caso de partida: antorcha fría (tubo que suelta gas sin llama) | ✅ | — |
| 3 | Simulador con verdad de terreno | ✅ | `ogi_sim.py` |
| 4 | Detector clásico, sin IA | ✅ | `gas_detect.py` |
| 5 | Primera medición sobre vídeos simulados | ✅ | `benchmark.py`, `results/` |
| 5b | Comparar detector y verdad con el mismo umbral | ✅ | `make_figure.py`, `iou_det` |
| 5c | **Simulador totalmente parametrizable** (80 parámetros, presets, JSON, `--set`, validación, tests) | ✅ | `ogi_sim.py`, `configs/`, `docs/parametros.md` |

## Antes de tener la cámara (se puede hacer ya)

| # | Paso | Estado |
|---|---|---|
| A1 | Perturbaciones de mundo real en el simulador: deriva del fondo, vibración, saltos del obturador, objetos que se mueven | ⬜ |
| A2 | Detector más robusto: referencia que se actualiza sola, zona de vigilancia | ⬜ |
| A3 | Vídeos largos: escritura por trozos a disco | ⬜ |
| A4 | Programa de captura probado con una cámara simulada | ⬜ |
| A5 | Protocolo escrito del ensayo en banco | ⬜ |
| A6 | Sensibilidad a `band_fraction` y `k_abs` | ⬜ |
| A7 | Panel de control (Streamlit): ajustes de vídeo, simulación y detector; reproductor con máscaras superpuestas; resultados y exportación (`app.py`, `docs/interfaz.md`) | ✅ |

## Validación con la cámara real

| # | Paso | Estado |
|---|---|---|
| 6 | Programa de captura con la Hikmicro (vídeo y matriz de temperaturas) | ⬜ |
| 7 | Conocer la cámara: ruido real, deriva y saltos del obturador; ajustar el simulador | ⬜ |
| 8 | Ensayo en banco: spray de refrigerante frente a placa templada, tiempos conocidos y pesada del bote | ⬜ |
| 9 | Decisión: ¿el detector ve la fuga real? | ⬜ |
| 10 | Ensayo en exterior: sol, nubes, viento; lista de falsas alarmas | ⬜ |

## Hacerlo robusto y aplicación

| # | Paso | Estado |
|---|---|---|
| 11 | Endurecer el detector con lo aprendido | ⬜ |
| 12 | Decisión: ¿red neuronal? Solo si el clásico no resuelve casos reales | ⬜ |
| 13 | Cuantificar (opcional): de "hay gas" a gramos por hora | ⬜ |
| 14 | Requisitos de la aplicación: usuario, cámara, directo o grabaciones, qué entrega | ⬜ |
| 15 | Diseño de la aplicación: captura, detección, alarma y registro, pantalla, instalación | ⬜ |
