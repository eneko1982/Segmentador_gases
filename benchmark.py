"""
benchmark.py - Measure gas_detect.py against the simulator's ground truth.

Sweeps the leak flow rate over three scenes and scores the detector on both
camera outputs (8-bit video and 16-bit radiometric matrix).

    python benchmark.py --out bench            # writes bench/results.csv

Per sequence it reports
    alarm        was the leak detected, and how long after the valve opened
    false_alarm  did the detector fire while the valve was still closed
    fp_free_pct  % of truly gas-free pixels flagged (last second)
    iou_det      detector mask vs the true region above the detector's own
                 threshold (2.5 sigma of the filtered noise)
    iou_1x/2x/3x overlap between "estimate above N x NETD" and the true
                 region above N x NETD (last second)
"""
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from gas_detect import alarm, detect, scores
from ogi_sim import Config, simulate

LEAK_ON_S = 2.0
DURATION_S = 5.0
FLOWS_G_S = [0.001, 0.003, 0.01, 0.03, 0.1, 0.3]
SCENES = {
    # name: (label, Config overrides)
    "hot_plate_dT48": dict(scene="hot_plate", t_bg_c=70.0),
    "hot_plate_dT5": dict(scene="hot_plate", t_bg_c=27.0, level_c=25.0, span_c=8.0),
    "cold_sky": dict(scene="cold_sky", t_bg_c=-25.0),
}


def run_one(job):
    scene, flow, seed = job
    cfg = Config(flow_g_s=flow, leak_on_s=LEAK_ON_S, duration_s=DURATION_S, seed=seed, **SCENES[scene])
    seq = simulate(cfg)
    meta = json.loads(seq["meta"])
    fps, netd = meta["fps"], meta["netd_k"]
    contrast = np.abs(seq["contrast"].astype(np.float32))
    n = len(contrast)
    on = int(round(LEAK_ON_S * fps))
    last = slice(n - int(fps), n)
    rows = []
    for inp in ("u8", "temp"):
        frames = seq["u8"] if inp == "u8" else seq["temp_ck"].astype(np.float32) / 100.0
        unit_k = meta["k_per_dn"] if inp == "u8" else 1.0
        res = detect(frames, fps, gas_free_s=0.8 * LEAK_ON_S)
        pred, valid = res["mask"], res["valid"]
        al = alarm(pred)
        first = np.flatnonzero(al[on:])
        free = (contrast[last] < 0.002) & valid
        est = np.abs(res["estimate"][last]) * unit_k
        row = dict(scene=scene, flow_g_s=flow, seed=seed, input=inp,
                   mk_per_level=unit_k * 1e3 if inp == "u8" else 0.0,
                   peak_contrast_mk=float(contrast[last].max()) * 1e3,
                   noise_mk=res["sigma"] * unit_k * 1e3,
                   alarm=int(len(first) > 0),
                   delay_s=float(first[0] / fps) if len(first) else float("nan"),
                   false_alarm=int(al[res["n_free"]:on].any()),
                   fp_free_pct=100.0 * float((pred[last] & free).sum()) / max(int(free.sum()), 1),
                   detected_px=float(pred[last].sum()) / int(fps))
        # detector mask vs truth at the detector's own threshold (z_low sigmas)
        thr_k = 2.5 * res["sigma"] * unit_k
        truth_det = (contrast[last] > thr_k) & valid
        sc = scores(pred[last], truth_det)
        row.update(det_thr_mk=thr_k * 1e3, true_px_det=float(truth_det.sum()) / int(fps),
                   iou_det=sc["iou"], recall_det=sc["recall"], precision_det=sc["precision"])
        for mult in (1, 2, 3):
            truth = (contrast[last] > mult * netd) & valid
            sc = scores((est > mult * netd) & valid, truth)
            row[f"true_px_{mult}x"] = float(truth.sum()) / int(fps)
            row[f"iou_{mult}x"] = sc["iou"]
            row[f"recall_{mult}x"] = sc["recall"]
            row[f"precision_{mult}x"] = sc["precision"]
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="bench")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(s, f, seed) for s in SCENES for f in FLOWS_G_S for seed in range(args.seeds)]
    rows = []
    with ProcessPoolExecutor(args.workers) as pool:
        for k, res in enumerate(pool.map(run_one, jobs), 1):
            rows.extend(res)
            print(f"[{k}/{len(jobs)}] {res[0]['scene']} {res[0]['flow_g_s']} g/s seed {res[0]['seed']}", flush=True)
    with open(out / "results.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out / 'results.csv'} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
