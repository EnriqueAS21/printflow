from config import db

ROLES = ('Administrador', 'Recepcion', 'Produccion', 'Facturacion')


class Usuario(db.Model):
    __tablename__ = 'usuarios'

    id_usuario = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    correo = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    telefono = db.Column(db.String(20))
    rol = db.Column(db.String(50), nullable=False)
    # Los usuarios no se borran (hay pedidos y bitácora que los referencian): se desactivan.
    activo = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('true'))