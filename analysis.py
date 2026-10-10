"""
analysis.py - Máscaras, métricas y series temporales del panel (sin interfaz).

Dada una secuencia simulada y el resultado de `gas_detect.detect`, construye:
  - la máscara REAL (verdad del simulador) con el umbral que se elija,
  - la máscara DETECTADA (histéresis del detector, o la estimación con un umbral),
  - métricas por fotograma y por ventana, y un resumen.

Una "especificación" de máscara es un diccionario {"kind": ..., "mk": ...}:
    real      "sim"  |contraste| > mask_thresh_k del simulador
              "det"  umbral propio del detector (z_low · σ del ruido filtrado)
              "1x" "2x" "3x"  múltiplos del NETD de un píxel
              "custom"  `mk` milikelvin
    detectada "mask" la máscara del detector (histéresis)
              y los mismos "det", "1x", "2x", "3x", "custom" aplicados a la estimación
"""
from __future__ import annotations

import json

import numpy as np

from gas_detect import alarm, scores

TRUTH_KINDS = ["sim", "det", "1x", "2x", "3x", "custom"]
PRED_KINDS = ["mask", "det", "1x", "2x", "3x", "custom"]
WINDOWS = {"last": "Último segundo", "from_on": "Desde que se abre la fuga", "all": "Todo el vídeo"}


class Analysis:
    """Valores derivados de (secuencia, detección) que se reutilizan en todo el panel."""

    def __init__(self, seq: dict, res: dict, kind: str, z_low: float):
        self.meta = json.loads(str(seq["meta"]))
        self.fps = self.meta["fps"]
        self.netd = self.meta["netd_k"]
        self.leak_on_s = self.meta["leak_on_s"]
        self.kind = kind
        self.k = self.meta["k_per_dn"] if kind == "u8" else 1.0          # K por unidad de la entrada
        self.res = res
        self.z_low = z_low
        self.valid = res["valid"]
        self.n = len(res["mask"])
        self.on = int(round(self.leak_on_s * self.fps))
        self.has_truth = "contrast" in seq
        self.contrast = np.abs(seq["contrast"].astype(np.float32)) if self.has_truth else None
        self.sim_mask = seq["mask"] if "mask" in seq else None
        self.est = np.abs(res["estimate"]) * self.k                       # kelvin, (T, H, W)
        self.det_thr_k = z_low * res["sigma"] * self.k
        self.mask_thr_k = self.meta.get("mask_thresh_k", 0.035)
        self.alarm = alarm(res["mask"])

    # ------------------------------------------------------------------ umbrales
    def thr_k(self, spec: dict) -> float:
        kind = spec["kind"]
        if kind == "sim":
            return self.mask_thr_k
        if kind == "det":
            return self.det_thr_k
        if kind in ("1x", "2x", "3x"):
            return int(kind[0]) * self.netd
        return float(spec.get("mk", 35.0)) / 1e3

    def label(self, spec: dict, role: str) -> str:
        kind = spec["kind"]
        if role == "pred" and kind == "mask":
            return f"Máscara del detector (histéresis {self.z_low:g}σ)"
        mk = 1e3 * self.thr_k(spec)
        names = {"sim": "del simulador", "det": "umbral propio del detector", "1x": "1× NETD", "2x": "2× NETD",
                 "3x": "3× NETD", "custom": "personalizado"}
        prefix = "Verdad " if role == "truth" else "Estimación > "
        return f"{prefix}{names[kind]} ({mk:.0f} mK)"

    # ------------------------------------------------------------------ máscaras
    def truth(self, spec: dict):
        if not self.has_truth:
            return None
        if spec["kind"] == "sim" and self.sim_mask is not None:
            return self.sim_mask & self.valid
        return (self.contrast > self.thr_k(spec)) & self.valid

    def pred(self, spec: dict):
        if spec["kind"] == "mask":
            return self.res["mask"]
        return (self.est > self.thr_k(spec)) & self.valid

    # ------------------------------------------------------------------ métricas
    def window(self, name: str) -> slice:
        if name == "from_on":
            return slice(self.on, self.n)
        if name == "all":
            return slice(0, self.n)
        return slice(max(self.n - int(self.fps), 0), self.n)

    def series(self, truth, pred) -> dict:
        """Cantidades por fotograma (arrays de longitud T)."""
        n = self.n
        zmax = self.res["zmax"] if self.res.get("zmax") is not None else np.abs(self.res["z"])[:, self.valid].max(1)
        out = dict(t_s=np.arange(n) / self.fps, detected_px=pred.reshape(n, -1).sum(1), zmax=zmax, alarm=self.alarm)
        if truth is not None:
            tp = (pred & truth).reshape(n, -1).sum(1)
            fp, fn = out["detected_px"] - tp, truth.reshape(n, -1).sum(1) - tp
            with np.errstate(divide="ignore", invalid="ignore"):
                out.update(true_px=tp + fn, tp_px=tp, fp_px=fp, fn_px=fn,
                           iou=np.where(tp + fp + fn > 0, tp / (tp + fp + fn), np.nan),
                           recall=np.where(tp + fn > 0, tp / (tp + fn), np.nan),
                           precision=np.where(tp + fp > 0, tp / (tp + fp), np.nan))
        return out

    def window_scores(self, truth, pred, window: str) -> dict:
        s = self.window(window)
        return scores(pred[s], truth[s])

    def summary(self, truth_spec: dict, pred_spec: dict, window: str = "last") -> dict:
        """Resumen de resultados (todo con tipos de Python, listo para JSON)."""
        res, last = self.res, self.window("last")
        first = np.flatnonzero(self.alarm[self.on:])
        out = dict(
            noise_mk=1e3 * res["sigma"] * self.k, pixel_netd_mk=1e3 * self.netd,
            detector_threshold_mk=1e3 * self.det_thr_k,
            detection_delay_s=float(first[0] / self.fps) if len(first) else None,
            false_alarm_before_leak=bool(self.alarm[res["n_free"]:self.on].any()),
            gas_free_period_s=res["n_free"] / self.fps, input=self.kind,
            emitted_g=self.meta.get("emitted_g"))
        if self.has_truth:
            free = (self.contrast[last] < 0.002) & self.valid
            out["gas_free_pixels_flagged_pct"] = float(100 * (res["mask"][last] & free).sum() / max(free.sum(), 1))
            out["max_pixel_change_mk"] = 1e3 * float(self.contrast.max())
            t, p = self.truth(truth_spec), self.pred(pred_spec)
            out["selected_masks"] = dict(truth=self.label(truth_spec, "truth"), detected=self.label(pred_spec, "pred"),
                                         window=WINDOWS[window], **self.window_scores(t, p, window))
            rows = [("Máscara del detector frente a la verdad a su umbral", {"kind": "mask"}, {"kind": "det"})]
            rows += [(f"Estimación frente a la verdad a {m}× NETD", {"kind": f"{m}x"}, {"kind": f"{m}x"})
                     for m in (1, 2, 3)]
            out["table"] = [dict(comparison=name, threshold_mk=1e3 * self.thr_k(ts), **self.window_scores(
                self.truth(ts), self.pred(ps), window)) for name, ps, ts in rows]
        return out


def clean(obj):
    """Convierte NaN/numpy a tipos que JSON admite (NaN -> None)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj
