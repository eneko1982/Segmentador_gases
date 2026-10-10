"""
notebook_ui.py - Panel de control completo dentro de un cuaderno (Google Colab o Jupyter).

No necesita servidor web ni túnel: usa ipywidgets (Colab ya lo incluye) y el mismo reproductor HTML/JS
del panel web, incrustado en la salida de la celda.

    import notebook_ui
    notebook_ui.launch()

Pestañas:  ⚙️ Ajustes · 🎬 Vídeo · 📊 Resultados · 📦 Exportar
"""
from __future__ import annotations

import base64
import html
import json
import math
import warnings
from dataclasses import fields
from pathlib import Path

import ipywidgets as w
import numpy as np
import pandas as pd
from IPython.display import HTML, Image, clear_output, display

import core
import longvideo
import exports
import param_guide as pg
import ogi_sim as sim
import video_io
import viewer
from analysis import PRED_KINDS, TRUTH_KINDS, WINDOWS

CONFIG_FIELDS = fields(sim.Config)
OPTIONAL = {f.name for f in CONFIG_FIELDS if "None" in f.type.replace(" ", "").split("|")}
NONE_FALLBACK = dict(level_c=25.0, span_c=10.0, t_exit_c=22.0, leak_off_s=3.0,
                     gas_molar_mass_kg_mol=0.10, gas_density_kg_m3=4.0)
FLOWS = [0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
SCENE_LABEL = {"hot_plate": "Placa caliente (el gas absorbe: se ve oscuro)", "cold_sky": "Cielo frío (el gas emite: se ve claro)"}
NO_PRESET = 'Por defecto'
PART_ORDER = [('video', '1 · Vídeo y cámara'), ('escena', '2 · Escena'), ('gas', '3 · Gas y fuga'),
              ('viento', '4 · Viento y turbulencia'), ('perturb', '5 · Perturbaciones del mundo real'), ('salida', '6 · Salida')]
AUTO_LEVEL = 'B'
# (parámetro, mínimo, máximo, paso, etiqueta)
SLIDERS = {"netd_k": (0.005, 0.2, 0.005, "Ruido de la cámara NETD (K)", ".3f"), "duration_s": (1.0, 60.0, 0.5, "Duración (s)", ".1f"),
           "leak_on_s": (0.0, 60.0, 0.1, "La fuga empieza en (s)", ".1f"),
           "wind_ms": (-5.0, 5.0, 0.05, "Viento horizontal (m/s, + = derecha)", ".2f"),
           "gust_ms": (0.0, 2.0, 0.01, "Ráfagas (m/s rms)", ".2f"), "turb_rms": (0.0, 1.0, 0.01, "Turbulencia ambiente (m/s rms)", ".2f")}
STYLE = {"description_width": "46%"}
FULL = w.Layout(width="96%")


def _fmt(v) -> str:
    if v is None:
        return "sin definir"
    if isinstance(v, bool):
        return "sí" if v else "no"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _step(default) -> float:
    return 0.01 if not default else 10.0 ** (math.floor(math.log10(abs(default))) - 1)


def _set_tip(widget, text: str):
    """Ayuda emergente compatible con ipywidgets 7 (Colab) y 8."""
    names = widget.trait_names()
    for attr in ("tooltip", "description_tooltip"):
        if attr in names:
            try:
                setattr(widget, attr, text)
            except Exception:  # noqa: BLE001
                pass
            break
    return widget


class Panel:
    """Todo el panel: ajustes, ejecución, vídeo con máscaras, resultados y exportación."""

    def __init__(self, out_dir: str = "salida"):
        self.out_dir = Path(out_dir)
        self.seq = self.an = self.params = None
        self.version = 0
        self.W: dict[str, w.Widget] = {}        # parámetros del simulador
        self.U: dict[str, w.Checkbox] = {}      # casillas «definir» de los parámetros opcionales
        self._rendering = False
        self._build_top()
        self._build_settings()
        self._build_video()
        self._build_results()
        self._build_export()
        self._build_long()
        self.tabs = w.Tab(children=[self.settings_box, self.video_box, self.results_box, self.export_box, self.guide_box,
                                    self.long_box])
        for i, t in enumerate(["⚙️ Ajustes", "🎬 Vídeo", "📊 Resultados", "📦 Exportar", "📖 Guía", "🎞️ Vídeo largo"]):
            self.tabs.set_title(i, t)
        self.set_params({})
        self._apply_profile()
        self._apply_level()
        self._update_changes()
        self._watch_changes()
        self.root = w.VBox([self.top, self.status, self.tabs])

    # ------------------------------------------------------------------ barra superior
    def _build_top(self):
        self.catalog = core.preset_catalog()
        presets = [(NO_PRESET, NO_PRESET)] + [(v["title"], k) for k, v in self.catalog.items()]
        self.preset = w.Dropdown(options=presets, value=NO_PRESET, description="Escenario", layout=w.Layout(width="420px"))
        self.level = w.ToggleButtons(options=[(v, k) for k, v in pg.LEVELS.items()], value="B", description="Ajustes a ver",
                                     tooltips=["lo que normalmente se toca", "más detalle", "todos los parámetros"])
        self.res = w.ToggleButtons(options=["Rápida (½)", "Completa"], value="Rápida (½)", description="Resolución",
                                   tooltips=["mitad de píxeles: unas 4 veces más rápida", "tamaño completo"])
        self.run_btn = w.Button(description="Simular y calcular", icon="play", button_style="primary",
                                layout=w.Layout(width="190px"))
        self.det_btn = w.Button(description="Recalcular detector", icon="refresh", layout=w.Layout(width="190px"))
        self.auto_det = w.Checkbox(value=True, description="Calcular el detector tras simular", indent=False)
        self.status = w.HTML(self._msg("Ajusta los parámetros y pulsa «Simular y calcular».", "info"))
        self.top = w.VBox([w.HBox([self.preset, self.res]), self.level,
                           w.HBox([self.run_btn, self.det_btn, self.auto_det])])
        self.preset.observe(lambda c: self._load_preset(), names="value")
        self.level.observe(lambda c: self._apply_level(), names="value")
        self.run_btn.on_click(lambda _: self.run_simulation())
        self.det_btn.on_click(lambda _: self.run_detector())

    @staticmethod
    def _msg(text: str, kind: str = "info") -> str:
        colour = {"info": "#1f2937", "ok": "#166534", "warn": "#92400e", "error": "#b91c1c"}[kind]
        return f'<div style="padding:4px 2px;color:{colour}">{html.escape(text)}</div>'

    def _say(self, text, kind="info"):
        self.status.value = self._msg(text, kind)

    # ------------------------------------------------------------------ ajustes
    def _param_widget(self, f):
        name, meta = f.name, f.metadata
        unit = f" ({meta['unit']})" if meta["unit"] else ""
        label, lo, hi = f"{pg.guide(name)['title']}{unit}", meta["lo"], meta["hi"]
        kinds = f.type.replace(" ", "").split("|")
        kw = dict(description=label, style=STYLE, layout=FULL)
        if f.type == "bool":
            return w.Checkbox(value=bool(f.default), **kw)
        if meta["choices"]:
            return w.Dropdown(options=[(SCENE_LABEL.get(c, c), c) for c in meta["choices"]], value=f.default, **kw)
        if name == "gas":
            return w.Dropdown(options=list(sim.GASES) + ["personalizado"], value=f.default, **kw)
        if name in SLIDERS:
            a, b, st_, _text, fmt = SLIDERS[name]
            return w.FloatSlider(value=float(f.default), min=a, max=b, step=st_, description=label, style=STYLE, layout=FULL,
                                 continuous_update=False, readout_format=fmt)
        if name == "flow_g_s":
            return w.SelectionSlider(options=FLOWS, value=0.1, description=label, style=STYLE, layout=FULL,
                                     continuous_update=False)
        if "int" in kinds:
            return w.BoundedIntText(value=int(f.default), min=int(lo), max=int(hi), step=1, **kw)
        default = NONE_FALLBACK.get(name, 1.0) if f.default is None else f.default
        if lo is not None and hi is not None:
            return w.BoundedFloatText(value=float(default), min=float(lo), max=float(hi), step=_step(default), **kw)
        return w.FloatText(value=float(default), step=_step(default), **kw)

    def _make(self, f):
        """Crea el widget de un parámetro (con su casilla «definir» si puede ser None) y lo devuelve ya empaquetado."""
        tip = pg.help_text(f.name, _fmt(getattr(sim.Config(), f.name)))
        widget = _set_tip(self._param_widget(f), tip)
        self.W[f.name] = widget
        if f.name in OPTIONAL:
            chk = _set_tip(w.Checkbox(value=False, description=f"Definir: {pg.guide(f.name)['title']}", indent=False,
                                      layout=FULL), tip)
            widget.disabled = True
            chk.observe(lambda c, wd=widget: setattr(wd, "disabled", not c["new"]), names="value")
            self.U[f.name] = chk
            box = w.VBox([chk, widget])
        else:
            box = w.VBox([widget])
        self.boxes[f.name] = box
        return box

    def _det_widget(self, name, wd):
        self.D[name] = _set_tip(wd, pg.help_text(name, _fmt(core.DET_PARAM_DEFAULTS.get(name))))
        self.dboxes[name] = w.VBox([self.D[name]])        # contenedor propio: así se oculta sin tocar el Layout compartido
        return self.dboxes[name]

    def _build_settings(self):
        self.boxes: dict[str, w.Widget] = {}
        self.dboxes: dict[str, w.Widget] = {}
        self.D: dict[str, w.Widget] = {}
        by = {f.name: f for f in CONFIG_FIELDS}
        h = lambda t: w.HTML(f'<h4 style="margin:6px 0">{t}</h4>')                     # noqa: E731
        cols = {"video": [], "gas": [], "perturb": []}
        place = {"video": "video", "escena": "video", "gas": "gas", "viento": "gas", "perturb": "perturb", "salida": "perturb"}
        self.headers: dict[str, tuple[w.HTML, list[str]]] = {}
        for part, title in PART_ORDER:
            hd = h(title)
            names = [f.name for f in CONFIG_FIELDS if pg.guide(f.name)["part"] == part]
            self.headers[part] = (hd, names)
            cols[place[part]] += [hd] + [self._make(by[n]) for n in names]
        t = lambda n: pg.guide(n)["title"]                                              # noqa: E731
        opt = lambda d: [(v, k) for k, v in d.items()]                                  # noqa: E731
        fl = lambda **k: dict(style=STYLE, layout=FULL, **k)                            # noqa: E731
        dv = core.DET_PARAM_DEFAULTS
        self.D_profile = w.Dropdown(options=list(core.DETECTOR_PROFILES), value="Estable (por defecto)",
                                    description="Qué detector usar", **fl())
        self.profile_info = w.HTML("")
        self.auto_gf = w.Checkbox(value=True, description="Periodo sin gas automático (80 % del tiempo antes de la fuga)",
                                  indent=False, layout=FULL)
        self._det_widget("input", w.Dropdown(options=["u8", "temp"], value="u8", description=t("input"), **fl()))
        self._det_widget("gas_free_s", w.BoundedFloatText(value=1.0, min=0.1, max=60, step=0.1, disabled=True,
                                                          description=t("gas_free_s") + " (s)", **fl()))
        self._det_widget("z_low", w.BoundedFloatText(value=dv["z_low"], min=0.5, max=10, step=0.1, description=t("z_low"), **fl()))
        self._det_widget("z_high", w.BoundedFloatText(value=dv["z_high"], min=0.5, max=20, step=0.1, description=t("z_high"), **fl()))
        self._det_widget("sigma_px", w.BoundedFloatText(value=dv["sigma_px"], min=0.5, max=10, step=0.5,
                                                        description=t("sigma_px") + " (px)", **fl()))
        self._det_widget("window_s", w.BoundedFloatText(value=dv["window_s"], min=0.04, max=2, step=0.04,
                                                        description=t("window_s") + " (s)", **fl()))
        self._det_widget("min_area", w.BoundedIntText(value=dv["min_area"], min=1, max=5000, step=5,
                                                      description=t("min_area") + " (px)", **fl()))
        self._det_widget("background", w.Dropdown(options=opt(dict(static="Fijo (aprendido al principio)", adaptive="Adaptativo")),
                                                  value="static", description=t("background"), **fl()))
        self._det_widget("drift_order", w.Dropdown(options=[("Solo un nivel global", 0), ("Superficie plana inclinada", 1),
                                                            ("Superficie curva", 2)], value=0, description=t("drift_order"), **fl()))
        self._det_widget("stabilize", w.Checkbox(value=False, description=t("stabilize"), indent=False, layout=FULL))
        self._det_widget("polarity", w.Dropdown(options=opt(dict(both="Cualquiera", dark="Solo más oscuro", bright="Solo más claro",
                                                                 auto="Automático según la escena")), value="both",
                                                description=t("polarity"), **fl()))
        self._det_widget("auto_threshold", w.Checkbox(value=False, description=t("auto_threshold"), indent=False, layout=FULL))
        self._det_widget("roi_text", w.Text(value="", placeholder="0.1,0.1; 0.9,0.1; 0.9,0.9", description=t("roi_text"), **fl()))
        self._det_widget("require_source", w.Checkbox(value=False, description=t("require_source"), indent=False, layout=FULL))
        self._det_widget("bg_tau_s", w.BoundedFloatText(value=dv["bg_tau_s"], min=1, max=600, step=1,
                                                        description=t("bg_tau_s") + " (s)", **fl()))
        self._det_widget("source_text", w.Text(value="", description=t("source_text"), **fl()))
        self._det_widget("bg_margin_px", w.BoundedIntText(value=dv["bg_margin_px"], min=0, max=50, description=t("bg_margin_px") + " (px)", **fl()))
        self._det_widget("bg_hold_on_alarm", w.Checkbox(value=True, description=t("bg_hold_on_alarm"), indent=False, layout=FULL))
        self._det_widget("max_shift_px", w.BoundedFloatText(value=dv["max_shift_px"], min=1, max=30, step=1,
                                                            description=t("max_shift_px") + " (px)", **fl()))
        self.D["gas_free_s"].disabled = True
        self.auto_gf.observe(lambda c: setattr(self.D["gas_free_s"], "disabled", c["new"]), names="value")
        self.D_profile.observe(lambda c: self._apply_profile(), names="value")
        det = w.VBox([h("7 · Detector (los cálculos)"), self.D_profile, self.profile_info, self.auto_gf] + list(self.dboxes.values()))
        for c in (self.D["gas_free_s"], ):
            pass
        L = lambda x: w.Layout(width="25%")                                             # noqa: E731
        columns = [w.VBox(cols["video"], layout=L(0)), w.VBox(cols["gas"], layout=L(0)),
                   w.VBox(cols["perturb"], layout=L(0)), w.VBox([det], layout=L(0))]
        # resumen de cambios
        self.changes_html = w.HTML("")
        self.changes_pick = w.Dropdown(options=[], description="Devolver a su valor", style=STYLE, layout=w.Layout(width="420px"))
        self.changes_btn = w.Button(description="Devolver este", layout=w.Layout(width="130px"))
        self.reset_btn = w.Button(description="Todo por defecto", icon="undo", layout=w.Layout(width="150px"))
        self.changes_btn.on_click(lambda _: self._reset_one())
        self.reset_btn.on_click(lambda _: self.reset_all())
        self.changes_box = w.Accordion(children=[w.VBox([self.changes_html, w.HBox([self.changes_pick, self.changes_btn, self.reset_btn])])],
                                       selected_index=None)
        self.changes_box.set_title(0, "Lo que has cambiado (0)")
        self.settings_box = w.VBox([self.changes_box,
                                    w.HBox(columns, layout=w.Layout(align_items="flex-start")),
                                    w.HTML("<small>Guía de cada ajuste: pestaña «Guía» o pasa el ratón por el nombre. "
                                           "<code>band_fraction</code> y <code>k_abs</code> son marcadores: hay que calibrarlos "
                                           "con la cámara real.</small>")])
        self._build_video_loader()
        self.settings_box.children = (*self.settings_box.children, self.vid_box)
        self._build_guide()
        self._watching = False

    # ------------------------------------------------------------------ vídeo real
    def _build_video_loader(self):
        sty = {"description_width": "initial"}
        self.vid_path = w.Text(placeholder="/content/mi_video.mp4  (o sube el archivo con el botón)", description="Archivo",
                               layout=w.Layout(width="520px"))
        self.vid_upload = w.FileUpload(accept=",".join(video_io.EXTENSIONS), multiple=False, description="Subir vídeo")
        self.vid_gf = w.BoundedFloatText(value=3.0, min=0.5, max=120, step=0.5, description="Segundos iniciales sin gas", style=sty)
        self.vid_k = w.BoundedFloatText(value=0.0, min=0.0, max=5.0, step=0.001, description="K por nivel de gris (0 = desconocido)", style=sty)
        self.vid_max = w.BoundedFloatText(value=30.0, min=1, max=600, step=1, description="Máx. segundos", style=sty)
        self.vid_btn = w.Button(description="Usar este vídeo", icon="film", layout=w.Layout(width="180px"))
        self.vid_btn.on_click(lambda _: self.load_real_video())
        self.vid_box = w.Accordion(children=[w.VBox([
            w.HTML("<small>Un vídeo real no tiene máscara verdadera: se ve y se detecta, pero sin IoU ni recall. Si no conoces los K "
                   "por nivel de gris déjalo en 0: el detector trabaja en niveles de gris (los «mK» mostrados serán niveles ×1000).</small>"),
            w.HBox([self.vid_path, self.vid_upload]), w.HBox([self.vid_gf, self.vid_k, self.vid_max]), self.vid_btn])],
            selected_index=None)
        self.vid_box.set_title(0, "Cargar un vídeo real (.mp4, .avi…)")

    def _uploaded_video(self):
        """(nombre, bytes) del archivo subido; vale para ipywidgets 7 (dict) y 8 (tupla)."""
        val = self.vid_upload.value
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not items:
            return None
        it = items[0]
        name = it.get("name") or it.get("metadata", {}).get("name", "video.mp4")
        return name, bytes(it["content"])

    def load_real_video(self):
        import tempfile
        try:
            up = self._uploaded_video()
            kw = dict(gas_free_s=self.vid_gf.value, k_per_dn=self.vid_k.value, max_frames=int(self.vid_max.value * 25))
            self._say("Leyendo el vídeo…", "info")
            if self.vid_path.value.strip():
                seq = video_io.load_video(self.vid_path.value.strip(), **kw)
            elif up:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / Path(up[0]).name
                    path.write_bytes(up[1])
                    seq = video_io.load_video(path, **kw)
            else:
                self._say("Indica la ruta de un vídeo o súbelo con el botón.", "warn")
                return
        except (ValueError, RuntimeError, OSError) as err:
            self._say(str(err), "error")
            return
        self.seq = seq
        self.version += 1
        self.an = self.params = None
        self.auto_gf.value = False
        self.D["gas_free_s"].value = float(self.vid_gf.value)
        self.D["polarity"].value = "both"
        self._clear_outputs()
        self.run_detector()

    def _watch_changes(self):
        if self._watching:
            return
        self._watching = True
        for wd in list(self.W.values()) + list(self.U.values()) + list(self.D.values()) + [self.auto_gf]:
            wd.observe(lambda c: self._update_changes(), names="value")

    # ------------------------------------------------------------------ niveles, perfiles y cambios
    def _apply_level(self):
        order = {"B": 0, "A": 1, "E": 2}[self.level.value]
        sim_vis = set(pg.names_at_level(self.level.value, "sim"))
        det_vis = set(pg.names_at_level(self.level.value, "det"))
        for name, box in self.boxes.items():
            box.layout.display = "" if name in sim_vis else "none"
        for part, (hd, names) in self.headers.items():
            hd.layout.display = "" if any(n in sim_vis for n in names) else "none"
        for name, box in self.dboxes.items():
            box.layout.display = "" if (name in det_vis or name == "gas_free_s") else "none"
        del order

    def _apply_profile(self):
        prof = core.DETECTOR_PROFILES[self.D_profile.value]
        self.profile_info.value = (f"<small>{html.escape(prof['desc'])}<br><b>Coste:</b> {html.escape(prof['cost'])}</small>")
        for k, v in {**core.DET_PARAM_DEFAULTS, **prof["params"]}.items():
            if k in self.D and k != "gas_free_s":
                self.D[k].value = v

    def _update_changes(self):
        try:
            cfg = self.config()
        except ValueError:
            return
        ch = core.changed_from_default(cfg)
        dch = core.changed_detector(self.det_params())
        rows = [f"<li><b>{html.escape(pg.guide(n)['title'])}</b>: {html.escape(_fmt(v))} <small>(por defecto {html.escape(_fmt(d))})</small></li>"
                for n, v, d in ch]
        rows += [f"<li><b>Detector · {html.escape(pg.guide(n)['title'])}</b>: {html.escape(_fmt(v))} "
                 f"<small>(por defecto {html.escape(_fmt(d))})</small></li>" for n, v, d in dch]
        self.changes_html.value = "<ul>" + "".join(rows) + "</ul>" if rows else "Todo está en sus valores por defecto."
        self.changes_box.set_title(0, f"Lo que has cambiado ({len(rows)})")
        cur = self.changes_pick.value
        opts = [(pg.guide(n)["title"], n) for n, _, _ in ch]
        self.changes_pick.options = opts
        if cur in [o[1] for o in opts]:
            self.changes_pick.value = cur

    def _reset_one(self):
        n = self.changes_pick.value
        if n:
            self.set_params({n: getattr(sim.Config(), n)}, only=n)

    def reset_all(self):
        self.set_params({})
        self.D_profile.value = "Estable (por defecto)"
        self._apply_profile()
        self.auto_gf.value = True
        self.preset.value = NO_PRESET

    def _build_guide(self):
        self.guide_q = w.Text(placeholder="Buscar: ruido, viento, umbral…", description="Buscar", layout=w.Layout(width="420px"))
        self.guide_lv = w.ToggleButtons(options=[("Todos", "E")] + [(v, k) for k, v in pg.LEVELS.items() if k != "E"], value="E",
                                        description="Hasta el nivel")
        self.guide_html = w.HTML("")
        self.guide_box = w.VBox([w.HTML("<p>Qué hace cada ajuste, qué pasa si lo subes y qué valor suele ir bien. "
                                        "<b>Los valores por defecto están elegidos para que todo funcione: si no sabes qué hace "
                                        "un ajuste, no lo toques.</b></p>"), w.HBox([self.guide_q, self.guide_lv]), self.guide_html])
        self.guide_q.observe(lambda c: self._render_guide(), names="value")
        self.guide_lv.observe(lambda c: self._render_guide(), names="value")
        self._render_guide()

    def _render_guide(self):
        q, lv = self.guide_q.value.strip().lower(), {"B": 0, "A": 1, "E": 2}[self.guide_lv.value]
        rows = []
        for name in list(pg.GUIDE) + list(pg.DETECTOR_GUIDE):
            g = pg.guide(name)
            d = _fmt(getattr(sim.Config(), name)) if name in pg.GUIDE else _fmt(core.DET_PARAM_DEFAULTS.get(name))
            cells = [g["title"], g["level_name"], g["part_name"], g["what"], g["up"], g["typical"], d]
            if {"B": 0, "A": 1, "E": 2}[g["level"]] <= lv and (not q or q in (" ".join(cells) + name).lower()):
                rows.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
        head = "".join(f"<th>{h}</th>" for h in ["Ajuste", "Nivel", "Parte", "Qué hace", "Si lo subes", "Valor típico", "Por defecto"])
        self.guide_html.value = (f"<div style='max-height:520px;overflow:auto'><small>{len(rows)} ajustes</small>"
                                 f"<table style='font-size:12px;border-collapse:collapse'><tr>{head}</tr>{''.join(rows)}</table></div>")

    def set_params(self, over: dict, only: str | None = None):
        cfg = sim.apply_overrides(sim.Config(), over)
        for f in CONFIG_FIELDS:
            if only and f.name != only:
                continue
            v = getattr(cfg, f.name)
            if f.name in OPTIONAL:
                self.U[f.name].value = v is not None
                v = NONE_FALLBACK[f.name] if v is None else v
            wd = self.W[f.name]
            if isinstance(wd, w.SelectionSlider):
                v = min(wd.options, key=lambda o: abs(o - v))
            elif isinstance(wd, w.FloatSlider):
                v = min(max(v, wd.min), wd.max)
            wd.value = v

    def _load_preset(self):
        name = self.preset.value
        try:
            self.set_params({} if name == NO_PRESET else sim.load_preset(name))
        except ValueError as err:
            self._say(str(err), "error")
            return
        det = self.catalog[name]["detector"] if name in self.catalog else "Estable (por defecto)"
        self.D_profile.value = det
        self._apply_profile()
        self._say(f"Escenario «{self.preset.label}» cargado. Detector sugerido: {det}.", "ok")

    def config(self) -> sim.Config:
        over = {}
        for f in CONFIG_FIELDS:
            v = self.W[f.name].value
            over[f.name] = None if (f.name in OPTIONAL and not self.U[f.name].value) else v
        return sim.apply_overrides(sim.Config(), over)

    def det_params(self) -> dict:
        p = {k: self.D[k].value for k in core.DET_PARAM_DEFAULTS if k != "gas_free_s"}
        p["gas_free_s"] = None if self.auto_gf.value else self.D["gas_free_s"].value
        p["min_area"], p["drift_order"] = int(p["min_area"]), int(p["drift_order"])
        return p

    # ------------------------------------------------------------------ ejecución
    def run_simulation(self):
        try:
            cfg = self.config()
        except ValueError as err:
            self._say(str(err), "error")
            self.tabs.selected_index = 0
            return
        run_cfg = sim.half_resolution(cfg) if self.res.value.startswith("Rápida") else cfg
        self._say(f"Simulando {run_cfg.width}×{run_cfg.height} px, {run_cfg.duration_s:g} s…", "info")
        self.run_btn.disabled = True
        try:
            self.seq = sim.simulate(run_cfg)
        finally:
            self.run_btn.disabled = False
        self.version += 1
        self.an = self.params = None
        self._clear_outputs()
        if self.auto_det.value:
            self.run_detector()
        else:
            self._say("Secuencia simulada. Pulsa «Recalcular detector» para calcular el detector.", "ok")
            self._refresh_all()
            self.tabs.selected_index = 1

    def run_detector(self):
        if self.seq is None:
            self._say("Primero simula una secuencia.", "warn")
            return
        self._say("Calculando el detector…", "info")
        an, msg, p = core.run_detection(self.seq, self.det_params())
        if an is None:
            self.an = None
            self._say(msg, "error")
            self._refresh_all()
            return
        self.an, self.params = an, p
        self._clear_outputs(keep_video=True)
        self._update_mask_labels()
        self._say("Listo." + (f" {msg}" if msg else ""), "ok")
        self._refresh_all()
        self.tabs.selected_index = 1

    def _clear_outputs(self, keep_video=False):
        for o in (self.results_out, self.export_out) + (() if keep_video else (self.video_out,)):
            o.clear_output()

    def _refresh_all(self):
        self.render_video()
        self.render_results()

    # ------------------------------------------------------------------ máscaras elegidas
    def specs(self):
        return ({"kind": self.v_truth.value, "mk": self.v_truth_mk.value},
                {"kind": self.v_pred.value, "mk": self.v_pred_mk.value})

    def masks(self):
        ts, ps = self.specs()
        return self.an.truth(ts), self.an.pred(ps), ts, ps

    def _update_mask_labels(self):
        if self.an is None:
            return
        tl, pl = core.mask_labels(self.an, self.v_truth_mk.value, self.v_pred_mk.value)
        self._rendering = True
        t_old, p_old = self.v_truth.value, self.v_pred.value
        self.v_truth.options = [(tl[k], k) for k in TRUTH_KINDS]
        self.v_pred.options = [(pl[k], k) for k in PRED_KINDS]
        self.v_truth.value, self.v_pred.value = t_old, p_old
        self.v_truth.disabled = not self.an.has_truth
        self._rendering = False

    # ------------------------------------------------------------------ pestaña Vídeo
    def _build_video(self):
        sty = {"description_width": "initial"}
        self.v_views = {k: w.Checkbox(value=k in ("cam", "est"), description=core.VIEW_LABEL[k], indent=False)
                        for k in ("cam", "est", "real")}
        self.v_vmax = w.IntSlider(value=100, min=10, max=500, step=10, description="Escala del mapa (± mK)", style=sty,
                                  continuous_update=False)
        self.v_stride = w.Dropdown(options=[("todos los fotogramas", 1), ("1 de cada 2", 2), ("1 de cada 3", 3), ("1 de cada 4", 4)],
                                   value=2, description="Mostrar", style=sty, layout=w.Layout(width="260px"))
        self.v_zoom = w.IntSlider(value=480, min=250, max=800, step=10, description="Tamaño inicial (px)", style=sty,
                                  continuous_update=False)
        self.v_truth = w.Dropdown(options=[(k, k) for k in TRUTH_KINDS], value="det", description="Máscara real",
                                  style=sty, layout=w.Layout(width="420px"))
        self.v_truth_mk = w.BoundedFloatText(value=35.0, min=0.5, max=2000, step=0.5, description="mK", style=sty,
                                             layout=w.Layout(width="130px"))
        self.v_pred = w.Dropdown(options=[(k, k) for k in PRED_KINDS], value="mask", description="Máscara detectada",
                                 style=sty, layout=w.Layout(width="440px"))
        self.v_pred_mk = w.BoundedFloatText(value=35.0, min=0.5, max=2000, step=0.5, description="mK", style=sty,
                                            layout=w.Layout(width="130px"))
        self.video_out = w.Output()
        self.kpis = w.HTML()
        for wd in (*self.v_views.values(), self.v_vmax, self.v_stride, self.v_zoom, self.v_truth, self.v_pred):
            wd.observe(self._on_video_change, names="value")
        for wd in (self.v_truth_mk, self.v_pred_mk):
            wd.observe(self._on_video_change, names="value")
        self.video_box = w.VBox([
            self.kpis,
            w.HBox([w.HTML("<b>Vistas:</b>&nbsp;"), *self.v_views.values()]),
            w.HBox([self.v_vmax, self.v_stride, self.v_zoom]),
            w.HBox([self.v_truth, self.v_truth_mk]), w.HBox([self.v_pred, self.v_pred_mk]),
            self.video_out,
            w.HTML("<small>Espacio: reproducir/pausar · ←/→: fotograma anterior/siguiente. Rojo = más caliente, azul = más "
                   "frío en los mapas de mK. El botón «Descargar fotograma» está bajo el reproductor.</small>")])

    def _on_video_change(self, change):
        if self._rendering:
            return
        self._update_mask_labels()
        self.render_video()
        self.render_results()

    def _kpis_html(self) -> str:
        if self.seq is None:
            return ""
        meta = json.loads(str(self.seq["meta"]))
        cells = [("Fotogramas", len(self.seq["u8"])), ("Gas emitido", "—" if meta.get("emitted_g") is None else f"{meta['emitted_g']:.3f} g")]
        if "contrast" in self.seq:
            cells.append(("Cambio máximo en un píxel", f"{1e3 * float(np.abs(self.seq['contrast']).max()):.0f} mK"))
        if self.an is not None:
            ts, ps = self.specs()
            sm = self.an.summary(ts, ps, self.r_window.value)
            cells.append(("Retardo de detección", "no detectado" if sm["detection_delay_s"] is None else f"{sm['detection_delay_s']:.2f} s"))
            if "selected_masks" in sm:
                cells.append(("IoU (máscaras elegidas)", f"{sm['selected_masks']['iou']:.2f}"))
        td = "".join(f'<div style="margin-right:28px"><div style="color:#6b7280;font-size:12px">{k}</div>'
                     f'<div style="font-size:22px">{v}</div></div>' for k, v in cells)
        return f'<div style="display:flex;flex-wrap:wrap;margin:6px 0 10px">{td}</div>'

    def render_video(self):
        self.kpis.value = self._kpis_html()
        with self.video_out:
            clear_output(wait=True)
            if self.seq is None:
                print("Todavía no hay ninguna secuencia. Pulsa «Simular y calcular».")
                return
            views = [k for k, c in self.v_views.items() if c.value and (k != "real" or "contrast" in self.seq)
                     and (k != "est" or self.an is not None)]
            if not views:
                print("Elige al menos una vista.")
                return
            if self.an is not None:
                truth, pred, _, _ = self.masks()
            else:
                truth, pred = (self.seq["mask"] if "mask" in self.seq else None), None
            payload = core.build_player_payload(self.seq, self.an, truth, pred, views, self.v_vmax.value, self.v_stride.value)
            with warnings.catch_warnings():                      # IPython recomienda IFrame, pero aquí hace falta srcdoc
                warnings.simplefilter("ignore")
                display(HTML(viewer.player_iframe(payload, self.v_zoom.value)))

    # ------------------------------------------------------------------ pestaña Vídeo largo
    def _build_long(self):
        self.long = None
        self.l_dur = w.BoundedFloatText(value=120.0, min=5, max=7200, step=5, description="Duración (s)",
                                        style={"description_width": "initial"})
        self.l_info = w.HTML("")
        self.l_btn = w.Button(description="Simular vídeo largo", icon="play", button_style="primary", layout=w.Layout(width="220px"))
        self.l_out = w.Output()
        self.l_btn.on_click(lambda _: self.run_long())
        self.l_dur.observe(lambda c: self._long_estimate(), names="value")
        self.long_box = w.VBox([w.HTML("<p>Para vídeos de minutos u horas: se simula y se detecta fotograma a fotograma guardando "
                                       "en disco, así que la memoria no depende de la duración. Usa los ajustes de la pestaña "
                                       "Ajustes; la duración se define aquí.</p>"),
                                w.HBox([self.l_dur, self.l_btn]), self.l_info, self.l_out])

    def _long_cfg(self):
        cfg = sim.apply_overrides(self.config(), dict(duration_s=float(self.l_dur.value)))
        return sim.half_resolution(cfg) if self.res.value.startswith("Rápida") else cfg

    def _long_estimate(self):
        try:
            est = longvideo.disk_estimate(self._long_cfg())
        except ValueError as err:
            self.l_info.value = self._msg(str(err), "error")
            return None
        self.l_info.value = self._msg(f"{est['n_frames']} fotogramas: unos {est['need_gb']:.2f} GB en disco "
                                      f"({est['free_gb']:.1f} GB libres); tiempo orientativo {est['n_frames'] / 15 / 60:.1f} min.",
                                      "info" if est["need_gb"] < est["free_gb"] * 0.9 else "error")
        return est

    def run_long(self):
        est = self._long_estimate()
        if est is None or est["need_gb"] >= est["free_gb"] * 0.9:
            return
        cfg = self._long_cfg()
        det_p = self.det_params()
        det_p["gas_free_s"] = round(0.8 * cfg.leak_on_s, 3) if det_p["gas_free_s"] is None else det_p["gas_free_s"]
        self.l_btn.disabled = True
        try:
            self._say("Simulando el vídeo largo…", "info")
            with self.l_out:
                clear_output(wait=True)
                print("Trabajando… (progreso en la línea de estado)")
            last = [0]

            def prog(phase, i, n):
                if i - last[0] >= max(n // 20, 1) or phase != "simulando":
                    last[0] = i
                    self._say(f"{phase.capitalize()}… {i}/{n} fotogramas", "info")
            self.long = longvideo.run_long(cfg, self.out_dir / "largo", det_p, progress=prog)
        except ValueError as err:
            self._say(str(err), "error")
            return
        finally:
            self.l_btn.disabled = False
        self._say("Vídeo largo listo.", "ok")
        run, sm = self.long, self.long.summary
        with self.l_out:
            clear_output(wait=True)
            sc = sm.get("scores", {}).get("all", {})
            display(HTML(f"<p><b>{sm['duration_s']:.0f} s</b> · retraso {sm['detection_delay_s']} s · falsa alarma antes de la fuga: "
                         f"{'sí' if sm['false_alarm_before_leak'] else 'no'} · IoU global {sc.get('iou', float('nan')):.2f}</p>"))
            payload = run.payload(150)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                display(HTML(viewer.player_iframe(payload, 520)))
            files = run.export(run.path / "resultados")
            display(HTML(f"<p>{html.escape(payload['note'])}. Resultados escritos en <code>{html.escape(str(files[0].parent))}</code>:</p>"))
            display(Image(filename=str(files[2])))

    # ------------------------------------------------------------------ pestaña Resultados
    def _build_results(self):
        sty = {"description_width": "initial"}
        self.r_window = w.Dropdown(options=[(v, k) for k, v in WINDOWS.items()], value="last",
                                   description="Ventana de tiempo para las métricas", style=sty, layout=w.Layout(width="480px"))
        self.r_k = w.BoundedIntText(value=4, min=2, max=8, description="Fotogramas de la galería", style=sty,
                                    layout=w.Layout(width="260px"))
        self.r_views = {k: w.Checkbox(value=k in ("cam", "est"), description=core.VIEW_LABEL[k], indent=False)
                        for k in ("cam", "est", "real")}
        self.results_out = w.Output()
        for wd in (self.r_window, self.r_k, *self.r_views.values()):
            wd.observe(lambda c: self.render_results(), names="value")
        self.results_box = w.VBox([w.HBox([self.r_window, self.r_k]), w.HBox([w.HTML("<b>Galería:</b>&nbsp;"), *self.r_views.values()]),
                                   self.results_out])

    def render_results(self):
        with self.results_out:
            clear_output(wait=True)
            if self.an is None:
                print("Calcula el detector para ver los resultados.")
                return
            an = self.an
            truth, pred, ts, ps = self.masks()
            sm = an.summary(ts, ps, self.r_window.value)
            rows = [("Ruido tras el filtrado", f"{sm['noise_mk']:.1f} mK (un píxel: {sm['pixel_netd_mk']:.0f} mK)"),
                    ("Umbral propio del detector", f"{sm['detector_threshold_mk']:.1f} mK"),
                    ("Retardo de detección", "no detectado" if sm["detection_delay_s"] is None else f"{sm['detection_delay_s']:.2f} s"),
                    ("Falsa alarma antes de la fuga", "SÍ" if sm["false_alarm_before_leak"] else "no")]
            if an.has_truth:
                sel = sm["selected_masks"]
                rows += [("Píxeles sin gas marcados", f"{sm['gas_free_pixels_flagged_pct']:.2f} %"),
                         ("Máscaras comparadas", f"{sel['truth']}  frente a  {sel['detected']}"),
                         ("IoU / Recall / Precisión", f"{sel['iou']:.3f} / {sel['recall']:.3f} / {sel['precision']:.3f}")]
            display(HTML("<table style='border-collapse:collapse'>" + "".join(
                f"<tr><td style='padding:3px 14px 3px 0;color:#6b7280'>{html.escape(k)}</td><td><b>{html.escape(v)}</b></td></tr>"
                for k, v in rows) + "</table>"))
            if an.has_truth:
                display(HTML("<h4>Comparación a distintos umbrales</h4>"))
                display(pd.DataFrame([{"Comparación": r["comparison"], "Umbral (mK)": round(r["threshold_mk"]),
                                       "IoU": round(r["iou"], 4), "Recall": round(r["recall"], 4),
                                       "Precisión": round(r["precision"], 4)} for r in sm["table"]]).style.hide(axis="index"))
            df = exports.series_dataframe(an, truth, pred)
            p = self.params
            display(HTML("<h4>Series temporales</h4>"))
            display(Image(data=exports.series_figure_bytes(an, df, p["z_low"], p["z_high"])))
            display(HTML("<h4>Galería de fotogramas (con las máscaras elegidas)</h4>"))
            fr = exports.Frames(self.seq, an, truth, pred, self.v_vmax.value)
            views = [k for k, c in self.r_views.items() if c.value and (k != "real" or an.has_truth)] or ["cam"]
            display(Image(data=exports.gallery_bytes(fr, exports.default_frames(an, int(self.r_k.value)), views,
                                                     self.e_style.value, self.e_opacity.value, self.e_thick.value)))

    # ------------------------------------------------------------------ pestaña Exportar
    def _build_export(self):
        sty = {"description_width": "initial"}
        self.e_style = w.Dropdown(options=[(v, k) for k, v in viewer.STYLES.items()], value="contour",
                                  description="Estilo de las máscaras", style=sty)
        self.e_opacity = w.FloatSlider(value=0.9, min=0.1, max=1, step=0.05, description="Opacidad", style=sty,
                                       continuous_update=False)
        self.e_thick = w.Dropdown(options=[1, 2, 3], value=1, description="Grosor", style=sty, layout=w.Layout(width="160px"))
        self.e_scale = w.Dropdown(options=[1, 2, 3, 4], value=3, description="Ampliación", style=sty, layout=w.Layout(width="190px"))
        self.e_views = {k: w.Checkbox(value=k in ("cam", "est"), description=core.VIEW_LABEL[k], indent=False)
                        for k in ("cam", "est", "real")}
        self.e_k = w.BoundedIntText(value=4, min=1, max=20, description="Nº de fotogramas (repartidos tras abrirse la fuga)",
                                    style=sty, layout=w.Layout(width="520px"))
        self.e_report = w.Checkbox(value=True, description="Informe HTML (resumen + imágenes)", indent=False)
        self.e_npz_det = w.Checkbox(value=True, description="Detección (.npz: máscaras y estimación)", indent=False)
        self.e_npz_seq = w.Checkbox(value=False, description="Secuencia simulada (.npz, pesada)", indent=False)
        self.e_gif = w.Checkbox(value=True, description="Vídeo GIF con las máscaras", indent=False)
        self.e_gif_stride = w.Dropdown(options=[1, 2, 3, 4, 6], value=2, description="GIF: 1 de cada N", style=sty,
                                       layout=w.Layout(width="190px"))
        self.e_all = w.Checkbox(value=False, description="Todos los fotogramas en PNG (pesado)", indent=False)
        self.e_all_stride = w.Dropdown(options=[1, 2, 3, 5, 10], value=2, description="PNG: 1 de cada N", style=sty,
                                       layout=w.Layout(width="190px"))
        self.e_btn = w.Button(description="Preparar exportación", icon="download", button_style="primary",
                              layout=w.Layout(width="220px"))
        self.e_btn.on_click(lambda _: self.export())
        self.export_out = w.Output()
        self.export_box = w.VBox([
            w.HTML("<b>Imágenes</b>"), w.HBox([self.e_style, self.e_opacity, self.e_thick, self.e_scale]),
            w.HBox([w.HTML("Vistas:&nbsp;"), *self.e_views.values()]), self.e_k,
            w.HTML("<b>Qué incluir en el paquete ZIP</b>"),
            w.HBox([w.VBox([self.e_report, self.e_npz_det, self.e_npz_seq]), w.VBox([self.e_gif, self.e_gif_stride]),
                    w.VBox([self.e_all, self.e_all_stride])]),
            w.HTML("<small>Siempre se incluyen resumen.json, serie_temporal.csv, figuras/series.png, figuras/galeria.png y "
                   "los fotogramas elegidos de cada vista.</small>"), self.e_btn, self.export_out])

    @staticmethod
    def _in_colab() -> bool:
        try:
            import google.colab  # noqa: F401
            return True
        except ImportError:
            return False

    def export(self):
        with self.export_out:
            clear_output(wait=True)
            if self.an is None:
                print("Calcula el detector antes de exportar.")
                return
            an, p = self.an, self.params
            truth, pred, ts, ps = self.masks()
            views = [k for k, c in self.e_views.items() if c.value and (k != "real" or an.has_truth)] or ["cam"]
            frames = exports.default_frames(an, int(self.e_k.value))
            opts = dict(style=self.e_style.value, opacity=self.e_opacity.value, thick=self.e_thick.value,
                        scale=self.e_scale.value, vmax=self.v_vmax.value, views=views, frames=frames,
                        report=self.e_report.value, gif=self.e_gif.value, gif_stride=self.e_gif_stride.value,
                        all_frames=self.e_all.value, frames_stride=self.e_all_stride.value,
                        npz_seq=self.e_npz_seq.value, npz_det=self.e_npz_det.value)
            print("Preparando archivos…")
            df = exports.series_dataframe(an, truth, pred)
            fr = exports.Frames(self.seq, an, truth, pred, self.v_vmax.value)
            win = self.r_window.value
            items = {
                "resumen.json": exports.summary_bytes(an, ts, ps, win, p),
                "serie_temporal.csv": exports.csv_bytes(df),
                "series.png": exports.series_figure_bytes(an, df, p["z_low"], p["z_high"]),
                "galeria.png": exports.gallery_bytes(fr, frames, views, opts["style"], opts["opacity"], opts["thick"]),
                "paquete.zip": exports.build_package(self.seq, an, truth, pred, ts, ps, win, p, opts),
            }
            self.out_dir.mkdir(parents=True, exist_ok=True)
            clear_output(wait=True)
            colab = self._in_colab()
            display(HTML(f"<p>Listo. Los archivos están en la carpeta <code>{html.escape(str(self.out_dir))}/</code>"
                         + (" (panel de archivos de la izquierda en Colab, o los botones de abajo)." if colab else ".") + "</p>"))
            for name, data in items.items():
                path = self.out_dir / name
                path.write_bytes(data)
                label = f"{name} ({len(data) / 1024:.0f} KB)"
                if colab:
                    btn = w.Button(description=f"⬇ {label}", layout=w.Layout(width="320px"))
                    btn.on_click(lambda _, pth=str(path): self._colab_download(pth))
                    display(btn)
                else:
                    uri = "data:application/octet-stream;base64," + base64.b64encode(data).decode()
                    display(HTML(f'<div><a download="{name}" href="{uri}">⬇ {html.escape(label)}</a></div>'))

    def _colab_download(self, path: str):
        with self.export_out:
            from google.colab import files
            files.download(path)

    def show(self):
        display(self.root)
        self.render_video()
        self.render_results()
        return self


def launch() -> Panel:
    """Muestra el panel completo y lo devuelve (por si quieres manejarlo desde código)."""
    return Panel().show()
