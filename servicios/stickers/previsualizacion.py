"""Vista previa en SVG de un pliego, con la misma geometría que el PDF de impresión."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from .corte import COLOR_CORTE_SVG, _ajuste, _datos_path, _num, _trazo_corte
from .disenos import InfoDiseno, colocar
from .exportar import Ajuste
from .geometria import Hoja, Marcas, Pliego, area_util, posiciones_marcas
from .trazos import obtener_trazo


GROSOR_CORTE_VISTA = 0.25
COLOR_BORDE_HOJA = "#9E9E9E"
COLOR_AREA_UTIL = "#C8C8C8"


def svg_previsualizacion(
    hoja: Hoja,
    marcas: Marcas,
    pliego: Pliego,
    disenos: dict[str, InfoDiseno],
    ajustes: dict[str, Ajuste],
    url_miniatura: Callable[[str], str],
) -> str:
    """Genera el SVG de vista previa de un pliego, en milímetros."""
    ancho = _num(hoja.ancho_mm)
    alto = _num(hoja.alto_mm)
    defs: list[str] = []
    cuerpo: list[str] = []

    for indice, col in enumerate(pliego.colocaciones):
        id_diseno = str(col.id_diseno)
        info = disenos.get(id_diseno)
        if info is None:
            raise ValueError(f"Falta el diseño {id_diseno!r}.")
        ajuste = _ajuste(ajustes, id_diseno)
        ubic = colocar(
            info, col.ancho_mm, col.alto_mm, col.forma,
            ajuste.modo, ajuste.sangrado_mm, ajuste.padding_mm, ajuste.borde_mm,
        )
        expansion = ajuste.sangrado_mm if ubic.recorte == "sangrado" else 0.0
        contorno = _datos_path(obtener_trazo(col, ajuste, info, expansion))
        if ubic.recorte is None:
            cuerpo.append("<g>")
        else:
            id_clip = f"recorte-{indice}"
            defs.append(f'<clipPath id="{id_clip}"><path d="{contorno}"/></clipPath>')
            cuerpo.append(f'<g clip-path="url(#{id_clip})">')
        if ajuste.color_fondo:
            cuerpo.append(f'<path d="{contorno}" fill="{ajuste.color_fondo}" stroke="none"/>')
        cuerpo.append(
            f'<image href="{escape(url_miniatura(id_diseno), quote=True)}" '
            f'x="{_num(col.x_mm + ubic.x)}" y="{_num(col.y_mm + ubic.y)}" '
            f'width="{_num(ubic.ancho)}" height="{_num(ubic.alto)}" '
            'preserveAspectRatio="none"/>'
        )
        cuerpo.append("</g>")

    for col in pliego.colocaciones:
        corte = _datos_path(_trazo_corte(col, ajustes, disenos))
        cuerpo.append(
            f'<path class="corte" d="{corte}" stroke="{COLOR_CORTE_SVG}" '
            f'stroke-width="{_num(GROSOR_CORTE_VISTA)}" fill="none"/>'
        )

    for x, y, ancho_marca, alto_marca in posiciones_marcas(hoja, marcas):
        cuerpo.append(
            f'<rect class="marca" x="{_num(x)}" y="{_num(y)}" width="{_num(ancho_marca)}" '
            f'height="{_num(alto_marca)}" fill="#000000" stroke="none"/>'
        )

    x0, y0, x1, y1 = area_util(hoja, marcas)
    lineas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {ancho} {alto}">',
        f'<defs>{"".join(defs)}</defs>',
        f'<rect x="0" y="0" width="{ancho}" height="{alto}" fill="#FFFFFF" '
        f'stroke="{COLOR_BORDE_HOJA}" stroke-width="0.5"/>',
        f'<rect x="{_num(x0)}" y="{_num(y0)}" width="{_num(x1 - x0)}" height="{_num(y1 - y0)}" '
        f'fill="none" stroke="{COLOR_AREA_UTIL}" stroke-width="0.3" stroke-dasharray="2 1.5"/>',
        *cuerpo,
        "</svg>",
    ]
    return "\n".join(lineas)
