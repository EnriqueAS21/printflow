from datetime import datetime
from enum import Enum

from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import joinedload, selectinload, validates

from config import db
from modelos import producto  # noqa: F401  # registra listas_precio, productos y presentaciones
from modelos.cliente import Cliente  # noqa: F401  # registra la tabla clientes
from modelos.usuario import Usuario  # noqa: F401  # registra la tabla usuarios
from servicios.telefonos import whatsapp_url
from servicios.textos import normalizar_nombre

TASA_IVA = 0.16
TASA_COMISION_TARJETA = 0.04
REGIMENES_FISCALES = [
    ("601", "General de Ley Personas Morales"),
    ("603", "Personas Morales con Fines no Lucrativos"),
    ("605", "Sueldos y Salarios e Ingresos Asimilados a Salarios"),
    ("606", "Arrendamiento"),
    ("612", "Personas Físicas con Actividades Empresariales y Profesionales"),
    ("616", "Sin obligaciones fiscales"),
    ("621", "Régimen de Incorporación Fiscal"),
    ("625", "Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas"),
    ("626", "Régimen Simplificado de Confianza"),
]
USOS_CFDI = [
    ("G01", "Adquisición de mercancías"),
    ("G03", "Gastos en general"),
    ("I08", "Otra maquinaria y equipo"),
    ("S01", "Sin efectos fiscales"),
    ("CP01", "Pagos"),
]

MESES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


def _a_float(valor):
    """Convierte Decimal/str/None a float de forma segura."""
    try:
        return float(valor or 0.0)
    except (TypeError, ValueError):
        return 0.0


class EstadoPedido(str, Enum):
    EN_PROCESO = "En proceso"
    TERMINADO = "Terminado"
    ENTREGADO = "Entregado"
    CANCELADO = "Cancelado"

class EstadoPago(str, Enum):
    SIN_PAGAR = "Sin pagar"
    ANTICIPO = "Anticipo"
    PAGADO = "Pagado"


# Tabla de asociación: un pedido puede tener varios responsables de producción.
pedido_responsables = db.Table(
    "pedido_responsables",
    db.Column("id_pedido", db.Integer,
              db.ForeignKey("pedidos.id_pedido", ondelete="CASCADE"), primary_key=True),
    db.Column("id_usuario", db.Integer,
              db.ForeignKey("usuarios.id_usuario", ondelete="CASCADE"), primary_key=True),
)


ESTADOS_ACTIVOS = (EstadoPedido.EN_PROCESO.value, EstadoPedido.TERMINADO.value)


def pedidos_activos(id_cliente):
    """Pedidos del cliente aún no entregados ni cancelados, por fecha de entrega."""
    return (
        Pedido.query
        .options(selectinload(Pedido.detalles), selectinload(Pedido.pagos))
        .filter(Pedido.id_cliente == id_cliente, Pedido.estatus.in_(ESTADOS_ACTIVOS))
        .order_by(Pedido.fecha_entrega.asc(), Pedido.id_pedido.asc())
        .all()
    )


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id_pedido = db.Column(db.Integer, primary_key=True)
    folio = db.Column(db.String(20), unique=True, nullable=False)
    nombre_cliente = db.Column(db.String(120), nullable=False)
    telefono_cliente = db.Column(db.String(20), nullable=False)
    id_cliente = db.Column(
        db.Integer,
        db.ForeignKey("clientes.id_cliente", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    id_lista_precio = db.Column(
        db.Integer,
        db.ForeignKey("listas_precio.id_lista", ondelete="SET NULL"),
        nullable=True,
    )
    fecha_creacion = db.Column(db.DateTime, default=datetime.now)
    fecha_entrega = db.Column(db.DateTime, nullable=False)
    notas = db.Column(db.Text, nullable=True)
    requiere_factura = db.Column(db.Boolean, default=False)
    id_usuario_produccion = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
    )
    # Estado de PRODUCCIÓN (En proceso / Terminado / Entregado).
    # El estado financiero NO se guarda: se deriva de los pagos (estado_pago).
    estatus = db.Column(
        db.String(50), default=EstadoPedido.EN_PROCESO.value, nullable=False
    )
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    iva = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    fecha_terminado = db.Column(db.DateTime, nullable=True)
    fecha_entregado = db.Column(db.DateTime, nullable=True)   # cuando pasó a Entregado

    # Relaciones ORM
    detalles = db.relationship(
        "DetallePedido",
        backref="pedido",
        lazy=True,
        cascade="all, delete-orphan",
    )
    pagos = db.relationship(
        "Pago",
        backref="pedido",
        lazy=True,
        cascade="all, delete-orphan",
        order_by=lambda: (Pago.fecha_pago.asc(), Pago.id_pago.asc()),
    )
    factura = db.relationship(
        "DatosFactura",
        back_populates="pedido",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )
    cliente = db.relationship(
        "Cliente",
        backref=db.backref("pedidos", lazy="dynamic"),
        lazy=True,
    )
    responsable_produccion = db.relationship(
        "Usuario",
        foreign_keys=[id_usuario_produccion],
        lazy=True,
    )
    responsables = db.relationship(
        "Usuario",
        secondary=pedido_responsables,
        lazy=True,
        order_by="Usuario.nombre",
    )

    @validates('nombre_cliente')
    def _validar_nombre_cliente(self, _clave, valor):
        return normalizar_nombre(valor)

    # ---------- Presentación ----------
    @property
    def fecha_creacion_larga(self):
        f = self.fecha_creacion
        if not f:
            return ''
        return f'{f.day:02d} de {MESES[f.month - 1]} {f.year}'

    @property
    def whatsapp_url(self):
        return whatsapp_url(self.telefono_cliente)

    @property
    def texto_apartados_whatsapp(self):
        """Apartados configurados para el mensaje de WhatsApp de este pedido."""
        from modelos.configuracion_nota import texto_apartados_whatsapp
        return texto_apartados_whatsapp(self)

    @property
    def hora_creacion(self):
        return self.fecha_creacion.strftime('%I:%M %p').lower() if self.fecha_creacion else ''

    @property
    def resumen_descripcion(self):
        if self.detalles:
            return ", ".join(f"{d.cantidad} {d.descripcion}" for d in self.detalles)
        return self.notas or "Sin especificaciones"

    @property
    def responsables_asignados(self):
        """Lista de responsables; cae al campo legado si la tabla nueva está vacía."""
        if self.responsables:
            return list(self.responsables)
        if self.responsable_produccion:
            return [self.responsable_produccion]
        return []

    @property
    def nombre_responsable(self):
        nombres = [u.nombre for u in self.responsables_asignados]
        return ", ".join(nombres) if nombres else "Sin asignar"

    @property
    def tiene_asignaciones_por_producto(self):
        return len(self.responsables_asignados) > 1

    @property
    def productos_completos(self):
        return bool(self.detalles) and all(
            detalle.asignacion is not None and detalle.asignacion.completado
            for detalle in self.detalles
        )

    # ---------- Finanzas (siempre derivadas de Pago) ----------
    @property
    def total_float(self):
        return round(_a_float(self.total), 2)

    @property
    def total_pagado(self):
        """Suma acumulada de todos los pagos registrados."""
        return round(sum(_a_float(p.monto) for p in (self.pagos or [])), 2)

    @property
    def saldo_restante(self):
        """Monto pendiente por liquidar (nunca negativo)."""
        return round(max(self.total_float - self.total_pagado, 0.0), 2)

    @property
    def estado_pago(self):
        """'Sin pagar' | 'Anticipo' | 'Pagado', calculado dinámicamente."""
        total = self.total_float
        pagado = self.total_pagado
        if total > 0 and pagado >= total:
            return EstadoPago.PAGADO.value
        if pagado > 0:
            return EstadoPago.ANTICIPO.value
        return EstadoPago.SIN_PAGAR.value

    @property
    def historial_pagos(self):
        """Pagos ordenados con el saldo pendiente real tras cada abono."""
        total = self.total_float
        acumulado = 0.0
        filas = []
        ordenados = sorted(
            self.pagos or [],
            key=lambda p: (p.fecha_pago or datetime.min, p.id_pago or 0),
        )
        for p in ordenados:
            monto = _a_float(p.monto)
            acumulado = round(acumulado + monto, 2)
            filas.append({
                "metodo_pago": p.metodo_pago,
                "fecha_pago": p.fecha_pago,
                "monto": round(monto, 2),
                "iva": round(_a_float(p.iva), 2),
                "comision": round(_a_float(p.comision), 2),
                "restante": round(max(total - acumulado, 0.0), 2),
            })
        return filas

    # ---------- Operaciones ----------
    def reemplazar_detalles(self, items):
        """
        Sustituye los detalles por `items` (lista de dicts con cantidad,
        descripcion, precio_unitario) y recalcula total. El subtotal se calcula
        siempre aquí, por lo que nunca viaja None a PostgreSQL.
        """
        anteriores = {
            detalle.id_detalle: {
                "cantidad": _a_float(detalle.cantidad),
                "descripcion": detalle.descripcion,
                "precio_unitario": _a_float(detalle.precio_unitario),
                "id_producto": detalle.id_producto,
                "id_presentacion": detalle.id_presentacion,
                "configuracion": detalle.configuracion,
                "id_usuario": detalle.asignacion.id_usuario if detalle.asignacion else None,
                "completado": detalle.asignacion.completado if detalle.asignacion else False,
            }
            for detalle in self.detalles
            if detalle.id_detalle is not None
        }
        self.detalles.clear()
        for it in items:
            cantidad = round(_a_float(it["cantidad"]), 2)
            precio = round(_a_float(it["precio_unitario"]), 2)
            subtotal = round(cantidad * precio, 2)
            detalle = DetallePedido(
                cantidad=cantidad,
                descripcion=it["descripcion"],
                precio_unitario=precio,
                subtotal=subtotal,
                id_producto=it.get("id_producto"),
                id_presentacion=it.get("id_presentacion"),
                configuracion=it.get("configuracion"),
            )
            id_usuario = it.get("responsable_id")
            if id_usuario is not None:
                anterior = anteriores.get(it.get("id_detalle"))
                sin_cambios = anterior and (
                    anterior["cantidad"] == cantidad
                    and anterior["descripcion"] == it["descripcion"]
                    and anterior["precio_unitario"] == precio
                    and anterior["id_producto"] == it.get("id_producto")
                    and anterior["id_presentacion"] == it.get("id_presentacion")
                    and anterior["configuracion"] == it.get("configuracion")
                    and anterior["id_usuario"] == id_usuario
                )
                detalle.asignacion = DetalleAsignacion(
                    id_usuario=id_usuario,
                    completado=bool(sin_cambios and anterior["completado"]),
                )
            self.detalles.append(detalle)
        self.recalcular_totales()

    def recalcular_totales(self):
        self.subtotal = round(
            sum(_a_float(detalle.subtotal) for detalle in (self.detalles or [])), 2
        )
        self.iva = (
            round(self.subtotal * TASA_IVA, 2)
            if self.requiere_factura else 0
        )
        self.total = round(self.subtotal + self.iva, 2)

    def agregar_pago(self, monto, metodo):
        monto = _a_float(monto)
        if monto <= 0 or monto > self.saldo_restante:
            raise ValueError("El monto debe ser mayor que cero y no superar el saldo restante")
        if self.requiere_factura and metodo == "Tarjeta":
            raise ValueError("Con factura solo se acepta efectivo o transferencia")
        monto = round(monto, 2)
        if monto <= 0:
            raise ValueError("El monto debe ser mayor que cero y no superar el saldo restante")

        iva_pago = (
            round(monto * _a_float(self.iva) / _a_float(self.total), 2)
            if _a_float(self.iva) > 0 and _a_float(self.total) > 0 else 0
        )
        comision = (
            round(monto * TASA_COMISION_TARJETA, 2)
            if metodo == "Tarjeta" else 0
        )
        pago = Pago(monto=monto, metodo_pago=metodo, iva=iva_pago, comision=comision)
        self.pagos.append(pago)
        return pago

    def to_dict(self):
        return {
            "id_pedido": self.id_pedido,
            "folio": self.folio,
            "cliente_nombre": self.nombre_cliente,
            "cliente_telefono": self.telefono_cliente,
            "descripcion": self.resumen_descripcion,
            "fecha_entrega": self.fecha_entrega.strftime("%d/%m/%Y") if self.fecha_entrega else "",
            "hora_entrega": self.fecha_entrega.strftime("%I:%M %p") if self.fecha_entrega else "",
            "responsable": self.nombre_responsable,
            "estado": self.estatus,
            "estado_pago": self.estado_pago,
            "subtotal": round(_a_float(self.subtotal), 2),
            "iva": round(_a_float(self.iva), 2),
            "total": self.total_float,
            "comision": round(sum(_a_float(p.comision) for p in (self.pagos or [])), 2),
            "total_pagado": self.total_pagado,
            "saldo_restante": self.saldo_restante,
        }


class DatosFactura(db.Model):
    __tablename__ = "pedido_factura"
    __table_args__ = (
        db.CheckConstraint("estado IN ('Pendiente', 'Facturado')", name="ck_pedido_factura_estado"),
    )

    id_pedido = db.Column(
        db.Integer,
        db.ForeignKey("pedidos.id_pedido", ondelete="CASCADE"),
        primary_key=True,
    )
    rfc = db.Column(db.String(13), nullable=False)
    razon_social = db.Column(db.String(200), nullable=False)
    regimen_fiscal = db.Column(db.String(3), nullable=False)
    cp_fiscal = db.Column(db.String(5), nullable=False)
    uso_cfdi = db.Column(db.String(4), nullable=False)
    correo = db.Column(db.String(120), nullable=True)
    estado = db.Column(db.String(20), nullable=False, default="Pendiente")
    fecha_alta = db.Column(db.DateTime, nullable=False, default=datetime.now)

    pedido = db.relationship("Pedido", back_populates="factura")


class DetallePedido(db.Model):
    __tablename__ = "detalle_pedido"
    __table_args__ = (
        db.CheckConstraint("cantidad > 0", name="ck_detalle_cantidad_positiva"),
    )

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_pedido = db.Column(
        db.Integer, db.ForeignKey("pedidos.id_pedido"), nullable=False, index=True
    )
    id_producto = db.Column(
        db.Integer,
        db.ForeignKey("productos.id_producto", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    id_presentacion = db.Column(
        db.Integer,
        db.ForeignKey("presentaciones.id_presentacion", ondelete="SET NULL"),
        nullable=True,
    )
    configuracion = db.Column(db.JSON().with_variant(JSONB, "postgresql"), nullable=True)
    cantidad = db.Column(db.Numeric(10, 2), nullable=False, default=1)
    descripcion = db.Column(db.Text, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    asignacion = db.relationship(
        "DetalleAsignacion",
        back_populates="detalle",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )
    pasos = db.relationship(
        "LineaPaso",
        back_populates="detalle",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="LineaPaso.orden",
    )


class DetalleAsignacion(db.Model):
    """Responsable y avance del producto dentro de un pedido compartido."""

    __tablename__ = "detalle_asignaciones"

    id_detalle = db.Column(
        db.Integer,
        db.ForeignKey("detalle_pedido.id_detalle", ondelete="CASCADE"),
        primary_key=True,
    )
    id_usuario = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id_usuario", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    completado = db.Column(db.Boolean, nullable=False, default=False)
    fecha_completado = db.Column(db.DateTime, nullable=True)
    id_usuario_completo = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id_usuario", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    id_proceso_actual = db.Column(
        db.Integer,
        db.ForeignKey("procesos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    detalle = db.relationship("DetallePedido", back_populates="asignacion")
    usuario = db.relationship(
        "Usuario",
        foreign_keys=[id_usuario],
        lazy=True,
    )
    usuario_completo = db.relationship(
        "Usuario",
        foreign_keys=[id_usuario_completo],
        lazy=True,
    )
    proceso_actual = db.relationship("Proceso", foreign_keys=[id_proceso_actual], lazy=True)


def con_relaciones_tarjeta(query):
    """Carga las relaciones usadas al renderizar una tarjeta de pedido."""
    return query.options(
        selectinload(Pedido.detalles)
        .selectinload(DetallePedido.asignacion)
        .joinedload(DetalleAsignacion.usuario),
        selectinload(Pedido.pagos),
        selectinload(Pedido.responsables),
        joinedload(Pedido.responsable_produccion),
        joinedload(Pedido.factura),
        joinedload(Pedido.cliente),
    )


@event.listens_for(DetallePedido, "before_insert")
@event.listens_for(DetallePedido, "before_update")
def _garantizar_subtotal(mapper, connection, detalle):
    """Red de seguridad: el subtotal nunca llega NULL ni desalineado."""
    detalle.subtotal = round(
        _a_float(detalle.cantidad) * _a_float(detalle.precio_unitario), 2
    )


class Pago(db.Model):
    __tablename__ = "pagos"
    __table_args__ = (
        db.CheckConstraint("monto > 0", name="ck_pagos_monto_positivo"),
    )

    id_pago = db.Column(db.Integer, primary_key=True)
    id_pedido = db.Column(
        db.Integer, db.ForeignKey("pedidos.id_pedido"), nullable=False, index=True
    )
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    iva = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    comision = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    metodo_pago = db.Column(db.String(50), nullable=False)
    fecha_pago = db.Column(db.DateTime, default=datetime.now)  # hora local del servidor

    @property
    def base(self):
        return round(_a_float(self.monto) - _a_float(self.iva), 2)

    @property
    def cobrado(self):
        return round(_a_float(self.monto) + _a_float(self.comision), 2)


# Registra las tablas de rutas aunque este módulo se importe directamente en pruebas.
from modelos import produccion as _modelo_produccion  # noqa: E402,F401
