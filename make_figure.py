"""
make_figure.py - Detector vs ground truth with a consistent threshold.

Simulates one scene at several flow rates and draws, for the last frame:
    1. camera image (8 bit)
    2. what the detector computes (estimated change, mK)
    3. orange = estimate above 1x NETD,   blue = true change above 1x NETD
    4. orange = detector mask,            blue = true change above the detector's own
                                          threshold (z_low sigmas of the filtered noise)

    python make_figure.py --out docs/figures/detector_same_threshold.png
"""
from __future__ import annotations

import argparse
import json

import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage as ndi

from benchmark import DURATION_S, LEAK_ON_S, SCENES
from gas_detect import detect, scores
from ogi_sim import Config, simulate

ORANGE, BLUE = "#eb6834", "#2a78d6"
Z_LOW = 2.5


def contour(ax, mask, colour):
    ax.contour(mask.astype(float), levels=[0.5], colors=colour, linewidths=1.4)


def build_figure(scene: str = "hot_plate_dT5", flows=(0.003, 0.03, 0.3), seed: int = 0):
    """Simulate `scene` at each flow rate and return the 4-row comparison figure."""
    flows = list(flows)
    fig, axes = plt.subplots(4, len(flows), figsize=(4.2 * len(flows) + 2.2, 13), squeeze=False)
    for j, flow in enumerate(flows):
        cfg = Config(flow_g_s=flow, leak_on_s=LEAK_ON_S, duration_s=DURATION_S, seed=seed, **SCENES[scene])
        seq = simulate(cfg)
        meta = json.loads(seq["meta"])
        fps, netd, k = meta["fps"], meta["netd_k"], meta["k_per_dn"]
        res = detect(seq["u8"], fps, gas_free_s=0.8 * LEAK_ON_S)
        valid = res["valid"]
        i = len(seq["u8"]) - 1
        est = res["estimate"][i] * k                                   # kelvin
        con = seq["contrast"][i].astype(np.float32)
        det_thr = Z_LOW * res["sigma"] * k
        img = seq["u8"][i]

        # panel masks
        est_1x, true_1x = (np.abs(est) > netd) & valid, (np.abs(con) > netd) & valid
        mask, true_det = res["mask"][i], (np.abs(con) > det_thr) & valid
        s1, s2 = scores(est_1x, true_1x), scores(mask, true_det)

        for r in range(4):
            axes[r, j].set_xticks([]); axes[r, j].set_yticks([])
        axes[0, j].set_title(f"{flow:g} g/s\ncambio máximo en un píxel: {1e3 * np.abs(con).max():.0f} mK", fontsize=10)
        axes[0, j].imshow(img, cmap="gray", vmin=0, vmax=255)
        im = axes[1, j].imshow(est * 1e3, cmap="RdBu_r", vmin=-60, vmax=60)
        for r, (pred, truth, sc, note) in enumerate([
                (est_1x, true_1x, s1, f"umbral {netd * 1e3:.0f} mK en ambas"),
                (mask, true_det, s2, f"umbral {det_thr * 1e3:.0f} mK en ambas")], start=2):
            ax = axes[r, j]
            ax.imshow(img, cmap="gray", vmin=0, vmax=255)
            if truth.any():
                contour(ax, truth, BLUE)
            if pred.any():
                contour(ax, pred, ORANGE)
            iou = "—" if np.isnan(sc["iou"]) else f"{sc['iou']:.2f}"
            ax.set_xlabel(f"{note}\nIoU {iou}" + ("   (no hay gas sobre el umbral)" if not truth.any() else ""),
                          fontsize=9)
    labels = ["1. Imagen de la cámara\n(8 bit, último fotograma)", "2. Lo que calcula el detector\n(cambio respecto al fondo, mK)",
              "3. Mismo umbral = 1× NETD\n(35 mK) en detector y verdad", "4. Máscara del detector frente a la\nverdad a su propio umbral (2,5σ)"]
    for r, t in enumerate(labels):
        axes[r, 0].set_ylabel(t, fontsize=10, rotation=0, ha="right", va="center", labelpad=10)
    cax = fig.add_axes([0.93, 0.50, 0.012, 0.20])
    fig.colorbar(im, cax=cax, label="mK")
    fig.suptitle(f"Detector clásico, escena {scene} (ΔT = 5 K): misma frontera para detector y verdad", fontsize=13)
    fig.legend(handles=[plt.Line2D([], [], color=ORANGE, lw=2, label="Detectado por el algoritmo"),
                        plt.Line2D([], [], color=BLUE, lw=2, label="Región real (simulador) con el mismo umbral")],
               loc="lower center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0.08, 0.03, 0.92, 0.96))
    return fig


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="detector_same_threshold.png")
    ap.add_argument("--scene", default="hot_plate_dT5", choices=list(SCENES))
    ap.add_argument("--flows", type=float, nargs="+", default=[0.003, 0.03, 0.3])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    build_figure(args.scene, args.flows, args.seed).savefig(args.out, dpi=110)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
