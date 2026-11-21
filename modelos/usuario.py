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

    @classmethod
    def operadores_asignables(cls, incluir_ids=()):
        """Usuarios de Producción a quienes se puede asignar trabajo: solo activos.

        `incluir_ids`: responsables ya asignados que se conservan aunque estén dados de baja
        (al editar un pedido, para no quitarlos sin querer del formulario).
        """
        condicion = cls.activo.is_(True)
        ids = {i for i in incluir_ids if i}
        if ids:
            condicion = db.or_(condicion, cls.id_usuario.in_(ids))
        return cls.query.filter(cls.rol == 'Produccion', condicion).order_by(cls.nombre.asc()).all()
