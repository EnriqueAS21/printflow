import pytest

from servicios.stickers import (
    Colocacion,
    Hoja,
    Marcas,
    Pliego,
    ResultadoAcomodo,
    Sticker,
    acomodar,
    agrupar_pliegos_identicos,
    area_util,
    capacidad,
    posiciones_marcas,
    sugerir_orientacion,
)


def _interseccion_positiva(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_carta_vertical_centra_bloque_y_respeta_area_util():
    hoja = Hoja.carta()
    marcas = Marcas()
    sticker = Sticker("cuadrado", 50, 50, 9, "rectangulo")

    resultado = acomodar(hoja, marcas, [sticker], separacion_mm=3)
    x0, y0, x1, y1 = area_util(hoja, marcas)

    assert resultado.por_pliego_max == 9
    assert resultado.total == 9
    assert len(resultado.pliegos) == 1
    colocaciones = resultado.pliegos[0].colocaciones
    assert all(
        c.x_mm >= x0 - 0.01
        and c.y_mm >= y0 - 0.01
        and c.x_mm + c.ancho_mm <= x1 + 0.01
        and c.y_mm + c.alto_mm <= y1 + 0.01
        for c in colocaciones
    )

    izquierda = min(c.x_mm for c in colocaciones) - x0
    derecha = x1 - max(c.x_mm + c.ancho_mm for c in colocaciones)
    arriba = min(c.y_mm for c in colocaciones) - y0
    abajo = y1 - max(c.y_mm + c.alto_mm for c in colocaciones)
    assert izquierda == pytest.approx(derecha, abs=0.01)
    assert arriba == pytest.approx(abajo, abs=0.01)


def test_carta_horizontal_varios_pliegos_y_ultima_fila_centrada():
    hoja = Hoja.carta().girada()
    marcas = Marcas()
    resultado = acomodar(
        hoja,
        marcas,
        [Sticker("etiqueta", 60, 40, 40, "rectangulo")],
        separacion_mm=3,
    )
    x0, y0, x1, y1 = area_util(hoja, marcas)

    assert len(resultado.pliegos) == 5
    assert [len(p.colocaciones) for p in resultado.pliegos] == [9, 9, 9, 9, 4]
    ultimo = resultado.pliegos[-1].colocaciones
    filas = {}
    for colocacion in ultimo:
        filas.setdefault(round(colocacion.y_mm, 2), []).append(colocacion)
    assert sorted(map(len, filas.values())) == [1, 3]
    ultima_fila = max(filas.values(), key=len) if len(filas) == 1 else filas[max(filas)]
    if len(ultima_fila) == 3:
        ultima_fila = filas[min(filas, key=lambda y: len(filas[y]))]
    assert len(ultima_fila) == 1
    unica = ultima_fila[0]
    assert unica.x_mm + unica.ancho_mm / 2 == pytest.approx((x0 + x1) / 2, abs=0.01)

    arriba = min(c.y_mm for c in ultimo) - y0
    abajo = y1 - max(c.y_mm + c.alto_mm for c in ultimo)
    assert arriba == pytest.approx(abajo, abs=0.01)


def test_hoja_personalizada_admite_dos_diseno_de_tamanos_distintos():
    resultado = acomodar(
        Hoja(300, 200, margen_mm=5),
        Marcas(activas=False),
        [
            Sticker("grande", 120, 50, 4, "rectangulo"),
            Sticker("pequeno", 80, 80, 3, "redondeado"),
        ],
        separacion_mm=5,
    )

    assert resultado.total == 7
    assert {c.id_diseno for p in resultado.pliegos for c in p.colocaciones} == {
        "grande",
        "pequeno",
    }
    for pliego in resultado.pliegos:
        for colocacion in pliego.colocaciones:
            assert colocacion.x_mm >= 5
            assert colocacion.y_mm >= 5
            assert colocacion.x_mm + colocacion.ancho_mm <= 295
            assert colocacion.y_mm + colocacion.alto_mm <= 195


def test_sticker_mayor_que_area_util_se_omite_con_aviso():
    resultado = acomodar(
        Hoja(100, 100),
        Marcas(activas=False),
        [Sticker("gigante", 120, 80, 1, "rectangulo")],
    )

    assert resultado.total == 0
    assert resultado.pliegos == []
    assert len(resultado.avisos) == 1
    assert "120 × 80 mm" in resultado.avisos[0]
    assert "no cabe" in resultado.avisos[0]


def test_circulo_fuerza_alto_igual_al_ancho():
    sticker = Sticker("redondo", 50, 35, 2, "circulo")

    assert sticker.alto_mm == 50


def test_rellenar_ajusta_total_a_multiplos_de_capacidad():
    hoja = Hoja.carta()
    marcas = Marcas()
    sticker = Sticker("cuadrado", 50, 50, 13, "rectangulo")
    capacidad_pliego = capacidad(hoja, marcas, 50, 50, 3)

    resultado = acomodar(hoja, marcas, [sticker], separacion_mm=3, rellenar=True)

    assert resultado.total == 18
    assert resultado.total % capacidad_pliego == 0
    assert [len(p.colocaciones) for p in resultado.pliegos] == [9, 9]


@pytest.mark.parametrize("tipo", ["L", "cuadro"])
def test_marcas_quedan_en_hoja_y_fuera_de_colocaciones(tipo):
    hoja = Hoja.carta()
    marcas = Marcas(tipo=tipo)
    colocaciones = acomodar(
        hoja,
        marcas,
        [Sticker("cuadrado", 50, 50, 12, "rectangulo")],
        separacion_mm=3,
    ).pliegos[0].colocaciones
    rectangulos_marcas = posiciones_marcas(hoja, marcas)

    assert len(rectangulos_marcas) == (8 if tipo == "L" else 4)
    for x, y, ancho, alto in rectangulos_marcas:
        assert x >= 0 and y >= 0
        assert x + ancho <= hoja.ancho_mm
        assert y + alto <= hoja.alto_mm
        for colocacion in colocaciones:
            caja = (colocacion.x_mm, colocacion.y_mm, colocacion.ancho_mm, colocacion.alto_mm)
            assert not _interseccion_positiva((x, y, ancho, alto), caja)


def test_agrupa_tres_pliegos_llenos_y_uno_parcial():
    lleno = Pliego(
        1,
        [
            Colocacion("A", 10, 10, 20, 20, "rectangulo"),
            Colocacion("A", 35, 10, 20, 20, "rectangulo"),
        ],
    )
    lleno_2 = Pliego(
        2,
        [
            Colocacion("A", 10.001, 10, 20, 20, "rectangulo"),
            Colocacion("A", 35, 10, 20, 20, "rectangulo"),
        ],
    )
    lleno_3 = Pliego(
        3,
        [
            Colocacion("A", 10, 10, 20, 20, "rectangulo"),
            Colocacion("A", 35, 10, 20, 20, "rectangulo"),
        ],
    )
    parcial = Pliego(4, [Colocacion("A", 10, 10, 20, 20, "rectangulo")])
    resultado = ResultadoAcomodo([lleno, lleno_2, lleno_3, parcial], 2, 7, [])

    assert agrupar_pliegos_identicos(resultado) == [[1, 2, 3], [4]]


def test_capacidad_y_sugerencia_de_orientacion():
    hoja = Hoja.carta()
    marcas = Marcas(activas=False)

    assert capacidad(hoja, marcas, 50, 50, 3) > 0
    assert sugerir_orientacion(hoja, marcas, 40, 60, 3) == "horizontal"

def test_carta_marcas_inferiores_a_40_mm_y_superiores_a_distancia_borde():
    hoja = Hoja.carta()
    marcas = Marcas(tipo="L")
    rectangulos = posiciones_marcas(hoja, marcas)

    superiores = rectangulos[:4]
    inferiores = rectangulos[4:]
    for _x, y, _ancho, _alto in superiores:
        assert y == pytest.approx(marcas.distancia_borde_mm, abs=0.001)
    for _x, y, _ancho, alto in inferiores:
        assert y + alto == pytest.approx(279.4 - 40, abs=0.001)


def test_colocaciones_respetan_limite_inferior_de_marcas():
    hoja = Hoja.carta()
    marcas = Marcas()
    resultado = acomodar(
        hoja, marcas, [Sticker("cuadrado", 20, 20, 300, "rectangulo")], separacion_mm=2
    )
    limite = 279.4 - 40 - marcas.largo_mm - marcas.holgura_mm

    assert resultado.total == 300
    for pliego in resultado.pliegos:
        for colocacion in pliego.colocaciones:
            assert colocacion.y_mm + colocacion.alto_mm <= limite + 0.001


def test_distancia_inferior_menor_a_40_falla():
    with pytest.raises(ValueError):
        Marcas(distancia_inferior_mm=39)


def test_solicitud_distancia_inferior_menor_a_40_falla():
    from servicios.stickers.solicitud import parsear

    datos = {
        "hoja": {"ancho_mm": 215.9, "alto_mm": 279.4},
        "marcas": {"distancia_inferior_mm": 30},
        "disenos": [],
    }
    with pytest.raises(ValueError, match="distancia inferior"):
        parsear(datos, {})


def test_svg_y_pdf_usan_posiciones_de_marcas():
    import re
    import xml.etree.ElementTree as ET

    from servicios.stickers.corte import generar_svg_corte
    from servicios.stickers.exportar import generar_pdf_impresion

    hoja = Hoja.carta()
    marcas = Marcas(tipo="L")
    rectangulos = posiciones_marcas(hoja, marcas)
    pliego = Pliego(1, [])

    raiz = ET.fromstring(generar_svg_corte(hoja, marcas, pliego, {}))
    lineas = list(raiz.iter("{http://www.w3.org/2000/svg}line"))
    primera_inferior = lineas[4]
    x, y, ancho, alto = rectangulos[4]
    esperado = (x, y + alto / 2, x + ancho, y + alto / 2)
    obtenido = tuple(float(primera_inferior.get(a)) for a in ("x1", "y1", "x2", "y2"))
    assert obtenido == pytest.approx(esperado, abs=0.001)

    pdf = generar_pdf_impresion(
        hoja, marcas, ResultadoAcomodo([pliego], 0, 0, []), {}, {}, comprimir=False
    )
    contenido = b"".join(re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S))
    numero = rb"(-?\d+(?:\.\d+)?)"
    rects = [
        tuple(float(v) for v in m)
        for m in re.findall(numero + rb"\s+" + numero + rb"\s+" + numero + rb"\s+" + numero + rb"\s+re\b", contenido)
    ]
    mm = 72 / 25.4
    for x, y, ancho, alto in rectangulos:
        esperado = (x * mm, (hoja.alto_mm - y - alto) * mm, ancho * mm, alto * mm)
        assert any(
            all(abs(a - b) <= 0.001 for a, b in zip(rect, esperado)) for rect in rects
        )
