from config import db
from datetime import datetime


class MovimientoCaja(db.Model):
    __tablename__ = 'movimientos_caja'

    id_movimiento = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # 'VENTA_TICKET', 'EGRESO'
    cantidad = db.Column(db.Numeric(10, 2), nullable=False, default=1)
    descripcion = db.Column(db.String(255), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    metodo_pago = db.Column(db.String(50), nullable=False)
    comision = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # solo ventas con tarjeta
    fecha = db.Column(db.DateTime, default=datetime.now, index=True)
    folio = db.Column(db.String(20), nullable=True, index=True)


class FondoCaja(db.Model):
    """Fondo inicial definido manualmente. Vale el último registrado
    después del último corte; si no hay, se usa el saldo final del corte."""
    __tablename__ = 'fondos_caja'

    id_fondo = db.Column(db.Integer, primary_key=True)
    monto = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    fecha = db.Column(db.DateTime, default=datetime.now, index=True)


class CorteCaja(db.Model):
    """Un corte cierra un periodo: (fecha_inicio, fecha_corte].
    Guarda todo lo necesario para reportes semanales / mensuales."""
    __tablename__ = 'cortes_caja'

    id_corte = db.Column(db.Integer, primary_key=True)
    fecha_inicio = db.Column(db.DateTime, nullable=True)   # NULL = primer corte
    fecha_corte = db.Column(db.DateTime, default=datetime.now, index=True)
    fondo_inicial = db.Column(db.Numeric(10, 2), default=0.0)
    total_ventas = db.Column(db.Numeric(10, 2), default=0.0)
    total_efectivo = db.Column(db.Numeric(10, 2), default=0.0)
    total_transferencia = db.Column(db.Numeric(10, 2), default=0.0)
    total_tarjeta = db.Column(db.Numeric(10, 2), default=0.0)
    total_egresos = db.Column(db.Numeric(10, 2), default=0.0)
    egresos_efectivo = db.Column(db.Numeric(10, 2), default=0.0)
    retiro_efectivo = db.Column(db.Numeric(10, 2), default=0.0)
    saldo_final = db.Column(db.Numeric(10, 2), default=0.0)  # queda en cajón