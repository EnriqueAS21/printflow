"""Motor geométrico para el acomodo de stickers en pliegos."""

from .geometria import (
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
    firma_pliego,
    posiciones_marcas,
    sugerir_orientacion,
)

__all__ = [
    "Hoja",
    "Marcas",
    "Sticker",
    "Colocacion",
    "Pliego",
    "ResultadoAcomodo",
    "area_util",
    "posiciones_marcas",
    "acomodar",
    "capacidad",
    "sugerir_orientacion",
    "firma_pliego",
    "agrupar_pliegos_identicos",
]