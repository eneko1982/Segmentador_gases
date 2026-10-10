"""
app.py - Interfaz del simulador y del detector de gases.

    streamlit run app.py

Cuatro pestañas:
    Simulador          todos los parámetros de ogi_sim.py, vista previa y descarga
    Detector           gas_detect.py sobre la última simulación o un .npz, con métricas
    Benchmark          resultados de benchmark.py (y botón para relanzarlo)
    Comparar umbrales  figura detector frente a verdad con el mismo umbral
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
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import ogi_sim as sim  # noqa: E402
from gas_detect import alarm, detect, min_gas_free_s, preview_frames, scores  # noqa: E402

ROOT = Path(__file__).resolve().parent
CONFIG_FIELDS = fields(sim.Config)
OPTIONAL = {f.name for f in CONFIG_FIELDS if "None" in f.type.replace(" ", "").split("|")}
# valor propuesto cuando se activa un parámetro que por defecto es None
NONE_FALLBACK = dict(level_c=25.0, span_c=10.0, t_exit_c=22.0, leak_off_s=3.0,
                     gas_molar_mass_kg_mol=0.10, gas_density_kg_m3=4.0)
FLOW_OPTIONS = [0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
MAIN = ["scene", "gas", "flow_g_s", "wind_ms", "gust_ms", "turb_rms", "t_bg_c", "netd_k", "duration_s", "leak_on_s"]
SCENE_LABEL = {"hot_plate": "Placa caliente (el gas absorbe: se ve oscuro)",
               "cold_sky": "Cielo frío (el gas emite: se ve claro)"}
ORANGE, BLUE = (235, 104, 52), (42, 120, 214)

st.set_page_config(page_title="Segmentador de gases", page_icon="🌫️", layout="wide")


# ---------------------------------------------------------------------------
# Estado de los parámetros
# ---------------------------------------------------------------------------
def set_params(over: dict):
    """Carga en el estado de los widgets una configuración (valores por defecto + `over`)."""
    cfg = sim.apply_overrides(sim.Config(), over)
    for f in CONFIG_FIELDS:
        v = getattr(cfg, f.name)
        if f.name in OPTIONAL:
            st.session_state[f"u_{f.name}"] = v is not None
            v = NONE_FALLBACK[f.name] if v is None else v
        st.session_state[f"p_{f.name}"] = v


def on_preset():
    name = st.session_state["preset"]
    try:
        set_params({} if name == "Por defecto" else sim.load_preset(name))
    except ValueError as err:
        st.session_state["preset_error"] = str(err)


def current_config() -> sim.Config:
    """Configuración construida desde los widgets (lanza ValueError si es incoherente)."""
    over = {}
    for f in CONFIG_FIELDS:
        v = st.session_state[f"p_{f.name}"]
        if f.name in OPTIONAL and not st.session_state[f"u_{f.name}"]:
            v = None
        over[f.name] = v
    return sim.apply_overrides(sim.Config(), over)


def command_line(cfg: sim.Config) -> str:
    """Comando equivalente a los parámetros que difieren del valor por defecto."""
    base = sim.Config()
    parts = []
    for f in CONFIG_FIELDS:
        v, d = getattr(cfg, f.name), getattr(base, f.name)
        if v != d:
            txt = "none" if v is None else (json.dumps(v) if not isinstance(v, str) else v)
            parts.append(f"--set {f.name}={txt}")
    return "python ogi_sim.py --out demo --preview " + " ".join(parts)


def preview_config(cfg: sim.Config) -> sim.Config:
    """Mismo campo de visión con la mitad de píxeles (≈4 veces más rápido)."""
    return sim.half_resolution(cfg)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
def _step(default: float) -> float:
    return 0.01 if not default else 10.0 ** (math.floor(math.log10(abs(default))) - 1)


def widget(f):
    """Widget genérico para un parámetro de Config (según su tipo y su rango)."""
    name, meta, key = f.name, f.metadata, f"p_{f.name}"
    unit = f" ({meta['unit']})" if meta["unit"] else ""
    label, doc = f"{name}{unit}", meta["doc"]
    lo, hi = meta["lo"], meta["hi"]
    if f.type == "bool":
        st.checkbox(label, key=key, help=doc)
    elif meta["choices"]:
        st.selectbox(label, meta["choices"], key=key, help=doc)
    elif name == "gas":
        gas_widget(label, doc)
    else:
        disabled = False
        if name in OPTIONAL:
            disabled = not st.checkbox(f"Definir {name}", key=f"u_{name}", help=doc)
        is_int = "int" in f.type.replace(" ", "").split("|")
        if is_int:
            st.number_input(label, key=key, min_value=int(lo), max_value=int(hi), step=1, help=doc, disabled=disabled)
        else:
            st.number_input(label, key=key, min_value=None if lo is None else float(lo),
                            max_value=None if hi is None else float(hi), step=_step(f.default or 1.0),
                            format="%g", help=doc, disabled=disabled)


def gas_widget(label="gas", doc=""):
    options = list(sim.GASES) + ["personalizado"]
    if st.session_state["p_gas"] not in options:
        options.insert(0, st.session_state["p_gas"])
    st.selectbox(label, options, key="p_gas", help=doc + " «personalizado» exige masa molar y densidad (grupo Gas y fuga).")


def slider(name: str, lo: float, hi: float, step: float, label: str, help: str = ""):
    """Deslizador de rango fijo (si cambiara con otro deslizador, Streamlit lo reiniciaría);
    solo se amplía si un valor cargado, p. ej. de un preset, queda fuera."""
    v = st.session_state[f"p_{name}"]
    st.slider(label, min_value=min(lo, v), max_value=max(hi, v), step=step, key=f"p_{name}", help=help, format="%g")


def main_parameters():
    c1, c2, c3 = st.columns(3)
    with c1:
        st.selectbox("Escena", sim.SCENES, key="p_scene", format_func=SCENE_LABEL.get,
                     help=sim.Config.__dataclass_fields__["scene"].metadata["doc"])
        gas_widget("Gas", "Gas liberado.")
        v = st.session_state["p_flow_g_s"]
        options = sorted(set(FLOW_OPTIONS + [v]))
        st.select_slider("Caudal (g/s)", options=options, key="p_flow_g_s",
                         help="Un bote de aire comprimido suelta del orden de 1 g/s o más; una fuga real pequeña, 0,001–0,01 g/s.")
        st.number_input("Temperatura del fondo (°C)", key="p_t_bg_c", step=1.0, format="%g",
                        min_value=-80.0, max_value=400.0,
                        help="Placa caliente: su temperatura. Cielo frío: temperatura aparente del cielo.")
    with c2:
        slider("wind_ms", -5.0, 5.0, 0.05, "Viento horizontal (m/s, + = hacia la derecha)")
        slider("gust_ms", 0.0, 2.0, 0.01, "Ráfagas (m/s rms)")
        slider("turb_rms", 0.0, 1.0, 0.01, "Turbulencia ambiente (m/s rms)")
    with c3:
        slider("duration_s", 1.0, 60.0, 0.5, "Duración (s)")
        slider("leak_on_s", 0.0, 60.0, 0.1, "La fuga empieza en (s)",
               "Debe ser anterior al final; el detector necesita ~1 s sin gas al principio.")
        slider("netd_k", 0.005, 0.2, 0.005, "Ruido de la cámara NETD (K)",
               "Ruido de un píxel en un fotograma.")


def all_parameters():
    for group in sim.GROUPS:
        with st.expander(group):
            cols = st.columns(3)
            i = 0
            for f in CONFIG_FIELDS:
                if f.metadata["group"] == group and f.name not in MAIN:
                    with cols[i % 3]:
                        widget(f)
                    i += 1


# ---------------------------------------------------------------------------
# Imágenes y cálculos de visualización
# ---------------------------------------------------------------------------
def gif_bytes(frames: list, fps: float, step: int) -> bytes:
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=int(1000 * step / fps), loop=0)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=8)
def make_gif(version, kind, fps, _u8, _pred, _truth):
    """GIF con los contornos; `version` identifica la secuencia para la caché."""
    frames = preview_frames(_u8, _pred, _truth, step=2)
    return gif_bytes(frames, fps, 2)


def colorize(x_mk: np.ndarray, vmax: float) -> np.ndarray:
    """Mapa divergente azul–blanco–rojo (cambio en mK) como imagen RGB."""
    rgba = plt.get_cmap("RdBu_r")(np.clip(x_mk / vmax, -1, 1) * 0.5 + 0.5)
    return (rgba[..., :3] * 255).astype(np.uint8)


def outline(rgb: np.ndarray, mask: np.ndarray, colour) -> np.ndarray:
    from scipy import ndimage as ndi
    out = rgb.copy()
    out[mask & ~ndi.binary_erosion(mask)] = colour
    return out


def gray_rgb(frame: np.ndarray) -> np.ndarray:
    return np.repeat(frame[:, :, None], 3, 2)


def seq_info(seq: dict) -> dict:
    meta = json.loads(str(seq["meta"]))
    return dict(meta=meta, fps=meta["fps"], netd=meta["netd_k"], k_per_dn=meta["k_per_dn"],
                leak_on_s=meta["leak_on_s"])


def npz_bytes(seq: dict) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **seq)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pestaña Simulador
# ---------------------------------------------------------------------------
def tab_simulador():
    st.subheader("Simulador")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.selectbox("Preset", ["Por defecto"] + sorted(p.stem for p in sim.PRESET_DIR.glob("*.json")),
                     key="preset", on_change=on_preset,
                     help="Carga un conjunto de parámetros de la carpeta configs/. Después puedes modificarlos.")
    with c2:
        st.radio("Resolución", ["Rápida (½ resolución)", "Completa"], key="res_mode", horizontal=True,
                 help="La vista rápida usa la mitad de píxeles con el mismo campo de visión: unas 4 veces más rápida.")
    if st.session_state.pop("preset_error", None):
        st.error("No se pudo cargar el preset.")

    main_parameters()
    with st.expander(f"Todos los parámetros ({len(CONFIG_FIELDS)})", expanded=False):
        all_parameters()

    try:
        cfg = current_config()
    except ValueError as err:
        st.error(str(err))
        return
    run_cfg = preview_config(cfg) if st.session_state["res_mode"].startswith("Rápida") else cfg
    st.caption(f"Se simulará a {run_cfg.width}×{run_cfg.height} px, {run_cfg.duration_s:g} s, "
               f"{int(round(run_cfg.duration_s * run_cfg.fps))} fotogramas.")
    with st.expander("Comando equivalente (resolución completa)"):
        st.code(command_line(cfg), language="bash")

    if st.button("Generar secuencia", type="primary", key="btn_sim"):
        with st.spinner("Simulando…"):
            seq = sim.simulate(run_cfg)
        version = st.session_state.get("seq_version", 0) + 1
        st.session_state.update(seq=seq, seq_cfg=run_cfg, seq_version=version)
        st.session_state.pop("det", None)

    seq = st.session_state.get("seq")
    if seq is None:
        st.info("Ajusta los parámetros y pulsa «Generar secuencia».")
        return
    show_sequence(seq, st.session_state["seq_cfg"], st.session_state["seq_version"])


def show_sequence(seq: dict, cfg: sim.Config, version: int):
    info = seq_info(seq)
    mask, contrast = seq["mask"], seq["contrast"]
    m = st.columns(4)
    m[0].metric("Fotogramas", len(seq["u8"]))
    m[1].metric("Gas emitido", f"{info['meta']['emitted_g']:.3f} g")
    m[2].metric("Cambio máximo en un píxel", f"{1e3 * float(np.abs(contrast).max()):.0f} mK")
    m[3].metric("mK por nivel de gris", f"{1e3 * info['k_per_dn']:.0f}")

    c1, c2 = st.columns([1, 1])
    with c1:
        show_truth = st.checkbox("Dibujar el contorno de la verdad (azul)", value=True, key="sim_truth")
        gif = make_gif(("sim", version), show_truth, info["fps"], seq["u8"], np.zeros_like(mask),
                       mask if show_truth else None)
        st.image(gif, caption="Vídeo de 8 bit (lo que daría la cámara)", width="stretch")
    with c2:
        i = st.slider("Fotograma", 0, len(seq["u8"]) - 1, len(seq["u8"]) - 1, key="sim_frame")
        vmax = st.slider("Escala del mapa (± mK)", 10, 500, 100, 10, key="sim_vmax")
        a, b = st.columns(2)
        a.image(gray_rgb(seq["u8"][i]), caption=f"Cámara, t = {i / info['fps']:.2f} s", width="stretch")
        b.image(colorize(contrast[i].astype(np.float32) * 1e3, vmax),
                caption="Cambio causado por el gas (sin ruido), mK", width="stretch")

    t = np.arange(len(mask)) / info["fps"]
    df = pd.DataFrame({"píxeles con gas (verdad)": mask.reshape(len(mask), -1).sum(1)}, index=pd.Index(t, name="t (s)"))
    st.line_chart(df, height=180)

    d1, d2, d3 = st.columns(3)
    d1.download_button("Descargar secuencia (.npz)", npz_bytes(seq), "secuencia.npz", key="dl_npz")
    d2.download_button("Descargar configuración (.json)", json.dumps(json.loads(str(seq["meta"])), indent=2),
                       "secuencia.json", key="dl_json")
    d3.download_button("Descargar vídeo (.gif)", gif, "secuencia.gif", key="dl_gif")
    st.caption("La secuencia queda disponible en la pestaña Detector.")


# ---------------------------------------------------------------------------
# Pestaña Detector
# ---------------------------------------------------------------------------
def load_uploaded(file) -> dict | None:
    try:
        data = np.load(io.BytesIO(file.getvalue()), allow_pickle=False)
        seq = {k: data[k] for k in data.files}
    except Exception as err:  # noqa: BLE001
        st.error(f"No se pudo leer el archivo: {err}")
        return None
    if "u8" not in seq or "meta" not in seq:
        st.error("El .npz debe contener al menos `u8` y `meta` (lo escribe ogi_sim.py).")
        return None
    return seq


def tab_detector():
    st.subheader("Detector")
    options = ["Última secuencia simulada", "Subir un .npz"]
    source = st.radio("Secuencia", options, horizontal=True, key="det_source")
    seq, version = None, None
    if source == options[0]:
        seq, version = st.session_state.get("seq"), ("sim", st.session_state.get("seq_version"))
        if seq is None:
            st.info("Todavía no has generado ninguna secuencia en la pestaña Simulador.")
            return
    else:
        file = st.file_uploader("Archivo .npz de ogi_sim.py", type=["npz"], key="det_file")
        if file is None:
            return
        seq, version = load_uploaded(file), ("file", file.name, file.size)
        if seq is None:
            return

    info = seq_info(seq)
    has_temp = "temp_ck" in seq
    has_truth = "contrast" in seq
    st.markdown("**Parámetros del detector**")
    c = st.columns(6)
    kind = c[0].radio("Entrada", ["u8", "temp"] if has_temp else ["u8"], key="det_input",
                      help="u8 = vídeo de 8 bit; temp = matriz radiométrica.")
    gas_free_s = c[1].number_input("Periodo sin gas (s)", 0.1, 60.0, max(0.1, round(0.8 * info["leak_on_s"], 2)), 0.1,
                                   key=f"det_gas_free_{info['leak_on_s']:g}",
                                   help="Tramo inicial sin gas: se usa para aprender fondo y ruido (debe ser anterior a la fuga).")
    z_low = c[2].number_input("z_low (σ)", 0.5, 10.0, 2.5, 0.1, key="det_zlow")
    z_high = c[3].number_input("z_high (σ)", 0.5, 20.0, 4.5, 0.1, key="det_zhigh")
    sigma_px = c[4].number_input("Suavizado (px)", 0.5, 10.0, 2.0, 0.5, key="det_sigma")
    window_s = c[5].number_input("Promedio temporal (s)", 0.04, 2.0, 0.2, 0.04, key="det_window")
    min_area = st.number_input("Área mínima (px)", 1, 5000, 40, 5, key="det_area")

    if st.button("Ejecutar detector", type="primary", key="btn_det"):
        frames = seq["u8"] if kind == "u8" else seq["temp_ck"].astype(np.float32) / 100.0
        try:
            with st.spinner("Detectando…"):
                res = detect(frames, info["fps"], gas_free_s=gas_free_s, sigma_px=sigma_px, window_s=window_s,
                             z_low=z_low, z_high=z_high, min_area=int(min_area))
        except ValueError as err:
            need = min_gas_free_s(info["fps"], window_s)
            st.error(f"{err}. El detector necesita un tramo inicial sin gas de al menos {need:.2f} s con estos "
                     f"parámetros (ahora: {gas_free_s:.2f} s). Haz que la fuga empiece más tarde o acorta el promedio temporal.")
            return
        st.session_state["det"] = dict(res=res, version=version, kind=kind, z_low=z_low)
    det = st.session_state.get("det")
    if det is None or det["version"] != version:
        st.info("Ajusta los parámetros y pulsa «Ejecutar detector».")
        return
    show_detection(seq, info, det, has_truth)


def show_detection(seq: dict, info: dict, det: dict, has_truth: bool):
    res, kind, z_low = det["res"], det["kind"], det["z_low"]
    fps, netd, on = info["fps"], info["netd"], int(round(info["leak_on_s"] * info["fps"]))
    unit_k = info["k_per_dn"] if kind == "u8" else 1.0
    pred, valid = res["mask"], res["valid"]
    n, last = len(pred), slice(len(pred) - int(info["fps"]), len(pred))
    al = alarm(pred)
    first = np.flatnonzero(al[on:])
    det_thr_k = z_low * res["sigma"] * unit_k

    m = st.columns(4)
    m[0].metric("Ruido tras el filtrado", f"{1e3 * res['sigma'] * unit_k:.1f} mK",
                help=f"Ruido de un solo píxel (NETD): {1e3 * netd:.0f} mK")
    m[1].metric("Retardo de detección", "no detectado" if not len(first) else f"{first[0] / fps:.2f} s")
    m[2].metric("Falsa alarma antes de la fuga", "SÍ" if al[res["n_free"]:on].any() else "no")
    contrast = np.abs(seq["contrast"].astype(np.float32)) if has_truth else None
    if has_truth:
        free = (contrast[last] < 0.002) & valid
        m[3].metric("Píxeles sin gas marcados", f"{100 * (pred[last] & free).sum() / max(free.sum(), 1):.2f} %")

    est = np.abs(res["estimate"]) * unit_k
    if has_truth:
        rows = []
        sc = scores(pred[last], ((contrast > det_thr_k) & valid)[last])
        rows.append(("Máscara del detector frente a la verdad a su umbral", f"{1e3 * det_thr_k:.0f}", sc))
        for mult in (1, 2, 3):
            thr = mult * netd
            sc = scores(((est > thr) & valid)[last], ((contrast > thr) & valid)[last])
            rows.append((f"Estimación frente a la verdad a {mult}× NETD", f"{1e3 * thr:.0f}", sc))
        st.dataframe(pd.DataFrame([dict(Comparación=a, **{"Umbral (mK)": b}, IoU=s["iou"], Recall=s["recall"],
                                        Precisión=s["precision"]) for a, b, s in rows]),
                     hide_index=True, width="stretch")
        choice = st.radio("Umbral con el que se dibujan las dos líneas (naranja = detector, azul = verdad)",
                          [f"El del detector ({1e3 * det_thr_k:.0f} mK)", "1× NETD", "2× NETD", "3× NETD"],
                          horizontal=True, key="det_thr_choice")
        mult = {"1": 1, "2": 2, "3": 3}.get(choice[0])
        if mult is None:
            pred_draw, truth_draw = pred, (contrast > det_thr_k) & valid
        else:
            pred_draw, truth_draw = (est > mult * netd) & valid, (contrast > mult * netd) & valid
    else:
        st.info("Esta secuencia no incluye la verdad (`contrast`): solo se muestra la detección.")
        pred_draw, truth_draw = pred, None

    c1, c2 = st.columns(2)
    with c1:
        gif = make_gif(("det", det["version"], kind, id(res)), choice if has_truth else "", fps,
                       seq["u8"], pred_draw, truth_draw)
        st.image(gif, caption="Naranja: detectado. Azul: verdad con el mismo umbral.", width="stretch")
    with c2:
        i = st.slider("Fotograma", 0, n - 1, n - 1, key="det_frame")
        vmax = st.slider("Escala del mapa (± mK)", 10, 200, 60, 10, key="det_vmax")
        a, b = st.columns(2)
        img = gray_rgb(seq["u8"][i])
        if truth_draw is not None:
            img = outline(img, truth_draw[i], BLUE)
        a.image(outline(img, pred_draw[i], ORANGE), caption=f"t = {i / fps:.2f} s", width="stretch")
        b.image(colorize(res["estimate"][i] * unit_k * 1e3, vmax), caption="Lo que calcula el detector (mK)",
                width="stretch")

    t = pd.Index(np.arange(n) / fps, name="t (s)")
    series = {"píxeles detectados": pred_draw.reshape(n, -1).sum(1)}
    if truth_draw is not None:
        series["píxeles reales"] = truth_draw.reshape(n, -1).sum(1)
    st.line_chart(pd.DataFrame(series, index=t), height=200)
    st.caption(f"La fuga empieza en t = {info['leak_on_s']:.2f} s; el detector aprende el fondo con los "
               f"primeros {res['n_free'] / fps:.2f} s.")


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
            k, total = int(found.group(1)), int(found.group(2))
            bar.progress(k / total)
            text.caption(line.strip())
    proc.wait()
    return proc.returncode == 0


def tab_benchmark():
    st.subheader("Benchmark")
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
    table = agg.pivot(index="flow_g_s", columns="scene", values=metric)
    st.dataframe(table.round(3), width="stretch")
    st.caption(f"Media de las semillas. {len(sub)} secuencias en total. Los huecos son casos en los que no hay "
               "píxeles reales sobre el umbral y la métrica no está definida.")

    with st.expander("Relanzar el barrido"):
        st.write("Simula 6 caudales × 3 escenas × semillas y mide el detector. Con 2 semillas y 4 procesos tarda "
                 "unos 2 minutos; con 1 proceso, bastante más.")
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
    st.subheader("Comparar umbrales")
    st.write("Simula una escena a varios caudales y dibuja el detector (naranja) frente a la verdad (azul) con el "
             "mismo umbral: a 1× NETD y al umbral propio del detector.")
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
    if "p_scene" not in st.session_state:
        set_params({})
    st.title("Segmentador de gases")
    st.caption("Simulador de fugas en imagen térmica (LWIR) y detector clásico, con la verdad de terreno conocida.")
    with st.sidebar:
        st.markdown("### Segmentador de gases")
        st.write("Flujo de trabajo: **Simulador** → **Detector** → **Benchmark** / **Comparar umbrales**.")
        if st.button("Restablecer parámetros", key="btn_reset"):
            st.session_state["preset"] = "Por defecto"
            set_params({})
            st.rerun()
        st.caption("Referencia de parámetros: docs/parametros.md. Los valores `band_fraction` y `k_abs` son "
                   "marcadores: hay que calibrarlos con la cámara real.")
    t1, t2, t3, t4 = st.tabs(["Simulador", "Detector", "Benchmark", "Comparar umbrales"])
    with t1:
        tab_simulador()
    with t2:
        tab_detector()
    with t3:
        tab_benchmark()
    with t4:
        tab_comparar()


main()
