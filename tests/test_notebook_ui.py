import sys
from pathlib import Path

import matplotlib
import pytest

matplotlib.use("Agg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notebook_ui  # noqa: E402

SMALL = dict(width=96, height=72, duration_s=3.0, leak_on_s=1.5, flow_g_s=0.1, fps=20.0)


def test_run_once_returns_figure_and_sensible_metrics():
    fig, m = notebook_ui.run_once(SMALL)
    assert fig is not None and len(fig.axes) == 4
    assert m["iou"] > 0.5 and not m["falsa_alarma"] and m["retardo_s"] is not None
    assert "IoU" in notebook_ui.describe(m)


def test_run_once_explains_a_leak_that_starts_too_early():
    fig, msg = notebook_ui.run_once({**SMALL, "leak_on_s": 0.4})
    assert fig is None and "demasiado pronto" in msg


def test_run_once_rejects_incoherent_parameters():
    with pytest.raises(ValueError):
        notebook_ui.run_once({**SMALL, "leak_off_s": 0.5})


def test_widgets_build():
    pytest.importorskip("ipywidgets")
    notebook_ui.launch()
