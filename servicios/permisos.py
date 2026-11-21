"""Roles, matriz de permisos y control de acceso.

Para cambiar quién puede qué, edita SOLO PERMISOS_POR_ROL.
"""
import os
import threading
import time
from functools import wraps
from typing import NamedTuple

import click
from flask import abort, flash, g, jsonify, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from config import db
from modelos.bitacora import registrar
from modelos.usuario import Usuario
from modelos.envios import nuevo_token
from modelos.perfil_usuario import PerfilUsuario

TODOS = '*'

PERMISOS_POR_ROL = {
    'Administrador': {TODOS},
    'Recepcion': {
        'dashboard.ver', 'dashboard.entregar',
        'pedidos.gestionar', 'pedidos.cancelar',  'pedidos.imprimir',           # crear, editar y abonar pedidos
        'caja.ver', 'caja.operar',      # tickets y egresos
        'clientes.ver', 'clientes.crear',   # buscar y dar de alta clientes
        'productos.ver', 'productos.usar',   # catálogo y buscador en pedidos
        'stickers.usar',
        'etiquetas.imprimir',
    },
    'Produccion': {
        'dashboard.ver', 'dashboard.terminar',   # solo sus pedidos asignados
        'stickers.usar',
        'etiquetas.imprimir',
    },
    'Facturacion': {
        'dashboard.ver', 'caja.ver',
        'etiquetas.imprimir',
    },
}
# Solo Administrador: usuarios.administrar, bitacora.ver, caja.corte, caja.fondo,
# productos.administrar,
# clientes.editar, clientes.estadisticas

ENDPOINTS_PUBLICOS = {'autenticacion.login', 'static'}


def leer_modulos_desactivados(valor):
    """Convierte "Productos, clientes" en {'productos', 'clientes'}."""
    return {nombre.strip().lower() for nombre in (valor or '').split(',') if nombre.strip()}


# config.py ya cargó el .env al importarse `db`.
MODULOS_DESACTIVADOS = leer_modulos_desactivados(os.environ.get('MODULOS_DESACTIVADOS'))


def modulo_activo(nombre):
    return (nombre or '').strip().lower() not in MODULOS_DESACTIVADOS


# --------------------------------------------------- caché de usuario por request
class UsuarioActual(NamedTuple):
    """Copia ligera e inmutable de lo que cada request necesita del usuario."""
    id_usuario: int
    nombre: str
    rol: str
    activo: bool
    foto: str | None


# Tope de desfase ante cambios hechos fuera de este proceso (p. ej. `flask crear-admin`).
# Los cambios hechos desde rutas/usuarios.py invalidan al instante con invalidar_usuario().
USUARIO_TTL_SEG = 60

_cache_usuarios = {}          # id_usuario -> (expira_monotonic, UsuarioActual)
_cache_generacion = 0         # sube con cada invalidación; evita guardar lecturas viejas
_cache_lock = threading.Lock()


def invalidar_usuario(id_usuario=None):
    """Olvida al usuario (o a todos). Llamar DESPUÉS del commit que lo cambia:
    así ningún request puede recargar el valor anterior y dejarlo en caché."""
    global _cache_generacion
    with _cache_lock:
        _cache_generacion += 1
        if id_usuario is None:
            _cache_usuarios.clear()
        else:
            _cache_usuarios.pop(id_usuario, None)


def cargar_usuario_actual(id_usuario):
    """UsuarioActual desde caché (TTL) o con UNA consulta (usuario + foto). None si no existe."""
    ahora = time.monotonic()
    with _cache_lock:
        hit = _cache_usuarios.get(id_usuario)
        if hit and hit[0] > ahora:
            return hit[1]
        generacion = _cache_generacion
    fila = (
        db.session.query(
            Usuario.id_usuario, Usuario.nombre, Usuario.rol, Usuario.activo, PerfilUsuario.foto_archivo,
        )
        .outerjoin(PerfilUsuario, PerfilUsuario.id_usuario == Usuario.id_usuario)
        .filter(Usuario.id_usuario == id_usuario)
        .first()
    )
    if fila is None:
        return None
    usuario = UsuarioActual(*fila)
    with _cache_lock:
        # Si hubo una invalidación mientras consultábamos, la lectura pudo quedar vieja: no se guarda.
        if generacion == _cache_generacion:
            _cache_usuarios[id_usuario] = (time.monotonic() + USUARIO_TTL_SEG, usuario)
    return usuario


def tiene_permiso(permiso, usuario=None):
    # Un módulo apagado gana sobre cualquier rol, incluido Administrador.
    if '.' in permiso and not modulo_activo(permiso.split('.', 1)[0]):
        return False
    usuario = usuario or g.get('usuario')
    if usuario is None or not usuario.activo:
        return False
    permisos = PERMISOS_POR_ROL.get(usuario.rol, set())
    return TODOS in permisos or permiso in permisos


def solo_sus_pedidos(usuario=None):
    """¿Ve y opera únicamente los pedidos donde es responsable?

    Hoy lo identifica 'dashboard.terminar' sin 'dashboard.entregar' (solo Producción).
    Si se crea un permiso propio, cambiar SOLO esta función.
    """
    return tiene_permiso('dashboard.terminar', usuario) and not tiene_permiso('dashboard.entregar', usuario)


def es_responsable(pedido, usuario):
    return (
        pedido.id_usuario_produccion == usuario.id_usuario
        or any(u.id_usuario == usuario.id_usuario for u in pedido.responsables)
    )


def denegar(mensaje='No tienes permiso para realizar esta acción.', estricto=False):
    """AJAX: 403 con JSON. Navegador: aviso y vuelta al tablero; con estricto=True, 403 real."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': False, 'error': mensaje}), 403
    if estricto:
        abort(403)
    flash(mensaje, 'danger')
    if request.endpoint == 'dashboard.index':
        abort(403)
    return redirect(url_for('dashboard.index'))


def permiso_requerido(*permisos, estricto=False):
    """Pasa si el usuario tiene AL MENOS uno de los permisos.
    estricto=True: sin permiso responde 403 (página o JSON), en vez de redirigir al tablero."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not any(tiene_permiso(p) for p in permisos):
                registrar('ACCESO_DENEGADO', detalle=f'{request.method} {request.path}')
                db.session.commit()
                return denegar(estricto=estricto)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def registrar_seguridad(app):
    """Llamar una vez al crear la app: exige sesión en TODAS las rutas
    (menos login y static), carga g.usuario y expone `puede()` a las plantillas."""

    # Va antes de exigir sesión: un módulo apagado no existe ni para visitantes.
    invalidar_usuario()  # app nueva (p. ej. en tests) = caché limpia

    @app.before_request
    def _bloquear_modulo_desactivado():
        if request.blueprint is None or modulo_activo(request.blueprint):
            return None
        pide_json = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.is_json
            or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
        )
        if pide_json:
            return jsonify({'error': 'Módulo no disponible'}), 404
        abort(404)

    @app.before_request
    def _exigir_sesion():
        g.usuario = None
        if request.endpoint in ENDPOINTS_PUBLICOS or request.endpoint is None:
            return None
        uid = session.get('usuario_id')
        usuario = cargar_usuario_actual(uid) if uid else None
        if usuario is None or not usuario.activo:
            session.clear()
            flash('Inicia sesión para continuar.', 'warning')
            siguiente = request.path if request.method == 'GET' else None
            return redirect(url_for('autenticacion.login', next=siguiente))
        g.usuario = usuario
        session['usuario_rol'] = usuario.rol  # compatibilidad con código anterior
        return None

    @app.context_processor
    def _contexto():
        usuario = g.get('usuario')
        return {
            'usuario_actual': usuario,
            'foto_perfil_actual': getattr(usuario, 'foto', None),
            'puede': tiene_permiso,
            'solo_sus_pedidos': solo_sus_pedidos,
            'modulo_activo': modulo_activo,
            'token_envio': nuevo_token,
        }
    
    @app.cli.command('crear-admin')
    @click.option('--nombre', prompt=True)
    @click.option('--correo', prompt=True)
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    def crear_admin(nombre, correo, password):
        """Crea (o restablece) un usuario Administrador."""
        correo = correo.strip().lower()
        usuario = Usuario.query.filter(db.func.lower(Usuario.correo) == correo).first()
        if usuario is None:
            usuario = Usuario(nombre=nombre.strip(), correo=correo, rol='Administrador')
            db.session.add(usuario)
        usuario.rol = 'Administrador'
        usuario.activo = True
        usuario.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        db.session.commit()
        click.echo(f'Administrador listo: {correo}')