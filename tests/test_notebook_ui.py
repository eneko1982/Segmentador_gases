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


def test_levels_hide_parameters_and_summary_tracks_changes(panel):
    assert panel.boxes["psf_sigma_px"].layout.display == "none" and panel.boxes["netd_k"].layout.display == ""
    panel.level.value = "E"
    assert panel.boxes["psf_sigma_px"].layout.display == ""
    n0 = int(panel.changes_box.get_title(0).split("(")[1].rstrip(")"))
    panel.W["wind_ms"].value = 1.0
    assert int(panel.changes_box.get_title(0).split("(")[1].rstrip(")")) == n0 + 1
    panel.changes_pick.value = "wind_ms"
    panel._reset_one()
    assert panel.W["wind_ms"].value == sim.Config().wind_ms


def test_scenario_suggests_detector_and_guide_searches(panel):
    panel.preset.value = "camera_shake"
    assert panel.D_profile.value == "Cámara con vibración" and panel.D["stabilize"].value is True
    panel.guide_q.value = "viento"
    assert "viento" in panel.guide_html.value.lower()
    panel.reset_all()
    assert panel.D["stabilize"].value is False and panel.preset.value == "Por defecto"


def test_long_video_tab_runs_and_writes_results(panel):
    panel.l_dur.value = 6.0
    panel.W["leak_on_s"].value = 2.0
    panel.run_long()
    assert panel.long is not None and "Vídeo largo listo" in panel.status.value
    assert (Path(panel.out_dir) / "largo" / "resultados" / "serie_temporal.csv").exists()


def test_real_video_button_loads_a_file(panel, tmp_path):
    cv2 = pytest.importorskip("cv2")
    seq = sim.simulate(sim.half_resolution(sim.Config(duration_s=4, leak_on_s=1.5, flow_g_s=0.5)))
    h, wd = seq["u8"].shape[1:3]
    path = tmp_path / "v.avi"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (wd, h), False)
    for f in seq["u8"]:
        vw.write(f)
    vw.release()
    panel.vid_path.value = str(path)
    panel.vid_gf.value = 1.2
    panel.load_real_video()
    assert panel.seq is not None and panel.an is not None and not panel.an.has_truth
