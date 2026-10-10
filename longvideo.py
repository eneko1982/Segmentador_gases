"""
longvideo.py - Vídeos largos: el simulador escribe a disco fotograma a fotograma y el detector también trabaja
fotograma a fotograma, así que la memoria no depende de la duración (solo el disco).

    run = longvideo.run_long(cfg, "salida/largo", det_params)
    run.series          métricas por fotograma (DataFrame)
    run.summary         resumen (retraso, falsa alarma, IoU por ventana…)
    run.payload()       datos del reproductor, con los fotogramas decimados
    run.export(dir)     CSV, JSON y figura de series

Limitaciones: no se guardan z ni la estimación (ocuparían tanto como el vídeo); la verdad es la máscara del simulador
(«sim») o un umbral de contraste, y la predicción es la máscara del detector.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

import core
import ogi_sim as sim
import viewer
from analysis import clean
from gas_detect import alarm as frame_alarm
from gas_detect import detect

BYTES_PER_PX_FRAME = 10          # u8 + temp_ck (u16) + cl (f32) + contrast (f32 de media) + máscara: orden de magnitud
CHUNK = 200


def disk_estimate(cfg: sim.Config) -> dict:
    """Tamaño aproximado en disco y espacio libre, para avisar antes de empezar."""
    n = int(round(cfg.duration_s * cfg.fps))
    need = n * cfg.width * cfg.height * BYTES_PER_PX_FRAME
    return dict(n_frames=n, need_gb=need / 1e9, free_gb=shutil.disk_usage(Path(".").resolve()).free / 1e9)


class LongRun:
    def __init__(self, path: Path, seq: dict, res: dict, mask: np.ndarray, det_p: dict):
        self.path, self.seq, self.res, self.mask, self.det_p = Path(path), seq, res, mask, det_p
        self.meta = json.loads(str(seq["meta"]))
        self.fps = self.meta["fps"]
        self.n = len(mask)
        self.on = int(round(self.meta["leak_on_s"] * self.fps))
        self.alarm = frame_alarm(mask)
        self.series = self._series()
        self.summary = self._summary()

    # ------------------------------------------------------------------ métricas por fotograma
    def truth_chunk(self, sl: slice, kind: str = "sim") -> np.ndarray:
        if kind == "sim" and "mask" in self.seq:
            return np.asarray(self.seq["mask"][sl])
        thr = {"1x": 1, "2x": 2, "3x": 3}.get(kind)
        k = thr * self.meta["netd_k"] if thr else self.meta.get("mask_thresh_k", 0.035)
        return np.abs(np.asarray(self.seq["contrast"][sl], np.float32)) > k

    def _series(self) -> pd.DataFrame:
        n = self.n
        rows = {k: np.zeros(n) for k in ("px_detectados", "px_reales", "px_acierto")}
        for a in range(0, n, CHUNK):
            sl = slice(a, min(a + CHUNK, n))
            pred = np.asarray(self.mask[sl])
            rows["px_detectados"][sl] = pred.reshape(len(pred), -1).sum(1)
            if "mask" in self.seq or "contrast" in self.seq:
                tr = self.truth_chunk(sl)
                rows["px_reales"][sl] = tr.reshape(len(tr), -1).sum(1)
                rows["px_acierto"][sl] = (pred & tr).reshape(len(tr), -1).sum(1)
        df = pd.DataFrame(dict(frame=np.arange(n) + 1, t_s=np.arange(n) / self.fps, **rows,
                               z_max=np.asarray(self.res["zmax"]), alarma=self.alarm))
        tp, det, tru = df.px_acierto, df.px_detectados, df.px_reales
        with np.errstate(divide="ignore", invalid="ignore"):
            df["iou"] = np.where(det + tru - tp > 0, tp / (det + tru - tp), np.nan)
        return df

    def window(self, name: str) -> slice:
        return {"all": slice(0, self.n), "from_on": slice(self.on, self.n)}.get(name, slice(max(self.n - int(self.fps), 0), self.n))

    def window_scores(self, name: str) -> dict:
        d = self.series.iloc[self.window(name)]
        tp, det, tru = d.px_acierto.sum(), d.px_detectados.sum(), d.px_reales.sum()
        fp, fn = det - tp, tru - tp
        return dict(iou=tp / (tp + fp + fn) if tp + fp + fn else float("nan"),
                    recall=tp / tru if tru else float("nan"), precision=tp / det if det else float("nan"))

    def _summary(self) -> dict:
        first = np.flatnonzero(self.alarm[self.on:])
        out = dict(duration_s=self.n / self.fps, frames=self.n, noise_mk=1e3 * self.res["sigma"] * self.meta["k_per_dn"],
                   detection_delay_s=float(first[0] / self.fps) if len(first) else None,
                   false_alarm_before_leak=bool(self.alarm[self.res["n_free"]:self.on].any()),
                   alarm_fraction_after_leak=float(self.alarm[self.on:].mean()) if self.n > self.on else None,
                   emitted_g=self.meta.get("emitted_g"))
        if "mask" in self.seq:
            out["scores"] = {w: self.window_scores(w) for w in ("last", "from_on", "all")}
        return clean(out)

    # ------------------------------------------------------------------ reproductor e imágenes
    def indices(self, max_frames: int = 150) -> list[int]:
        step = max(1, int(np.ceil(self.n / max_frames)))
        return list(range(0, self.n, step))

    def payload(self, max_frames: int = 150) -> dict:
        """Reproductor con la cámara, la verdad y la detección en un subconjunto repartido de fotogramas."""
        idx = self.indices(max_frames)
        step = idx[1] - idx[0] if len(idx) > 1 else 1
        cam = np.stack([np.asarray(self.seq["u8"][i]) for i in idx])
        truth = np.stack([np.asarray(self.seq["mask"][i]) for i in idx]) if "mask" in self.seq else None
        pred = np.stack([np.asarray(self.mask[i]) for i in idx])
        p = viewer.build_payload({core.VIEW_LABEL["cam"]: cam}, truth, pred, self.fps / step, 1, self.meta["leak_on_s"],
                                 self.res["n_free"] / self.fps)
        p["note"] = f"1 de cada {step} fotogramas ({len(idx)} de {self.n})"
        return p

    def export(self, out_dir) -> list[Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        files = []
        (out / "serie_temporal.csv").write_bytes(self.series.to_csv(index=False, float_format="%.6g").encode())
        (out / "resumen.json").write_text(json.dumps(dict(summary=self.summary, detector_parameters=clean(self.det_p)),
                                                     indent=2, ensure_ascii=False))
        files += [out / "serie_temporal.csv", out / "resumen.json"]
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(3, 1, figsize=(9, 6), sharex=True)
        t = self.series.t_s
        ax[0].plot(t, self.series.px_reales, label="reales"); ax[0].plot(t, self.series.px_detectados, label="detectados")
        ax[0].set_ylabel("píxeles"); ax[0].legend()
        ax[1].plot(t, self.series.iou); ax[1].set_ylabel("IoU"); ax[1].set_ylim(0, 1)
        ax[2].plot(t, self.series.z_max); ax[2].fill_between(t, 0, self.series.alarma * self.series.z_max.max(), alpha=.2, color="r")
        ax[2].set_ylabel("z máx"); ax[2].set_xlabel("t (s)")
        fig.tight_layout(); fig.savefig(out / "series.png", dpi=120); plt.close(fig)
        files.append(out / "series.png")
        return files


def run_long(cfg: sim.Config, path, det_p: dict | None = None, progress=None) -> LongRun:
    """Simula a disco en `path` y detecta fotograma a fotograma. `progress(fase, i, n)` es opcional."""
    path = Path(path)
    sim.simulate_to_dir(cfg, path, progress=(lambda i, n: progress("simulando", i, n)) if progress else None)
    seq = sim.load_sequence(path)
    meta = json.loads(str(seq["meta"]))
    p = {**core.DET_PARAM_DEFAULTS, **(det_p or {})}
    if p["gas_free_s"] is None:
        p["gas_free_s"] = round(0.8 * meta["leak_on_s"], 3)
    polarity = p["polarity"]
    if polarity == "auto":
        polarity = {"hot_plate": "dark", "cold_sky": "bright"}.get(meta.get("scene"), "both")
    import regions
    shape = tuple(seq["u8"].shape[1:3])
    roi = regions.polygon_mask(p["roi_text"], shape) if str(p["roi_text"]).strip() else None
    source = None
    if p["require_source"]:
        source = regions.polygon_mask(p["source_text"], shape) if str(p["source_text"]).strip() else regions.source_from_meta(meta, shape)
    mask = np.lib.format.open_memmap(path / "det_mask.npy", mode="w+", dtype=bool, shape=seq["u8"].shape)
    if progress:
        progress("detectando", 0, len(mask))
    res = detect(seq["u8"], meta["fps"], gas_free_s=p["gas_free_s"], sigma_px=p["sigma_px"], window_s=p["window_s"],
                 z_low=p["z_low"], z_high=p["z_high"], min_area=int(p["min_area"]), background=p["background"],
                 bg_tau_s=p["bg_tau_s"], bg_margin_px=int(p["bg_margin_px"]), bg_hold_on_alarm=p["bg_hold_on_alarm"],
                 drift_order=int(p["drift_order"]), stabilize=p["stabilize"], max_shift_px=p["max_shift_px"],
                 polarity=polarity, auto_threshold=p["auto_threshold"], roi=roi, source_roi=source,
                 require_source=p["require_source"], keep_maps=False, out_mask=mask)
    mask.flush()
    return LongRun(path, seq, res, mask, p)
