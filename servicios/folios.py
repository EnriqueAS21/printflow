from sqlalchemy import text

from config import db

PREFIJO = 'AC'      # <-- ajusta a tu formato actual, p. ej. 'AC-'
DIGITOS = 4       # <-- p. ej. 4 => 0001


def _formatear(numero):
    return f'{PREFIJO}{numero:0{DIGITOS}d}'


def generar_siguiente_folio():
    """Consume el siguiente número. Usar SOLO al crear el pedido."""
    numero = db.session.execute(text("SELECT nextval('folio_pedido_seq')")).scalar()
    return _formatear(numero)


def previsualizar_siguiente_folio():
    """Muestra el próximo folio sin consumirlo (para mostrar en formularios)."""
    ultimo, usado = db.session.execute(
        text("SELECT last_value, is_called FROM folio_pedido_seq")
    ).one()
    return _formatear(ultimo + 1 if usado else ultimo)