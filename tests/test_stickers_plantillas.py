from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from flask import Flask
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from servicios.stickers.geometria import (
    Colocacion,
    Hoja,
    Marcas,
    Pliego,
    ResultadoAcomodo,
    Sticker,
    acomodar,
)
from servicios.stickers.plantillas import PT_MM, combinar_con_plantilla, guardar_plantilla


MM_PT = 72 / 25.4


FIXTURES = Path(__file__).parent / "fixtures"


def _marcas_l(x0, y0, x1, y1):
    """Rectángulos (mm, origen arriba) de las 8 marcas en L de SignMaster."""
    marcas = []
    for cx, sx in ((x0, 1), (x1, -1)):
        for cy, sy in ((y0, 1), (y1, -1)):
            for a in ((cx, cy, cx + 20 * sx, cy + sy), (cx, cy, cx + sx, cy + 20 * sy)):
                marcas.append((min(a[0], a[2]), min(a[1], a[3]), max(a[0], a[2]), max(a[1], a[3])))
    return marcas


def _pdf_plantilla(con_area=True):
    salida = BytesIO()
    lienzo = canvas.Canvas(salida, pagesize=letter)
    lienzo.setFillColorRGB(0, 0, 0)
    for x0, y0, x1, y1 in _marcas_l(12, 10, 203.9, 230):
        lienzo.rect(x0 * MM_PT, (279.4 - y1) * MM_PT, (x1 - x0) * MM_PT, (y1 - y0) * MM_PT, fill=1, stroke=0)
    if con_area:
        lienzo.setFillGray(0.85)
        lienzo.rect(
            20 * MM_PT,
            (279.4 - 229.4) * MM_PT,
            (195.9 - 20) * MM_PT,
            (229.4 - 25) * MM_PT,
            fill=1,
            stroke=0,
        )
    lienzo.save()
    return salida.getvalue()


def _pdf_dos_paginas():
    salida = BytesIO()
    lienzo = canvas.Canvas(salida, pagesize=letter)
    for _ in range(2):
        lienzo.setFillColorRGB(0, 0, 0)
        lienzo.rect(10 * MM_PT, 10 * MM_PT, 5 * MM_PT, 5 * MM_PT, fill=1, stroke=0)
        lienzo.showPage()
    lienzo.save()
    return salida.getvalue()


def test_guardar_plantilla_detecta_area_gris(tmp_path):
    guardada = guardar_plantilla(tmp_path, _pdf_plantilla(), "Plantilla Carta")

    assert guardada["area"] == pytest.approx((20, 25, 195.9, 229.4), abs=0.5)
    assert guardada["ancho_mm"] == pytest.approx(215.9, abs=0.02)
    assert guardada["alto_mm"] == pytest.approx(279.4, abs=0.02)
    assert guardada["slug"] == "plantilla-carta"


def test_guardar_plantilla_rechaza_pdf_sin_area_gris(tmp_path):
    with pytest.raises(ValueError, match="No se encontró el área gris"):
        guardar_plantilla(tmp_path, _pdf_plantilla(con_area=False), "Sin área")


def test_guardar_plantilla_rechaza_datos_que_no_son_pdf(tmp_path):
    with pytest.raises(ValueError, match="no es un PDF"):
        guardar_plantilla(tmp_path, b"texto", "Invalido")


def test_guardar_plantilla_rechaza_mas_de_5_mb(tmp_path):
    with pytest.raises(ValueError, match="5 MB"):
        guardar_plantilla(tmp_path, b"%PDF" + b"x" * (5 * 1024 * 1024), "Grande")


def test_acomodar_usa_el_area_explicita():
    area = (20.0, 25.0, 195.9, 229.4)
    resultado = acomodar(
        Hoja.carta(),
        Marcas(),
        [Sticker("diseno", 30, 25, 12, "rectangulo")],
        separacion_mm=3,
        area=area,
    )

    assert resultado.total == 12
    assert all(
        c.x_mm >= area[0]
        and c.y_mm >= area[1]
        and c.x_mm + c.ancho_mm <= area[2]
        and c.y_mm + c.alto_mm <= area[3]
        for pliego in resultado.pliegos
        for c in pliego.colocaciones
    )


def test_combinar_con_plantilla_conserva_paginas_y_marcas(tmp_path):
    ruta_pdf = tmp_path / "carta.pdf"
    ruta_pdf.write_bytes(_pdf_plantilla())
    resultado = combinar_con_plantilla(
        _pdf_dos_paginas(),
        ruta_pdf,
        (20, 25, 195.9, 229.4),
    )

    documento = fitz.open(stream=resultado, filetype="pdf")
    try:
        assert len(documento) == 2
        for pagina in documento:
            assert pagina.rect.width == pytest.approx(612)
            assert pagina.rect.height == pytest.approx(792)
            rellenos_negros = [
                dibujo
                for dibujo in pagina.get_drawings()
                if dibujo.get("fill") == (0.0, 0.0, 0.0)
            ]
            assert len(rellenos_negros) >= 4
    finally:
        documento.close()


def _pdf_vacio(ancho_mm, alto_mm):
    salida = BytesIO()
    lienzo = canvas.Canvas(salida, pagesize=(ancho_mm * MM_PT, alto_mm * MM_PT))
    lienzo.showPage()
    lienzo.save()
    return salida.getvalue()


def _acomodo_falso(ruta_plantilla, meta=None, hoja=None):
    if meta is None:
        meta = guardar_plantilla(ruta_plantilla.parent, ruta_plantilla.read_bytes(), "Plantilla Carta")
    hoja = hoja or Hoja(215.9, 279.4, 0)
    pliego = Pliego(1, [Colocacion("d", 40, 40, 40, 30, "rectangulo")])
    return SimpleNamespace(
        tipo="con_corte",
        stickers=[],
        plantilla=meta,
        plantilla_slug=meta["slug"],
        ruta_plantilla=ruta_plantilla,
        area=tuple(meta["area"]),
        ocultar_gris=True,
        hoja=hoja,
        marcas=Marcas(activas=False),
        resultado=ResultadoAcomodo([pliego], 1, 1, []),
        ajustes={},
        disenos={},
        incluir_marcas=False,
        detalle=lambda tipo: f"{tipo}: 1 sticker, 1 hoja",
    )


def _crear_cliente_exportar(monkeypatch, tmp_path, fixture=None, hoja_fisica="carta"):
    import rutas.stickers as rutas_stickers

    if fixture is None:
        ruta_plantilla = tmp_path / "plantilla-carta.pdf"
        ruta_plantilla.write_bytes(_pdf_plantilla())
        fake = _acomodo_falso(ruta_plantilla)
    else:
        carpeta = tmp_path / "plantillas"
        meta = guardar_plantilla(carpeta, (FIXTURES / fixture).read_bytes(), "Fixture", hoja_fisica)
        fake = _acomodo_falso(
            carpeta / f"{meta['slug']}.pdf", meta, Hoja(meta["ancho_mm"], meta["alto_mm"], 0)
        )
    monkeypatch.setattr(rutas_stickers, "_preparar_acomodo", lambda: (fake, None))
    monkeypatch.setattr(rutas_stickers, "_registrar", lambda *args, **kwargs: None)

    app = Flask(__name__, instance_path=str(tmp_path), instance_relative_config=True)
    app.register_blueprint(rutas_stickers.stickers_bp, url_prefix="/stickers")
    vista = app.view_functions["stickers.exportar"]
    while hasattr(vista, "__wrapped__"):
        vista = vista.__wrapped__
    app.view_functions["stickers.exportar"] = vista
    return app.test_client(), fake


def test_api_exportar_corte_con_plantilla_devuelve_pdf_signmaster(monkeypatch, tmp_path):
    cliente, _fake = _crear_cliente_exportar(monkeypatch, tmp_path)

    respuesta = cliente.post("/stickers/api/exportar/corte?grupo=0", json={})

    assert respuesta.status_code == 200
    assert respuesta.mimetype == "application/pdf"
    assert "corte_hoja_1.pdf" in respuesta.headers["Content-Disposition"]
    pdf = fitz.open(stream=respuesta.data, filetype="pdf")
    try:
        assert pdf[0].get_images() == []
    finally:
        pdf.close()


def test_corte_signmaster_marco_magenta_cubre_pagina(monkeypatch, tmp_path):
    cliente, fake = _crear_cliente_exportar(monkeypatch, tmp_path)
    gx0, gy0, gx1, gy1 = fake.plantilla["gris"]
    ancho, alto = gx1 - gx0, gy1 - gy0
    n = len(fake.resultado.pliegos[0].colocaciones)

    respuesta = cliente.post("/stickers/api/exportar/corte?grupo=0", json={})

    assert respuesta.status_code == 200
    pdf = fitz.open(stream=respuesta.data, filetype="pdf")
    try:
        dibujos = pdf[0].get_drawings()
        union = fitz.Rect(dibujos[0]["rect"])
        for d in dibujos[1:]:
            union |= d["rect"]
        assert [v * PT_MM for v in union] == pytest.approx([0, 0, ancho, alto], abs=0.2)

        def _es(color, objetivo):
            return color is not None and color == pytest.approx(objetivo, abs=0.01)

        magentas = [d for d in dibujos if _es(d.get("color"), (1, 0, 1))]
        cian = [d for d in dibujos if _es(d.get("color"), (0, 1, 1))]
        assert len(magentas) == n + 1
        assert cian == []

        marco = dibujos[-1]["rect"]
        assert [v * PT_MM for v in marco] == pytest.approx([0, 0, ancho, alto], abs=0.2)
    finally:
        pdf.close()


def test_api_exportar_impresion_con_plantilla_respeta_tamano(monkeypatch, tmp_path):
    import rutas.stickers as rutas_stickers

    cliente, fake = _crear_cliente_exportar(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rutas_stickers,
        "generar_pdf_impresion",
        lambda *args, **kwargs: _pdf_vacio(fake.hoja.ancho_mm, fake.hoja.alto_mm),
    )

    respuesta = cliente.post("/stickers/api/exportar/impresion", json={})

    assert respuesta.status_code == 200
    documento = fitz.open(stream=respuesta.data, filetype="pdf")
    try:
        assert len(documento) == 1
        assert documento[0].rect.width == pytest.approx(612, abs=0.1)
        assert documento[0].rect.height == pytest.approx(792, abs=0.1)
    finally:
        documento.close()


def test_plantilla_carta_real_detecta_area_y_marcas(tmp_path):
    guardada = guardar_plantilla(tmp_path, (FIXTURES / "plantilla_carta.pdf").read_bytes(), "Carta", "carta")

    assert guardada["area"] == pytest.approx((31, 28.43, 186, 259.85), abs=0.3)
    assert guardada["marcas_bbox"] == pytest.approx((10, 19.35, 207, 260.35), abs=0.3)
    assert guardada["valida"] is False
    assert "6.6" in guardada["error"]


def test_plantilla_tabloide_real_es_valida(tmp_path):
    guardada = guardar_plantilla(
        tmp_path, (FIXTURES / "plantilla_tabloide.pdf").read_bytes(), "Tabloide", "tabloide"
    )

    assert guardada["valida"] is True
    assert guardada["hoja_fisica"] == "tabloide"
    assert guardada["dx"] == pytest.approx(13.9, abs=0.3)
    assert guardada["libre_abajo"] == pytest.approx(51.2, abs=0.3)


def test_guardar_plantilla_sin_marcas_l_falla(tmp_path):
    salida = BytesIO()
    lienzo = canvas.Canvas(salida, pagesize=letter)
    lienzo.setFillGray(0.85)
    lienzo.rect(20 * MM_PT, 50 * MM_PT, 175 * MM_PT, 200 * MM_PT, fill=1, stroke=0)
    lienzo.save()

    with pytest.raises(ValueError, match="8 marcas en L"):
        guardar_plantilla(tmp_path, salida.getvalue(), "Sin marcas")


def test_api_exportar_impresion_tabloide_traslada_marcas(monkeypatch, tmp_path):
    import rutas.stickers as rutas_stickers

    cliente, fake = _crear_cliente_exportar(monkeypatch, tmp_path, "plantilla_tabloide.pdf", "tabloide")
    monkeypatch.setattr(
        rutas_stickers,
        "generar_pdf_impresion",
        lambda *args, **kwargs: _pdf_vacio(fake.hoja.ancho_mm, fake.hoja.alto_mm),
    )

    respuesta = cliente.post("/stickers/api/exportar/impresion", json={})

    assert respuesta.status_code == 200
    meta = fake.plantilla
    esperadas = _marcas_l(*meta["marcas_bbox"])
    documento = fitz.open(stream=respuesta.data, filetype="pdf")
    try:
        pagina = documento[0]
        assert pagina.rect.width == pytest.approx(864, abs=0.1)
        assert pagina.rect.height == pytest.approx(1296, abs=0.1)
        negros = [
            [v * PT_MM for v in (d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)]
            for d in pagina.get_drawings()
            if d.get("fill") and all(c <= 0.05 for c in d["fill"]) and len(d["items"]) <= 20
        ]
        for x0, y0, x1, y1 in esperadas:
            trasladada = (x0 + meta["dx"], y0 + meta["dy"], x1 + meta["dx"], y1 + meta["dy"])
            assert any(r == pytest.approx(trasladada, abs=0.3) for r in negros), trasladada
    finally:
        documento.close()


def test_plantilla_tabloide_guarda_gris_original(tmp_path):
    meta = guardar_plantilla(
        tmp_path, (FIXTURES / "plantilla_tabloide.pdf").read_bytes(), "Tabloide", "tabloide"
    )

    assert meta["gris"] == pytest.approx((21.0, 0.61, 256.0, 400.61), abs=0.2)


def test_plantilla_json_viejo_recalcula_gris(tmp_path):
    import json

    from servicios.stickers.plantillas import obtener_plantilla

    meta = guardar_plantilla(
        tmp_path, (FIXTURES / "plantilla_tabloide.pdf").read_bytes(), "Tabloide", "tabloide"
    )
    ruta_json = tmp_path / f"{meta['slug']}.json"
    datos = json.loads(ruta_json.read_text(encoding="utf-8"))
    del datos["gris"]
    ruta_json.write_text(json.dumps(datos), encoding="utf-8")

    assert obtener_plantilla(tmp_path, meta["slug"])["gris"] == pytest.approx(meta["gris"], abs=0.01)
    assert json.loads(ruta_json.read_text(encoding="utf-8"))["gris"] == pytest.approx(meta["gris"], abs=0.01)


def test_api_exportar_corte_tabloide_mide_area_gris(monkeypatch, tmp_path):
    cliente, fake = _crear_cliente_exportar(monkeypatch, tmp_path, "plantilla_tabloide.pdf", "tabloide")
    gx0, gy0, gx1, gy1 = fake.plantilla["gris"]
    ancho, alto = gx1 - gx0, gy1 - gy0
    colocacion = fake.resultado.pliegos[0].colocaciones[0]

    respuesta = cliente.post("/stickers/api/exportar/corte?grupo=0", json={})

    assert respuesta.status_code == 200
    pdf = fitz.open(stream=respuesta.data, filetype="pdf")
    try:
        pagina = pdf[0]
        assert pagina.rect.width * PT_MM == pytest.approx(235, abs=0.2)
        assert pagina.rect.height * PT_MM == pytest.approx(400, abs=0.2)
        assert pagina.rect.width * PT_MM == pytest.approx(ancho, abs=0.2)
        assert pagina.rect.height * PT_MM == pytest.approx(alto, abs=0.2)

        dibujos = pagina.get_drawings()
        marco = [v * PT_MM for v in dibujos[-1]["rect"]]
        assert marco == pytest.approx([0, 0, 235, 400], abs=0.2)

        for d in dibujos[:-1]:
            x0, y0, x1, y1 = (v * PT_MM for v in d["rect"])
            assert x0 >= -0.2 and y0 >= -0.2 and x1 <= 235.2 and y1 <= 400.2

        primero = dibujos[0]["rect"]
        assert primero.x0 * PT_MM == pytest.approx(colocacion.x_mm - gx0, abs=0.2)
        assert primero.x0 * PT_MM == pytest.approx(colocacion.x_mm - 21.0, abs=0.2)
        assert primero.y0 * PT_MM == pytest.approx(colocacion.y_mm - gy0, abs=0.2)
    finally:
        pdf.close()


def test_api_exportar_plantilla_no_valida_devuelve_400(monkeypatch, tmp_path):
    cliente, fake = _crear_cliente_exportar(monkeypatch, tmp_path, "plantilla_carta.pdf", "carta")

    respuesta = cliente.post("/stickers/api/exportar/impresion", json={})

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == fake.plantilla["error"]
