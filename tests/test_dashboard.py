"""Cálculo, visor y exportación del panel (sin interfaz)."""
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exports  # noqa: E402
import ogi_sim as sim  # noqa: E402
import viewer  # noqa: E402
from analysis import Analysis, clean  # noqa: E402
from gas_detect import detect  # noqa: E402


@pytest.fixture(scope="module")
def case():
    cfg = sim.Config(width=96, height=72, duration_s=3.0, leak_on_s=1.5, flow_g_s=0.1, fps=20.0)
    seq = sim.simulate(cfg)
    res = detect(seq["u8"], 20.0, gas_free_s=1.2)
    return seq, res, Analysis(seq, res, "u8", 2.5)


def test_masks_have_the_chosen_thresholds(case):
    seq, _, an = case
    sim_mask = an.truth({"kind": "sim"})
    one_x = an.truth({"kind": "1x"})
    custom = an.truth({"kind": "custom", "mk": 70.0})
    assert np.array_equal(custom, an.truth({"kind": "2x"}))             # 2 × 35 mK = 70 mK
    assert one_x.sum() <= an.truth({"kind": "det"}).sum()               # umbral más alto -> menos píxeles
    assert sim_mask.shape == seq["u8"].shape
    assert np.array_equal(an.pred({"kind": "mask"}), an.res["mask"])
    assert an.pred({"kind": "1x"}).sum() < an.pred({"kind": "det"}).sum()


def test_scores_and_series_are_consistent(case):
    _, _, an = case
    ts, ps = {"kind": "det"}, {"kind": "mask"}
    s = an.series(an.truth(ts), an.pred(ps))
    assert len(s["iou"]) == an.n and np.nanmax(s["iou"]) > 0.8
    assert (s["tp_px"] + s["fp_px"] == s["detected_px"]).all()
    sm = an.summary(ts, ps, "last")
    assert sm["selected_masks"]["iou"] > 0.8 and sm["false_alarm_before_leak"] is False
    assert len(sm["table"]) == 4
    json.dumps(clean(sm))                                                # serializable (NaN -> null)
    # different windows give different accumulations
    assert an.summary(ts, ps, "all")["selected_masks"]["iou"] != sm["selected_masks"]["iou"]


def test_compose_frame_styles_and_scale():
    base = np.full((20, 30), 128, np.uint8)
    t = np.zeros((20, 30), bool); t[5:15, 5:15] = True
    p = np.zeros((20, 30), bool); p[8:18, 8:18] = True
    for style in viewer.STYLES:
        out = viewer.compose_frame(base, t, p, style, 0.9, 1, 1)
        assert out.shape == (20, 30, 3) and (out != 128).any()
    assert viewer.compose_frame(base, t, p, "contour", 0.9, 1, 4).shape == (80, 120, 3)
    assert np.array_equal(viewer.compose_frame(base, None, None), np.repeat(base[:, :, None], 3, 2))
    err = viewer.compose_frame(base, t, p, "error", 1.0, 1, 1)
    assert tuple(err[10, 10]) != tuple(err[6, 6]) != tuple(err[16, 16])      # acierto, FN y FP con colores distintos


def test_player_html_is_self_contained_and_valid_javascript(case):
    seq, _, an = case
    truth, pred = an.truth({"kind": "det"}), an.pred({"kind": "mask"})
    payload = viewer.build_payload({"Cámara": seq["u8"]}, truth, pred, an.fps, 2, an.leak_on_s, 1.2)
    assert payload["n"] == 30 and payload["views"][0]["frames"][0].startswith("data:image/png;base64,")
    html = viewer.player_html(payload, 480)
    assert "__DATA__" not in html and "__ZOOM__" not in html and viewer.player_height(payload) > 300
    if shutil.which("node"):
        script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
        path = Path("/tmp/_player_check.js")
        path.write_text(script)
        assert subprocess.run(["node", "--check", str(path)], capture_output=True).returncode == 0


def test_export_package_contents(case):
    seq, _, an = case
    ts, ps = {"kind": "det"}, {"kind": "mask"}
    truth, pred = an.truth(ts), an.pred(ps)
    dp = dict(input="u8", gas_free_s=1.2, z_low=2.5, z_high=4.5, sigma_px=2.0, window_s=0.2, min_area=40)
    opts = dict(style="error", opacity=0.8, thick=2, scale=2, vmax=100.0, views=["cam", "est", "real"], frames=[40, 55],
                report=True, gif=True, gif_stride=4, all_frames=True, frames_stride=20, npz_seq=False, npz_det=True)
    data = exports.build_package(seq, an, truth, pred, ts, ps, "last", dp, opts)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert {"resumen.json", "serie_temporal.csv", "informe.html", "figuras/series.png", "figuras/galeria.png",
            "video/superposicion.gif", "datos/deteccion.npz", "figuras/fotogramas/cam_0041.png",
            "figuras/fotogramas/est_0056.png", "figuras/fotogramas/real_0041.png", "fotogramas_png/cam_0001.png"} <= names
    assert "datos/secuencia.npz" not in names
    summary = json.loads(z.read("resumen.json"))
    assert summary["summary"]["selected_masks"]["iou"] > 0.8 and summary["detector_parameters"]["z_low"] == 2.5
    assert z.read("serie_temporal.csv").decode().splitlines()[0].startswith("frame,t_s,")
    assert b"Informe del detector" in z.read("informe.html")
    arrays = np.load(io.BytesIO(z.read("datos/deteccion.npz")))
    assert arrays["mascara_detectada"].shape == seq["u8"].shape and "mascara_real" in arrays.files
