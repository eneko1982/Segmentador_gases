"""
notebook_ui.py - Interfaz con deslizadores para usar dentro de un cuaderno (Colab o Jupyter).

No necesita ningún servidor web ni túnel: funciona con ipywidgets, que Colab ya incluye.

    import notebook_ui
    notebook_ui.launch()
"""
from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np

import ogi_sim as sim
from gas_detect import alarm, detect, min_gas_free_s, preview_frames, scores

FLOWS = [0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
# (parámetro, mínimo, máximo, paso, etiqueta)
SLIDERS = [("wind_ms", -3.0, 3.0, 0.05, "Viento (m/s, + = derecha)"),
           ("gust_ms", 0.0, 1.0, 0.01, "Ráfagas (m/s)"),
           ("turb_rms", 0.0, 0.6, 0.01, "Turbulencia (m/s)"),
           ("duration_s", 2.0, 20.0, 0.5, "Duración (s)"),
           ("leak_on_s", 0.5, 10.0, 0.1, "La fuga empieza en (s)"),
           ("netd_k", 0.01, 0.15, 0.005, "Ruido NETD (K)"),
           ("t_bg_c", -60.0, 120.0, 1.0, "Temperatura del fondo (°C)")]


def run_once(over: dict, full: bool = False):
    """Simula y detecta. Devuelve (figura, métricas) o (None, mensaje de error)."""
    cfg = sim.apply_overrides(sim.Config(), over)
    if not full:
        cfg = sim.half_resolution(cfg)
    need = min_gas_free_s(cfg.fps)
    if 0.8 * cfg.leak_on_s < need:
        return None, (f"La fuga empieza demasiado pronto: el detector necesita al menos {need / 0.8:.1f} s "
                      f"sin gas al principio (ahora {cfg.leak_on_s:g} s).")
    seq = sim.simulate(cfg)
    meta = json.loads(seq["meta"])
    fps, unit_k = meta["fps"], meta["k_per_dn"]
    res = detect(seq["u8"], fps, gas_free_s=0.8 * cfg.leak_on_s)
    pred, valid = res["mask"], res["valid"]
    n, last, on = len(pred), slice(len(pred) - int(fps), len(pred)), int(round(cfg.leak_on_s * fps))
    contrast = np.abs(seq["contrast"].astype(np.float32))
    thr_k = 2.5 * res["sigma"] * unit_k
    truth = (contrast > thr_k) & valid
    sc = scores(pred[last], truth[last])
    al = alarm(pred)
    first = np.flatnonzero(al[on:])
    metrics = dict(ruido_mk=1e3 * res["sigma"] * unit_k, umbral_mk=1e3 * thr_k, iou=sc["iou"], recall=sc["recall"],
                   precision=sc["precision"], retardo_s=float(first[0] / fps) if len(first) else None,
                   falsa_alarma=bool(al[res["n_free"]:on].any()), gas_g=meta["emitted_g"],
                   max_mk=1e3 * float(contrast.max()))

    fig, ax = plt.subplots(1, 4, figsize=(17, 3.8))
    i = n - 1
    ax[0].imshow(seq["u8"][i], cmap="gray", vmin=0, vmax=255)
    ax[0].set_title("Cámara (último fotograma)")
    ax[1].imshow(res["estimate"][i] * unit_k * 1e3, cmap="RdBu_r", vmin=-60, vmax=60)
    ax[1].set_title("Lo que calcula el detector (mK)")
    ax[2].imshow(preview_frames(seq["u8"], pred, truth, step=n - 1)[-1])
    ax[2].set_title("Naranja: detector · Azul: verdad")
    for a in ax[:3]:
        a.set_xticks([]); a.set_yticks([])
    t = np.arange(n) / fps
    ax[3].plot(t, pred.reshape(n, -1).sum(1), color="#eb6834", label="píxeles detectados")
    ax[3].plot(t, truth.reshape(n, -1).sum(1), color="#2a78d6", label="píxeles reales")
    ax[3].axvline(cfg.leak_on_s, color="gray", ls=":")
    ax[3].set_xlabel("t (s)"); ax[3].legend(); ax[3].set_title("Píxeles con gas")
    fig.tight_layout()
    return fig, metrics


def describe(m: dict) -> str:
    delay = "no detectado" if m["retardo_s"] is None else f"{m['retardo_s']:.2f} s"
    return (f"Gas emitido {m['gas_g']:.3f} g · cambio máximo en un píxel {m['max_mk']:.0f} mK · "
            f"ruido tras el filtrado {m['ruido_mk']:.1f} mK\n"
            f"Retardo de detección: {delay} · falsa alarma antes de la fuga: {'SÍ' if m['falsa_alarma'] else 'no'}\n"
            f"Máscara frente a la verdad a {m['umbral_mk']:.0f} mK: IoU {m['iou']:.2f}, recall {m['recall']:.2f}, "
            f"precisión {m['precision']:.2f}")


def launch():
    """Muestra los controles y el botón «Simular y detectar»."""
    import ipywidgets as w
    from IPython.display import display

    presets = ["Por defecto"] + sorted(p.stem for p in sim.PRESET_DIR.glob("*.json"))
    preset = w.Dropdown(options=presets, value="hot_plate_dT5", description="Preset")
    gas = w.Dropdown(options=list(sim.GASES), value="R134a", description="Gas")
    flow = w.SelectionSlider(options=FLOWS, value=0.03, description="Caudal (g/s)",
                             style={"description_width": "110px"}, layout=w.Layout(width="480px"))
    sliders = {n: w.FloatSlider(min=lo, max=hi, step=st, description=label, continuous_update=False,
                                style={"description_width": "170px"}, layout=w.Layout(width="520px"))
               for n, lo, hi, st, label in SLIDERS}
    full = w.Checkbox(value=False, description="Resolución completa (más lento)")
    button = w.Button(description="Simular y detectar", button_style="primary", icon="play")
    out = w.Output()

    def load_preset(_=None):
        name = preset.value
        cfg = sim.apply_overrides(sim.Config(), {} if name == "Por defecto" else sim.load_preset(name))
        flow.value = min(FLOWS, key=lambda f: abs(f - cfg.flow_g_s))
        gas.value = cfg.gas if cfg.gas in sim.GASES else "R134a"
        for n, s in sliders.items():
            s.value = min(max(getattr(cfg, n), s.min), s.max)

    def overrides() -> dict:
        name = preset.value
        over = {} if name == "Por defecto" else dict(sim.load_preset(name))
        over.update({n: s.value for n, s in sliders.items()})
        over.update(flow_g_s=flow.value, gas=gas.value)
        return over

    def on_click(_):
        with out:
            out.clear_output(wait=True)
            try:
                fig, result = run_once(overrides(), full.value)
            except ValueError as err:
                print("Parámetros incoherentes:", err)
                return
            if fig is None:
                print(result)
                return
            print(describe(result))
            plt.show()

    preset.observe(load_preset, names="value")
    button.on_click(on_click)
    load_preset()
    display(w.VBox([preset, gas, flow, *sliders.values(), full, button, out]))
