"""
ogi_sim.py - Synthetic LWIR gas-plume sequences for a cold vent (unlit flare).

Fixed scene (background + vent pipe), simulated gas release with known flow
rate. Every sequence comes with exact ground truth:

    u8        (T, H, W) uint8    8-bit grayscale video, fixed level/span (no AGC)
    temp_ck   (T, H, W) uint16   apparent temperature, centi-kelvin ("radiometric")
    cl        (T, H, W) float32  gas column density, ppm*m
    contrast  (T, H, W) float16  noise-free apparent-temperature change due to gas, K
    mask      (T, H, W) bool     |contrast| > mask_thresh_k
    meta      JSON string with every parameter used (including the seed)

Physics, in short
-----------------
Plume    : Gaussian puffs released at the pipe tip. Each puff carries a known
           mass, moves with jet momentum + wind + a kinematic turbulence field
           + buoyancy, and grows as it entrains air. Summing the puffs gives
           the column density map directly (the line-of-sight integral of a
           3-D Gaussian is a 2-D Gaussian).
Radiance : L = tau * L_background + (1 - tau) * L(T_gas), band 8-14 um.
           tau = 1 - band_fraction * (1 - exp(-k_abs * CL)): the gas only
           absorbs in a fraction of the broadband detector response.
Camera   : optical blur, detector lag, temporal noise (NETD), fixed-pattern
           and column noise, slow offset drift, 8-bit quantisation.

IMPORTANT: band_fraction and k_abs are order-of-magnitude placeholders. They
must be calibrated against real captures before any quantitative use.

Parameters
----------
Every tunable number lives in `Config` (see docs/parametros.md, generated with
`--params-md`). Defaults reproduce the original simulator bit for bit.
A run is described by a preset/JSON file plus overrides:

    python ogi_sim.py --list-params                       # every parameter
    python ogi_sim.py --preset hot_plate_dT5 --flow 0.03 --out demo --preview
    python ogi_sim.py --config my_run.json --set turb_rms=0.3 --set duration_s=20
    python ogi_sim.py --out dataset --n 50 --seed 1       # randomised dataset
    python ogi_sim.py --preset cold_sky --dump-config     # print the final JSON

Precedence (low -> high): defaults, --preset, --config, --scene/--flow/--wind/
--duration/--seed, --set. Each sequence is saved with a sidecar <name>.json.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x renamed trapz

PRESET_DIR = Path(__file__).resolve().parent / "configs"

# --------------------------------------------------------------------------
# Gas properties: molar mass (kg/mol) and density at ~20 C (kg/m3)
# --------------------------------------------------------------------------
GASES = {
    "R134a": dict(molar_mass=0.10203, density=4.25),
    "R152a": dict(molar_mass=0.06605, density=2.76),
    "R1234ze": dict(molar_mass=0.11404, density=4.77),
}
SCENES = ("hot_plate", "cold_sky")


def P(default, unit="", doc="", group="", lo=None, hi=None, choices=None):
    """Dataclass field carrying the documentation used by --list-params."""
    return field(default=default, metadata=dict(unit=unit, doc=doc, group=group, lo=lo, hi=hi, choices=choices))


GROUPS = ["Cámara", "Escena", "Gas y fuga", "Chorro y pluma", "Viento y turbulencia", "Salida y ejecución"]


@dataclass
class Config:
    # --- camera -----------------------------------------------------------
    width: int = P(384, "px", "Ancho de la imagen.", "Cámara", 16, 4096)
    height: int = P(288, "px", "Alto de la imagen.", "Cámara", 16, 4096)
    fps: float = P(25.0, "Hz", "Fotogramas por segundo.", "Cámara", 1, 240)
    m_per_px: float = P(0.005, "m", "Tamaño en el plano del objeto de un píxel.", "Cámara", 1e-4, 1.0)
    netd_k: float = P(0.035, "K", "Ruido temporal de un píxel en un fotograma (NETD).", "Cámara", 0.0, 1.0)
    fpn_k: float = P(0.020, "K", "Patrón fijo residual por píxel.", "Cámara", 0.0, 1.0)
    col_noise_k: float = P(0.010, "K", "Patrón fijo residual por columna.", "Cámara", 0.0, 1.0)
    drift_k: float = P(0.030, "K", "Amplitud de la deriva lenta global del nivel.", "Cámara", 0.0, 1.0)
    drift_period_s: float = P(20.0, "s", "Periodo de la deriva senoidal.", "Cámara", 0.1, 3600)
    psf_sigma_px: float = P(0.7, "px", "Desenfoque óptico (σ de la gaussiana).", "Cámara", 0.0, 10)
    detector_tau_s: float = P(0.010, "s", "Constante de tiempo térmica del microbolómetro.", "Cámara", 1e-6, 1.0)
    level_c: float | None = P(None, "°C", "Centro de la ventana de 8 bit. None = automático (y fijo).", "Cámara")
    span_c: float | None = P(None, "K", "Ancho de la ventana de 8 bit. None = automático (y fijo).", "Cámara")
    auto_span_margin_hot_k: float = P(8.0, "K", "Margen que se suma al rango automático en placa caliente.", "Cámara", 0.0, 100)
    auto_span_margin_cold_k: float = P(12.0, "K", "Margen que se suma al rango automático en cielo frío.", "Cámara", 0.0, 100)
    auto_headroom_k: float = P(1.0, "K", "Holgura de la ventana automática respecto al fondo.", "Cámara", 0.0, 50)
    hot_ref_margin_k: float = P(5.0, "K", "Placa caliente: se usan los píxeles a más de (t_bg_c − margen) para fijar la ventana.", "Cámara", 0.0, 100)
    cold_ref_margin_k: float = P(8.0, "K", "Cielo frío: se usan los píxeles a menos de (t_amb_c − margen) para fijar la ventana.", "Cámara", 0.0, 100)
    band_lo_um: float = P(8.0, "µm", "Límite inferior de la banda espectral de la cámara.", "Cámara", 1.0, 30)
    band_hi_um: float = P(14.0, "µm", "Límite superior de la banda espectral de la cámara.", "Cámara", 1.0, 30)
    # --- scene ------------------------------------------------------------
    scene: str = P("hot_plate", "", "hot_plate (el gas absorbe: se ve oscuro) o cold_sky (el gas emite: se ve claro).", "Escena", choices=SCENES)
    t_amb_c: float = P(22.0, "°C", "Temperatura ambiente (también la del tubo y, por defecto, la del gas).", "Escena", -60, 60)
    t_bg_c: float = P(70.0, "°C", "Temperatura de la placa caliente / aparente del cielo.", "Escena", -80, 400)
    tip_x_frac: float = P(0.42, "", "Posición horizontal de la boca del tubo (fracción del ancho).", "Escena", 0.0, 1.0)
    tip_y_frac: float = P(0.70, "", "Posición vertical de la boca del tubo (fracción del alto, desde arriba).", "Escena", 0.0, 1.0)
    pipe_half_px: int = P(3, "px", "Semianchura del tubo.", "Escena", 0, 50)
    pipe_dt_k: float = P(0.5, "K", "Temperatura del tubo sobre la ambiente.", "Escena", -20, 20)
    amb_nonuniformity_k: float = P(0.4, "K", "Falta de uniformidad suave del entorno (placa caliente).", "Escena", 0.0, 10)
    plate_top_frac: float = P(0.06, "", "Borde superior de la placa (fracción del alto).", "Escena", 0.0, 1.0)
    plate_bottom_frac: float = P(0.86, "", "Borde inferior de la placa (fracción del alto).", "Escena", 0.0, 1.0)
    plate_left_frac: float = P(0.08, "", "Borde izquierdo de la placa (fracción del ancho).", "Escena", 0.0, 1.0)
    plate_right_frac: float = P(0.92, "", "Borde derecho de la placa (fracción del ancho).", "Escena", 0.0, 1.0)
    plate_nonuniformity_k: float = P(0.8, "K", "Falta de uniformidad suave de la placa.", "Escena", 0.0, 20)
    plate_gradient_k: float = P(1.5, "K", "Caída de temperatura de la placa de arriba abajo.", "Escena", 0.0, 50)
    sky_gradient_k: float = P(18.0, "K", "Cuánto más frío es el cielo en lo alto que en el horizonte.", "Escena", 0.0, 80)
    sky_nonuniformity_k: float = P(0.6, "K", "Falta de uniformidad suave del cielo.", "Escena", 0.0, 20)
    ground_start_frac: float = P(0.90, "", "Fila donde empieza el suelo (fracción del alto).", "Escena", 0.0, 1.0)
    ground_dt_k: float = P(-1.0, "K", "Temperatura del suelo respecto a la ambiente.", "Escena", -30, 30)
    ground_nonuniformity_k: float = P(0.5, "K", "Falta de uniformidad suave del suelo.", "Escena", 0.0, 20)
    # --- gas and release --------------------------------------------------
    gas: str = P("R134a", "", "Gas liberado (R134a, R152a, R1234ze) o cualquier nombre si se dan masa molar y densidad.", "Gas y fuga")
    gas_molar_mass_kg_mol: float | None = P(None, "kg/mol", "Sustituye la masa molar de la tabla de gases.", "Gas y fuga", 0.001, 1.0)
    gas_density_kg_m3: float | None = P(None, "kg/m³", "Sustituye la densidad de la tabla de gases (~20 °C).", "Gas y fuga", 0.01, 50)
    air_density_kg_m3: float = P(1.20, "kg/m³", "Densidad del aire (flotabilidad).", "Gas y fuga", 0.5, 2.0)
    gravity_m_s2: float = P(9.81, "m/s²", "Gravedad.", "Gas y fuga", 0.0, 30)
    molar_volume_m3_mol: float = P(0.02445, "m³/mol", "Volumen molar a 25 °C y 1 atm (pasa kg/m² a ppm·m).", "Gas y fuga", 0.01, 0.05)
    flow_g_s: float = P(0.10, "g/s", "Caudal másico mientras el venteo está abierto.", "Gas y fuga", 0.0, 1000)
    t_exit_c: float | None = P(None, "°C", "Temperatura del gas al salir. None = ambiente.", "Gas y fuga", -100, 300)
    band_fraction: float = P(0.25, "", "MARCADOR: fracción de la banda de la cámara en que el gas absorbe. Calibrar.", "Gas y fuga", 0.0, 1.0)
    k_abs: float = P(2.0e-3, "1/(ppm·m)", "MARCADOR: coeficiente de absorción. Calibrar.", "Gas y fuga", 0.0, 1.0)
    leak_on_s: float = P(1.0, "s", "Instante en que se abre el venteo.", "Gas y fuga", 0.0, 3600)
    leak_off_s: float | None = P(None, "s", "Instante en que se cierra. None = no se cierra.", "Gas y fuga", 0.0, 3600)
    # --- jet and plume ----------------------------------------------------
    v_exit: float = P(3.0, "m/s", "Velocidad de salida del gas.", "Chorro y pluma", 0.0, 100)
    exit_angle_deg: float = P(0.0, "°", "Ángulo de salida: 0 = vertical, positivo = hacia +x.", "Chorro y pluma", -90, 90)
    nozzle_sigma_m: float = P(0.005, "m", "Tamaño inicial de cada puff.", "Chorro y pluma", 1e-4, 1.0)
    jet_spread: float = P(0.11, "", "Ensanchamiento del chorro: d(σ)/d(recorrido).", "Chorro y pluma", 0.0, 1.0)
    jet_turb: float = P(0.25, "", "Intensidad de turbulencia del chorro (fracción de la velocidad local).", "Chorro y pluma", 0.0, 2.0)
    jet_tau_min_s: float = P(0.01, "s", "Mínimo tiempo de correlación de la turbulencia del chorro.", "Chorro y pluma", 1e-4, 10)
    jet_tau_max_s: float = P(0.5, "s", "Máximo tiempo de correlación de la turbulencia del chorro.", "Chorro y pluma", 1e-4, 10)
    buoy_tau_s: float = P(0.5, "s", "Tiempo de amortiguación de la velocidad de flotabilidad.", "Chorro y pluma", 1e-3, 100)
    entrain_ambient_coeff: float = P(0.10, "", "Crecimiento de los puffs por difusión con el aire (× turb_rms).", "Chorro y pluma", 0.0, 5)
    diffusion_base_m_s: float = P(0.002, "m/s", "Crecimiento base de cada puff.", "Chorro y pluma", 0.0, 1)
    puffs_per_s: float = P(500.0, "1/s", "Puffs emitidos por segundo (resolución de la pluma; más = más lento).", "Chorro y pluma", 10, 20000)
    puff_mass_lognormal_sigma: float = P(0.35, "", "Dispersión de masa entre puffs (lognormal).", "Chorro y pluma", 0.0, 3)
    nozzle_jitter_frac: float = P(0.3, "", "Dispersión de la posición de emisión (× nozzle_sigma_m).", "Chorro y pluma", 0.0, 10)
    substep_s: float = P(0.005, "s", "Paso de integración del movimiento.", "Chorro y pluma", 1e-4, 0.1)
    # --- wind and turbulence ----------------------------------------------
    wind_ms: float = P(0.30, "m/s", "Viento horizontal medio (+x = hacia la derecha).", "Viento y turbulencia", -30, 30)
    wind_vertical_ms: float = P(0.0, "m/s", "Componente vertical media del viento (+ = hacia arriba).", "Viento y turbulencia", -10, 10)
    gust_ms: float = P(0.15, "m/s", "Fluctuación del viento (rms).", "Viento y turbulencia", 0.0, 10)
    gust_tau_s: float = P(2.0, "s", "Tiempo de correlación de las ráfagas.", "Viento y turbulencia", 0.01, 600)
    turb_rms: float = P(0.15, "m/s", "Velocidad rms de la turbulencia ambiente.", "Viento y turbulencia", 0.0, 5)
    turb_n_modes: int = P(60, "", "Número de modos de Fourier de la turbulencia (más = más detalle).", "Viento y turbulencia", 1, 5000)
    turb_lambda_min_m: float = P(0.02, "m", "Longitud de onda mínima de la turbulencia.", "Viento y turbulencia", 1e-3, 10)
    turb_lambda_max_m: float = P(0.8, "m", "Longitud de onda máxima de la turbulencia.", "Viento y turbulencia", 1e-3, 100)
    # --- run --------------------------------------------------------------
    duration_s: float = P(4.0, "s", "Duración de la secuencia.", "Salida y ejecución", 0.1, 3600)
    mask_thresh_k: float = P(0.035, "K", "La máscara de verdad marca |contraste sin ruido| mayor que esto.", "Salida y ejecución", 0.0, 10)
    fov_margin_sigmas: float = P(4.0, "σ", "Los puffs se descartan al salir del campo de visión con este margen.", "Salida y ejecución", 0.0, 20)
    cl_kernel_sigmas: float = P(3.5, "σ", "Radio (en σ) con que se dibuja cada puff en el mapa de columna.", "Salida y ejecución", 1.0, 10)
    store_cl: bool = P(True, "", "Guardar el mapa de densidad de columna (ocupa 4 de cada ~10 bytes por píxel).", "Salida y ejecución")
    store_temp: bool = P(True, "", "Guardar la matriz radiométrica temp_ck.", "Salida y ejecución")
    seed: int = P(0, "", "Semilla del generador aleatorio.", "Salida y ejecución", 0, 2 ** 32 - 1)


# --------------------------------------------------------------------------
# Parameter handling: parsing, validation, documentation
# --------------------------------------------------------------------------
_FIELDS = {f.name: f for f in fields(Config)}


def _coerce(name: str, raw):
    """Convert a JSON value or a command-line string to the field's type."""
    if name not in _FIELDS:
        close = [k for k in _FIELDS if name[:4] in k][:5]
        raise ValueError(f"unknown parameter {name!r}" + (f" (did you mean {', '.join(close)}?)" if close else ""))
    kinds = _FIELDS[name].type.replace(" ", "").split("|")
    v = raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
        except ValueError:
            v = raw
        if isinstance(v, str) and v.lower() in ("none", "null"):
            v = None
    if v is None:
        if "None" not in kinds:
            raise ValueError(f"{name} cannot be None")
        return None
    if "bool" in kinds:
        if not isinstance(v, bool):
            raise ValueError(f"{name} must be true or false, got {raw!r}")
        return v
    if isinstance(v, bool):
        raise ValueError(f"{name} must be a number or text, got {raw!r}")
    if "int" in kinds:
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if not isinstance(v, int):
            raise ValueError(f"{name} must be an integer, got {raw!r}")
        return v
    if "float" in kinds:
        if not isinstance(v, (int, float)):
            raise ValueError(f"{name} must be a number, got {raw!r}")
        return float(v)
    if "str" in kinds:
        if not isinstance(v, str):
            raise ValueError(f"{name} must be text, got {raw!r}")
        return v
    return v


def validate(cfg: Config) -> Config:
    """Raise ValueError listing every inconsistent parameter."""
    bad = []
    for name, f in _FIELDS.items():
        v = getattr(cfg, name)
        if v is None or isinstance(v, (bool, str)):
            continue
        lo, hi = f.metadata.get("lo"), f.metadata.get("hi")
        if lo is not None and v < lo or hi is not None and v > hi:
            bad.append(f"{name}={v} outside [{lo}, {hi}]")
    if cfg.scene not in SCENES:
        bad.append(f"scene={cfg.scene!r} must be one of {SCENES}")
    if cfg.gas not in GASES and (cfg.gas_molar_mass_kg_mol is None or cfg.gas_density_kg_m3 is None):
        bad.append(f"gas={cfg.gas!r} is not in {list(GASES)}: give gas_molar_mass_kg_mol and gas_density_kg_m3")
    if cfg.leak_off_s is not None and cfg.leak_off_s <= cfg.leak_on_s:
        bad.append("leak_off_s must be later than leak_on_s")
    if cfg.leak_on_s >= cfg.duration_s:
        bad.append("leak_on_s must be earlier than duration_s")
    if cfg.band_hi_um <= cfg.band_lo_um:
        bad.append("band_hi_um must be larger than band_lo_um")
    if cfg.plate_bottom_frac <= cfg.plate_top_frac or cfg.plate_right_frac <= cfg.plate_left_frac:
        bad.append("the plate must have positive size (bottom > top, right > left)")
    if cfg.jet_tau_max_s < cfg.jet_tau_min_s:
        bad.append("jet_tau_max_s must be >= jet_tau_min_s")
    if cfg.turb_lambda_max_m <= cfg.turb_lambda_min_m:
        bad.append("turb_lambda_max_m must be larger than turb_lambda_min_m")
    if bad:
        raise ValueError("invalid configuration:\n  " + "\n  ".join(bad))
    return cfg


def half_resolution(cfg: Config) -> Config:
    """Same field of view with half the pixels (about 4 times faster): quick previews."""
    if cfg.width < 128 or cfg.height < 96:
        return cfg
    data = asdict(cfg)
    data.update(width=cfg.width // 2, height=cfg.height // 2, m_per_px=cfg.m_per_px * 2,
                psf_sigma_px=cfg.psf_sigma_px / 2, pipe_half_px=max(1, round(cfg.pipe_half_px / 2)))
    return Config(**data)


def apply_overrides(cfg: Config, overrides: dict) -> Config:
    """Return a validated copy of `cfg` with `overrides` ({name: value}) applied."""
    new = {name: _coerce(name, value) for name, value in overrides.items() if not name.startswith("_")}
    data = asdict(cfg)
    data.update(new)
    return validate(Config(**data))


def load_overrides(path) -> dict:
    """Read a JSON parameter file (keys starting with '_' are comments)."""
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_preset(name: str) -> dict:
    """Overrides of a preset stored in configs/<name>.json."""
    path = PRESET_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in PRESET_DIR.glob("*.json"))) or "none"
        raise ValueError(f"preset {name!r} not found in {PRESET_DIR} (available: {available})")
    return load_overrides(path)


def parse_set(items) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def params_table() -> list[tuple]:
    """(group, name, default, unit, lo, hi, doc) for every parameter, in display order."""
    rows = []
    for g in GROUPS:
        for f in fields(Config):
            if f.metadata["group"] == g:
                rows.append((g, f.name, f.default, f.metadata["unit"], f.metadata["lo"], f.metadata["hi"], f.metadata["doc"]))
    assert len(rows) == len(_FIELDS), "a parameter has no group"
    return rows


def _fmt(v):
    return "None" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def params_text() -> str:
    lines = []
    for g in GROUPS:
        lines.append(f"\n[{g}]")
        for grp, name, default, unit, lo, hi, doc in params_table():
            if grp == g:
                rng = "" if lo is None and hi is None else f" [{_fmt(lo)}..{_fmt(hi)}]"
                lines.append(f"  {name:<28} = {_fmt(default):<10} {unit:<10}{rng}  {doc}")
    return "\n".join(lines).lstrip("\n")


def params_markdown() -> str:
    out = ["# Parámetros del simulador", "",
           "Generado con `python ogi_sim.py --params-md docs/parametros.md`. No editar a mano: "
           "la fuente es `Config` en `ogi_sim.py`.", "",
           "Se cambian con un preset (`--preset`), un fichero JSON (`--config`) o `--set clave=valor`. "
           "Los valores por defecto reproducen el simulador original bit a bit.", ""]
    for g in GROUPS:
        out += [f"## {g}", "", "| Parámetro | Por defecto | Unidad | Rango | Descripción |", "|---|---|---|---|---|"]
        for grp, name, default, unit, lo, hi, doc in params_table():
            if grp == g:
                rng = "" if lo is None and hi is None else f"{_fmt(lo)} … {_fmt(hi)}"
                if name == "scene":
                    rng = " / ".join(SCENES)
                out.append(f"| `{name}` | `{_fmt(default)}` | {unit} | {rng} | {doc} |")
        out.append("")
    out += ["## Constantes que siguen fijas en el código", "",
            "- Forma de la falta de uniformidad suave de la escena (`lowfreq`, senos de frecuencias fijas).",
            "- Percentiles 1 y 99 con que se elige la ventana automática de 8 bit.",
            "- Rango de frecuencias y fases aleatorias de cada modo de turbulencia (±50 % de `k·rms`).",
            "- Rejilla de temperaturas de la tabla de Planck (150–800 K, paso 0,1 K) y 241 longitudes de onda en la banda.",
            "- Modelo de ráfagas (Ornstein–Uhlenbeck solo en la componente horizontal).", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Radiometry: Planck radiance integrated over the camera band
# --------------------------------------------------------------------------
class Band:
    def __init__(self, lo_um: float = 8.0, hi_um: float = 14.0):
        lam = np.linspace(lo_um, hi_um, 241) * 1e-6
        self.t_k = np.arange(150.0, 800.0, 0.1)
        c1, c2 = 1.191042e-16, 1.438777e-2
        spec = c1 / lam[None, :] ** 5 / np.expm1(c2 / (lam[None, :] * self.t_k[:, None]))
        self.rad = _trapz(spec, lam, axis=1)

    def radiance(self, t_c):
        return np.interp(np.asarray(t_c) + 273.15, self.t_k, self.rad)

    def temperature_c(self, radiance):
        return np.interp(radiance, self.rad, self.t_k) - 273.15


# --------------------------------------------------------------------------
# Fixed scene: background temperature map (deg C) and pipe geometry
# --------------------------------------------------------------------------
def build_scene(cfg: Config):
    h, w = cfg.height, cfg.width
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    tip_px = (int(cfg.tip_x_frac * w), int(cfg.tip_y_frac * h))   # (col, row) of the pipe tip
    pipe_half = cfg.pipe_half_px

    # smooth low-frequency non-uniformity shared by both scenes
    lowfreq = (np.sin(xx / w * 2.1 + 0.4) * np.cos(yy / h * 1.7 + 1.1)).astype(np.float32)

    if cfg.scene == "hot_plate":
        scene = np.full((h, w), cfg.t_amb_c, np.float32) + cfg.amb_nonuniformity_k * lowfreq
        r0, r1 = int(cfg.plate_top_frac * h), int(cfg.plate_bottom_frac * h)
        c0, c1 = int(cfg.plate_left_frac * w), int(cfg.plate_right_frac * w)
        plate = cfg.t_bg_c + cfg.plate_nonuniformity_k * lowfreq - cfg.plate_gradient_k * ((yy - r0) / (r1 - r0)) ** 2
        scene[r0:r1, c0:c1] = plate[r0:r1, c0:c1]
    elif cfg.scene == "cold_sky":
        # clear sky gets colder with elevation; ground strip at the bottom
        elev = 1.0 - yy / h
        scene = cfg.t_bg_c - cfg.sky_gradient_k * elev + cfg.sky_nonuniformity_k * lowfreq
        ground = int(cfg.ground_start_frac * h)
        scene[ground:, :] = cfg.t_amb_c + cfg.ground_dt_k + cfg.ground_nonuniformity_k * lowfreq[ground:, :]
    else:
        raise ValueError(f"unknown scene {cfg.scene!r}")

    # vent pipe: opaque, at ambient temperature
    scene[tip_px[1]:, tip_px[0] - pipe_half: tip_px[0] + pipe_half + 1] = cfg.t_amb_c + cfg.pipe_dt_k
    # tip position in metres (x right, y up, origin at bottom-left corner)
    tip_m = np.array([tip_px[0] * cfg.m_per_px, (h - tip_px[1]) * cfg.m_per_px])
    return scene.astype(np.float32), tip_m


# --------------------------------------------------------------------------
# Kinematic turbulence: divergence-free sum of random Fourier modes
# --------------------------------------------------------------------------
class Turbulence:
    def __init__(self, rms: float, rng: np.random.Generator, n_modes: int = 60,
                 lam_min: float = 0.02, lam_max: float = 0.8):
        lam = np.exp(rng.uniform(np.log(lam_min), np.log(lam_max), n_modes))   # wavelengths, m
        k = 2 * np.pi / lam
        theta = rng.uniform(0, 2 * np.pi, n_modes)
        self.kvec = np.stack([k * np.cos(theta), k * np.sin(theta)], 1)
        self.perp = np.stack([-np.sin(theta), np.cos(theta)], 1)
        amp = lam ** (1 / 3)                                            # Kolmogorov scaling
        self.amp = amp * rms / np.sqrt(0.5 * np.sum(amp ** 2) + 1e-12)
        self.omega = k * max(rms, 0.02) * rng.uniform(0.5, 1.5, n_modes)
        self.phase = rng.uniform(0, 2 * np.pi, n_modes)

    def velocity(self, pos: np.ndarray, t: float) -> np.ndarray:
        arg = pos @ self.kvec.T - self.omega * t + self.phase           # (N, M)
        return (np.cos(arg) * self.amp) @ self.perp                     # (N, 2)


# --------------------------------------------------------------------------
# Puff plume
# --------------------------------------------------------------------------
class Plume:
    def __init__(self, cfg: Config, tip_m: np.ndarray, rng: np.random.Generator):
        self.cfg, self.tip, self.rng = cfg, tip_m, rng
        self.puffs_per_s, self.substep_s = cfg.puffs_per_s, cfg.substep_s
        self.turb = Turbulence(cfg.turb_rms, rng, cfg.turb_n_modes, cfg.turb_lambda_min_m, cfg.turb_lambda_max_m)
        gas = GASES.get(cfg.gas, {})
        molar_mass = cfg.gas_molar_mass_kg_mol if cfg.gas_molar_mass_kg_mol is not None else gas["molar_mass"]
        density = cfg.gas_density_kg_m3 if cfg.gas_density_kg_m3 is not None else gas["density"]
        self.kg_to_ppm_m = cfg.molar_volume_m3_mol / molar_mass * 1e6   # kg/m2 -> ppm*m
        self.buoy0 = -cfg.gravity_m_s2 * (density - cfg.air_density_kg_m3) / density   # m/s2, <0 = sinks
        ang = np.deg2rad(cfg.exit_angle_deg)
        self.exit_dir = np.array([np.sin(ang), np.cos(ang)])
        self.t = 0.0
        self.gust = 0.0
        self.pos = np.zeros((0, 2))
        self.sigma = np.zeros(0)
        self.mass = np.zeros(0)
        self.vbuoy = np.zeros(0)
        self.vjt = np.zeros((0, 2))      # jet-turbulence velocity of each puff
        self.emitted_kg = 0.0
        self._carry = 0.0

    def _is_open(self) -> bool:
        c = self.cfg
        return self.t >= c.leak_on_s and (c.leak_off_s is None or self.t < c.leak_off_s)

    def step(self, dt: float):
        c, rng = self.cfg, self.rng
        # wind gust: Ornstein-Uhlenbeck process with correlation time gust_tau_s
        tau_g = c.gust_tau_s
        self.gust += -self.gust / tau_g * dt + c.gust_ms * np.sqrt(2 * dt / tau_g) * rng.standard_normal()
        wind = np.array([c.wind_ms + self.gust, c.wind_vertical_ms])

        # advance existing puffs
        if len(self.sigma):
            ratio = c.nozzle_sigma_m / self.sigma                       # 1 at exit -> 0 when diluted
            v_jet = c.v_exit * ratio[:, None] * self.exit_dir
            ambient = wind + self.turb.velocity(self.pos, self.t)
            self.vbuoy += dt * (self.buoy0 * ratio ** 2 - self.vbuoy / c.buoy_tau_s)
            # shear-generated turbulence of the jet itself: one Ornstein-Uhlenbeck
            # velocity per puff, rms proportional to the local jet speed
            jet_speed = c.v_exit * ratio
            tau_j = np.clip(2.0 * self.sigma / (jet_speed + 1e-3), c.jet_tau_min_s, c.jet_tau_max_s)[:, None]
            rms_j = (c.jet_turb * jet_speed)[:, None]
            self.vjt += (-self.vjt / tau_j * dt
                         + rms_j * np.sqrt(2 * dt / tau_j) * rng.standard_normal(self.vjt.shape))
            vel = v_jet + self.vjt + (1 - ratio[:, None]) * ambient
            vel[:, 1] += self.vbuoy
            self.pos += vel * dt
            # puffs only grow by entrainment (jet) and slow relative diffusion;
            # the large-scale shape comes from the velocity fields above
            self.sigma += dt * (c.jet_spread * jet_speed + c.entrain_ambient_coeff * c.turb_rms * (1 - ratio)
                                + c.diffusion_base_m_s)

        # emit new puffs, spread along the sub-step to avoid beading
        if self._is_open():
            self._carry += self.puffs_per_s * dt
            n = int(self._carry)
            self._carry -= n
            if n:
                frac = rng.uniform(0, 1, n)
                pos = self.tip + frac[:, None] * dt * c.v_exit * self.exit_dir
                pos += rng.normal(0, c.nozzle_jitter_frac * c.nozzle_sigma_m, (n, 2))
                m_total = c.flow_g_s * 1e-3 * n / self.puffs_per_s
                w = rng.lognormal(0.0, c.puff_mass_lognormal_sigma, n)
                mass = m_total * w / w.sum()
                self.pos = np.vstack([self.pos, pos])
                self.sigma = np.concatenate([self.sigma, np.full(n, c.nozzle_sigma_m)])
                self.mass = np.concatenate([self.mass, mass])
                self.vbuoy = np.concatenate([self.vbuoy, np.zeros(n)])
                self.vjt = np.vstack([self.vjt, np.zeros((n, 2))])
                self.emitted_kg += m_total

        # drop puffs that left the field of view (with margin)
        if len(self.sigma):
            wm, hm = c.width * c.m_per_px, c.height * c.m_per_px
            margin = c.fov_margin_sigmas * self.sigma
            keep = ((self.pos[:, 0] > -margin) & (self.pos[:, 0] < wm + margin)
                    & (self.pos[:, 1] > -margin) & (self.pos[:, 1] < hm + margin))
            if not keep.all():
                self.pos, self.sigma = self.pos[keep], self.sigma[keep]
                self.mass, self.vbuoy = self.mass[keep], self.vbuoy[keep]
                self.vjt = self.vjt[keep]
        self.t += dt

    def advance(self, duration: float):
        n = max(1, int(round(duration / self.substep_s)))
        for _ in range(n):
            self.step(duration / n)

    def column_density(self) -> np.ndarray:
        """Column density map in ppm*m (exact sum of Gaussian puffs)."""
        c = self.cfg
        h, w = c.height, c.width
        out = np.zeros((h, w), np.float64)
        px = self.pos[:, 0] / c.m_per_px
        py = h - self.pos[:, 1] / c.m_per_px
        sp = self.sigma / c.m_per_px
        peak = self.mass / (2 * np.pi * self.sigma ** 2) * self.kg_to_ppm_m
        for x, y, s, a in zip(px, py, sp, peak):
            r = int(c.cl_kernel_sigmas * s) + 1
            c0, c1 = max(0, int(x) - r), min(w, int(x) + r + 1)
            r0, r1 = max(0, int(y) - r), min(h, int(y) + r + 1)
            if c0 >= c1 or r0 >= r1:
                continue
            gx = np.exp(-0.5 * ((np.arange(c0, c1) - x) / s) ** 2)
            gy = np.exp(-0.5 * ((np.arange(r0, r1) - y) / s) ** 2)
            out[r0:r1, c0:c1] += a * gy[:, None] * gx[None, :]
        return out


# --------------------------------------------------------------------------
# Camera helpers
# --------------------------------------------------------------------------
def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return img
    r = max(1, int(3 * sigma + 0.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(img, ((0, 0), (r, r)), mode="edge")
    img = sum(k[i] * pad[:, i:i + img.shape[1]] for i in range(2 * r + 1))
    pad = np.pad(img, ((r, r), (0, 0)), mode="edge")
    return sum(k[i] * pad[i:i + img.shape[0], :] for i in range(2 * r + 1))


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def simulate(cfg: Config) -> dict:
    validate(cfg)
    rng = np.random.default_rng(cfg.seed)
    band = Band(cfg.band_lo_um, cfg.band_hi_um)
    scene_c, tip_m = build_scene(cfg)
    plume = Plume(cfg, tip_m, rng)

    n_frames = int(round(cfg.duration_s * cfg.fps))
    h, w = cfg.height, cfg.width
    l_bg = band.radiance(scene_c)
    t_gas_c = cfg.t_amb_c if cfg.t_exit_c is None else cfg.t_exit_c
    l_gas = float(band.radiance(t_gas_c))
    t_bg_cam = band.temperature_c(gaussian_blur(l_bg, cfg.psf_sigma_px))

    # fixed 8-bit window (manual level/span, as with AGC switched off)
    # The window is chosen once from the gas-free background and then frozen.
    if cfg.scene == "hot_plate":                     # gas darkens a hot background
        ref = t_bg_cam[t_bg_cam > cfg.t_bg_c - cfg.hot_ref_margin_k]
        p_lo, p_hi = np.percentile(ref, [1, 99])
        auto_span = (p_hi - p_lo) + cfg.auto_span_margin_hot_k
        auto_level = p_hi + cfg.auto_headroom_k - auto_span / 2      # headroom below for the gas
    else:                                            # gas brightens a cold sky
        ref = t_bg_cam[t_bg_cam < cfg.t_amb_c - cfg.cold_ref_margin_k]
        p_lo, p_hi = np.percentile(ref, [1, 99])
        auto_span = (p_hi - p_lo) + cfg.auto_span_margin_cold_k
        auto_level = p_lo - cfg.auto_headroom_k + auto_span / 2      # headroom above for the gas
    span = float(auto_span if cfg.span_c is None else cfg.span_c)
    level = float(auto_level if cfg.level_c is None else cfg.level_c)
    lo = level - span / 2

    fpn = cfg.fpn_k * rng.standard_normal((h, w)) + cfg.col_noise_k * rng.standard_normal((1, w))
    drift_phase = rng.uniform(0, 2 * np.pi)
    lag = 1.0 - np.exp(-(1.0 / cfg.fps) / max(cfg.detector_tau_s, 1e-6))

    out = dict(
        u8=np.zeros((n_frames, h, w), np.uint8),
        contrast=np.zeros((n_frames, h, w), np.float16),
        mask=np.zeros((n_frames, h, w), bool),
    )
    if cfg.store_temp:
        out["temp_ck"] = np.zeros((n_frames, h, w), np.uint16)
    if cfg.store_cl:
        out["cl"] = np.zeros((n_frames, h, w), np.float32)
    state = t_bg_cam.copy()
    for i in range(n_frames):
        plume.advance(1.0 / cfg.fps)
        cl = plume.column_density()
        tau = 1.0 - cfg.band_fraction * (1.0 - np.exp(-cfg.k_abs * cl))
        l_scene = tau * l_bg + (1.0 - tau) * l_gas
        t_clean = band.temperature_c(gaussian_blur(l_scene, cfg.psf_sigma_px))
        contrast = t_clean - t_bg_cam

        state += lag * (t_clean - state)             # detector thermal lag
        drift = cfg.drift_k * np.sin(2 * np.pi * i / cfg.fps / cfg.drift_period_s + drift_phase)
        t_meas = state + fpn + drift + cfg.netd_k * rng.standard_normal((h, w))

        out["u8"][i] = np.clip((t_meas - lo) / span * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if cfg.store_temp:
            out["temp_ck"][i] = np.clip((t_meas + 273.15) * 100.0 + 0.5, 0, 65535).astype(np.uint16)
        if cfg.store_cl:
            out["cl"][i] = cl
        out["contrast"][i] = contrast
        out["mask"][i] = np.abs(contrast) > cfg.mask_thresh_k

    meta = asdict(cfg)
    meta.update(level_c=level, span_c=span, k_per_dn=span / 255.0, emitted_g=plume.emitted_kg * 1e3,
                ppm_m_per_kg_m2=plume.kg_to_ppm_m)
    out["meta"] = json.dumps(meta)
    return out


# Domain randomisation: (name, low, high, "uniform" | "log"), drawn in this order.
# t_bg_c depends on the scene and is drawn right after t_amb_c.
RANDOM_RANGES = [
    ("t_amb_c", 8, 30, "uniform"),
    ("t_bg_c", None, None, None),                        # hot_plate 40..90, cold_sky -45..-10
    ("flow_g_s", 0.01, 1.0, "log"),
    ("v_exit", 0.8, 6.0, "uniform"),
    ("exit_angle_deg", -20, 20, "uniform"),
    ("wind_ms", -1.2, 1.2, "uniform"),
    ("gust_ms", 0.02, 0.4, "uniform"),
    ("turb_rms", 0.05, 0.35, "uniform"),
    ("leak_on_s", 0.4, 1.6, "uniform"),
    ("netd_k", 0.025, 0.06, "uniform"),
    ("fpn_k", 0.005, 0.04, "uniform"),
]
RANDOM_T_BG = {"hot_plate": (40, 90), "cold_sky": (-45, -10)}


def random_config(rng: np.random.Generator, seed: int) -> Config:
    """Domain randomisation over release, weather, scene and camera noise."""
    scene = str(rng.choice(list(SCENES)))
    gas = None
    values = {}
    for name, lo, hi, kind in RANDOM_RANGES:
        if name == "t_bg_c":
            lo, hi = RANDOM_T_BG[scene]
            kind = "uniform"
        if name == "flow_g_s":
            gas = str(rng.choice(list(GASES)))           # drawn before the flow, as in the original
        if kind == "log":
            values[name] = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
        else:
            values[name] = float(rng.uniform(lo, hi))
    return Config(scene=scene, gas=gas, seed=seed, **values)


def save_sequence(path: Path, seq: dict):
    np.savez_compressed(path, **seq)
    path.with_suffix(".json").write_text(json.dumps(json.loads(seq["meta"]), indent=2))


def save_preview(path: Path, seq: dict, step: int = 2):
    """Animated GIF: 8-bit frame | same frame with the ground-truth mask edge."""
    from PIL import Image
    frames = []
    for u8, m in zip(seq["u8"][::step], seq["mask"][::step]):
        rgb = np.repeat(u8[:, :, None], 3, 2)
        over = rgb.copy()
        edge = m & ~(np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1))
        over[edge] = (255, 60, 60)
        frames.append(Image.fromarray(np.hstack([rgb, over])))
    fps = json.loads(seq["meta"])["fps"] / step
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="out")
    ap.add_argument("--n", type=int, default=0, help="generate N randomised sequences")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--scene", default=None, choices=list(SCENES))
    ap.add_argument("--flow", type=float, default=None, help="g/s")
    ap.add_argument("--wind", type=float, default=None, help="m/s")
    ap.add_argument("--duration", type=float, default=None, help="s")
    ap.add_argument("--preview", action="store_true", help="also write an animated GIF")
    ap.add_argument("--preset", default=None, help="name of a file in configs/ (without .json)")
    ap.add_argument("--config", default=None, help="JSON file with parameter overrides")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="override any parameter (repeatable)")
    ap.add_argument("--dump-config", action="store_true", help="print the final configuration as JSON and exit")
    ap.add_argument("--list-params", action="store_true", help="list every parameter and exit")
    ap.add_argument("--params-md", default=None, metavar="PATH", help="write the parameter reference (Markdown) and exit")
    args = ap.parse_args()

    if args.list_params:
        print(params_text())
        return
    if args.params_md:
        Path(args.params_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.params_md).write_text(params_markdown())
        print("wrote", args.params_md)
        return

    try:
        over: dict = {}
        if args.preset:
            over.update(load_preset(args.preset))
        if args.config:
            over.update(load_overrides(args.config))
        flags = dict(scene=args.scene, flow_g_s=args.flow, wind_ms=args.wind, duration_s=args.duration, seed=args.seed)
        over.update({k: v for k, v in flags.items() if v is not None})
        over.update(parse_set(args.set))
        if args.n:
            over.pop("seed", None)                                  # each randomised sequence has its own seed

        out = Path(args.out)
        if args.n:
            rng = np.random.default_rng(args.seed or 0)
            base = args.seed or 0
            configs = [apply_overrides(random_config(rng, base * 100000 + i), over) for i in range(args.n)]
        else:
            if over.get("scene", "hot_plate") == "cold_sky" and "t_bg_c" not in over:
                over["t_bg_c"] = -25.0                              # sky apparent temperature
            configs = [apply_overrides(Config(), over)]
    except ValueError as err:
        ap.error(str(err))
    if args.dump_config:
        for cfg in configs:
            print(json.dumps(asdict(cfg), indent=2))
        return

    out.mkdir(parents=True, exist_ok=True)
    for i, cfg in enumerate(configs):
        seq = simulate(cfg)
        name = f"seq_{i:04d}_{cfg.scene}"
        save_sequence(out / f"{name}.npz", seq)
        if args.preview:
            save_preview(out / f"{name}.gif", seq)
        meta = json.loads(seq["meta"])
        cl_txt = f", max CL {float(seq['cl'].max()):.0f} ppm*m" if "cl" in seq else ""
        print(f"{name}: {cfg.gas} {cfg.flow_g_s:.3f} g/s, wind {cfg.wind_ms:+.2f} m/s, "
              f"emitted {meta['emitted_g']:.3f} g{cl_txt}, "
              f"mask px (last frame) {int(seq['mask'][-1].sum())}")


if __name__ == "__main__":
    main()
