import io
import re
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image, ImageDraw

from servicios.stickers.contorno import silueta
from servicios.stickers.corte import generar_svg_corte
from servicios.stickers.disenos import cargar_diseno
from servicios.stickers.exportar import Ajuste
from servicios.stickers.geometria import Hoja, Marcas, Sticker, acomodar


def _png(dibujar, ancho=400, alto=400) -> bytes:
    imagen = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    dibujar(ImageDraw.Draw(imagen))
    salida = io.BytesIO()
    imagen.save(salida, "PNG")
    return salida.getvalue()


def _circulo(tmp_path):
    datos = _png(lambda d: d.ellipse((50, 50, 349, 349), fill=(255, 0, 0, 255)))
    return cargar_diseno(datos, "circulo.png", tmp_path)


def _rectangulo(tmp_path):
    datos = _png(lambda d: d.rectangle((40, 100, 359, 299), fill=(0, 0, 255, 255)))
    return cargar_diseno(datos, "rectangulo.png", tmp_path)


def _manchas(tmp_path):
    def dibujar(d):
        d.ellipse((20, 125, 170, 275), fill=(0, 0, 0, 255))
        d.ellipse((230, 125, 380, 275), fill=(0, 0, 0, 255))
    return cargar_diseno(_png(dibujar), "manchas.png", tmp_path)


def _caja(puntos):
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return min(xs), min(ys), max(xs), max(ys)


def test_circulo_radio(tmp_path):
    info = _circulo(tmp_path)
    puntos, piezas = silueta(info, 50, 50, 2)
    assert piezas == 1
    p = np.array(puntos)
    distancia = np.hypot(p[:, 0] - 25, p[:, 1] - 25)
    assert np.all(np.abs(distancia - 25) <= 0.3)


def test_caja_rectangulo_sin_candado(tmp_path):
    info = _rectangulo(tmp_path)
    puntos, _ = silueta(info, 80, 30, 3)
    x0, y0, x1, y1 = _caja(puntos)
    assert x0 == pytest.approx(0, abs=0.3)
    assert y0 == pytest.approx(0, abs=0.3)
    assert x1 == pytest.approx(80, abs=0.3)
    assert y1 == pytest.approx(30, abs=0.3)


def test_menos_de_400_puntos(tmp_path):
    info = _circulo(tmp_path)
    puntos, _ = silueta(info, 50, 50, 2)
    assert len(puntos) < 400


def test_jpg_error(tmp_path):
    salida = io.BytesIO()
    Image.new("RGB", (100, 100), (200, 0, 0)).save(salida, "JPEG")
    info = cargar_diseno(salida.getvalue(), "foto.jpg", tmp_path)
    with pytest.raises(ValueError, match="transparencia"):
        silueta(info, 50, 50, 2)


def test_piezas_separadas(tmp_path):
    info = _manchas(tmp_path)
    assert silueta(info, 60, 30, 1)[1] == 2
    assert silueta(info, 100, 60, 15)[1] == 1


def test_svg_corte_silueta(tmp_path):
    info = _circulo(tmp_path)
    resultado = acomodar(Hoja.carta(), Marcas(), [Sticker(info.id, 40, 40, 4, "silueta")], 3.0)
    pliego = resultado.pliegos[0]
    ajustes = {info.id: Ajuste(borde_mm=2.0)}
    svg = generar_svg_corte(Hoja.carta(), Marcas(), pliego, ajustes, True, {info.id: info})
    raiz = ET.fromstring(svg)
    capa = next(
        g for g in raiz.iter("{http://www.w3.org/2000/svg}g") if g.get("id") == "CORTE"
    )
    paths = capa.findall("{http://www.w3.org/2000/svg}path")
    assert len(paths) == len(pliego.colocaciones)
    for path, col in zip(paths, pliego.colocaciones):
        numeros = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", path.get("d"))]
        x0, y0, x1, y1 = _caja(list(zip(numeros[0::2], numeros[1::2])))
        assert x0 == pytest.approx(col.x_mm, abs=0.3)
        assert y0 == pytest.approx(col.y_mm, abs=0.3)
        assert x1 == pytest.approx(col.x_mm + col.ancho_mm, abs=0.3)
        assert y1 == pytest.approx(col.y_mm + col.alto_mm, abs=0.3)


def test_rendimiento(tmp_path):
    info = _rectangulo(tmp_path)
    inicio = time.perf_counter()
    silueta(info, 70, 45, 2.5, resolucion_px=800)
    assert time.perf_counter() - inicio < 1.5
