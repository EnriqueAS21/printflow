"""Carga de diseños de sticker y su colocación dentro de la caja de corte, en milímetros."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from math import hypot
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from .geometria import FormaSticker


ModoColocacion = Literal["cubrir", "ajustar", "estirar"]
TipoRecorte = Literal["sangrado", "forma"]

MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELES = 60_000_000
UMBRAL_ALFA = 8
LADO_MINIATURA = 300
DPI_PDF = 300
DPI_MINIMO = 150.0
MM_POR_PULGADA = 25.4

_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"
_FIRMA_JPG = b"\xff\xd8\xff"
_FIRMA_PDF = b"%PDF-"


@dataclass
class InfoDiseno:
    """Datos de un diseño guardado; las medidas de contenido están en píxeles."""

    id: str
    nombre: str
    ruta: str
    ancho_px: int
    alto_px: int
    tiene_alfa: bool
    dpi: float | None
    contenido_px: tuple[int, int, int, int]
    radio_px: float


@dataclass
class Ubicacion:
    """Rectángulo de la imagen completa relativo a la esquina de la caja de corte."""

    x: float
    y: float
    ancho: float
    alto: float
    recorte: TipoRecorte | None


def _pdf_a_png(archivo_bytes: bytes) -> bytes:
    try:
        import fitz
    except ImportError:
        raise ValueError("No se pueden leer archivos PDF en este equipo.") from None
    try:
        with fitz.open(stream=archivo_bytes, filetype="pdf") as documento:
            if documento.page_count < 1:
                raise ValueError("El PDF no tiene páginas.")
            pixmap = documento[0].get_pixmap(dpi=DPI_PDF, alpha=True)
            return pixmap.tobytes("png")
    except ValueError:
        raise
    except Exception:
        raise ValueError("El archivo PDF está dañado o no se puede leer.") from None


def _abrir_imagen(datos: bytes) -> Image.Image:
    try:
        imagen = Image.open(io.BytesIO(datos))
    except Exception:
        raise ValueError("La imagen está dañada o no se puede leer.") from None
    ancho, alto = imagen.size
    if ancho * alto > MAX_PIXELES:
        raise ValueError("La imagen supera el máximo de 60 megapíxeles.")
    try:
        imagen.load()
    except Exception:
        raise ValueError("La imagen está dañada o no se puede leer.") from None
    return imagen


def _leer_dpi(imagen: Image.Image) -> float | None:
    dpi = imagen.info.get("dpi")
    if not dpi:
        return None
    valor = float(dpi[0] if isinstance(dpi, tuple) else dpi)
    return valor if valor > 1 else None


def _medir_contenido(
    imagen: Image.Image, tiene_alfa: bool
) -> tuple[tuple[int, int, int, int], float]:
    ancho, alto = imagen.size
    if not tiene_alfa:
        return (0, 0, ancho, alto), hypot(ancho, alto) / 2.0

    mascara = np.asarray(imagen.getchannel("A")) > UMBRAL_ALFA
    filas = np.flatnonzero(mascara.any(axis=1))
    if filas.size == 0:
        raise ValueError("El diseño es completamente transparente.")
    columnas = np.flatnonzero(mascara.any(axis=0))
    x0, x1 = int(columnas[0]), int(columnas[-1]) + 1
    y0, y1 = int(filas[0]), int(filas[-1]) + 1
    ccx = (x0 + x1) / 2.0
    ccy = (y0 + y1) / 2.0

    # En cada fila el píxel más lejano del centro es el primero o el último opaco;
    # se mide hasta la esquina exterior de ese píxel.
    mascara_filas = mascara[filas]
    izquierda = mascara_filas.argmax(axis=1)
    derecha = ancho - 1 - mascara_filas[:, ::-1].argmax(axis=1)
    dx = np.maximum(np.abs(izquierda - ccx), np.abs(derecha + 1 - ccx))
    dy = np.maximum(np.abs(filas - ccy), np.abs(filas + 1 - ccy))
    radio = float(np.sqrt(dx * dx + dy * dy).max())
    return (x0, y0, x1, y1), radio


def cargar_diseno(archivo_bytes: bytes, nombre: str, carpeta: str | Path) -> InfoDiseno:
    """Valida, guarda y mide un diseño PNG, JPG o PDF."""
    if len(archivo_bytes) > MAX_BYTES:
        raise ValueError("El archivo supera el máximo de 25 MB.")

    dpi: float | None
    if archivo_bytes.startswith(_FIRMA_PDF):
        datos = _pdf_a_png(archivo_bytes)
        es_jpg = False
        dpi = float(DPI_PDF)
    elif archivo_bytes.startswith(_FIRMA_PNG):
        datos = archivo_bytes
        es_jpg = False
        dpi = None
    elif archivo_bytes.startswith(_FIRMA_JPG):
        datos = archivo_bytes
        es_jpg = True
        dpi = None
    else:
        raise ValueError("Formato no admitido: el archivo debe ser PNG, JPG o PDF.")

    imagen = _abrir_imagen(datos)
    if dpi is None:
        dpi = _leer_dpi(imagen)

    if es_jpg:
        tiene_alfa = False
        imagen = imagen.convert("RGB")
    else:
        tiene_alfa = "A" in imagen.getbands() or "transparency" in imagen.info
        imagen = imagen.convert("RGBA")

    contenido, radio = _medir_contenido(imagen, tiene_alfa)

    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    id_diseno = uuid.uuid4().hex
    if es_jpg:
        ruta = carpeta / f"{id_diseno}.jpg"
        ruta.write_bytes(archivo_bytes)
    else:
        ruta = carpeta / f"{id_diseno}.png"
        imagen.save(ruta, "PNG")

    miniatura = imagen.copy()
    miniatura.thumbnail((LADO_MINIATURA, LADO_MINIATURA))
    miniatura.save(carpeta / f"{id_diseno}_mini.png", "PNG")

    return InfoDiseno(
        id=id_diseno,
        nombre=nombre,
        ruta=str(ruta),
        ancho_px=imagen.width,
        alto_px=imagen.height,
        tiene_alfa=tiene_alfa,
        dpi=dpi,
        contenido_px=contenido,
        radio_px=radio,
    )


def colocar(
    info: InfoDiseno,
    ancho_mm: float,
    alto_mm: float,
    forma: FormaSticker,
    modo: ModoColocacion,
    sangrado_mm: float = 1.5,
    padding_mm: float = 2.0,
    borde_mm: float = 2.0,
) -> Ubicacion:
    """Calcula dónde dibujar la imagen para centrar su contenido en la caja de corte."""
    x0, y0, x1, y1 = info.contenido_px
    cw = x1 - x0
    ch = y1 - y0
    ccx = (x0 + x1) / 2.0
    ccy = (y0 + y1) / 2.0
    if cw <= 0 or ch <= 0:
        raise ValueError("El diseño no tiene contenido visible.")

    w = float(ancho_mm)
    h = float(alto_mm)
    b = float(sangrado_mm)
    p = float(padding_mm)

    recorte: TipoRecorte | None
    if forma == "silueta":
        borde = float(borde_mm)
        if w - 2 * borde <= 0 or h - 2 * borde <= 0:
            raise ValueError("El borde no deja espacio para el diseño.")
        sx = (w - 2 * borde) / cw
        sy = (h - 2 * borde) / ch
        recorte = None
    elif modo == "cubrir":
        sx = sy = max((w + 2 * b) / cw, (h + 2 * b) / ch)
        recorte = "sangrado"
    elif modo == "ajustar":
        if w - 2 * p <= 0 or h - 2 * p <= 0:
            raise ValueError("El padding no deja espacio para el diseño.")
        if forma == "circulo":
            if info.radio_px <= 0:
                raise ValueError("El diseño no tiene contenido visible.")
            sx = sy = (w / 2.0 - p) / info.radio_px
        else:
            sx = sy = min((w - 2 * p) / cw, (h - 2 * p) / ch)
        recorte = "forma"
    elif modo == "estirar":
        sx = (w + 2 * b) / cw
        sy = (h + 2 * b) / ch
        recorte = "sangrado"
    else:
        raise ValueError('El modo debe ser "cubrir", "ajustar" o "estirar".')

    return Ubicacion(
        x=w / 2.0 - ccx * sx,
        y=h / 2.0 - ccy * sy,
        ancho=info.ancho_px * sx,
        alto=info.alto_px * sy,
        recorte=recorte,
    )


def dpi_efectivo(info: InfoDiseno, ubic: Ubicacion) -> float:
    """Resolución a la que se imprimirá la imagen con esta ubicación."""
    return info.ancho_px / (ubic.ancho / MM_POR_PULGADA)


def aviso_resolucion(
    info: InfoDiseno, ubic: Ubicacion, minimo: float = DPI_MINIMO
) -> str | None:
    """Devuelve un aviso si la resolución efectiva es menor que el mínimo."""
    dpi = dpi_efectivo(info, ubic)
    if dpi >= minimo:
        return None
    return (
        f"El diseño {info.nombre!r} se imprimirá a {dpi:.0f} dpi; "
        f"se recomiendan al menos {minimo:.0f} dpi."
    )
