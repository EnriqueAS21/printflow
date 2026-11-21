"""Línea base de rendimiento: consultas SQL y milisegundos por petición.

SOLO DESARROLLO. No es una migración (no va en MIGRACIONES.txt) ni se despliega.

Uso (desde la raíz del proyecto):
    .venv/bin/python scripts/medir_consultas.py \
        --admin ID_ADMINISTRADOR --produccion ID_PRODUCCION --pedido ID_PEDIDO

Usa app.test_client() contra la BD configurada en DATABASE_URL (debe ser local).
Toda escritura ocurre dentro de una transacción externa que se revierte al final:
los commit() de la app se convierten en SAVEPOINT y nada llega a la BD.
Las secuencias de PostgreSQL (ids de bitácora/notificaciones) sí avanzan; es normal.
"""
import argparse
import os
import statistics
import sys
import time
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from sqlalchemy import event, text  # noqa: E402

import flask_sqlalchemy.session as fsa_session  # noqa: E402

HOSTS_LOCALES = {'localhost', '127.0.0.1', '::1', ''}
PREFIJOS_SAVEPOINT = ('SAVEPOINT', 'RELEASE SAVEPOINT', 'ROLLBACK TO SAVEPOINT')
AJAX = {'X-Requested-With': 'XMLHttpRequest'}


def leer_argumentos():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--admin', type=int, required=True, help='id_usuario de un Administrador')
    p.add_argument('--produccion', type=int, required=True, help='id_usuario de un usuario de Producción')
    p.add_argument('--pedido', type=int, required=True,
                   help='id_pedido En proceso con al menos un producto asignado sin terminar')
    p.add_argument('--repeticiones', type=int, default=3,
                   help='repeticiones de cada GET; se reporta la mediana de ms (default 3)')
    p.add_argument('--permitir-remota', action='store_true',
                   help='permite una BD que no sea localhost (no recomendado)')
    return p.parse_args()


class Contador:
    """Cuenta consultas y suma ms de SQL de la petición en curso."""

    def __init__(self, engine):
        self.consultas = 0
        self.ms_sql = 0.0
        self._inicio = None
        event.listen(engine, 'before_cursor_execute', self._antes)
        event.listen(engine, 'after_cursor_execute', self._despues)

    def reiniciar(self):
        self.consultas = 0
        self.ms_sql = 0.0

    def _antes(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(PREFIJOS_SAVEPOINT):
            context._pf_ignorar = True
            return
        context._pf_ignorar = False
        context._pf_inicio = time.perf_counter()

    def _despues(self, conn, cursor, statement, parameters, context, executemany):
        if getattr(context, '_pf_ignorar', False):
            return
        self.consultas += 1
        self.ms_sql += (time.perf_counter() - context._pf_inicio) * 1000


def instantanea(engine, tablas):
    """Estado confirmado en la BD, leído por una conexión aparte."""
    with engine.connect() as conn:
        return {nombre: conn.execute(text(sql)).scalar() for nombre, sql in tablas.items()}


def main():
    args = leer_argumentos()

    from app import app  # importa la app real; crea tablas solo si faltan (idempotente)
    from config import db

    host = urlparse(app.config['SQLALCHEMY_DATABASE_URI']).hostname or ''
    if host not in HOSTS_LOCALES and not args.permitir_remota:
        sys.exit(f'DATABASE_URL apunta a "{host}", que no es local. Usa --permitir-remota si es intencional.')

    from modelos.bitacora import Bitacora
    from modelos.notificacion import Notificacion
    from modelos.pedido import DetalleAsignacion, EstadoPedido, Pedido
    from modelos.usuario import Usuario
    from rutas.notificaciones import version_tablero

    with app.app_context():
        engine = db.engine
        for rol, uid, esperado in (('Administrador', args.admin, 'Administrador'),
                                   ('Producción', args.produccion, 'Produccion')):
            usuario = db.session.get(Usuario, uid)
            if usuario is None or usuario.rol != esperado or not usuario.activo:
                sys.exit(f'El usuario {uid} no es un {esperado} activo.')
        pedido = db.session.get(Pedido, args.pedido)
        if pedido is None or pedido.estatus != EstadoPedido.EN_PROCESO.value:
            sys.exit(f'El pedido {args.pedido} no existe o no está En proceso.')
        detalle = next((d for d in pedido.detalles
                        if d.asignacion is not None and not d.asignacion.completado), None)
        if detalle is None:
            sys.exit(f'El pedido {args.pedido} no tiene productos asignados pendientes de terminar.')
        id_detalle = detalle.id_detalle
        db.session.remove()

    tablas_vigiladas = {
        'bitacora': f'SELECT count(*) FROM {Bitacora.__tablename__}',
        'notificaciones': f'SELECT count(*) FROM {Notificacion.__tablename__}',
        'asignaciones_completadas':
            f'SELECT count(*) FROM {DetalleAsignacion.__tablename__} WHERE completado',
        'pedidos_no_en_proceso':
            f"SELECT count(*) FROM {Pedido.__tablename__} WHERE estatus <> 'En proceso'",
    }
    antes = instantanea(engine, tablas_vigiladas)

    contador = Contador(engine)

    # Todo el trabajo de la app comparte una conexión con transacción externa; los commit() de
    # la app liberan un SAVEPOINT y al final se hace rollback de la externa.
    conexion = engine.connect()
    externa = conexion.begin()
    db.session.session_factory.configure(join_transaction_mode='create_savepoint')
    fsa_session.Session.get_bind = lambda self, *a, **k: conexion

    clientes = {}
    for rol, uid in (('Administrador', args.admin), ('Producción', args.produccion)):
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion['usuario_id'] = uid
        clientes[rol] = cliente

    filas = []

    def medir(accion, rol, metodo, url, repeticiones=1, calentar=False, **kw):
        cliente = clientes[rol]
        llamar = getattr(cliente, metodo)
        if calentar:
            llamar(url, **kw)  # descarta plantillas/caches en frío
        tiempos = []
        for _ in range(repeticiones):
            contador.reiniciar()
            inicio = time.perf_counter()
            respuesta = llamar(url, **kw)
            tiempos.append((time.perf_counter() - inicio) * 1000)
        filas.append((accion, rol, contador.consultas, statistics.median(tiempos),
                      contador.ms_sql, respuesta.status_code))
        return respuesta

    try:
        n = args.repeticiones
        with app.app_context():
            v0 = version_tablero()
            db.session.remove()

        for rol in clientes:
            medir('GET /dashboard', rol, 'get', '/dashboard', n, calentar=True)
        for rol in clientes:
            medir('GET /dashboard/cambios (sin cambios)', rol, 'get',
                  f'/dashboard/cambios?desde={v0}', n, calentar=True, headers=AJAX)

        medir('POST terminar_producto', 'Administrador', 'post',
              f'/pedidos/{args.pedido}/productos/{id_detalle}/terminar')

        for rol in clientes:
            medir('GET /dashboard/cambios (tras un cambio)', rol, 'get',
                  f'/dashboard/cambios?desde={v0}', n, headers=AJAX)
        for rol in clientes:
            medir('GET /notificaciones/resumen', rol, 'get', '/notificaciones/resumen',
                  n, calentar=True, headers=AJAX)
    finally:
        externa.rollback()
        conexion.close()

    despues = instantanea(engine, tablas_vigiladas)

    ancho = max(len(f[0]) for f in filas)
    print()
    print(f'{"acción":<{ancho}} | {"rol":<13} | {"consultas":>9} | {"ms":>8} | {"ms SQL":>8} | http')
    print('-' * (ancho + 56))
    for accion, rol, consultas, ms, ms_sql, status in filas:
        print(f'{accion:<{ancho}} | {rol:<13} | {consultas:>9} | {ms:>8.1f} | {ms_sql:>8.1f} | {status}')
    print(f'\nms = mediana de {args.repeticiones} peticiones de GET (el POST se mide una vez); '
          'consultas excluye SAVEPOINT/RELEASE.')

    if antes == despues:
        print('Verificación: la BD quedó igual (bitácora, notificaciones, asignaciones, pedidos).')
    else:
        print(f'¡ATENCIÓN! La BD cambió.\n  antes:   {antes}\n  después: {despues}')
        sys.exit(1)


if __name__ == '__main__':
    main()
