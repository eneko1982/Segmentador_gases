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
"""
from __future__ import annotations

import argparse
import json
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


def detect(frames: np.ndarray, fps: float, gas_free_s: float = 1.0, sigma_px: float = 2.0,
           window_s: float = 0.2, z_low: float = 2.5, z_high: float = 4.5,
           min_area: int = 40, valid: np.ndarray | None = None) -> dict:
    """Detect gas in a fixed-camera sequence. See module docstring.

    frames      (T, H, W), any numeric dtype (grey levels or temperature)
    gas_free_s  length of the initial period known to be gas-free
    sigma_px    spatial smoothing (pixels)
    window_s    temporal averaging window (seconds)
    z_low/high  hysteresis thresholds, in noise sigmas
    min_area    smallest region kept (pixels)
    valid       (H, W) bool, pixels to use (default: all, minus saturated
                pixels when the input is 8-bit)
    """
    x = np.asarray(frames, np.float32)
    t = x.shape[0]
    n_free = int(round(gas_free_s * fps))
    n_t = max(1, int(round(window_s * fps)))
    n_bg = max(3, int(0.6 * n_free))
    if n_free - n_bg - n_t < 2 or n_free >= t:
        raise ValueError("gas-free period too short for this frame rate / window")

    background = x[:n_bg].mean(0)
    if valid is None:
        valid = np.ones(background.shape, bool)
        if np.asarray(frames).dtype == np.uint8:
            valid = (background > 0.5) & (background < 254.5)
    if valid.mean() < 0.05:
        raise ValueError("almost every pixel is saturated: widen the level/span")
    weight = np.maximum(ndi.gaussian_filter(valid.astype(np.float32), sigma_px, mode="nearest"), 1e-3)

    # spatial smoothing that ignores invalid pixels (normalised convolution)
    filtered = np.empty_like(x)
    for i in range(t):
        d = np.where(valid, x[i] - background, 0.0)
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

    mask = np.zeros(x.shape, bool)
    for i in range(n_free, t):
        mask[i] = (_hysteresis(z[i], z_low, z_high, min_area)
                   | _hysteresis(-z[i], z_low, z_high, min_area))
    return dict(mask=mask, z=z.astype(np.float32), estimate=smooth, sigma=sigma,
                n_free=n_free, background=background, valid=valid)


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
    args = ap.parse_args()

    path = Path(args.sequence)
    d = np.load(path)
    meta = json.loads(str(d["meta"]))
    fps = meta["fps"]
    frames = d["u8"] if args.input == "u8" else d["temp_ck"].astype(np.float32) / 100.0
    gas_free = args.gas_free_s if args.gas_free_s is not None else 0.8 * meta["leak_on_s"]

    res = detect(frames, fps, gas_free_s=gas_free, z_low=args.z_low, z_high=args.z_high)
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
