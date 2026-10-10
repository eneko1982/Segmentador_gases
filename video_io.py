"""
video_io.py - Carga de vídeos reales (.mp4, .avi, .mov…) como secuencia del panel.

Un vídeo real no tiene máscara verdadera: se puede ver y detectar, pero no hay IoU ni recall.
Hay que decir tres cosas que el archivo no contiene:
  - segundos iniciales sin gas (el detector aprende ahí el fondo y el ruido),
  - K por nivel de gris (para convertir niveles a kelvin; 0 = desconocido, el detector trabaja en niveles),
  - NETD aproximado (solo informativo).
OpenCV se importa al usar la función (`pip install opencv-python-headless`).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".m4v"]


def load_video(path, *, gas_free_s: float = 2.0, k_per_dn: float = 0.0, fps: float | None = None, netd_k: float = 0.05,
               max_frames: int | None = None, stride: int = 1, scale: float = 1.0, start_s: float = 0.0) -> dict:
    """Lee un vídeo y devuelve {'u8': (T,H,W) uint8 gris, 'meta': json} compatible con el resto del panel."""
    try:
        import cv2
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("Para leer vídeos hace falta OpenCV: pip install opencv-python-headless") from err
    path = Path(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"No se pudo abrir el vídeo {path.name}.")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    use_fps = float(fps) if fps else (src_fps if src_fps > 1 else 25.0)
    frames, i = [], 0
    first = int(round(start_s * use_fps))
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                break
            if i >= first and (i - first) % max(int(stride), 1) == 0:
                g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                if scale != 1.0:
                    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                frames.append(g)
                if max_frames and len(frames) >= max_frames:
                    break
            i += 1
    finally:
        cap.release()
    if len(frames) < 10:
        raise ValueError("El vídeo tiene muy pocos fotogramas (menos de 10).")
    u8 = np.ascontiguousarray(np.stack(frames))
    out_fps = use_fps / max(int(stride), 1)
    meta = dict(fps=float(out_fps), duration_s=len(u8) / out_fps, width=int(u8.shape[2]), height=int(u8.shape[1]),
                leak_on_s=float(gas_free_s), netd_k=float(netd_k), k_per_dn=float(k_per_dn) if k_per_dn > 0 else 1.0,
                scene="unknown", real_video=True, source_file=path.name, calibrated=bool(k_per_dn > 0))
    return dict(u8=u8, meta=np.array(json.dumps(meta)))
