from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

from config import db
from modelos.bitacora import Bitacora
from modelos.notificacion import Notificacion, icono_notificacion

notificaciones_bp = Blueprint('notificaciones', __name__, template_folder='../templates')

MAX_RECIENTES = 6
POR_PAGINA = 30
ACCIONES_TABLERO = (
    'PEDIDO_CREADO', 'PEDIDO_EDITADO', 'PEDIDO_ESTADO',
    'PEDIDO_ABONO', 'PEDIDO_CANCELADO', 'PRODUCTO_TERMINADO',
)


# ------------------------------------------------------------ utilidades
def _propias():
    """Consulta base: SIEMPRE limitada al usuario de la sesión."""
    return Notificacion.query.filter(Notificacion.id_usuario == g.usuario.id_usuario)


def _hace(fecha):
    """Texto corto en español: 'ahora', 'hace 3 min', 'hace 2 h', 'ayer' o dd/mm."""
    ahora = datetime.now()
    segundos = (ahora - fecha).total_seconds()
    if segundos < 60:
        return 'ahora'
    if segundos < 3600:
        return f'hace {int(segundos // 60)} min'
    if fecha.date() == ahora.date():
        return f'hace {int(segundos // 3600)} h'
    if fecha.date() == ahora.date() - timedelta(days=1):
        return 'ayer'
    return fecha.strftime('%d/%m')


def version_tablero():
    """Último id de bitácora que afecta al tablero de pedidos (0 si no hay)."""
    return (
        db.session.query(func.max(Bitacora.id_bitacora))
        .filter(Bitacora.entidad == 'pedido', Bitacora.accion.in_(ACCIONES_TABLERO))
        .scalar()
    ) or 0


def _es_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


# ------------------------------------------------------------ rutas
@notificaciones_bp.route('/resumen')
def resumen():
    no_leidas = _propias().filter(Notificacion.leida.is_(False)).count()
    recientes = (
        _propias()
        .order_by(Notificacion.fecha.desc(), Notificacion.id_notificacion.desc())
        .limit(MAX_RECIENTES)
        .all()
    )
    recientes_json = []
    for n in recientes:
        icono, color = icono_notificacion(n.tipo)
        recientes_json.append({
            'id': n.id_notificacion,
            'tipo': n.tipo,
            'icono': icono,
            'color': color,
            'titulo': n.titulo,
            'mensaje': n.mensaje,
            'folio': n.folio,
            'leida': n.leida,
            'hace': _hace(n.fecha),
        })
    return jsonify(no_leidas=no_leidas, recientes=recientes_json, version_tablero=version_tablero())


@notificaciones_bp.route('/')
def lista():
    solo_no_leidas = request.args.get('solo') == 'no_leidas'
    q = _propias()
    if solo_no_leidas:
        q = q.filter(Notificacion.leida.is_(False))

    pagina = request.args.get('pagina', 1, type=int)
    paginacion = q.order_by(Notificacion.fecha.desc(), Notificacion.id_notificacion.desc()).paginate(
        page=pagina, per_page=POR_PAGINA, error_out=False,
    )
    return render_template(
        'notificaciones/lista.html', paginacion=paginacion,
        solo_no_leidas=solo_no_leidas, hace=_hace, icono_notificacion=icono_notificacion,
        filtros={'solo': 'no_leidas'} if solo_no_leidas else {},
    )


@notificaciones_bp.route('/<int:id>/abrir', methods=['POST'])
def abrir(id):
    notificacion = _propias().filter(Notificacion.id_notificacion == id).first_or_404()
    if not notificacion.leida:
        notificacion.leida = True
        db.session.commit()
    if notificacion.folio:
        return redirect(url_for('pedidos.ficha', folio=notificacion.folio))
    return redirect(url_for('notificaciones.lista'))


@notificaciones_bp.route('/leer-todas', methods=['POST'])
def leer_todas():
    _propias().filter(Notificacion.leida.is_(False)).update(
        {Notificacion.leida: True}, synchronize_session=False,
    )
    db.session.commit()
    if _es_ajax():
        return jsonify(success=True)
    return redirect(url_for('notificaciones.lista'))
