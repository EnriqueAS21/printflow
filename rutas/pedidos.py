from datetime import datetime
from io import BytesIO
import json
import math
from pathlib import Path
import re

import pymupdf
from flask import (
    Blueprint, abort, current_app, flash, g, redirect,
    render_template, request, send_file, session, url_for,jsonify, Response,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from PIL import Image
from werkzeug.utils import secure_filename

from config import db
from modelos.bitacora import registrar
from modelos.cajon import abrir_cajon_o_avisar
from modelos.cliente import Cliente
from modelos.envios import MENSAJE_DUPLICADO, reclamar_envio
from modelos.notificacion import notificar, usuarios_por_rol
from modelos.pedido import (
    DatosFactura, DetallePedido, EstadoPedido, Pago, Pedido, REGIMENES_FISCALES,
    TASA_IVA, USOS_CFDI, con_relaciones_tarjeta,
)
from modelos.usuario import Usuario
from servicios.folios import generar_siguiente_folio, previsualizar_siguiente_folio
from servicios.generador_ticket_pdf import generar_ticket_pdf
from servicios.permisos import denegar, es_responsable, modulo_activo, permiso_requerido, tiene_permiso
from servicios.telefonos import normalizar_telefono
from servicios.textos import normalizar_nombre
from servicios.Etiquetas import _url_pedido, generar_qr_data_uri
from modelos.caja import MovimientoCaja
from modelos.configuracion_nota import ConfiguracionNota, obtener_configuracion_nota
from servicios import Etiquetas as etiquetas

from servicios.ColaImpresion import (
    agregar as encolar_etiqueta,
    estado as obtener_estado_cola_etiquetas,
    reanudar as reanudar_trabajos_etiquetas,
    estado_trabajo,
    reintentar,
    descartar,
)



pedidos_bp = Blueprint('pedidos', __name__, template_folder='../templates')

METODOS_PAGO = ('Efectivo', 'Tarjeta', 'Transferencia')
MESES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]
TOLERANCIA = 0.005
MAX_CONFIGURACION_BYTES = 5 * 1024


@pedidos_bp.route('/ficha/<folio>')
@permiso_requerido('dashboard.ver')
def ficha(folio):
    pedido = Pedido.query.filter_by(folio=folio.strip().upper()).first_or_404()
    responsable = es_responsable(pedido, g.usuario)
    if g.usuario.rol == 'Produccion' and not responsable:
        return denegar('Este pedido no está asignado a ti.')
    return render_template('pedidos/ficha.html', pedido=pedido, es_responsable=responsable)


@pedidos_bp.route('/<int:id_pedido>/productos/<int:id_detalle>/terminar', methods=['POST'])
@permiso_requerido('dashboard.terminar')
def terminar_producto(id_pedido, id_detalle):
    pedido = Pedido.query.filter_by(id_pedido=id_pedido).with_for_update().first_or_404()
    detalle = next((item for item in pedido.detalles if item.id_detalle == id_detalle), None)
    if detalle is None or detalle.asignacion is None:
        abort(404)
    if pedido.estatus != EstadoPedido.EN_PROCESO.value:
        flash('El pedido ya no está en proceso.', 'warning')
        return redirect(url_for('pedidos.ficha', folio=pedido.folio))

    asignacion = detalle.asignacion
    if g.usuario.rol == 'Produccion' and asignacion.id_usuario != g.usuario.id_usuario:
        return denegar('Este producto no está asignado a ti.')
    if asignacion.completado:
        flash('Este producto ya estaba marcado como terminado.', 'info')
        return redirect(url_for('pedidos.ficha', folio=pedido.folio))

    asignacion.completado = True
    registrar(
        'PRODUCTO_TERMINADO', 'pedido', pedido.id_pedido,
        f'{pedido.folio}: {detalle.descripcion} · {asignacion.usuario.nombre}',
    )
    if pedido.tiene_asignaciones_por_producto and pedido.productos_completos:
        pedido.estatus = EstadoPedido.TERMINADO.value
        pedido.fecha_terminado = datetime.now()
        registrar('PEDIDO_ESTADO', 'pedido', pedido.id_pedido, f'{pedido.folio}: {pedido.estatus}')
        notificar(
            usuarios_por_rol('Recepcion', 'Administrador'), 'PEDIDO_TERMINADO',
            f'Pedido {pedido.folio} terminado', f'{pedido.nombre_cliente} · listo para entregar',
            pedido=pedido,
        )
        flash('Todos los productos están terminados. El pedido pasó a Terminado.', 'success')
    else:
        flash('Producto marcado como terminado.', 'success')
    db.session.commit()
    return redirect(url_for('pedidos.ficha', folio=pedido.folio))


# ---------------------------------------------------------------- utilidades
def _a_float(valor, default=0.0):
    try:
        return float(valor or default)
    except (TypeError, ValueError):
        return default


def _redirigir_origen(origen, fallback_endpoint='caja.index', **fallback_args):
    """Vuelve a la pantalla desde la que se abrió el formulario."""
    if origen == 'dashboard':
        return redirect(url_for('dashboard.index'))
    if origen == 'caja':
        return redirect(url_for('caja.index'))
    return redirect(url_for(fallback_endpoint, **fallback_args))


def _id_pedido_solicitado():
    """id por form (`pedido_id`, o `id_pedido` legado) o query `?editar=`."""
    raw = (
        request.form.get('pedido_id')
        or request.form.get('id_pedido')
        or request.args.get('editar')
    )
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError('Identificador de pedido inválido.')


# ---------------------------------------------------------- lectura de datos
def _leer_fecha_entrega(form):
    fecha_str = (form.get('fecha_entrega') or '').strip()
    hora_str = (form.get('hora_entrega') or '').strip()
    if not fecha_str:
        raise ValueError('La fecha de entrega es obligatoria.')
    if not hora_str:
        raise ValueError('La hora de entrega es obligatoria.')
    try:
        # El campo de hora siempre envía formato 24 h ("17:30"), sin importar cómo lo muestre el navegador
        return datetime.strptime(f'{fecha_str} {hora_str[:5]}', '%Y-%m-%d %H:%M')
    except ValueError:
        raise ValueError('La fecha u hora de entrega no es válida.')


def _leer_detalles(form, responsables):
    cantidades = form.getlist('cantidad[]')
    descripciones = form.getlist('descripcion[]')
    precios = form.getlist('precio_unitario[]')
    ids_detalle = form.getlist('detalle_id[]')
    ids_producto = form.getlist('producto_id[]')
    ids_presentacion = form.getlist('presentacion_id[]')
    configuraciones = form.getlist('configuracion[]')
    asignados = form.getlist('responsable_producto[]')
    ids_responsables = {usuario.id_usuario for usuario in responsables}
    tabla_presentaciones = db.metadata.tables.get('presentaciones')

    detalles = []
    for indice, (cant, desc, precio) in enumerate(zip(cantidades, descripciones, precios)):
        desc = (desc or '').strip()
        if not (cant or '').strip() and not desc and not (precio or '').strip():
            continue  # fila completamente vacía
        try:
            cantidad = round(float(cant), 2)
            precio_unitario = round(float(precio), 2)
        except (TypeError, ValueError):
            raise ValueError('Cantidad y precio unitario deben ser numéricos.')
        if cantidad <= 0 or precio_unitario < 0 or not desc:
            raise ValueError('Cada producto requiere descripción, cantidad > 0 y precio válido.')
        raw_id_detalle = ids_detalle[indice] if indice < len(ids_detalle) else ''
        try:
            id_detalle = int(raw_id_detalle) if raw_id_detalle else None
        except ValueError:
            raise ValueError('Identificador de producto no válido.')

        raw_responsable = asignados[indice] if indice < len(asignados) else ''
        try:
            responsable_id = int(raw_responsable) if raw_responsable else None
        except ValueError:
            raise ValueError('Responsable del producto no válido.')
        if responsable_id is not None and responsable_id not in ids_responsables:
            raise ValueError('Asigna cada producto a un responsable válido.')

        raw_configuracion = configuraciones[indice] if indice < len(configuraciones) else ''
        if raw_configuracion:
            if len(raw_configuracion.encode('utf-8')) > MAX_CONFIGURACION_BYTES:
                raise ValueError('La configuración del producto no puede superar 5 KB.')
            try:
                configuracion = json.loads(raw_configuracion)
            except (TypeError, ValueError):
                raise ValueError('La configuración del producto no contiene JSON válido.')
            if not isinstance(configuracion, dict):
                raise ValueError('La configuración del producto debe ser un objeto JSON.')
        else:
            configuracion = None

        id_producto = None
        id_presentacion = None
        raw_producto = ids_producto[indice] if indice < len(ids_producto) else ''
        raw_presentacion = ids_presentacion[indice] if indice < len(ids_presentacion) else ''
        try:
            candidato_producto = int(raw_producto) if raw_producto else None
            candidato_presentacion = int(raw_presentacion) if raw_presentacion else None
        except (TypeError, ValueError):
            candidato_producto = candidato_presentacion = None
        if candidato_producto is not None and candidato_presentacion is not None and tabla_presentaciones is not None:
            columna_id = tabla_presentaciones.c.get('id_presentacion')
            columna_producto = tabla_presentaciones.c.get('id_producto')
            if columna_id is not None and columna_producto is not None:
                producto_presentacion = db.session.execute(
                    db.select(columna_producto).where(columna_id == candidato_presentacion)
                ).scalar_one_or_none()
                if producto_presentacion is not None and int(producto_presentacion) == candidato_producto:
                    id_producto = candidato_producto
                    id_presentacion = candidato_presentacion
        if id_producto is None:
            configuracion = None
        elif configuracion is not None:
            configuracion['id_producto'] = id_producto
            configuracion['id_presentacion'] = id_presentacion
            try:
                configuracion_bytes = json.dumps(
                    configuracion, ensure_ascii=False, allow_nan=False,
                    separators=(',', ':'),
                ).encode('utf-8')
            except (TypeError, ValueError):
                raise ValueError('La configuración del producto no contiene JSON válido.')
            if len(configuracion_bytes) > MAX_CONFIGURACION_BYTES:
                raise ValueError('La configuración del producto no puede superar 5 KB.')

        detalles.append({
            'cantidad': cantidad,
            'descripcion': desc,
            'precio_unitario': precio_unitario,
            'id_detalle': id_detalle,
            'responsable_id': responsable_id,
            'id_producto': id_producto,
            'id_presentacion': id_presentacion,
            'configuracion': configuracion,
        })

    if not detalles or sum(d['cantidad'] * d['precio_unitario'] for d in detalles) <= 0:
        raise ValueError('El pedido debe tener al menos un producto con importe mayor a 0.')
    return detalles


def _listas_precio_activas():
    """Devuelve las listas activas normalizadas para el formulario compartido."""
    tabla = next((
        item for item in db.metadata.tables.values()
        if 'lista' in item.name.lower() and 'precio' in item.name.lower()
    ), None)
    if tabla is None:
        return []

    columna_id = next(iter(tabla.primary_key.columns), None)
    columna_nombre = tabla.c.get('nombre')
    if columna_nombre is None:
        columna_nombre = tabla.c.get('descripcion')
    if columna_id is None or columna_nombre is None:
        return []

    columna_activa = tabla.c.get('activo')
    if columna_activa is None:
        columna_activa = tabla.c.get('activa')
    columna_predeterminada = next((tabla.c.get(nombre) for nombre in (
        'predeterminada', 'es_predeterminada', 'por_defecto', 'predeterminada_por_defecto',
    ) if tabla.c.get(nombre) is not None), None)
    filas = db.session.execute(db.select(tabla)).mappings().all()
    listas = []
    for fila in filas:
        if columna_activa is not None and not fila[columna_activa.name]:
            continue
        listas.append({
            'id_lista': fila[columna_id.name],
            'nombre': fila[columna_nombre.name],
            'predeterminada': bool(fila[columna_predeterminada.name]) if columna_predeterminada is not None else False,
        })
    listas.sort(key=lambda lista: (not lista['predeterminada'], str(lista['nombre']).casefold()))
    return listas


def _leer_lista_precio(form):
    if not modulo_activo('clientes'):   # la lista solo llega por un cliente vinculado
        return None
    raw = (form.get('id_lista_precio') or '').strip()
    if not raw:
        return None
    try:
        id_lista = int(raw)
    except (TypeError, ValueError):
        raise ValueError('La lista de precios seleccionada no es válida.')
    listas = _listas_precio_activas()
    tabla_disponible = any(
        'lista' in tabla.name.lower() and 'precio' in tabla.name.lower()
        for tabla in db.metadata.tables.values()
    )
    if tabla_disponible and id_lista not in {int(lista['id_lista']) for lista in listas}:
        raise ValueError('La lista de precios seleccionada no existe o está inactiva.')
    return id_lista


def _leer_responsables(form):
    ids = []
    for raw in form.getlist('responsable_producto[]'):
        raw = (raw or '').strip()
        if not raw:
            continue
        try:
            id_usuario = int(raw)
        except ValueError:
            raise ValueError('Responsable no válido.')
        if id_usuario not in ids:
            ids.append(id_usuario)
    if not ids:
        return []
    usuarios = Usuario.query.filter(
        Usuario.id_usuario.in_(ids), Usuario.rol == 'Produccion'
    ).all()
    if len(usuarios) != len(ids):
        raise ValueError('Algún responsable no existe o no es de Producción.')
    por_id = {u.id_usuario: u for u in usuarios}
    return [por_id[i] for i in ids]


def _leer_datos_formulario(form):
    nombre = normalizar_nombre(form.get('nombre_cliente'))
    telefono = (form.get('telefono_cliente') or '').strip()
    if not nombre or not telefono:
        raise ValueError('Nombre y teléfono del cliente son obligatorios.')
    if len(nombre) > 120:
        raise ValueError('El nombre del cliente admite máximo 120 caracteres.')

    anticipo = round(_a_float(form.get('anticipo')), 2)
    metodo_pago = form.get('metodo_pago', 'Efectivo')
    if anticipo < 0:
        raise ValueError('El anticipo no puede ser negativo.')
    if metodo_pago not in METODOS_PAGO:
        raise ValueError('Método de pago no válido.')

    responsables = _leer_responsables(form)
    detalles = _leer_detalles(form, responsables)
    requiere_factura = form.get('requiere_factura') == 'on'
    subtotal = round(sum(d['cantidad'] * d['precio_unitario'] for d in detalles), 2)
    iva = round(subtotal * TASA_IVA, 2) if requiere_factura else 0
    factura = None
    if requiere_factura:
        rfc = (form.get('rfc') or '').strip().upper()
        razon_social = (form.get('razon_social') or '').strip().upper()
        regimen_fiscal = (form.get('regimen_fiscal') or '').strip().upper()
        cp_fiscal = (form.get('cp_fiscal') or '').strip()
        uso_cfdi = (form.get('uso_cfdi') or '').strip().upper()
        correo = (form.get('correo_factura') or '').strip().lower() or None

        if not re.fullmatch(r'[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}', rfc):
            raise ValueError('El RFC debe tener formato válido (12 o 13 caracteres).')
        if not razon_social:
            raise ValueError('La razón social es obligatoria para facturar.')
        if len(razon_social) > 200:
            raise ValueError('La razón social admite máximo 200 caracteres.')
        if not re.fullmatch(r'\d{5}', cp_fiscal):
            raise ValueError('El código postal fiscal debe tener 5 dígitos.')
        if regimen_fiscal not in {clave for clave, _ in REGIMENES_FISCALES}:
            raise ValueError('Selecciona un régimen fiscal válido.')
        if uso_cfdi not in {clave for clave, _ in USOS_CFDI}:
            raise ValueError('Selecciona un uso de CFDI válido.')
        if correo and len(correo) > 120:
            raise ValueError('El correo para factura admite máximo 120 caracteres.')
        factura = {
            'rfc': rfc,
            'razon_social': razon_social,
            'regimen_fiscal': regimen_fiscal,
            'cp_fiscal': cp_fiscal,
            'uso_cfdi': uso_cfdi,
            'correo': correo,
        }
    return {
        'nombre_cliente': nombre,
        'telefono_cliente': telefono,
        'id_lista_precio': _leer_lista_precio(form),
        'fecha_entrega': _leer_fecha_entrega(form),
        'notas': (form.get('notas') or '').strip() or None,
        'requiere_factura': requiere_factura,
        'factura': factura,
        'responsables': responsables,
        'detalles': detalles,
        'subtotal': subtotal,
        'total': round(subtotal + iva, 2),
        'anticipo': anticipo,
        'metodo_pago': metodo_pago,
    }


def _leer_borrador_formulario(form):
    """Lee los datos disponibles para una vista previa sin validar el formulario."""
    nombre = form.get('nombre_cliente') or ''
    telefono = form.get('telefono_cliente') or ''
    fecha_entrega = None
    fecha = (form.get('fecha_entrega') or '').strip()
    hora = (form.get('hora_entrega') or '').strip()
    if fecha and hora:
        try:
            fecha_entrega = datetime.strptime(f'{fecha} {hora[:5]}', '%Y-%m-%d %H:%M')
        except ValueError:
            pass

    detalles = []
    cantidades = form.getlist('cantidad[]')
    descripciones = form.getlist('descripcion[]')
    precios = form.getlist('precio_unitario[]')
    ids_detalle = form.getlist('detalle_id[]')
    for indice, (cant, desc, precio) in enumerate(zip(cantidades, descripciones, precios)):
        descripcion = (desc or '').strip()
        if not (cant or '').strip() and not descripcion and not (precio or '').strip():
            continue
        try:
            cantidad = float(cant)
            precio_unitario = float(precio)
            if not math.isfinite(cantidad) or not math.isfinite(precio_unitario):
                continue
        except (TypeError, ValueError):
            continue

        raw_id_detalle = ids_detalle[indice] if indice < len(ids_detalle) else ''
        try:
            id_detalle = int(raw_id_detalle) if raw_id_detalle else None
        except (TypeError, ValueError):
            id_detalle = None
        detalles.append({
            'cantidad': round(cantidad, 2),
            'descripcion': descripcion,
            'precio_unitario': round(precio_unitario, 2),
            'id_detalle': id_detalle,
            'responsable_id': None,
        })

    subtotal = round(sum(d['cantidad'] * d['precio_unitario'] for d in detalles), 2)
    requiere_factura = form.get('requiere_factura') == 'on'
    iva = round(subtotal * TASA_IVA, 2) if requiere_factura else 0
    total = round(subtotal + iva, 2)
    anticipo = 0.0
    metodo_pago = None
    try:
        anticipo_leido = float(form.get('anticipo') or '')
        if math.isfinite(anticipo_leido) and 0 <= anticipo_leido <= total:
            anticipo = round(anticipo_leido, 2)
            metodo_pago = form.get('metodo_pago') or 'Efectivo'
            if metodo_pago not in METODOS_PAGO:
                metodo_pago = 'Efectivo'
    except (TypeError, ValueError):
        pass

    factura = None
    if requiere_factura:
        factura = {
            'rfc': form.get('rfc'),
            'razon_social': form.get('razon_social'),
            'regimen_fiscal': form.get('regimen_fiscal'),
            'cp_fiscal': form.get('cp_fiscal'),
            'uso_cfdi': form.get('uso_cfdi'),
            'correo': form.get('correo_factura'),
        }

    return {
        'nombre_cliente': nombre,
        'telefono_cliente': telefono,
        'fecha_entrega': fecha_entrega,
        'notas': form.get('notas'),
        'requiere_factura': requiere_factura,
        'factura': factura,
        'responsables': [],
        'detalles': detalles,
        'subtotal': subtotal,
        'total': total,
        'anticipo': anticipo,
        'metodo_pago': metodo_pago,
    }


def _texto_cobro_terminal(pago):
    if pago and pago.metodo_pago == 'Tarjeta':
        return f' Cobrar en terminal: ${pago.cobrado:.2f}'
    return ''


def _resolver_cliente(form, datos):
    """Cliente a ligar con el pedido: el elegido en el buscador (si está activo) o,
    con 'Guardar cliente' marcado, el que tenga ese teléfono (lo crea si no existe).
    Devuelve (cliente o None, creado). Llamar DESPUÉS de reclamar_envio."""
    if not modulo_activo('clientes'):
        return None, False
    try:
        id_cliente = int(form.get('id_cliente') or 0)
    except (TypeError, ValueError):
        id_cliente = 0
    if id_cliente:
        cliente = db.session.get(Cliente, id_cliente)
        if cliente is not None and cliente.activo:
            return cliente, False

    if not form.get('guardar_cliente') or not tiene_permiso('clientes.crear'):
        return None, False

    telefono = normalizar_telefono(datos['telefono_cliente'])
    if not 10 <= len(telefono) <= 20:
        flash('El cliente no se guardó: el teléfono debe tener de 10 a 20 dígitos.', 'warning')
        return None, False
    existente = Cliente.query.filter_by(telefono=telefono).first()
    if existente is not None:
        return existente, False

    cliente = Cliente(nombre=datos['nombre_cliente'][:120], telefono=telefono)
    db.session.add(cliente)
    db.session.flush()   # asigna id_cliente para la clave
    registrar('CLIENTE_ALTA', 'cliente', cliente.id_cliente, f'{cliente.clave} {cliente.nombre}')
    return cliente, True


# ------------------------------------------------------- creación / edición
def _actualizar_pedido(pedido, datos):
    """Actualiza el pedido, conserva folio y registra cualquier nuevo anticipo."""
    if pedido.estatus in (EstadoPedido.ENTREGADO.value, EstadoPedido.CANCELADO.value):
        raise ValueError('Un pedido entregado o cancelado ya no se puede editar.')
    if datos['requiere_factura'] and any(
        pago.metodo_pago == 'Tarjeta' for pago in pedido.pagos
    ):
        raise ValueError('Este pedido ya tiene pagos con tarjeta; no se puede facturar.')
    if datos['total'] + TOLERANCIA < pedido.total_pagado:
        raise ValueError(
            f"El nuevo total (${datos['total']:.2f}) es menor a lo ya pagado "
            f"(${pedido.total_pagado:.2f}). Ajusta los productos."
        )
    antes = {u.id_usuario for u in pedido.responsables}

    pedido.nombre_cliente = datos['nombre_cliente']
    pedido.telefono_cliente = datos['telefono_cliente']
    if modulo_activo('clientes'):   # apagado: conserva la lista que ya tenía
        pedido.id_lista_precio = datos['id_lista_precio']
    pedido.fecha_entrega = datos['fecha_entrega']
    pedido.notas = datos['notas']
    pedido.requiere_factura = datos['requiere_factura']
    pedido.factura = DatosFactura(**datos['factura']) if datos['factura'] else None
    pedido.responsables = datos['responsables']
    pedido.id_usuario_produccion = (
        datos['responsables'][0].id_usuario if datos['responsables'] else None
    )
    notificar(
        [u for u in datos['responsables'] if u.id_usuario not in antes], 'PEDIDO_ASIGNADO',
        f'Te asignaron el pedido {pedido.folio}',
        f"{datos['nombre_cliente']} · entrega {datos['fecha_entrega'].strftime('%d/%m %I:%M %p')}",
        pedido=pedido,
    )
    pedido.reemplazar_detalles(datos['detalles'])  # recalcula subtotal, IVA y total
    if datos['anticipo'] > 0:
        pedido.agregar_pago(datos['anticipo'], datos['metodo_pago'])


def _crear_pedido(datos):
    """INSERT de un pedido nuevo con folio consecutivo y anticipo opcional."""
    nuevo = Pedido(
        folio=generar_siguiente_folio(),
        nombre_cliente=datos['nombre_cliente'],
        telefono_cliente=datos['telefono_cliente'],
        id_lista_precio=datos['id_lista_precio'],
        fecha_entrega=datos['fecha_entrega'],
        notas=datos['notas'],
        requiere_factura=datos['requiere_factura'],
        estatus=EstadoPedido.EN_PROCESO.value,
        total=0,
    )
    nuevo.factura = DatosFactura(**datos['factura']) if datos['factura'] else None
    nuevo.reemplazar_detalles(datos['detalles'])
    if datos['anticipo'] > 0:
        nuevo.agregar_pago(datos['anticipo'], datos['metodo_pago'])
    nuevo.responsables = datos['responsables']
    nuevo.id_usuario_produccion = (
        datos['responsables'][0].id_usuario if datos['responsables'] else None
    )
    db.session.add(nuevo)
    db.session.flush()
    notificar(
        datos['responsables'], 'PEDIDO_ASIGNADO', f'Nuevo pedido {nuevo.folio}',
        f"{nuevo.nombre_cliente} · entrega {nuevo.fecha_entrega.strftime('%d/%m %I:%M %p')}",
        pedido=nuevo,
    )
    return nuevo


def _columna_tarjeta(pedido):
    return {
        EstadoPedido.EN_PROCESO.value: 'en_proceso',
        EstadoPedido.TERMINADO.value: 'terminado',
        EstadoPedido.ENTREGADO.value: 'entregado',
        EstadoPedido.CANCELADO.value: 'cancelado',
    }.get(pedido.estatus, 'en_proceso')


# ------------------------------------------------------------------- rutas
@pedidos_bp.route('/listas-precio', methods=['GET'])
@permiso_requerido('pedidos.gestionar')
def listar_listas_precio():
    listas = _listas_precio_activas()
    return jsonify(
        listas=listas,
        lista_predeterminada=next(
            (lista['id_lista'] for lista in listas if lista['predeterminada']), None
        ),
    )


@pedidos_bp.route('/<int:id_pedido>/formulario-editar', methods=['GET'])
@permiso_requerido('pedidos.gestionar')
def formulario_editar(id_pedido):
    """HTML del formulario de edición, para insertarlo en el modal del tablero."""
    pedido = Pedido.query.options(
        selectinload(Pedido.cliente),
        selectinload(Pedido.factura),
        selectinload(Pedido.pagos),
        selectinload(Pedido.detalles).selectinload(DetallePedido.asignacion),
    ).filter_by(id_pedido=id_pedido).first_or_404()
    if pedido.estatus in (EstadoPedido.ENTREGADO.value, EstadoPedido.CANCELADO.value):
        mensaje = f'El pedido {pedido.folio} está {pedido.estatus.lower()} y ya no se puede editar.'
        return jsonify(success=False, error=mensaje), 400
    return render_template(
        'pedidos/_formulario_pedido.html',
        pedido=pedido,
        usuarios_produccion=Usuario.query.filter_by(rol='Produccion').order_by(Usuario.nombre).all(),
        origen_formulario='dashboard',
    )


@pedidos_bp.route('/<int:id_pedido>/tarjeta', methods=['GET'])
@permiso_requerido('pedidos.gestionar')
def tarjeta_pedido(id_pedido):
    pedido = con_relaciones_tarjeta(Pedido.query).filter_by(id_pedido=id_pedido).first_or_404()
    return jsonify(
        ok=True,
        html_tarjeta=render_template(
            'pedidos/_tarjeta_pedido.html',
            pedido=pedido,
            columna=_columna_tarjeta(pedido),
        ),
    )


@pedidos_bp.route('/vista-previa', methods=['POST'])
@permiso_requerido('pedidos.gestionar')
def vista_previa_nota_pedido():
    try:
        datos = _leer_borrador_formulario(request.form)
        try:
            id_pedido = _id_pedido_solicitado()
        except ValueError:
            id_pedido = None
        existente = db.session.get(Pedido, id_pedido) if id_pedido is not None else None

        ahora = datetime.now()
        transitorio = Pedido(
            folio=existente.folio if existente else previsualizar_siguiente_folio(),
            nombre_cliente=datos['nombre_cliente'],
            telefono_cliente=datos['telefono_cliente'],
            fecha_creacion=existente.fecha_creacion if existente else ahora,
            fecha_entrega=datos['fecha_entrega'],
            notas=datos['notas'],
            requiere_factura=datos['requiere_factura'],
            estatus=existente.estatus if existente else EstadoPedido.EN_PROCESO.value,
            total=0,
        )
        transitorio.reemplazar_detalles(datos['detalles'])
        transitorio.factura = DatosFactura(**datos['factura']) if datos['factura'] else None

        if existente is not None:
            for pago in existente.pagos:
                transitorio.pagos.append(Pago(
                    monto=pago.monto,
                    iva=pago.iva,
                    comision=pago.comision,
                    metodo_pago=pago.metodo_pago,
                    fecha_pago=pago.fecha_pago,
                ))
        saldo_disponible = max(datos['total'] - (existente.total_pagado if existente else 0), 0)
        if 0 < datos['anticipo'] <= saldo_disponible:
            transitorio.agregar_pago(datos['anticipo'], datos['metodo_pago'])

        configuracion = db.session.get(ConfiguracionNota, 1) or ConfiguracionNota(id=1)
        return render_template(
            'pedidos/nota_pedido.html',
            pedido=transitorio,
            configuracion=configuracion,
            emision=ahora,
            vista_previa=True,
        )
    finally:
        db.session.rollback()


@pedidos_bp.route('/nuevo', methods=['GET', 'POST'])
@permiso_requerido('pedidos.gestionar')
def registrar_pedido():
    origen = request.form.get('origen', '')

    try:
        id_pedido = _id_pedido_solicitado()
    except ValueError as exc:
        flash(str(exc), 'danger')
        return _redirigir_origen(origen)

    # Bifurcación estricta: con id => EDICIÓN (nunca crea); sin id => CREACIÓN.
    pedido = None
    if id_pedido is not None:
        pedido = db.session.get(Pedido, id_pedido)
        if pedido is None:
            flash(f'El pedido #{id_pedido} no existe.', 'danger')
            return _redirigir_origen(origen)

    ajax_edicion = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        and pedido is not None
    )

    if request.method == 'POST':
        try:
            datos = _leer_datos_formulario(request.form)
            if not reclamar_envio(request.form.get('token_envio')):
                if ajax_edicion:
                    return jsonify(
                        ok=False,
                        duplicado=True,
                        mensaje=MENSAJE_DUPLICADO,
                    ), 409
                flash(MENSAJE_DUPLICADO, 'info')
                return _redirigir_origen(origen)
            cliente, cliente_creado = _resolver_cliente(request.form, datos)
            if pedido is not None:
                pagos_antes = len(pedido.pagos)
                _actualizar_pedido(pedido, datos)
                pago_anticipo = (
                    pedido.pagos[-1] if len(pedido.pagos) > pagos_antes else None
                )
                if modulo_activo('clientes'):   # apagado: conserva el vínculo que ya tenía
                    pedido.id_cliente = cliente.id_cliente if cliente else None
                folio = pedido.folio
                texto_terminal = _texto_cobro_terminal(pago_anticipo)
                registrar(
                    'PEDIDO_EDITADO', 'pedido', pedido.id_pedido,
                    f"{folio}: total ${datos['total']:.2f}{texto_terminal}",
                )
                mensaje = f'¡Pedido {folio} actualizado exitosamente!{texto_terminal}'
            else:
                nuevo = _crear_pedido(datos)
                pago_anticipo = nuevo.pagos[-1] if datos['anticipo'] > 0 else None
                nuevo.id_cliente = cliente.id_cliente if cliente else None
                folio = nuevo.folio
                texto_terminal = _texto_cobro_terminal(pago_anticipo)
                db.session.flush()   # asigna id_pedido para la bitácora
                registrar(
                    'PEDIDO_CREADO', 'pedido', nuevo.id_pedido,
                    f"{folio}: {datos['nombre_cliente']} · total ${datos['total']:.2f}{texto_terminal}",
                )
                mensaje = f'¡Pedido {folio} registrado exitosamente!{texto_terminal}'
            id_pedido_guardado = pedido.id_pedido if pedido is not None else nuevo.id_pedido
            respuesta_ajax = None
            if ajax_edicion:
                db.session.flush()
                pedido_tarjeta = con_relaciones_tarjeta(
                    Pedido.query.populate_existing()
                ).filter_by(id_pedido=id_pedido_guardado).first_or_404()
                respuesta_ajax = {
                    'ok': True,
                    'mensaje': mensaje,
                    'id_pedido': id_pedido_guardado,
                    'estado': pedido_tarjeta.estatus,
                    'html_tarjeta': render_template(
                        'pedidos/_tarjeta_pedido.html',
                        pedido=pedido_tarjeta,
                        columna=_columna_tarjeta(pedido_tarjeta),
                    ),
                }
                if request.form.get('accion') == 'pdf':
                    respuesta_ajax['url_pdf'] = url_for(
                        'pedidos.descargar_ticket_pdf',
                        id_pedido=id_pedido_guardado,
                    )
                if cliente_creado:
                    respuesta_ajax['recargar'] = True
            db.session.commit()
            if pago_anticipo and pago_anticipo.metodo_pago == 'Efectivo':
                abrir_cajon_o_avisar()
            if ajax_edicion and cliente_creado:
                session['cliente_por_completar'] = cliente.id_cliente
            elif not ajax_edicion:
                if request.form.get('accion') == 'pdf':
                    session['descargar_pdf'] = id_pedido_guardado
                if cliente_creado:
                    session['cliente_por_completar'] = cliente.id_cliente
            if ajax_edicion:
                return jsonify(respuesta_ajax)
            flash(mensaje, 'success')
            return _redirigir_origen(origen)

        except ValueError as exc:
            db.session.rollback()
            if ajax_edicion:
                return jsonify(ok=False, mensaje=str(exc)), 400
            flash(str(exc), 'warning')
        except IntegrityError:
            db.session.rollback()
            current_app.logger.exception('Integridad al guardar pedido')
            mensaje_error = 'No se pudo guardar: conflicto con datos existentes (¿folio duplicado?). Intenta de nuevo.'
            if ajax_edicion:
                return jsonify(ok=False, mensaje=mensaje_error), 409
            flash(mensaje_error, 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Error al guardar pedido')
            mensaje_error = 'Ocurrió un error inesperado al guardar el pedido.'
            if ajax_edicion:
                return jsonify(ok=False, mensaje=mensaje_error), 500
            flash(mensaje_error, 'danger')

        if pedido is not None:
            pedido = db.session.get(Pedido, id_pedido)  # estado limpio tras rollback

    ahora = datetime.now()
    return render_template(
        'pedidos/nuevo_pedido.html',
        pedido=pedido,
        folio_sugerido=pedido.folio if pedido else previsualizar_siguiente_folio(),
        fecha_larga=f'{ahora.day:02d} de {MESES[ahora.month - 1]} {ahora.year}',
        hora_actual=ahora.strftime('%I:%M %p').lower(),
        usuarios_produccion=Usuario.query.filter_by(rol='Produccion').order_by(Usuario.nombre).all(),
        listas_precio=(listas := _listas_precio_activas()),
        lista_precio_predeterminada=next(
            (lista['id_lista'] for lista in listas if lista['predeterminada']), None
        ),
        regimenes_fiscales=REGIMENES_FISCALES,
        usos_cfdi=USOS_CFDI,
    )


@pedidos_bp.route('/<int:id_pedido>/abonar', methods=['POST'])
@permiso_requerido('pedidos.gestionar')
def registrar_abono(id_pedido):
    origen = request.form.get('origen', '')

    # Bloqueo de fila: evita que dos abonos simultáneos excedan el saldo.
    pedido = db.session.get(Pedido, id_pedido, with_for_update=True)
    if pedido is None:
        abort(404)

    try:
        monto = round(_a_float(request.form.get('monto_abono')), 2)
        metodo_pago = request.form.get('metodo_pago_abono', 'Efectivo')

        if metodo_pago not in METODOS_PAGO:
            raise ValueError('Método de pago no válido.')
        if monto <= 0:
            raise ValueError('El monto a abonar debe ser mayor a 0.')

        saldo = pedido.saldo_restante
        if pedido.estatus == EstadoPedido.CANCELADO.value:
            raise ValueError('El pedido está cancelado.')
        if saldo <= 0:
            raise ValueError('Este pedido ya está liquidado.')
        if monto > saldo + TOLERANCIA:
            raise ValueError(f'El monto excede el saldo restante (${saldo:.2f}).')

        if not reclamar_envio(request.form.get('token_envio')):
            flash(MENSAJE_DUPLICADO, 'info')
            return _redirigir_origen(origen, 'pedidos.registrar_pedido', editar=id_pedido)

        pago = pedido.agregar_pago(monto, metodo_pago)
        texto_terminal = _texto_cobro_terminal(pago)
        registrar(
            'PEDIDO_ABONO', 'pedido', pedido.id_pedido,
            f'{pedido.folio}: abono ${monto:.2f} ({metodo_pago}){texto_terminal}',
        )
        db.session.commit()
        if metodo_pago == 'Efectivo':
            abrir_cajon_o_avisar()

        # `estatus` (producción) NO se modifica: el estado financiero es derivado.
        if pedido.estado_pago == 'Pagado':
            flash(f'¡Pedido {pedido.folio} liquidado exitosamente!{texto_terminal}', 'success')
        else:
            flash(
                f'Abono de ${monto:.2f} registrado en {pedido.folio}. '
                f'Saldo pendiente: ${pedido.saldo_restante:.2f}.{texto_terminal}',
                'success',
            )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'warning')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al registrar abono')
        flash('Ocurrió un error inesperado al registrar el abono.', 'danger')

    return _redirigir_origen(origen, 'pedidos.registrar_pedido', editar=id_pedido)


@pedidos_bp.route('/<int:id_pedido>/pago', methods=['POST'])
@permiso_requerido('pedidos.gestionar')
def registrar_pago(id_pedido):
    pedido = db.session.get(Pedido, id_pedido, with_for_update=True)
    if pedido is None:
        abort(404)

    try:
        metodo_pago = (request.form.get('metodo') or '').strip()
        try:
            monto = round(float(request.form.get('monto') or ''), 2)
        except (TypeError, ValueError):
            raise ValueError('El monto debe ser numérico.')

        if not math.isfinite(monto) or monto <= 0:
            raise ValueError('El monto a pagar debe ser mayor a 0.')
        if metodo_pago not in METODOS_PAGO:
            raise ValueError('Método de pago no válido.')
        if pedido.estatus == EstadoPedido.CANCELADO.value:
            raise ValueError('El pedido está cancelado.')
        saldo = pedido.saldo_restante
        if saldo <= 0:
            raise ValueError('Este pedido ya está liquidado.')
        if monto > saldo:
            raise ValueError(f'El monto excede el saldo restante (${saldo:.2f}).')

        if not reclamar_envio(request.form.get('token_envio')):
            return jsonify(ok=False, error=MENSAJE_DUPLICADO), 409

        pedido.agregar_pago(monto, metodo_pago)
        registrar(
            'PEDIDO_PAGO', 'pedido', pedido.id_pedido,
            f'{pedido.folio}: {metodo_pago} ${monto:.2f}',
        )
        db.session.flush()
        pedido = con_relaciones_tarjeta(Pedido.query.populate_existing()).filter_by(
            id_pedido=id_pedido,
        ).first_or_404()
        html = render_template('pedidos/_tarjeta_pedido.html', pedido=pedido)
        db.session.commit()
        if metodo_pago == 'Efectivo':
            abrir_cajon_o_avisar()
        return jsonify(ok=True, html=html)
    except ValueError as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al registrar pago')
        return jsonify(ok=False, error='Ocurrió un error inesperado al registrar el pago.'), 500


@pedidos_bp.route('/<int:id_pedido>/cancelar', methods=['POST'])
@permiso_requerido('pedidos.cancelar')
def cancelar_pedido(id_pedido):
    origen = request.form.get('origen', '')

    pedido = db.session.get(Pedido, id_pedido, with_for_update=True)
    if pedido is None:
        abort(404)

    try:
        motivo = (request.form.get('motivo') or '').strip()
        if len(motivo) < 3:
            raise ValueError('Indica el motivo de la cancelación.')
        if pedido.estatus == EstadoPedido.CANCELADO.value:
            raise ValueError('Este pedido ya está cancelado.')
        if pedido.estatus == EstadoPedido.ENTREGADO.value:
            raise ValueError('Un pedido entregado no se puede cancelar.')
        if not reclamar_envio(request.form.get('token_envio')):
            flash(MENSAJE_DUPLICADO, 'info')
            return _redirigir_origen(origen, 'dashboard.index')

        devuelto = 0.0
        if request.form.get('devolver') == 'on':
            por_metodo = {}
            for p in pedido.pagos:
                por_metodo[p.metodo_pago] = round(por_metodo.get(p.metodo_pago, 0.0) + float(p.monto or 0), 2)
            for metodo, monto in por_metodo.items():
                db.session.add(MovimientoCaja(
                    tipo='EGRESO', cantidad=1,
                    descripcion=f'Devolución por cancelación {pedido.folio}',
                    folio=pedido.folio,
                    monto=monto, metodo_pago=metodo, fecha=datetime.now(),
                ))
                devuelto += monto

        pedido.estatus = EstadoPedido.CANCELADO.value
        registrar('PEDIDO_CANCELADO', 'pedido', pedido.id_pedido,
                  f'{pedido.folio}: {motivo[:200]} · devuelto ${devuelto:.2f}')
        notificar(pedido.responsables_asignados, 'PEDIDO_CANCELADO', f'Pedido {pedido.folio} cancelado',
                  pedido.nombre_cliente, pedido=pedido)
        db.session.commit()
        flash(f'Pedido {pedido.folio} cancelado.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'warning')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al cancelar pedido')
        flash('Ocurrió un error inesperado al cancelar el pedido.', 'danger')

    return _redirigir_origen(origen, 'dashboard.index')

@pedidos_bp.route('/<int:id_pedido>/ticket', methods=['GET'])
@permiso_requerido('pedidos.imprimir')
def ticket_pedido(id_pedido):
    pedido = db.session.get(Pedido, id_pedido)
    if pedido is None:
        abort(404)
    registrar('PEDIDO_TICKET', 'pedido', pedido.id_pedido, f'{pedido.folio}: ticket generado')
    db.session.commit()
    return render_template(
        'pedidos/ticket.html', pedido=pedido, ahora=datetime.now(),
        url_ficha=_url_pedido(pedido),
        qr_ficha=generar_qr_data_uri(_url_pedido(pedido)),
    )


@pedidos_bp.route('/<int:id_pedido>/ticket.pdf', methods=['GET'])
@permiso_requerido('pedidos.imprimir')
def descargar_ticket_pdf(id_pedido):
    pedido = db.session.get(Pedido, id_pedido)
    if pedido is None:
        abort(404)
    configuracion = obtener_configuracion_nota()
    logo_path = None
    if configuracion.logo_archivo:
        logo_nombre = secure_filename(configuracion.logo_archivo)
        logo_path = Path(current_app.static_folder) / 'uploads' / 'logos' / logo_nombre
    pdf = generar_ticket_pdf(
        pedido,
        atendio=g.get('usuario').nombre if g.get('usuario') else '',
        configuracion=configuracion,
        logo_path=logo_path,
    )
    registrar('PEDIDO_PDF_DESCARGADO', 'pedido', pedido.id_pedido, f'{pedido.folio}: nota PDF descargada')
    db.session.commit()
    nombre = secure_filename(pedido.folio) or f'pedido-{pedido.id_pedido}'
    return send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'nota-{nombre}.pdf',
    )


@pedidos_bp.route('/<int:id_pedido>/ticket.png', methods=['GET'])
@permiso_requerido('pedidos.imprimir')
def descargar_ticket_png(id_pedido):
    """Renderiza todas las páginas de la nota PDF como una sola imagen PNG."""
    pedido = db.session.get(Pedido, id_pedido)
    if pedido is None:
        abort(404)
    configuracion = obtener_configuracion_nota()
    logo_path = None
    if configuracion.logo_archivo:
        logo_nombre = secure_filename(configuracion.logo_archivo)
        logo_path = Path(current_app.static_folder) / 'uploads' / 'logos' / logo_nombre
    pdf = generar_ticket_pdf(
        pedido,
        atendio=g.get('usuario').nombre if g.get('usuario') else '',
        configuracion=configuracion,
        logo_path=logo_path,
    )

    paginas = []
    with pymupdf.open(stream=pdf.getvalue(), filetype='pdf') as documento:
        for pagina in documento:
            pixmap = pagina.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            paginas.append(Image.open(BytesIO(pixmap.tobytes('png'))).convert('RGB'))

    ancho = max(pagina.width for pagina in paginas)
    separacion = 16
    alto = sum(pagina.height for pagina in paginas) + separacion * (len(paginas) - 1)
    imagen = Image.new('RGB', (ancho, alto), 'white')
    y = 0
    for pagina in paginas:
        imagen.paste(pagina, ((ancho - pagina.width) // 2, y))
        y += pagina.height + separacion

    salida = BytesIO()
    imagen.save(salida, format='PNG', optimize=True)
    salida.seek(0)
    return send_file(salida, mimetype='image/png', as_attachment=False, download_name=f'nota-{pedido.folio}.png')

@pedidos_bp.route('/<int:id_pedido>/etiqueta', methods=['POST'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def imprimir_etiqueta(id_pedido):
    pedido = db.session.get(Pedido, id_pedido)
    if pedido is None:
        return jsonify(success=False, error='El pedido no existe.'), 404
    try:
        modo = etiquetas.modo_impresion()
        trabajo = encolar_etiqueta(
            pedido.id_pedido,
            pedido.folio,
            request.host_url,
            current_app._get_current_object(),
        )
        registrar(
            'ETIQUETA_ENCOLADA', 'pedido', pedido.id_pedido,
            f"{pedido.folio}: trabajo {trabajo['id']} agregado a la cola",
        )
        db.session.commit()
        return jsonify(
            success=True,
            encolada=True,
            modo=modo,
            trabajo=trabajo,
        ), 202
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception('No se pudo encolar la etiqueta del pedido %s', id_pedido)
        return jsonify(success=False, error=str(exc)), 500


@pedidos_bp.route('/etiquetas/cola', methods=['GET'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def consultar_cola_etiquetas():
    return jsonify(success=True, cola=obtener_estado_cola_etiquetas())


@pedidos_bp.route('/etiquetas/cola/reanudar', methods=['POST'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def reanudar_cola_etiquetas():
    resultado = reanudar_trabajos_etiquetas()
    return jsonify(success=True, **resultado)


@pedidos_bp.route('/etiquetas/cola/<id_trabajo>', methods=['GET'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def consultar_trabajo_etiqueta(id_trabajo):
    trabajo = estado_trabajo(id_trabajo)
    if trabajo is None:
        return jsonify(success=False, error='El trabajo no existe.'), 404
    return jsonify(success=True, trabajo=trabajo)


@pedidos_bp.route('/etiquetas/cola/reintentar', methods=['POST'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def reintentar_cola_etiquetas():
    return jsonify(success=True, **reintentar())


@pedidos_bp.route('/etiquetas/cola/descartar', methods=['POST'])
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def descartar_cola_etiquetas():
    return jsonify(success=True, **descartar())


@pedidos_bp.route('/<int:id_pedido>/etiqueta/vista')
@permiso_requerido('etiquetas.imprimir', 'pedidos.gestionar')
def vista_etiqueta(id_pedido):
    """PNG de la etiqueta, para probar el diseño sin gastar papel."""
    import io
    pedido = db.session.get(Pedido, id_pedido) or abort(404)
    buf = io.BytesIO()
    etiquetas.generar_imagen(pedido).save(buf, 'PNG')
    return Response(buf.getvalue(), mimetype='image/png')
