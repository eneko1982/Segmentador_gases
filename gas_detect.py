"""
gas_detect.py - Classical (no training) gas-plume detector for a fixed camera.

Works on any (T, H, W) image sequence, 8-bit or radiometric, as long as the
first part of the sequence is gas-free (used to learn the background and the
noise level). Nothing here is learned from labelled data.

Pipeline
--------
1. background   mean of the first gas-free frames
2. difference   frame - background              (removes the scene and the
                                                 fixed-pattern noise)
3. spatial      Gaussian smoothing              (plumes are large and smooth,
                                                 noise is pixel-sized)
4. drift        subtract the most common value of the smoothed difference
                (the mode: removes the slow global offset drift of the
                detector without being pulled by the plume, as long as part
                of the image is gas-free)
5. temporal     causal mean of the last frames  (gas persists, noise does not)
6. normalise    divide by the noise measured on the remaining gas-free frames
                -> z-score: "how many noise sigmas is this pixel away from
                the gas-free scene"
7. threshold    hysteresis: keep regions above z_low that contain at least
                one pixel above z_high; both polarities (darker or brighter)
8. clean-up     drop regions smaller than min_area pixels

Usage
-----
    python gas_detect.py demo/seq_0000_hot_plate.npz --preview
    python gas_detect.py demo/seq_0000_cold_sky.npz --input temp --preview

From Python, on your own data:
    from gas_detect import detect
    result = detect(frames, fps=25, gas_free_s=1.0)     # frames: (T, H, W)
    result["mask"]      # (T, H, W) bool, most sensitive detection
    result["z"]         # (T, H, W) signed z-score (noise sigmas)
    result["estimate"]  # (T, H, W) estimated change caused by the gas, in the
                        # units of the input (grey levels or kelvin)

Saturated pixels (0 or 255 in 8-bit input) carry no information and are
ignored automatically.

Robust options (all off by default: the default result is the original detector)
-------------------------------------------------------------------------------
background="adaptive"  the background follows slow changes and the slow drift of the camera pattern (shutter
                       events) with time constant `bg_tau_s`, and is frozen while an alarm is active, so a
                       constant leak is not "absorbed".
drift_order=1|2        subtracts a smooth surface (plane / quadratic) from the difference image: the sun, a
                       warming plate or a cloud edge change the background unevenly, a single global level
                       cannot remove that.
auto_threshold=True    raises the thresholds until they clear what was seen while learning (compressed video).
stabilize=True         removes camera vibration: each frame is aligned to the background (phase correlation).
roi=<mask>             watch zone: nothing outside it can start or keep a detection.
polarity="dark|bright" only the expected sign (hot plate: gas darker; a warm person in front is brighter).
source_roi + require_source=True
                       a detection only counts if it starts in the source zone (around the vent) and then grows
                       from there: a person who walks in from the border is rejected.
keep_maps=False        streaming mode for long videos: only the masks (and per-frame summaries) are kept.
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi


def _hysteresis(score: np.ndarray, low: float, high: float, min_area: int) -> np.ndarray:
    """Connected regions above `low` that contain a pixel above `high`."""
    weak = score > low
    if not weak.any():
        return weak
    labels, n = ndi.label(weak)
    if n == 0:
        return weak
    area = np.bincount(labels.ravel(), minlength=n + 1)
    has_strong = np.zeros(n + 1, bool)
    has_strong[np.unique(labels[score > high])] = True
    keep = has_strong & (area >= min_area)
    keep[0] = False
    return keep[labels]


def _mode(values: np.ndarray, scale: float) -> float:
    """Most common value of a sample: histogram peak refined by a local median."""
    lo, hi = np.percentile(values, [0.5, 99.5])
    if hi - lo < scale:
        return float(np.median(values))
    bins = int(np.clip((hi - lo) / scale, 8, 4000))
    hist, edges = np.histogram(values, bins=bins, range=(lo, hi))
    hist = np.convolve(hist, np.ones(3), mode="same")
    k = int(np.argmax(hist))
    centre = 0.5 * (edges[k] + edges[k + 1])
    half = max(2.0 * scale, edges[1] - edges[0])
    for _ in range(3):
        near = values[np.abs(values - centre) < half]
        if near.size == 0:
            break
        centre = float(np.median(near))
    return centre


def _frame_mask(z: np.ndarray, z_low: float, z_high: float, min_area: int, roi=None, polarity: str = "both") -> np.ndarray:
    """Hysteresis mask of one z-score frame. z > 0 = brighter than the background, z < 0 = darker."""
    if roi is not None:
        z = np.where(roi, z, 0.0)
    out = None
    if polarity in ("both", "bright"):
        out = _hysteresis(z, z_low, z_high, min_area)
    if polarity in ("both", "dark"):
        neg = _hysteresis(-z, z_low, z_high, min_area)
        out = neg if out is None else (out | neg)
    return out


STAB_SPREAD_FACTOR = 1.6    # stabilize=True: pixels whose frame-to-frame spread over the learning frames exceeds this × the noise are dropped
AUTO_THRESHOLD_MARGIN = 1.15   # auto_threshold: thresholds are raised until they clear 1.15 × the largest |z| seen while learning
STAB_DEADBAND_PX = 0.12     # shifts smaller than this are not corrected (interpolating a still scene would only blur it)


class _OriginTracker:
    """Keeps only detections that start in the source zone and then grow from it (frame to frame)."""

    def __init__(self, source: np.ndarray, source_dilate_px: int = 10, follow_px: int = 4):
        self.source = ndi.binary_dilation(source, iterations=max(int(source_dilate_px), 0)) if source_dilate_px else source
        self.follow = int(follow_px)
        self.prev = np.zeros_like(source, bool)

    def __call__(self, mask: np.ndarray) -> np.ndarray:
        if not mask.any():
            self.prev = np.zeros_like(mask)
            return self.prev
        labels, n = ndi.label(mask)
        seeds = self.source | (ndi.binary_dilation(self.prev, iterations=self.follow) if self.prev.any() else False)
        keep = np.unique(labels[seeds & mask])
        keep = keep[keep > 0]
        self.prev = np.isin(labels, keep)
        return self.prev


class ShiftEstimator:
    """Translation of an image relative to a reference: phase correlation, then a least-squares refinement.

    The refinement (Gauss-Newton on the whole image gradient, with a free brightness offset) brings the error
    from a few hundredths to about a hundredth of a pixel, which matters on sharp edges."""

    def __init__(self, ref: np.ndarray, max_shift: float = 6.0, hp_sigma: float = 3.0, refine_iters: int = 3):
        self.ref = ref.astype(np.float32)
        self.max_shift, self.hp_sigma, self.refine_iters = float(max_shift), hp_sigma, refine_iters
        h, w = ref.shape
        self.win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
        self.f_ref = np.conj(np.fft.fft2(self._prep(ref)))
        b = int(np.ceil(max_shift)) + 2
        self.inner = (slice(b, h - b), slice(b, w - b))

    def _prep(self, img: np.ndarray) -> np.ndarray:
        return (img - ndi.gaussian_filter(img, self.hp_sigma)) * self.win

    def coarse(self, img: np.ndarray) -> np.ndarray:
        cross = np.fft.fft2(self._prep(img)) * self.f_ref
        cross /= np.abs(cross) + 1e-9
        r = np.fft.fftshift(np.fft.ifft2(cross).real)
        h, w = r.shape
        cy, cx, m = h // 2, w // 2, int(np.ceil(self.max_shift)) + 1
        sub = r[cy - m: cy + m + 1, cx - m: cx + m + 1]
        iy, ix = np.unravel_index(int(np.argmax(sub)), sub.shape)

        def parabola(a, b, c):
            den = a - 2.0 * b + c
            return 0.0 if abs(den) < 1e-12 else 0.5 * (a - c) / den

        dy = dx = 0.0
        if 0 < iy < sub.shape[0] - 1:
            dy = parabola(sub[iy - 1, ix], sub[iy, ix], sub[iy + 1, ix])
        if 0 < ix < sub.shape[1] - 1:
            dx = parabola(sub[iy, ix - 1], sub[iy, ix], sub[iy, ix + 1])
        return np.array([iy - m + dy, ix - m + dx])

    def __call__(self, img: np.ndarray, ignore: np.ndarray | None = None) -> np.ndarray:
        """(dy, dx) such that img(y, x) ≈ ref(y - dy, x - dx); undo it with ndi.shift(img, -shift).

        `ignore` (H, W bool): pixels the refinement must not use (where gas is being detected)."""
        s = self.coarse(img)
        img = np.asarray(img, np.float32)[self.inner]
        keep0 = np.ones(img.size) if ignore is None else (~ignore[self.inner]).ravel().astype(np.float64)
        for _ in range(self.refine_iters):
            moved = ndi.shift(self.ref, (s[0], s[1]), order=1, mode="nearest")
            gy, gx = np.gradient(ndi.gaussian_filter(moved, 0.7))
            res = (img - moved[self.inner]).ravel().astype(np.float64)
            a = np.stack([-gy[self.inner].ravel(), -gx[self.inner].ravel(), np.ones(res.size)], 1).astype(np.float64)
            wgt = keep0.copy()
            for _ in range(2):                               # Huber weights: the plume must not pull the fit
                ata, atb = (a * wgt[:, None]).T @ a, (a * wgt[:, None]).T @ res
                try:
                    p = np.linalg.solve(ata, atb)
                except np.linalg.LinAlgError:
                    break
                resid = res - a @ p
                k = 1.5 * 1.4826 * float(np.median(np.abs(resid))) + 1e-9
                wgt = keep0 * np.minimum(1.0, k / np.maximum(np.abs(resid), 1e-12))
            s = s + p[:2]
        return np.clip(s, -self.max_shift, self.max_shift)


def _design(h: int, w: int, order: int) -> np.ndarray:
    v, u = np.mgrid[0:h, 0:w]
    u, v = (u / max(w - 1, 1)) * 2 - 1, (v / max(h - 1, 1)) * 2 - 1
    cols = [np.ones_like(u), u, v]
    if order >= 2:
        cols += [u * u, u * v, v * v]
    return np.stack([c.ravel() for c in cols], 1).astype(np.float64)


def _drift_surface(f: np.ndarray, valid: np.ndarray, design: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
    """Smooth background drift (sun, equipment warming) as a plane or quadratic surface, fitted robustly.

    Gas is local and large changes are rare in the rest of the image, so Huber weights (plus the pixels where gas was
    just detected, given in `exclude`) keep the plume out of the fit."""
    use = valid.ravel() if exclude is None else (valid & ~exclude).ravel()
    idx = np.flatnonzero(use)[::5]
    if idx.size < 3 * design.shape[1] * 10:                    # the gas covers (almost) everything: use all valid pixels
        idx = np.flatnonzero(valid.ravel())[::5]
    a, y = design[idx], f.ravel()[idx].astype(np.float64)
    wgt = np.ones(len(idx))
    p = np.zeros(design.shape[1])
    for _ in range(4):
        aw = a * wgt[:, None]
        try:
            p = np.linalg.solve(aw.T @ a + 1e-9 * np.eye(a.shape[1]), aw.T @ y)
        except np.linalg.LinAlgError:
            break
        r = y - a @ p
        k = 1.5 * 1.4826 * float(np.median(np.abs(r - np.median(r)))) + 1e-9
        wgt = np.minimum(1.0, k / np.maximum(np.abs(r), 1e-12))
    return (design @ p).reshape(f.shape).astype(np.float32)


def _align(img: np.ndarray, shift: np.ndarray) -> np.ndarray:
    if abs(shift[0]) < STAB_DEADBAND_PX and abs(shift[1]) < STAB_DEADBAND_PX:
        return img
    return ndi.shift(img, (-shift[0], -shift[1]), order=1, mode="nearest")


def _check_region(name: str, region, shape):
    if region is None:
        return None
    region = np.asarray(region, bool)
    if region.shape != shape:
        raise ValueError(f"{name}: la zona debe tener el tamaño de la imagen {shape}, no {region.shape}")
    if not region.any():
        raise ValueError(f"{name}: la zona está vacía")
    return region


def detect(frames: np.ndarray, fps: float, gas_free_s: float = 1.0, sigma_px: float = 2.0,
           window_s: float = 0.2, z_low: float = 2.5, z_high: float = 4.5,
           min_area: int = 40, valid: np.ndarray | None = None, *,
           background: str = "static", bg_tau_s: float = 30.0, bg_margin_px: int = 6, bg_hold_on_alarm: bool = True,
           stabilize: bool = False, max_shift_px: float = 6.0, roi: np.ndarray | None = None,
           polarity: str = "both", source_roi: np.ndarray | None = None, source_dilate_px: int | None = None,
           require_source: bool = False, drift_order: int = 0, auto_threshold: bool = False,
           keep_maps: bool = True, out_mask: np.ndarray | None = None) -> dict:
    """Detect gas in a fixed-camera sequence. See module docstring.

    frames      (T, H, W), any numeric dtype (grey levels or temperature); a memory map works too
    gas_free_s  length of the initial period known to be gas-free
    sigma_px    spatial smoothing (pixels)
    window_s    temporal averaging window (seconds)
    z_low/high  hysteresis thresholds, in noise sigmas
    min_area    smallest region kept (pixels)
    valid       (H, W) bool, pixels to use (default: all, minus saturated
                pixels when the input is 8-bit)

    Robust options: background, bg_tau_s, bg_margin_px, bg_hold_on_alarm, stabilize, max_shift_px, roi, polarity,
    source_roi, source_dilate_px, require_source, drift_order, auto_threshold, keep_maps, out_mask (see the module docstring).
    """
    if background not in ("static", "adaptive"):
        raise ValueError("background must be 'static' or 'adaptive'")
    if polarity not in ("both", "dark", "bright"):
        raise ValueError("polarity must be 'both', 'dark' or 'bright'")
    shape = tuple(np.shape(frames)[1:3])
    roi = _check_region("roi", roi, shape)
    source_roi = _check_region("source_roi", source_roi, shape)
    if require_source and source_roi is None:
        raise ValueError("require_source needs a source_roi")
    if source_dilate_px is None:                       # margin around the source zone: 2 % of the image width
        source_dilate_px = max(2, int(round(0.02 * shape[1])))
    if drift_order not in (0, 1, 2):
        raise ValueError("drift_order must be 0 (global level), 1 (plane) or 2 (quadratic surface)")
    options = dict(background=background, bg_tau_s=bg_tau_s, bg_margin_px=bg_margin_px, bg_hold_on_alarm=bg_hold_on_alarm,
                   stabilize=stabilize, max_shift_px=max_shift_px, roi=roi, polarity=polarity, source_roi=source_roi,
                   source_dilate_px=source_dilate_px, require_source=require_source, drift_order=drift_order,
                   auto_threshold=auto_threshold, keep_maps=keep_maps, out_mask=out_mask)
    if background == "adaptive" or stabilize or drift_order or auto_threshold or not keep_maps:
        return _detect_online(frames, fps, gas_free_s, sigma_px, window_s, z_low, z_high, min_area, valid, **options)

    x = np.asarray(frames, np.float32)
    t = x.shape[0]
    n_free = int(round(gas_free_s * fps))
    n_t = max(1, int(round(window_s * fps)))
    n_bg = max(3, int(0.6 * n_free))
    if n_free - n_bg - n_t < 2 or n_free >= t:
        raise ValueError("gas-free period too short for this frame rate / window")

    background_img = x[:n_bg].mean(0)
    if valid is None:
        valid = np.ones(background_img.shape, bool)
        if np.asarray(frames).dtype == np.uint8:
            valid = (background_img > 0.5) & (background_img < 254.5)
    if valid.mean() < 0.05:
        raise ValueError("almost every pixel is saturated: widen the level/span")
    weight = np.maximum(ndi.gaussian_filter(valid.astype(np.float32), sigma_px, mode="nearest"), 1e-3)

    # spatial smoothing that ignores invalid pixels (normalised convolution)
    filtered = np.empty_like(x)
    for i in range(t):
        d = np.where(valid, x[i] - background_img, 0.0)
        filtered[i] = ndi.gaussian_filter(d, sigma_px, mode="nearest") / weight
        filtered[i][~valid] = 0.0

    # noise of one smoothed frame, from the gas-free frames (plain median is safe there)
    free = filtered[n_bg:n_free][:, valid]
    free = free - np.median(free, axis=1, keepdims=True)
    scale = max(1.4826 * float(np.median(np.abs(free))), 1e-6)

    # global drift: mode of the smoothed difference, robust to the plume
    for i in range(t):
        filtered[i][valid] -= _mode(filtered[i][valid], scale)

    # causal temporal mean (the first frames use a shorter window)
    csum = np.cumsum(filtered, axis=0, dtype=np.float64)
    smooth = np.empty_like(filtered)
    for i in range(t):
        j = i - n_t
        total = csum[i] - (csum[j] if j >= 0 else 0.0)
        smooth[i] = total / min(i + 1, n_t)

    # noise of the filtered signal, from gas-free frames not used for the background
    ref = smooth[n_bg + n_t: n_free][:, valid]
    sigma = 1.4826 * float(np.median(np.abs(ref - np.median(ref))))
    sigma = max(sigma, 1e-6)
    z = smooth / sigma

    mask = np.zeros(x.shape, bool) if out_mask is None else out_mask
    tracker = _OriginTracker(source_roi, source_dilate_px) if require_source else None
    for i in range(n_free, t):
        m = _frame_mask(z[i], z_low, z_high, min_area, roi, polarity)
        mask[i] = tracker(m) if tracker is not None else m
    return dict(mask=mask, z=z.astype(np.float32), estimate=smooth, sigma=sigma,
                n_free=n_free, background=background_img, valid=valid)


def _detect_online(frames, fps, gas_free_s, sigma_px, window_s, z_low, z_high, min_area, valid, *, background,
                   bg_tau_s, bg_margin_px, bg_hold_on_alarm, stabilize, max_shift_px, roi, polarity, source_roi,
                   source_dilate_px, require_source, drift_order, auto_threshold, keep_maps, out_mask) -> dict:
    """Frame-by-frame detector: same arithmetic as the batch version plus the robust options.

    Needed by the adaptive background (it feeds the detections back), and it keeps memory use independent of the
    length of the video (`keep_maps=False`)."""
    t = len(frames)
    first = np.asarray(frames[0])
    h, w = first.shape
    is_u8 = first.dtype == np.uint8
    n_free = int(round(gas_free_s * fps))
    n_t = max(1, int(round(window_s * fps)))
    n_bg = max(3, int(0.6 * n_free))
    if n_free - n_bg - n_t < 2 or n_free >= t:
        raise ValueError("gas-free period too short for this frame rate / window")
    get = lambda i: np.asarray(frames[i], np.float32)          # noqa: E731

    # ---- learning period (vectorised, as in the batch version) ----------------------------------------------
    shifts = np.zeros((t, 2), np.float32)
    if stabilize:
        ref0 = get(0)
        est0 = ShiftEstimator(ref0, max_shift_px)
        rough = np.mean([ref0] + [_align(get(i), est0(get(i))) for i in range(1, n_bg)], axis=0).astype(np.float32)
        estimator = ShiftEstimator(rough, max_shift_px)
        learn = []
        for i in range(n_free):
            raw = get(i)
            shifts[i] = estimator(raw)
            learn.append(_align(raw, shifts[i]))
        x0 = np.stack(learn)
    else:
        estimator = None
        x0 = np.stack([get(i) for i in range(n_free)])
    bg = x0[:n_bg].mean(0)
    if valid is None:
        valid = np.ones((h, w), bool)
        if is_u8:
            valid = (bg > 0.5) & (bg < 254.5)
    valid = valid.copy()
    pix_noise = max(1.4826 * float(np.median(np.abs((x0[1] - x0[0])[valid]))) / np.sqrt(2.0), 1e-6)
    if stabilize:
        b = int(np.ceil(1.5 * np.abs(shifts[:n_free]).max())) + 2        # border refilled by the alignment
        valid[:b], valid[-b:], valid[:, :b], valid[:, -b:] = False, False, False, False
        # Pixels that are still unstable after alignment (sharp edges: a hundredth of a pixel is already more than the
        # noise we look for) are measured, not guessed: their temporal spread over the learning frames.
        spread = np.diff(x0, axis=0).std(axis=0) / np.sqrt(2.0)        # frame-to-frame: ignores slow drifts
        unstable = spread > STAB_SPREAD_FACTOR * pix_noise
        valid &= ~ndi.binary_dilation(unstable, iterations=1)
    if valid.mean() < 0.05:
        raise ValueError("almost every pixel is saturated: widen the level/span")
    weight = np.maximum(ndi.gaussian_filter(valid.astype(np.float32), sigma_px, mode="nearest"), 1e-3)

    def smooth_frame(xi, bgi):
        f = ndi.gaussian_filter(np.where(valid, xi - bgi, 0.0), sigma_px, mode="nearest") / weight
        f[~valid] = 0.0
        return f

    filt0 = np.stack([smooth_frame(x0[i], bg) for i in range(n_free)])
    free = filt0[n_bg:n_free][:, valid]
    free = free - np.median(free, axis=1, keepdims=True)
    scale = max(1.4826 * float(np.median(np.abs(free))), 1e-6)
    design = _design(h, w, drift_order) if drift_order else None

    def remove_drift(f, exclude=None):
        if design is None:
            f[valid] -= _mode(f[valid], scale)                     # global level: the most common value
        else:
            f -= np.where(valid, _drift_surface(f, valid, design, exclude), 0.0)
        return f

    for i in range(n_free):
        remove_drift(filt0[i])
    csum = np.cumsum(filt0, axis=0, dtype=np.float64)
    smooth0 = np.empty_like(filt0)
    for i in range(n_free):
        j = i - n_t
        smooth0[i] = (csum[i] - (csum[j] if j >= 0 else 0.0)) / min(i + 1, n_t)
    ref_s = smooth0[n_bg + n_t: n_free][:, valid]
    sigma = max(1.4826 * float(np.median(np.abs(ref_s - np.median(ref_s)))), 1e-6)
    z_scale = 1.0
    if auto_threshold:           # never alarm on what was seen during the gas-free period (heavy-tailed noise)
        seen = float(np.abs(ref_s / sigma).max())
        z_scale = max(1.0, AUTO_THRESHOLD_MARGIN * seen / z_high)
        z_low, z_high = z_low * z_scale, z_high * z_scale

    # shutter (NUC) detector: a sudden global jump between consecutive frames
    sub = np.flatnonzero(valid.ravel())[::13]
    steps = np.array([np.median((x0[i] - x0[i - 1]).ravel()[sub]) for i in range(1, n_free)])
    nuc_thr = max(8.0 * 1.4826 * float(np.median(np.abs(steps - np.median(steps)))), 0.3 * pix_noise)

    # ---- outputs ------------------------------------------------------------------------------------------------
    mask = np.zeros((t, h, w), bool) if out_mask is None else out_mask
    z_all = est_all = None
    if keep_maps:
        z_all, est_all = np.zeros((t, h, w), np.float32), np.zeros((t, h, w), np.float32)
        est_all[:n_free], z_all[:n_free] = smooth0, smooth0 / sigma
    zmax = np.zeros(t, np.float32)
    zmax[:n_free] = np.abs(smooth0 / sigma)[:, valid].max(1)
    alarm_arr = np.zeros(t, bool)
    nuc_events: list[int] = []
    tracker = _OriginTracker(source_roi, source_dilate_px) if require_source else None
    adaptive = background == "adaptive"
    alpha = 1.0 - np.exp(-1.0 / (fps * bg_tau_s))

    # ---- frame by frame -----------------------------------------------------------------------------------------
    ring = deque(filt0[n_free - n_t:], maxlen=n_t)
    rsum = np.sum(np.stack(list(ring)), axis=0, dtype=np.float64)
    prev_raw, prev_x, run = np.asarray(frames[n_free - 1]), x0[n_free - 1], 0
    for i in range(n_free, t):
        raw = np.asarray(frames[i])
        if np.array_equal(raw, prev_raw):                          # frozen image (shutter closed): nothing new
            mask[i], zmax[i], alarm_arr[i] = mask[i - 1], zmax[i - 1], alarm_arr[i - 1]
            if keep_maps:
                z_all[i], est_all[i] = z_all[i - 1], est_all[i - 1]
            continue
        xi = raw.astype(np.float32)
        if estimator is not None:
            busy = mask[i - 1]
            shifts[i] = estimator(xi, ndi.binary_dilation(busy, iterations=int(bg_margin_px)) if busy.any() else None)
            xi = _align(xi, shifts[i])
        f = smooth_frame(xi, bg)
        busy_prev = mask[i - 1]
        remove_drift(f, ndi.binary_dilation(busy_prev, iterations=int(bg_margin_px)) if (design is not None and busy_prev.any()) else None)
        old = ring[0]
        ring.append(f)
        rsum += f
        rsum -= old
        smooth = (rsum / n_t).astype(np.float32)
        z = smooth / sigma
        raw_mask = _frame_mask(z, z_low, z_high, min_area, roi, polarity)
        acc = tracker(raw_mask) if tracker is not None else raw_mask
        mask[i] = acc
        zmax[i] = float(np.abs(z[valid]).max())
        if keep_maps:
            z_all[i], est_all[i] = z, smooth
        run = run + 1 if acc.any() else 0
        alarm_arr[i] = run >= 3

        if adaptive:
            quiet = sub if not mask[i - 1].any() else sub[~ndi.binary_dilation(mask[i - 1], iterations=int(bg_margin_px)).ravel()[sub]]
            step = float(np.median((xi - prev_x).ravel()[quiet])) if quiet.size > 50 else 0.0   # gas must not look like a shutter
            if abs(step) > nuc_thr:
                nuc_events.append(i)                       # diagnostic: a sudden global jump, probably the shutter
            protect = ndi.binary_dilation(raw_mask | acc, iterations=int(bg_margin_px)) if (raw_mask.any() or acc.any()) else None
            # Update everywhere unless an alarm is active, then freeze the background completely. (Protecting only the
            # pixels that look like gas is a trap: a false detection would never be learned away and would grow.)
            if not (bg_hold_on_alarm and alarm_arr[i]):
                upd = valid if (protect is None or bg_hold_on_alarm) else (valid & ~protect)
                bg[upd] += alpha * (xi[upd] - bg[upd])

        prev_raw, prev_x = raw, xi

    return dict(mask=mask, z=z_all, estimate=est_all, sigma=sigma, n_free=n_free, background=bg, valid=valid,
                zmax=zmax, alarm=alarm_arr, shifts=shifts, nuc_events=nuc_events, z_scale=z_scale)


def min_gas_free_s(fps: float, window_s: float = 0.2) -> float:
    """Shortest gas-free period (s) that `detect` accepts: 60 % trains the background, the rest measures the noise."""
    n_t = max(1, int(round(window_s * fps)))
    n = 1
    while n - max(3, int(0.6 * n)) - n_t < 2:
        n += 1
    return n / fps


def alarm(mask: np.ndarray, persist: int = 3) -> np.ndarray:
    """Frame-level alarm: gas present in `persist` consecutive frames."""
    present = mask.reshape(mask.shape[0], -1).any(1)
    out = np.zeros_like(present)
    run = 0
    for i, p in enumerate(present):
        run = run + 1 if p else 0
        out[i] = run >= persist
    return out


def scores(pred: np.ndarray, truth: np.ndarray) -> dict:
    """Pixel scores of a predicted mask against a reference mask."""
    tp = float(np.logical_and(pred, truth).sum())
    fp = float(np.logical_and(pred, ~truth).sum())
    fn = float(np.logical_and(~pred, truth).sum())
    return dict(iou=tp / (tp + fp + fn) if tp + fp + fn else float("nan"),
                recall=tp / (tp + fn) if tp + fn else float("nan"),
                precision=tp / (tp + fp) if tp + fp else float("nan"))


def preview_frames(frames_u8: np.ndarray, pred: np.ndarray, truth: np.ndarray | None, step: int = 2) -> list:
    """RGB frames (PIL): detected region outlined in orange, reference mask (if any) in blue.

    The reference should use the same threshold as the detector (see main()),
    otherwise the two outlines answer different questions."""
    from PIL import Image

    def edge(m):
        return m & ~ndi.binary_erosion(m)

    out = []
    for i in range(0, len(frames_u8), step):
        rgb = np.repeat(frames_u8[i][:, :, None], 3, 2)
        if truth is not None:
            rgb[edge(truth[i])] = (42, 120, 214)
        rgb[edge(pred[i])] = (235, 104, 52)
        out.append(Image.fromarray(rgb))
    return out


def save_preview(path: Path, frames_u8: np.ndarray, pred: np.ndarray, truth: np.ndarray | None,
                 fps: float, step: int = 2):
    """GIF of `preview_frames`."""
    out = preview_frames(frames_u8, pred, truth, step)
    out[0].save(path, save_all=True, append_images=out[1:], duration=int(1000 * step / fps), loop=0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sequence", help=".npz written by ogi_sim.py")
    ap.add_argument("--input", default="u8", choices=["u8", "temp"],
                    help="u8 = 8-bit video, temp = 16-bit radiometric matrix")
    ap.add_argument("--gas-free-s", type=float, default=None,
                    help="initial gas-free period (default: 80%% of the time before the leak)")
    ap.add_argument("--z-low", type=float, default=2.5)
    ap.add_argument("--z-high", type=float, default=4.5)
    ap.add_argument("--preview", action="store_true", help="write a GIF next to the sequence")
    ap.add_argument("--background", default="static", choices=["static", "adaptive"], help="adaptive: follows slow changes")
    ap.add_argument("--bg-tau-s", type=float, default=30.0, help="adaptive background time constant (s)")
    ap.add_argument("--drift-order", type=int, default=0, choices=[0, 1, 2], help="0 level, 1 plane, 2 quadratic surface")
    ap.add_argument("--stabilize", action="store_true", help="remove camera vibration")
    ap.add_argument("--polarity", default="both", choices=["both", "dark", "bright"])
    ap.add_argument("--roi", default=None, help='watch zone, polygon "x,y; x,y; ..." (fractions 0-1 or pixels)')
    ap.add_argument("--require-source", action="store_true", help="only detections that start at the vent (simulated sequences)")
    ap.add_argument("--auto-threshold", action="store_true", help="raise thresholds to clear the noise seen while learning")
    args = ap.parse_args()

    path = Path(args.sequence)
    d = np.load(path)
    meta = json.loads(str(d["meta"]))
    fps = meta["fps"]
    frames = d["u8"] if args.input == "u8" else d["temp_ck"].astype(np.float32) / 100.0
    gas_free = args.gas_free_s if args.gas_free_s is not None else 0.8 * meta["leak_on_s"]

    import regions
    shape = frames.shape[1:3]
    res = detect(frames, fps, gas_free_s=gas_free, z_low=args.z_low, z_high=args.z_high, background=args.background,
                 bg_tau_s=args.bg_tau_s, drift_order=args.drift_order, stabilize=args.stabilize, polarity=args.polarity,
                 roi=regions.polygon_mask(args.roi, shape) if args.roi else None,
                 source_roi=regions.source_from_meta(meta, shape) if args.require_source else None,
                 require_source=args.require_source, auto_threshold=args.auto_threshold)
    pred, valid = res["mask"], res["valid"]
    # kelvin per input unit: lets us cut the estimate at a physical contrast
    unit_k = meta["k_per_dn"] if args.input == "u8" else 1.0
    netd = meta["netd_k"]
    on = int(round(meta["leak_on_s"] * fps))
    al = alarm(pred)
    first = np.flatnonzero(al[on:])
    print(f"{path.name}  input={args.input}")
    print(f"  noise after filtering       : {res['sigma'] * unit_k * 1e3:.1f} mK "
          f"(single-pixel NETD {netd * 1e3:.0f} mK)")
    print(f"  false alarm before the leak : {'YES' if al[res['n_free']:on].any() else 'no'}")
    print(f"  detection delay             : "
          f"{'not detected' if not len(first) else f'{first[0] / fps:.2f} s after the valve opens'}")
    contrast = np.abs(d["contrast"].astype(np.float32))
    last = slice(len(frames) - int(fps), len(frames))            # last second
    gas_free_px = (contrast[last] < 0.002) & valid
    print(f"  gas-free pixels flagged     : {100 * (pred[last] & gas_free_px).sum() / max(gas_free_px.sum(), 1):.2f} %")
    # threshold the detector itself applies, in kelvin (z_low noise sigmas)
    det_thr_k = args.z_low * res["sigma"] * unit_k
    truth_det = (contrast > det_thr_k) & valid
    sc = scores(pred[last], truth_det[last])
    print(f"  mask vs truth at its own {det_thr_k * 1e3:.0f} mK : IoU {sc['iou']:.2f}  recall {sc['recall']:.2f}  "
          f"precision {sc['precision']:.2f}")
    estimate = np.abs(res["estimate"][last]) * unit_k
    for mult in (1, 2, 3):
        sc = scores((estimate > mult * netd) & valid, (contrast[last] > mult * netd) & valid)
        print(f"  region above {mult}x NETD        : IoU {sc['iou']:.2f}  recall {sc['recall']:.2f}  "
              f"precision {sc['precision']:.2f}")
    if args.preview:
        out = path.with_name(path.stem + f"_detect_{args.input}.gif")
        save_preview(out, d["u8"], pred, truth_det, fps)
        print(f"  preview -> {out}")


if __name__ == "__main__":
    main()
