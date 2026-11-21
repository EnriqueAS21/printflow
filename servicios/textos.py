import re

from markupsafe import Markup, escape
from sqlalchemy import func

_ENLACES = re.compile(r'(https?://[^\s<]+)|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)')
_ACENTOS_BUSQUEDA = str.maketrans('áéíóúüñ', 'aeiouun')


def enlazar(texto):
    """Escapa el texto y vuelve clicables las ligas y los correos."""
    seguro = str(escape(texto or ''))

    def reemplazo(m):
        if m.group(1):
            return f'<a href="{m.group(1)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'
        return f'<a href="mailto:{m.group(2)}">{m.group(2)}</a>'

    return Markup(_ENLACES.sub(reemplazo, seguro))

def normalizar_nombre(texto):
    """Recorta, colapsa espacios y pasa a mayúsculas conservando acentos y Ñ.
    None si queda vacío."""
    return ' '.join((texto or '').split()).upper() or None


def normalizar_rfc(texto):
    """RFC sin espacios ni guiones, en mayúsculas. None si queda vacío."""
    return re.sub(r'[\s-]', '', texto or '').upper() or None


def normalizar_correo(texto):
    """Correo recortado y en minúsculas. None si queda vacío."""
    return (texto or '').strip().lower() or None


def normalizar_busqueda(texto):
    """Pasa a minúsculas y quita acentos para búsquedas de nombres."""
    return (texto or '').lower().translate(_ACENTOS_BUSQUEDA)


def nombre_normalizado(columna):
    """Expresión SQL que normaliza nombres como normalizar_busqueda()."""
    return func.translate(
        func.lower(columna), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunaeiouun'
    )
