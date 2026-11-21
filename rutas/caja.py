import math
from datetime import datetime

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, url_for,
)
from sqlalchemy.orm import selectinload

from config import db
from modelos.bitacora import registrar
from modelos.caja import CorteCaja, FondoCaja, MovimientoCaja
from modelos import cajon as modulo_cajon
from modelos.cajon import abrir_cajon_o_avisar
from modelos.configuracion_nota import obtener_configuracion_nota
from modelos.envios import MENSAJE_DUPLICADO, reclamar_envio
from modelos.pedido import Pago, Pedido
from modelos.usuario import Usuario
from servicios.folios import previsualizar_siguiente_folio
from servicios.permisos import permiso_requerido

caja_bp = Blueprint('caja', __name__, template_folder='../templates')

METODOS = ('Efectivo', 'Tarjeta', 'Transferencia')
MESES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


# ------------------------------------------------------------ utilidades
def _monto_valido(texto, permitir_cero=True):
    """float finito >= 0 (o > 0), o lanza ValueError."""
    valor = float(texto or 0)
    if not math.isfinite(valor) or valor < 0 or (not permitir_cero and valor == 0):
        raise ValueError
    return round(valor, 2)


def _fmt_cant(cantidad):
    return f"{float(cantidad or 0):g}"


def _fecha_larga(f):
    return f"{f.day:02d} de {MESES[f.month - 1]} {f.year}"


def _ultimo_corte():
    return CorteCaja.query.order_by(CorteCaja.fecha_corte.desc()).first()


def _fondo_inicial(inicio):
    """Último fondo definido a mano dentro del periodo abierto; si no hay,
    lo que quedó en el cajón tras el último corte; si no hay cortes, 0."""
    q = FondoCaja.query
    if inicio:
        q = q.filter(FondoCaja.fecha > inicio)
    fondo = q.order_by(FondoCaja.fecha.desc(), FondoCaja.id_fondo.desc()).first()
    if fondo:
        return float(fondo.monto or 0)
    corte = _ultimo_corte()
    return float(corte.saldo_final or 0) if corte else 0.0


def _resumen_periodo(inicio, fin=None):
    """Movimientos con inicio < fecha <= fin (inicio=None => desde siempre,
    fin=None => hasta ahora). Misma lógica para la pantalla y para el corte."""
    pq = Pago.query.options(selectinload(Pago.pedido).selectinload(Pedido.detalles))
    mq = MovimientoCaja.query
    if inicio:
        pq = pq.filter(Pago.fecha_pago > inicio)
        mq = mq.filter(MovimientoCaja.fecha > inicio)
    if fin:
        pq = pq.filter(Pago.fecha_pago <= fin)
        mq = mq.filter(MovimientoCaja.fecha <= fin)

    ingresos = dict.fromkeys(METODOS, 0.0)
    cobrado = dict.fromkeys(METODOS, 0.0)
    egresos = dict.fromkeys(METODOS, 0.0)
    iva_total = 0.0
    comision_total = 0.0
    historial = []

    for p in pq.all():
        monto = float(p.monto or 0)
        iva = float(p.iva or 0)
        comision = float(p.comision or 0)
        base = monto - iva
        metodo = (p.metodo_pago or 'Efectivo').strip()
        if metodo in ingresos:
            ingresos[metodo] += base
            cobrado[metodo] += monto + comision
        iva_total += iva
        comision_total += comision

        pedido = p.pedido
        if pedido and pedido.detalles:
            lineas = [f"{_fmt_cant(d.cantidad)} {d.descripcion}" for d in pedido.detalles]
            cantidad = sum(float(d.cantidad or 0) for d in pedido.detalles)
        else:
            lineas = ["Pago de pedido"]
            cantidad = 1
        historial.append({
            "tipo": "VENTA", "origen": "Pedido", "cantidad": _fmt_cant(cantidad),
            "lineas": lineas, "subtotal": base, "iva": iva, "comision": comision,
            "metodo_pago": metodo,
            "folio": pedido.folio if pedido else "N/A", "fecha": p.fecha_pago,
        })

    for m in mq.all():
        monto = float(m.monto or 0)
        metodo = (m.metodo_pago or 'Efectivo').strip()
        es_egreso = m.tipo == 'EGRESO'
        destino = egresos if es_egreso else ingresos
        if metodo in destino:
            destino[metodo] += monto
            if not es_egreso:
                cobrado[metodo] += monto
        historial.append({
            "tipo": "EGRESO" if es_egreso else "VENTA",
            "origen": "Egreso" if es_egreso else "Mostrador",
            "cantidad": _fmt_cant(m.cantidad or 1),
            "lineas": [m.descripcion],
            "subtotal": -monto if es_egreso else monto,
            "iva": 0.0, "comision": 0.0,
            "metodo_pago": metodo, "folio": m.folio or "N/A", "fecha": m.fecha,
        })

    historial.sort(key=lambda f: f["fecha"] or datetime.min, reverse=True)
    return {
        "ingresos": ingresos, "cobrado": cobrado, "egresos": egresos,
        "iva": iva_total, "comision": comision_total, "historial": historial,
    }


# ------------------------------------------------------------------ rutas
@caja_bp.route('/', methods=['GET'])
@permiso_requerido('caja.ver')
def index():
    ultimo = _ultimo_corte()
    inicio = ultimo.fecha_corte if ultimo else None
    r = _resumen_periodo(inicio)
    ing, egr = r["ingresos"], r["egresos"]

    fondo_inicial = _fondo_inicial(inicio)
    ahora = datetime.now()
    periodo_atrasado = any(
        f["fecha"] and f["fecha"].date() < ahora.date() for f in r["historial"]
    )
    # Solo presentación: desde cuándo está abierto el periodo y cuántos días lleva.
    fechas = [f["fecha"] for f in r["historial"] if f["fecha"]]
    periodo_desde = inicio or (min(fechas) if fechas else None)
    dias_sin_corte = (ahora.date() - periodo_desde.date()).days if periodo_desde else 0

    return render_template(
        'caja.html',
        historial=r["historial"],
        total_ventas=sum(ing.values()),
        total_egresos=sum(egr.values()),
        fondo_inicial=fondo_inicial,
        # Misma fórmula que usa guardar_corte para cuadrar el efectivo (cobrado incluye IVA).
        efectivo_esperado=fondo_inicial + r["cobrado"]["Efectivo"] - egr["Efectivo"],
        ingresos_efectivo=ing['Efectivo'],
        ingresos_transferencia=ing['Transferencia'],
        ingresos_tarjeta=ing['Tarjeta'],
        egresos_efectivo=egr['Efectivo'],
        egresos_transferencia=egr['Transferencia'],
        egresos_tarjeta=egr['Tarjeta'],
        # periodo abierto
        inicio_periodo=inicio,
        periodo_atrasado=periodo_atrasado,
        periodo_desde=periodo_desde,
        dias_sin_corte=dias_sin_corte,
        hoy=ahora.date(),
        fecha_actual=ahora.strftime('%d/%m/%Y'),
        fecha_larga=_fecha_larga(ahora),
        hora_actual=ahora.strftime('%I:%M %p').lower(),
        folio_sugerido=previsualizar_siguiente_folio(),
        usuarios_produccion=Usuario.operadores_asignables(),
    )


@caja_bp.route('/fondo', methods=['POST'])
@permiso_requerido('caja.fondo')
def definir_fondo():
    try:
        monto = _monto_valido(request.form.get('fondo_inicial'))
        db.session.add(FondoCaja(monto=monto, fecha=datetime.now()))
        registrar('CAJA_FONDO', 'caja', None, f'Fondo inicial definido en ${monto:.2f}')
        db.session.commit()
        flash(f"Fondo inicial definido en ${monto:.2f}.", "success")
    except (ValueError, TypeError):
        db.session.rollback()
        flash("El fondo inicial debe ser un monto válido mayor o igual a cero.", "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al definir fondo')
        flash("No se pudo guardar el fondo inicial.", "danger")
    return redirect(url_for('caja.index'))


@caja_bp.route('/ticket', methods=['POST'])
@permiso_requerido('caja.operar')
def crear_ticket():
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    if metodo_pago not in METODOS:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, error='Método de pago no válido.'), 400
        flash("Método de pago no válido.", "warning")
        return redirect(url_for('caja.index'))

    items, total_ticket = [], 0.0
    for cant_str, precio_str, desc in zip(
        request.form.getlist('cantidad[]'),
        request.form.getlist('precio_unitario[]'),
        request.form.getlist('descripcion[]'),
    ):
        try:
            cant = round(float(cant_str), 2)
            precio = round(float(precio_str), 2)
        except (ValueError, TypeError):
            continue
        if not (math.isfinite(cant) and math.isfinite(precio)):
            continue
        subtotal = round(cant * precio, 2)
        desc = (desc or '').strip()
        if cant > 0 and precio > 0 and subtotal > 0 and desc:
            items.append(MovimientoCaja(
                tipo='VENTA_TICKET', cantidad=cant, descripcion=desc,
                monto=subtotal, metodo_pago=metodo_pago, fecha=datetime.now(),
            ))
            total_ticket += subtotal

    if not items:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, error='No se agregaron productos válidos al ticket.'), 400
        flash("No se agregaron productos válidos al ticket.", "warning")
        return redirect(url_for('caja.index'))

    try:
        if not reclamar_envio(request.form.get('token_envio')):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=False, error=MENSAJE_DUPLICADO), 409
            flash(MENSAJE_DUPLICADO, 'info')
            return redirect(url_for('caja.index'))
        db.session.add_all(items)  # todo el ticket o nada
        db.session.flush()
        folio_ticket = f'T-{items[0].id_movimiento:07d}'
        for item in items:
            item.folio = folio_ticket
        registrar('CAJA_TICKET', 'caja', None, f'Ticket ${total_ticket:.2f} ({metodo_pago}), {len(items)} producto(s)')
        db.session.commit()
        cajon_abierto = abrir_cajon_o_avisar() if metodo_pago == 'Efectivo' else None
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            respuesta = dict(
                success=True,
                folio=folio_ticket,
                total=round(total_ticket, 2),
                print_url=url_for('caja.imprimir_ticket', folio=folio_ticket),
            )
            if metodo_pago == 'Efectivo':
                respuesta['cajon_abierto'] = cajon_abierto
                if not cajon_abierto:
                    respuesta['aviso'] = 'Se guardó, pero no se pudo abrir el cajón. Ábrelo con la llave.'
            return jsonify(respuesta)
        flash(f"Ticket registrado con éxito (${total_ticket:.2f}).", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al guardar ticket')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, error='No se pudo guardar el ticket. Intenta nuevamente.'), 500
        flash("No se pudo guardar el ticket. Intenta nuevamente.", "danger")
    return redirect(url_for('caja.index'))


@caja_bp.route('/ticket/<string:folio>/imprimir', methods=['GET'])
@permiso_requerido('caja.operar')
def imprimir_ticket(folio):
    movimientos = MovimientoCaja.query.filter_by(
        tipo='VENTA_TICKET', folio=folio,
    ).order_by(MovimientoCaja.id_movimiento.asc()).all()
    if not movimientos:
        abort(404)
    registrar('CAJA_TICKET_IMPRESO', 'caja', None, f'{folio}: vista de impresión abierta')
    db.session.commit()
    return render_template(
        'caja_ticket.html',
        movimientos=movimientos,
        folio=folio,
        total=sum(float(m.monto or 0) for m in movimientos),
        fecha=movimientos[0].fecha,
        metodo_pago=movimientos[0].metodo_pago,
        atendio=g.get('usuario'),
        configuracion=obtener_configuracion_nota(),
        auto_print=request.args.get('auto') == '1',
    )


@caja_bp.route('/egreso', methods=['POST'])
@permiso_requerido('caja.operar')
def registrar_egreso():
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    if metodo_pago not in METODOS:
        flash("Método de pago no válido.", "warning")
        return redirect(url_for('caja.index'))

    descripcion = (request.form.get('descripcion') or '').strip()
    try:
        costo = _monto_valido(request.form.get('costo'), permitir_cero=False)
    except (ValueError, TypeError):
        costo = 0.0

    if costo > 0 and descripcion:
        try:
            if not reclamar_envio(request.form.get('token_envio')):
                flash(MENSAJE_DUPLICADO, 'info')
                return redirect(url_for('caja.index'))
            db.session.add(MovimientoCaja(
                tipo='EGRESO', cantidad=1, descripcion=descripcion,
                monto=costo, metodo_pago=metodo_pago, fecha=datetime.now(),
            ))
            registrar('CAJA_EGRESO', 'caja', None, f'{descripcion[:120]}: ${costo:.2f} ({metodo_pago})')
            db.session.commit()
            if metodo_pago == 'Efectivo':
                abrir_cajon_o_avisar()
            flash("Egreso guardado e incorporado al historial.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Error al guardar egreso')
            flash("No se pudo guardar el egreso. Intenta nuevamente.", "danger")
    else:
        flash("Datos de egreso no válidos.", "warning")
    return redirect(url_for('caja.index'))


@caja_bp.route('/corte/abrir-cajon', methods=['POST'])
@permiso_requerido('caja.corte')
def abrir_cajon_corte():
    abierto = bool(modulo_cajon.abrir_cajon())
    try:
        registrar('CAJA_CORTE_CAJON', 'caja', None, 'Apertura de cajón para corte')
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('No se pudo registrar la apertura de cajón para corte')
    return jsonify({'cajon_abierto': abierto})


@caja_bp.route('/corte', methods=['POST'])
@permiso_requerido('caja.corte')
def guardar_corte():
    try:
        retiro = _monto_valido(request.form.get('retiro_efectivo'))

        ultimo = _ultimo_corte()
        inicio = ultimo.fecha_corte if ultimo else None
        ahora = datetime.now()

        r = _resumen_periodo(inicio, ahora)
        ing, egr = r["ingresos"], r["egresos"]
        fondo = _fondo_inicial(inicio)
        efectivo_disponible = round(fondo + r['cobrado']['Efectivo'] - egr['Efectivo'], 2)

        if retiro > efectivo_disponible:
            flash(f"El retiro excede el efectivo disponible (${efectivo_disponible:.2f}).", "warning")
            return redirect(url_for('caja.index'))

        db.session.add(CorteCaja(
            fecha_inicio=inicio,
            fecha_corte=ahora,
            fondo_inicial=fondo,
            total_ventas=sum(ing.values()),
            total_efectivo=ing['Efectivo'],
            total_transferencia=ing['Transferencia'],
            total_tarjeta=ing['Tarjeta'],
            total_egresos=sum(egr.values()),
            egresos_efectivo=egr['Efectivo'],
            retiro_efectivo=retiro,
            saldo_final=round(efectivo_disponible - retiro, 2),
        ))
        registrar('CAJA_CORTE', 'caja', None,
                  f'Ventas ${sum(ing.values()):.2f} · egresos ${sum(egr.values()):.2f} · '
                  f'retiro ${retiro:.2f} · queda en caja ${efectivo_disponible - retiro:.2f}')
        db.session.commit()
        flash(
            f"Corte guardado. Nuevo periodo iniciado con ${efectivo_disponible - retiro:.2f} "
            "en caja (puedes cambiar el fondo inicial cuando quieras).",
            "success",
        )
    except (ValueError, TypeError):
        db.session.rollback()
        flash("El retiro debe ser un monto válido mayor o igual a cero.", "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al guardar corte')
        flash("No se pudo guardar el corte. Intenta nuevamente.", "danger")
    return redirect(url_for('caja.index'))

@caja_bp.route('/reporte', methods=['GET'])
@permiso_requerido('caja.corte')
def reporte():
    cortes = CorteCaja.query.order_by(CorteCaja.fecha_corte.desc()).limit(30).all()

    corte = None
    id_corte = request.args.get('corte', type=int)
    if id_corte:
        corte = db.session.get(CorteCaja, id_corte)
        if corte is None:
            abort(404)

    if corte:                                   # corte ya guardado
        inicio, fin = corte.fecha_inicio, corte.fecha_corte
        fondo = float(corte.fondo_inicial or 0)
    else:                                       # periodo abierto
        ultimo = _ultimo_corte()
        inicio, fin = (ultimo.fecha_corte if ultimo else None), None
        fondo = _fondo_inicial(inicio)

    r = _resumen_periodo(inicio, fin)
    ing, egr = r['ingresos'], r['egresos']
    movimientos = sorted(r['historial'], key=lambda f: f['fecha'] or datetime.min)

    total_ventas = round(sum(ing.values()), 2)
    total_egresos = round(sum(egr.values()), 2)
    cobrado_efectivo = r['cobrado']['Efectivo']

    return render_template(
        'caja_reporte.html',
        corte=corte, cortes=cortes, inicio=inicio, fin=fin, generado=datetime.now(),
        fondo=fondo, movimientos=movimientos, metodos=METODOS,
        desglose=[{'metodo': m, 'ventas': ing[m], 'egresos': egr[m],
                   'neto': round(ing[m] - egr[m], 2)} for m in METODOS],
        total_ventas=total_ventas, total_egresos=total_egresos,
        ganancia=round(total_ventas - total_egresos, 2),
        ventas_pedidos=round(sum(f['subtotal'] for f in movimientos if f['origen'] == 'Pedido'), 2),
        ventas_mostrador=round(sum(f['subtotal'] for f in movimientos if f['origen'] == 'Mostrador'), 2),
        ing_efectivo=cobrado_efectivo, egr_efectivo=egr['Efectivo'],
        efectivo_disponible=round(fondo + cobrado_efectivo - egr['Efectivo'], 2),
        iva_periodo=r['iva'], comision_periodo=r['comision'],
    )