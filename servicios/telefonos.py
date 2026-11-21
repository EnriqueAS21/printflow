import re


def normalizar_telefono(texto):
    """Deja solo los dígitos del teléfono ('' si viene vacío)."""
    return re.sub(r'\D', '', texto or '')


def whatsapp_url(telefono):
    """Enlace wa.me al teléfono (asume México +52 si trae 10 dígitos).
    Devuelve None si el número no se puede interpretar."""
    digitos = normalizar_telefono(telefono)
    if digitos.startswith('00'):
        digitos = digitos[2:]
    if len(digitos) == 10:
        digitos = '52' + digitos
    elif digitos.startswith('521') and len(digitos) == 13:
        digitos = '52' + digitos[3:]
    if not 11 <= len(digitos) <= 15:
        return None
    return f'https://wa.me/{digitos}'
