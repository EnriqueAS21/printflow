"""Cálculo geométrico de acomodo de stickers, expresado en milímetros."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Literal


FormaSticker = Literal["rectangulo", "redondeado", "circulo", "silueta"]
TipoMarca = Literal["L", "cuadro"]
Rectangulo = tuple[float, float, float, float]


@dataclass
class Hoja:
    """Dimensiones de una hoja, con origen geométrico en la esquina superior izquierda."""

    ancho_mm: float
    alto_mm: float
    margen_mm: float = 5.0

    def __post_init__(self) -> None:
        self.ancho_mm = float(self.ancho_mm)
        self.alto_mm = float(self.alto_mm)
        self.margen_mm = float(self.margen_mm)
        if self.ancho_mm <= 0 or self.alto_mm <= 0:
            raise ValueError("El ancho y el alto de la hoja deben ser mayores que cero.")
        if self.margen_mm < 0:
            raise ValueError("El margen de la hoja no puede ser negativo.")

    @classmethod
    def carta(cls) -> Hoja:
        """Crea una hoja carta en orientación vertical."""
        return cls(215.9, 279.4)

    @classmethod
    def tabloide_12x18(cls) -> Hoja:
        """Crea una hoja tabloide de 12 × 18 pulgadas en orientación vertical."""
        return cls(304.8, 457.2)

    def girada(self) -> Hoja:
        """Devuelve una hoja con el ancho y el alto intercambiados."""
        return Hoja(self.alto_mm, self.ancho_mm, self.margen_mm)


@dataclass
class Marcas:
    """Marcas de registro que deben quedar libres alrededor de los stickers."""

    activas: bool = True
    tipo: TipoMarca = "L"
    largo_mm: float = 10.0
    grosor_mm: float = 0.5
    distancia_borde_mm: float = 10.0
    holgura_mm: float = 3.0
    distancia_inferior_mm: float = 40.0

    def __post_init__(self) -> None:
        if self.tipo not in ("L", "cuadro"):
            raise ValueError('El tipo de marca debe ser "L" o "cuadro".')
        self.largo_mm = float(self.largo_mm)
        self.grosor_mm = float(self.grosor_mm)
        self.distancia_borde_mm = float(self.distancia_borde_mm)
        self.holgura_mm = float(self.holgura_mm)
        self.distancia_inferior_mm = float(self.distancia_inferior_mm)
        if min(self.largo_mm, self.grosor_mm, self.distancia_borde_mm, self.holgura_mm) < 0:
            raise ValueError("Las dimensiones y distancias de las marcas no pueden ser negativas.")
        if self.distancia_inferior_mm < 40:
            raise ValueError("Las marcas inferiores deben estar a mínimo 40 mm del borde.")


@dataclass
class Sticker:
    """Diseño de sticker y cantidad solicitada."""

    id_diseno: str | int
    ancho_mm: float
    alto_mm: float
    cantidad: int
    forma: FormaSticker

    def __post_init__(self) -> None:
        self.ancho_mm = float(self.ancho_mm)
        self.alto_mm = float(self.alto_mm)
        if self.forma not in ("rectangulo", "redondeado", "circulo", "silueta"):
            raise ValueError(
                'La forma debe ser "rectangulo", "redondeado", "circulo" o "silueta".'
            )
        if self.ancho_mm <= 0 or self.alto_mm <= 0:
            raise ValueError("El ancho y el alto del sticker deben ser mayores que cero.")
        if self.cantidad < 0:
            raise ValueError("La cantidad de stickers no puede ser negativa.")
        if self.forma == "circulo":
            self.alto_mm = self.ancho_mm


@dataclass
class Colocacion:
    """Caja de la línea de corte de un sticker colocado sobre una hoja."""

    id_diseno: str | int
    x_mm: float
    y_mm: float
    ancho_mm: float
    alto_mm: float
    forma: FormaSticker


@dataclass
class Pliego:
    """Un pliego y sus colocaciones en el orden de corte."""

    numero: int
    colocaciones: list[Colocacion]


@dataclass
class ResultadoAcomodo:
    """Resultado completo de un cálculo de acomodo."""

    pliegos: list[Pliego]
    por_pliego_max: int
    total: int
    avisos: list[str]


@dataclass(frozen=True)
class _Instancia:
    id_diseno: str | int
    ancho_mm: float
    alto_mm: float
    forma: FormaSticker


def area_util(hoja: Hoja, marcas: Marcas) -> tuple[float, float, float, float]:
    """Devuelve los límites (x0, y0, x1, y1) del área imprimible y cortable."""
    if marcas.activas:
        retranqueo = max(
            hoja.margen_mm,
            marcas.distancia_borde_mm + marcas.largo_mm + marcas.holgura_mm,
        )
        retranqueo_inferior = max(
            hoja.margen_mm,
            marcas.distancia_inferior_mm + marcas.largo_mm + marcas.holgura_mm,
        )
    else:
        retranqueo = hoja.margen_mm
        retranqueo_inferior = hoja.margen_mm
    return (
        retranqueo,
        retranqueo,
        hoja.ancho_mm - retranqueo,
        hoja.alto_mm - retranqueo_inferior,
    )


def posiciones_marcas(hoja: Hoja, marcas: Marcas) -> list[Rectangulo]:
    """Devuelve las marcas sólidas como cajas (x, y, ancho, alto), en milímetros."""
    if not marcas.activas:
        return []

    d = marcas.distancia_borde_mm
    di = marcas.distancia_inferior_mm
    largo = marcas.largo_mm
    ancho_hoja = hoja.ancho_mm
    alto_hoja = hoja.alto_mm
    if marcas.tipo == "cuadro":
        return [
            (d, d, largo, largo),
            (ancho_hoja - d - largo, d, largo, largo),
            (d, alto_hoja - di - largo, largo, largo),
            (ancho_hoja - d - largo, alto_hoja - di - largo, largo, largo),
        ]

    t = marcas.grosor_mm
    # Cada par de segmentos comparte el vértice más cercano a la esquina.
    return [
        (d, d, largo, t),
        (d, d, t, largo),
        (ancho_hoja - d - largo, d, largo, t),
        (ancho_hoja - d - t, d, t, largo),
        (d, alto_hoja - di - t, largo, t),
        (d, alto_hoja - di - largo, t, largo),
        (ancho_hoja - d - largo, alto_hoja - di - t, largo, t),
        (ancho_hoja - d - t, alto_hoja - di - largo, t, largo),
    ]


def capacidad(
    hoja: Hoja,
    marcas: Marcas,
    ancho: float,
    alto: float,
    separacion: float,
    area: tuple[float, float, float, float] | None = None,
) -> int:
    """Calcula cuántos stickers iguales caben usando filas y sin girarlos."""
    if ancho <= 0 or alto <= 0 or separacion < 0:
        return 0
    x0, y0, x1, y1 = area if area is not None else area_util(hoja, marcas)
    ancho_util = max(0.0, x1 - x0)
    alto_util = max(0.0, y1 - y0)
    columnas = floor((ancho_util + separacion) / (ancho + separacion))
    filas = floor((alto_util + separacion) / (alto + separacion))
    return max(0, columnas) * max(0, filas)


def sugerir_orientacion(
    hoja: Hoja,
    marcas: Marcas,
    ancho: float,
    alto: float,
    separacion: float,
) -> Literal["vertical", "horizontal"] | None:
    """Sugiere la orientación de hoja con mayor capacidad; devuelve None si empatan."""
    lado_corto = min(hoja.ancho_mm, hoja.alto_mm)
    lado_largo = max(hoja.ancho_mm, hoja.alto_mm)
    vertical = Hoja(lado_corto, lado_largo, hoja.margen_mm)
    horizontal = vertical.girada()
    capacidad_vertical = capacidad(vertical, marcas, ancho, alto, separacion)
    capacidad_horizontal = capacidad(horizontal, marcas, ancho, alto, separacion)
    if capacidad_vertical == capacidad_horizontal:
        return None
    return "vertical" if capacidad_vertical > capacidad_horizontal else "horizontal"


def acomodar(
    hoja: Hoja,
    marcas: Marcas,
    stickers: list[Sticker],
    separacion_mm: float = 3.0,
    rellenar: bool = False,
    area: tuple[float, float, float, float] | None = None,
) -> ResultadoAcomodo:
    """Acomoda los stickers en filas centradas y ordena el corte en serpentina."""
    if separacion_mm < 0:
        raise ValueError("La separación entre stickers no puede ser negativa.")

    x0, y0, x1, y1 = area if area is not None else area_util(hoja, marcas)
    ancho_util = max(0.0, x1 - x0)
    alto_util = max(0.0, y1 - y0)
    avisos: list[str] = []

    cantidad_por_diseno = [sticker.cantidad for sticker in stickers]
    if rellenar and len(stickers) == 1 and stickers[0].cantidad > 0:
        sticker = stickers[0]
        maximo = capacidad(
            hoja, marcas, sticker.ancho_mm, sticker.alto_mm, separacion_mm, area
        )
        if maximo > 0:
            cantidad_por_diseno[0] = ceil(sticker.cantidad / maximo) * maximo

    filas: list[list[_Instancia]] = []
    fila_actual: list[_Instancia] = []
    ancho_fila = 0.0

    for sticker, cantidad in zip(stickers, cantidad_por_diseno):
        if sticker.ancho_mm > ancho_util or sticker.alto_mm > alto_util:
            avisos.append(
                f"El sticker {sticker.id_diseno!r} de "
                f"{sticker.ancho_mm:g} × {sticker.alto_mm:g} mm no cabe "
                f"en el área útil de {ancho_util:g} × {alto_util:g} mm; se omitió."
            )
            continue

        instancia = _Instancia(
            sticker.id_diseno,
            sticker.ancho_mm,
            sticker.alto_mm,
            sticker.forma,
        )
        for _ in range(cantidad):
            ancho_siguiente = ancho_fila + (separacion_mm if fila_actual else 0.0) + sticker.ancho_mm
            if fila_actual and ancho_siguiente > ancho_util:
                filas.append(fila_actual)
                fila_actual = []
                ancho_fila = 0.0
            fila_actual.append(instancia)
            ancho_fila += (separacion_mm if len(fila_actual) > 1 else 0.0) + sticker.ancho_mm

    if fila_actual:
        filas.append(fila_actual)

    filas_por_pliego: list[list[list[_Instancia]]] = []
    filas_actuales: list[list[_Instancia]] = []
    alto_usado = 0.0
    for fila in filas:
        alto_fila = max(item.alto_mm for item in fila)
        alto_siguiente = alto_usado + (separacion_mm if filas_actuales else 0.0) + alto_fila
        if filas_actuales and alto_siguiente > alto_util:
            filas_por_pliego.append(filas_actuales)
            filas_actuales = []
            alto_usado = 0.0
        filas_actuales.append(fila)
        alto_usado += (separacion_mm if len(filas_actuales) > 1 else 0.0) + alto_fila
    if filas_actuales:
        filas_por_pliego.append(filas_actuales)

    pliegos: list[Pliego] = []
    for numero, filas_pliego in enumerate(filas_por_pliego, start=1):
        alturas = [max(item.alto_mm for item in fila) for fila in filas_pliego]
        alto_bloque = sum(alturas) + separacion_mm * (len(filas_pliego) - 1)
        y_fila = y0 + (alto_util - alto_bloque) / 2.0
        colocaciones: list[Colocacion] = []

        for indice_fila, (fila, alto_fila) in enumerate(zip(filas_pliego, alturas)):
            ancho_fila = sum(item.ancho_mm for item in fila)
            ancho_fila += separacion_mm * (len(fila) - 1)
            x = x0 + (ancho_util - ancho_fila) / 2.0
            fila_colocaciones: list[Colocacion] = []
            for item in fila:
                fila_colocaciones.append(
                    Colocacion(
                        item.id_diseno,
                        x,
                        y_fila + (alto_fila - item.alto_mm) / 2.0,
                        item.ancho_mm,
                        item.alto_mm,
                        item.forma,
                    )
                )
                x += item.ancho_mm + separacion_mm
            if indice_fila % 2:
                fila_colocaciones.reverse()
            colocaciones.extend(fila_colocaciones)
            y_fila += alto_fila + separacion_mm

        pliegos.append(Pliego(numero, colocaciones))

    total = sum(len(pliego.colocaciones) for pliego in pliegos)
    por_pliego_max = max((len(pliego.colocaciones) for pliego in pliegos), default=0)
    return ResultadoAcomodo(pliegos, por_pliego_max, total, avisos)


def firma_pliego(pliego: Pliego) -> str:
    """Firma geométrica de un pliego, redondeada a 0.01 mm.

    El identificador del diseño se omite: para el archivo de corte solo importan
    la geometría, la forma y el orden en que se recorren las líneas.
    """
    geometria = [
        (
            round(float(colocacion.x_mm), 2),
            round(float(colocacion.y_mm), 2),
            round(float(colocacion.ancho_mm), 2),
            round(float(colocacion.alto_mm), 2),
            colocacion.forma,
        )
        for colocacion in pliego.colocaciones
    ]
    return repr(geometria)


def agrupar_pliegos_identicos(resultado: ResultadoAcomodo) -> list[list[int]]:
    """Agrupa por firma de corte y devuelve los números de pliego, empezando en 1."""
    grupos_por_firma: dict[str, list[int]] = {}
    for indice, pliego in enumerate(resultado.pliegos, start=1):
        grupos_por_firma.setdefault(firma_pliego(pliego), []).append(indice)
    return list(grupos_por_firma.values())