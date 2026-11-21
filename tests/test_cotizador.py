from decimal import Decimal

import pytest

from servicios.cotizador import cotizar


LISTA_PUBLICO = 1
LISTA_MAQUILA = 2


def _precio(lista_id, precio, cantidad_minima=0):
    return {
        "id_lista": lista_id,
        "cantidad_minima": cantidad_minima,
        "precio": precio,
    }


def _rollos():
    return [
        {
            "id_presentacion": indice,
            "nombre": f"Rollo {ancho:.2f}",
            "ancho_rollo": ancho,
            "margen_rollo": 0.05,
            "cargo_union": 0,
            "precios": [_precio(LISTA_PUBLICO, 1)],
        }
        for indice, ancho in enumerate((1.10, 1.60, 1.80), start=1)
    ]


def test_fijo_dtf_a3_cobra_precio_por_cantidad():
    resultado = cotizar(
        "fijo",
        [_precio(LISTA_PUBLICO, 80)],
        cantidad=3,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
    )

    assert resultado["precio_unitario"] == Decimal("80.00")
    assert resultado["subtotal"] == Decimal("240.00")


def test_lineal_multiplica_largo_por_precio():
    resultado = cotizar(
        "lineal",
        [_precio(LISTA_PUBLICO, 100)],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        largo=3,
    )

    assert resultado["precio_unitario"] == Decimal("300.00")
    assert resultado["subtotal"] == Decimal("300.00")
    assert resultado["descripcion_medida"] == "3.00 m"


def test_usa_tramo_de_publico_al_llegar_a_cantidad_minima():
    resultado = cotizar(
        "fijo",
        [
            _precio(LISTA_PUBLICO, 15, 0),
            _precio(LISTA_PUBLICO, 10, 10),
        ],
        cantidad=10,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
    )

    assert resultado["precio_unitario"] == Decimal("10.00")
    assert resultado["subtotal"] == Decimal("100.00")


def test_maquila_usa_su_precio_o_el_de_publico_si_no_tiene_filas():
    con_precio_maquila = cotizar(
        "fijo",
        [_precio(LISTA_PUBLICO, 80), _precio(LISTA_MAQUILA, 60)],
        cantidad=1,
        lista_id=LISTA_MAQUILA,
        lista_predeterminada_id=LISTA_PUBLICO,
    )
    sin_precio_maquila = cotizar(
        "fijo",
        [_precio(LISTA_PUBLICO, 80)],
        cantidad=1,
        lista_id=LISTA_MAQUILA,
        lista_predeterminada_id=LISTA_PUBLICO,
    )

    assert con_precio_maquila["precio_unitario"] == Decimal("60.00")
    assert sin_precio_maquila["precio_unitario"] == Decimal("80.00")


@pytest.mark.parametrize(
    ("minutos", "esperado"),
    [(3, Decimal("50.00")), (8, Decimal("80.00"))],
)
def test_tiempo_respeta_minutos_minimos(minutos, esperado):
    resultado = cotizar(
        "tiempo",
        [_precio(LISTA_PUBLICO, 10)],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        minutos=minutos,
        minutos_minimos=5,
    )

    assert resultado["precio_unitario"] == esperado
    assert resultado["subtotal"] == esperado


@pytest.mark.parametrize(
    ("ancho", "alto", "ancho_rollo", "area", "girada"),
    [
        (1.50, 4, 1.60, Decimal("6.00"), False),
        (4, 1.50, 1.60, Decimal("6.00"), True),
        (1.06, 2, 1.60, Decimal("2.12"), False),
    ],
)
def test_rollo_selecciona_ancho_util_y_calcula_area(
    ancho, alto, ancho_rollo, area, girada
):
    resultado = cotizar(
        "rollo",
        [],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        ancho=ancho,
        alto=alto,
        rollos=_rollos(),
    )

    assert resultado["rollo"]["ancho_rollo"] == ancho_rollo
    assert resultado["medida_por_pieza"] == area
    assert resultado["subtotal"] == area
    assert resultado["girada"] is girada


def test_rollo_usa_tramos_y_emite_aviso_si_no_cabe():
    resultado = cotizar(
        "rollo",
        [],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        ancho=3,
        alto=3,
        rollos=_rollos(),
    )

    assert resultado["rollo"]["ancho_rollo"] == 1.60
    assert resultado["tramos"] == 2
    assert resultado["ancho_tramo"] == Decimal("1.50")
    assert resultado["avisos"] == ["Se imprime en 2 tramos"]

    ancho_rollo = cotizar(
        "rollo",
        [],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        ancho=2,
        alto=2,
        cobro_rollo="ancho_rollo",
        rollos=_rollos(),
    )
    m2_real = cotizar(
        "rollo",
        [],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        ancho=2,
        alto=2,
        cobro_rollo="m2_real",
        rollos=_rollos(),
    )

    assert ancho_rollo["rollo"]["ancho_rollo"] == 1.10
    assert ancho_rollo["tramos"] == 2
    assert ancho_rollo["ancho_tramo"] == Decimal("1.00")
    assert ancho_rollo["medida_cobrada"] == Decimal("4.40")
    assert m2_real["rollo"]["ancho_rollo"] == 1.10
    assert m2_real["tramos"] == 2
    assert m2_real["ancho_tramo"] == Decimal("1.00")
    assert m2_real["medida_cobrada"] == Decimal("4.00")


def test_rollo_cobro_por_ancho_calcula_area_de_material():
    resultado = cotizar(
        "rollo",
        [],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        ancho=1.50,
        alto=4,
        cobro_rollo="ancho_rollo",
        rollos=_rollos(),
    )

    assert resultado["rollo"]["ancho_rollo"] == 1.60
    assert resultado["medida_por_pieza"] == Decimal("6.40")
    assert resultado["subtotal"] == Decimal("6.40")


def test_aplica_minimo_de_cobro():
    resultado = cotizar(
        "fijo",
        [_precio(LISTA_PUBLICO, 10)],
        cantidad=1,
        lista_id=LISTA_PUBLICO,
        lista_predeterminada_id=LISTA_PUBLICO,
        minimo_cobro=25,
    )

    assert resultado["precio_unitario"] == Decimal("25.00")
    assert resultado["subtotal"] == Decimal("25.00")
    assert resultado["aplico_minimo"] is True


def test_rechaza_cantidad_cero():
    with pytest.raises(ValueError, match="cantidad.*mayor que cero"):
        cotizar(
            "fijo",
            [_precio(LISTA_PUBLICO, 80)],
            cantidad=0,
            lista_id=LISTA_PUBLICO,
            lista_predeterminada_id=LISTA_PUBLICO,
        )
