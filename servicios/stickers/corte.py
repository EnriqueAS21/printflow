"""Archivos de corte (SVG y PDF) del acomodo de stickers, en milímetros."""

from __future__ import annotations

import io

from reportlab.lib.colors import CMYKColorSep
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .exportar import Ajuste, _ruta_pdf
from .geometria import (
    Hoja,
    Marcas,
    Pliego,
    ResultadoAcomodo,
    agrupar_pliegos_identicos,
    posiciones_marcas,
)
from .disenos import InfoDiseno
from .trazos import obtener_trazo


COLOR_CORTE_SVG = "#FF00FF"
GROSOR_CORTE_SVG = 0.1
GROSOR_CORTE_PT = 0.25
NOMBRE_TINTA_CORTE = "CutContour"

Linea = tuple[float, float, float, float]
Cuadro = tuple[float, float, float]


def lineas_marcas(hoja: Hoja, marcas: Marcas) -> list[Linea]:
    """Convierte las marcas en L en sus líneas centrales (x1, y1, x2, y2)."""
    if marcas.tipo != "L":
        return []
    lineas: list[Linea] = []
    for x, y, ancho, alto in posiciones_marcas(hoja, marcas):
        if ancho > alto:
            yc = y + alto / 2.0
            lineas.append((x, yc, x + ancho, yc))
        else:
            xc = x + ancho / 2.0
            lineas.append((xc, y, xc, y + alto))
    return lineas


def cuadros_marcas(hoja: Hoja, marcas: Marcas) -> list[Cuadro]:
    """Devuelve las marcas tipo cuadro como (x, y, lado)."""
    if marcas.tipo != "cuadro":
        return []
    return [(x, y, ancho) for x, y, ancho, _alto in posiciones_marcas(hoja, marcas)]


def _num(valor: float) -> str:
    texto = f"{float(valor):.3f}".rstrip("0").rstrip(".")
    return "0" if texto in ("", "-0") else texto


def _ajuste(ajustes: dict[str, Ajuste], id_diseno) -> Ajuste:
    return ajustes.get(str(id_diseno)) or Ajuste()


def _trazo_corte(col, ajustes: dict[str, Ajuste], disenos: dict[str, InfoDiseno] | None):
    info = (disenos or {}).get(str(col.id_diseno))
    return obtener_trazo(col, _ajuste(ajustes, col.id_diseno), info, 0.0)


def _datos_path(trazo: list[tuple]) -> str:
    partes: list[str] = []
    for comando in trazo:
        partes.append(" ".join([comando[0], *(_num(v) for v in comando[1:])]))
    return " ".join(partes)


def _trasladar_trazo(trazo: list[tuple], dx: float, dy: float) -> list[tuple]:
    trasladado = []
    for comando in trazo:
        valores = [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(comando[1:])]
        trasladado.append((comando[0], *valores))
    return trasladado


def generar_svg_corte(
    hoja: Hoja,
    marcas: Marcas,
    pliego: Pliego,
    ajustes: dict[str, Ajuste],
    incluir_marcas: bool = True,
    disenos: dict[str, InfoDiseno] | None = None,
) -> str:
    """Genera el SVG de corte con capas REGISTRO y CORTE."""
    ancho = _num(hoja.ancho_mm)
    alto = _num(hoja.alto_mm)
    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{ancho}mm" height="{alto}mm" viewBox="0 0 {ancho} {alto}">',
    ]

    if incluir_marcas and marcas.activas:
        lineas.append(
            '<g id="REGISTRO" inkscape:groupmode="layer" inkscape:label="REGISTRO">'
        )
        grosor = _num(marcas.grosor_mm)
        for x1, y1, x2, y2 in lineas_marcas(hoja, marcas):
            lineas.append(
                f'<line x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}" '
                f'stroke="#000000" stroke-width="{grosor}" fill="none"/>'
            )
        for x, y, lado in cuadros_marcas(hoja, marcas):
            lineas.append(
                f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(lado)}" '
                f'height="{_num(lado)}" fill="#000000" stroke="none"/>'
            )
        lineas.append("</g>")

    lineas.append('<g id="CORTE" inkscape:groupmode="layer" inkscape:label="CORTE">')
    for col in pliego.colocaciones:
        trazo = _trazo_corte(col, ajustes, disenos)
        lineas.append(
            f'<path d="{_datos_path(trazo)}" stroke="{COLOR_CORTE_SVG}" '
            f'stroke-width="{_num(GROSOR_CORTE_SVG)}" fill="none"/>'
        )
    lineas.append("</g>")
    lineas.append("</svg>")
    return "\n".join(lineas) + "\n"


def generar_pdf_corte(
    hoja: Hoja,
    marcas: Marcas,
    pliego: Pliego,
    ajustes: dict[str, Ajuste],
    incluir_marcas: bool = True,
    comprimir: bool = True,
    disenos: dict[str, InfoDiseno] | None = None,
) -> bytes:
    """Genera el PDF de corte con la tinta directa CutContour."""
    alto_hoja = hoja.alto_mm
    salida = io.BytesIO()
    lienzo = canvas.Canvas(
        salida,
        pagesize=(hoja.ancho_mm * mm, hoja.alto_mm * mm),
        pageCompression=1 if comprimir else 0,
    )
    lienzo.setTitle("Acomodo de stickers - corte")

    if incluir_marcas and marcas.activas:
        lienzo.saveState()
        lienzo.setStrokeColorRGB(0, 0, 0)
        lienzo.setFillColorRGB(0, 0, 0)
        lienzo.setLineWidth(marcas.grosor_mm * mm)
        for x1, y1, x2, y2 in lineas_marcas(hoja, marcas):
            lienzo.line(x1 * mm, (alto_hoja - y1) * mm, x2 * mm, (alto_hoja - y2) * mm)
        for x, y, lado in cuadros_marcas(hoja, marcas):
            lienzo.rect(x * mm, (alto_hoja - y - lado) * mm, lado * mm, lado * mm,
                        stroke=0, fill=1)
        lienzo.restoreState()

    lienzo.saveState()
    lienzo.setStrokeColor(CMYKColorSep(0, 1, 0, 0, spotName=NOMBRE_TINTA_CORTE))
    lienzo.setLineWidth(GROSOR_CORTE_PT)
    for col in pliego.colocaciones:
        trazo = _trazo_corte(col, ajustes, disenos)
        lienzo.drawPath(_ruta_pdf(lienzo, trazo, alto_hoja), stroke=1, fill=0)
    lienzo.restoreState()

    lienzo.showPage()
    lienzo.save()
    return salida.getvalue()


def pdf_corte_signmaster(
    hoja: Hoja,
    pliego: Pliego,
    ajustes: dict[str, Ajuste],
    infos: dict[str, InfoDiseno] | None,
    gris: tuple[float, float, float, float],
) -> bytes:
    """Genera una página de contornos magenta del tamaño del área gris."""
    gx0, gy0, gx1, gy1 = (float(v) for v in gris)
    ancho_mm, alto_mm = gx1 - gx0, gy1 - gy0
    salida = io.BytesIO()
    lienzo = canvas.Canvas(salida, pagesize=(ancho_mm * mm, alto_mm * mm), pageCompression=1)
    lienzo.setTitle("Acomodo de stickers - corte SignMaster")
    lienzo.saveState()
    lienzo.setStrokeColorRGB(1, 0, 1)
    lienzo.setLineWidth(GROSOR_CORTE_PT)
    for col in pliego.colocaciones:
        trazo = _trasladar_trazo(_trazo_corte(col, ajustes, infos), -gx0, -gy0)
        lienzo.drawPath(_ruta_pdf(lienzo, trazo, alto_mm), stroke=1, fill=0)
    # Marco del área gris: se corta al final como corte perimetral.
    ancho, alto = ancho_mm * mm, alto_mm * mm
    marco = lienzo.beginPath()
    marco.moveTo(0, 0)
    marco.lineTo(ancho, 0)
    marco.lineTo(ancho, alto)
    marco.lineTo(0, alto)
    marco.close()
    lienzo.drawPath(marco, stroke=1, fill=0)
    lienzo.restoreState()
    lienzo.showPage()
    lienzo.save()
    return salida.getvalue()


def _sufijo_hojas(numeros: list[int]) -> str:
    if len(numeros) == 1:
        return f"hoja_{numeros[0]}"
    consecutivas = all(b - a == 1 for a, b in zip(numeros, numeros[1:]))
    if consecutivas:
        return f"hojas_{numeros[0]}-{numeros[-1]}"
    return "hojas_" + "_".join(str(n) for n in numeros)


def archivos_corte(
    hoja: Hoja,
    marcas: Marcas,
    resultado: ResultadoAcomodo,
    ajustes: dict[str, Ajuste],
    incluir_marcas: bool = True,
    disenos: dict[str, InfoDiseno] | None = None,
) -> list[tuple[str, bytes]]:
    """Genera un PDF y un SVG de corte por cada grupo de pliegos idénticos."""
    archivos: list[tuple[str, bytes]] = []
    for numeros in agrupar_pliegos_identicos(resultado):
        pliego = resultado.pliegos[numeros[0] - 1]
        base = f"corte_{_sufijo_hojas(numeros)}"
        archivos.append(
            (f"{base}.pdf", generar_pdf_corte(hoja, marcas, pliego, ajustes, incluir_marcas,
                                                 disenos=disenos))
        )
        archivos.append(
            (
                f"{base}.svg",
                generar_svg_corte(
                    hoja, marcas, pliego, ajustes, incluir_marcas, disenos
                ).encode("utf-8"),
            )
        )
    return archivos


def archivos_corte_signmaster(
    hoja: Hoja,
    resultado: ResultadoAcomodo,
    ajustes: dict[str, Ajuste],
    infos: dict[str, InfoDiseno] | None = None,
    gris: tuple[float, float, float, float] | None = None,
) -> list[tuple[str, bytes]]:
    """Genera un PDF SignMaster por grupo de pliegos geométricamente idénticos."""
    archivos: list[tuple[str, bytes]] = []
    for numeros in agrupar_pliegos_identicos(resultado):
        pliego = resultado.pliegos[numeros[0] - 1]
        nombre = f"corte_signmaster_{_sufijo_hojas(numeros)}.pdf"
        archivos.append((nombre, pdf_corte_signmaster(hoja, pliego, ajustes, infos, gris)))
    return archivos

