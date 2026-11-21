from datetime import datetime

from sqlalchemy.orm import validates

from config import db
from modelos import producto  # noqa: F401  # registra listas_precio, productos y presentaciones
from servicios.telefonos import normalizar_telefono, whatsapp_url
from servicios.textos import normalizar_correo, normalizar_nombre, normalizar_rfc


class Cliente(db.Model):
    __tablename__ = "clientes"

    id_cliente = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(20), unique=True, nullable=False)
    id_lista_precio = db.Column(
        db.Integer,
        db.ForeignKey("listas_precio.id_lista", ondelete="SET NULL"),
        nullable=True,
    )
    correo = db.Column(db.String(120), nullable=True)
    tipo = db.Column(db.String(30), default="Particular")
    rfc = db.Column(db.String(13), nullable=True)
    razon_social = db.Column(db.String(150), nullable=True)
    notas = db.Column(db.Text, nullable=True)
    activo = db.Column(db.Boolean, default=True)
    fecha_alta = db.Column(db.DateTime, default=datetime.now)

    lista_precio = db.relationship("ListaPrecio", lazy=True)

    @validates('nombre', 'razon_social')
    def _validar_nombre(self, _clave, valor):
        return normalizar_nombre(valor)

    @validates('rfc')
    def _validar_rfc(self, _clave, valor):
        return normalizar_rfc(valor)

    @validates('correo')
    def _validar_correo(self, _clave, valor):
        return normalizar_correo(valor)

    @validates('telefono')
    def _validar_telefono(self, _clave, valor):
        return normalizar_telefono(valor)

    @validates('notas')
    def _validar_notas(self, _clave, valor):
        return valor.strip() if valor is not None else None

    @property
    def clave(self):
        return f"C-{self.id_cliente:04d}"

    @property
    def whatsapp_url(self):
        return whatsapp_url(self.telefono)
