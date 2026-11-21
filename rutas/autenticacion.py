from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from config import db
from modelos.bitacora import registrar
from modelos.usuario import Usuario
from servicios.permisos import es_destino_interno, invalidar_usuario

autenticacion_bp = Blueprint('autenticacion', __name__, template_folder='../templates')


def _destino_seguro(destino):
    """Solo rutas de esta app (ver es_destino_interno): evita redirigir a sitios externos tras el login."""
    return destino if es_destino_interno(destino) else url_for('dashboard.index')


@autenticacion_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('usuario_id'):
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        correo = (request.form.get('correo') or '').strip().lower()
        password = request.form.get('password') or ''
        usuario = Usuario.query.filter(db.func.lower(Usuario.correo) == correo).first()

        if usuario and usuario.activo and check_password_hash(usuario.password_hash, password):
            session.clear()
            session['usuario_id'] = usuario.id_usuario
            session.permanent = request.form.get('recordarme') == 'on'
            registrar('LOGIN', 'usuario', usuario.id_usuario, usuario=usuario)
            db.session.commit()
            # Este login lee la BD directo; la caché de requests pudo quedarse con el estado anterior
            # (p. ej. usuario reactivado con `flask crear-admin`, otro proceso): se descarta.
            invalidar_usuario(usuario.id_usuario)
            return redirect(_destino_seguro(request.args.get('next')))

        registrar('LOGIN_FALLIDO', detalle=f'Correo: {correo[:100]}')
        db.session.commit()
        flash('Correo o contraseña incorrectos.', 'danger')

    return render_template('login.html')


@autenticacion_bp.route('/logout', methods=['POST'])
def logout():
    id_usuario = session.get('usuario_id')
    registrar('LOGOUT', 'usuario', id_usuario)
    db.session.commit()
    invalidar_usuario(id_usuario)
    session.clear()
    flash('Sesión cerrada.', 'success')
    return redirect(url_for('autenticacion.login'))