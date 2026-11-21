from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint

from config import db


class Proceso(db.Model):
    __tablename__ = "procesos"

    id_proceso = db.Column("id", db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False, unique=True)
    orden = db.Column(db.Integer, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    es_sistema = db.Column(db.Boolean, nullable=False, default=False)


class ProductoPaso(db.Model):
    __tablename__ = "producto_pasos"
    __table_args__ = (
        UniqueConstraint("id_producto", "orden", name="uq_producto_pasos_producto_orden"),
        UniqueConstraint("id_producto", "id_proceso", name="uq_producto_pasos_producto_proceso"),
    )

    id_producto_paso = db.Column("id", db.Integer, primary_key=True)
    id_producto = db.Column(
        db.Integer, db.ForeignKey("productos.id_producto", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    orden = db.Column(db.Integer, nullable=False)
    id_proceso = db.Column(
        db.Integer, db.ForeignKey("procesos.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    opcional = db.Column(db.Boolean, nullable=False, default=False)

    producto = db.relationship(
        "Producto",
        foreign_keys=[id_producto],
        backref=db.backref("rutas_produccion", lazy=True, cascade="all, delete-orphan"),
    )
    proceso = db.relationship("Proceso", foreign_keys=[id_proceso])


class ReglaClasificacion(db.Model):
    __tablename__ = "reglas_clasificacion"

    id_regla = db.Column("id", db.Integer, primary_key=True)
    palabra_clave = db.Column(db.String(120), nullable=False)
    prioridad = db.Column(db.Integer, nullable=False, default=0)
    categoria = db.Column(db.String(60), nullable=True)
    id_proceso_principal = db.Column(
        db.Integer, db.ForeignKey("procesos.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    activo = db.Column(db.Boolean, nullable=False, default=True)

    proceso_principal = db.relationship("Proceso", foreign_keys=[id_proceso_principal])


class ReglaPaso(db.Model):
    __tablename__ = "regla_pasos"
    __table_args__ = (
        UniqueConstraint("id_regla", "orden", name="uq_regla_pasos_regla_orden"),
        UniqueConstraint("id_regla", "id_proceso", name="uq_regla_pasos_regla_proceso"),
    )

    id_regla_paso = db.Column("id", db.Integer, primary_key=True)
    id_regla = db.Column(
        db.Integer, db.ForeignKey("reglas_clasificacion.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    orden = db.Column(db.Integer, nullable=False)
    id_proceso = db.Column(
        db.Integer, db.ForeignKey("procesos.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    opcional = db.Column(db.Boolean, nullable=False, default=False)

    regla = db.relationship("ReglaClasificacion", backref=db.backref("pasos", lazy=True, cascade="all, delete-orphan"))
    proceso = db.relationship("Proceso", foreign_keys=[id_proceso])


class LineaPaso(db.Model):
    __tablename__ = "linea_pasos"
    __table_args__ = (
        CheckConstraint("estado IN ('pendiente', 'hecho', 'no_aplica')", name="ck_linea_pasos_estado"),
        UniqueConstraint("id_detalle", "orden", name="uq_linea_pasos_detalle_orden"),
    )

    id_linea_paso = db.Column("id", db.Integer, primary_key=True)
    id_detalle = db.Column(
        db.Integer, db.ForeignKey("detalle_pedido.id_detalle", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    orden = db.Column(db.Integer, nullable=False)
    id_proceso = db.Column(
        db.Integer, db.ForeignKey("procesos.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    opcional = db.Column(db.Boolean, nullable=False, default=False)
    estado = db.Column(db.String(20), nullable=False, default="pendiente")
    fecha = db.Column(db.DateTime, nullable=True)
    id_usuario = db.Column(
        db.Integer, db.ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    detalle = db.relationship(
        "DetallePedido",
        foreign_keys=[id_detalle],
        back_populates="pasos",
    )
    proceso = db.relationship("Proceso", foreign_keys=[id_proceso])
    usuario = db.relationship("Usuario", foreign_keys=[id_usuario])
