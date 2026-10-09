import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ogi_sim as sim  # noqa: E402

SMALL = dict(width=96, height=72, duration_s=1.0, leak_on_s=0.4, fps=20.0)


def run(**kw):
    return sim.simulate(sim.Config(**{**SMALL, **kw}))


def test_every_parameter_is_documented_and_grouped():
    rows = sim.params_table()
    assert len(rows) == len(sim.Config.__dataclass_fields__)
    assert all(doc for *_, doc in rows)
    assert "`flow_g_s`" in sim.params_markdown()


def test_presets_load_and_validate():
    for p in sorted(sim.PRESET_DIR.glob("*.json")):
        sim.apply_overrides(sim.Config(), sim.load_preset(p.stem))


def test_overrides_are_typed_and_validated():
    cfg = sim.apply_overrides(sim.Config(), sim.parse_set(["flow_g_s=1", "turb_n_modes=80.0", "leak_off_s=2", "t_exit_c=none"]))
    assert cfg.flow_g_s == 1.0 and isinstance(cfg.flow_g_s, float)
    assert cfg.turb_n_modes == 80 and cfg.leak_off_s == 2.0 and cfg.t_exit_c is None
    with pytest.raises(ValueError, match="unknown parameter"):
        sim.apply_overrides(sim.Config(), {"flow": 1})
    with pytest.raises(ValueError, match="outside"):
        sim.apply_overrides(sim.Config(), {"fps": 0})
    with pytest.raises(ValueError, match="leak_off_s"):
        sim.apply_overrides(sim.Config(), {"leak_off_s": 0.5})
    with pytest.raises(ValueError, match="gas"):
        sim.apply_overrides(sim.Config(), {"gas": "methane"})
    with pytest.raises(ValueError, match="integer"):
        sim.apply_overrides(sim.Config(), {"width": 100.5})


def test_custom_gas_with_overrides():
    cfg = sim.apply_overrides(sim.Config(), {"gas": "SF6", "gas_molar_mass_kg_mol": 0.146, "gas_density_kg_m3": 6.08})
    assert sim.simulate(sim.Config(**{**asdict(cfg), **SMALL}))["u8"].shape[0] == 20


def test_same_seed_same_video_and_meta_records_every_parameter():
    a, b = run(seed=3), run(seed=3)
    assert np.array_equal(a["u8"], b["u8"])
    assert not np.array_equal(a["u8"], run(seed=4)["u8"])
    meta = json.loads(a["meta"])
    assert set(asdict(sim.Config())) <= set(meta)


def test_leak_on_off_and_optional_outputs():
    seq = run(leak_off_s=0.7, store_cl=False, store_temp=False)
    assert "cl" not in seq and "temp_ck" not in seq
    m = seq["mask"].reshape(len(seq["mask"]), -1).any(1)
    assert not m[:8].any() and m[8:14].any()     # nothing before the valve opens (t < 0.4 s), gas after


def test_wind_direction_changes_plume():
    right, left = run(wind_ms=0.8, duration_s=1.5), run(wind_ms=-0.8, duration_s=1.5)
    xs = lambda s: np.nonzero(s["mask"][-1])[1].mean()
    assert xs(right) > xs(left)
