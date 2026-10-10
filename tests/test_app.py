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


def test_loads_with_five_tabs_and_all_parameters():
    at = fresh()
    assert len(at.tabs) == 5
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
    at.number_input(key="det_zlow").set_value(3.5).run()
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
