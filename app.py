"""
app.py - Panel de control del simulador y del detector de gases.

    streamlit run app.py

Barra lateral (ajustes):
    1 · Vídeo y cámara        resolución, duración, ruido, escena, ventana de 8 bit…
    2 · Simulación del gas    gas, caudal, fuga, viento, turbulencia, chorro…
    3 · Cálculos del detector umbrales, suavizado, promedio temporal, periodo sin gas…
Pestañas:
    Vídeo        reproductor con máscara real y detectada superpuestas (activables), imágenes y estadísticas
    Resultados   métricas, tablas por umbral, series temporales y galería de fotogramas
    Exportar     CSV, JSON, imágenes, GIF, informe HTML y paquete ZIP
    Benchmark    resultados de benchmark.py (y botón para relanzarlo)
    Comparar umbrales   figura detector frente a verdad con el mismo umbral
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import altair as alt
import matplotlib
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import exports  # noqa: E402
import ogi_sim as sim  # noqa: E402
import viewer  # noqa: E402
from analysis import PRED_KINDS, TRUTH_KINDS, WINDOWS, Analysis  # noqa: E402
import core  # noqa: E402
import param_guide as pg  # noqa: E402
import longvideo  # noqa: E402
import video_io  # noqa: E402

ROOT = Path(__file__).resolve().parent
CATALOG = core.preset_catalog()
CONFIG_FIELDS = fields(sim.Config)
OPTIONAL = {f.name for f in CONFIG_FIELDS if "None" in f.type.replace(" ", "").split("|")}
NONE_FALLBACK = dict(level_c=25.0, span_c=10.0, t_exit_c=22.0, leak_off_s=3.0,
                     gas_molar_mass_kg_mol=0.10, gas_density_kg_m3=4.0)
FLOW_OPTIONS = [0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
SCENE_LABEL = {"hot_plate": "Placa caliente (el gas absorbe: se ve oscuro)",
               "cold_sky": "Cielo frío (el gas emite: se ve claro)"}
VIEW_LABEL = exports.VIEW_NAMES
VIDEO_MAIN = ["scene", "t_bg_c", "netd_k", "duration_s"]
SIM_MAIN = ["gas", "flow_g_s", "leak_on_s", "wind_ms", "gust_ms", "turb_rms"]
VIDEO_GROUPS = ["Cámara", "Escena", "Salida y ejecución"]
SIM_GROUPS = ["Gas y fuga", "Chorro y pluma", "Viento y turbulencia"]
DET_DEFAULTS = {"det_auto_gf": True, "det_gas_free": 1.0, "det_profile": "Estable (por defecto)",
                **{f"det_{k}": v for k, v in core.DET_PARAM_DEFAULTS.items() if k != "gas_free_s"}}
NO_PRESET = "Por defecto"
UI_DEFAULTS = dict(level="B", auto_det=True, res_mode="Rápida (½ resolución)", vid_truth_kind="det", vid_truth_mk=35.0,
                   vid_pred_kind="mask", vid_pred_mk=35.0, vid_vmax=100, vid_stride=1, vid_zoom=520,
                   res_window="last", res_gallery_k=4, exp_style="contour", exp_opacity=0.9, exp_thick=1,
                   exp_scale=3, exp_views=["cam", "est"], res_views=["cam", "est"], exp_report=True, exp_gif=True, exp_gif_stride=2,
                   exp_all_frames=False, exp_frames_stride=2, exp_npz_seq=False, exp_npz_det=True)

st.set_page_config(page_title="Segmentador de gases", page_icon="🌫️", layout="wide")
st.markdown("<style>section[data-testid='stSidebar']{min-width:400px;max-width:520px}"
            ".block-container{padding-top:2.2rem}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Estado y parámetros del simulador
# ---------------------------------------------------------------------------
def set_params(over: dict, only: str | None = None):
    cfg = sim.apply_overrides(sim.Config(), over)
    for f in CONFIG_FIELDS:
        if only and f.name != only:
            continue
        v = getattr(cfg, f.name)
        if f.name in OPTIONAL:
            st.session_state[f"u_{f.name}"] = v is not None
            v = NONE_FALLBACK[f.name] if v is None else v
        st.session_state[f"p_{f.name}"] = v


def init_state():
    # Streamlit olvida los widgets que no se dibujan (p. ej. al bajar el nivel): se reasigna cada valor para conservarlo.
    for k in [k for k in st.session_state if k.startswith(("p_", "u_", "det_"))]:
        st.session_state[k] = st.session_state[k]
    if "p_scene" not in st.session_state:
        set_params({})
    for k, v in {**DET_DEFAULTS, **UI_DEFAULTS}.items():
        st.session_state.setdefault(k, v)


def on_preset():
    name = st.session_state["preset"]
    try:
        set_params({} if name == NO_PRESET else sim.load_preset(name))
    except ValueError as err:
        st.session_state["preset_error"] = str(err)
        return
    st.session_state["det_profile"] = CATALOG[name]["detector"] if name in CATALOG else DET_DEFAULTS["det_profile"]
    apply_profile()


def current_config() -> sim.Config:
    over = {}
    for f in CONFIG_FIELDS:
        v = st.session_state[f"p_{f.name}"]
        if f.name in OPTIONAL and not st.session_state[f"u_{f.name}"]:
            v = None
        over[f.name] = v
    return sim.apply_overrides(sim.Config(), over)


def command_line(cfg: sim.Config) -> str:
    base, parts = sim.Config(), []
    for f in CONFIG_FIELDS:
        v, d = getattr(cfg, f.name), getattr(base, f.name)
        if v != d:
            parts.append(f"--set {f.name}=" + ("none" if v is None else (json.dumps(v) if not isinstance(v, str) else v)))
    return "python ogi_sim.py --out demo --preview " + " ".join(parts)


# ---------------------------------------------------------------------------
# Widgets de la barra lateral
# ---------------------------------------------------------------------------
def _step(default: float) -> float:
    return 0.01 if not default else 10.0 ** (math.floor(math.log10(abs(default))) - 1)


def _fmt(v) -> str:
    if v is None:
        return "sin definir"
    if isinstance(v, bool):
        return "sí" if v else "no"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _default(name: str):
    return getattr(sim.Config(), name)


def _label(name: str) -> str:
    f = sim.Config.__dataclass_fields__[name]
    unit = f" ({f.metadata['unit']})" if f.metadata["unit"] else ""
    return f"{pg.guide(name)['title']}{unit}"


def _help(name: str) -> str:
    return pg.help_text(name, _fmt(_default(name)))


def gas_widget(label="Gas", doc=""):
    options = list(sim.GASES) + ["personalizado"]
    if st.session_state["p_gas"] not in options:
        options.insert(0, st.session_state["p_gas"])
    st.selectbox(label, options, key="p_gas", help=(doc or "Gas liberado.") +
                 " «personalizado» exige masa molar y densidad (nivel Experto).")


def slider(name: str, lo: float, hi: float, step: float):
    """Deslizador de rango fijo (si cambiara, Streamlit lo reiniciaría); solo se amplía si un valor cargado queda fuera."""
    v = st.session_state[f"p_{name}"]
    st.slider(_label(name), min_value=min(lo, v), max_value=max(hi, v), step=step, key=f"p_{name}", help=_help(name),
              format="%g")


SLIDERS = dict(netd_k=(0.005, 0.2, 0.005), duration_s=(1.0, 60.0, 0.5), leak_on_s=(0.0, 60.0, 0.1),
               wind_ms=(-5.0, 5.0, 0.05), gust_ms=(0.0, 2.0, 0.01), turb_rms=(0.0, 1.0, 0.01))


def widget(f):
    """Widget de un parámetro de Config: etiqueta en lenguaje llano y ayuda con qué hace, qué pasa si se sube y valor típico."""
    name, meta, key = f.name, f.metadata, f"p_{f.name}"
    label, doc, lo, hi = _label(name), _help(name), meta["lo"], meta["hi"]
    if name in SLIDERS:
        slider(name, *SLIDERS[name])
    elif name == "flow_g_s":
        v = st.session_state[key]
        st.select_slider(label, options=sorted(set(FLOW_OPTIONS + [v])), key=key, help=doc)
    elif f.type == "bool":
        st.checkbox(label, key=key, help=doc)
    elif name == "scene":
        st.selectbox(label, sim.SCENES, key=key, format_func=SCENE_LABEL.get, help=doc)
    elif meta["choices"]:
        st.selectbox(label, meta["choices"], key=key, help=doc, format_func=lambda c: SCENE_LABEL.get(c, c))
    elif name == "gas":
        gas_widget(label, doc)
    else:
        disabled = False
        if name in OPTIONAL:
            disabled = not st.checkbox(f"Definir: {pg.guide(name)['title']}", key=f"u_{name}", help=doc)
        if "int" in f.type.replace(" ", "").split("|"):
            st.number_input(label, key=key, min_value=int(lo), max_value=int(hi), step=1, help=doc, disabled=disabled)
        else:
            st.number_input(label, key=key, min_value=None if lo is None else float(lo),
                            max_value=None if hi is None else float(hi), step=_step(f.default or 1.0),
                            format="%g", help=doc, disabled=disabled)


def level_names() -> set:
    return set(pg.names_at_level(st.session_state["level"], "sim"))


def simulator_sections():
    """Parámetros del simulador agrupados por la parte del sistema a la que afectan, filtrados por nivel."""
    visible = level_names()
    order = [("video", "1 · Vídeo y cámara"), ("escena", "2 · Escena"), ("gas", "3 · Gas y fuga"),
             ("viento", "4 · Viento y turbulencia"), ("perturb", "5 · Perturbaciones del mundo real"),
             ("salida", "6 · Salida")]
    for part, title in order:
        fields_ = [f for f in CONFIG_FIELDS if f.name in visible and pg.guide(f.name)["part"] == part]
        if not fields_:
            continue
        with st.expander(title, expanded=part in ("video", "gas")):
            if part == "video":
                st.radio("Resolución", ["Rápida (½ resolución)", "Completa"], key="res_mode", horizontal=True,
                         help="La vista rápida usa la mitad de píxeles con el mismo campo de visión: unas 4 veces más rápida.")
            for f in fields_:
                widget(f)


def reset_param(name: str):
    set_params({name: _default(name)}, only=name)


def changes_summary():
    """Lista de lo cambiado respecto al valor por defecto, con un botón para devolver cada uno."""
    try:
        cfg = current_config()
    except ValueError:
        return
    ch = core.changed_from_default(cfg)
    dch = core.changed_detector(det_params())
    with st.expander(f"Lo que has cambiado ({len(ch) + len(dch)})", expanded=False):
        if not ch and not dch:
            st.caption("Todo está en sus valores por defecto.")
        for name, v, d in ch:
            c = st.columns([5, 1])
            c[0].markdown(f"**{pg.guide(name)['title']}**: {_fmt(v)}  \n<small>por defecto {_fmt(d)}</small>",
                          unsafe_allow_html=True)
            c[1].button("↩", key=f"rs_{name}", on_click=reset_param, args=(name,), help="Volver al valor por defecto")
        for name, v, d in dch:
            st.markdown(f"**Detector · {pg.guide(name)['title']}**: {_fmt(v)}  \n<small>por defecto {_fmt(d)}</small>",
                        unsafe_allow_html=True)
        if ch or dch:
            st.button("Volver a todo por defecto", key="btn_reset_all", on_click=reset_all)


def reset_all():
    st.session_state["preset"] = NO_PRESET
    set_params({})
    for k, v in {**DET_DEFAULTS, **UI_DEFAULTS}.items():
        st.session_state[k] = v


def apply_profile():
    prof = core.DETECTOR_PROFILES.get(st.session_state["det_profile"])
    if prof is None:
        return
    for k, v in {**core.DET_PARAM_DEFAULTS, **prof["params"]}.items():
        if f"det_{k}" in st.session_state:
            st.session_state[f"det_{k}"] = v


def detector_section():
    s = st.session_state
    lvl = pg.names_at_level(s["level"], "det")
    st.selectbox("Qué detector usar", list(core.DETECTOR_PROFILES), key="det_profile", on_change=apply_profile,
                 help="Perfiles ya preparados. Si no sabes cuál, deja «Estable» salvo que tu vídeo tenga sol, vibración, "
                      "obturador o compresión (los escenarios de arriba sugieren uno).")
    prof = core.DETECTOR_PROFILES[s["det_profile"]]
    st.caption(f"{prof['desc']}  \n**Coste:** {prof['cost']}")

    def has(n):
        return n in lvl

    def h(n):
        return pg.help_text(n, _fmt(core.DET_PARAM_DEFAULTS.get(n)))

    def t(n):
        return pg.guide(n)["title"]

    if has("input"):
        st.radio(t("input"), ["u8", "temp"], key="det_input", horizontal=True, help=h("input"))
    st.checkbox("Periodo sin gas automático (80 % del tiempo antes de la fuga)", key="det_auto_gf", help=h("gas_free_s"))
    st.number_input(t("gas_free_s") + " (s)", 0.1, 60.0, step=0.1, key="det_gas_free", disabled=s["det_auto_gf"], help=h("gas_free_s"))
    if has("z_low"):
        c = st.columns(2)
        c[0].number_input(t("z_low"), 0.5, 10.0, step=0.1, key="det_z_low", help=h("z_low"))
        c[1].number_input(t("z_high"), 0.5, 20.0, step=0.1, key="det_z_high", help=h("z_high"))
        c = st.columns(2)
        c[0].number_input(t("sigma_px") + " (px)", 0.5, 10.0, step=0.5, key="det_sigma_px", help=h("sigma_px"))
        c[1].number_input(t("window_s") + " (s)", 0.04, 2.0, step=0.04, key="det_window_s", format="%g", help=h("window_s"))
        st.number_input(t("min_area") + " (px)", 1, 5000, step=5, key="det_min_area", help=h("min_area"))
    if has("background"):
        st.selectbox(t("background"), ["static", "adaptive"], key="det_background", help=h("background"),
                     format_func=lambda c: {"static": "Fijo (aprendido al principio)", "adaptive": "Adaptativo"}[c])
        st.selectbox(t("drift_order"), [0, 1, 2], key="det_drift_order", help=h("drift_order"),
                     format_func=lambda c: {0: "Solo un nivel global", 1: "Superficie plana inclinada", 2: "Superficie curva"}[c])
        st.checkbox(t("stabilize"), key="det_stabilize", help=h("stabilize"))
        st.selectbox(t("polarity"), ["both", "dark", "bright", "auto"], key="det_polarity", help=h("polarity"),
                     format_func=lambda c: dict(both="Cualquiera", dark="Solo más oscuro", bright="Solo más claro",
                                                auto="Automático según la escena")[c])
        st.checkbox(t("auto_threshold"), key="det_auto_threshold", help=h("auto_threshold"))
        st.text_input(t("roi_text"), key="det_roi_text", help=h("roi_text"), placeholder="0.1,0.1; 0.9,0.1; 0.9,0.9")
        st.checkbox(t("require_source"), key="det_require_source", help=h("require_source"))
    if has("bg_tau_s"):
        st.number_input(t("bg_tau_s") + " (s)", 1.0, 600.0, step=1.0, key="det_bg_tau_s", help=h("bg_tau_s"))
        st.text_input(t("source_text"), key="det_source_text", help=h("source_text"))
        st.number_input(t("bg_margin_px") + " (px)", 0, 50, step=1, key="det_bg_margin_px", help=h("bg_margin_px"))
        st.checkbox(t("bg_hold_on_alarm"), key="det_bg_hold_on_alarm", help=h("bg_hold_on_alarm"))
        st.number_input(t("max_shift_px") + " (px)", 1.0, 30.0, step=1.0, key="det_max_shift_px", help=h("max_shift_px"))


# ---------------------------------------------------------------------------
# Simulación y detección
# ---------------------------------------------------------------------------
def seq_info(seq: dict) -> dict:
    meta = json.loads(str(seq["meta"]))
    return dict(meta=meta, fps=meta["fps"], leak_on_s=meta["leak_on_s"])


def new_sequence(seq: dict):
    st.session_state.update(seq=seq, seq_version=st.session_state.get("seq_version", 0) + 1)
    for k in ("det", "det_error", "masks_cache", "sum_cache", "exp_items"):
        st.session_state.pop(k, None)


def run_simulation():
    try:
        cfg = current_config()
    except ValueError as err:
        st.sidebar.error(str(err))
        return
    run_cfg = sim.half_resolution(cfg) if st.session_state["res_mode"].startswith("Rápida") else cfg
    with st.spinner("Simulando…"):
        new_sequence(sim.simulate(run_cfg))
    if st.session_state["auto_det"]:
        run_detector()


def det_params() -> dict:
    s = st.session_state
    info = seq_info(s["seq"]) if "seq" in s else None
    gf = 0.8 * info["leak_on_s"] if (info and s["det_auto_gf"]) else s["det_gas_free"]
    p = {k: s[f"det_{k}"] for k in core.DET_PARAM_DEFAULTS if k != "gas_free_s"}
    p.update(gas_free_s=round(gf, 3), min_area=int(p["min_area"]), drift_order=int(p["drift_order"]))
    return p


def run_detector():
    s = st.session_state
    seq = s.get("seq")
    if seq is None:
        return
    p = det_params()
    with st.spinner("Calculando el detector…"):
        an, msg, p = core.run_detection(seq, p)
    if an is None:
        s["det_error"] = msg
        s.pop("det", None)
        return
    if msg:
        s["det_error"] = msg
    else:
        s.pop("det_error", None)
    s["det"] = dict(an=an, params=p, version=s["seq_version"], uid=s.get("det_uid", 0) + 1)
    s["det_uid"] = s["det"]["uid"]
    for k in ("masks_cache", "sum_cache", "exp_items"):
        s.pop(k, None)


def load_uploaded(file) -> dict | None:
    try:
        data = np.load(io.BytesIO(file.getvalue()), allow_pickle=False)
        seq = {k: data[k] for k in data.files}
    except Exception as err:  # noqa: BLE001
        st.sidebar.error(f"No se pudo leer el archivo: {err}")
        return None
    if "u8" not in seq or "meta" not in seq:
        st.sidebar.error("El .npz debe contener al menos `u8` y `meta` (lo escribe ogi_sim.py).")
        return None
    return seq


LONG_DIR = ROOT / "salida" / "largo"


def long_section():
    st.caption("Para vídeos de minutos u horas: se simula y se detecta fotograma a fotograma, guardando en disco, así que "
               "la memoria no depende de la duración. Usa los parámetros de arriba; la duración se define aquí. "
               "No se guardan los mapas z ni la estimación (ocuparían como el vídeo).")
    dur = st.number_input("Duración del vídeo largo (s)", 5.0, 7200.0, 120.0, 5.0, key="long_dur")
    try:
        cfg = sim.apply_overrides(current_config(), dict(duration_s=float(dur)))
        run_cfg = sim.half_resolution(cfg) if st.session_state["res_mode"].startswith("Rápida") else cfg
        est = longvideo.disk_estimate(run_cfg)
        st.caption(f"{est['n_frames']} fotogramas a {run_cfg.width}×{run_cfg.height}: unos {est['need_gb']:.2f} GB en disco "
                   f"({est['free_gb']:.1f} GB libres). Tiempo orientativo: {est['n_frames'] / 15 / 60:.1f} min.")
        ok = est["need_gb"] < est["free_gb"] * 0.9
        if not ok:
            st.error("No hay espacio en disco suficiente.")
    except ValueError as err:
        st.error(str(err))
        return
    if st.button("▶ Simular vídeo largo", key="btn_long", disabled=not ok):
        bar = st.progress(0.0, text="Simulando…")
        det_p = det_params()
        det_p["gas_free_s"] = round(0.8 * cfg.leak_on_s, 3) if st.session_state["det_auto_gf"] else st.session_state["det_gas_free"]

        def prog(phase, i, n):
            bar.progress(min(i / max(n, 1), 1.0) * (0.85 if phase == "simulando" else 1.0), text=f"{phase.capitalize()}… {i}/{n}")
        try:
            LONG_DIR.parent.mkdir(exist_ok=True)
            st.session_state["long"] = longvideo.run_long(run_cfg, LONG_DIR, det_p, progress=prog)
            st.session_state.pop("long_error", None)
        except ValueError as err:
            st.session_state["long_error"] = str(err)
        bar.empty()


def tab_largo():
    run = st.session_state.get("long")
    if st.session_state.get("long_error"):
        st.error(st.session_state["long_error"])
    if run is None:
        st.info("Abre «Vídeo largo (se guarda en disco)» en la barra lateral, elige la duración y pulsa **▶ Simular vídeo largo**.")
        return
    sm = run.summary
    c = st.columns(5)
    c[0].metric("Duración", f"{sm['duration_s']:.0f} s")
    c[1].metric("Retraso de detección", "—" if sm["detection_delay_s"] is None else f"{sm['detection_delay_s']:.2f} s")
    c[2].metric("Falsa alarma antes de la fuga", "sí" if sm["false_alarm_before_leak"] else "no")
    if "scores" in sm:
        c[3].metric("IoU (todo el vídeo)", f"{sm['scores']['all']['iou']:.2f}")
        c[4].metric("IoU (último segundo)", f"{sm['scores']['last']['iou']:.2f}")
    mf = st.select_slider("Fotogramas en el reproductor", [50, 100, 150, 250, 400], 150, key="long_frames")
    payload = run.payload(mf)
    components.html(viewer.player_html(payload, st.session_state["vid_zoom"]), height=viewer.player_height(payload), scrolling=False)
    st.caption(payload["note"] + ". Los datos completos están en la carpeta " + str(run.path) + ".")
    df = run.series
    chart_df = df.set_index("t_s")[["px_reales", "px_detectados"]]
    st.line_chart(chart_df)
    st.line_chart(df.set_index("t_s")[["iou"]])
    st.line_chart(df.set_index("t_s")[["z_max"]])
    if st.button("Exportar CSV, resumen y figura a la carpeta del vídeo", key="btn_long_exp"):
        files = run.export(run.path / "resultados")
        st.success("Escrito: " + ", ".join(str(f) for f in files))
    st.download_button("Descargar serie temporal (CSV)", exports.csv_bytes(df), "serie_temporal_largo.csv", "text/csv")


def video_loader():
    st.caption("Un vídeo real no tiene máscara verdadera: se ve y se detecta, pero sin IoU ni recall. Hay que decir cuántos "
               "segundos iniciales no tienen gas. Si no conoces los K por nivel de gris, déjalo en 0: el detector trabaja en "
               "niveles de gris (los «mK» mostrados serán niveles ×1000).")
    f = st.file_uploader("Vídeo", type=[e.lstrip(".") for e in video_io.EXTENSIONS], key="up_video")
    c = st.columns(2)
    gf = c[0].number_input("Segundos iniciales sin gas", 0.5, 120.0, 3.0, 0.5, key="vid_gf")
    k = c[1].number_input("K por nivel de gris (0 = desconocido)", 0.0, 5.0, 0.0, 0.001, format="%g", key="vid_k")
    c = st.columns(3)
    fps = c[0].number_input("FPS (0 = el del archivo)", 0.0, 120.0, 0.0, 1.0, key="vid_fps")
    mx = c[1].number_input("Máx. segundos", 1.0, 600.0, 30.0, 1.0, key="vid_max")
    sc = c[2].number_input("Escala", 0.1, 1.0, 1.0, 0.1, key="vid_scale")
    if f is not None and st.button("Usar este vídeo", key="btn_vid"):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f.name
            path.write_bytes(f.getvalue())
            try:
                with st.spinner("Leyendo el vídeo…"):
                    probe = fps or 25.0
                    seq = video_io.load_video(path, gas_free_s=gf, k_per_dn=k, fps=fps or None, scale=sc,
                                              max_frames=int(mx * probe))
            except (ValueError, RuntimeError) as err:
                st.error(str(err))
                return
        st.session_state.update(det_auto_gf=False, det_gas_free=float(gf), det_polarity="both")
        new_sequence(seq)
        run_detector()


def sidebar():
    with st.sidebar:
        st.markdown("## 🌫️ Segmentador de gases")
        names = {NO_PRESET: NO_PRESET} | {k: v["title"] for k, v in CATALOG.items()}
        st.selectbox("Escenario", list(names), key="preset", on_change=on_preset, format_func=names.get,
                     help="Situaciones ya preparadas (carpeta configs/). Al elegir una se cargan sus parámetros y se sugiere el detector adecuado.")
        if st.session_state["preset"] in CATALOG:
            st.caption(CATALOG[st.session_state["preset"]]["comment"])
        if st.session_state.pop("preset_error", None):
            st.error("No se pudo cargar el escenario.")
        st.radio("Cuántos ajustes ver", list(pg.LEVELS), key="level", horizontal=True, format_func=pg.LEVELS.get,
                 help="Básico: lo que normalmente se toca (unos 14 ajustes). Avanzado: más detalle. Experto: los 100+ parámetros. "
                      "Lo que no ves conserva su valor por defecto. Pasa el ratón por el «?» de cada ajuste para ver qué hace.")
        try:
            cfg = current_config()
            run_cfg = sim.half_resolution(cfg) if st.session_state["res_mode"].startswith("Rápida") else cfg
            st.caption(f"Se simulará a {run_cfg.width}×{run_cfg.height} px, {run_cfg.duration_s:g} s, "
                       f"{int(round(run_cfg.duration_s * run_cfg.fps))} fotogramas.")
            ok = True
        except ValueError as err:
            st.error(str(err))
            ok = False
        if st.button("▶ Simular y calcular", type="primary", key="btn_sim", disabled=not ok, width="stretch"):
            run_simulation()
        c = st.columns([3, 2])
        c[0].checkbox("Calcular el detector tras simular", key="auto_det")
        if c[1].button("Recalcular detector", key="btn_det", disabled="seq" not in st.session_state):
            run_detector()
        changes_summary()
        simulator_sections()
        with st.expander("7 · Detector (los cálculos)", expanded=True):
            detector_section()
        with st.expander("Cargar una secuencia .npz"):
            file = st.file_uploader("Archivo de ogi_sim.py", type=["npz"], key="up_file")
            if file is not None and st.button("Usar esta secuencia", key="btn_up"):
                seq = load_uploaded(file)
                if seq is not None:
                    new_sequence(seq)
                    run_detector()
        with st.expander("Vídeo largo (se guarda en disco)"):
            long_section()
        with st.expander("Cargar un vídeo real (.mp4, .avi…)"):
            video_loader()
        with st.expander("Comando equivalente (resolución completa)"):
            if ok:
                st.code(command_line(cfg), language="bash")
        st.button("Restablecer todo", key="btn_reset", on_click=reset_all)
        st.caption("Guía de todos los parámetros: pestaña 📖 Guía. `band_fraction` y `k_abs` son marcadores: hay que "
                   "calibrarlos con la cámara real.")


# ---------------------------------------------------------------------------
# Máscaras elegidas
# ---------------------------------------------------------------------------
def current_det():
    det = st.session_state.get("det")
    return det if det is not None and det["version"] == st.session_state.get("seq_version") else None


def specs() -> tuple[dict, dict]:
    s = st.session_state
    return ({"kind": s["vid_truth_kind"], "mk": s["vid_truth_mk"]}, {"kind": s["vid_pred_kind"], "mk": s["vid_pred_mk"]})


def get_summary(det):
    """Resumen de resultados para las máscaras y la ventana elegidas (se recalcula solo si cambian)."""
    ts, ps = specs()
    key = (det["uid"], tuple(ts.items()), tuple(ps.items()), st.session_state["res_window"])
    cache = st.session_state.get("sum_cache")
    if cache is None or cache[0] != key:
        cache = (key, det["an"].summary(ts, ps, st.session_state["res_window"]))
        st.session_state["sum_cache"] = cache
    return cache[1]


def get_masks(det):
    """Máscaras real y detectada para las especificaciones elegidas (se guardan para no recalcular)."""
    ts, ps = specs()
    key = (det["uid"], ts["kind"], ts["mk"] if ts["kind"] == "custom" else 0, ps["kind"],
           ps["mk"] if ps["kind"] == "custom" else 0)
    cache = st.session_state.get("masks_cache")
    if cache is None or cache[0] != key:
        an = det["an"]
        cache = (key, an.truth(ts), an.pred(ps))
        st.session_state["masks_cache"] = cache
    return cache[1], cache[2], ts, ps


# ---------------------------------------------------------------------------
# Pestaña Vídeo
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Preparando el reproductor…", max_entries=3)
def cached_payload(key, _seq, _an, _truth, _pred, views, vmax, stride):
    return core.build_player_payload(_seq, _an, _truth, _pred, views, vmax, stride)


def kpi_row(seq, det):
    info = seq_info(seq)
    c = st.columns(5)
    c[0].metric("Fotogramas", len(seq["u8"]))
    c[1].metric("Gas emitido", "—" if info["meta"].get("emitted_g") is None else f"{info['meta']['emitted_g']:.3f} g")
    c[2].metric("Cambio máximo en un píxel", f"{1e3 * float(np.abs(seq['contrast']).max()):.0f} mK" if "contrast" in seq else "—")
    if det is not None:
        sm = get_summary(det)
        c[3].metric("Retardo de detección", "no detectado" if sm["detection_delay_s"] is None else f"{sm['detection_delay_s']:.2f} s")
        c[4].metric("IoU (máscaras elegidas)", f"{sm['selected_masks']['iou']:.2f}" if "selected_masks" in sm else "—")
    else:
        c[3].metric("mK por nivel de gris", f"{1e3 * info['meta']['k_per_dn']:.0f}")


def tab_video():
    seq = st.session_state.get("seq")
    if seq is None:
        st.info("Ajusta los parámetros de la barra lateral y pulsa **▶ Simular y calcular**. También puedes cargar un "
                "`.npz` de `ogi_sim.py` desde «Cargar una secuencia .npz».")
        return
    det = current_det()
    if st.session_state.get("det_error"):
        st.warning(st.session_state["det_error"])
    kpi_row(seq, det)

    has_truth = "contrast" in seq
    with st.expander("Máscaras y vistas", expanded=True):
        c = st.columns([2, 2, 2])
        views_avail = ["cam"] + (["real"] if has_truth else []) + (["est"] if det else [])
        defaults = [v for v in (["cam", "est"] if det else ["cam"]) if v in views_avail]
        views = c[0].multiselect("Vistas", views_avail, default=defaults, format_func=VIEW_LABEL.get, key="vid_views_sel")
        c[1].slider("Escala del mapa (± mK)", 10, 500, step=10, key="vid_vmax",
                    help="Escala de color de «Cambio real» y «Estimación del detector».")
        c[2].select_slider("Fotogramas mostrados", [1, 2, 3, 4], key="vid_stride",
                           format_func=lambda s: "todos" if s == 1 else f"1 de cada {s}",
                           help="Menos fotogramas = reproductor más ligero.")
        if det is not None:
            an = det["an"]
            ts_opts = {k: an.label({"kind": k, "mk": st.session_state["vid_truth_mk"]}, "truth") for k in TRUTH_KINDS}
            ps_opts = {k: an.label({"kind": k, "mk": st.session_state["vid_pred_mk"]}, "pred") for k in PRED_KINDS}
            c = st.columns([3, 1, 3, 1])
            if has_truth:
                c[0].selectbox("Máscara real (verdad del simulador)", TRUTH_KINDS, key="vid_truth_kind",
                               format_func=ts_opts.get, help="Define qué píxeles cuentan como «gas real».")
                if st.session_state["vid_truth_kind"] == "custom":
                    c[1].number_input("mK", 0.5, 2000.0, step=0.5, key="vid_truth_mk")
            c[2].selectbox("Máscara detectada", PRED_KINDS, key="vid_pred_kind", format_func=ps_opts.get,
                           help="La máscara del detector, o la estimación con un umbral fijo.")
            if st.session_state["vid_pred_kind"] == "custom":
                c[3].number_input("mK", 0.5, 2000.0, step=0.5, key="vid_pred_mk", label_visibility="visible")
        else:
            st.caption("Calcula el detector para ver su máscara y para elegir otros umbrales. Mientras tanto se puede "
                       "superponer la máscara real del simulador.")

    s = st.session_state
    stride = int(s["vid_stride"])
    info = seq_info(seq)
    if not views:
        st.info("Elige al menos una vista.")
        return
    if det is not None:
        truth, pred, ts, ps = get_masks(det)
        key = (s["seq_version"], det["uid"], tuple(views), s["vid_vmax"], stride, tuple(ts.items()), tuple(ps.items()))
        an, learn = det["an"], det["an"].res["n_free"] / info["fps"]
    else:
        truth, pred, an, learn = (seq["mask"] if "mask" in seq else None), None, None, 0.0
        key = (s["seq_version"], "nodet", tuple(views), s["vid_vmax"], stride)
    payload = cached_payload(key, seq, an, truth, pred, tuple(views), s["vid_vmax"], stride)
    components.html(viewer.player_html(payload, s["vid_zoom"]), height=viewer.player_height(payload), scrolling=False)
    st.caption("Espacio: reproducir/pausar · ←/→: fotograma anterior/siguiente. Rojo = más caliente, azul = más frío "
               "en los mapas de mK. El botón «Descargar fotograma» guarda las vistas con las máscaras activas. "
               "«Dibujar zona» marca un polígono: copia sus coordenadas en el detector (nivel Avanzado: zona de interés / "
               "zona de origen).")


# ---------------------------------------------------------------------------
# Pestaña Resultados
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=4)
def cached_series(key, _an, _truth, _pred):
    return exports.series_dataframe(_an, _truth, _pred)


def tab_resultados():
    seq, det = st.session_state.get("seq"), current_det()
    if seq is None:
        st.info("Primero genera una secuencia.")
        return
    if det is None:
        st.info("Calcula el detector (barra lateral) para ver los resultados.")
        return
    an, s = det["an"], st.session_state
    truth, pred, ts, ps = get_masks(det)
    st.selectbox("Ventana de tiempo para las métricas", list(WINDOWS), format_func=WINDOWS.get, key="res_window",
                 help="Las métricas por píxel se acumulan en esa ventana.")
    sm = get_summary(det)
    c = st.columns(4)
    c[0].metric("Ruido tras el filtrado", f"{sm['noise_mk']:.1f} mK", help=f"Ruido de un solo píxel (NETD): {sm['pixel_netd_mk']:.0f} mK")
    c[1].metric("Umbral propio del detector", f"{sm['detector_threshold_mk']:.1f} mK")
    c[2].metric("Retardo de detección", "no detectado" if sm["detection_delay_s"] is None else f"{sm['detection_delay_s']:.2f} s")
    c[3].metric("Falsa alarma antes de la fuga", "SÍ" if sm["false_alarm_before_leak"] else "no")
    if an.has_truth:
        c = st.columns(4)
        sel = sm["selected_masks"]
        c[0].metric("IoU", f"{sel['iou']:.3f}")
        c[1].metric("Recall", f"{sel['recall']:.3f}")
        c[2].metric("Precisión", f"{sel['precision']:.3f}")
        c[3].metric("Píxeles sin gas marcados", f"{sm['gas_free_pixels_flagged_pct']:.2f} %")
        st.caption(f"Máscaras comparadas: **{sel['truth']}** (real) frente a **{sel['detected']}** (detectada). "
                   "Se cambian en la pestaña Vídeo.")
        st.markdown("**Comparación a distintos umbrales**")
        st.dataframe(pd.DataFrame([{"Comparación": r["comparison"], "Umbral (mK)": round(r["threshold_mk"]),
                                    "IoU": r["iou"], "Recall": r["recall"], "Precisión": r["precision"]}
                                   for r in sm["table"]]), hide_index=True, width="stretch")
    else:
        st.info("Esta secuencia no incluye la verdad (`contrast`): no hay métricas de acierto.")

    key = (det["uid"], tuple(ts.items()), tuple(ps.items()))
    df = cached_series(key, an, truth, pred)
    base = alt.Chart(df).encode(x=alt.X("t_s:Q", title="t (s)"))
    rules = alt.Chart(pd.DataFrame({"t": [an.leak_on_s]})).mark_rule(strokeDash=[4, 4], color="gray").encode(x=alt.X("t:Q", title="t (s)"))
    long = df.melt("t_s", [c for c in ("px_detectados", "px_reales") if c in df], "serie", "píxeles")
    long["serie"] = long["serie"].map({"px_detectados": "detectados", "px_reales": "reales"})
    cols = {"detectados": "#eb6834", "reales": "#2a78d6"}
    xt = alt.X("t_s:Q", title="t (s)")
    c1 = alt.Chart(long).mark_line().encode(x=xt, y=alt.Y("píxeles:Q", title="píxeles con gas"),
                                             color=alt.Color("serie:N", scale=alt.Scale(domain=list(cols), range=list(cols.values())),
                                                             legend=alt.Legend(title=None, orient="top")))
    st.markdown("**Píxeles con gas en el tiempo** (línea punteada: apertura de la fuga)")
    st.altair_chart((c1 + rules).properties(height=210), width="stretch")
    c = st.columns(2)
    if "iou" in df:
        c[0].markdown("**IoU por fotograma**")
        c[0].altair_chart((base.mark_line(color="#2ea043").encode(y=alt.Y("iou:Q", scale=alt.Scale(domain=[0, 1]), title="IoU")) + rules)
                          .properties(height=200), width="stretch")
    zl, zh = s["det_z_low"], s["det_z_high"]
    zr = alt.Chart(pd.DataFrame({"z": [zl, zh]})).mark_rule(strokeDash=[3, 3], color="#2a78d6").encode(y="z:Q")
    c[1].markdown("**Evidencia del detector: |z| máximo**")
    c[1].altair_chart((base.mark_line(color="#6b7280").encode(y=alt.Y("z_max:Q", scale=alt.Scale(type="symlog"), title="|z| máx.")) + zr + rules)
                      .properties(height=200), width="stretch")

    st.markdown("**Galería de fotogramas** (con las máscaras elegidas)")
    c = st.columns([1, 3])
    k = c[0].number_input("Fotogramas", 2, 8, key="res_gallery_k")
    c[1].multiselect("Vistas", ["cam", "est"] + (["real"] if an.has_truth else []), key="res_views", format_func=VIEW_LABEL.get)
    gviews = [v for v in s["res_views"] if v != "real" or an.has_truth] or ["cam"]
    frames = exports.default_frames(an, int(k))
    fr = exports.Frames(seq, an, truth, pred, s["vid_vmax"])
    for v in gviews:
        cols = st.columns(len(frames))
        for col, i in zip(cols, frames):
            col.image(fr.image(v, i, s["exp_style"], s["exp_opacity"], s["exp_thick"]),
                      caption=f"{VIEW_LABEL[v]} · t = {i / an.fps:.2f} s", width="stretch")
    st.caption("El estilo de las máscaras (contorno, relleno, acierto/error) se elige en la pestaña Exportar.")


# ---------------------------------------------------------------------------
# Pestaña Exportar
# ---------------------------------------------------------------------------
def tab_exportar():
    seq, det = st.session_state.get("seq"), current_det()
    if seq is None or det is None:
        st.info("Genera una secuencia y calcula el detector para poder exportar.")
        return
    an, s = det["an"], st.session_state
    truth, pred, ts, ps = get_masks(det)
    st.markdown("**Imágenes**")
    c = st.columns(4)
    c[0].selectbox("Estilo de las máscaras", list(viewer.STYLES), format_func=viewer.STYLES.get, key="exp_style",
                   help="Contorno: líneas. Relleno: zonas semitransparentes. Acierto/error: verde = acierto, "
                        "naranja = falso positivo, azul = falso negativo.")
    c[1].slider("Opacidad", 0.1, 1.0, step=0.05, key="exp_opacity")
    c[2].select_slider("Grosor del contorno", [1, 2, 3], key="exp_thick")
    c[3].select_slider("Ampliación", [1, 2, 3, 4], key="exp_scale", help="Aumenta el tamaño de las imágenes exportadas.")
    avail = ["cam", "est"] + (["real"] if an.has_truth else [])
    st.multiselect("Vistas a exportar", avail, key="exp_views", format_func=VIEW_LABEL.get)
    default = exports.default_frames(an)
    frames = st.multiselect("Fotogramas a exportar", list(range(an.n)), default=default, key=f"exp_frames_{det['uid']}",
                            format_func=lambda i: f"{i + 1} (t = {i / an.fps:.2f} s)")
    st.markdown("**Qué incluir en el paquete ZIP**")
    c = st.columns(3)
    c[0].checkbox("Informe HTML (resumen + imágenes)", key="exp_report")
    c[0].checkbox("Detección (.npz: máscaras y estimación)", key="exp_npz_det")
    c[0].checkbox("Secuencia simulada (.npz, pesada)", key="exp_npz_seq")
    c[1].checkbox("Vídeo GIF con las máscaras", key="exp_gif")
    c[1].select_slider("GIF: 1 de cada N fotogramas", [1, 2, 3, 4, 6], key="exp_gif_stride")
    c[2].checkbox("Todos los fotogramas en PNG (pesado)", key="exp_all_frames")
    c[2].select_slider("PNG: 1 de cada N fotogramas", [1, 2, 3, 5, 10], key="exp_frames_stride")
    st.caption("Siempre se incluyen: resumen.json, serie_temporal.csv, figuras/series.png, figuras/galeria.png y los "
               "fotogramas elegidos de cada vista.")
    if st.button("Preparar exportación", type="primary", key="btn_export"):
        opts = dict(style=s["exp_style"], opacity=s["exp_opacity"], thick=s["exp_thick"], scale=s["exp_scale"],
                    vmax=s["vid_vmax"], views=s["exp_views"] or ["cam"], frames=frames, report=s["exp_report"],
                    gif=s["exp_gif"], gif_stride=s["exp_gif_stride"], all_frames=s["exp_all_frames"],
                    frames_stride=s["exp_frames_stride"], npz_seq=s["exp_npz_seq"], npz_det=s["exp_npz_det"])
        p = det["params"]
        with st.spinner("Preparando archivos…"):
            df = exports.series_dataframe(an, truth, pred)
            fr = exports.Frames(seq, an, truth, pred, s["vid_vmax"])
            items = {
                "resumen.json": (exports.summary_bytes(an, ts, ps, s["res_window"], p), "application/json"),
                "serie_temporal.csv": (exports.csv_bytes(df), "text/csv"),
                "series.png": (exports.series_figure_bytes(an, df, p["z_low"], p["z_high"]), "image/png"),
                "galeria.png": (exports.gallery_bytes(fr, frames or exports.default_frames(an), opts["views"], opts["style"],
                                                      opts["opacity"], opts["thick"]), "image/png"),
            }
            items["paquete.zip"] = (exports.build_package(seq, an, truth, pred, ts, ps, s["res_window"], p, opts), "application/zip")
        s["exp_items"] = items
    items = s.get("exp_items")
    if items:
        st.success("Listo. Descarga lo que necesites:")
        cols = st.columns(len(items))
        for col, (name, (data, mime)) in zip(cols, items.items()):
            col.download_button(f"⬇ {name}\n({len(data) / 1024:.0f} KB)", data, name, mime, key=f"dl_{name}", width="stretch")
    st.caption("Para guardar un fotograma suelto tal como lo ves, usa «Descargar fotograma (PNG)» bajo el reproductor de la pestaña Vídeo.")


# ---------------------------------------------------------------------------
# Pestaña Benchmark
# ---------------------------------------------------------------------------
METRICS = {"iou_det": "IoU (umbral del detector)", "iou_1x": "IoU a 1× NETD", "iou_2x": "IoU a 2× NETD",
           "iou_3x": "IoU a 3× NETD", "recall_det": "Recall (umbral del detector)",
           "precision_det": "Precisión (umbral del detector)", "fp_free_pct": "% de píxeles sin gas marcados",
           "delay_s": "Retardo de detección (s)", "noise_mk": "Ruido tras el filtrado (mK)"}


def run_benchmark(seeds: int, workers: int):
    cmd = [sys.executable, "benchmark.py", "--out", "bench", "--seeds", str(seeds), "--workers", str(workers)]
    bar, text = st.progress(0.0), st.empty()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        found = re.match(r"\[(\d+)/(\d+)\]", line)
        if found:
            bar.progress(int(found.group(1)) / int(found.group(2)))
            text.caption(line.strip())
    proc.wait()
    return proc.returncode == 0


def tab_benchmark():
    sources = {"Resultados guardados (results/results.csv)": ROOT / "results" / "results.csv"}
    if (ROOT / "bench" / "results.csv").exists():
        sources["Último barrido relanzado (bench/results.csv)"] = ROOT / "bench" / "results.csv"
    name = st.radio("Datos", list(sources), key="bench_source", horizontal=True)
    path = sources[name]
    if not path.exists():
        st.warning(f"No existe {path}.")
        return
    df = pd.read_csv(path)
    c = st.columns(2)
    inp = c[0].radio("Entrada del detector", sorted(df["input"].unique(), reverse=True), horizontal=True, key="bench_input")
    metric = c[1].selectbox("Métrica", list(METRICS), format_func=METRICS.get, key="bench_metric")
    sub = df[df["input"] == inp]
    agg = sub.groupby(["scene", "flow_g_s"], as_index=False)[metric].mean()
    chart = (alt.Chart(agg).mark_line(point=True)
             .encode(x=alt.X("flow_g_s:Q", title="Caudal (g/s)",
                             scale=alt.Scale(type="log", domain=[agg["flow_g_s"].min() / 1.3, agg["flow_g_s"].max() * 1.3])),
                     y=alt.Y(f"{metric}:Q", title=METRICS[metric]),
                     color=alt.Color("scene:N", title="Escena", scale=alt.Scale(scheme="category10")),
                     tooltip=["scene", "flow_g_s", alt.Tooltip(f"{metric}:Q", format=".3f")]))
    st.altair_chart(chart, width="stretch")
    st.dataframe(agg.pivot(index="flow_g_s", columns="scene", values=metric).round(3), width="stretch")
    st.caption(f"Media de las semillas. {len(sub)} secuencias en total. Los huecos son casos sin píxeles reales sobre el umbral.")
    with st.expander("Relanzar el barrido"):
        st.write("Simula 6 caudales × 3 escenas × semillas y mide el detector. Con 2 semillas y 4 procesos tarda unos 2 minutos.")
        c = st.columns(2)
        seeds = c[0].number_input("Semillas", 1, 10, 2, key="bench_seeds")
        workers = c[1].number_input("Procesos", 1, 16, min(4, os.cpu_count() or 1), key="bench_workers")
        if st.button("Lanzar benchmark", key="btn_bench"):
            if run_benchmark(int(seeds), int(workers)):
                st.success("Terminado. Selecciona «Último barrido relanzado» arriba.")
                st.rerun()
            else:
                st.error("El benchmark terminó con error.")


# ---------------------------------------------------------------------------
# Pestaña Comparar umbrales
# ---------------------------------------------------------------------------
def tab_comparar():
    st.write("Simula una escena a varios caudales y dibuja el detector (naranja) frente a la verdad (azul) con el mismo "
             "umbral: a 1× NETD y al umbral propio del detector.")
    import make_figure
    from benchmark import SCENES

    c = st.columns(3)
    scene = c[0].selectbox("Escena", list(SCENES), index=list(SCENES).index("hot_plate_dT5"), key="cmp_scene")
    flows = c[1].multiselect("Caudales (g/s)", FLOW_OPTIONS, default=[0.003, 0.03, 0.3], key="cmp_flows")
    seed = c[2].number_input("Semilla", 0, 1000, 0, key="cmp_seed")
    st.caption("Cada caudal se simula a resolución completa (5 s): unos 10 s por caudal.")
    if st.button("Generar figura", type="primary", key="btn_cmp"):
        if not flows:
            st.warning("Elige al menos un caudal.")
        else:
            with st.spinner("Simulando y detectando…"):
                fig = make_figure.build_figure(scene, sorted(flows), int(seed))
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=110)
                plt.close(fig)
            st.session_state["cmp_png"] = buf.getvalue()
    png = st.session_state.get("cmp_png")
    if png:
        st.image(png, width="stretch")
        st.download_button("Descargar figura (.png)", png, "detector_mismo_umbral.png", key="dl_cmp")


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def tab_guia():
    st.markdown("Qué hace cada ajuste, qué pasa si lo subes y qué valor suele ir bien. **Los valores por defecto están "
                "elegidos para que todo funcione: si no sabes qué hace un ajuste, no lo toques.**")
    c = st.columns([3, 2, 2])
    q = c[0].text_input("Buscar", key="guide_q", placeholder="p. ej. ruido, viento, umbral…").strip().lower()
    lv = c[1].multiselect("Nivel", list(pg.LEVELS), default=list(pg.LEVELS), format_func=pg.LEVELS.get, key="guide_lv")
    pt = c[2].multiselect("Parte", list(pg.PARTS), default=list(pg.PARTS), format_func=pg.PARTS.get, key="guide_pt")
    rows = []
    for name in list(pg.GUIDE) + list(pg.DETECTOR_GUIDE):
        g = pg.guide(name)
        d = _fmt(_default(name)) if name in pg.GUIDE else _fmt(core.DET_PARAM_DEFAULTS.get(name, "—"))
        row = {"Parámetro": g["title"], "Nombre": name, "Nivel": g["level_name"], "Parte": g["part_name"],
               "Qué hace": g["what"], "Si lo subes": g["up"], "Valor típico": g["typical"], "Por defecto": d}
        if g["level"] in lv and g["part"] in pt and (not q or q in " ".join(map(str, row.values())).lower()):
            rows.append(row)
    st.caption(f"{len(rows)} ajustes")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=560)
    with st.expander("Detectores preparados"):
        for n, p in core.DETECTOR_PROFILES.items():
            st.markdown(f"**{n}** — {p['desc']}  \n*Coste:* {p['cost']}")


def main():
    init_state()
    sidebar()
    st.title("Segmentador de gases")
    st.caption("Panel de control: ajusta el vídeo, la simulación y los cálculos; mira el vídeo con las máscaras real y "
               "detectada superpuestas; exporta resultados e imágenes.")
    t1, t2, t3, t4, t5, t6, t7 = st.tabs(["🎬 Vídeo", "📊 Resultados", "📦 Exportar", "📈 Benchmark", "⚖️ Comparar umbrales", "📖 Guía", "🎞️ Vídeo largo"])
    with t1:
        tab_video()
    with t2:
        tab_resultados()
    with t3:
        tab_exportar()
    with t4:
        tab_benchmark()
    with t5:
        tab_comparar()
    with t6:
        tab_guia()
    with t7:
        tab_largo()


main()
