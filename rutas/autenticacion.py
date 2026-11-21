from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from config import db
from modelos.bitacora import registrar
from modelos.usuario import Usuario

autenticacion_bp = Blueprint('autenticacion', __name__, template_folder='../templates')


def _destino_seguro(destino):
    """Solo rutas internas: evita redirigir a sitios externos tras el login."""
    if destino and destino.startswith('/') and not destino.startswith('//'):
        return destino
    return url_for('dashboard.index')


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
            session['usuario_rol'] = usuario.rol
            session.permanent = request.form.get('recordarme') == 'on'
            registrar('LOGIN', 'usuario', usuario.id_usuario, usuario=usuario)
            db.session.commit()
            return redirect(_destino_seguro(request.args.get('next')))

        registrar('LOGIN_FALLIDO', detalle=f'Correo: {correo[:100]}')
        db.session.commit()
        flash('Correo o contraseña incorrectos.', 'danger')

    return render_template('login.html')


@autenticacion_bp.route('/logout', methods=['POST'])
def logout():
    registrar('LOGOUT', 'usuario', session.get('usuario_id'))
    db.session.commit()
    session.clear()
    flash('Sesión cerrada.', 'success')
    return redirect(url_for('autenticacion.login'))