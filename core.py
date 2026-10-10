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

DET_PARAM_DEFAULTS = dict(input="u8", gas_free_s=None, z_low=2.5, z_high=4.5, sigma_px=2.0, window_s=0.2, min_area=40,
                          background="static", bg_tau_s=30.0, bg_margin_px=6, bg_hold_on_alarm=True, drift_order=0,
                          stabilize=False, max_shift_px=6.0, polarity="both", auto_threshold=False,
                          roi_text="", source_text="", require_source=False)

# Perfiles del detector: qué opciones activar según el problema. polarity="auto" = oscuro en placa caliente, claro en cielo frío.
DETECTOR_PROFILES = {
    "Estable (por defecto)": dict(
        desc="Cámara fija, escena estable, sin perturbaciones. Es el detector original.",
        cost="Se confunde con cambios lentos del fondo, vibración, obturador o compresión.", params={}),
    "Exterior: sol y nubes lentas": dict(
        desc="El fondo cambia despacio por el sol o por equipos que se calientan. Resta del fondo una superficie suave y "
             "solo acepta el signo esperado (placa caliente: más oscuro).",
        cost="No distingue una nube pequeña de una fuga (para eso, exige el origen en el tubo).",
        params=dict(drift_order=1, polarity="auto")),
    "Cámara con vibración": dict(
        desc="El soporte vibra o se mueve un poco. Alinea cada fotograma con el fondo y descarta los bordes nítidos.",
        cost="Pierde una franja de los bordes de la imagen y los píxeles de bordes muy marcados.",
        params=dict(stabilize=True, drift_order=1)),
    "Vídeo comprimido": dict(
        desc="Vídeo con compresión (RTSP/MP4): el ruido en bloques no es gaussiano. Sube los umbrales hasta superar lo que "
             "vio sin gas.",
        cost="Pierde sensibilidad; con vibración fuerte puede dejar de ver el gas.", params=dict(auto_threshold=True)),
    "Robusto (varias perturbaciones)": dict(
        desc="Fondo adaptativo + superficie suave + estabilización + signo esperado. Para exterior con sol, vibración y "
             "obturador a la vez.",
        cost="Algo menos preciso que el estable con obturador; la compresión fuerte sigue siendo un problema.",
        params=dict(background="adaptive", bg_tau_s=10.0, drift_order=1, stabilize=True, polarity="auto")),
    "Robusto + exigir el origen en el tubo": dict(
        desc="Lo anterior, y además una detección solo cuenta si nace en la zona del tubo y crece desde allí: rechaza "
             "personas, pájaros o nubes que entran por un borde.",
        cost="Necesita saber dónde está el tubo (secuencia simulada o zona dibujada); una nube que llegue al tubo cuenta.",
        params=dict(background="adaptive", bg_tau_s=10.0, drift_order=1, stabilize=True, polarity="auto",
                    require_source=True)),
}
VIEW_LABEL = exports.VIEW_NAMES


def run_detection(seq: dict, p: dict):
    """Ejecuta el detector. `p` = parámetros (ver DET_PARAM_DEFAULTS; gas_free_s=None -> 80 % del tiempo sin gas).

    Devuelve (analysis | None, mensaje | None, parámetros efectivos). Si hay `analysis`, el mensaje es solo un aviso;
    si no la hay, es el error."""
    import json

    import regions
    meta = json.loads(str(seq["meta"]))
    fps = meta["fps"]
    p = {**DET_PARAM_DEFAULTS, **p}
    if p["gas_free_s"] is None:
        p["gas_free_s"] = round(0.8 * meta["leak_on_s"], 3)
    note = None
    if p["input"] == "temp" and "temp_ck" not in seq:
        p["input"], note = "u8", "Esta secuencia no incluye `temp_ck`: se usa el vídeo de 8 bit."
    frames = seq["u8"] if p["input"] == "u8" else seq["temp_ck"].astype(np.float32) / 100.0
    shape = tuple(frames.shape[1:3])
    polarity = p["polarity"]
    if polarity == "auto":
        polarity = {"hot_plate": "dark", "cold_sky": "bright"}.get(meta.get("scene"), "both")
    try:
        roi = regions.polygon_mask(p["roi_text"], shape) if str(p["roi_text"]).strip() else None
        source = None
        if p["require_source"]:
            source = regions.polygon_mask(p["source_text"], shape) if str(p["source_text"]).strip() \
                else regions.source_from_meta(meta, shape)
            if source is None:
                return None, ("«Exigir el origen» necesita una zona de origen: dibújala en el reproductor y pega sus "
                              "coordenadas, o usa una secuencia simulada (el tubo se conoce)."), p
        res = detect(frames, fps, gas_free_s=p["gas_free_s"], sigma_px=p["sigma_px"], window_s=p["window_s"],
                     z_low=p["z_low"], z_high=p["z_high"], min_area=int(p["min_area"]), background=p["background"],
                     bg_tau_s=p["bg_tau_s"], bg_margin_px=int(p["bg_margin_px"]), bg_hold_on_alarm=p["bg_hold_on_alarm"],
                     drift_order=int(p["drift_order"]), stabilize=p["stabilize"], max_shift_px=p["max_shift_px"],
                     polarity=polarity, auto_threshold=p["auto_threshold"], roi=roi, source_roi=source,
                     require_source=p["require_source"])
    except ValueError as err:
        need = min_gas_free_s(fps, p["window_s"])
        if "gas-free period too short" in str(err):
            return None, (f"{err}. El detector necesita un tramo inicial sin gas de al menos {need:.2f} s con estos "
                          f"parámetros (ahora: {p['gas_free_s']:.2f} s). Haz que la fuga empiece más tarde o acorta el "
                          "promedio temporal."), p
        return None, str(err), p
    p["polarity_used"] = polarity
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
