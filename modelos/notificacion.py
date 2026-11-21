from datetime import datetime

from flask import g, has_request_context

from config import db
from modelos.pedido import Pedido  # noqa: F401  # registra la tabla pedidos
from modelos.usuario import Usuario

# tipo -> (icono de Bootstrap Icons, color contextual de Bootstrap)
ICONOS_NOTIFICACION = {
    'PEDIDO_ASIGNADO': ('bi-person-plus', 'primary'),
    'PEDIDO_TERMINADO': ('bi-check2-circle', 'success'),
    'PEDIDO_CANCELADO': ('bi-x-circle', 'danger'),
}
ICONO_NOTIFICACION_DEFECTO = ('bi-bell', 'secondary')


def icono_notificacion(tipo):
    """(icono, color) para un tipo de notificación; valor por defecto si no se conoce."""
    return ICONOS_NOTIFICACION.get(tipo, ICONO_NOTIFICACION_DEFECTO)


class Notificacion(db.Model):
    __tablename__ = 'notificaciones'
    __table_args__ = (
        db.Index('ix_notif_usuario_leida', 'id_usuario', 'leida'),
    )

    id_notificacion = db.Column(db.Integer, primary_key=True)
    # Destinatario de la notificación.
    id_usuario = db.Column(
        db.Integer, db.ForeignKey('usuarios.id_usuario', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    tipo = db.Column(db.String(40), nullable=False)  # PEDIDO_ASIGNADO, PEDIDO_TERMINADO, PEDIDO_CANCELADO
    titulo = db.Column(db.String(120), nullable=False)
    mensaje = db.Column(db.String(255))
    id_pedido = db.Column(
        db.Integer, db.ForeignKey('pedidos.id_pedido', ondelete='CASCADE'),
        nullable=True,
    )
    folio = db.Column(db.String(20))
    leida = db.Column(db.Boolean, default=False, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


def notificar(destinatarios, tipo, titulo, mensaje=None, pedido=None):
    """Agrega una notificación por destinatario SIN hacer commit: se guarda
    junto con el cambio que la origina (o ambos se revierten).
    Ignora None, duplicados, usuarios inactivos y al usuario actual."""
    actual = g.get('usuario') if has_request_context() else None
    id_actual = actual.id_usuario if actual else None
    vistos = set()
    for usuario in destinatarios or []:
        if usuario is None or not usuario.activo:
            continue
        if usuario.id_usuario == id_actual or usuario.id_usuario in vistos:
            continue
        vistos.add(usuario.id_usuario)
        db.session.add(Notificacion(
            id_usuario=usuario.id_usuario,
            tipo=tipo,
            titulo=titulo,
            mensaje=mensaje,
            id_pedido=pedido.id_pedido if pedido else None,
            folio=pedido.folio if pedido else None,
        ))


def usuarios_por_rol(*roles):
    return Usuario.query.filter(Usuario.rol.in_(roles), Usuario.activo.is_(True)).all()
