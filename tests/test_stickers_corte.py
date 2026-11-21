import re
import xml.etree.ElementTree as ET

from servicios.stickers.corte import (
    archivos_corte,
    generar_pdf_corte,
    generar_svg_corte,
)
from servicios.stickers.exportar import Ajuste
from servicios.stickers.geometria import Hoja, Marcas, Sticker, acomodar
from servicios.stickers.trazos import trazo_forma


SVG = "{http://www.w3.org/2000/svg}"
INKSCAPE = "{http://www.inkscape.org/namespaces/inkscape}"


def _capa(raiz, nombre):
    for g in raiz.iter(f"{SVG}g"):
        if g.get("id") == nombre and g.get(f"{INKSCAPE}groupmode") == "layer":
            return g
    return None


def _datos():
    hoja = Hoja.carta()
    marcas = Marcas()
    stickers = [Sticker("a", 50, 40, 6, "redondeado")]
    resultado = acomodar(hoja, marcas, stickers)
    ajustes = {"a": Ajuste(radio_esquina_mm=4.0)}
    return hoja, marcas, resultado, ajustes


def test_svg_dimensiones_y_capas():
    hoja, marcas, resultado, ajustes = _datos()
    pliego = resultado.pliegos[0]
    raiz = ET.fromstring(generar_svg_corte(hoja, marcas, pliego, ajustes))
    assert raiz.get("width") == "215.9mm"
    assert raiz.get("height") == "279.4mm"
    assert raiz.get("viewBox") == "0 0 215.9 279.4"
    registro = _capa(raiz, "REGISTRO")
    corte = _capa(raiz, "CORTE")
    assert registro is not None and corte is not None
    assert len(corte.findall(f"{SVG}path")) == len(pliego.colocaciones)
    assert len(registro.findall(f"{SVG}line")) == 8


def test_svg_sin_marcas():
    hoja, marcas, resultado, ajustes = _datos()
    raiz = ET.fromstring(
        generar_svg_corte(hoja, marcas, resultado.pliegos[0], ajustes, incluir_marcas=False)
    )
    registro = _capa(raiz, "REGISTRO")
    assert registro is None or len(list(registro)) == 0


def test_svg_coordenadas_primera_colocacion():
    hoja, marcas, resultado, ajustes = _datos()
    pliego = resultado.pliegos[0]
    raiz = ET.fromstring(generar_svg_corte(hoja, marcas, pliego, ajustes))
    path = _capa(raiz, "CORTE").findall(f"{SVG}path")[0]
    numeros = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", path.get("d"))]
    esperados = [v for comando in trazo_forma(pliego.colocaciones[0], 0, 4.0) for v in comando[1:]]
    assert len(numeros) == len(esperados)
    for obtenido, esperado in zip(numeros, esperados):
        assert abs(obtenido - esperado) <= 0.001


def test_pdf_corte_cutcontour_y_mediabox():
    hoja, marcas, resultado, ajustes = _datos()
    pdf = generar_pdf_corte(hoja, marcas, resultado.pliegos[0], ajustes, comprimir=False)
    assert b"CutContour" in pdf
    caja = re.search(rb"/MediaBox\s*\[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", pdf)
    assert caja is not None
    assert abs(float(caja.group(1)) - 612.0) < 0.01
    assert abs(float(caja.group(2)) - 792.0) < 0.01


def test_nombres_por_grupo():
    hoja = Hoja.carta()
    marcas = Marcas()
    sticker = Sticker("a", 50, 40, 1, "rectangulo")
    from servicios.stickers.geometria import capacidad

    por_hoja = capacidad(hoja, marcas, 50, 40, 3.0)
    sticker.cantidad = por_hoja * 3 + 2
    resultado = acomodar(hoja, marcas, [sticker])
    assert len(resultado.pliegos) == 4
    nombres = [n for n, _ in archivos_corte(hoja, marcas, resultado, {})]
    assert nombres == [
        "corte_hojas_1-3.pdf",
        "corte_hojas_1-3.svg",
        "corte_hoja_4.pdf",
        "corte_hoja_4.svg",
    ]
