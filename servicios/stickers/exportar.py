"""Exportación del acomodo de stickers a PDF de impresión, en milímetros."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .disenos import MM_POR_PULGADA, InfoDiseno, ModoColocacion, colocar
from .geometria import Hoja, Marcas, ResultadoAcomodo, posiciones_marcas
from .trazos import obtener_trazo


DPI_MAXIMO = 300.0
GROSOR_GUIA_PT = 0.25


@dataclass
class Ajuste:
    """Ajuste de colocación de un diseño dentro de su caja de corte."""

    modo: ModoColocacion = "cubrir"
    sangrado_mm: float = 1.5
    padding_mm: float = 2.0
    radio_esquina_mm: float = 3.0
    color_fondo: str | None = None
    borde_mm: float = 2.0


def _reducir_a_300(info: InfoDiseno, ancho_mm: float) -> ImageReader | str:
    """Reduce la imagen a 300 dpi para el ancho dado si la supera."""
    dpi = info.ancho_px / (ancho_mm / MM_POR_PULGADA)
    if dpi <= DPI_MAXIMO:
        return info.ruta
    ancho_px = max(1, round(ancho_mm / MM_POR_PULGADA * DPI_MAXIMO))
    alto_px = max(1, round(info.alto_px * ancho_px / info.ancho_px))
    with Image.open(info.ruta) as imagen:
        reducida = imagen.resize((ancho_px, alto_px), Image.LANCZOS)
    salida = io.BytesIO()
    reducida.save(salida, "PNG")
    salida.seek(0)
    return ImageReader(salida)


def _ruta_pdf(lienzo: canvas.Canvas, trazo: list[tuple], alto_hoja: float):
    ruta = lienzo.beginPath()
    for comando in trazo:
        tipo = comando[0]
        if tipo == "M":
            ruta.moveTo(comando[1] * mm, (alto_hoja - comando[2]) * mm)
        elif tipo == "L":
            ruta.lineTo(comando[1] * mm, (alto_hoja - comando[2]) * mm)
        elif tipo == "C":
            x1, y1, x2, y2, x, y = comando[1:]
            ruta.curveTo(
                x1 * mm, (alto_hoja - y1) * mm,
                x2 * mm, (alto_hoja - y2) * mm,
                x * mm, (alto_hoja - y) * mm,
            )
        elif tipo == "Z":
            ruta.close()
    return ruta


def generar_pdf_impresion(
    hoja: Hoja,
    marcas: Marcas,
    resultado: ResultadoAcomodo,
    disenos: dict[str, InfoDiseno],
    ajustes: dict[str, Ajuste],
    guia_corte: bool = False,
    comprimir: bool = True,
) -> bytes:
    """Genera el PDF de impresión con una página por pliego."""
    alto_hoja = hoja.alto_mm
    rect_marcas = posiciones_marcas(hoja, marcas)

    # Primero se ubican todas las colocaciones para reducir cada imagen una sola vez,
    # al tamaño más grande con el que se imprime.
    ubicaciones = []
    ancho_maximo: dict[str, float] = {}
    for pliego in resultado.pliegos:
        ubic_pliego = []
        for col in pliego.colocaciones:
            id_diseno = str(col.id_diseno)
            info = disenos.get(id_diseno)
            if info is None:
                raise ValueError(f"Falta el diseño {id_diseno!r}.")
            ajuste = ajustes.get(id_diseno) or Ajuste()
            ubic = colocar(
                info, col.ancho_mm, col.alto_mm, col.forma,
                ajuste.modo, ajuste.sangrado_mm, ajuste.padding_mm, ajuste.borde_mm,
            )
            ancho_maximo[id_diseno] = max(ancho_maximo.get(id_diseno, 0.0), ubic.ancho)
            ubic_pliego.append((col, info, ajuste, ubic))
        ubicaciones.append(ubic_pliego)

    imagenes: dict[str, ImageReader | str] = {}
    for id_diseno, ancho in ancho_maximo.items():
        imagenes[id_diseno] = _reducir_a_300(disenos[id_diseno], ancho)

    salida = io.BytesIO()
    lienzo = canvas.Canvas(
        salida,
        pagesize=(hoja.ancho_mm * mm, hoja.alto_mm * mm),
        pageCompression=1 if comprimir else 0,
    )
    lienzo.setTitle("Acomodo de stickers - impresión")

    for ubic_pliego in ubicaciones:
        for col, info, ajuste, ubic in ubic_pliego:
            expansion = ajuste.sangrado_mm if ubic.recorte == "sangrado" else 0.0
            trazo = obtener_trazo(col, ajuste, info, expansion)
            lienzo.saveState()
            if ubic.recorte is not None:
                lienzo.clipPath(_ruta_pdf(lienzo, trazo, alto_hoja), stroke=0, fill=0)
            if ajuste.color_fondo:
                lienzo.setFillColor(HexColor(ajuste.color_fondo))
                lienzo.drawPath(_ruta_pdf(lienzo, trazo, alto_hoja), stroke=0, fill=1)
            x = col.x_mm + ubic.x
            y = col.y_mm + ubic.y
            lienzo.drawImage(
                imagenes[str(col.id_diseno)],
                x * mm,
                (alto_hoja - y - ubic.alto) * mm,
                width=ubic.ancho * mm,
                height=ubic.alto * mm,
                mask="auto",
            )
            lienzo.restoreState()

        if guia_corte:
            lienzo.saveState()
            lienzo.setStrokeGray(0.5)
            lienzo.setLineWidth(GROSOR_GUIA_PT)
            for col, info, ajuste, _ubic in ubic_pliego:
                trazo = obtener_trazo(col, ajuste, info, 0.0)
                lienzo.drawPath(_ruta_pdf(lienzo, trazo, alto_hoja), stroke=1, fill=0)
            lienzo.restoreState()

        if rect_marcas:
            lienzo.saveState()
            lienzo.setFillColorRGB(0, 0, 0)
            for x, y, ancho, alto in rect_marcas:
                lienzo.rect(x * mm, (alto_hoja - y - alto) * mm, ancho * mm, alto * mm,
                            stroke=0, fill=1)
            lienzo.restoreState()

        lienzo.showPage()

    if not resultado.pliegos:
        lienzo.showPage()
    lienzo.save()
    return salida.getvalue()
