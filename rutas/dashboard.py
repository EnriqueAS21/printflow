
import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import selectinload

from config import db
from modelos.bitacora import Bitacora, registrar
from modelos.notificacion import notificar, usuarios_por_rol
from modelos.pedido import DetallePedido, EstadoPedido, Pedido, con_relaciones_tarjeta
from modelos.perfil_usuario import PerfilUsuario
from modelos.usuario import Usuario
from rutas.notificaciones import ACCIONES_TABLERO, version_tablero
from servicios.permisos import es_responsable, permiso_requerido, solo_sus_pedidos, tiene_permiso
from servicios.telefonos import normalizar_telefono
from servicios.textos import nombre_normalizado, normalizar_busqueda

PERMISO_POR_ESTADO = {
    EstadoPedido.TERMINADO.value: 'dashboard.terminar',
    EstadoPedido.ENTREGADO.value: 'dashboard.entregar',
}

dashboard_bp = Blueprint('dashboard', __name__, template_folder='../templates')

TRANSICIONES = {
    EstadoPedido.EN_PROCESO.value: EstadoPedido.TERMINADO.value,
    EstadoPedido.TERMINADO.value: EstadoPedido.ENTREGADO.value
}

COLUMNA_POR_ESTADO = {
    EstadoPedido.EN_PROCESO.value: 'en-proceso',
    EstadoPedido.TERMINADO.value: 'terminado',
    EstadoPedido.ENTREGADO.value: 'entregado',
}
MAX_FILAS_CAMBIOS = 200
PAGINA_ENTREGADOS = 30
ORDENES_ENTREGADOS = ('reciente', 'antiguo')   # el primero es el predeterminado
# Repaso de bitácora: un guardado con id menor puede confirmarse después de otro.
# Solo entran las entradas recientes, para que un sondeo sin cambios no renderice nada.
REPASO_BITACORA = 20
REPASO_SEGUNDOS = 30

# Tablero de Producción: la columna depende de la fecha de entrega contra hoy (hora local).
COLUMNAS_PRODUCCION = (
    ('vencidos', 'Vencidos'),
    ('hoy', 'Para hoy'),
    ('proximos', 'Próximos'),
    ('terminados-hoy', 'Listos'),   # terminados hoy; un solo título en todos los anchos
)
PLANTILLA_POR_COLUMNA_PRODUCCION = {   # la tarjeta solo distingue en_proceso / terminado
    'vencidos': 'en_proceso', 'hoy': 'en_proceso', 'proximos': 'en_proceso', 'terminados-hoy': 'terminado',
}
COLUMNA_CANCELADO = 'cancelado'   # no existe en el tablero: la tarjeta sale en solo lectura
MIN_CARACTERES_BUSQUEDA = 2
MAX_CARACTERES_BUSQUEDA = 100
MAX_RESULTADOS_BUSQUEDA = 20
MIN_DIGITOS_TELEFONO = 4
MODO_COMPLETO = 'completo'   # cualquier otro valor es "auto": sin buscar en productos
_PARECE_FOLIO = re.compile(r'[^\W\d_]*-?[0-9]+')   # "AC1234", "AC-1234" o "1234"
_TELEFONO_CON_SEPARADORES = re.compile(r'[0-9\s\-.()+]+')   # "81 1234-5678", "+52 (81) ..."
_ID_PEDIDO = re.compile(r'[0-9]{1,10}')


def _es_produccion():
    return getattr(g.usuario, 'rol', None) == 'Produccion'


def _columna_produccion(pedido, hoy):
    """Columna del tablero de Producción, o None si el pedido no se muestra (sin consultas)."""
    if pedido.estatus == EstadoPedido.EN_PROCESO.value:
        entrega = pedido.fecha_entrega
        if entrega is None:
            return 'proximos'
        dia = entrega.date()
        return 'vencidos' if dia < hoy else 'hoy' if dia == hoy else 'proximos'
    if (pedido.estatus == EstadoPedido.TERMINADO.value
            and pedido.fecha_terminado is not None and pedido.fecha_terminado.date() == hoy):
        return 'terminados-hoy'
    return None


def _filtrar_por_rol(query):
    """Quien solo opera sus pedidos (Producción) solo ve los pedidos donde es UNO de los responsables."""
    if solo_sus_pedidos():
        uid = g.usuario.id_usuario
        query = query.filter(or_(
            Pedido.id_usuario_produccion == uid,
            Pedido.responsables.any(Usuario.id_usuario == uid),
        ))
    return query


def _consulta_entregados():
    return _filtrar_por_rol(
        con_relaciones_tarjeta(Pedido.query).filter_by(estatus=EstadoPedido.ENTREGADO.value)
    )


def _total_entregados():
    return _filtrar_por_rol(Pedido.query.filter_by(estatus=EstadoPedido.ENTREGADO.value)).count()


def _por_cobrar_terminados():
    """Saldo pendiente de los pedidos Terminado (por recoger). Solo se calcula para quien ve caja."""
    pedidos = _filtrar_por_rol(
        Pedido.query.options(selectinload(Pedido.pagos)).filter_by(estatus=EstadoPedido.TERMINADO.value)
    ).all()
    return round(sum(p.saldo_restante for p in pedidos), 2)


def _pagina_entregados(orden, despues=None):
    """Una página de Entregado en el orden pedido: (pedidos, hay_mas).

    `despues` = (fecha_entregado, id) de la última tarjeta que el cliente ya tiene. Se pagina por
    posición y no por desplazamiento: los entregados nuevos no repiten ni saltan tarjetas.
    """
    descendente = orden == 'reciente'
    consulta = _consulta_entregados()
    if despues is not None:
        fecha, id_pedido = despues
        if descendente:
            consulta = consulta.filter(or_(
                Pedido.fecha_entregado < fecha,
                and_(Pedido.fecha_entregado == fecha, Pedido.id_pedido < id_pedido)))
        else:
            consulta = consulta.filter(or_(
                Pedido.fecha_entregado > fecha,
                and_(Pedido.fecha_entregado == fecha, Pedido.id_pedido > id_pedido)))
    columnas = (Pedido.fecha_entregado, Pedido.id_pedido)
    filas = consulta.order_by(*[c.desc() if descendente else c.asc() for c in columnas]) \
        .limit(PAGINA_ENTREGADOS + 1).all()
    return filas[:PAGINA_ENTREGADOS], len(filas) > PAGINA_ENTREGADOS


def _perfiles_foto():
    """Fotos de los operadores (activos o no: una tarjeta puede mostrar a un responsable dado de baja)."""
    return {
        id_usuario: archivo
        for id_usuario, archivo in db.session.query(
            PerfilUsuario.id_usuario, PerfilUsuario.foto_archivo,
        ).join(Usuario, Usuario.id_usuario == PerfilUsuario.id_usuario).filter(
            Usuario.rol == 'Produccion',
            PerfilUsuario.foto_archivo.isnot(None),
        ).all()
    }


def _contexto_tarjeta():
    """Lo que la tarjeta necesita además de `pedido` y `columna`. Nunca incluye la lista de usuarios."""
    return {'perfiles_foto': _perfiles_foto()}


def _contexto_tablero():
    """Contexto de la página completa.

    Los operadores (menú Responsable y formulario de nota) solo se entregan a quien no se limita a sus
    pedidos: Producción no debe recibir los nombres de sus compañeros, ni en el HTML ni en /cambios.
    Solo operadores activos: no se asigna trabajo a quien está dado de baja.
    """
    contexto = _contexto_tarjeta()
    if not solo_sus_pedidos():
        operadores = Usuario.operadores_asignables()
        contexto.update(operadores=operadores, usuarios_produccion=operadores)
    return contexto


def _renderizar_tarjetas(pedidos, por_fecha=False):
    """Tarjetas en formato JSON, en el mismo orden que `pedidos`.

    `por_fecha` (tablero de Producción): la columna sale de la fecha; lo que no se muestra se omite.
    """
    # Una sola corrida de los context processors, sin importar cuántas tarjetas se rendericen.
    contexto = _contexto_tarjeta()
    current_app.update_template_context(contexto)
    plantilla = current_app.jinja_env.get_template('pedidos/_tarjeta_pedido.html')
    hoy = datetime.now().date()

    tarjetas = []
    for pedido in pedidos:
        if por_fecha:
            columna = _columna_produccion(pedido, hoy)
            if columna is None:
                if pedido.estatus != EstadoPedido.CANCELADO.value:
                    continue
                columna, para_plantilla = COLUMNA_CANCELADO, COLUMNA_CANCELADO
            else:
                para_plantilla = PLANTILLA_POR_COLUMNA_PRODUCCION[columna]
        else:
            columna = COLUMNA_POR_ESTADO.get(pedido.estatus, COLUMNA_CANCELADO)
            para_plantilla = columna.replace('-', '_')
        tarjetas.append({
            'id': pedido.id_pedido,
            'columna': columna,
            # En Entregado el orden (y el cursor de la paginación) es la fecha de entregado.
            'orden': (
                (pedido.fecha_entregado or pedido.fecha_entrega)
                if columna == 'entregado' else pedido.fecha_entrega
            ).isoformat(),
            'html': plantilla.render(contexto, pedido=pedido, columna=para_plantilla),
        })
    return tarjetas


def _escapar_like(texto):
    return texto.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _ids_excluidos(valor):
    """Ids válidos de "1,2,3"; lo demás se ignora."""
    return {int(parte) for parte in (valor or '').split(',') if _ID_PEDIDO.fullmatch(parte.strip())}


def _coincide_nombre(texto):
    patron = f'%{_escapar_like(normalizar_busqueda(texto))}%'
    return nombre_normalizado(Pedido.nombre_cliente).ilike(patron, escape='\\')


def _coincide_producto(texto):
    patron = f'%{_escapar_like(normalizar_busqueda(texto))}%'
    return Pedido.detalles.any(nombre_normalizado(DetallePedido.descripcion).ilike(patron, escape='\\'))


def _condiciones_busqueda(q, modo):
    """Devuelve (condición de coincidencia, prioridad para ordenar)."""
    folio_parcial = Pedido.folio.ilike(f'%{_escapar_like(q)}%', escape='\\')
    if modo == MODO_COMPLETO:
        # Cada palabra en el cliente o en algún producto: "colegio lona" = lonas del Colegio X.
        texto = and_(*[or_(_coincide_nombre(p), _coincide_producto(p)) for p in q.split()])
    else:
        texto = _coincide_nombre(q)
    condiciones = [folio_parcial, texto]

    digitos = normalizar_telefono(q)
    if _TELEFONO_CON_SEPARADORES.fullmatch(q) and len(digitos) >= MIN_DIGITOS_TELEFONO:
        telefono = func.regexp_replace(Pedido.telefono_cliente, r'\D', '', 'g')
        condiciones.append(telefono.contains(digitos, autoescape=True))

    if not _PARECE_FOLIO.fullmatch(q):
        return or_(*condiciones), case((func.lower(Pedido.folio) == q.lower(), 0), else_=1)

    folio_exacto = func.lower(Pedido.folio) == q.lower()
    numero = q.lstrip('0')
    if q.isdigit() and numero:   # "1234" o "0123": el consecutivo, sin importar el prefijo ni los ceros
        consecutivo = func.ltrim(func.substring(Pedido.folio, r'[0-9]+$'), '0')
        folio_exacto = or_(folio_exacto, consecutivo == numero)
    return or_(*condiciones), case((folio_exacto, 0), (folio_parcial, 1), else_=2)


def _folio_sugerido():
    """Consecutivo siguiente al último folio ('' si no termina en número)."""
    ultimo_pedido = Pedido.query.order_by(Pedido.id_pedido.desc()).first()
    if ultimo_pedido and ultimo_pedido.folio:
        coincidencia = re.match(r'^(.*?)(\d+)$', str(ultimo_pedido.folio).strip())
        if coincidencia:
            prefijo, consecutivo = coincidencia.groups()
            return f'{prefijo}{int(consecutivo) + 1:0{len(consecutivo)}d}'
    return ''


@dashboard_bp.route('/', methods=['GET'])
@dashboard_bp.route('', methods=['GET'])
def index():
    # Se lee antes que los pedidos: un cambio simultáneo se vuelve a enviar, nunca se pierde.
    version = version_tablero()

    ahora = datetime.now()
    pedidos_en_proceso = _filtrar_por_rol(
        con_relaciones_tarjeta(Pedido.query).filter_by(estatus=EstadoPedido.EN_PROCESO.value)
    ).order_by(Pedido.fecha_entrega.asc(), Pedido.id_pedido.asc()).all()

    columnas_produccion = None
    if _es_produccion():
        # Producción: Vencidos · Para hoy · Próximos · Listos (terminados hoy). No ve Entregado (ni su total).
        hoy = ahora.date()
        terminados_hoy = _filtrar_por_rol(
            con_relaciones_tarjeta(Pedido.query).filter(
                Pedido.estatus == EstadoPedido.TERMINADO.value,
                Pedido.fecha_terminado >= datetime.combine(hoy, datetime.min.time()),
            )
        ).order_by(Pedido.fecha_terminado.desc(), Pedido.id_pedido.desc()).all()
        por_columna = {clave: [] for clave, _ in COLUMNAS_PRODUCCION}
        for pedido in pedidos_en_proceso:
            por_columna[_columna_produccion(pedido, hoy)].append(pedido)
        por_columna['terminados-hoy'] = terminados_hoy
        columnas_produccion = [
            {
                'clave': clave, 'titulo': titulo, 'pedidos': por_columna[clave],
                'plantilla': PLANTILLA_POR_COLUMNA_PRODUCCION[clave],
            }
            for clave, titulo in COLUMNAS_PRODUCCION
        ]
        pedidos_terminados, pedidos_entregados, hay_mas_entregados, total_entregados = [], [], False, 0
        folio_sugerido = ''
    else:
        # Más días esperando = se terminó hace más tiempo (sin fecha de término: la de entrega).
        pedidos_terminados = _filtrar_por_rol(
            con_relaciones_tarjeta(Pedido.query).filter_by(estatus=EstadoPedido.TERMINADO.value)
        ).order_by(func.coalesce(Pedido.fecha_terminado, Pedido.fecha_entrega).asc(), Pedido.id_pedido.asc()).all()
        pedidos_entregados, hay_mas_entregados = _pagina_entregados(ORDENES_ENTREGADOS[0])
        total_entregados = _total_entregados()
        folio_sugerido = _folio_sugerido()

    dias = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
    meses = (
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
    )
    fecha_larga = f'{dias[ahora.weekday()]}, {ahora.day} de {meses[ahora.month - 1]} de {ahora.year}'
    fecha_corta = f'{dias[ahora.weekday()][:3].capitalize()} {ahora.day} {meses[ahora.month - 1][:3]}'
    # El monto solo viaja a la plantilla con caja.ver; Producción no lo recibe.
    por_cobrar = round(sum(p.saldo_restante for p in pedidos_terminados), 2) if tiene_permiso('caja.ver') else None

    return render_template(
        'dashboard.html',
        en_proceso=pedidos_en_proceso,
        terminados=pedidos_terminados,
        entregados=pedidos_entregados,
        total_entregados=total_entregados,
        hay_mas_entregados=hay_mas_entregados,
        folio_sugerido=folio_sugerido,
        columnas_produccion=columnas_produccion,
        fecha_larga=fecha_larga,
        fecha_corta=fecha_corta,
        por_cobrar=por_cobrar,
        hora_actual=ahora.strftime('%H:%M'),
        version_tablero=version,
        **_contexto_tablero(),
    )


@dashboard_bp.route('/entregados', methods=['GET'])
def entregados():
    """Página de Entregado en el orden elegido; sin cursor es la primera."""
    if _es_produccion():
        return jsonify(error='No disponible.'), 403
    orden = request.args.get('orden', ORDENES_ENTREGADOS[0])
    if orden not in ORDENES_ENTREGADOS:
        return jsonify(error='Orden no válido.'), 400

    despues = None
    if request.args.get('despues_orden') or request.args.get('despues_id'):
        try:
            despues = (
                datetime.fromisoformat(request.args['despues_orden']),
                int(request.args['despues_id']),
            )
        except (KeyError, ValueError):
            return jsonify(error='Cursor no válido.'), 400

    pedidos, hay_mas = _pagina_entregados(orden, despues)
    return jsonify(
        tarjetas=_renderizar_tarjetas(pedidos),
        hay_mas=hay_mas,
        total=_total_entregados(),
    )


@dashboard_bp.route('/cambios', methods=['GET'])
def cambios():
    """Tarjetas que cambiaron desde la versión `desde` (id de bitácora) que tiene el cliente."""
    desde = request.args.get('desde', type=int)
    if desde is None or desde < 0:
        return jsonify(error='Versión no válida.'), 400

    filas = (
        db.session.query(Bitacora.id_bitacora, Bitacora.id_entidad, Bitacora.accion)
        .filter(
            Bitacora.entidad == 'pedido',
            Bitacora.accion.in_(ACCIONES_TABLERO),
            Bitacora.id_entidad.isnot(None),
            or_(
                Bitacora.id_bitacora > desde,
                and_(
                    Bitacora.id_bitacora > desde - REPASO_BITACORA,
                    Bitacora.fecha >= datetime.now() - timedelta(seconds=REPASO_SEGUNDOS),
                ),
            ),
        )
        .order_by(Bitacora.id_bitacora.asc())
        .limit(MAX_FILAS_CAMBIOS + 1)
        .all()
    )
    if not filas:
        return jsonify(version=desde, tarjetas=[], quitar=[])
    if len(filas) > MAX_FILAS_CAMBIOS:
        return jsonify(recargar=True)

    ids = {fila.id_entidad for fila in filas}
    consulta = con_relaciones_tarjeta(Pedido.query).filter(Pedido.id_pedido.in_(ids))
    produccion = _es_produccion()
    if produccion:   # no ve Entregado: ni se carga; sale por `quitar`
        consulta = consulta.filter(Pedido.estatus != EstadoPedido.ENTREGADO.value)
    pedidos = _filtrar_por_rol(consulta).all()

    renderizadas = _renderizar_tarjetas(pedidos, por_fecha=produccion)
    tarjetas = [tarjeta for tarjeta in renderizadas if tarjeta['columna'] != COLUMNA_CANCELADO]
    visibles = {tarjeta['id'] for tarjeta in tarjetas}
    respuesta = {
        'version': max(desde, filas[-1].id_bitacora),
        'tarjetas': tarjetas,
        'quitar': sorted(ids - visibles),   # el tablero no muestra cancelados
        # Solo para la sección de historial, donde el pedido se queda en su lugar.
        'canceladas': [tarjeta for tarjeta in renderizadas if tarjeta['columna'] == COLUMNA_CANCELADO],
    }
    if not produccion:
        respuesta['total_entregados'] = _total_entregados()
    if tiene_permiso('caja.ver'):
        respuesta['por_cobrar'] = _por_cobrar_terminados()
    if tiene_permiso('pedidos.gestionar') and any(fila.accion == 'PEDIDO_CREADO' for fila in filas):
        respuesta['folio_sugerido'] = _folio_sugerido()
    return jsonify(respuesta)


@dashboard_bp.route('/buscar', methods=['GET'])
def buscar():
    """Busca en todos los pedidos los que el tablero no tiene cargados."""
    q = ' '.join((request.args.get('q') or '').replace('\x00', '').split())[:MAX_CARACTERES_BUSQUEDA]
    if len(q) < MIN_CARACTERES_BUSQUEDA:
        return jsonify(tarjetas=[], hay_mas=False)

    coincide, prioridad = _condiciones_busqueda(q, request.args.get('modo'))
    consulta = _filtrar_por_rol(con_relaciones_tarjeta(Pedido.query).filter(coincide))
    if _es_produccion():   # no ve Entregado
        consulta = consulta.filter(Pedido.estatus != EstadoPedido.ENTREGADO.value)
    excluir = _ids_excluidos(request.args.get('excluir'))
    if excluir:
        consulta = consulta.filter(Pedido.id_pedido.notin_(excluir))
    pedidos = (
        consulta
        .order_by(prioridad, Pedido.fecha_entrega.desc(), Pedido.id_pedido.desc())
        .limit(MAX_RESULTADOS_BUSQUEDA + 1)
        .all()
    )
    return jsonify(
        tarjetas=_renderizar_tarjetas(pedidos[:MAX_RESULTADOS_BUSQUEDA]),
        hay_mas=len(pedidos) > MAX_RESULTADOS_BUSQUEDA,
    )


@dashboard_bp.route('/pedidos/<int:pedido_id>/cambiar-estado', methods=['POST'])
@permiso_requerido('dashboard.terminar', 'dashboard.entregar')
def cambiar_estado(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    nuevo = request.form.get('nuevo_estado') or TRANSICIONES.get(pedido.estatus)
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def volver():
        if request.form.get('volver') == 'ficha':
            return redirect(url_for('pedidos.ficha', folio=pedido.folio))
        return redirect(url_for('dashboard.index'))

    def error(msg, codigo=400):
        if ajax:
            return jsonify({"success": False, "error": msg}), codigo
        flash(msg, 'danger')
        return volver()

    if TRANSICIONES.get(pedido.estatus) != nuevo:
        return error('Cambio de estado no válido.')
    if not tiene_permiso(PERMISO_POR_ESTADO[nuevo]):
        return error('No tienes permiso para este cambio.', 403)
    if solo_sus_pedidos() and not es_responsable(pedido, g.usuario):
        return error('Este pedido no está asignado a ti.', 403)
    if (nuevo == EstadoPedido.TERMINADO.value
            and pedido.tiene_asignaciones_por_producto
            and not pedido.productos_completos):
        return error('Cada responsable debe marcar como terminados sus productos antes de cerrar el pedido.')
    if nuevo == EstadoPedido.ENTREGADO.value and pedido.estado_pago != 'Pagado':
        return error('El pedido tiene saldo pendiente y no puede ser entregado.')

    pedido.estatus = nuevo
    if nuevo == EstadoPedido.TERMINADO.value:
        pedido.fecha_terminado = datetime.now()
        notificar(
            usuarios_por_rol('Recepcion', 'Administrador'), 'PEDIDO_TERMINADO',
            f'Pedido {pedido.folio} terminado', f'{pedido.nombre_cliente} · listo para entregar',
            pedido=pedido,
        )
    elif nuevo == EstadoPedido.ENTREGADO.value:
        pedido.fecha_entregado = datetime.now()
    registrar('PEDIDO_ESTADO', 'pedido', pedido.id_pedido, f'{pedido.folio}: {nuevo}')
    db.session.commit()

    imprimir_entrega = nuevo == EstadoPedido.ENTREGADO.value and tiene_permiso('pedidos.imprimir')
    if ajax:
        respuesta = {"success": True, "nuevo_estado": pedido.estatus, "pedido_id": pedido.id_pedido}
        if imprimir_entrega:
            respuesta["url_ticket_entrega"] = url_for('pedidos.ticket_entrega', id_pedido=pedido.id_pedido)
        return jsonify(respuesta)
    if imprimir_entrega:
        session['imprimir_ticket_entrega'] = pedido.id_pedido
    return volver()