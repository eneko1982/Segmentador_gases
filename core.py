"""
core.py - Lógica del panel compartida por la interfaz web (app.py) y la del cuaderno (notebook_ui.py).

Sin dependencias de interfaz: recibe una secuencia y parámetros, devuelve datos.
"""
from __future__ import annotations

import numpy as np

import exports
import viewer
from analysis import PRED_KINDS, TRUTH_KINDS, Analysis
from gas_detect import detect, min_gas_free_s

DET_PARAM_DEFAULTS = dict(input="u8", gas_free_s=None, z_low=2.5, z_high=4.5, sigma_px=2.0, window_s=0.2, min_area=40)
VIEW_LABEL = exports.VIEW_NAMES


def run_detection(seq: dict, p: dict):
    """Ejecuta el detector. `p` = parámetros (ver DET_PARAM_DEFAULTS; gas_free_s=None -> 80 % del tiempo sin gas).

    Devuelve (analysis | None, mensaje | None, parámetros efectivos). Si hay `analysis`, el mensaje es solo un aviso;
    si no la hay, es el error."""
    import json
    meta = json.loads(str(seq["meta"]))
    fps = meta["fps"]
    p = {**DET_PARAM_DEFAULTS, **p}
    if p["gas_free_s"] is None:
        p["gas_free_s"] = round(0.8 * meta["leak_on_s"], 3)
    note = None
    if p["input"] == "temp" and "temp_ck" not in seq:
        p["input"], note = "u8", "Esta secuencia no incluye `temp_ck`: se usa el vídeo de 8 bit."
    frames = seq["u8"] if p["input"] == "u8" else seq["temp_ck"].astype(np.float32) / 100.0
    try:
        res = detect(frames, fps, gas_free_s=p["gas_free_s"], sigma_px=p["sigma_px"], window_s=p["window_s"],
                     z_low=p["z_low"], z_high=p["z_high"], min_area=int(p["min_area"]))
    except ValueError as err:
        need = min_gas_free_s(fps, p["window_s"])
        return None, (f"{err}. El detector necesita un tramo inicial sin gas de al menos {need:.2f} s con estos "
                      f"parámetros (ahora: {p['gas_free_s']:.2f} s). Haz que la fuga empiece más tarde o acorta el "
                      "promedio temporal."), p
    return Analysis(seq, res, p["input"], p["z_low"]), note, p


def mask_labels(an: Analysis, truth_mk: float = 35.0, pred_mk: float = 35.0):
    """Etiquetas legibles de cada tipo de máscara real y detectada."""
    return ({k: an.label({"kind": k, "mk": truth_mk}, "truth") for k in TRUTH_KINDS},
            {k: an.label({"kind": k, "mk": pred_mk}, "pred") for k in PRED_KINDS})


def view_arrays(seq: dict, an: Analysis | None, views, vmax: float) -> dict:
    """Imágenes de cada vista elegida ('cam', 'real', 'est') como arrays para el reproductor."""
    out = {}
    for v in views:
        if v == "cam":
            out[VIEW_LABEL[v]] = seq["u8"]
        elif v == "real":
            out[VIEW_LABEL[v]] = np.stack([viewer.colorize(c.astype(np.float32) * 1e3, vmax) for c in seq["contrast"]])
        elif an is not None:
            out[VIEW_LABEL[v]] = np.stack([viewer.colorize(e * an.k * 1e3, vmax) for e in an.res["estimate"]])
    return out


def build_player_payload(seq: dict, an: Analysis | None, truth, pred, views, vmax: float, stride: int) -> dict:
    import json
    meta = json.loads(str(seq["meta"]))
    learn = an.res["n_free"] / meta["fps"] if an is not None else 0.0
    return viewer.build_payload(view_arrays(seq, an, views, vmax), truth, pred, meta["fps"], stride,
                                meta["leak_on_s"], learn)
