"""Trazos de corte de los stickers, en milímetros con origen arriba-izquierda."""

from __future__ import annotations

from .contorno import silueta
from .geometria import Colocacion


K_BEZIER = 0.5522847498

Comando = tuple


def trazo_forma(
    col: Colocacion, expansion_mm: float = 0.0, radio_esquina_mm: float = 3.0
) -> list[Comando]:
    """Devuelve el trazo de la forma, en sentido horario desde arriba-izquierda."""
    e = float(expansion_mm)
    x0 = float(col.x_mm) - e
    y0 = float(col.y_mm) - e
    x1 = float(col.x_mm) + float(col.ancho_mm) + e
    y1 = float(col.y_mm) + float(col.alto_mm) + e
    ancho = x1 - x0
    alto = y1 - y0

    if col.forma == "circulo":
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        rx = ancho / 2.0
        ry = alto / 2.0
        kx = rx * K_BEZIER
        ky = ry * K_BEZIER
        return [
            ("M", cx, y0),
            ("C", cx + kx, y0, x1, cy - ky, x1, cy),
            ("C", x1, cy + ky, cx + kx, y1, cx, y1),
            ("C", cx - kx, y1, x0, cy + ky, x0, cy),
            ("C", x0, cy - ky, cx - kx, y0, cx, y0),
            ("Z",),
        ]

    if col.forma == "redondeado":
        r = max(0.0, min(float(radio_esquina_mm) + e, min(ancho, alto) / 2.0))
        k = r * K_BEZIER
        return [
            ("M", x0 + r, y0),
            ("L", x1 - r, y0),
            ("C", x1 - r + k, y0, x1, y0 + r - k, x1, y0 + r),
            ("L", x1, y1 - r),
            ("C", x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1),
            ("L", x0 + r, y1),
            ("C", x0 + r - k, y1, x0, y1 - r + k, x0, y1 - r),
            ("L", x0, y0 + r),
            ("C", x0, y0 + r - k, x0 + r - k, y0, x0 + r, y0),
            ("Z",),
        ]

    return [
        ("M", x0, y0),
        ("L", x1, y0),
        ("L", x1, y1),
        ("L", x0, y1),
        ("L", x0, y0),
        ("Z",),
    ]


def obtener_trazo(col: Colocacion, ajuste, info, expansion_mm: float = 0.0) -> list[Comando]:
    """Trazo de corte de la colocación; la silueta ignora la expansión."""
    if col.forma == "silueta":
        if info is None:
            raise ValueError(f"Falta el diseño {col.id_diseno!r}.")
        puntos, _piezas = silueta(info, col.ancho_mm, col.alto_mm, ajuste.borde_mm)
        x = float(col.x_mm)
        y = float(col.y_mm)
        trazo: list[Comando] = [("M", x + puntos[0][0], y + puntos[0][1])]
        trazo += [("L", x + px, y + py) for px, py in puntos[1:]]
        trazo.append(("Z",))
        return trazo
    return trazo_forma(col, expansion_mm, ajuste.radio_esquina_mm)


def caja_trazo(trazo: list[Comando]) -> tuple[float, float, float, float]:
    """Caja (x0, y0, x1, y1) de los puntos finales del trazo."""
    xs: list[float] = []
    ys: list[float] = []
    for comando in trazo:
        if comando[0] == "Z":
            continue
        xs.append(comando[-2])
        ys.append(comando[-1])
    return (min(xs), min(ys), max(xs), max(ys))
