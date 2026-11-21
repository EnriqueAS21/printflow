import re
from datetime import date, datetime
from types import SimpleNamespace

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for,
)
from sqlalchemy import case, func, literal_column, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from config import db
from modelos.bitacora import registrar
from modelos.cliente import Cliente
from modelos.configuracion_nota import apartados_para, obtener_configuracion_nota
from modelos.envios import MENSAJE_DUPLICADO, reclamar_envio
from modelos.pedido import (
    ESTADOS_ACTIVOS, MESES, DetallePedido, EstadoPedido, Pago, Pedido, pedidos_activos,
)
from modelos.producto import ListaPrecio
from servicios.permisos import permiso_requerido, tiene_permiso
from servicios.telefonos import normalizar_telefono
from servicios.textos import (
    nombre_normalizado, normalizar_busqueda, normalizar_correo, normalizar_nombre,
    normalizar_rfc,
)

clientes_bp = Blueprint('clientes', __name__, template_folder='../templates')

MAX_RESULTADOS = 15
MAX_RECIENTES = 30
POR_PAGINA_HISTORIAL = 20
MESES_ESTADISTICAS = 12
ESTADOS_HISTORIAL = (EstadoPedido.ENTREGADO.value, EstadoPedido.CANCELADO.value)
CAMPOS_EDITABLES = ('nombre', 'telefono', 'correo', 'tipo', 'rfc', 'razon_social', 'notas', 'activo')
TIPOS = ('Escuela', 'Particular', 'Empresa')
TIPO_DEFAULT = 'Particular'
_CLAVE = re.compile(r'c-?(\d{1,9})', re.IGNORECASE)   # "C-12", "c12"
_DIGITOS = re.compile(r'[0-9]+')
_TELEFONO_CON_SEPARADORES = re.compile(r'[0-9\s\-.()+]+')   # "81 1234-5678", "+52 (81) ..."


# ------------------------------------------------------------ utilidades
def _recortar(texto):
    return (texto or '').strip() or None


# Misma normalización que los @validates de Cliente; aquí se usa para validar
# longitudes y comparar contra lo guardado.
NORMALIZADORES = {
    'correo': normalizar_correo,
    'rfc': normalizar_rfc,
    'razon_social': normalizar_nombre,
    'notas': _recortar,
}


def _escapar_like(texto):
    return texto.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _es_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _a_dict(cliente):
    lista = cliente.lista_precio if cliente.id_lista_precio else None
    if lista is not None and not lista.activo:
        lista = None   # lista inactiva: el pedido usa la predeterminada
    return {
        'id_cliente': cliente.id_cliente,
        'clave': cliente.clave,
        'nombre': cliente.nombre,
        'telefono': cliente.telefono,
        'correo': cliente.correo,
        'whatsapp_url': cliente.whatsapp_url,
        'url_ficha': url_for('clientes.ficha', id_cliente=cliente.id_cliente),
        'id_lista_precio': lista.id_lista if lista else None,
        'nombre_lista': lista.nombre if lista else None,
        'lista_predeterminada': bool(lista.predeterminada) if lista else False,
    }


def _texto(form, campo, etiqueta, maximo, normalizar=_recortar):
    """Texto normalizado o None si queda vacío; ValueError si excede el largo."""
    valor = normalizar(form.get(campo))
    if valor and len(valor) > maximo:
        raise ValueError(f'{etiqueta} admite máximo {maximo} caracteres.')
    return valor


def _leer_basicos(form):
    """Nombre y teléfono obligatorios, con las reglas del alta."""
    nombre = _texto(form, 'nombre', 'El nombre', 120, normalizar_nombre)
    if not nombre:
        raise ValueError('El nombre es obligatorio.')
    telefono = normalizar_telefono(form.get('telefono'))
    if len(telefono) < 10:
        raise ValueError('El teléfono debe tener al menos 10 dígitos.')
    if len(telefono) > 20:
        raise ValueError('El teléfono admite máximo 20 dígitos.')
    return nombre, telefono


def _leer_extras(form):
    """Campos opcionales comunes al alta y a 'completar datos'."""
    tipo = (form.get('tipo') or '').strip() or None
    if tipo is not None and tipo not in TIPOS:
        raise ValueError('Tipo de cliente no válido.')
    return {
        'correo': _texto(form, 'correo', 'El correo', 120, normalizar_correo),
        'tipo': tipo,
        'rfc': _texto(form, 'rfc', 'El RFC', 13, normalizar_rfc),
        'razon_social': _texto(form, 'razon_social', 'La razón social', 150, normalizar_nombre),
        'notas': _texto(form, 'notas', 'Las notas', 2000),
    }


def _totales_activos(pedidos):
    return {
        'total': round(sum(p.total_float for p in pedidos), 2),
        'pagado': round(sum(p.total_pagado for p in pedidos), 2),
        'saldo': round(sum(p.saldo_restante for p in pedidos), 2),
    }


def _ultimos_meses(hoy, cantidad):
    """[(año, mes), ...] de los últimos `cantidad` meses, el actual al final."""
    indice = hoy.year * 12 + hoy.month - 1
    return [divmod(i, 12) for i in range(indice - cantidad + 1, indice + 1)]


def estadisticas_cliente(id_cliente, hoy=None):
    """Resumen de compras del cliente (excluye cancelados), todo con consultas agregadas."""
    hoy = hoy or date.today()
    no_cancelado = (Pedido.id_cliente == id_cliente,
                    Pedido.estatus != EstadoPedido.CANCELADO.value)

    total, num_pedidos, ultimo = (
        db.session.query(
            func.coalesce(func.sum(Pedido.total), 0),
            func.count(Pedido.id_pedido),
            func.max(Pedido.fecha_creacion),
        )
        .filter(*no_cancelado)
        .one()
    )
    total = float(total or 0)

    # Saldo de activos: por pedido, total menos lo abonado (nunca negativo).
    pagado = (
        db.session.query(Pago.id_pedido, func.sum(Pago.monto).label('monto'))
        .group_by(Pago.id_pedido)
        .subquery()
    )
    pendiente = Pedido.total - func.coalesce(pagado.c.monto, 0)
    saldo = (
        db.session.query(func.coalesce(func.sum(case((pendiente > 0, pendiente), else_=0)), 0))
        .select_from(Pedido)
        .outerjoin(pagado, pagado.c.id_pedido == Pedido.id_pedido)
        .filter(Pedido.id_cliente == id_cliente, Pedido.estatus.in_(ESTADOS_ACTIVOS))
        .scalar()
    )

    meses = _ultimos_meses(hoy, MESES_ESTADISTICAS)
    anio_inicio, mes_inicio = meses[0]
    mes = func.date_trunc(literal_column("'month'"), Pedido.fecha_creacion,
                          type_=db.DateTime).label('mes')
    por_mes = {
        (fila.mes.year, fila.mes.month): (fila.pedidos, float(fila.monto or 0))
        for fila in (
            db.session.query(mes, func.count(Pedido.id_pedido).label('pedidos'),
                             func.coalesce(func.sum(Pedido.total), 0).label('monto'))
            .filter(*no_cancelado,
                    Pedido.fecha_creacion >= datetime(anio_inicio, mes_inicio + 1, 1))
            .group_by(mes)
            .all()
        )
    }
    mensual = []
    for anio, mes_cero in meses:
        pedidos_mes, monto = por_mes.get((anio, mes_cero + 1), (0, 0.0))
        mensual.append({'etiqueta': f'{MESES[mes_cero][:3].capitalize()} {anio}',
                        'pedidos': pedidos_mes, 'monto': round(monto, 2)})
    maximo = max((m['monto'] for m in mensual), default=0)
    for m in mensual:
        m['porcentaje'] = round(m['monto'] * 100 / maximo, 1) if maximo else 0

    cantidad = func.sum(DetallePedido.cantidad).label('cantidad')
    top_productos = [
        {'descripcion': descripcion, 'cantidad': float(suma or 0)}
        for descripcion, suma in (
            db.session.query(DetallePedido.descripcion, cantidad)
            .join(Pedido, Pedido.id_pedido == DetallePedido.id_pedido)
            .filter(*no_cancelado)
            .group_by(DetallePedido.descripcion)
            .order_by(cantidad.desc(), DetallePedido.descripcion)
            .limit(5)
            .all()
        )
    ]

    return {
        'total_comprado': round(total, 2),
        'num_pedidos': num_pedidos,
        'ticket_promedio': round(total / num_pedidos, 2) if num_pedidos else 0.0,
        'saldo_activos': round(float(saldo or 0), 2),
        'ultimo_pedido': ultimo,
        'mensual': mensual,
        'top_productos': top_productos,
    }


def _responder_error(mensaje, status, destino, categoria='warning', **extra):
    if _es_ajax():
        return jsonify({'success': False, 'error': mensaje, **extra}), status
    flash(mensaje, categoria)
    return redirect(destino)


@clientes_bp.app_context_processor
def _contexto_clientes():
    def obtener_cliente(id_cliente):
        """Para base.html: carga el cliente a completar (None si no existe)."""
        try:
            return db.session.get(Cliente, int(id_cliente))
        except (TypeError, ValueError):
            return None
    return {'obtener_cliente': obtener_cliente}


# ------------------------------------------------------------ rutas
@clientes_bp.route('/')
@permiso_requerido('clientes.ver')
def lista():
    recientes = (
        Cliente.query
        .filter(Cliente.activo.is_(True))
        .order_by(Cliente.fecha_alta.desc(), Cliente.id_cliente.desc())
        .limit(MAX_RECIENTES)
        .all()
    )
    return render_template('clientes/lista.html', clientes=recientes, tipos=TIPOS)


@clientes_bp.route('/<int:id_cliente>/fiscal')
@permiso_requerido('clientes.ver')
def fiscal(id_cliente):
    cliente = db.session.get(Cliente, id_cliente) or abort(404)
    pedido = (
        Pedido.query
        .join(Pedido.factura)
        .filter(Pedido.id_cliente == cliente.id_cliente)
        .order_by(Pedido.fecha_creacion.desc(), Pedido.id_pedido.desc())
        .first()
    )
    factura = pedido.factura if pedido else None
    return jsonify({
        'rfc': factura.rfc if factura else cliente.rfc or '',
        'razon_social': factura.razon_social if factura else cliente.razon_social or '',
        'regimen': factura.regimen_fiscal if factura else '',
        'cp': factura.cp_fiscal if factura else '',
        'uso_cfdi': factura.uso_cfdi if factura else '',
        'correo': factura.correo if factura else cliente.correo or '',
    })


@clientes_bp.route('/<int:id_cliente>')
@permiso_requerido('clientes.ver')
def ficha(id_cliente):
    cliente = db.session.get(Cliente, id_cliente) or abort(404)
    activos = pedidos_activos(cliente.id_cliente)
    pagina = request.args.get('pagina', 1, type=int)
    historial = (
        Pedido.query
        .options(selectinload(Pedido.detalles))
        .filter(Pedido.id_cliente == cliente.id_cliente, Pedido.estatus.in_(ESTADOS_HISTORIAL))
        .order_by(Pedido.fecha_creacion.desc(), Pedido.id_pedido.desc())
        .paginate(page=pagina, per_page=POR_PAGINA_HISTORIAL, error_out=False)
    )
    # Solo se consulta si el usuario puede verlas; la plantilla tampoco las renderiza.
    estadisticas = (estadisticas_cliente(cliente.id_cliente)
                    if tiene_permiso('clientes.estadisticas') else None)
    listas_precio = (
        ListaPrecio.query.filter(ListaPrecio.activo.is_(True))
        .order_by(ListaPrecio.orden, ListaPrecio.nombre, ListaPrecio.id_lista).all()
        if tiene_permiso('productos.administrar') else []
    )
    return render_template(
        'clientes/ficha.html',
        cliente=cliente, tipos=TIPOS,
        activos=activos, totales=_totales_activos(activos),
        historial=historial, estadisticas=estadisticas,
        listas_precio=listas_precio,
        pestana='historial' if 'pagina' in request.args else 'activos',
    )


@clientes_bp.route('/<int:id_cliente>/lista', methods=['POST'])
@permiso_requerido('productos.administrar')
def asignar_lista(id_cliente):
    cliente = db.session.get(Cliente, id_cliente) or abort(404)
    valor = (request.form.get('id_lista_precio') or '').strip()
    if valor:
        try:
            id_lista = int(valor)
        except (TypeError, ValueError):
            flash('La lista de precios indicada no es válida.', 'warning')
            return redirect(url_for('clientes.ficha', id_cliente=id_cliente))
        lista = ListaPrecio.query.filter_by(id_lista=id_lista, activo=True).first()
        if lista is None:
            flash('La lista de precios indicada no existe o está inactiva.', 'warning')
            return redirect(url_for('clientes.ficha', id_cliente=id_cliente))
        nuevo_id = lista.id_lista
    else:
        lista = None
        nuevo_id = None

    anterior = cliente.id_lista_precio
    if anterior != nuevo_id:
        cliente.id_lista_precio = nuevo_id
        try:
            registrar(
                'CLIENTE_LISTA', 'cliente', cliente.id_cliente,
                f'{cliente.clave} {cliente.nombre}: lista de precios '
                f'{anterior or "—"} → {lista.nombre if lista else "sin asignar"}',
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Error al asignar lista de precios al cliente')
            flash('No se pudo guardar la lista de precios del cliente.', 'danger')
            return redirect(url_for('clientes.ficha', id_cliente=id_cliente))
        flash('Lista de precios del cliente actualizada.', 'success')
    else:
        flash('La lista de precios no cambió.', 'info')
    return redirect(url_for('clientes.ficha', id_cliente=id_cliente))


@clientes_bp.route('/<int:id_cliente>/editar', methods=['POST'])
@permiso_requerido('clientes.editar')
def editar(id_cliente):
    cliente = db.session.get(Cliente, id_cliente, with_for_update=True) or abort(404)
    destino = url_for('clientes.ficha', id_cliente=cliente.id_cliente)
    try:
        nombre, telefono = _leer_basicos(request.form)
        extras = _leer_extras(request.form)
    except ValueError as exc:
        return _responder_error(str(exc), 400, destino)

    existente = (Cliente.query
                 .filter(Cliente.telefono == telefono, Cliente.id_cliente != cliente.id_cliente)
                 .first())
    if existente is not None:
        return _responder_error(
            f'El teléfono ya está registrado con {existente.nombre} ({existente.clave}).',
            409, destino, existente=_a_dict(existente),
        )

    nuevos = {'nombre': nombre, 'telefono': telefono, **extras,
              'tipo': extras['tipo'] or TIPO_DEFAULT,
              'activo': request.form.get('activo') in ('1', 'on', 'true')}
    cambios = []
    for campo in CAMPOS_EDITABLES:
        antes, despues = getattr(cliente, campo), nuevos[campo]
        if antes == despues:
            continue
        setattr(cliente, campo, despues)
        # Las notas pueden ser largas: solo se anota que cambiaron.
        cambios.append(campo if campo == 'notas' else f'{campo}: {antes or "—"} → {despues or "—"}')

    try:
        if cambios:
            registrar('CLIENTE_EDITAR', 'cliente', cliente.id_cliente,
                      f'{cliente.clave} {cliente.nombre}: {"; ".join(cambios)}')
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _responder_error(
            'Ese teléfono acaba de registrarse con otro cliente.', 409, destino,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al editar cliente')
        return _responder_error('Ocurrió un error inesperado al guardar los cambios.', 500,
                                destino, categoria='danger')

    mensaje = 'Cliente actualizado.' if cambios else 'No hubo cambios.'
    if _es_ajax():
        return jsonify({'success': True, 'mensaje': mensaje, 'cambios': cambios,
                        'cliente': _a_dict(cliente)})
    flash(mensaje, 'success' if cambios else 'info')
    return redirect(destino)


@clientes_bp.route('/<int:id_cliente>/nota-activos')
@permiso_requerido('clientes.ver')
def nota_activos(id_cliente):
    cliente = db.session.get(Cliente, id_cliente) or abort(404)
    pedidos = pedidos_activos(cliente.id_cliente)
    totales = _totales_activos(pedidos)
    configuracion = obtener_configuracion_nota()
    # Mismos atributos que apartados_para lee de un pedido, con los totales del cliente.
    resumen = SimpleNamespace(
        folio=cliente.clave, nombre_cliente=cliente.nombre,
        total=totales['total'], saldo_restante=totales['saldo'],
    )
    try:
        apartados = apartados_para('nota', resumen)
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al obtener apartados de la nota de activos')
        apartados = []
    registrar('CLIENTE_NOTA_ACTIVOS', 'cliente', cliente.id_cliente, cliente.clave)
    db.session.commit()
    return render_template(
        'clientes/nota_activos.html',
        cliente=cliente, pedidos=pedidos, totales=totales,
        configuracion=configuracion, apartados=apartados, emision=datetime.now(),
    )


@clientes_bp.route('/buscar')
@permiso_requerido('clientes.ver')
def buscar():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])

    q_normalizado = _escapar_like(normalizar_busqueda(q))
    if _TELEFONO_CON_SEPARADORES.fullmatch(q) and normalizar_telefono(q):
        q = normalizar_telefono(q)   # búsqueda numérica sin separadores
    condiciones = []
    clave = _CLAVE.fullmatch(q)
    if clave:
        condiciones.append(Cliente.id_cliente == int(clave.group(1)))
    elif _DIGITOS.fullmatch(q):
        if len(q) <= 5:
            condiciones.append(Cliente.id_cliente == int(q))
        telefono = func.regexp_replace(Cliente.telefono, r'\D', '', 'g')
        condiciones.append(telefono.contains(q, autoescape=True))
    else:
        condiciones.append(nombre_normalizado(Cliente.nombre).ilike(
            f'%{q_normalizado}%', escape='\\'
        ))

    empieza = case(
        (nombre_normalizado(Cliente.nombre).ilike(f'{q_normalizado}%', escape='\\'), 0), else_=1
    )
    clientes = (
        Cliente.query
        .options(selectinload(Cliente.lista_precio))
        .filter(Cliente.activo.is_(True), or_(*condiciones))
        .order_by(empieza, Cliente.nombre)
        .limit(MAX_RESULTADOS)
        .all()
    )
    return jsonify([_a_dict(c) for c in clientes])


@clientes_bp.route('/nuevo', methods=['POST'])
@permiso_requerido('clientes.crear')
def nuevo():
    destino_error = url_for('clientes.lista')
    try:
        nombre, telefono = _leer_basicos(request.form)
        extras = _leer_extras(request.form)
    except ValueError as exc:
        return _responder_error(str(exc), 400, destino_error)

    existente = Cliente.query.filter_by(telefono=telefono).first()
    if existente is not None:
        return _responder_error(
            f'El teléfono ya está registrado con {existente.nombre} ({existente.clave}).',
            409, destino_error, existente=_a_dict(existente),
        )

    if not reclamar_envio(request.form.get('token_envio')):
        return _responder_error(MENSAJE_DUPLICADO, 409, destino_error, categoria='info')

    try:
        cliente = Cliente(
            nombre=nombre, telefono=telefono,
            correo=extras['correo'], tipo=extras['tipo'] or TIPO_DEFAULT,
            rfc=extras['rfc'], razon_social=extras['razon_social'], notas=extras['notas'],
        )
        db.session.add(cliente)
        db.session.flush()   # asigna id_cliente para la clave
        registrar('CLIENTE_ALTA', 'cliente', cliente.id_cliente, f'{cliente.clave} {cliente.nombre}')
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _responder_error(
            'Ese teléfono acaba de registrarse con otro cliente. Búscalo antes de intentar de nuevo.',
            409, destino_error,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al registrar cliente')
        return _responder_error('Ocurrió un error inesperado al registrar el cliente.', 500,
                                destino_error, categoria='danger')

    mensaje = f'Cliente {cliente.clave} {cliente.nombre} registrado.'
    if _es_ajax():
        return jsonify({'success': True, 'mensaje': mensaje, 'cliente': _a_dict(cliente)})
    flash(mensaje, 'success')
    return redirect(url_for('clientes.ficha', id_cliente=cliente.id_cliente))


@clientes_bp.route('/<int:id_cliente>/completar', methods=['POST'])
@permiso_requerido('clientes.crear')
def completar(id_cliente):
    cliente = db.session.get(Cliente, id_cliente, with_for_update=True) or abort(404)
    destino = url_for('clientes.ficha', id_cliente=cliente.id_cliente)
    try:
        extras = _leer_extras(request.form)
    except ValueError as exc:
        return _responder_error(str(exc), 400, destino)

    # Solo llena lo vacío (o el tipo por defecto); nunca sobrescribe datos capturados.
    actualizados = []
    for campo, valor in extras.items():
        if valor is None:
            continue
        actual = getattr(cliente, campo)
        normalizado = NORMALIZADORES.get(campo, _recortar)(actual)
        vacio = normalizado is None or (campo == 'tipo' and actual == TIPO_DEFAULT)
        if vacio and valor != actual:
            setattr(cliente, campo, valor)
            actualizados.append(campo)

    try:
        if actualizados:
            registrar('CLIENTE_COMPLETAR', 'cliente', cliente.id_cliente,
                      f'{cliente.clave} {cliente.nombre}: {", ".join(actualizados)}')
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al completar cliente')
        return _responder_error('Ocurrió un error inesperado al guardar los datos.', 500,
                                destino, categoria='danger')

    mensaje = 'Datos completados.' if actualizados else 'No había datos nuevos por completar.'
    if _es_ajax():
        return jsonify({'success': True, 'mensaje': mensaje, 'actualizados': actualizados,
                        'cliente': _a_dict(cliente)})
    flash(mensaje, 'success' if actualizados else 'info')
    return redirect(destino)
