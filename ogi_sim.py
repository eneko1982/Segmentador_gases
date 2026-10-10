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


GROUPS = ["Cámara", "Escena", "Gas y fuga", "Chorro y pluma", "Viento y turbulencia", "Perturbaciones del mundo real",
          "Salida y ejecución"]


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
    # --- real-world perturbations (all off by default) --------------------
    bg_change_k: float = P(0.0, "K", "Cambio lento del fondo (sol, equipos que se calientan): amplitud. 0 = sin cambio.", "Perturbaciones del mundo real", -20, 20)
    bg_change_period_s: float = P(20.0, "s", "Periodo del cambio del fondo (senoidal) o duración de la rampa.", "Perturbaciones del mundo real", 0.5, 3600)
    bg_change_shape: str = P("sine", "", "Forma del cambio del fondo: sine (sube y baja) o ramp (sube y se queda).", "Perturbaciones del mundo real", choices=("sine", "ramp"))
    bg_change_spatial: float = P(0.5, "", "0 = el fondo cambia igual en toda la imagen; 1 = cambia más en un lado (más difícil).", "Perturbaciones del mundo real", 0.0, 1.0)
    cloud_k: float = P(0.0, "K", "Nube o sombra que cruza la escena: cambio de temperatura aparente en su centro (negativo = más fría). 0 = sin nube.", "Perturbaciones del mundo real", -30, 30)
    cloud_size_m: float = P(0.5, "m", "Tamaño de la nube o sombra.", "Perturbaciones del mundo real", 0.05, 20)
    cloud_speed_ms: float = P(0.3, "m/s", "Velocidad de la nube (hacia la derecha).", "Perturbaciones del mundo real", 0.0, 20)
    cloud_start_s: float = P(0.0, "s", "Instante en que la nube empieza a entrar por la izquierda.", "Perturbaciones del mundo real", 0.0, 3600)
    object_count: int = P(0, "", "Objetos que cruzan la imagen y NO son gas (personas, pájaros, vehículos). 0 = ninguno.", "Perturbaciones del mundo real", 0, 50)
    object_size_m: float = P(0.15, "m", "Tamaño del objeto (ancho; el alto es el doble).", "Perturbaciones del mundo real", 0.02, 5)
    object_speed_ms: float = P(0.6, "m/s", "Velocidad del objeto (de izquierda a derecha).", "Perturbaciones del mundo real", 0.0, 30)
    object_temp_c: float = P(32.0, "°C", "Temperatura aparente del objeto (opaco, delante del gas).", "Perturbaciones del mundo real", -60, 400)
    object_start_s: float = P(0.5, "s", "Instante en que entra el primer objeto.", "Perturbaciones del mundo real", 0.0, 3600)
    object_row_frac: float = P(-1.0, "", "Altura por la que cruza el objeto (0 = abajo, 1 = arriba). -1 = al azar.", "Perturbaciones del mundo real", -1.0, 1.0)
    object_gap_s: float = P(1.5, "s", "Tiempo entre la entrada de un objeto y el siguiente.", "Perturbaciones del mundo real", 0.0, 3600)
    nuc_period_s: float = P(0.0, "s", "Cada cuánto se cierra el obturador de la cámara (corrección de no uniformidad). 0 = nunca.", "Perturbaciones del mundo real", 0.0, 3600)
    nuc_step_k: float = P(0.03, "K", "Salto aleatorio del nivel global en cada obturación (rms).", "Perturbaciones del mundo real", 0.0, 1.0)
    nuc_fpn_change: float = P(0.3, "", "Fracción del patrón fijo que cambia en cada obturación.", "Perturbaciones del mundo real", 0.0, 1.0)
    nuc_freeze_frames: int = P(2, "", "Fotogramas repetidos (imagen congelada) mientras actúa el obturador.", "Perturbaciones del mundo real", 0, 50)
    vibration_px: float = P(0.0, "px", "Vibración del soporte: desplazamiento rms de toda la imagen. 0 = sin vibración.", "Perturbaciones del mundo real", 0.0, 20)
    vibration_tau_s: float = P(0.3, "s", "Tiempo de correlación de la vibración.", "Perturbaciones del mundo real", 0.01, 60)
    compression_quality: int = P(0, "", "Compresión de vídeo tipo JPEG sobre el vídeo de 8 bit (1 = muy fuerte, 100 = casi nada). 0 = sin compresión.", "Perturbaciones del mundo real", 0, 100)
    bad_pixel_frac: float = P(0.0, "", "Fracción de píxeles defectuosos (fijos en 0 o en 255).", "Perturbaciones del mundo real", 0.0, 0.05)
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
    for name, f in _FIELDS.items():
        choices = f.metadata.get("choices")
        if choices and getattr(cfg, name) not in choices:
            bad.append(f"{name}={getattr(cfg, name)!r} must be one of {tuple(choices)}")
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
    import param_guide as pg
    out = ["# Parámetros del simulador", "",
           "Generado con `python ogi_sim.py --params-md docs/parametros.md`. No editar a mano: "
           "la fuente es `Config` en `ogi_sim.py`.", "",
           "Se cambian con un preset (`--preset`), un fichero JSON (`--config`) o `--set clave=valor`. "
           "Los valores por defecto reproducen el simulador original bit a bit.", "",
           "**Si no sabes qué hace un parámetro, no lo toques**: los valores por defecto están elegidos para que todo "
           "funcione. Nivel: **B** básico (lo que normalmente se toca), **A** avanzado, **E** experto. "
           "El panel muestra solo el nivel que elijas; la guía completa también está en su pestaña «Guía».", ""]
    for g in GROUPS:
        out += [f"## {g}", "", "| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |",
                "|---|---|---|---|---|---|---|---|"]
        for grp, name, default, unit, lo, hi, doc in params_table():
            if grp == g:
                rng = "" if lo is None and hi is None else f"{_fmt(lo)} … {_fmt(hi)}"
                if name == "scene":
                    rng = " / ".join(SCENES)
                e = pg.guide(name)
                out.append(f"| `{name}` — {e['title']} | {e['level']} | {e['what']} | {e['up']} | {e['typical']} | "
                           f"`{_fmt(default)}` | {unit} | {rng} |")
        out.append("")
    out += ["## Opciones del detector", "", "| Opción | Nivel | Qué es | Si la subes | Valor típico |", "|---|---|---|---|---|"]
    for name in pg.DETECTOR_GUIDE:
        e = pg.guide(name)
        out.append(f"| `{name}` — {e['title']} | {e['level']} | {e['what']} | {e['up']} | {e['typical']} |")
    out += ["", "## Constantes que siguen fijas en el código", "",
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
# --------------------------------------------------------------------------
# Real-world perturbations: everything that makes a video look real besides the gas
# --------------------------------------------------------------------------
_JPEG_Q = np.array([[16, 11, 10, 16, 24, 40, 51, 61], [12, 12, 14, 19, 26, 58, 60, 55],
                    [14, 13, 16, 24, 40, 57, 69, 56], [14, 17, 22, 29, 51, 87, 80, 62],
                    [18, 22, 37, 56, 68, 109, 103, 77], [24, 35, 55, 64, 81, 104, 113, 92],
                    [49, 64, 78, 87, 103, 121, 120, 101], [72, 92, 95, 98, 112, 100, 103, 99]], np.float64)


def jpeg_like(frame: np.ndarray, quality: int) -> np.ndarray:
    """Block-DCT quantisation of an 8-bit frame (JPEG-like): approximates the loss of a compressed video stream.

    quality 1 = very strong, 100 = almost lossless. Real H.264/H.265 also uses motion prediction; this keeps the
    main effect (small contrasts and noise flattened, 8x8 block artefacts)."""
    from scipy import fft
    h, w = frame.shape
    scale = 5000.0 / quality if quality < 50 else 200.0 - 2.0 * quality
    q = np.clip(np.floor((_JPEG_Q * scale + 50.0) / 100.0), 1, 255)
    ph, pw = (-h) % 8, (-w) % 8
    img = np.pad(frame.astype(np.float64) - 128.0, ((0, ph), (0, pw)), mode="edge")
    blocks = img.reshape((h + ph) // 8, 8, (w + pw) // 8, 8).transpose(0, 2, 1, 3)
    coef = np.round(fft.dctn(blocks, axes=(2, 3), norm="ortho") / q) * q
    out = fft.idctn(coef, axes=(2, 3), norm="ortho").transpose(0, 2, 1, 3).reshape(h + ph, w + pw)[:h, :w] + 128.0
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


class Perturbations:
    """Slow background change, moving cloud, non-gas objects, shutter events, vibration and bad pixels.

    It has its own random stream, so switching perturbations on never changes the gas or the camera noise."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        h, w = cfg.height, cfg.width
        self.rng = np.random.default_rng([cfg.seed, 20260101])
        xm = (np.arange(w) + 0.5) * cfg.m_per_px
        ym = (h - (np.arange(h) + 0.5)) * cfg.m_per_px                    # metres, y up
        self.X, self.Y = np.meshgrid(xm, ym)
        self.fov_h = h * cfg.m_per_px
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        p = np.clip(0.5 + 0.8 * (xx / w - 0.5) + 0.3 * (yy / h - 0.5), 0.0, 1.0)
        self.weight = (1.0 - cfg.bg_change_spatial) + cfg.bg_change_spatial * 2.0 * p
        self.has_bg = cfg.bg_change_k != 0.0
        self.has_cloud = cfg.cloud_k != 0.0
        self.has_objects = cfg.object_count > 0
        self.has_nuc = cfg.nuc_period_s > 0.0
        self.has_vib = cfg.vibration_px > 0.0
        self.compress = cfg.compression_quality > 0
        self.dynamic_bg = self.has_bg or self.has_cloud
        self.nuisance = self.has_cloud or self.has_objects
        self.cloud_y = float(self.rng.uniform(0.25, 0.65)) * self.fov_h
        rows = self.rng.uniform(0.12, 0.75, max(cfg.object_count, 0))
        self.object_y = (np.full(len(rows), cfg.object_row_frac) if cfg.object_row_frac >= 0 else rows) * self.fov_h
        self.nuc_every = max(1, int(round(cfg.nuc_period_s * cfg.fps))) if self.has_nuc else 0
        self.nuc_frames: list[int] = []
        self.offset = 0.0
        self.hold = 0
        self.vel = np.zeros(2)
        self.shift = np.zeros(2)
        n_bad = int(round(cfg.bad_pixel_frac * h * w))
        idx = self.rng.choice(h * w, size=n_bad, replace=False) if n_bad else np.zeros(0, int)
        self.bad_idx, self.bad_hot = idx, self.rng.random(n_bad) < 0.5

    # -- scene ------------------------------------------------------------------
    def cloud_field(self, t: float):
        c = self.cfg
        if not self.has_cloud:
            return None
        sigma = c.cloud_size_m / 2.0
        cx = -2.0 * c.cloud_size_m + c.cloud_speed_ms * max(t - c.cloud_start_s, 0.0)
        return c.cloud_k * np.exp(-0.5 * (((self.X - cx) / sigma) ** 2 + ((self.Y - self.cloud_y) / sigma) ** 2))

    def field(self, t: float):
        """Temperature change (K) of the background at time t, or None when there is none."""
        c = self.cfg
        out = None
        if self.has_bg:
            f = np.sin(2 * np.pi * t / c.bg_change_period_s) if c.bg_change_shape == "sine" else min(t / c.bg_change_period_s, 1.0)
            out = c.bg_change_k * f * self.weight
        cloud = self.cloud_field(t)
        if cloud is not None:
            out = cloud if out is None else out + cloud
        return out

    def object_mask(self, t: float):
        c = self.cfg
        if not self.has_objects:
            return None
        mask = None
        for k in range(c.object_count):
            start = c.object_start_s + k * c.object_gap_s
            if t < start:
                continue
            cx = -c.object_size_m + c.object_speed_ms * (t - start)
            m = ((self.X - cx) / (c.object_size_m / 2.0)) ** 2 + ((self.Y - self.object_y[k]) / c.object_size_m) ** 2 < 1.0
            mask = m if mask is None else (mask | m)
        return mask

    # -- camera -----------------------------------------------------------------
    def step_vibration(self, dt: float):
        c = self.cfg
        self.vel += -self.vel / c.vibration_tau_s * dt + c.vibration_px * np.sqrt(2.0 * dt / c.vibration_tau_s) * self.rng.standard_normal(2)
        self.shift = self.vel.copy()
        return self.shift

    def shift_image(self, a: np.ndarray) -> np.ndarray:
        from scipy import ndimage as ndi
        return ndi.shift(a, (self.shift[0], self.shift[1]), order=1, mode="nearest")

    def nuc_event(self, i: int) -> bool:
        return self.has_nuc and i > 0 and i % self.nuc_every == 0

    def apply_nuc(self, i: int, fpn: np.ndarray) -> np.ndarray:
        c = self.cfg
        self.nuc_frames.append(i)
        self.offset += float(self.rng.normal(0.0, c.nuc_step_k))
        self.hold = c.nuc_freeze_frames
        if c.nuc_fpn_change > 0:
            new = c.fpn_k * self.rng.standard_normal(fpn.shape) + c.col_noise_k * self.rng.standard_normal((1, fpn.shape[1]))
            fpn = np.sqrt(1.0 - c.nuc_fpn_change) * fpn + np.sqrt(c.nuc_fpn_change) * new
        return fpn

    def damage(self, u8: np.ndarray, temp_ck: np.ndarray | None):
        """Compression and bad pixels, applied to the finished frames."""
        c = self.cfg
        if self.compress:
            u8 = jpeg_like(u8, c.compression_quality)
        if len(self.bad_idx):
            flat = u8.reshape(-1)
            flat[self.bad_idx] = np.where(self.bad_hot, 255, 0).astype(np.uint8)
            if temp_ck is not None:
                tflat = temp_ck.reshape(-1)
                tflat[self.bad_idx] = np.where(self.bad_hot, 65535, 0).astype(np.uint16)
        return u8, temp_ck


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
class Simulation:
    """One simulated sequence, produced frame by frame (so long videos never have to fit in memory)."""

    def __init__(self, cfg: Config):
        validate(cfg)
        self.cfg = cfg
        self.n_frames = int(round(cfg.duration_s * cfg.fps))
        self.rng = np.random.default_rng(cfg.seed)
        self.band = Band(cfg.band_lo_um, cfg.band_hi_um)
        self.scene_c, tip_m = build_scene(cfg)
        self.plume = Plume(cfg, tip_m, self.rng)
        self.pert = Perturbations(cfg)
        h, w = cfg.height, cfg.width
        self.l_bg = self.band.radiance(self.scene_c)
        t_gas_c = cfg.t_amb_c if cfg.t_exit_c is None else cfg.t_exit_c
        self.l_gas = float(self.band.radiance(t_gas_c))
        self.t_bg_cam = self.band.temperature_c(gaussian_blur(self.l_bg, cfg.psf_sigma_px))
        t_bg_cam = self.t_bg_cam

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
        self.span = float(auto_span if cfg.span_c is None else cfg.span_c)
        self.level = float(auto_level if cfg.level_c is None else cfg.level_c)
        self.lo = self.level - self.span / 2

        self.fpn = cfg.fpn_k * self.rng.standard_normal((h, w)) + cfg.col_noise_k * self.rng.standard_normal((1, w))
        self.drift_phase = self.rng.uniform(0, 2 * np.pi)
        self.lag = 1.0 - np.exp(-(1.0 / cfg.fps) / max(cfg.detector_tau_s, 1e-6))
        self.finished = False

    def layout(self) -> dict:
        """name -> (shape of one frame, dtype) of every array the sequence contains."""
        c, p = self.cfg, self.pert
        shape = (c.height, c.width)
        lay = dict(u8=(shape, np.uint8), contrast=(shape, np.float16), mask=(shape, bool))
        if c.store_temp:
            lay["temp_ck"] = (shape, np.uint16)
        if c.store_cl:
            lay["cl"] = (shape, np.float32)
        if p.has_vib:
            lay["shift_px"] = ((2,), np.float32)
        if p.nuisance:
            lay["nuisance"] = (shape, bool)
        return lay

    def frames(self):
        """Generator: one dict of per-frame arrays at a time."""
        cfg, band, pert = self.cfg, self.band, self.pert
        h, w = cfg.height, cfg.width
        state = self.t_bg_cam.copy()
        fpn, last = self.fpn, None
        l_bg, l_gas, t_bg_cam = self.l_bg, self.l_gas, self.t_bg_cam
        l_obj = float(band.radiance(cfg.object_temp_c)) if pert.has_objects else None
        dt = 1.0 / cfg.fps
        for i in range(self.n_frames):
            self.plume.advance(dt)
            cl = self.plume.column_density()
            t = i * dt
            if pert.dynamic_bg:
                scene_t = self.scene_c + pert.field(t)
                l_bg_t = band.radiance(scene_t)
                t_bg_t = band.temperature_c(gaussian_blur(l_bg_t, cfg.psf_sigma_px))
            else:
                l_bg_t, t_bg_t = l_bg, t_bg_cam
            tau = 1.0 - cfg.band_fraction * (1.0 - np.exp(-cfg.k_abs * cl))
            l_scene = tau * l_bg_t + (1.0 - tau) * l_gas
            t_clean = band.temperature_c(gaussian_blur(l_scene, cfg.psf_sigma_px))
            contrast = t_clean - t_bg_t                                  # change caused by the gas only
            nuis = None
            if pert.nuisance:
                om = pert.object_mask(t)
                cloud = pert.cloud_field(t)
                nuis = np.zeros((h, w), bool)
                if om is not None:
                    nuis |= om
                    t_clean = band.temperature_c(gaussian_blur(np.where(om, l_obj, l_scene), cfg.psf_sigma_px))
                if cloud is not None:
                    nuis |= np.abs(cloud) > cfg.mask_thresh_k
            if pert.has_vib:
                pert.step_vibration(dt)
                t_clean, contrast = pert.shift_image(t_clean), pert.shift_image(contrast)
                cl = pert.shift_image(cl)
                if nuis is not None:
                    nuis = pert.shift_image(nuis.astype(np.float32)) > 0.5
            if pert.nuc_event(i):
                fpn = pert.apply_nuc(i, fpn)

            state += self.lag * (t_clean - state)            # detector thermal lag
            drift = cfg.drift_k * np.sin(2 * np.pi * i / cfg.fps / cfg.drift_period_s + self.drift_phase)
            t_meas = state + fpn + drift
            if pert.has_nuc:
                t_meas = t_meas + pert.offset
            t_meas = t_meas + cfg.netd_k * self.rng.standard_normal((h, w))

            if pert.hold > 0 and last is not None:           # shutter closed: the image freezes
                pert.hold -= 1
                yield {k: v.copy() for k, v in last.items()}
                continue
            u8 = np.clip((t_meas - self.lo) / self.span * 255.0 + 0.5, 0, 255).astype(np.uint8)
            temp = np.clip((t_meas + 273.15) * 100.0 + 0.5, 0, 65535).astype(np.uint16) if cfg.store_temp else None
            if pert.compress or len(pert.bad_idx):
                u8, temp = pert.damage(u8, temp)
            frame = dict(u8=u8, contrast=contrast.astype(np.float16), mask=np.abs(contrast) > cfg.mask_thresh_k)
            if temp is not None:
                frame["temp_ck"] = temp
            if cfg.store_cl:
                frame["cl"] = np.asarray(cl, np.float32)
            if pert.has_vib:
                frame["shift_px"] = pert.shift.astype(np.float32)
            if nuis is not None:
                frame["nuisance"] = nuis
            last = frame
            yield frame
        self.finished = True

    def meta(self) -> str:
        meta = asdict(self.cfg)
        meta.update(level_c=self.level, span_c=self.span, k_per_dn=self.span / 255.0,
                    emitted_g=self.plume.emitted_kg * 1e3, ppm_m_per_kg_m2=self.plume.kg_to_ppm_m,
                    nuc_frames=list(self.pert.nuc_frames), n_frames=self.n_frames)
        return json.dumps(meta)


def simulate(cfg: Config) -> dict:
    """The whole sequence in memory (see `simulate_to_dir` for videos that do not fit)."""
    sim_ = Simulation(cfg)
    out = {name: np.zeros((sim_.n_frames, *shape), dtype) for name, (shape, dtype) in sim_.layout().items()}
    for i, frame in enumerate(sim_.frames()):
        for name, arr in frame.items():
            out[name][i] = arr
    out["meta"] = sim_.meta()
    return out


def simulate_to_dir(cfg: Config, path, flush_every: int = 250, progress=None) -> Path:
    """Write the sequence to a folder frame by frame (one .npy per array, read later as memory maps).

    Memory use does not depend on the duration. Layout: <path>/u8.npy, contrast.npy, mask.npy, ... and meta.json."""
    sim_ = Simulation(cfg)
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    maps = {name: np.lib.format.open_memmap(path / f"{name}.npy", mode="w+", dtype=dtype, shape=(sim_.n_frames, *shape))
            for name, (shape, dtype) in sim_.layout().items()}
    for i, frame in enumerate(sim_.frames()):
        for name, arr in frame.items():
            maps[name][i] = arr
        if (i + 1) % flush_every == 0:
            for m in maps.values():
                m.flush()
            if progress:
                progress(i + 1, sim_.n_frames)
    for m in maps.values():
        m.flush()
    (path / "meta.json").write_text(sim_.meta())
    return path


def load_sequence(path) -> dict:
    """Read a sequence written by `save_sequence` (.npz) or `simulate_to_dir` (folder, as memory maps)."""
    path = Path(path)
    if path.is_dir():
        seq = {p.stem: np.load(p, mmap_mode="r") for p in sorted(path.glob("*.npy"))}
        seq["meta"] = (path / "meta.json").read_text()
        return seq
    data = np.load(path, allow_pickle=False)
    return {k: data[k] for k in data.files}


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


def _random_perturbations(rng: np.random.Generator) -> dict:
    """Each real-world perturbation is switched on with probability 0.4 (drawn after the base configuration)."""
    out = {}
    if rng.random() < 0.4:
        out.update(bg_change_k=float(rng.uniform(0.3, 2.0) * rng.choice([-1, 1])), bg_change_period_s=float(rng.uniform(8, 40)),
                   bg_change_spatial=float(rng.uniform(0, 1)))
    if rng.random() < 0.4:
        out.update(cloud_k=float(rng.uniform(0.4, 1.5) * rng.choice([-1, 1])), cloud_size_m=float(rng.uniform(0.3, 0.9)),
                   cloud_speed_ms=float(rng.uniform(0.08, 0.3)))
    if rng.random() < 0.4:
        out.update(object_count=int(rng.integers(1, 4)), object_size_m=float(rng.uniform(0.08, 0.2)),
                   object_speed_ms=float(rng.uniform(0.15, 0.5)), object_start_s=float(rng.uniform(1.5, 3.0)))
    if rng.random() < 0.4:
        out.update(nuc_period_s=float(rng.uniform(2, 6)), nuc_step_k=float(rng.uniform(0.02, 0.15)))
    if rng.random() < 0.4:
        out.update(vibration_px=float(rng.uniform(0.3, 2.0)))
    if rng.random() < 0.4:
        out.update(compression_quality=int(rng.integers(15, 80)))
    if rng.random() < 0.3:
        out.update(bad_pixel_frac=float(rng.uniform(0.0002, 0.003)))
    return out


def random_config(rng: np.random.Generator, seed: int, perturbations: bool = False) -> Config:
    """Domain randomisation over release, weather, scene and camera noise.

    With `perturbations=True` real-world perturbations are added (extra draws after the base ones, so the base
    configuration of a given seed is the same with and without them)."""
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
    extra = _random_perturbations(rng) if perturbations else {}
    return Config(scene=scene, gas=gas, seed=seed, **values, **extra)


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
    ap.add_argument("--stream", action="store_true",
                    help="write each sequence to a folder frame by frame (memory independent of the duration; for long videos)")
    ap.add_argument("--perturbations", action="store_true", help="with --n: add random real-world perturbations")
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
            configs = [apply_overrides(random_config(rng, base * 100000 + i, args.perturbations), over) for i in range(args.n)]
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
        name = f"seq_{i:04d}_{cfg.scene}"
        if args.stream:
            simulate_to_dir(cfg, out / name, progress=lambda k, n: print(f"\r{name}: {k}/{n} frames", end="", flush=True))
            print(f"\r{name}: {int(round(cfg.duration_s * cfg.fps))} frames written to {out / name}/ (folder of .npy + meta.json)")
            continue
        seq = simulate(cfg)
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
