import csv
import io
from datetime import datetime

from flask import Blueprint, Response, redirect, render_template, request, url_for

from config import db
from modelos.bitacora import registrar
from modelos.usuario import Usuario
from rutas.caja import METODOS
from servicios import generador_reportes as gen
from servicios.permisos import permiso_requerido

reportes_bp = Blueprint('reportes', __name__, template_folder='../templates')


def _fecha(dt, formato='%Y-%m-%d %H:%M'):
    return dt.strftime(formato) if dt else ''


def _csv(nombre, encabezados, filas):
    """CSV en UTF-8 con BOM para que Excel respete los acentos."""
    buf = io.StringIO()
    escritor = csv.writer(buf)
    escritor.writerow(encabezados)
    escritor.writerows(filas)
    registrar('REPORTE_EXPORTADO', 'reporte', None, nombre)
    db.session.commit()
    resp = Response('\ufeff' + buf.getvalue(), mimetype='text/csv')
    resp.headers['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return resp


@reportes_bp.route('/')
@permiso_requerido('reportes.ver')
def index():
    return redirect(url_for('reportes.ventas'))


@reportes_bp.route('/ventas')
@permiso_requerido('reportes.ver')
def ventas():
    desde, hasta = gen.rango_fechas(request.args.get('desde'), request.args.get('hasta'))
    metodo = request.args.get('metodo')
    metodo = metodo if metodo in METODOS else None
    datos = gen.reporte_ventas(desde, hasta, metodo)

    if request.args.get('formato') == 'csv':
        # Para pedidos, subtotal ya representa Pago.monto - Pago.iva.
        filas = [[
            _fecha(f['fecha']), 'Egreso' if f['origen'] == 'Egreso' else 'Venta', f['origen'],
            f['folio'] if f['folio'] != 'N/A' else '', f['cantidad'], ' | '.join(f['lineas']),
            f['metodo_pago'], f"{f['subtotal']:.2f}",
        ] for f in datos['filas']]
        return _csv(f'ventas_{desde}_{hasta}.csv',
                    ['Fecha y hora', 'Tipo', 'Origen', 'Folio', 'Cantidad', 'Descripción',
                     'Método de pago', 'Importe'], filas)

    return render_template('reportes/ventas.html', desde=desde, hasta=hasta, metodo=metodo,
                           metodos=METODOS, datos=datos, generado=datetime.now())


@reportes_bp.route('/productividad')
@permiso_requerido('reportes.ver')
def productividad():
    desde, hasta = gen.rango_fechas(request.args.get('desde'), request.args.get('hasta'))
    operador = request.args.get('operador', type=int)
    datos = gen.reporte_productividad(desde, hasta, operador)
    formato = request.args.get('formato')

    if formato == 'csv':
        filas = [[f['operador'], f['pedidos'], f"{f['piezas']:g}", f['a_tiempo'], f['retraso'],
                  '' if f['pct'] is None else f"{f['pct']}", f['promedio_txt']]
                 for f in datos['filas'] + [datos['totales']]]
        return _csv(f'productividad_resumen_{desde}_{hasta}.csv',
                    ['Operador', 'Pedidos finalizados', 'Piezas', 'A tiempo', 'Con retraso',
                     '% a tiempo', 'Tiempo promedio de producción'], filas)
    if formato == 'csv_detalle':
        filas = [[d['folio'], d['operadores'], _fecha(d['entrega']), _fecha(d['terminado']),
                  f"{d['piezas']:g}", 'A tiempo' if d['a_tiempo'] else 'Con retraso', d['retraso_txt']]
                 for d in datos['detalle']]
        return _csv(f'productividad_detalle_{desde}_{hasta}.csv',
                    ['Folio', 'Operador(es)', 'Entrega pactada', 'Terminado', 'Piezas',
                     'Resultado', 'Retraso'], filas)

    return render_template(
        'reportes/productividad.html', desde=desde, hasta=hasta, operador=operador, datos=datos,
        operadores=Usuario.query.filter_by(rol='Produccion').order_by(Usuario.nombre).all(),
        generado=datetime.now())


@reportes_bp.route('/cartera')
@permiso_requerido('reportes.ver')
def cartera():
    estado = request.args.get('estado')
    estado = estado if estado in ('Sin pagar', 'Anticipo') else None
    datos = gen.reporte_cartera(estado)

    if request.args.get('formato') == 'csv':
        filas = [[f['folio'], f['cliente'], f['telefono'], _fecha(f['creacion']), _fecha(f['entrega']),
                  f"{f['total']:.2f}", f"{f['pagado']:.2f}", f"{f['saldo']:.2f}", f['estado_pago'],
                  f['estatus'], f['dias_vencido']] for f in datos['filas']]
        return _csv(f'cartera_{datetime.now():%Y-%m-%d}.csv',
                    ['Folio', 'Cliente', 'Teléfono', 'Fecha de creación', 'Fecha de entrega', 'Total',
                     'Anticipo / pagado', 'Saldo pendiente', 'Estado de pago', 'Estatus de producción',
                     'Días de retraso en la entrega'], filas)

    return render_template('reportes/cartera.html', estado=estado, datos=datos, generado=datetime.now())