"""Validación del JSON de acomodo que envía la interfaz de stickers."""

from __future__ import annotations

import math
import re

from . import biblioteca
from .disenos import InfoDiseno
from .contorno import silueta
from .exportar import Ajuste
from .geometria import Hoja, Marcas, Sticker


FORMAS = ("rectangulo", "redondeado", "circulo", "silueta")
MODOS = ("cubrir", "ajustar", "estirar")
TIPOS_MARCA = ("L", "cuadro")
TIPOS_TRABAJO = ("con_corte", "sin_corte")
MODOS_CON_SANGRADO = ("cubrir", "estirar")

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _objeto(valor, campo: str) -> dict:
    if not isinstance(valor, dict):
        raise ValueError(f"{campo} debe ser un objeto.")
    return valor


def _numero(datos: dict, clave: str, campo: str, minimo: float, maximo: float,
            defecto: float | None = None) -> float:
    valor = datos.get(clave, defecto)
    if valor is None:
        raise ValueError(f"Falta {campo}.")
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise ValueError(f"{campo} debe ser un número.")
    if not minimo <= valor <= maximo:
        raise ValueError(f"{campo} debe estar entre {minimo:g} y {maximo:g} mm.")
    return float(valor)


def _entero(datos: dict, clave: str, campo: str, minimo: int, maximo: int) -> int:
    valor = datos.get(clave)
    if valor is None:
        raise ValueError(f"Falta {campo}.")
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"{campo} debe ser un número entero.")
    if not minimo <= valor <= maximo:
        raise ValueError(f"{campo} debe estar entre {minimo} y {maximo}.")
    return valor


def _booleano(datos: dict, clave: str, campo: str, defecto: bool) -> bool:
    valor = datos.get(clave, defecto)
    if not isinstance(valor, bool):
        raise ValueError(f"{campo} debe ser verdadero o falso.")
    return valor


def _opcion(datos: dict, clave: str, campo: str, opciones: tuple[str, ...], defecto: str) -> str:
    valor = datos.get(clave, defecto)
    if valor not in opciones:
        raise ValueError(f"{campo} debe ser uno de: {', '.join(opciones)}.")
    return valor


def _color(datos: dict, campo: str) -> str | None:
    valor = datos.get("color_fondo")
    if valor is None:
        return None
    if not isinstance(valor, str) or not _COLOR.fullmatch(valor):
        raise ValueError(f'{campo} debe tener el formato "#RRGGBB" o ser null.')
    return valor.upper()


def _hoja(datos) -> Hoja:
    datos = _objeto(datos, "hoja")
    return Hoja(
        _numero(datos, "ancho_mm", "hoja.ancho_mm", 50, 1300),
        _numero(datos, "alto_mm", "hoja.alto_mm", 50, 1300),
        _numero(datos, "margen_mm", "hoja.margen_mm", 0, 50, defecto=5.0),
    )


def _marcas(datos) -> Marcas:
    if datos is None:
        return Marcas()
    datos = _objeto(datos, "marcas")
    defecto = Marcas()
    return Marcas(
        activas=_booleano(datos, "activas", "marcas.activas", defecto.activas),
        tipo=_opcion(datos, "tipo", "marcas.tipo", TIPOS_MARCA, defecto.tipo),
        largo_mm=_numero(datos, "largo_mm", "marcas.largo_mm", 3, 50, defecto.largo_mm),
        grosor_mm=_numero(datos, "grosor_mm", "marcas.grosor_mm", 0.1, 5, defecto.grosor_mm),
        distancia_borde_mm=_numero(
            datos, "distancia_borde_mm", "marcas.distancia_borde_mm", 0, 100,
            defecto.distancia_borde_mm,
        ),
        holgura_mm=_numero(datos, "holgura_mm", "marcas.holgura_mm", 0, 50, defecto.holgura_mm),
        distancia_inferior_mm=_numero(
            datos, "distancia_inferior_mm", "marcas.distancia_inferior_mm (distancia inferior)",
            40, 150, defecto.distancia_inferior_mm,
        ),
    )


def parsear(
    datos: dict,
    disenos_disponibles: dict[str, InfoDiseno],
    plantilla: dict | None = None,
) -> tuple[Hoja, Marcas, list[Sticker], dict[str, Ajuste], float, bool, tuple[float, float, float, float] | None, bool]:
    """Valida la solicitud; cualquier error lanza ValueError indicando el campo."""
    datos = _objeto(datos, "La solicitud")
    tipo = tipo_trabajo(datos)
    slug_plantilla = plantilla_solicitada(datos)
    ocultar_gris = _booleano(datos, "ocultar_gris", "ocultar_gris", True)
    if tipo == "sin_corte":
        hoja = _hoja(datos.get("hoja"))
        marcas = Marcas(activas=False)
        area = None
    elif slug_plantilla is not None:
        if plantilla is None or plantilla.get("slug") != slug_plantilla:
            raise ValueError("No se encontró la plantilla seleccionada.")
        hoja = Hoja(plantilla["ancho_mm"], plantilla["alto_mm"], 0)
        marcas = Marcas(activas=False)
        area = tuple(float(v) for v in plantilla["area"])
    else:
        hoja = _hoja(datos.get("hoja"))
        marcas = _marcas(datos.get("marcas"))
        area = None
    separacion = _numero(datos, "separacion_mm", "separacion_mm", 0, 50, defecto=3.0)
    rellenar = _booleano(datos, "rellenar", "rellenar", False)
    incluir_marcas_corte(datos)

    lista = datos.get("disenos")
    if not isinstance(lista, list) or not lista:
        raise ValueError("disenos debe ser una lista con al menos un diseño.")

    stickers: list[Sticker] = []
    ajustes: dict[str, Ajuste] = {}
    defecto = Ajuste()
    for indice, item in enumerate(lista):
        campo = f"disenos[{indice}]"
        item = _objeto(item, campo)
        id_diseno = item.get("id")
        if not isinstance(id_diseno, str) or id_diseno not in disenos_disponibles:
            raise ValueError(f"{campo}.id no corresponde a un diseño cargado.")
        if id_diseno in ajustes:
            raise ValueError(f"{campo}.id está repetido.")
        stickers.append(
            Sticker(
                id_diseno,
                _numero(item, "ancho_mm", f"{campo}.ancho_mm", 5, 1000),
                _numero(item, "alto_mm", f"{campo}.alto_mm", 5, 1000),
                _entero(item, "cantidad", f"{campo}.cantidad", 1, 5000),
                _opcion(item, "forma", f"{campo}.forma", FORMAS, "rectangulo"),
            )
        )
        ajustes[id_diseno] = Ajuste(
            modo=_opcion(item, "modo", f"{campo}.modo", MODOS, defecto.modo),
            sangrado_mm=_numero(item, "sangrado_mm", f"{campo}.sangrado_mm", 0, 5,
                                defecto.sangrado_mm),
            padding_mm=_numero(item, "padding_mm", f"{campo}.padding_mm", 0, 20,
                               defecto.padding_mm),
            radio_esquina_mm=_numero(item, "radio_esquina_mm", f"{campo}.radio_esquina_mm",
                                     0, 500, defecto.radio_esquina_mm),
            color_fondo=_color(item, f"{campo}.color_fondo"),
            borde_mm=_numero(item, "borde_mm", f"{campo}.borde_mm", 0.5, 10, defecto.borde_mm),
        )
        if stickers[-1].forma == "silueta" and not disenos_disponibles[id_diseno].tiene_alfa:
            raise ValueError(f"{campo}.forma: La silueta requiere un PNG con transparencia.")

    return hoja, marcas, stickers, ajustes, separacion, rellenar, area, ocultar_gris


def tipo_trabajo(datos: dict) -> str:
    """"con_corte" (marcas o plantilla) o "sin_corte" (solo impresión)."""
    return _opcion(datos, "tipo", "tipo", TIPOS_TRABAJO, "con_corte")


def plantilla_solicitada(datos: dict) -> str | None:
    """Slug de plantilla, o None si no hay o el trabajo es sin corte."""
    if tipo_trabajo(datos) == "sin_corte":
        return None
    slug = datos.get("plantilla")
    if slug is not None and not isinstance(slug, str):
        raise ValueError("plantilla debe ser un identificador o null.")
    return slug


def resolver_disenos(datos: dict, raiz) -> dict[str, InfoDiseno]:
    """Busca en la biblioteca los diseños que pide la solicitud."""
    lista = _objeto(datos, "La solicitud").get("disenos")
    if not isinstance(lista, list):
        return {}
    disenos: dict[str, InfoDiseno] = {}
    for item in lista:
        id_diseno = item.get("id") if isinstance(item, dict) else None
        if isinstance(id_diseno, str) and id_diseno not in disenos:
            try:
                disenos[id_diseno] = biblioteca.obtener(raiz, id_diseno)
            except LookupError:
                continue  # parsear informa el id que no corresponde
    return disenos


def incluir_marcas_corte(datos: dict) -> bool:
    """Indica si los archivos de corte deben llevar las marcas de registro."""
    return _booleano(datos, "incluir_marcas_corte", "incluir_marcas_corte", True)


def avisos(
    ajustes: dict[str, Ajuste],
    separacion_mm: float,
    disenos_disponibles: dict[str, InfoDiseno],
    stickers: list[Sticker] | None = None,
) -> list[str]:
    """Avisos que no impiden el acomodo, como sangrados que se enciman."""
    resultado: list[str] = []
    for sticker in stickers or []:
        info = disenos_disponibles.get(str(sticker.id_diseno))
        ajuste = ajustes.get(str(sticker.id_diseno))
        if sticker.forma != "silueta" or info is None or ajuste is None:
            continue
        _puntos, piezas = silueta(info, sticker.ancho_mm, sticker.alto_mm, ajuste.borde_mm)
        if piezas > 1:
            resultado.append(
                f"{info.nombre}: tiene piezas separadas; solo se corta la más grande. "
                "Aumenta el borde para unirlas."
            )
    for id_diseno, ajuste in ajustes.items():
        es_silueta = any(
            st.forma == "silueta" for st in stickers or [] if str(st.id_diseno) == id_diseno
        )
        if not es_silueta and ajuste.modo in MODOS_CON_SANGRADO and separacion_mm < ajuste.sangrado_mm:
            info = disenos_disponibles.get(id_diseno)
            nombre = info.nombre if info else id_diseno
            resultado.append(
                f"La separación ({separacion_mm:g} mm) es menor que el sangrado de "
                f"{nombre!r} ({ajuste.sangrado_mm:g} mm); el sangrado se encimará "
                "con los stickers vecinos."
            )
    return resultado
