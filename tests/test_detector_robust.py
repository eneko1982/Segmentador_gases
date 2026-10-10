"""Opciones robustas del detector: cada una se comprueba contra la perturbación para la que existe."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402
import gas_detect as gd  # noqa: E402
import ogi_sim as sim  # noqa: E402
import regions  # noqa: E402

FPS = 20.0
# Mismo campo de visión que el benchmark (1,9 m × 1,4 m) con una imagen pequeña: así la pluma no llena toda la imagen.
GEOM = dict(width=96, height=72, m_per_px=0.02, psf_sigma_px=0.35, pipe_half_px=1)
BASE = dict(**GEOM, duration_s=8.0, leak_on_s=3.0, fps=FPS, flow_g_s=0.1, seed=0)


def make(**kw):
    seq = sim.simulate(sim.Config(**{**BASE, **kw}))
    return seq, json.loads(seq["meta"])


def iou_last_second(res, seq):
    truth = seq["mask"] & res["valid"]
    m, sl = res["mask"], slice(-int(FPS), None)
    tp, fp, fn = (m[sl] & truth[sl]).sum(), (m[sl] & ~truth[sl]).sum(), (~m[sl] & truth[sl]).sum()
    return tp / max(tp + fp + fn, 1)


@pytest.fixture(scope="module")
def clean():
    seq = sim.simulate(sim.Config(width=96, height=72, duration_s=4.0, leak_on_s=1.5, fps=FPS, flow_g_s=0.1, seed=2))   # geometría original (huellas)
    return seq, gd.detect(seq["u8"], FPS, gas_free_s=1.2)


def test_default_detector_is_exactly_the_original(clean):
    """Huellas del detector anterior a las opciones robustas."""
    _, res = clean
    assert hashlib.sha256(res["mask"].tobytes()).hexdigest()[:16] == "292b770ce6e6b7e1"
    assert hashlib.sha256(res["z"].tobytes()).hexdigest()[:16] == "89f467cf10f1d23c"
    assert res["sigma"] == pytest.approx(0.07110356031507253, rel=1e-12)


def test_streaming_mode_gives_the_same_masks_without_keeping_maps(clean):
    seq, res = clean
    on = gd.detect(seq["u8"], FPS, gas_free_s=1.2, keep_maps=False)
    assert on["z"] is None and on["estimate"] is None
    assert np.array_equal(on["mask"], res["mask"])
    assert np.allclose(on["zmax"][30:], np.abs(res["z"])[:, res["valid"]].max(1)[30:], rtol=1e-3)


def test_masks_can_be_written_straight_to_disk(clean, tmp_path):
    seq, res = clean
    out = np.lib.format.open_memmap(tmp_path / "mask.npy", mode="w+", dtype=bool, shape=seq["u8"].shape)
    gd.detect(seq["u8"], FPS, gas_free_s=1.2, keep_maps=False, out_mask=out)
    out.flush()
    assert np.array_equal(np.load(tmp_path / "mask.npy"), res["mask"])


def test_watch_zone_limits_detections(clean):
    seq, res = clean
    left = np.zeros((72, 96), bool)
    left[:, :48] = True
    r = gd.detect(seq["u8"], FPS, gas_free_s=1.2, roi=left)
    assert not r["mask"][:, :, 48:].any() and r["mask"][:, :, :48].sum() > 0


def test_polarity_keeps_only_the_expected_sign(clean):
    seq, res = clean                                                  # placa caliente: el gas se ve MÁS OSCURO
    dark = gd.detect(seq["u8"], FPS, gas_free_s=1.2, polarity="dark")
    bright = gd.detect(seq["u8"], FPS, gas_free_s=1.2, polarity="bright")
    assert (dark["mask"] & ~res["mask"]).sum() == 0 and dark["mask"].sum() > 0.95 * res["mask"].sum()
    assert bright["mask"].sum() < 0.05 * dark["mask"].sum()


def test_invalid_options_are_rejected(clean):
    seq, _ = clean
    for kw in (dict(background="x"), dict(polarity="x"), dict(drift_order=3), dict(require_source=True),
               dict(roi=np.zeros((72, 96), bool)), dict(roi=np.ones((10, 10), bool))):
        with pytest.raises(ValueError):
            gd.detect(seq["u8"], FPS, gas_free_s=1.2, **kw)


def test_stabilisation_measures_the_vibration_and_removes_false_detections():
    seq, _ = make(vibration_px=1.5)
    res = gd.detect(seq["u8"], FPS, gas_free_s=2.4, stabilize=True)
    truth = seq["shift_px"].astype(float)
    est = res["shifts"].astype(float)
    off = (est[:48] - truth[:48]).mean(0)
    assert np.sqrt(((est - truth - off) ** 2).mean()) < 0.3                      # px, con 1,5 px de vibración
    assert np.corrcoef(est[:, 0], truth[:, 0])[0, 1] > 0.95
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    on = int(3.0 * FPS)
    assert res["mask"][res["n_free"]:on].sum() < 0.05 * plain["mask"][plain["n_free"]:on].sum()
    assert iou_last_second(res, seq) > iou_last_second(plain, seq) + 0.1


def test_static_scene_is_not_hurt_by_stabilisation():
    seq, _ = make()
    plain, stab = gd.detect(seq["u8"], FPS, gas_free_s=2.4), gd.detect(seq["u8"], FPS, gas_free_s=2.4, stabilize=True)
    assert abs(iou_last_second(plain, seq) - iou_last_second(stab, seq)) < 0.06
    assert stab["mask"][stab["n_free"]:int(3 * FPS)].sum() == 0


def test_smooth_surface_removes_a_background_that_changes_unevenly():
    seq, _ = make(bg_change_k=0.5, bg_change_period_s=8.0, bg_change_shape="ramp", bg_change_spatial=0.7)
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    plane = gd.detect(seq["u8"], FPS, gas_free_s=2.4, drift_order=1)
    assert iou_last_second(plane, seq) > iou_last_second(plain, seq) + 0.15
    assert iou_last_second(plane, seq) > 0.8


def test_adaptive_background_survives_repeated_shutter_events_without_gas():
    seq, meta = make(flow_g_s=0.0, duration_s=12.0, leak_on_s=4.0, nuc_period_s=2.0, nuc_step_k=0.1, nuc_fpn_change=0.4)
    plain = gd.detect(seq["u8"], FPS, gas_free_s=3.2)
    adapt = gd.detect(seq["u8"], FPS, gas_free_s=3.2, background="adaptive", bg_tau_s=6.0)
    assert adapt["mask"].sum() <= 0.1 * max(plain["mask"].sum(), 1) and len(adapt["nuc_events"]) >= 3


def test_an_adaptive_background_does_not_absorb_a_constant_leak():
    seq, _ = make(duration_s=14.0)
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    adapt = gd.detect(seq["u8"], FPS, gas_free_s=2.4, background="adaptive", bg_tau_s=3.0)   # τ short on purpose
    assert iou_last_second(adapt, seq) > iou_last_second(plain, seq) - 0.12


def test_requiring_the_source_rejects_objects_that_come_from_the_border():
    seq, meta = make(flow_g_s=0.0, object_count=1, object_size_m=0.25, object_speed_ms=0.3, object_start_s=3.2,
                     object_temp_c=30.0, object_row_frac=0.92)                         # cruza por arriba, lejos del tubo
    src = regions.source_from_meta(meta, (72, 96))
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    assert plain["mask"].sum() > 1000 and not (plain["mask"] & ndi.binary_dilation(src, iterations=6)).any()
    on = gd.detect(seq["u8"], FPS, gas_free_s=2.4, source_roi=src, require_source=True)
    assert on["mask"].sum() == 0


def test_requiring_the_source_keeps_the_real_plume():
    seq, meta = make()
    src = regions.source_from_meta(meta, (72, 96))
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    on = gd.detect(seq["u8"], FPS, gas_free_s=2.4, source_roi=src, require_source=True)
    assert iou_last_second(on, seq) > iou_last_second(plain, seq) - 0.05


def test_auto_threshold_raises_the_thresholds_when_the_noise_has_heavy_tails():
    seq, _ = make(compression_quality=25, flow_g_s=0.0)
    plain = gd.detect(seq["u8"], FPS, gas_free_s=2.4)
    auto = gd.detect(seq["u8"], FPS, gas_free_s=2.4, auto_threshold=True)
    assert auto["z_scale"] >= 1.0 and auto["mask"].sum() <= plain["mask"].sum()


def test_profiles_run_and_polarity_follows_the_scene():
    seq, meta = make()
    for name, prof in core.DETECTOR_PROFILES.items():
        an, msg, p = core.run_detection(seq, prof["params"])
        assert an is not None, f"{name}: {msg}"
    _, _, p = core.run_detection(seq, dict(polarity="auto"))
    assert p["polarity_used"] == "dark"
    cold, _ = make(scene="cold_sky", t_bg_c=-25.0)
    assert core.run_detection(cold, dict(polarity="auto"))[2]["polarity_used"] == "bright"


def test_core_explains_a_missing_source_and_a_bad_zone():
    seq, meta = make()
    seq = dict(seq, meta=json.dumps({k: v for k, v in meta.items() if k not in ("tip_x_frac", "tip_y_frac")}))
    an, msg, _ = core.run_detection(seq, dict(require_source=True))
    assert an is None and "origen" in msg
    an, msg, _ = core.run_detection(seq, dict(roi_text="0.1,0.1; 0.2,0.2"))
    assert an is None and "3 puntos" in msg
    an, msg, p = core.run_detection(seq, dict(roi_text="0.1,0.1; 0.9,0.1; 0.9,0.9; 0.1,0.9"))
    assert an is not None and an.res["mask"].any()


def test_regions_parse_polygons_and_circles():
    assert regions.polygon_mask("0.1,0.1; 0.9,0.1; 0.9,0.9", (72, 96)).sum() > 1000
    assert regions.polygon_mask("", (72, 96)) is None
    with pytest.raises(ValueError):
        regions.polygon_mask("a,b; c,d; e,f", (72, 96))
    assert regions.circle_mask(48, 36, 10, (72, 96)).sum() == pytest.approx(314, abs=15)
