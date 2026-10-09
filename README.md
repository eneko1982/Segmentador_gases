# Segmentador de gases (OGI sintético)

Simulador de secuencias LWIR de fugas de gas (cámara fija, tubo de venteo) y detector clásico, sin entrenamiento, que segmenta la pluma. Sirve para medir el detector contra una verdad de terreno exacta.

| Archivo | Función |
|---|---|
| `ogi_sim.py` | Simulador: pluma de puffs gaussianos, radiometría 8–14 µm, cámara (NETD, FPN, deriva, cuantización 8 bit). Salidas: `u8`, `temp_ck`, `cl`, `contrast`, `mask`, `meta`. |
| `gas_detect.py` | Detector: fondo → diferencia → suavizado espacial → deriva (moda) → media temporal → z-score → histéresis → limpieza. |
| `benchmark.py` | Barrido de caudal (0,001–0,3 g/s) en 3 escenas, evaluando vídeo 8 bit y matriz radiométrica. |
| `results/results.csv` | Resultados del benchmark (2 semillas). |
| `docs/figures/` | Figuras de escenas y del detector. |

## Uso

```bash
pip install -r requirements.txt
python ogi_sim.py --out demo --scene hot_plate --flow 0.1 --preview
python gas_detect.py demo/seq_0000_hot_plate.npz --preview
python benchmark.py --out bench
```

## Notas

- `band_fraction` y `k_abs` son valores de orden de magnitud: hay que calibrarlos con capturas reales antes de cualquier uso cuantitativo.
- Resultado clave del benchmark: con ΔT = 5 K, el detector falla por debajo de ~0,01 g/s (cambio máximo por píxel < 1× NETD); con ΔT = 48 K o cielo frío detecta desde 0,001 g/s.
