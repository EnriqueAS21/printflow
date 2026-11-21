"""Cotización de presentaciones sin dependencias de Flask ni de la base de datos."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP


_CENTAVO = Decimal("0.01")
_CERO = Decimal("0")


def _decimal(valor, nombre):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{nombre} debe ser un número válido") from None
    if not numero.is_finite():
        raise ValueError(f"{nombre} debe ser un número válido")
    return numero


def _redondear(valor):
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _formato(valor):
    return f"{_redondear(valor):.2f}"


def _medida_positiva(valor, nombre):
    numero = _decimal(valor, nombre)
    if numero <= _CERO:
        raise ValueError(f"{nombre} debe ser mayor que cero")
    return numero


def cotizar(
    forma,
    precios,
    cantidad,
    lista_id,
    lista_predeterminada_id,
    ancho=None,
    alto=None,
    largo=None,
    minutos=None,
    precio_libre=None,
    piezas_paquete=None,
    minutos_minimos=None,
    minimo_cobro=None,
    cobro_rollo="m2_real",
    rollos=None,
):
    """Calcula precio y medidas para una presentación de producto."""
    formas = {"fijo", "paquete", "lineal", "m2", "rollo", "tiempo", "libre"}
    if forma not in formas:
        raise ValueError("Forma de cobro desconocida")

    cantidad = _medida_positiva(cantidad, "La cantidad")

    def precio_para(filas, base):
        lista_elegida = [fila for fila in (filas or []) if fila.get("id_lista") == lista_id]
        if not lista_elegida:
            lista_elegida = [
                fila
                for fila in (filas or [])
                if fila.get("id_lista") == lista_predeterminada_id
            ]

        aplicables = [
            fila
            for fila in lista_elegida
            if _decimal(fila.get("cantidad_minima", 0), "La cantidad mínima") <= base
        ]
        if not aplicables:
            raise ValueError("Sin precio para esta presentación")
        fila = max(
            aplicables,
            key=lambda item: _decimal(item.get("cantidad_minima", 0), "La cantidad mínima"),
        )
        return _redondear(_decimal(fila.get("precio"), "El precio"))

    medida_por_pieza = None
    medida_total = None
    unidad_medida = None
    rollo_elegido = None
    tramos = None
    ancho_tramo = None
    precio_medida = None
    medida_cobrada = None
    girada = False
    descripcion_medida = ""
    avisos = []
    aplico_minimo = False

    if forma in ("fijo", "paquete"):
        precio = precio_para(precios, cantidad)
        precio_unitario = precio
        unidad_medida = "pieza" if forma == "fijo" else "paquete"

    elif forma == "lineal":
        largo = _medida_positiva(largo, "El largo")
        medida_por_pieza = largo
        medida_total = largo * cantidad
        unidad_medida = "m"
        precio = precio_para(precios, largo * cantidad)
        precio_medida = precio
        precio_unitario = largo * precio
        descripcion_medida = f"{_formato(largo)} m"

    elif forma == "m2":
        ancho = _medida_positiva(ancho, "El ancho")
        alto = _medida_positiva(alto, "El alto")
        medida_por_pieza = ancho * alto
        medida_total = medida_por_pieza * cantidad
        unidad_medida = "m²"
        precio = precio_para(precios, medida_total)
        precio_medida = precio
        medida_cobrada = medida_por_pieza
        precio_unitario = medida_por_pieza * precio
        descripcion_medida = (
            f"{_formato(ancho)} × {_formato(alto)} m "
            f"({_formato(medida_por_pieza)} m²)"
        )

    elif forma == "rollo":
        ancho = _medida_positiva(ancho, "El ancho")
        alto = _medida_positiva(alto, "El alto")
        if cobro_rollo not in ("m2_real", "ancho_rollo"):
            raise ValueError("El cobro del rollo debe ser 'm2_real' o 'ancho_rollo'")
        if not rollos:
            raise ValueError("No hay rollos disponibles para esta presentación")

        corto = min(ancho, alto)
        largo_rollo = max(ancho, alto)
        girada = ancho > alto
        opciones = []
        for rollo in rollos:
            ancho_disponible = _medida_positiva(
                rollo.get("ancho_rollo"), "El ancho del rollo"
            )
            margen = _decimal(rollo.get("margen_rollo", 0), "El margen del rollo")
            util = ancho_disponible - margen
            if util <= _CERO:
                raise ValueError("El margen debe ser menor que el ancho del rollo")
            opciones.append((ancho_disponible, util, margen, rollo))

        aptos = [opcion for opcion in opciones if opcion[1] >= corto]
        if aptos:
            tramos = 1
        else:
            _, util_maximo, _, _ = max(
                opciones, key=lambda opcion: opcion[0]
            )
            tramos = int((corto / util_maximo).to_integral_value(rounding=ROUND_CEILING))
            avisos.append(f"Se imprime en {tramos} tramos")
        ancho_tramo = corto / tramos
        candidatos = [opcion for opcion in opciones if opcion[1] >= ancho_tramo]
        ancho_disponible, util, margen, rollo_elegido = min(
            candidatos, key=lambda opcion: opcion[0]
        )

        if cobro_rollo == "m2_real":
            medida_por_pieza = ancho * alto
        else:
            medida_por_pieza = ancho_disponible * largo_rollo * tramos
        medida_cobrada = medida_por_pieza
        medida_total = medida_por_pieza * cantidad
        unidad_medida = "m²"
        precio = precio_para(rollo_elegido.get("precios"), medida_total)
        precio_medida = precio
        cargo_union = _decimal(rollo_elegido.get("cargo_union", 0), "El cargo de unión")
        precio_unitario = medida_por_pieza * precio + cargo_union * (tramos - 1)
        descripcion_medida = (
            f"ROLLO {_formato(ancho_disponible)} · {_formato(corto)} × "
            f"{_formato(largo_rollo)} m ({_formato(ancho * alto)} m²)"
        )
        rollo_elegido = dict(rollo_elegido)

    elif forma == "tiempo":
        minutos = _medida_positiva(minutos, "Los minutos")
        minimo_minutos = (
            _CERO
            if minutos_minimos is None
            else _decimal(minutos_minimos, "Los minutos mínimos")
        )
        medida_por_pieza = minutos
        medida_total = minutos * cantidad
        unidad_medida = "min"
        precio = precio_para(precios, minutos)
        precio_medida = precio
        precio_unitario = max(minutos, minimo_minutos) * precio
        descripcion_medida = f"{_formato(minutos)} min"

    else:  # libre
        if precio_libre is None:
            raise ValueError("Debe indicar el precio libre")
        precio_unitario = _redondear(_decimal(precio_libre, "El precio libre"))

    precio_unitario = _redondear(precio_unitario)
    subtotal = _redondear(precio_unitario * cantidad)
    if minimo_cobro is not None:
        minimo = _decimal(minimo_cobro, "El mínimo de cobro")
        if minimo > _CERO and subtotal < minimo:
            precio_unitario = _redondear(minimo / cantidad)
            aplico_minimo = True
            subtotal = _redondear(precio_unitario * cantidad)

    return {
        "precio_unitario": precio_unitario,
        "subtotal": subtotal,
        "medida_por_pieza": (
            _redondear(medida_por_pieza) if medida_por_pieza is not None else None
        ),
        "medida_total": _redondear(medida_total) if medida_total is not None else None,
        "unidad_medida": unidad_medida,
        "precio_medida": precio_medida,
        "medida_cobrada": (
            _redondear(medida_cobrada) if medida_cobrada is not None else None
        ),
        "rollo": rollo_elegido,
        "tramos": tramos,
        "ancho_tramo": _redondear(ancho_tramo) if ancho_tramo is not None else None,
        "girada": girada,
        "descripcion_medida": descripcion_medida,
        "avisos": avisos,
        "aplico_minimo": aplico_minimo,
    }