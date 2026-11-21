import re

import pytest
from PIL import Image

from servicios.stickers import exportar
from servicios.stickers.disenos import InfoDiseno
from servicios.stickers.exportar import Ajuste, generar_pdf_impresion
from servicios.stickers.geometria import (
    Colocacion,
    Hoja,
    Marcas,
    Pliego,
    ResultadoAcomodo,
)
from servicios.stickers.trazos import caja_trazo, trazo_forma


def _diseno(tmp_path, id_diseno="a", lado=1200):
    ruta = tmp_path / f"{id_diseno}.png"
    Image.new("RGBA", (lado, lado), (255, 0, 0, 255)).save(ruta)
    return InfoDiseno(
        id=id_diseno, nombre=id_diseno, ruta=str(ruta), ancho_px=lado, alto_px=lado,
        tiene_alfa=True, dpi=None, contenido_px=(0, 0, lado, lado), radio_px=lado * 0.7,
    )


def _resultado(pliegos):
    total = sum(len(p.colocaciones) for p in pliegos)
    maximo = max((len(p.colocaciones) for p in pliegos), default=0)
    return ResultadoAcomodo(pliegos, maximo, total, [])


def _cuenta_paginas(pdf):
    return len(re.findall(rb"/Type /Page[\n ]", pdf))


def _contenido(pdf):
    return b"".join(re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S))


def test_caja_circulo():
    col = Colocacion("a", 10, 10, 50, 50, "circulo")
    assert caja_trazo(trazo_forma(col)) == pytest.approx((10, 10, 60, 60))
    assert caja_trazo(trazo_forma(col, 2)) == pytest.approx((8, 8, 62, 62))


def test_redondeado_radio_grande_se_limita():
    col = Colocacion("a", 0, 0, 20, 10, "redondeado")
    trazo = trazo_forma(col, 0, radio_esquina_mm=50)
    assert trazo[0] == ("M", 5.0, 0.0)
    assert caja_trazo(trazo) == pytest.approx((0, 0, 20, 10))


def test_dos_pliegos_carta(tmp_path):
    disenos = {"a": _diseno(tmp_path)}
    pliegos = [
        Pliego(1, [Colocacion("a", 30, 30, 50, 50, "circulo")]),
        Pliego(2, [Colocacion("a", 30, 30, 50, 50, "redondeado")]),
    ]
    pdf = generar_pdf_impresion(
        Hoja.carta(), Marcas(), _resultado(pliegos), disenos, {}, comprimir=False
    )
    assert _cuenta_paginas(pdf) == 2
    assert b"/MediaBox [ 0 0 612 792 ]" in pdf or b"/MediaBox [0 0 612 792]" in pdf


def test_marcas_dibujan_relleno():
    resultado = _resultado([Pliego(1, [])])
    con = _contenido(generar_pdf_impresion(
        Hoja.carta(), Marcas(activas=True), resultado, {}, {}, comprimir=False))
    sin = _contenido(generar_pdf_impresion(
        Hoja.carta(), Marcas(activas=False), resultado, {}, {}, comprimir=False))
    patron = rb"(\sre\s|\sf\s)"
    assert re.search(patron, con)
    assert not re.search(patron, sin)


def test_diseno_faltante():
    resultado = _resultado([Pliego(1, [Colocacion("x", 30, 30, 50, 50, "circulo")])])
    with pytest.raises(ValueError, match="x"):
        generar_pdf_impresion(Hoja.carta(), Marcas(), resultado, {}, {}, comprimir=False)


def test_reduccion_una_vez_por_diseno(tmp_path, monkeypatch):
    disenos = {"a": _diseno(tmp_path, "a", 2000), "b": _diseno(tmp_path, "b", 2000)}
    llamadas = []
    original = exportar._reducir_a_300

    def contador(info, ancho_mm):
        llamadas.append(info.id)
        return original(info, ancho_mm)

    monkeypatch.setattr(exportar, "_reducir_a_300", contador)
    cols = [Colocacion("a" if i % 2 else "b", 30 + (i % 5) * 30, 30 + (i // 5) * 30,
                       25, 25, "rectangulo") for i in range(20)]
    pdf = generar_pdf_impresion(
        Hoja.carta(), Marcas(), _resultado([Pliego(1, cols)]), disenos,
        {"a": Ajuste(color_fondo="#FFFFFF")}, guia_corte=True, comprimir=False,
    )
    assert sorted(llamadas) == ["a", "b"]
    assert _cuenta_paginas(pdf) == 1
