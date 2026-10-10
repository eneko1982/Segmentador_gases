"""
regions.py - Zonas de la imagen (máscaras booleanas) para el detector: zona de vigilancia y zona de origen.

Una zona se escribe como un polígono de puntos "x,y; x,y; x,y" (también valen saltos de línea):
  - si todos los valores están entre 0 y 1 son fracciones del ancho y del alto (0,0 = esquina superior izquierda);
  - si alguno es mayor que 1 se interpretan como píxeles.
"""
from __future__ import annotations

import re

import numpy as np
from PIL import Image, ImageDraw


def parse_points(text) -> list[tuple[float, float]]:
    """"0.1,0.2; 0.8,0.2; 0.8,0.9" -> [(0.1, 0.2), (0.8, 0.2), (0.8, 0.9)]. Lanza ValueError si el texto no es válido."""
    if text is None:
        return []
    if not isinstance(text, str):
        return [(float(x), float(y)) for x, y in text]
    pts = []
    for part in re.split(r"[;\n]+", text.strip()):
        part = part.strip()
        if not part:
            continue
        nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", part.replace(",", " "))
        if len(nums) != 2:
            raise ValueError(f"punto no válido: {part!r} (se espera «x,y»)")
        pts.append((float(nums[0]), float(nums[1])))
    return pts


def polygon_mask(points, shape) -> np.ndarray | None:
    """Máscara (H, W) del polígono, o None si no hay puntos. Se necesitan al menos 3 puntos."""
    pts = parse_points(points)
    if not pts:
        return None
    if len(pts) < 3:
        raise ValueError("un polígono necesita al menos 3 puntos")
    h, w = shape
    arr = np.array(pts, float)
    if arr.max() <= 1.0 and arr.min() >= 0.0:
        arr = arr * np.array([w - 1, h - 1])
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in arr], outline=1, fill=1)
    mask = np.asarray(img, bool)
    if not mask.any():
        raise ValueError("el polígono no cubre ningún píxel de la imagen")
    return mask


def circle_mask(cx: float, cy: float, radius_px: float, shape) -> np.ndarray:
    """Círculo de centro (cx, cy) en píxeles."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= radius_px ** 2


def source_from_meta(meta: dict, shape, radius_frac: float = 0.06) -> np.ndarray | None:
    """Círculo alrededor de la boca del tubo de una secuencia simulada (None si el meta no la indica)."""
    if "tip_x_frac" not in meta or "tip_y_frac" not in meta:
        return None
    h, w = shape
    return circle_mask(meta["tip_x_frac"] * w, meta["tip_y_frac"] * h - 0.04 * h, radius_frac * w, shape)


def to_text(mask_points) -> str:
    """Puntos (fracciones) a texto, con 3 decimales."""
    return "; ".join(f"{x:.3f},{y:.3f}" for x, y in mask_points)
