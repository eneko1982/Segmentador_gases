# Segmentador de gases (OGI sintético)

Simulador de secuencias LWIR de fugas de gas (cámara fija, tubo de venteo) y detector clásico, sin entrenamiento, que segmenta la pluma. Sirve para medir el detector contra una verdad de terreno exacta.

| Archivo | Función |
|---|---|
| `ogi_sim.py` | Simulador totalmente parametrizable: pluma de puffs gaussianos, radiometría 8–14 µm, cámara (NETD, FPN, deriva, cuantización 8 bit). Salidas: `u8`, `temp_ck`, `cl`, `contrast`, `mask`, `meta`. |
| `gas_detect.py` | Detector: fondo → diferencia → suavizado espacial → deriva (moda) → media temporal → z-score → histéresis → limpieza. |
| `make_figure.py` | Figura detector vs verdad con umbral coherente (`docs/figures/detector_same_threshold.png`). |
| `benchmark.py` | Barrido de caudal (0,001–0,3 g/s) en 3 escenas, evaluando vídeo 8 bit y matriz radiométrica. |
| `results/results.csv` | Resultados del benchmark (2 semillas). |
| `configs/` | Presets en JSON (escenas del benchmark, vídeo largo turbulento, fuga intermitente). |
| `docs/` | `parametros.md` (referencia de parámetros), `hoja_de_ruta.md` (estado de los pasos), `registro.md` (cambios y decisiones) y `figures/`. |
| `tests/` | Tests del simulador. |
| `docs/figures/` | Figuras de escenas y del detector. |

## Uso

```bash
pip install -r requirements.txt
python ogi_sim.py --preset hot_plate_dT5 --flow 0.03 --out demo --preview
python gas_detect.py demo/seq_0000_hot_plate.npz --preview
python benchmark.py --out bench
python -m pytest tests
```

## Parametrizar el simulador

Todo lo ajustable está en `Config` y documentado en [`docs/parametros.md`](docs/parametros.md).

```bash
python ogi_sim.py --list-params                                   # todos los parámetros
python ogi_sim.py --preset long_turbulent --out largo             # preset de configs/
python ogi_sim.py --config mi_prueba.json --set turb_rms=0.3 --set wind_ms=-0.5
python ogi_sim.py --preset cold_sky --dump-config                 # ver la configuración final
python ogi_sim.py --out dataset --n 50 --seed 1 --set duration_s=6   # conjunto aleatorio
```

Precedencia: valores por defecto < `--preset` < `--config` < `--scene/--flow/--wind/--duration/--seed` < `--set`. Cada secuencia se guarda con un `.json` que recoge todos los parámetros y la semilla.

## Notas

- `band_fraction` y `k_abs` son valores de orden de magnitud: hay que calibrarlos con capturas reales antes de cualquier uso cuantitativo.
- **Umbrales**: el detector trabaja en unidades de ruido (2,5σ ≈ 6 mK tras el promediado), no en 35 mK. Por eso se mide de dos formas con el mismo umbral en detector y verdad: a 1×/2×/3× NETD (`iou_1x`…) y al umbral propio del detector (`iou_det`).
- **Resultado clave** (IoU medio `iou_det`, vídeo 8 bit): con ΔT = 5 K el detector funciona hasta 0,003 g/s (0,77) y falla a 0,001 g/s (0,24); con ΔT = 48 K y con cielo frío llega a 0,001 g/s (0,88–0,90). A 1× NETD (35 mK) en ΔT = 5 K no hay gas medible por debajo de 0,01 g/s, pero el promediado espacial y temporal lo recupera a niveles de ~6 mK.
