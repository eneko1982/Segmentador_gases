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

Usage
-----
    python ogi_sim.py --out demo --scene hot_plate --flow 0.1 --preview
    python ogi_sim.py --out dataset --n 50 --seed 1        # randomised dataset
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x renamed trapz

# --------------------------------------------------------------------------
# Gas properties: molar mass (kg/mol) and density at ~20 C (kg/m3)
# --------------------------------------------------------------------------
GASES = {
    "R134a": dict(molar_mass=0.10203, density=4.25),
    "R152a": dict(molar_mass=0.06605, density=2.76),
    "R1234ze": dict(molar_mass=0.11404, density=4.77),
}
RHO_AIR = 1.20
MOLAR_VOLUME = 0.02445  # m3/mol at 25 C, 1 atm
G = 9.81


@dataclass
class Config:
    # --- camera -----------------------------------------------------------
    width: int = 384
    height: int = 288
    fps: float = 25.0
    m_per_px: float = 0.005          # object-plane size of one pixel (m)
    netd_k: float = 0.035            # temporal noise, K
    fpn_k: float = 0.020             # residual per-pixel fixed pattern, K
    col_noise_k: float = 0.010       # residual column pattern, K
    drift_k: float = 0.030           # slow global offset drift amplitude, K
    psf_sigma_px: float = 0.7        # optical blur
    detector_tau_s: float = 0.010    # microbolometer thermal time constant
    level_c: float | None = None     # 8-bit window centre (None = auto, fixed)
    span_c: float | None = None      # 8-bit window width, K (None = auto, fixed)
    # --- scene ------------------------------------------------------------
    scene: str = "hot_plate"         # "hot_plate" (absorption) | "cold_sky" (emission)
    t_amb_c: float = 22.0
    t_bg_c: float = 70.0             # hot plate temperature / sky apparent temperature
    # --- gas and release --------------------------------------------------
    gas: str = "R134a"
    flow_g_s: float = 0.10           # mass flow rate while the vent is open
    t_exit_c: float | None = None    # gas exit temperature (None = ambient)
    band_fraction: float = 0.25      # PLACEHOLDER - calibrate
    k_abs: float = 2.0e-3            # PLACEHOLDER - 1/(ppm*m), calibrate
    v_exit: float = 3.0              # exit velocity, m/s
    exit_angle_deg: float = 0.0      # 0 = straight up, positive = towards +x
    nozzle_sigma_m: float = 0.005    # initial puff size
    jet_spread: float = 0.11         # d(sigma)/d(path) for the jet
    wind_ms: float = 0.30            # mean horizontal wind (+x)
    gust_ms: float = 0.15            # wind fluctuation (rms)
    turb_rms: float = 0.15           # ambient turbulence velocity rms, m/s
    jet_turb: float = 0.25           # jet turbulence intensity (fraction of local speed)
    leak_on_s: float = 1.0           # vent opens at this time
    leak_off_s: float | None = None  # vent closes (None = stays open)
    # --- run --------------------------------------------------------------
    duration_s: float = 4.0
    mask_thresh_k: float = 0.035     # mask = |noise-free contrast| above this
    seed: int = 0


# --------------------------------------------------------------------------
# Radiometry: Planck radiance integrated over the 8-14 um band
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

    tip_px = (int(0.42 * w), int(0.70 * h))          # (col, row) of the pipe tip
    pipe_half = 3

    # smooth low-frequency non-uniformity shared by both scenes
    lowfreq = (np.sin(xx / w * 2.1 + 0.4) * np.cos(yy / h * 1.7 + 1.1)).astype(np.float32)

    if cfg.scene == "hot_plate":
        scene = np.full((h, w), cfg.t_amb_c, np.float32) + 0.4 * lowfreq
        r0, r1, c0, c1 = int(0.06 * h), int(0.86 * h), int(0.08 * w), int(0.92 * w)
        plate = cfg.t_bg_c + 0.8 * lowfreq - 1.5 * ((yy - r0) / (r1 - r0)) ** 2
        scene[r0:r1, c0:c1] = plate[r0:r1, c0:c1]
    elif cfg.scene == "cold_sky":
        # clear sky gets colder with elevation; ground strip at the bottom
        elev = 1.0 - yy / h
        scene = cfg.t_bg_c - 18.0 * elev + 0.6 * lowfreq
        ground = int(0.90 * h)
        scene[ground:, :] = cfg.t_amb_c - 1.0 + 0.5 * lowfreq[ground:, :]
    else:
        raise ValueError(f"unknown scene {cfg.scene!r}")

    # vent pipe: opaque, at ambient temperature
    scene[tip_px[1]:, tip_px[0] - pipe_half: tip_px[0] + pipe_half + 1] = cfg.t_amb_c + 0.5
    # tip position in metres (x right, y up, origin at bottom-left corner)
    tip_m = np.array([tip_px[0] * cfg.m_per_px, (h - tip_px[1]) * cfg.m_per_px])
    return scene.astype(np.float32), tip_m


# --------------------------------------------------------------------------
# Kinematic turbulence: divergence-free sum of random Fourier modes
# --------------------------------------------------------------------------
class Turbulence:
    def __init__(self, rms: float, rng: np.random.Generator, n_modes: int = 60):
        lam = np.exp(rng.uniform(np.log(0.02), np.log(0.8), n_modes))   # wavelengths, m
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
    PUFFS_PER_S = 500.0
    SUBSTEP_S = 0.005

    def __init__(self, cfg: Config, tip_m: np.ndarray, rng: np.random.Generator):
        self.cfg, self.tip, self.rng = cfg, tip_m, rng
        self.turb = Turbulence(cfg.turb_rms, rng)
        gas = GASES[cfg.gas]
        self.kg_to_ppm_m = MOLAR_VOLUME / gas["molar_mass"] * 1e6       # kg/m2 -> ppm*m
        self.buoy0 = -G * (gas["density"] - RHO_AIR) / gas["density"]   # m/s2, <0 = sinks
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
        # wind gust: Ornstein-Uhlenbeck process, 2 s correlation time
        self.gust += -self.gust / 2.0 * dt + c.gust_ms * np.sqrt(2 * dt / 2.0) * rng.standard_normal()
        wind = np.array([c.wind_ms + self.gust, 0.0])

        # advance existing puffs
        if len(self.sigma):
            ratio = c.nozzle_sigma_m / self.sigma                       # 1 at exit -> 0 when diluted
            v_jet = c.v_exit * ratio[:, None] * self.exit_dir
            ambient = wind + self.turb.velocity(self.pos, self.t)
            self.vbuoy += dt * (self.buoy0 * ratio ** 2 - self.vbuoy / 0.5)
            # shear-generated turbulence of the jet itself: one Ornstein-Uhlenbeck
            # velocity per puff, rms proportional to the local jet speed
            jet_speed = c.v_exit * ratio
            tau_j = np.clip(2.0 * self.sigma / (jet_speed + 1e-3), 0.01, 0.5)[:, None]
            rms_j = (c.jet_turb * jet_speed)[:, None]
            self.vjt += (-self.vjt / tau_j * dt
                         + rms_j * np.sqrt(2 * dt / tau_j) * rng.standard_normal(self.vjt.shape))
            vel = v_jet + self.vjt + (1 - ratio[:, None]) * ambient
            vel[:, 1] += self.vbuoy
            self.pos += vel * dt
            # puffs only grow by entrainment (jet) and slow relative diffusion;
            # the large-scale shape comes from the velocity fields above
            self.sigma += dt * (c.jet_spread * jet_speed + 0.10 * c.turb_rms * (1 - ratio) + 0.002)

        # emit new puffs, spread along the sub-step to avoid beading
        if self._is_open():
            self._carry += self.PUFFS_PER_S * dt
            n = int(self._carry)
            self._carry -= n
            if n:
                frac = rng.uniform(0, 1, n)
                pos = self.tip + frac[:, None] * dt * c.v_exit * self.exit_dir
                pos += rng.normal(0, 0.3 * c.nozzle_sigma_m, (n, 2))
                m_total = c.flow_g_s * 1e-3 * n / self.PUFFS_PER_S
                w = rng.lognormal(0.0, 0.35, n)
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
            margin = 4 * self.sigma
            keep = ((self.pos[:, 0] > -margin) & (self.pos[:, 0] < wm + margin)
                    & (self.pos[:, 1] > -margin) & (self.pos[:, 1] < hm + margin))
            if not keep.all():
                self.pos, self.sigma = self.pos[keep], self.sigma[keep]
                self.mass, self.vbuoy = self.mass[keep], self.vbuoy[keep]
                self.vjt = self.vjt[keep]
        self.t += dt

    def advance(self, duration: float):
        n = max(1, int(round(duration / self.SUBSTEP_S)))
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
            r = int(3.5 * s) + 1
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
    rng = np.random.default_rng(cfg.seed)
    band = Band()
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
        ref = t_bg_cam[t_bg_cam > cfg.t_bg_c - 5.0]
        p_lo, p_hi = np.percentile(ref, [1, 99])
        auto_span = (p_hi - p_lo) + 8.0
        auto_level = p_hi + 1.0 - auto_span / 2      # headroom below for the gas
    else:                                            # gas brightens a cold sky
        ref = t_bg_cam[t_bg_cam < cfg.t_amb_c - 8.0]
        p_lo, p_hi = np.percentile(ref, [1, 99])
        auto_span = (p_hi - p_lo) + 12.0
        auto_level = p_lo - 1.0 + auto_span / 2      # headroom above for the gas
    span = float(auto_span if cfg.span_c is None else cfg.span_c)
    level = float(auto_level if cfg.level_c is None else cfg.level_c)
    lo = level - span / 2

    fpn = cfg.fpn_k * rng.standard_normal((h, w)) + cfg.col_noise_k * rng.standard_normal((1, w))
    drift_phase = rng.uniform(0, 2 * np.pi)
    lag = 1.0 - np.exp(-(1.0 / cfg.fps) / max(cfg.detector_tau_s, 1e-6))

    out = dict(
        u8=np.zeros((n_frames, h, w), np.uint8),
        temp_ck=np.zeros((n_frames, h, w), np.uint16),
        cl=np.zeros((n_frames, h, w), np.float32),
        contrast=np.zeros((n_frames, h, w), np.float16),
        mask=np.zeros((n_frames, h, w), bool),
    )
    state = t_bg_cam.copy()
    for i in range(n_frames):
        plume.advance(1.0 / cfg.fps)
        cl = plume.column_density()
        tau = 1.0 - cfg.band_fraction * (1.0 - np.exp(-cfg.k_abs * cl))
        l_scene = tau * l_bg + (1.0 - tau) * l_gas
        t_clean = band.temperature_c(gaussian_blur(l_scene, cfg.psf_sigma_px))
        contrast = t_clean - t_bg_cam

        state += lag * (t_clean - state)             # detector thermal lag
        drift = cfg.drift_k * np.sin(2 * np.pi * i / cfg.fps / 20.0 + drift_phase)
        t_meas = state + fpn + drift + cfg.netd_k * rng.standard_normal((h, w))

        out["u8"][i] = np.clip((t_meas - lo) / span * 255.0 + 0.5, 0, 255).astype(np.uint8)
        out["temp_ck"][i] = np.clip((t_meas + 273.15) * 100.0 + 0.5, 0, 65535).astype(np.uint16)
        out["cl"][i] = cl
        out["contrast"][i] = contrast
        out["mask"][i] = np.abs(contrast) > cfg.mask_thresh_k

    meta = asdict(cfg)
    meta.update(level_c=level, span_c=span, k_per_dn=span / 255.0, emitted_g=plume.emitted_kg * 1e3,
                ppm_m_per_kg_m2=plume.kg_to_ppm_m)
    out["meta"] = json.dumps(meta)
    return out


def random_config(rng: np.random.Generator, seed: int) -> Config:
    """Domain randomisation over release, weather, scene and camera noise."""
    scene = str(rng.choice(["hot_plate", "cold_sky"]))
    return Config(
        scene=scene,
        t_amb_c=float(rng.uniform(8, 30)),
        t_bg_c=float(rng.uniform(40, 90) if scene == "hot_plate" else rng.uniform(-45, -10)),
        gas=str(rng.choice(list(GASES))),
        flow_g_s=float(np.exp(rng.uniform(np.log(0.01), np.log(1.0)))),
        v_exit=float(rng.uniform(0.8, 6.0)),
        exit_angle_deg=float(rng.uniform(-20, 20)),
        wind_ms=float(rng.uniform(-1.2, 1.2)),
        gust_ms=float(rng.uniform(0.02, 0.4)),
        turb_rms=float(rng.uniform(0.05, 0.35)),
        leak_on_s=float(rng.uniform(0.4, 1.6)),
        netd_k=float(rng.uniform(0.025, 0.06)),
        fpn_k=float(rng.uniform(0.005, 0.04)),
        seed=seed,
    )


def save_sequence(path: Path, seq: dict):
    np.savez_compressed(path, **seq)


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
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--scene", default="hot_plate", choices=["hot_plate", "cold_sky"])
    ap.add_argument("--flow", type=float, default=0.10, help="g/s")
    ap.add_argument("--wind", type=float, default=0.30, help="m/s")
    ap.add_argument("--duration", type=float, default=4.0, help="s")
    ap.add_argument("--preview", action="store_true", help="also write an animated GIF")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.n:
        rng = np.random.default_rng(args.seed)
        configs = [random_config(rng, args.seed * 100000 + i) for i in range(args.n)]
    else:
        t_bg = 70.0 if args.scene == "hot_plate" else -25.0
        configs = [Config(scene=args.scene, t_bg_c=t_bg, flow_g_s=args.flow, wind_ms=args.wind,
                          seed=args.seed)]
    for i, cfg in enumerate(configs):
        cfg.duration_s = args.duration
        seq = simulate(cfg)
        name = f"seq_{i:04d}_{cfg.scene}"
        save_sequence(out / f"{name}.npz", seq)
        if args.preview:
            save_preview(out / f"{name}.gif", seq)
        meta = json.loads(seq["meta"])
        print(f"{name}: {cfg.gas} {cfg.flow_g_s:.3f} g/s, wind {cfg.wind_ms:+.2f} m/s, "
              f"emitted {meta['emitted_g']:.3f} g, max CL {float(seq['cl'].max()):.0f} ppm*m, "
              f"mask px (last frame) {int(seq['mask'][-1].sum())}")


if __name__ == "__main__":
    main()
