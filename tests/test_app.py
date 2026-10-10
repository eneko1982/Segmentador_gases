"""Pruebas de humo del panel (Streamlit AppTest): sin navegador."""
import io
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception
    return at


def simulated(duration=3.0, leak_on=1.5) -> AppTest:
    at = fresh()
    at.slider(key="p_duration_s").set_value(duration).run()
    at.slider(key="p_leak_on_s").set_value(leak_on).run()
    at.button(key="btn_sim").click().run()
    assert not at.exception
    return at


def test_loads_with_seven_tabs_and_all_parameters():
    at = fresh()
    assert len(at.tabs) == 7
    sys.path.insert(0, str(Path(APP).parent))
    import ogi_sim
    for f in ogi_sim.Config.__dataclass_fields__:
        assert f"p_{f}" in at.session_state


def test_preset_loads_parameters():
    at = fresh()
    at.selectbox(key="preset").select("hot_plate_dT5").run()
    assert at.number_input(key="p_t_bg_c").value == 27.0
    assert at.session_state["u_level_c"] is True and at.session_state["p_level_c"] == 25.0


def test_duration_slider_does_not_reset_leak_start():
    at = fresh()
    at.slider(key="p_leak_on_s").set_value(1.5).run()
    at.slider(key="p_duration_s").set_value(3.0).run()
    assert at.slider(key="p_leak_on_s").value == 1.5


def test_incoherent_parameters_show_an_error_and_block_the_button():
    at = fresh()
    at.slider(key="p_duration_s").set_value(1.0).run()
    at.slider(key="p_leak_on_s").set_value(2.0).run()
    assert at.error and "leak_on_s" in at.error[0].value
    assert at.button(key="btn_sim").disabled


def test_simulate_calculates_the_detector_and_shows_results():
    at = simulated()
    assert at.session_state["seq"]["u8"].shape[0] == 75
    assert at.session_state["det"]["an"].n == 75
    labels = {m.label for m in at.metric}
    assert {"Ruido tras el filtrado", "Retardo de detección", "IoU", "Recall", "Precisión"} <= labels


def test_changing_the_masks_updates_the_scores():
    at = simulated()
    iou_before = at.session_state["sum_cache"][1]["selected_masks"]["iou"]
    at.selectbox(key="vid_truth_kind").select("3x").run()
    assert not at.exception
    assert at.session_state["sum_cache"][1]["selected_masks"]["iou"] != iou_before
    at.selectbox(key="vid_pred_kind").select("custom").run()
    assert not at.exception and at.number_input(key="vid_pred_mk").value == 35.0


def test_detector_can_be_recalculated_with_other_parameters():
    at = simulated()
    uid = at.session_state["det"]["uid"]
    at.radio(key="level").set_value("A").run()
    at.number_input(key="det_z_low").set_value(3.5).run()
    at.button(key="btn_det").click().run()
    assert not at.exception and at.session_state["det"]["uid"] == uid + 1
    assert at.session_state["det"]["params"]["z_low"] == 3.5


def test_detector_explains_a_too_short_gas_free_period():
    at = fresh()
    at.slider(key="p_duration_s").set_value(2.0).run()
    at.slider(key="p_leak_on_s").set_value(0.4).run()
    at.button(key="btn_sim").click().run()
    assert at.warning and "al menos" in at.warning[0].value


def test_export_builds_a_valid_zip():
    at = simulated()
    at.checkbox(key="exp_gif").set_value(False)
    at.button(key="btn_export").click().run()
    assert not at.exception
    items = at.session_state["exp_items"]
    assert {"resumen.json", "serie_temporal.csv", "series.png", "galeria.png", "paquete.zip"} <= set(items)
    names = zipfile.ZipFile(io.BytesIO(items["paquete.zip"][0])).namelist()
    assert "informe.html" in names and "datos/deteccion.npz" in names


def test_basic_level_hides_expert_parameters_but_keeps_their_values():
    at = fresh()
    assert at.radio(key="level").value == "B"
    at.radio(key="level").set_value("E").run()
    assert not at.exception
    assert any(w.key == "p_psf_sigma_px" for w in at.number_input)
    at.radio(key="level").set_value("B").run()
    assert not at.exception
    assert not any(w.key == "p_psf_sigma_px" for w in at.number_input)
    assert "p_psf_sigma_px" in at.session_state


def test_changed_parameters_summary_and_reset():
    at = fresh()
    at.slider(key="p_wind_ms").set_value(1.0).run()
    assert any("Lo que has cambiado (1)" in e.label for e in at.sidebar.expander)
    at.button(key="rs_wind_ms").click().run()
    assert at.slider(key="p_wind_ms").value == 0.0 or at.slider(key="p_wind_ms").value == sim_default("wind_ms")


def sim_default(name):
    sys.path.insert(0, str(Path(APP).parent))
    import ogi_sim
    return getattr(ogi_sim.Config(), name)


def test_scenario_suggests_the_detector_profile():
    at = fresh()
    at.selectbox(key="preset").select("camera_shake").run()
    assert at.selectbox(key="det_profile").value == "Cámara con vibración"
    assert at.session_state["det_stabilize"] is True


def test_long_video_runs_from_the_sidebar():
    at = fresh()
    at.slider(key="p_leak_on_s").set_value(2.0).run()
    at.number_input(key="long_dur").set_value(5.0).run()
    at.button(key="btn_long").click().run()
    assert not at.exception
    run = at.session_state["long"]
    assert run.n > 100 and run.summary["scores"]["all"]["iou"] > 0.3


def test_real_video_sequence_displays_without_truth(tmp_path):
    cv2 = pytest.importorskip("cv2")
    sys.path.insert(0, str(Path(APP).parent))
    import ogi_sim
    import video_io
    seq = ogi_sim.simulate(ogi_sim.half_resolution(ogi_sim.Config(duration_s=4, leak_on_s=1.5, flow_g_s=0.5)))
    h, wd = seq["u8"].shape[1:3]
    vw = cv2.VideoWriter(str(tmp_path / "v.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 25, (wd, h), False)
    for f in seq["u8"]:
        vw.write(f)
    vw.release()
    real = video_io.load_video(tmp_path / "v.avi", gas_free_s=1.2)
    at = fresh()
    at.session_state["seq"] = real
    at.session_state["seq_version"] = 1
    at.session_state["det_auto_gf"] = False
    at.session_state["det_gas_free"] = 1.2
    at.run()
    at.button(key="btn_det").click().run()
    assert not at.exception, at.exception
    assert at.session_state["det"]["an"].has_truth is False
