"""Datos de los reportes gerenciales: solo consultas y cálculos, sin Flask."""
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy.orm import selectinload

from modelos.pedido import EstadoPedido, Pedido
from modelos.usuario import Usuario
from rutas.caja import METODOS, _resumen_periodo


def rango_fechas(desde_txt, hasta_txt):
    """Lee 'YYYY-MM-DD'. Por defecto: del día 1 del mes en curso a hoy."""
    hoy = datetime.now().date()

    def leer(txt, defecto):
        try:
            return datetime.strptime(txt, '%Y-%m-%d').date() if txt else defecto
        except ValueError:
            return defecto

    desde = leer(desde_txt, hoy.replace(day=1))
    hasta = leer(hasta_txt, hoy)
    return (hasta, desde) if desde > hasta else (desde, hasta)


def _duracion(horas):
    if horas is None:
        return '—'
    minutos = int(round(horas * 60))
    dias, resto = divmod(minutos, 1440)
    h, m = divmod(resto, 60)
    if dias:
        return f'{dias} d {h} h'
    if h:
        return f'{h} h {m} min'
    return f'{m} min'


# ------------------------------------------------------------ 1. Ventas y flujo
def reporte_ventas(desde, hasta, metodo=None):
    # _resumen_periodo usa inicio < fecha <= fin
    inicio = datetime.combine(desde, time.min) - timedelta(microseconds=1)
    fin = datetime.combine(hasta, time.max)
    filas = _resumen_periodo(inicio, fin)['historial']
    if metodo in METODOS:
        filas = [f for f in filas if f['metodo_pago'] == metodo]
    filas.sort(key=lambda f: f['fecha'] or datetime.min)

    ventas_pedidos = ventas_mostrador = egresos = 0.0
    por_metodo = {m: {'ventas': 0.0, 'egresos': 0.0} for m in METODOS}
    por_dia = defaultdict(lambda: {'pedidos': 0.0, 'mostrador': 0.0, 'egresos': 0.0})

    for f in filas:
        dia = (f['fecha'] or datetime.min).date()
        if f['origen'] == 'Egreso':
            valor = -f['subtotal']                    # el egreso viene en negativo
            egresos += valor
            por_dia[dia]['egresos'] += valor
            if f['metodo_pago'] in por_metodo:
                por_metodo[f['metodo_pago']]['egresos'] += valor
        else:
            # El subtotal de un pago de pedido ya excluye IVA; mostrador queda igual.
            valor = f['subtotal']
            if f['origen'] == 'Pedido':
                ventas_pedidos += valor
                por_dia[dia]['pedidos'] += valor
            else:
                ventas_mostrador += valor
                por_dia[dia]['mostrador'] += valor
            if f['metodo_pago'] in por_metodo:
                por_metodo[f['metodo_pago']]['ventas'] += valor

    total_ventas = round(ventas_pedidos + ventas_mostrador, 2)
    return {
        'filas': filas,
        'ventas_pedidos': round(ventas_pedidos, 2),
        'ventas_mostrador': round(ventas_mostrador, 2),
        'total_ventas': total_ventas,
        'egresos': round(egresos, 2),
        'balance': round(total_ventas - egresos, 2),
        'por_metodo': [
            {'metodo': m, 'ventas': round(v['ventas'], 2), 'egresos': round(v['egresos'], 2),
             'neto': round(v['ventas'] - v['egresos'], 2)}
            for m, v in por_metodo.items()
        ],
        'por_dia': [
            {'fecha': d, 'pedidos': round(v['pedidos'], 2), 'mostrador': round(v['mostrador'], 2),
             'egresos': round(v['egresos'], 2),
             'neto': round(v['pedidos'] + v['mostrador'] - v['egresos'], 2)}
            for d, v in sorted(por_dia.items())
        ],
    }


# ------------------------------------------------------------ 2. Productividad
def reporte_productividad(desde, hasta, id_operador=None):
    ini = datetime.combine(desde, time.min)
    fin = datetime.combine(hasta, time.max)
    pedidos = (
        Pedido.query
        .options(selectinload(Pedido.detalles), selectinload(Pedido.responsables),
                 selectinload(Pedido.responsable_produccion))
        .filter(Pedido.fecha_terminado >= ini, Pedido.fecha_terminado <= fin,
                Pedido.estatus != EstadoPedido.CANCELADO.value)
        .order_by(Pedido.fecha_terminado.asc())
        .all()
    )

    stats = {}

    def ficha(clave, nombre):
        return stats.setdefault(clave, {'operador': nombre, 'pedidos': 0, 'piezas': 0.0,
                                        'a_tiempo': 0, 'retraso': 0, 'horas': 0.0, 'con_horas': 0})

    # Toda la plantilla de Producción aparece, aunque no tenga pedidos en el rango
    for u in Usuario.query.filter_by(rol='Produccion', activo=True).order_by(Usuario.nombre).all():
        if not id_operador or u.id_usuario == id_operador:
            ficha(u.id_usuario, u.nombre)

    tot = {'pedidos': 0, 'piezas': 0.0, 'a_tiempo': 0, 'retraso': 0, 'horas': 0.0, 'con_horas': 0}
    detalle = []

    for p in pedidos:
        participantes = p.responsables_asignados
        if id_operador:
            participantes = [u for u in participantes if u.id_usuario == id_operador]
            if not participantes:
                continue

        piezas = sum(float(d.cantidad or 0) for d in p.detalles)
        a_tiempo = p.fecha_terminado <= p.fecha_entrega
        horas = ((p.fecha_terminado - p.fecha_creacion).total_seconds() / 3600
                 if p.fecha_creacion else None)
        retraso_h = 0 if a_tiempo else (p.fecha_terminado - p.fecha_entrega).total_seconds() / 3600
        clave_res = 'a_tiempo' if a_tiempo else 'retraso'

        # Un pedido con varios responsables cuenta completo para cada uno
        for clave, nombre in ([(u.id_usuario, u.nombre) for u in participantes] or [(0, 'Sin asignar')]):
            s = ficha(clave, nombre)
            s['pedidos'] += 1
            s['piezas'] += piezas
            s[clave_res] += 1
            if horas is not None:
                s['horas'] += horas
                s['con_horas'] += 1

        # El total general cuenta cada pedido una sola vez
        tot['pedidos'] += 1
        tot['piezas'] += piezas
        tot[clave_res] += 1
        if horas is not None:
            tot['horas'] += horas
            tot['con_horas'] += 1

        detalle.append({
            'folio': p.folio,
            'operadores': ', '.join(u.nombre for u in participantes) or 'Sin asignar',
            'entrega': p.fecha_entrega, 'terminado': p.fecha_terminado, 'piezas': piezas,
            'a_tiempo': a_tiempo, 'retraso_txt': '' if a_tiempo else _duracion(retraso_h),
        })

    def cierre(s):
        n = s['pedidos']
        return {**s,
                'pct': round(s['a_tiempo'] * 100 / n, 1) if n else None,
                'promedio_txt': _duracion(s['horas'] / s['con_horas']) if s['con_horas'] else '—'}

    filas = sorted((cierre(s) for s in stats.values()), key=lambda s: (-s['pedidos'], s['operador']))
    return {'filas': filas, 'detalle': detalle, 'totales': cierre({'operador': 'Total', **tot})}


# ------------------------------------------------------------ 3. Cartera
def reporte_cartera(estado_pago=None):
    ahora = datetime.now()
    # Un pedido solo se entrega liquidado, así que los pendientes están en estos dos estados
    pedidos = (
        Pedido.query.options(selectinload(Pedido.pagos))
        .filter(Pedido.estatus.in_([EstadoPedido.EN_PROCESO.value, EstadoPedido.TERMINADO.value]))
        .order_by(Pedido.fecha_entrega.asc())
        .all()
    )

    filas = []
    for p in pedidos:
        if p.saldo_restante <= 0:
            continue
        if estado_pago and p.estado_pago != estado_pago:
            continue
        vencido = p.fecha_entrega < ahora
        # Cartera refleja el saldo del pedido con abonos brutos, IVA incluido.
        filas.append({
            'folio': p.folio, 'cliente': p.nombre_cliente, 'telefono': p.telefono_cliente,
            'creacion': p.fecha_creacion, 'entrega': p.fecha_entrega,
            'total': p.total_float, 'pagado': p.total_pagado, 'saldo': p.saldo_restante,
            'estado_pago': p.estado_pago, 'estatus': p.estatus, 'vencido': vencido,
            'dias_vencido': (ahora.date() - p.fecha_entrega.date()).days if vencido else 0,
        })

    por_cliente = {}
    for f in filas:
        clave = (f['cliente'].strip().lower(), f['telefono'].strip())
        c = por_cliente.setdefault(clave, {'cliente': f['cliente'], 'telefono': f['telefono'],
                                           'pedidos': 0, 'saldo': 0.0})
        c['pedidos'] += 1
        c['saldo'] = round(c['saldo'] + f['saldo'], 2)

    def suma(lista):
        return round(sum(f['saldo'] for f in lista), 2)

    sin_pagar = [f for f in filas if f['estado_pago'] == 'Sin pagar']
    anticipo = [f for f in filas if f['estado_pago'] == 'Anticipo']
    vencidos = [f for f in filas if f['vencido']]
    return {
        'filas': filas,
        'por_cliente': sorted(por_cliente.values(), key=lambda c: -c['saldo']),
        'resumen': {
            'saldo': suma(filas), 'pedidos': len(filas),
            'sin_pagar': suma(sin_pagar), 'n_sin_pagar': len(sin_pagar),
            'anticipo': suma(anticipo), 'n_anticipo': len(anticipo),
            'vencidos': suma(vencidos), 'n_vencidos': len(vencidos),
            'total': round(sum(f['total'] for f in filas), 2),
            'pagado': round(sum(f['pagado'] for f in filas), 2),
        },
    }