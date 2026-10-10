"""
benchmark_robust.py - ¿Qué perfil del detector resiste cada perturbación del mundo real?

Cada escenario se simula con y sin fuga (0,1 g/s) y se pasa por cada perfil de `core.DETECTOR_PROFILES`.
Mide, para cada pareja escenario × perfil:
    falsa_alarma  ¿saltó la alarma antes de que se abriera la fuga? (simulación SIN gas; 0 = nunca, 1 = siempre)
    iou           solapamiento con la verdad en el último segundo (simulación CON gas)

    python benchmark_robust.py --out bench_robust        # escribe bench_robust/robustness.csv y una tabla
"""
from __future__ import annotations

import argparse
import json
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import core
import ogi_sim as sim
from gas_detect import alarm

warnings.simplefilter("ignore")
LEAK_ON_S, DURATION_S, FLOW = 3.0, 10.0, 0.1
HARD = {k: v for k, v in sim.load_preset("real_world_hard").items() if k not in ("scene", "t_bg_c", "duration_s", "leak_on_s", "flow_g_s")}
SCENARIOS = {
    "ninguna": {},
    "sol rápido": dict(bg_change_k=0.5, bg_change_period_s=8.0, bg_change_shape="ramp", bg_change_spatial=0.7),
    "sol lento": dict(bg_change_k=0.1, bg_change_period_s=10.0, bg_change_shape="ramp", bg_change_spatial=0.7),
    "nube": dict(cloud_k=-1.0, cloud_size_m=0.35, cloud_speed_ms=0.12),
    "personas": dict(object_count=2, object_size_m=0.12, object_speed_ms=0.2, object_start_s=3.0, object_gap_s=2.0),
    "vibración": dict(vibration_px=1.2),
    "obturador": dict(nuc_period_s=3.5, nuc_step_k=0.1, nuc_fpn_change=0.4, nuc_freeze_frames=2),
    "compresión": dict(compression_quality=25),
    "todo (duro)": HARD,
}


def one(job):
    scenario, flow, seed = job
    cfg = sim.apply_overrides(sim.Config(), dict(scene="hot_plate", t_bg_c=70.0, duration_s=DURATION_S, leak_on_s=LEAK_ON_S,
                                                  fps=20.0, flow_g_s=flow, seed=seed, **SCENARIOS[scenario]))
    seq = sim.simulate(sim.half_resolution(cfg))
    fps = json.loads(seq["meta"])["fps"]
    on = int(LEAK_ON_S * fps)
    rows = []
    for name, prof in core.DETECTOR_PROFILES.items():
        an, msg, _ = core.run_detection(seq, dict(prof["params"]))
        if an is None:
            rows.append((scenario, flow, seed, name, np.nan, np.nan, np.nan))
            continue
        m, n_free = an.res["mask"], an.res["n_free"]
        false_alarm = float(alarm(m)[n_free:on].any())
        if flow > 0:
            truth = seq["mask"] & an.valid
            sl = slice(-int(fps), None)
            tp, fp, fn = (m[sl] & truth[sl]).sum(), (m[sl] & ~truth[sl]).sum(), (~m[sl] & truth[sl]).sum()
            iou = float(tp / max(tp + fp + fn, 1))
        else:
            iou = np.nan
        rows.append((scenario, flow, seed, name, false_alarm, iou, float(m[n_free:on].sum())))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="bench_robust")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    jobs = [(s, f, seed) for s in SCENARIOS for f in (0.0, FLOW) for seed in range(args.seeds)]
    rows = []
    with ProcessPoolExecutor(args.workers) as pool:
        for k, r in enumerate(pool.map(one, jobs), 1):
            rows.extend(r)
            print(f"[{k}/{len(jobs)}] {r[0][0]} {r[0][1]} g/s semilla {r[0][2]}", flush=True)
    df = pd.DataFrame(rows, columns=["escenario", "flow_g_s", "seed", "perfil", "falsa_alarma", "iou", "px_falsos"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "robustness.csv", index=False)
    order = list(core.DETECTOR_PROFILES)
    fa = df[df.flow_g_s == 0].groupby(["escenario", "perfil"]).falsa_alarma.mean().unstack()[order].loc[list(SCENARIOS)]
    iou = df[df.flow_g_s > 0].groupby(["escenario", "perfil"]).iou.mean().unstack()[order].loc[list(SCENARIOS)]
    pd.set_option("display.width", 250)
    print("\nFALSAS ALARMAS (sin fuga)\n", fa.round(2), "\n\nIoU último segundo (con fuga)\n", iou.round(2))
    print("wrote", out / "robustness.csv")


if __name__ == "__main__":
    main()
