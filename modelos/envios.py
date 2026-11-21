import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from config import db

MENSAJE_DUPLICADO = 'Este envío ya se procesó o expiró. Revisa el historial antes de volver a intentarlo.'


class EnvioProcesado(db.Model):
    __tablename__ = 'envios_procesados'
    token = db.Column(db.String(32), primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.now, nullable=False)


def nuevo_token():
    return uuid.uuid4().hex


def reclamar_envio(token):
    """True si es la primera vez que llega este token. Llamar ANTES de agregar
    nada más a la sesión: el token se guarda en la misma transacción que el cambio."""
    token = (token or '').strip()
    if len(token) != 32:
        return False
    db.session.add(EnvioProcesado(token=token))
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return False
    return True