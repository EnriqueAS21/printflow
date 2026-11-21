from datetime import datetime

from flask import g, has_request_context, request

from config import db
from modelos.usuario import Usuario  # noqa: F401  # registra la tabla usuarios


class Bitacora(db.Model):
    __tablename__ = 'bitacora'

    id_bitacora = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    id_usuario = db.Column(
        db.Integer, db.ForeignKey('usuarios.id_usuario', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    # Copia del nombre y rol al momento del cambio: el historial no cambia si luego se edita el usuario.
    nombre_usuario = db.Column(db.String(100))
    rol = db.Column(db.String(50))
    accion = db.Column(db.String(50), nullable=False, index=True)
    entidad = db.Column(db.String(50))
    id_entidad = db.Column(db.Integer)
    detalle = db.Column(db.Text)
    ip = db.Column(db.String(45))


def registrar(accion, entidad=None, id_entidad=None, detalle=None, usuario=None):
    """Agrega un renglón a la bitácora SIN hacer commit: se guarda junto con
    el cambio que describe (o ambos se revierten)."""
    if usuario is None and has_request_context():
        usuario = g.get('usuario')
    db.session.add(Bitacora(
        id_usuario=usuario.id_usuario if usuario else None,
        nombre_usuario=usuario.nombre if usuario else None,
        rol=usuario.rol if usuario else None,
        accion=accion,
        entidad=entidad,
        id_entidad=id_entidad,
        detalle=detalle,
        ip=request.remote_addr if has_request_context() else None,
    ))