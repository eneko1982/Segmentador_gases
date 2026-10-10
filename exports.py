"""
exports.py - Exportación de resultados e imágenes del panel (sin interfaz).

Todas las funciones devuelven bytes (o un DataFrame) para que la interfaz las ofrezca como descarga.
"""
from __future__ import annotations

import base64
import html
import io
import json
import zipfile
from dataclasses import asdict

import matplotlib
import numpy as np
import pandas as pd
from PIL import Image

import ogi_sim as sim
from analysis import Analysis, clean
from viewer import colorize, compose_frame, gray_rgb

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

VIEW_NAMES = {"cam": "Cámara", "real": "Cambio real (mK)", "est": "Estimación del detector (mK)"}


# ---------------------------------------------------------------------------
# Datos tabulares
# ---------------------------------------------------------------------------
def series_dataframe(an: Analysis, truth, pred) -> pd.DataFrame:
    s = an.series(truth, pred)
    df = pd.DataFrame(s)
    df.insert(0, "frame", np.arange(len(df)) + 1)
    return df.rename(columns={"t_s": "t_s", "detected_px": "px_detectados", "true_px": "px_reales", "tp_px": "px_acierto",
                              "fp_px": "px_falso_positivo", "fn_px": "px_falso_negativo", "zmax": "z_max",
                              "alarm": "alarma"})


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, float_format="%.6g").encode("utf-8")


def changed_params(meta: dict) -> dict:
    """Parámetros del simulador que difieren de los valores por defecto."""
    base = asdict(sim.Config())
    return {k: meta[k] for k in base if k in meta and meta[k] != base[k]}


def summary_bytes(an: Analysis, truth_spec: dict, pred_spec: dict, window: str, det_params: dict) -> bytes:
    cfg = {k: v for k, v in an.meta.items() if k in sim.Config.__dataclass_fields__}
    out = dict(summary=an.summary(truth_spec, pred_spec, window), detector_parameters=det_params,
               simulator_parameters=cfg, derived=dict(k_per_dn=an.meta.get("k_per_dn"), emitted_g=an.meta.get("emitted_g")))
    return json.dumps(clean(out), indent=2, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Imágenes
# ---------------------------------------------------------------------------
class Frames:
    """Fotogramas de cada vista (cámara, cambio real, estimación) con las máscaras elegidas."""

    def __init__(self, seq: dict, an: Analysis, truth, pred, vmax: float = 100.0):
        self.seq, self.an, self.truth, self.pred, self.vmax = seq, an, truth, pred, vmax

    def base(self, view: str, i: int) -> np.ndarray:
        if view == "cam":
            return gray_rgb(self.seq["u8"][i])
        if view == "real":
            return colorize(self.seq["contrast"][i].astype(np.float32) * 1e3, self.vmax)
        return colorize(self.an.res["estimate"][i] * self.an.k * 1e3, self.vmax)

    def image(self, view: str, i: int, style="contour", opacity=0.9, thick=1, scale=1, show_t=True, show_p=True):
        t = self.truth[i] if (show_t and self.truth is not None) else None
        p = self.pred[i] if (show_p and self.pred is not None) else None
        return compose_frame(self.base(view, i), t, p, style, opacity, thick, scale)

    def available(self):
        return ["cam", "est"] + (["real"] if self.an.has_truth else [])


def png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def default_frames(an: Analysis, k: int = 4) -> list[int]:
    """k fotogramas repartidos desde que se abre la fuga hasta el final."""
    return sorted({int(x) for x in np.linspace(min(an.on + 3, an.n - 1), an.n - 1, k)})


def gallery_bytes(fr: Frames, frames: list[int], views=("cam", "est"), style="contour", opacity=0.9, thick=1) -> bytes:
    an = fr.an
    fig, ax = plt.subplots(len(views), len(frames), figsize=(3.6 * len(frames), 2.9 * len(views) + 0.5), squeeze=False)
    for r, v in enumerate(views):
        for c, i in enumerate(frames):
            ax[r, c].imshow(fr.image(v, i, style, opacity, thick))
            ax[r, c].set_xticks([]); ax[r, c].set_yticks([])
            if r == 0:
                ax[r, c].set_title(f"t = {i / an.fps:.2f} s", fontsize=10)
        ax[r, 0].set_ylabel(VIEW_NAMES[v], fontsize=9)
    legend = "verde: acierto · naranja: falso positivo · azul: falso negativo" if style == "error" else \
        "azul: máscara real · naranja: máscara detectada"
    fig.suptitle(legend, fontsize=10, y=0.995)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def series_figure_bytes(an: Analysis, df: pd.DataFrame, z_low: float, z_high: float) -> bytes:
    fig, ax = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    ax[0].plot(df["t_s"], df["px_detectados"], color="#eb6834", label="píxeles detectados")
    if "px_reales" in df:
        ax[0].plot(df["t_s"], df["px_reales"], color="#2a78d6", label="píxeles reales")
    ax[0].set_ylabel("píxeles con gas"); ax[0].legend()
    if "iou" in df:
        ax[1].plot(df["t_s"], df["iou"], color="#2ea043")
    ax[1].set_ylabel("IoU por fotograma"); ax[1].set_ylim(0, 1.02)
    ax[2].plot(df["t_s"], df["z_max"], color="#6b7280")
    ax[2].axhline(z_low, ls="--", color="#2a78d6", lw=1, label=f"z_low = {z_low:g}")
    ax[2].axhline(z_high, ls="--", color="#eb6834", lw=1, label=f"z_high = {z_high:g}")
    ax[2].set_ylabel("|z| máximo"); ax[2].set_xlabel("t (s)"); ax[2].legend()
    for a in ax:
        a.axvline(an.leak_on_s, color="gray", ls=":")
        a.axvspan(0, an.res["n_free"] / an.fps, color="gray", alpha=0.08)
    ax[0].set_title("Líneas punteadas: apertura de la fuga · zona gris: el detector aprende el fondo", fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def gif_bytes(fr: Frames, view="cam", stride=2, scale=2, style="contour", opacity=0.9, thick=1) -> bytes:
    frames = [Image.fromarray(fr.image(view, i, style, opacity, thick, scale)) for i in range(0, fr.an.n, stride)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0,
                   duration=int(1000 * stride / fr.an.fps))
    return buf.getvalue()


def npz_bytes(**arrays) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def _img(png: bytes, alt: str) -> str:
    return f'<img alt="{html.escape(alt)}" style="max-width:100%" src="data:image/png;base64,{base64.b64encode(png).decode()}">'


def _table(rows, headers) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _fmt(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def report_html(an: Analysis, summary: dict, det_params: dict, truth_spec: dict, pred_spec: dict,
                series_png: bytes, gallery_png: bytes) -> bytes:
    sm = summary
    rows = [("Entrada del detector", sm["input"]), ("Ruido tras el filtrado (mK)", _fmt(sm["noise_mk"], 1)),
            ("Ruido de un píxel, NETD (mK)", _fmt(sm["pixel_netd_mk"], 0)),
            ("Umbral propio del detector (mK)", _fmt(sm["detector_threshold_mk"], 1)),
            ("Retardo de detección (s)", _fmt(sm["detection_delay_s"], 2) if sm["detection_delay_s"] is not None else "no detectado"),
            ("Falsa alarma antes de la fuga", "sí" if sm["false_alarm_before_leak"] else "no"),
            ("Periodo de aprendizaje del fondo (s)", _fmt(sm["gas_free_period_s"], 2)),
            ("Gas emitido (g)", _fmt(sm.get("emitted_g"), 3))]
    if "gas_free_pixels_flagged_pct" in sm:
        rows.append(("Píxeles sin gas marcados (%)", _fmt(sm["gas_free_pixels_flagged_pct"], 2)))
        rows.append(("Cambio máximo en un píxel (mK)", _fmt(sm["max_pixel_change_mk"], 0)))
    sel = sm.get("selected_masks")
    sel_html = ""
    if sel:
        sel_html = ("<h2>Máscaras comparadas</h2>" + _table(
            [("Real", sel["truth"]), ("Detectada", sel["detected"]), ("Ventana", sel["window"]), ("IoU", _fmt(sel["iou"])),
             ("Recall", _fmt(sel["recall"])), ("Precisión", _fmt(sel["precision"]))], ["", ""]))
        sel_html += "<h2>Comparación a distintos umbrales</h2>" + _table(
            [(t["comparison"], _fmt(t["threshold_mk"], 0), _fmt(t["iou"]), _fmt(t["recall"]), _fmt(t["precision"]))
             for t in sm["table"]], ["Comparación", "Umbral (mK)", "IoU", "Recall", "Precisión"])
    changed = changed_params(an.meta)
    css = ("body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1f2937}"
           "table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #d1d5db;padding:4px 10px;text-align:left}"
           "th{background:#f3f4f6}h1,h2{margin-top:1.4em}code{background:#f3f4f6;padding:1px 4px;border-radius:3px}")
    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Informe del detector de gases</title>
<style>{css}</style></head><body><h1>Informe del detector de gases</h1>
<p>Secuencia simulada de {an.n} fotogramas a {an.fps:g} Hz; la fuga se abre en t = {an.leak_on_s:g} s.</p>
<h2>Resultados</h2>{_table(rows, ["Magnitud", "Valor"])}{sel_html}
<h2>Imágenes</h2>{_img(gallery_png, "Galería de fotogramas")}{_img(series_png, "Series temporales")}
<h2>Parámetros que difieren de los valores por defecto</h2>
{_table([(k, v) for k, v in changed.items()], ["Parámetro", "Valor"]) if changed else "<p>Ninguno: valores por defecto.</p>"}
<h2>Parámetros del detector</h2>{_table(list(det_params.items()), ["Parámetro", "Valor"])}
<details><summary>Configuración completa del simulador (JSON)</summary><pre>{html.escape(json.dumps(
        {k: v for k, v in an.meta.items() if k in sim.Config.__dataclass_fields__}, indent=2, ensure_ascii=False))}</pre></details>
</body></html>"""
    return doc.encode("utf-8")


# ---------------------------------------------------------------------------
# Paquete ZIP
# ---------------------------------------------------------------------------
def build_package(seq: dict, an: Analysis, truth, pred, truth_spec: dict, pred_spec: dict, window: str,
                  det_params: dict, options: dict) -> bytes:
    """ZIP con lo que marquen las `options` (ver la pestaña Exportar). Devuelve los bytes del ZIP."""
    style, opacity, thick, scale = options["style"], options["opacity"], options["thick"], options["scale"]
    fr = Frames(seq, an, truth, pred, options["vmax"])
    views = [v for v in options["views"] if v in fr.available()]
    frames = options["frames"] or default_frames(an)
    df = series_dataframe(an, truth, pred)
    summ = an.summary(truth_spec, pred_spec, window)
    z_low, z_high = det_params["z_low"], det_params["z_high"]
    series_png = series_figure_bytes(an, df, z_low, z_high)
    gallery_png = gallery_bytes(fr, frames, views or ["cam"], style, opacity, thick)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("resumen.json", summary_bytes(an, truth_spec, pred_spec, window, det_params))
        z.writestr("serie_temporal.csv", csv_bytes(df))
        z.writestr("figuras/series.png", series_png)
        z.writestr("figuras/galeria.png", gallery_png)
        if options["report"]:
            z.writestr("informe.html", report_html(an, summ, det_params, truth_spec, pred_spec, series_png, gallery_png))
        for i in frames:
            for v in views:
                z.writestr(f"figuras/fotogramas/{v}_{i + 1:04d}.png", png_bytes(fr.image(v, i, style, opacity, thick, scale)))
        if options["gif"]:
            z.writestr("video/superposicion.gif", gif_bytes(fr, "cam", options["gif_stride"], scale, style, opacity, thick))
        if options["all_frames"]:
            for i in range(0, an.n, options["frames_stride"]):
                z.writestr(f"fotogramas_png/cam_{i + 1:04d}.png", png_bytes(fr.image("cam", i, style, opacity, thick, scale)))
        if options["npz_seq"]:
            z.writestr("datos/secuencia.npz", npz_bytes(**seq))
        if options["npz_det"]:
            arrays = dict(mascara_detectada=pred, estimacion_mK=(an.res["estimate"] * an.k * 1e3).astype(np.float16))
            if truth is not None:
                arrays["mascara_real"] = truth
            z.writestr("datos/deteccion.npz", npz_bytes(**arrays))
    return buf.getvalue()
