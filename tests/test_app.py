"""Pruebas de humo de la interfaz (Streamlit AppTest): sin navegador."""
import sys
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


def test_loads_with_four_tabs_and_all_parameters():
    at = fresh()
    assert len(at.tabs) == 4
    sys.path.insert(0, str(Path(APP).parent))
    import ogi_sim
    for f in ogi_sim.Config.__dataclass_fields__:
        assert f"p_{f}" in at.session_state


def test_preset_loads_parameters():
    at = fresh()
    at.selectbox(key="preset").select("hot_plate_dT5").run()
    assert at.number_input(key="p_t_bg_c").value == 27.0
    assert at.checkbox(key="u_level_c").value is True


def test_duration_slider_does_not_reset_leak_start():
    at = fresh()
    at.slider(key="p_leak_on_s").set_value(1.5).run()
    at.slider(key="p_duration_s").set_value(3.0).run()
    assert at.slider(key="p_leak_on_s").value == 1.5


def test_incoherent_parameters_show_an_error():
    at = fresh()
    at.slider(key="p_duration_s").set_value(1.0).run()
    at.slider(key="p_leak_on_s").set_value(2.0).run()
    assert at.error and "leak_on_s" in at.error[0].value


def test_simulate_then_detect():
    at = fresh()
    at.slider(key="p_duration_s").set_value(3.0).run()
    at.slider(key="p_leak_on_s").set_value(1.5).run()
    at.button(key="btn_sim").click().run()
    assert not at.exception and at.session_state["seq"]["u8"].shape[0] == 75
    at.button(key="btn_det").click().run()
    assert not at.exception and not at.error
    labels = {m.label for m in at.metric}
    assert {"Ruido tras el filtrado", "Retardo de detección"} <= labels


def test_detector_explains_a_too_short_gas_free_period():
    at = fresh()
    at.slider(key="p_duration_s").set_value(2.0).run()
    at.slider(key="p_leak_on_s").set_value(0.4).run()
    at.button(key="btn_sim").click().run()
    at.button(key="btn_det").click().run()
    assert at.error and "al menos" in at.error[0].value
