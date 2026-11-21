from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import validates

from config import db
from servicios.textos import normalizar_nombre


FORMAS = ("fijo", "paquete", "lineal", "m2", "rollo", "tiempo", "libre")
COBROS_ROLLO = ("m2_real", "ancho_rollo")


class ListaPrecio(db.Model):
    __tablename__ = "listas_precio"

    id_lista = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(40), unique=True, nullable=False)
    predeterminada = db.Column(db.Boolean, default=False)
    orden = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)


class Producto(db.Model):
    __tablename__ = "productos"

    id_producto = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(60), index=True)
    descripcion = db.Column(db.Text, nullable=True)
    cobro_rollo = db.Column(db.String(20), default="m2_real")
    activo = db.Column(db.Boolean, default=True)
    fecha_alta = db.Column(db.DateTime, default=datetime.now)
    id_proceso_principal = db.Column(
        db.Integer,
        db.ForeignKey("procesos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    presentaciones = db.relationship(
        "Presentacion",
        back_populates="producto",
        order_by="Presentacion.orden",
        cascade="all, delete-orphan",
    )
    proceso_principal = db.relationship(
        "Proceso",
        foreign_keys=[id_proceso_principal],
        lazy=True,
    )

    @validates("nombre")
    def _validar_nombre(self, _clave, valor):
        return normalizar_nombre(valor)

    @validates("categoria")
    def _validar_categoria(self, _clave, valor):
        if valor is None:
            return None
        valor = valor.strip()
        return valor[:1].upper() + valor[1:] if valor else None

    @validates("cobro_rollo")
    def _validar_cobro_rollo(self, _clave, valor):
        if valor not in COBROS_ROLLO:
            raise ValueError(f"cobro_rollo debe ser uno de: {', '.join(COBROS_ROLLO)}")
        return valor

    @property
    def clave(self):
        return f"P-{self.id_producto:04d}"


class Presentacion(db.Model):
    __tablename__ = "presentaciones"

    id_presentacion = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(
        db.Integer,
        db.ForeignKey("productos.id_producto", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nombre = db.Column(db.String(80), nullable=False)
    forma = db.Column(db.String(20), nullable=False)
    orden = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    piezas_paquete = db.Column(db.Integer, nullable=True)
    ancho_rollo = db.Column(db.Numeric(6, 3), nullable=True)
    margen_rollo = db.Column(db.Numeric(6, 3), default=0.05)
    minutos_minimos = db.Column(db.Numeric(8, 2), nullable=True)
    minimo_cobro = db.Column(db.Numeric(10, 2), nullable=True)
    cargo_union = db.Column(db.Numeric(10, 2), default=0)

    producto = db.relationship("Producto", back_populates="presentaciones")
    precios = db.relationship(
        "PresentacionPrecio",
        back_populates="presentacion",
        cascade="all, delete-orphan",
    )


class PresentacionPrecio(db.Model):
    __tablename__ = "presentacion_precios"
    __table_args__ = (
        CheckConstraint("cantidad_minima >= 0", name="ck_presentacion_precio_cantidad_minima"),
        CheckConstraint("precio >= 0", name="ck_presentacion_precio_no_negativo"),
        UniqueConstraint(
            "id_presentacion",
            "id_lista",
            "cantidad_minima",
            name="uq_presentacion_precio_lista_cantidad",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    id_presentacion = db.Column(
        db.Integer,
        db.ForeignKey("presentaciones.id_presentacion", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_lista = db.Column(
        db.Integer,
        db.ForeignKey("listas_precio.id_lista", ondelete="CASCADE"),
        nullable=False,
    )
    cantidad_minima = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    precio = db.Column(db.Numeric(10, 2), nullable=False)

    presentacion = db.relationship("Presentacion", back_populates="precios")
    lista = db.relationship("ListaPrecio")


# Registra las tablas de rutas antes de configurar relaciones/metadata en pruebas
# que importan directamente este módulo.
from modelos import produccion as _modelo_produccion  # noqa: E402,F401
