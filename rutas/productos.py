import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from config import db
from modelos.bitacora import registrar
from modelos.cliente import Cliente
from modelos.envios import MENSAJE_DUPLICADO, reclamar_envio
from modelos.produccion import Proceso, ProductoPaso
from modelos.producto import FORMAS, ListaPrecio, Presentacion, PresentacionPrecio, Producto
from servicios.cotizador import cotizar
from servicios import ruta_producto
from servicios.permisos import permiso_requerido
from servicios.rutas_produccion import invalidar as invalidar_rutas
from servicios.textos import nombre_normalizado, normalizar_busqueda

productos_bp = Blueprint('productos', __name__)
_CLAVE_PRODUCTO = re.compile(r'p-?(\d{1,9})', re.IGNORECASE)


def _precios(presentacion):
    return [
        {
            'id_lista': precio.id_lista,
            'cantidad_minima': precio.cantidad_minima,
            'precio': precio.precio,
        }
        for precio in presentacion.precios
    ]


def _lista_cliente(cliente):
    for campo in ('id_lista', 'id_lista_precio', 'lista_precio_id'):
        id_lista = getattr(cliente, campo, None)
        if id_lista is not None:
            return id_lista
    lista = getattr(cliente, 'lista_precio', None)
    return getattr(lista, 'id_lista', None)


def _lista_activa(id_lista):
    if id_lista is None:
        return None
    try:
        id_lista = int(id_lista)
    except (TypeError, ValueError):
        return None
    return ListaPrecio.query.filter_by(id_lista=id_lista, activo=True).first()


def _lista_predeterminada():
    return (
        ListaPrecio.query
        .filter(ListaPrecio.activo.is_(True), ListaPrecio.predeterminada.is_(True))
        .order_by(ListaPrecio.orden, ListaPrecio.id_lista)
        .first()
    )


def _formato_cotizacion(valor):
    return f"{Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _resumen_ruta(producto):
    pasos = sorted(producto.rutas_produccion, key=lambda p: p.orden)
    if pasos:
        return ' → '.join(
            f"{p.proceso.nombre}{'*' if p.opcional else ''}" for p in pasos
        )
    if producto.proceso_principal is not None:
        return f"Principal: {producto.proceso_principal.nombre}"
    return ''


@productos_bp.route('/')
@permiso_requerido('productos.ver')
def lista():
    q = (request.args.get('q') or '').strip()
    categoria = (request.args.get('categoria') or '').strip()
    estado = request.args.get('estado', 'activos')
    if estado not in ('activos', 'inactivos', 'todos'):
        estado = 'activos'

    ruta = 'sin' if request.args.get('ruta') == 'sin' else ''

    consulta = Producto.query.options(
        selectinload(Producto.presentaciones).selectinload(Presentacion.precios),
        selectinload(Producto.rutas_produccion).selectinload(ProductoPaso.proceso),
        selectinload(Producto.proceso_principal),
    )
    if ruta == 'sin':
        consulta = consulta.filter(ruta_producto.condicion_sin_ruta())
    if estado == 'activos':
        consulta = consulta.filter(Producto.activo.is_(True))
    elif estado == 'inactivos':
        consulta = consulta.filter(Producto.activo.is_(False))

    if categoria:
        consulta = consulta.filter(Producto.categoria == categoria)
    if q:
        nombre_escapado = normalizar_busqueda(q).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        condiciones = [nombre_normalizado(Producto.nombre).ilike(f'%{nombre_escapado}%', escape='\\')]
        clave = _CLAVE_PRODUCTO.fullmatch(q)
        if clave:
            condiciones.append(Producto.id_producto == int(clave.group(1)))
        consulta = consulta.filter(or_(*condiciones))

    total = consulta.count()
    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except (TypeError, ValueError):
        pagina = 1
    por_pagina = 50
    paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = min(pagina, paginas)
    productos = (
        consulta.order_by(Producto.nombre, Producto.id_producto)
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
        .all()
    )

    lista_predeterminada = _lista_predeterminada()
    id_lista = lista_predeterminada.id_lista if lista_predeterminada else None
    filas = []
    for producto in productos:
        presentaciones = [p for p in producto.presentaciones if p.activo]
        precios = [
            precio.precio
            for presentacion in presentaciones
            for precio in presentacion.precios
            if id_lista is not None and precio.id_lista == id_lista
        ]
        filas.append({
            'producto': producto,
            'presentaciones': presentaciones,
            'desde': min(precios) if precios else None,
            'ruta': _resumen_ruta(producto),
        })

    categorias = [
        fila[0] for fila in db.session.query(Producto.categoria)
        .filter(Producto.categoria.isnot(None), Producto.categoria != '')
        .distinct().order_by(Producto.categoria).all()
    ]
    return render_template(
        'productos/lista.html', filas=filas, q=q, categoria=categoria,
        categorias=categorias, estado=estado, ruta=ruta, pagina=pagina,
        paginas=paginas, total=total,
    )


@productos_bp.route('/<int:id_producto>/estado', methods=['POST'])
@permiso_requerido('productos.administrar')
def cambiar_estado(id_producto):
    producto = db.session.get(Producto, id_producto)
    if producto is None:
        return jsonify({'error': 'El producto no existe.'}), 404

    producto.activo = not producto.activo
    registrar(
        'PRODUCTO_ESTADO',
        entidad='Producto',
        id_entidad=producto.id_producto,
        detalle=(
            f"Producto {producto.clave or producto.id_producto} ({producto.nombre}) "
            f"{'activado' if producto.activo else 'desactivado'}."
        ),
    )
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'activo': bool(producto.activo)})
    return redirect(request.referrer or url_for('productos.lista'))


@productos_bp.route('/buscar')
@permiso_requerido('productos.ver', 'productos.usar')
def buscar():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])

    consulta = (
        Producto.query
        .options(selectinload(Producto.presentaciones))
        .filter(
            Producto.activo.is_(True),
            Producto.presentaciones.any(Presentacion.activo.is_(True)),
        )
    )
    clave = _CLAVE_PRODUCTO.fullmatch(q)
    if clave:
        consulta = consulta.filter(Producto.id_producto == int(clave.group(1)))
    else:
        q_normalizado = normalizar_busqueda(q)
        q_escapado = q_normalizado.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        consulta = consulta.filter(
            nombre_normalizado(Producto.nombre).ilike(f'%{q_escapado}%', escape='\\')
        )

    productos = (
        consulta.order_by(Producto.nombre)
        .limit(15)
        .all()
    )
    return jsonify([
        {
            'id_producto': producto.id_producto,
            'clave': producto.clave,
            'nombre': producto.nombre,
            'categoria': producto.categoria,
            'presentaciones': [
                {
                    'id_presentacion': presentacion.id_presentacion,
                    'nombre': presentacion.nombre,
                    'forma': presentacion.forma,
                }
                for presentacion in producto.presentaciones if presentacion.activo
            ],
        }
        for producto in productos
    ])


@productos_bp.route('/listas')
@permiso_requerido('productos.ver')
def listas():
    filas = (
        ListaPrecio.query
        .filter(ListaPrecio.activo.is_(True))
        .order_by(ListaPrecio.orden, ListaPrecio.nombre, ListaPrecio.id_lista)
        .all()
    )
    return jsonify([
        {
            'id_lista': lista.id_lista,
            'nombre': lista.nombre,
            'predeterminada': bool(lista.predeterminada),
        }
        for lista in filas
    ])


@productos_bp.route('/listas/admin', methods=['GET', 'POST'])
@permiso_requerido('productos.administrar')
def administrar_listas():
    error = None
    if request.method == 'POST':
        datos = request.get_json(silent=True)
        filas = datos.get('listas') if isinstance(datos, dict) else None
        if not isinstance(filas, list):
            error = 'Los datos de las listas no son válidos.'
        else:
            existentes = {lista.id_lista: lista for lista in ListaPrecio.query.all()}
            recibidos = set()
            normalizadas = []
            for fila in filas:
                if not isinstance(fila, dict):
                    error = 'Hay una lista de precios no válida.'
                    break
                nombre = fila.get('nombre')
                if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 80:
                    error = 'Cada lista requiere un nombre de hasta 80 caracteres.'
                    break
                try:
                    orden = int(fila.get('orden', 0))
                    id_lista = int(fila['id_lista']) if fila.get('id_lista') not in (None, '') else None
                except (KeyError, TypeError, ValueError):
                    error = 'El orden o identificador de una lista no es válido.'
                    break
                if id_lista is not None and (id_lista not in existentes or id_lista in recibidos):
                    error = 'Una lista indicada no existe o está repetida.'
                    break
                recibidos.add(id_lista)
                normalizadas.append({
                    'id_lista': id_lista,
                    'nombre': nombre.strip(),
                    'orden': orden,
                    'activo': fila.get('activo') is True,
                    'predeterminada': fila.get('predeterminada') is True,
                })

            predeterminadas = [fila for fila in normalizadas if fila['predeterminada']]
            if error is None and (len(predeterminadas) != 1 or not predeterminadas[0]['activo']):
                error = 'Selecciona exactamente una lista activa como predeterminada.'

            if error is None:
                try:
                    for lista in existentes.values():
                        lista.predeterminada = False
                    db.session.flush()
                    for fila in normalizadas:
                        lista = existentes.get(fila['id_lista']) if fila['id_lista'] is not None else ListaPrecio()
                        if fila['id_lista'] is None:
                            db.session.add(lista)
                        lista.nombre = fila['nombre']
                        lista.orden = fila['orden']
                        lista.activo = fila['activo']
                        lista.predeterminada = fila['predeterminada']
                    # Apaga la predeterminada anterior, incluso si se omitió del formulario.
                    for id_lista, lista in existentes.items():
                        if id_lista not in recibidos:
                            lista.predeterminada = False
                    db.session.commit()
                    return redirect(url_for('productos.administrar_listas'))
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception('Error al guardar listas de precios')
                    error = 'No se pudieron guardar las listas de precios.'

                if request.is_json and error:
                    return jsonify({'error': error}), 400

    filas = ListaPrecio.query.order_by(ListaPrecio.orden, ListaPrecio.nombre, ListaPrecio.id_lista).all()
    return render_template('productos/listas.html', listas=filas, error=error)


@productos_bp.route('/cotizar', methods=['POST'])
@permiso_requerido('productos.ver', 'productos.usar')
def cotizar_producto():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({'error': 'Se requiere un objeto JSON.'}), 400

    try:
        id_presentacion = int(datos.get('id_presentacion'))
    except (TypeError, ValueError):
        return jsonify({'error': 'La presentación indicada no es válida.'}), 400

    presentacion = db.session.get(Presentacion, id_presentacion)
    if presentacion is None or not presentacion.activo:
        return jsonify({'error': 'La presentación no existe o está inactiva.'}), 400
    producto = presentacion.producto
    if not producto.activo:
        return jsonify({'error': 'El producto no existe o está inactivo.'}), 400

    lista_predeterminada = _lista_predeterminada()
    if lista_predeterminada is None:
        return jsonify({'error': 'No hay una lista de precios predeterminada activa.'}), 400

    id_lista_solicitada = datos.get('id_lista')
    if id_lista_solicitada not in (None, ''):
        lista = _lista_activa(id_lista_solicitada)
        if lista is None:
            return jsonify({'error': 'La lista de precios indicada no existe o está inactiva.'}), 400
    else:
        lista = None
        id_cliente = datos.get('id_cliente')
        if id_cliente not in (None, ''):
            try:
                cliente = db.session.get(Cliente, int(id_cliente))
            except (TypeError, ValueError):
                cliente = None
            if cliente is None:
                return jsonify({'error': 'El cliente indicado no existe.'}), 400
            lista = _lista_activa(_lista_cliente(cliente))
        lista = lista or lista_predeterminada

    presentaciones_rollo = []
    if presentacion.forma == 'rollo':
        presentaciones_rollo = (
            Presentacion.query
            .filter_by(id_producto=producto.id_producto, activo=True, forma='rollo')
            .all()
        )
    rollos = [
        {
            'ancho_rollo': rollo.ancho_rollo,
            'margen_rollo': rollo.margen_rollo,
            'cargo_union': rollo.cargo_union,
            'precios': _precios(rollo),
        }
        for rollo in presentaciones_rollo
    ]

    try:
        resultado = cotizar(
            forma=presentacion.forma,
            precios=_precios(presentacion),
            cantidad=datos.get('cantidad'),
            lista_id=lista.id_lista,
            lista_predeterminada_id=lista_predeterminada.id_lista,
            ancho=datos.get('ancho'),
            alto=datos.get('alto'),
            largo=datos.get('largo'),
            minutos=datos.get('minutos'),
            precio_libre=datos.get('precio_libre'),
            piezas_paquete=presentacion.piezas_paquete,
            minutos_minimos=presentacion.minutos_minimos,
            minimo_cobro=presentacion.minimo_cobro,
            cobro_rollo=producto.cobro_rollo,
            rollos=rollos,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    detalle_produccion = None
    if presentacion.forma == 'm2':
        descripcion = (
            f"{producto.nombre} | ({_formato_cotizacion(datos.get('ancho'))} × "
            f"{_formato_cotizacion(datos.get('alto'))} m) = "
            f"{_formato_cotizacion(resultado['medida_cobrada'])} m² × "
            f"${_formato_cotizacion(resultado['precio_medida'])}/m²"
        )
    elif presentacion.forma == 'rollo':
        ancho_rollo = resultado['rollo']['ancho_rollo']
        ancho_rollo_texto = _formato_cotizacion(ancho_rollo)
        detalle_produccion = f"Rollo {ancho_rollo_texto}"
        if resultado['tramos'] > 1:
            detalle_produccion += (
                f" · {resultado['tramos']} tramos de "
                f"{_formato_cotizacion(resultado['ancho_tramo'])} m"
            )
        if resultado['girada']:
            detalle_produccion += ' · girada'

        dimensiones = (
            f"{_formato_cotizacion(datos.get('ancho'))} × "
            f"{_formato_cotizacion(datos.get('alto'))} m"
        )
        if producto.cobro_rollo == 'ancho_rollo':
            if resultado['tramos'] > 1:
                descripcion = (
                    f"{producto.nombre} | {dimensiones} | se cobra "
                    f"{_formato_cotizacion(resultado['medida_cobrada'])} m² × "
                    f"${_formato_cotizacion(resultado['precio_medida'])}/m²"
                )
            else:
                dimensiones_cobradas = (
                    f"{ancho_rollo_texto} × "
                    f"{_formato_cotizacion(max(Decimal(str(datos.get('ancho'))), Decimal(str(datos.get('alto')))))} m"
                )
                descripcion = (
                    f"{producto.nombre} | {dimensiones} | se cobra "
                    f"({dimensiones_cobradas}) = "
                    f"{_formato_cotizacion(resultado['medida_cobrada'])} m² × "
                    f"${_formato_cotizacion(resultado['precio_medida'])}/m²"
                )
        else:
            descripcion = (
                f"{producto.nombre} | ({dimensiones}) = "
                f"{_formato_cotizacion(resultado['medida_cobrada'])} m² × "
                f"${_formato_cotizacion(resultado['precio_medida'])}/m²"
            )
    elif presentacion.forma == 'lineal':
        descripcion = (
            f"{producto.nombre} | {_formato_cotizacion(datos.get('largo'))} m × "
            f"${_formato_cotizacion(resultado['precio_medida'])}/m"
        )
    elif presentacion.forma == 'tiempo':
        descripcion = (
            f"{producto.nombre} | {_formato_cotizacion(datos.get('minutos'))} min × "
            f"${_formato_cotizacion(resultado['precio_medida'])}/min"
        )
    else:
        descripcion = f"{producto.nombre} · {presentacion.nombre}"
    if resultado['aplico_minimo']:
        descripcion += f" | mínimo de cobro ${_formato_cotizacion(presentacion.minimo_cobro)}"
    return jsonify({
        **resultado,
        'descripcion': descripcion,
        'detalle_produccion': detalle_produccion,
        'id_lista': lista.id_lista,
    })


@productos_bp.route('/cotizar-borrador', methods=['POST'])
@permiso_requerido('productos.administrar')
def cotizar_borrador():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({'error': 'Se requiere un objeto JSON.'}), 400

    presentacion = datos.get('presentacion')
    if not isinstance(presentacion, dict) or presentacion.get('forma') not in FORMAS:
        return jsonify({'error': 'La presentación indicada no es válida.'}), 400
    try:
        id_lista = int(datos.get('id_lista'))
    except (TypeError, ValueError):
        return jsonify({'error': 'La lista de precios indicada no es válida.'}), 400
    lista = _lista_activa(id_lista)
    if lista is None:
        return jsonify({'error': 'La lista de precios indicada no existe o está inactiva.'}), 400

    precios = presentacion.get('precios', [])
    if not isinstance(precios, list):
        return jsonify({'error': 'Los precios de la presentación no son válidos.'}), 400

    try:
        forma = presentacion['forma']
        rollos = []
        if forma == 'rollo':
            rollos = [{
                'ancho_rollo': presentacion.get('ancho_rollo'),
                'margen_rollo': presentacion.get('margen_rollo', 0.05),
                'cargo_union': presentacion.get('cargo_union', 0),
                'precios': precios,
            }]
        predeterminada = _lista_predeterminada()
        resultado = cotizar(
            forma=forma,
            precios=precios,
            cantidad=datos.get('cantidad'),
            lista_id=id_lista,
            lista_predeterminada_id=predeterminada.id_lista if predeterminada else id_lista,
            ancho=datos.get('ancho'),
            alto=datos.get('alto'),
            largo=datos.get('largo'),
            minutos=datos.get('minutos'),
            precio_libre=datos.get('precio_libre'),
            piezas_paquete=presentacion.get('piezas_paquete'),
            minutos_minimos=presentacion.get('minutos_minimos'),
            minimo_cobro=presentacion.get('minimo_cobro'),
            cobro_rollo=datos.get('cobro_rollo', 'm2_real'),
            rollos=rollos,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({**resultado, 'id_lista': lista.id_lista})


def _datos_presentacion(presentacion):
    return {
        'id_presentacion': presentacion.id_presentacion,
        'nombre': presentacion.nombre,
        'forma': presentacion.forma,
        'piezas_paquete': presentacion.piezas_paquete,
        'ancho_rollo': float(presentacion.ancho_rollo) if presentacion.ancho_rollo is not None else None,
        'margen_rollo': float(presentacion.margen_rollo) if presentacion.margen_rollo is not None else 0.05,
        'cargo_union': float(presentacion.cargo_union) if presentacion.cargo_union is not None else 0,
        'minutos_minimos': float(presentacion.minutos_minimos) if presentacion.minutos_minimos is not None else None,
        'minimo_cobro': float(presentacion.minimo_cobro) if presentacion.minimo_cobro is not None else None,
        'precios': [
            {
                'id_lista': precio.id_lista,
                'cantidad_minima': float(precio.cantidad_minima),
                'precio': float(precio.precio),
            }
            for precio in presentacion.precios
        ],
    }


def _datos_producto(producto=None):
    if producto is None:
        return {
            'nombre': '', 'categoria': '', 'descripcion': '', 'cobro_rollo': 'm2_real',
            'presentaciones': [], 'ruta': {'pasos': [], 'id_proceso_principal': None},
        }
    return {
        'nombre': producto.nombre,
        'categoria': producto.categoria or '',
        'descripcion': producto.descripcion or '',
        'cobro_rollo': producto.cobro_rollo or 'm2_real',
        'presentaciones': [_datos_presentacion(p) for p in producto.presentaciones if p.activo],
        'ruta': _datos_ruta(producto),
    }


def _datos_ruta(producto):
    ruta = ruta_producto.ruta_de(producto)
    nombres = {p.id_proceso: p for p in Proceso.query.all()}
    return {
        'pasos': [
            {'id_proceso': i, 'opcional': o}
            for i, o in ruta['pasos'] if i in nombres
        ],
        'id_proceso_principal': ruta['id_proceso_principal'],
    }


def _contexto_formulario(producto=None, datos=None, error=None):
    listas_activas = (
        ListaPrecio.query.filter(ListaPrecio.activo.is_(True))
        .order_by(ListaPrecio.orden, ListaPrecio.nombre, ListaPrecio.id_lista).all()
    )
    categorias = [fila[0] for fila in db.session.query(Producto.categoria)
                  .filter(Producto.categoria.isnot(None), Producto.categoria != '')
                  .distinct().order_by(Producto.categoria).all()]
    return {
        'producto': producto,
        'datos': datos if datos is not None else _datos_producto(producto),
        'listas': [
            {
                'id_lista': lista.id_lista,
                'nombre': lista.nombre,
                'predeterminada': bool(lista.predeterminada),
            }
            for lista in listas_activas
        ],
        'categorias': categorias,
        'procesos': [
            {'id_proceso': p.id_proceso, 'nombre': p.nombre, 'activo': bool(p.activo)}
            for p in Proceso.query.filter(Proceso.es_sistema.is_(False))
            .order_by(Proceso.orden, Proceso.nombre, Proceso.id_proceso).all()
        ],
        'error': error,
    }


def _validar_datos_producto(datos):
    errores = []
    if not isinstance(datos, dict):
        return ['Los datos del producto no son válidos.']
    nombre = datos.get('nombre')
    if not isinstance(nombre, str) or not nombre.strip():
        errores.append('El nombre del producto es obligatorio.')
    elif len(nombre.strip()) > 150:
        errores.append('El nombre no puede superar 150 caracteres.')
    categoria = datos.get('categoria')
    if categoria is not None and (not isinstance(categoria, str) or len(categoria.strip()) > 60):
        errores.append('La categoría no puede superar 60 caracteres.')
    descripcion = datos.get('descripcion')
    if descripcion is not None and not isinstance(descripcion, str):
        errores.append('La descripción no es válida.')
    if datos.get('cobro_rollo', 'm2_real') not in ('m2_real', 'ancho_rollo'):
        errores.append('El tipo de cobro de rollo no es válido.')

    if 'ruta' in datos:
        errores.extend(ruta_producto.validar_ruta(datos['ruta'])[1])

    presentaciones = datos.get('presentaciones')
    if not isinstance(presentaciones, list) or not presentaciones:
        errores.append('Agrega al menos una presentación.')
        return errores

    listas = ListaPrecio.query.filter(ListaPrecio.activo.is_(True)).all()
    listas_por_id = {lista.id_lista: lista for lista in listas}
    lista_predeterminada = next((lista for lista in listas if lista.predeterminada), None)
    ids_vistos = set()

    def decimal_valido(valor, etiqueta, permitir_vacio=True, minimo=Decimal('0')):
        if valor in (None, ''):
            if permitir_vacio:
                return None
            errores.append(f'{etiqueta} es obligatorio.')
            return None
        try:
            numero = Decimal(str(valor))
        except (InvalidOperation, TypeError, ValueError):
            errores.append(f'{etiqueta} debe ser numérico.')
            return None
        if not numero.is_finite() or numero < minimo:
            errores.append(f'{etiqueta} debe ser igual o mayor que {minimo}.')
            return None
        return numero

    for indice, presentacion in enumerate(presentaciones, start=1):
        etiqueta = f'Presentación {indice}'
        if not isinstance(presentacion, dict):
            errores.append(f'{etiqueta} no es válida.')
            continue
        id_presentacion = presentacion.get('id_presentacion')
        if id_presentacion not in (None, ''):
            try:
                id_presentacion = int(id_presentacion)
            except (TypeError, ValueError):
                errores.append(f'{etiqueta}: identificador no válido.')
                id_presentacion = None
            if id_presentacion in ids_vistos:
                errores.append(f'{etiqueta}: identificador repetido.')
            ids_vistos.add(id_presentacion)
        if not isinstance(presentacion.get('nombre'), str) or not presentacion['nombre'].strip():
            errores.append(f'{etiqueta}: el nombre es obligatorio.')
        elif len(presentacion['nombre'].strip()) > 80:
            errores.append(f'{etiqueta}: el nombre no puede superar 80 caracteres.')
        forma = presentacion.get('forma')
        if forma not in FORMAS:
            errores.append(f'{etiqueta}: la forma no es válida.')
            continue
        if forma == 'paquete':
            piezas = decimal_valido(presentacion.get('piezas_paquete'), f'{etiqueta}: piezas por paquete', False, Decimal('1'))
            if piezas is not None and piezas != piezas.to_integral_value():
                errores.append(f'{etiqueta}: las piezas por paquete deben ser un entero.')
        if forma == 'rollo':
            decimal_valido(presentacion.get('ancho_rollo'), f'{etiqueta}: ancho del rollo', False, Decimal('0.001'))
            decimal_valido(presentacion.get('margen_rollo', 0.05), f'{etiqueta}: margen del rollo', False)
            decimal_valido(presentacion.get('cargo_union', 0), f'{etiqueta}: cargo por unión')
        if forma == 'tiempo':
            decimal_valido(presentacion.get('minutos_minimos'), f'{etiqueta}: minutos mínimos')
        decimal_valido(presentacion.get('minimo_cobro'), f'{etiqueta}: mínimo de cobro')

        rangos = presentacion.get('rangos')
        if not isinstance(rangos, list) or not rangos:
            errores.append(f'{etiqueta}: agrega al menos un rango de precios.')
            continue
        desde_anterior = None
        for indice_rango, rango in enumerate(rangos):
            etiqueta_rango = f'{etiqueta}: rango {indice_rango + 1}'
            if not isinstance(rango, dict):
                errores.append(f'{etiqueta_rango} no es válido.')
                continue
            desde = decimal_valido(rango.get('desde'), f'{etiqueta_rango}: Desde', False, Decimal('1'))
            if desde is not None:
                if indice_rango == 0 and desde != 1:
                    errores.append(f'{etiqueta}: el primer rango debe comenzar en 1.')
                if desde_anterior is not None and desde <= desde_anterior:
                    errores.append(f'{etiqueta}: los valores Desde deben ser estrictamente crecientes.')
                if presentacion['forma'] not in ('lineal', 'm2', 'rollo') and desde != desde.to_integral_value():
                    errores.append(f'{etiqueta_rango}: Desde debe ser un entero para esta forma.')
                desde_anterior = desde

            precios_rango = rango.get('precios', {})
            if not isinstance(precios_rango, dict):
                errores.append(f'{etiqueta_rango}: los precios no son válidos.')
                continue
            precio_predeterminado_valido = False
            for id_lista_recibido, monto_recibido in precios_rango.items():
                if monto_recibido in (None, ''):
                    continue
                try:
                    id_lista = int(id_lista_recibido)
                except (TypeError, ValueError):
                    errores.append(f'{etiqueta_rango}: una lista de precio no es válida.')
                    continue
                if id_lista not in listas_por_id:
                    errores.append(f'{etiqueta_rango}: una lista de precio no existe o está inactiva.')
                    continue
                monto = decimal_valido(monto_recibido, f'{etiqueta_rango}: precio', False)
                if indice_rango == 0 and lista_predeterminada and id_lista == lista_predeterminada.id_lista and monto is not None:
                    precio_predeterminado_valido = True
            if indice_rango == 0 and not precio_predeterminado_valido:
                errores.append(f'{etiqueta}: el precio del primer rango para la lista predeterminada es obligatorio.')
    return errores


def _precios_desde_rangos(datos):
    for presentacion in datos['presentaciones']:
        precios = []
        for indice, rango in enumerate(presentacion['rangos']):
            cantidad_minima = Decimal('0') if indice == 0 else Decimal(str(rango['desde']))
            for id_lista, monto in rango.get('precios', {}).items():
                if monto in (None, ''):
                    continue
                precios.append({
                    'id_lista': int(id_lista),
                    'cantidad_minima': cantidad_minima,
                    'precio': Decimal(str(monto)),
                })
        presentacion['precios'] = precios


def _presentacion_en_uso(id_presentacion):
    from modelos.pedido import DetallePedido

    columna = getattr(DetallePedido, 'id_presentacion', None)
    if columna is None:
        return False
    return db.session.query(DetallePedido).filter(columna == id_presentacion).first() is not None


def _guardar_producto(producto, datos):
    anteriores = {}
    if producto is not None:
        for presentacion in producto.presentaciones:
            anteriores[presentacion.id_presentacion] = {
                (precio.id_lista, Decimal(str(precio.cantidad_minima))): Decimal(str(precio.precio))
                for precio in presentacion.precios
            }
    else:
        producto = Producto()
        db.session.add(producto)

    producto.nombre = datos['nombre'].strip()
    producto.categoria = (datos.get('categoria') or '').strip() or None
    producto.descripcion = (datos.get('descripcion') or '').strip() or None
    producto.cobro_rollo = datos.get('cobro_rollo', 'm2_real')
    db.session.flush()

    cambio_ruta = None
    if 'ruta' in datos:
        ruta, _errores = ruta_producto.validar_ruta(datos['ruta'])
        nombres = {p.id_proceso: p.nombre for p in Proceso.query.all()}
        antes = ruta_producto.ruta_de(producto)
        if ruta_producto.guardar_ruta(producto, ruta):
            cambio_ruta = (
                f"{_describir_ruta(antes, nombres)} → {_describir_ruta(ruta, nombres)}"
            )

    existentes = {p.id_presentacion: p for p in producto.presentaciones}
    recibidos = set()
    cambios_precios = []
    presentaciones_guardadas = []
    for orden, item in enumerate(datos['presentaciones']):
        id_presentacion = item.get('id_presentacion')
        if id_presentacion not in (None, ''):
            id_presentacion = int(id_presentacion)
            presentacion = existentes.get(id_presentacion)
            if presentacion is None:
                raise ValueError('Una presentación no pertenece a este producto.')
            recibidos.add(id_presentacion)
        else:
            presentacion = Presentacion(id_producto=producto.id_producto)
            db.session.add(presentacion)
        presentacion.nombre = item['nombre'].strip()
        presentacion.forma = item['forma']
        presentacion.orden = orden
        presentacion.activo = True
        presentacion.piezas_paquete = int(item['piezas_paquete']) if item.get('piezas_paquete') not in (None, '') else None
        presentacion.ancho_rollo = item.get('ancho_rollo') if item['forma'] == 'rollo' else None
        presentacion.margen_rollo = item.get('margen_rollo', 0.05) if item['forma'] == 'rollo' else 0.05
        presentacion.cargo_union = item.get('cargo_union', 0) if item['forma'] == 'rollo' else 0
        presentacion.minutos_minimos = item.get('minutos_minimos') if item['forma'] == 'tiempo' else None
        presentacion.minimo_cobro = item.get('minimo_cobro')
        anterior = anteriores.get(presentacion.id_presentacion, {})
        nuevo = {}
        for precio in item.get('precios', []):
            llave = (int(precio['id_lista']), Decimal(str(precio['cantidad_minima'])))
            nuevo[llave] = Decimal(str(precio['precio']))
        if anterior != nuevo:
            cambios_precios.append(
                f"{presentacion.nombre}: { _describir_precios(anterior) } → { _describir_precios(nuevo) }"
            )
        presentacion.precios.clear()
        presentaciones_guardadas.append((presentacion, item))

    db.session.flush()
    for presentacion, item in presentaciones_guardadas:
        presentacion.precios[:] = [
            PresentacionPrecio(
                id_lista=int(precio['id_lista']),
                cantidad_minima=precio['cantidad_minima'],
                precio=precio['precio'],
            )
            for precio in item.get('precios', [])
        ]

    for id_presentacion, presentacion in existentes.items():
        if id_presentacion in recibidos:
            continue
        if _presentacion_en_uso(id_presentacion):
            presentacion.activo = False
        else:
            db.session.delete(presentacion)
    return producto, cambios_precios, cambio_ruta


def _describir_ruta(ruta, nombres):
    pasos = ' > '.join(
        f"{nombres.get(i, i)}{'*' if o else ''}" for i, o in ruta['pasos']
    ) or 'sin pasos'
    principal = nombres.get(ruta['id_proceso_principal'])
    return f"{pasos}" + (f" (principal: {principal})" if principal else '')


def _describir_precios(precios):
    if not precios:
        return 'sin precios'
    partes = []
    nombres = {lista.id_lista: lista.nombre for lista in ListaPrecio.query.all()}
    for (id_lista, cantidad), monto in sorted(precios.items()):
        partes.append(f"{nombres.get(id_lista, f'Lista {id_lista}')}: desde {cantidad} = ${monto}")
    return '; '.join(partes)


def _procesar_formulario(producto=None):
    es_edicion = producto is not None
    try:
        datos = json.loads(request.form.get('datos', ''))
    except (TypeError, ValueError):
        datos = None
    errores = _validar_datos_producto(datos)
    if errores:
        return render_template('productos/form.html', **_contexto_formulario(
            producto, datos if isinstance(datos, dict) else _datos_producto(producto), ' '.join(errores)
        )), 400
    _precios_desde_rangos(datos)

    if producto is None:
        if not reclamar_envio(request.form.get('token_envio')):
            return render_template('productos/form.html', **_contexto_formulario(
                None, datos, MENSAJE_DUPLICADO
            )), 400

    try:
        producto, cambios_precios, cambio_ruta = _guardar_producto(producto, datos)
        if producto.id_producto:
            db.session.flush()
        if producto.id_producto and request.method == 'POST':
            registrar(
                'PRODUCTO_EDITAR' if producto.id_producto and request.path.endswith('/editar') else 'PRODUCTO_ALTA',
                entidad='Producto',
                id_entidad=producto.id_producto,
                detalle=(
                    f"Producto {producto.clave} ({producto.nombre}) guardado."
                    + (f" Cambios de precios: {' | '.join(cambios_precios)}" if cambios_precios else '')
                    + (f" Ruta de producción: {cambio_ruta}." if cambio_ruta else '')
                ),
            )
        db.session.commit()
        if cambio_ruta:
            invalidar_rutas()
    except Exception as exc:
        db.session.rollback()
        if isinstance(exc, ValueError):
            mensaje = str(exc)
        else:
            current_app.logger.exception('Error al guardar producto')
            mensaje = 'No se pudo guardar el producto. Verifica los datos e inténtalo de nuevo.'
        return render_template('productos/form.html', **_contexto_formulario(
            producto, datos, mensaje
        )), 400
    flash(f"Producto {producto.clave} {'actualizado' if es_edicion else 'guardado'}.", 'success')
    return redirect(url_for('productos.lista'))


@productos_bp.route('/<int:id_producto>/ruta')
@permiso_requerido('productos.administrar')
def ruta_guardada(id_producto):
    """Ruta de un producto, para copiarla en el editor (solo procesos activos)."""
    producto = db.session.get(Producto, id_producto)
    if producto is None:
        return jsonify({'error': 'El producto no existe.'}), 404
    ruta = ruta_producto.ruta_de(producto)
    procesos = {p.id_proceso: p for p in Proceso.query.all()}
    validos = [
        (i, o) for i, o in ruta['pasos']
        if i in procesos and procesos[i].activo and not procesos[i].es_sistema
    ]
    principal = ruta['id_proceso_principal']
    principal_valido = principal if principal in procesos and procesos[principal].activo else None
    return jsonify({
        'nombre': producto.nombre,
        'pasos': [{'id_proceso': i, 'opcional': o} for i, o in validos],
        'id_proceso_principal': principal_valido,
        'omitidos': len(ruta['pasos']) - len(validos) + (1 if principal and not principal_valido else 0),
    })


@productos_bp.route('/<int:id_producto>/aplicar-ruta', methods=['GET', 'POST'])
@permiso_requerido('productos.administrar')
def aplicar_ruta_categoria(id_producto):
    """GET: conteos de la categoría. POST {modo}: copia la ruta guardada a la categoría."""
    producto = db.session.get(Producto, id_producto)
    if producto is None:
        return jsonify({'error': 'El producto no existe.'}), 404
    if request.method == 'GET':
        return jsonify(ruta_producto.contar_categoria(producto))

    datos = request.get_json(silent=True)
    modo = datos.get('modo') if isinstance(datos, dict) else None
    try:
        resultado = ruta_producto.aplicar_a_categoria(producto, modo)
        registrar(
            'PRODUCTO_RUTA_CATEGORIA',
            entidad='Producto',
            id_entidad=producto.id_producto,
            detalle=(
                f"Ruta de {producto.clave} ({producto.nombre}) aplicada a la categoría "
                f"{resultado['categoria']} ({'solo sin ruta' if modo == 'sin_ruta' else 'sobrescribir todas'}): "
                f"{resultado['aplicados']} de {resultado['total']} productos "
                f"({resultado['sin_ruta']} sin ruta)."
            ),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error al aplicar ruta a la categoría')
        return jsonify({'error': 'No se pudo aplicar la ruta.'}), 500
    invalidar_rutas()
    return jsonify(resultado)


@productos_bp.route('/nuevo', methods=['GET', 'POST'])
@permiso_requerido('productos.administrar')
def nuevo():
    if request.method == 'POST':
        return _procesar_formulario()
    return render_template('productos/form.html', **_contexto_formulario())


@productos_bp.route('/<int:id_producto>/editar', methods=['GET', 'POST'])
@permiso_requerido('productos.administrar')
def editar(id_producto):
    producto = db.session.get(Producto, id_producto)
    if producto is None:
        return jsonify({'error': 'El producto no existe.'}), 404
    if request.method == 'POST':
        return _procesar_formulario(producto)
    return render_template('productos/form.html', **_contexto_formulario(producto))
