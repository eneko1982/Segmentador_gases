"""Perturbaciones del mundo real y escritura a disco del simulador."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ogi_sim as sim  # noqa: E402

BASE = dict(width=96, height=72, duration_s=4.0, leak_on_s=1.0, fps=20.0, flow_g_s=0.1)


def run(**kw):
    return sim.simulate(sim.Config(**{**BASE, **kw}))


def digest(seq, key):
    return hashlib.sha256(seq[key].tobytes()).hexdigest()[:16]


# Huellas del simulador ORIGINAL (antes de añadir perturbaciones, parámetros y vídeos por trozos).
ORIGINAL = {
    "hot_plate": dict(u8="052aa88ced68f28b", temp_ck="c3b9ffd230d6bb09", cl="dfd9ef8cab827344",
                      contrast="8c6c035a4b1e176d", mask="ce5872fc6b886126"),
    "cold_sky": dict(u8="132d4e6a16cb1d86", temp_ck="9156bfb1b9f2cc2d", cl="dfd9ef8cab827344",
                     contrast="9e8086bd82e0e32b", mask="a0a721aada5dbe3b"),
}


@pytest.mark.parametrize("scene,t_bg", [("hot_plate", 70.0), ("cold_sky", -25.0)])
def test_defaults_reproduce_the_original_simulator_bit_for_bit(scene, t_bg):
    seq = sim.simulate(sim.Config(width=96, height=72, duration_s=2.0, leak_on_s=0.5, fps=20.0, flow_g_s=0.1,
                                  seed=5, scene=scene, t_bg_c=t_bg))
    assert {k: digest(seq, k) for k in ORIGINAL[scene]} == ORIGINAL[scene]
    assert not {"nuisance", "shift_px"} & set(seq)


def test_perturbations_use_their_own_random_stream():
    ref = run()
    bp = run(bad_pixel_frac=0.01)
    changed = (bp["u8"] != ref["u8"]).any(0)
    assert 0 < changed.sum() <= 0.011 * 96 * 72
    assert np.array_equal(bp["u8"][:, ~changed], ref["u8"][:, ~changed])


def test_background_change_moves_the_image_but_not_the_gas_truth():
    seq = run(bg_change_k=1.5, bg_change_period_s=4.0, flow_g_s=0.0)
    mean = seq["u8"].astype(float).mean((1, 2))
    assert mean.max() - mean.min() > 20                                   # varios niveles de gris
    assert np.abs(seq["contrast"].astype(float)).max() == 0               # sin gas: la verdad es cero


def test_cloud_and_objects_are_reported_as_nuisance_not_gas():
    seq = run(cloud_k=-1.0, cloud_speed_ms=0.25, object_count=2, object_size_m=0.2, flow_g_s=0.0)
    n = seq["nuisance"].reshape(len(seq["nuisance"]), -1).sum(1)
    assert n.max() > 100 and n.min() == 0 and not seq["mask"].any()


def test_shutter_events_freeze_frames_and_are_logged():
    seq = run(nuc_period_s=1.0, nuc_freeze_frames=2, flow_g_s=0.0)
    frozen = [i for i in range(1, 80) if np.array_equal(seq["u8"][i], seq["u8"][i - 1])]
    assert json.loads(seq["meta"])["nuc_frames"] == [20, 40, 60] and frozen == [20, 21, 40, 41, 60, 61]


def test_vibration_is_recorded_and_moves_the_image():
    seq = run(vibration_px=1.5, flow_g_s=0.0)
    assert seq["shift_px"].shape == (80, 2) and np.abs(seq["shift_px"]).max() > 0.5
    assert not np.array_equal(seq["u8"][10], run(flow_g_s=0.0)["u8"][10])


def test_compression_only_degrades_the_8bit_video_and_more_with_lower_quality():
    ref = run()
    err = []
    for q in (90, 40, 10):
        cp = run(compression_quality=q)
        assert np.array_equal(cp["temp_ck"], ref["temp_ck"])
        err.append(np.sqrt(((cp["u8"].astype(float) - ref["u8"].astype(float)) ** 2).mean()))
    assert err[0] < err[1] < err[2]


def test_invalid_perturbation_parameters_are_rejected():
    with pytest.raises(ValueError, match="bg_change_shape"):
        sim.apply_overrides(sim.Config(), {"bg_change_shape": "square"})
    with pytest.raises(ValueError, match="outside"):
        sim.apply_overrides(sim.Config(), {"compression_quality": 150})


def test_presets_load():
    for name in ("sun_and_clouds", "person_crossing", "camera_shake", "shutter_events", "video_compression",
                 "real_world_mild", "real_world_hard"):
        sim.apply_overrides(sim.Config(), sim.load_preset(name))


def test_random_config_with_perturbations_keeps_the_base_draws():
    a = sim.random_config(np.random.default_rng(3), 7)
    b = sim.random_config(np.random.default_rng(3), 7, perturbations=True)
    for k in ("scene", "flow_g_s", "wind_ms", "netd_k", "t_amb_c"):
        assert getattr(a, k) == getattr(b, k)


def test_writing_to_disk_gives_exactly_the_same_sequence(tmp_path):
    cfg = sim.Config(**{**BASE, "vibration_px": 0.8, "object_count": 1})
    mem = sim.simulate(cfg)
    sim.simulate_to_dir(cfg, tmp_path / "seq", flush_every=10)
    disk = sim.load_sequence(tmp_path / "seq")
    assert isinstance(disk["u8"], np.memmap) and disk["meta"] == mem["meta"]
    assert all(np.array_equal(mem[k], disk[k]) for k in mem if k != "meta")
