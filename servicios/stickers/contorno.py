"""Contorno de corte que sigue la silueta de un PNG transparente, en milímetros."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from .disenos import InfoDiseno


UMBRAL_ALFA = 8
TOLERANCIA_MM = 0.05
AREA_MINIMA_PX = 4.0

Punto = tuple[float, float]

# Segmentos por caso de marching squares; bits: arriba-izq 8, arriba-der 4,
# abajo-der 2, abajo-izq 1. Lados: 0 arriba, 1 derecha, 2 abajo, 3 izquierda.
_CASOS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((3, 2),), 2: ((2, 1),), 3: ((3, 1),), 4: ((0, 1),),
    6: ((0, 2),), 7: ((3, 0),), 8: ((3, 0),), 9: ((0, 2),),
    11: ((0, 1),), 12: ((3, 1),), 13: ((2, 1),), 14: ((3, 2),),
}
_SILLA_CENTRO_DENTRO = {5: ((3, 0), (2, 1)), 10: ((0, 1), (3, 2))}
_SILLA_CENTRO_FUERA = {5: ((0, 1), (3, 2)), 10: ((3, 0), (2, 1))}


def _mascara(ruta: str, contenido: tuple[int, int, int, int], ancho_px: int, alto_px: int):
    with Image.open(ruta) as imagen:
        alfa = imagen.convert("RGBA").getchannel("A").crop(contenido)
    alfa = alfa.resize((ancho_px, alto_px), Image.Resampling.BILINEAR)
    return np.asarray(alfa) > UMBRAL_ALFA


def _dilatar(mascara: np.ndarray, radio: float) -> np.ndarray:
    filas, columnas = mascara.shape
    y = np.arange(filas)
    x = np.arange(columnas)
    y = np.minimum(y, filas - y)[:, None]
    x = np.minimum(x, columnas - x)[None, :]
    disco = (x * x + y * y <= radio * radio).astype(np.float64)
    conv = np.fft.irfft2(
        np.fft.rfft2(mascara.astype(np.float64)) * np.fft.rfft2(disco), s=mascara.shape
    )
    return conv > 0.5


def _box_blur(campo: np.ndarray) -> np.ndarray:
    p = np.pad(campo, 1, mode="edge")
    filas, columnas = campo.shape
    suma = np.zeros_like(campo)
    for dy in range(3):
        for dx in range(3):
            suma += p[dy:dy + filas, dx:dx + columnas]
    return suma / 9.0


def _marching_squares(f: np.ndarray, nivel: float = 0.5) -> list[np.ndarray]:
    filas, columnas = f.shape
    dentro = f > nivel
    caso = (
        dentro[:-1, :-1].astype(np.int8) * 8
        + dentro[:-1, 1:] * 4
        + dentro[1:, 1:] * 2
        + dentro[1:, :-1] * 1
    )
    centro = (f[:-1, :-1] + f[:-1, 1:] + f[1:, 1:] + f[1:, :-1]) / 4.0 > nivel

    base_v = filas * columnas

    def lado(i, j, n):
        # Identificador entero de la arista: horizontales h(i,j), verticales v(i,j).
        if n == 0:
            return i * columnas + j
        if n == 2:
            return (i + 1) * columnas + j
        if n == 3:
            return base_v + i * columnas + j
        return base_v + i * columnas + j + 1

    pares_a: list[np.ndarray] = []
    pares_b: list[np.ndarray] = []

    def agregar(celdas, segmentos):
        i, j = celdas
        for a, b in segmentos:
            pares_a.append(lado(i, j, a))
            pares_b.append(lado(i, j, b))

    for valor, segmentos in _CASOS.items():
        agregar(np.nonzero(caso == valor), segmentos)
    for valor in (5, 10):
        en_caso = caso == valor
        agregar(np.nonzero(en_caso & centro), _SILLA_CENTRO_DENTRO[valor])
        agregar(np.nonzero(en_caso & ~centro), _SILLA_CENTRO_FUERA[valor])

    if not pares_a:
        return []
    a = np.concatenate(pares_a)
    b = np.concatenate(pares_b)

    aristas = np.unique(np.concatenate([a, b]))
    horizontal = aristas < base_v
    k = np.where(horizontal, aristas, aristas - base_v)
    i = k // columnas
    j = k % columnas
    i2 = np.where(horizontal, i, i + 1)
    j2 = np.where(horizontal, j + 1, j)
    f1 = f[i, j]
    f2 = f[np.minimum(i2, filas - 1), np.minimum(j2, columnas - 1)]
    t = np.clip((nivel - f1) / np.where(f2 == f1, 1.0, f2 - f1), 0.0, 1.0)
    xs = np.where(horizontal, j + t, j).astype(np.float64)
    ys = np.where(horizontal, i, i + t).astype(np.float64)
    indice = {int(arista): n for n, arista in enumerate(aristas)}

    ady: dict[int, list[int]] = defaultdict(list)
    lista_a = a.tolist()
    lista_b = b.tolist()
    for n, (ea, eb) in enumerate(zip(lista_a, lista_b)):
        ady[ea].append(n)
        ady[eb].append(n)

    usado = np.zeros(len(lista_a), dtype=bool)
    lazos: list[np.ndarray] = []
    for inicio in range(len(lista_a)):
        if usado[inicio]:
            continue
        usado[inicio] = True
        primero = lista_a[inicio]
        actual = lista_b[inicio]
        camino = [primero, actual]
        segmento = inicio
        while actual != primero:
            siguiente = next((s for s in ady[actual] if s != segmento and not usado[s]), None)
            if siguiente is None:
                break
            usado[siguiente] = True
            actual = lista_b[siguiente] if lista_a[siguiente] == actual else lista_a[siguiente]
            camino.append(actual)
            segmento = siguiente
        if actual != primero or len(camino) < 4:
            continue
        pos = np.array([indice[e] for e in camino[:-1]])
        lazos.append(np.column_stack([xs[pos], ys[pos]]))
    return lazos


def _area(lazo: np.ndarray) -> float:
    x = lazo[:, 0]
    y = lazo[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) / 2.0


def _dentro(punto: np.ndarray, lazo: np.ndarray) -> bool:
    x, y = punto
    x1, y1 = lazo[:, 0], lazo[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    cruza = (y1 > y) != (y2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return bool(np.count_nonzero(cruza & (x < xc)) % 2)


def _rdp(puntos: np.ndarray, tolerancia: float) -> np.ndarray:
    conservar = np.zeros(len(puntos), dtype=bool)
    conservar[0] = conservar[-1] = True
    pila = [(0, len(puntos) - 1)]
    while pila:
        i0, i1 = pila.pop()
        if i1 - i0 < 2:
            continue
        p0 = puntos[i0]
        d = puntos[i1] - p0
        tramo = puntos[i0 + 1:i1] - p0
        largo = float(np.hypot(*d))
        if largo == 0:
            dist = np.hypot(tramo[:, 0], tramo[:, 1])
        else:
            dist = np.abs(d[0] * tramo[:, 1] - d[1] * tramo[:, 0]) / largo
        k = int(np.argmax(dist))
        if dist[k] > tolerancia:
            medio = i0 + 1 + k
            conservar[medio] = True
            pila.append((i0, medio))
            pila.append((medio, i1))
    return puntos[conservar]


def _simplificar_lazo(lazo: np.ndarray, tolerancia: float) -> np.ndarray:
    lejano = int(np.argmax(np.hypot(*(lazo - lazo[0]).T)))
    ida = _rdp(lazo[:lejano + 1], tolerancia)
    vuelta = _rdp(np.vstack([lazo[lejano:], lazo[:1]]), tolerancia)
    return np.vstack([ida, vuelta[1:-1]])


@lru_cache(maxsize=128)
def _silueta_cache(
    id_diseno: str,
    ruta: str,
    contenido: tuple[int, int, int, int],
    w: float,
    h: float,
    b: float,
    resolucion_px: int,
) -> tuple[tuple[Punto, ...], int]:
    px_por_mm = resolucion_px / max(w, h)
    ancho_px = max(1, round((w - 2 * b) * px_por_mm))
    alto_px = max(1, round((h - 2 * b) * px_por_mm))
    radio = b * px_por_mm
    relleno = int(np.ceil(radio)) + 4

    mascara = np.pad(_mascara(ruta, contenido, ancho_px, alto_px), relleno)
    campo = _dilatar(mascara, radio).astype(np.float64)
    for _ in range(2):
        campo = _box_blur(campo)

    lazos = [lazo for lazo in _marching_squares(campo) if abs(_area(lazo)) >= AREA_MINIMA_PX]
    if not lazos:
        raise ValueError("El diseño no tiene contenido visible.")

    piezas = 0
    for n, lazo in enumerate(lazos):
        profundidad = sum(
            1 for m, otro in enumerate(lazos) if m != n and _dentro(lazo[0], otro)
        )
        piezas += profundidad % 2 == 0

    mayor = max(lazos, key=lambda lazo: abs(_area(lazo)))
    mayor = _simplificar_lazo(mayor, TOLERANCIA_MM * px_por_mm)
    if _area(mayor) < 0:
        mayor = mayor[::-1]

    # Las coordenadas de la rejilla son centros de píxel.
    x = (mayor[:, 0] + 0.5 - relleno) / px_por_mm + b
    y = (mayor[:, 1] + 0.5 - relleno) / px_por_mm + b
    x0, x1 = float(x.min()), float(x.max())
    y0, y1 = float(y.min()), float(y.max())
    x = (x - x0) * (w / (x1 - x0)) if x1 > x0 else x - x0
    y = (y - y0) * (h / (y1 - y0)) if y1 > y0 else y - y0
    puntos = tuple((float(px), float(py)) for px, py in zip(x, y))
    return puntos, int(piezas)


def silueta(
    info: InfoDiseno,
    ancho_mm: float,
    alto_mm: float,
    borde_mm: float,
    resolucion_px: int = 800,
) -> tuple[list[Punto], int]:
    """Contorno horario de la silueta dilatada por el borde y número de piezas."""
    if not info.tiene_alfa:
        raise ValueError("La silueta requiere un PNG con transparencia.")
    w = round(float(ancho_mm), 2)
    h = round(float(alto_mm), 2)
    b = round(float(borde_mm), 2)
    if w - 2 * b <= 0 or h - 2 * b <= 0:
        raise ValueError("El borde no deja espacio para el diseño.")
    puntos, piezas = _silueta_cache(
        info.id, info.ruta, tuple(info.contenido_px), w, h, b, int(resolucion_px)
    )
    return list(puntos), piezas
