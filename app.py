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

ROOT = Path(__file__).resolve().parent
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
DET_DEFAULTS = dict(det_input="u8", det_auto_gf=True, det_gas_free=1.0, det_zlow=2.5, det_zhigh=4.5,
                    det_sigma=2.0, det_window=0.2, det_area=40)
UI_DEFAULTS = dict(auto_det=True, res_mode="Rápida (½ resolución)", vid_truth_kind="det", vid_truth_mk=35.0,
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
def set_params(over: dict):
    cfg = sim.apply_overrides(sim.Config(), over)
    for f in CONFIG_FIELDS:
        v = getattr(cfg, f.name)
        if f.name in OPTIONAL:
            st.session_state[f"u_{f.name}"] = v is not None
            v = NONE_FALLBACK[f.name] if v is None else v
        st.session_state[f"p_{f.name}"] = v


def init_state():
    if "p_scene" not in st.session_state:
        set_params({})
    for k, v in {**DET_DEFAULTS, **UI_DEFAULTS}.items():
        st.session_state.setdefault(k, v)


def on_preset():
    name = st.session_state["preset"]
    try:
        set_params({} if name == "Por defecto" else sim.load_preset(name))
    except ValueError as err:
        st.session_state["preset_error"] = str(err)


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


def gas_widget(label="Gas", doc=""):
    options = list(sim.GASES) + ["personalizado"]
    if st.session_state["p_gas"] not in options:
        options.insert(0, st.session_state["p_gas"])
    st.selectbox(label, options, key="p_gas", help=(doc or "Gas liberado.") +
                 " «personalizado» exige masa molar y densidad (grupo Gas y fuga, «Más parámetros»).")


def slider(name: str, lo: float, hi: float, step: float, label: str, help: str = ""):
    """Deslizador de rango fijo (si cambiara, Streamlit lo reiniciaría); solo se amplía si un valor cargado queda fuera."""
    v = st.session_state[f"p_{name}"]
    st.slider(label, min_value=min(lo, v), max_value=max(hi, v), step=step, key=f"p_{name}", help=help, format="%g")


def widget(f):
    """Widget genérico para un parámetro de Config (según su tipo y su rango)."""
    name, meta, key = f.name, f.metadata, f"p_{f.name}"
    unit = f" ({meta['unit']})" if meta["unit"] else ""
    label, doc, lo, hi = f"{name}{unit}", meta["doc"], meta["lo"], meta["hi"]
    if f.type == "bool":
        st.checkbox(label, key=key, help=doc)
    elif meta["choices"]:
        st.selectbox(label, meta["choices"], key=key, help=doc, format_func=SCENE_LABEL.get)
    elif name == "gas":
        gas_widget(label, doc)
    else:
        disabled = False
        if name in OPTIONAL:
            disabled = not st.checkbox(f"Definir {name}", key=f"u_{name}", help=doc)
        if "int" in f.type.replace(" ", "").split("|"):
            st.number_input(label, key=key, min_value=int(lo), max_value=int(hi), step=1, help=doc, disabled=disabled)
        else:
            st.number_input(label, key=key, min_value=None if lo is None else float(lo),
                            max_value=None if hi is None else float(hi), step=_step(f.default or 1.0),
                            format="%g", help=doc, disabled=disabled)


def more_parameters(groups, skip):
    for g in groups:
        st.markdown(f"**{g}**")
        for f in CONFIG_FIELDS:
            if f.metadata["group"] == g and f.name not in skip:
                widget(f)


def video_section():
    st.selectbox("Escena", sim.SCENES, key="p_scene", format_func=SCENE_LABEL.get,
                 help=sim.Config.__dataclass_fields__["scene"].metadata["doc"])
    st.number_input("Temperatura del fondo (°C)", key="p_t_bg_c", step=1.0, format="%g", min_value=-80.0,
                    max_value=400.0, help="Placa caliente: su temperatura. Cielo frío: temperatura aparente del cielo.")
    slider("netd_k", 0.005, 0.2, 0.005, "Ruido de la cámara NETD (K)", "Ruido de un píxel en un fotograma.")
    slider("duration_s", 1.0, 60.0, 0.5, "Duración (s)")
    st.radio("Resolución", ["Rápida (½ resolución)", "Completa"], key="res_mode", horizontal=True,
             help="La vista rápida usa la mitad de píxeles con el mismo campo de visión: unas 4 veces más rápida.")
    if st.toggle("Más parámetros de vídeo y cámara", key="more_video"):
        more_parameters(VIDEO_GROUPS, VIDEO_MAIN)


def sim_section():
    gas_widget()
    v = st.session_state["p_flow_g_s"]
    st.select_slider("Caudal (g/s)", options=sorted(set(FLOW_OPTIONS + [v])), key="p_flow_g_s",
                     help="Un bote de aire comprimido suelta del orden de 1 g/s o más; una fuga real pequeña, 0,001–0,01 g/s.")
    slider("leak_on_s", 0.0, 60.0, 0.1, "La fuga empieza en (s)",
           "Debe ser anterior al final; el detector necesita ~1 s sin gas al principio.")
    slider("wind_ms", -5.0, 5.0, 0.05, "Viento horizontal (m/s, + = hacia la derecha)")
    slider("gust_ms", 0.0, 2.0, 0.01, "Ráfagas (m/s rms)")
    slider("turb_rms", 0.0, 1.0, 0.01, "Turbulencia ambiente (m/s rms)")
    if st.toggle("Más parámetros de la simulación del gas", key="more_sim"):
        more_parameters(SIM_GROUPS, SIM_MAIN)


def detector_section():
    st.radio("Entrada", ["u8", "temp"], key="det_input", horizontal=True,
             help="u8 = vídeo de 8 bit; temp = matriz radiométrica (temp_ck).")
    st.checkbox("Periodo sin gas automático (80 % del tiempo antes de la fuga)", key="det_auto_gf")
    st.number_input("Periodo sin gas (s)", 0.1, 60.0, step=0.1, key="det_gas_free", disabled=st.session_state["det_auto_gf"],
                    help="Tramo inicial sin gas: se usa para aprender el fondo y el ruido.")
    c = st.columns(2)
    c[0].number_input("z_low (σ)", 0.5, 10.0, step=0.1, key="det_zlow", help="Umbral bajo de la histéresis.")
    c[1].number_input("z_high (σ)", 0.5, 20.0, step=0.1, key="det_zhigh", help="Umbral alto de la histéresis.")
    c = st.columns(2)
    c[0].number_input("Suavizado (px)", 0.5, 10.0, step=0.5, key="det_sigma", help="σ de la gaussiana espacial.")
    c[1].number_input("Promedio temporal (s)", 0.04, 2.0, step=0.04, key="det_window", format="%g")
    st.number_input("Área mínima (px)", 1, 5000, step=5, key="det_area")


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
    return dict(input=s["det_input"], gas_free_s=round(gf, 3), z_low=s["det_zlow"], z_high=s["det_zhigh"],
                sigma_px=s["det_sigma"], window_s=s["det_window"], min_area=int(s["det_area"]))


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


def sidebar():
    with st.sidebar:
        st.markdown("## 🌫️ Segmentador de gases")
        st.selectbox("Preset", ["Por defecto"] + sorted(p.stem for p in sim.PRESET_DIR.glob("*.json")), key="preset",
                     on_change=on_preset, help="Carga un conjunto de parámetros de la carpeta configs/.")
        if st.session_state.pop("preset_error", None):
            st.error("No se pudo cargar el preset.")
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
        with st.expander("1 · Vídeo y cámara", expanded=True):
            video_section()
        with st.expander("2 · Simulación del gas", expanded=True):
            sim_section()
        with st.expander("3 · Cálculos del detector", expanded=True):
            detector_section()
        with st.expander("Cargar una secuencia .npz"):
            file = st.file_uploader("Archivo de ogi_sim.py", type=["npz"], key="up_file")
            if file is not None and st.button("Usar esta secuencia", key="btn_up"):
                seq = load_uploaded(file)
                if seq is not None:
                    new_sequence(seq)
                    run_detector()
        with st.expander("Comando equivalente (resolución completa)"):
            if ok:
                st.code(command_line(cfg), language="bash")
        if st.button("Restablecer parámetros", key="btn_reset"):
            st.session_state["preset"] = "Por defecto"
            set_params({})
            for k, v in {**DET_DEFAULTS, **UI_DEFAULTS}.items():
                st.session_state[k] = v
            st.rerun()
        st.caption("Referencia de parámetros: docs/parametros.md. `band_fraction` y `k_abs` son marcadores: hay que "
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
    c[1].metric("Gas emitido", f"{info['meta']['emitted_g']:.3f} g")
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
               "en los mapas de mK. El botón «Descargar fotograma» guarda las vistas con las máscaras activas.")


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
    zl, zh = s["det_zlow"], s["det_zhigh"]
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
def main():
    init_state()
    sidebar()
    st.title("Segmentador de gases")
    st.caption("Panel de control: ajusta el vídeo, la simulación y los cálculos; mira el vídeo con las máscaras real y "
               "detectada superpuestas; exporta resultados e imágenes.")
    t1, t2, t3, t4, t5 = st.tabs(["🎬 Vídeo", "📊 Resultados", "📦 Exportar", "📈 Benchmark", "⚖️ Comparar umbrales"])
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


main()
