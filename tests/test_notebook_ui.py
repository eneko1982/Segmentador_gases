"""Panel del cuaderno (ipywidgets): construcción, simulación, máscaras y exportación, sin navegador."""
import sys
import zipfile
from pathlib import Path

import matplotlib
import pytest

matplotlib.use("Agg")
pytest.importorskip("ipywidgets")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notebook_ui  # noqa: E402
import ogi_sim as sim  # noqa: E402


@pytest.fixture()
def panel(tmp_path):
    p = notebook_ui.Panel(out_dir=str(tmp_path / "salida"))
    p.res.value = "Completa"
    for k, v in dict(width=96, height=72, fps=20.0, duration_s=3.0, leak_on_s=1.5).items():
        p.W[k].value = v
    return p


def test_every_simulator_parameter_has_a_widget(panel):
    assert set(panel.W) == set(sim.Config.__dataclass_fields__)
    cfg = panel.config()
    assert cfg.width == 96 and cfg.leak_on_s == 1.5 and cfg.leak_off_s is None and cfg.level_c is None


def test_preset_loading_and_optional_parameters(panel):
    panel.preset.value = "hot_plate_dT5"
    assert panel.W["t_bg_c"].value == 27.0 and panel.U["level_c"].value is True and panel.W["level_c"].disabled is False
    assert panel.config().level_c == 25.0
    panel.U["level_c"].value = False
    assert panel.config().level_c is None and panel.W["level_c"].disabled is True


def test_simulate_detect_and_choose_masks(panel):
    panel.run_simulation()
    assert panel.seq is not None and panel.an is not None and "Listo" in panel.status.value
    n_det = panel.masks()[0].sum()
    panel.v_truth.value = "3x"
    panel.v_pred.value = "custom"
    panel.v_pred_mk.value = 80.0
    truth, pred, ts, ps = panel.masks()
    assert truth.sum() != n_det and ts["kind"] == "3x" and ps == {"kind": "custom", "mk": 80.0}
    assert "IoU" in panel._kpis_html()


def test_errors_are_reported_in_the_status_line(panel):
    panel.W["leak_on_s"].value = 0.5
    panel.run_simulation()                                   # fuga muy pronto: el detector no puede aprender
    assert panel.an is None and "al menos" in panel.status.value
    panel.W["leak_off_s"].value = 0.2
    panel.U["leak_off_s"].value = True
    panel.run_simulation()
    assert "leak_off_s" in panel.status.value


def test_export_writes_the_files(panel):
    panel.e_gif.value = False
    panel.run_simulation()
    panel.export()
    out = Path(panel.out_dir)
    assert {"resumen.json", "serie_temporal.csv", "series.png", "galeria.png", "paquete.zip"} <= {p.name for p in out.iterdir()}
    assert "informe.html" in zipfile.ZipFile(out / "paquete.zip").namelist()
