# Robustez del detector ante el mundo real

Qué pasa cuando el vídeo no es «de laboratorio». Generado con `python benchmark_robust.py` (datos en `results/robustness.csv`).
Cada celda es la media de **2 semillas** y 2 caudal(es) por escenario, así que son
**indicios, no estadística**: con tan pocas repeticiones, diferencias de ±0,05 no significan nada.

Son vídeos **simulados** con las perturbaciones del simulador (`docs/parametros.md`, grupo «Perturbaciones del mundo real»). Que
funcione aquí no demuestra que funcione con la Hikmicro: es la lista de problemas que el ensayo real tiene que confirmar o
desmentir.

### IoU medio con fuga (1 = perfecto)

| Perturbación | Estable | Sol/nubes | Vibración | Comprimido | Robusto | Robusto + origen |
|---|---|---|---|---|---|---|
| compresión | 0.55 | 0.49 | 0.50 | 0.79 | 0.54 | 0.00 |
| ninguna | 0.87 | 0.86 | 0.86 | 0.88 | 0.86 | 0.86 |
| nube | 0.55 | 0.54 | 0.54 | 0.56 | 0.54 | 0.54 |
| obturador | 0.81 | 0.82 | 0.81 | 0.85 | 0.82 | 0.83 |
| personas | 0.82 | 0.82 | 0.64 | 0.83 | 0.82 | 0.82 |
| sol lento | 0.60 | 0.86 | 0.86 | 0.61 | 0.86 | 0.86 |
| sol rápido | 0.55 | 0.87 | 0.87 | 0.55 | 0.87 | 0.87 |
| todo (duro) | 0.49 | 0.54 | 0.66 | 0.14 | 0.66 | 0.82 |
| vibración | 0.65 | 0.66 | 0.87 | 0.03 | 0.87 | 0.87 |

### Fracción de vídeos con falsa alarma antes de abrir la fuga

| Perturbación | Estable | Sol/nubes | Vibración | Comprimido | Robusto | Robusto + origen |
|---|---|---|---|---|---|---|
| compresión | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | 0.00 |
| ninguna | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| nube | 0.50 | 0.50 | 0.50 | 0.00 | 0.50 | 0.00 |
| obturador | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| personas | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| sol lento | 0.50 | 0.00 | 0.00 | 0.50 | 0.00 | 0.00 |
| sol rápido | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| todo (duro) | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | 0.00 |
| vibración | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 |

## Qué se aprende

- **Sol lento y deriva del fondo**: el detector estable falla (IoU 0,55–0,60). Restar una superficie suave del fondo
  (`drift_order=1`) lo arregla (0,86–0,87) sin coste visible.
- **Vibración de la cámara**: el estable genera falsa alarma en todos los vídeos (IoU 0,65). Estabilizar la imagen lo arregla
  (0,87, sin falsas alarmas). Coste: pierde una franja de borde, y **sin vibración real empeora** con personas en escena
  (0,82 → 0,64), por eso no se activa por defecto.
- **Compresión**: sube el ruido no gaussiano; falsa alarma siempre con el estable. El umbral automático la quita (IoU 0,55 →
  0,79) pero **con vibración fuerte deja ciego al detector** (0,03).
- **Obturador (NUC)**: el estable ya lo aguanta bien en este simulador (0,81). El fondo adaptativo no mejoró y las ideas más
  elaboradas (reaprender solo altas frecuencias, proteger píxeles) fueron peores y se descartaron.
- **Nube pequeña y objetos calientes**: ninguna opción de imagen los distingue de gas por sí sola. «Exigir el origen en el
  tubo» elimina las falsas alarmas (nube: 0,50 → 0,00), pero **necesita saber dónde está el tubo** y, en compresión, el IoU cae a
  0,00: la condición de origen no se cumple con ese ruido. Para el caso duro conjunto es lo único que baja la falsa alarma.
- **Ninguna combinación gana en todo.** Por eso hay perfiles con su coste explicado, y el panel sugiere uno por escenario.

## Limitaciones

- Perturbaciones simuladas con modelos sencillos (seno/rampa para el sol, disco gaussiano para la nube, JPEG-like para la
  compresión). La cámara real tendrá otras.
- Una sola geometría y un solo gas; pocas semillas.
- El perfil «Robusto + origen» usa la posición real del tubo si el vídeo es simulado; con un vídeo real hay que dibujar la
  zona (botón «Dibujar zona» del reproductor).
