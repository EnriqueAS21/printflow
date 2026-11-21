"""Datos opcionales del perfil personal del usuario."""
from config import db


class PerfilUsuario(db.Model):
    __tablename__ = "perfiles_usuario"

    id_usuario = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id_usuario", ondelete="CASCADE"),
        primary_key=True,
    )
    foto_archivo = db.Column(db.String(100), nullable=True)
