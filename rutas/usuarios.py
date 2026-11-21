import re
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import db
from modelos.bitacora import Bitacora, registrar
from modelos.usuario import ROLES, Usuario
from modelos.perfil_usuario import PerfilUsuario
from servicios.permisos import invalidar_usuario, permiso_requerido

usuarios_bp = Blueprint('usuarios', __name__, template_folder='../templates')
bitacora_bp = Blueprint('bitacora', __name__, template_folder='../templates')

CORREO_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MIN_PASSWORD = 8
EXTENSIONES_FOTO = {'png', 'jpg', 'jpeg', 'webp'}
MAX_FOTO_BYTES = 3 * 1024 * 1024


def _subir_foto_perfil(archivo):
    nombre = secure_filename(archivo.filename or '')
    extension = nombre.rsplit('.', 1)[-1].lower() if '.' in nombre else ''
    if extension not in EXTENSIONES_FOTO:
        raise ValueError('La foto debe estar en formato PNG, JPG o WEBP.')

    contenido = archivo.stream.read(MAX_FOTO_BYTES + 1)
    if len(contenido) > MAX_FOTO_BYTES:
        raise ValueError('La foto no debe superar 3 MB.')
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            imagen.verify()
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.width <= 0 or imagen.height <= 0 or imagen.width * imagen.height > 25_000_000:
                raise ValueError('La resolución de la foto es demasiado grande.')
            imagen = imagen.convert('RGB')
            imagen.thumbnail((900, 900))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('El archivo seleccionado no es una imagen válida.') from exc

    directorio = Path(current_app.static_folder) / 'uploads' / 'perfiles'
    directorio.mkdir(parents=True, exist_ok=True)
    archivo_final = f'perfil-{uuid4().hex}.jpg'
    imagen.save(directorio / archivo_final, format='JPEG', quality=88, optimize=True)
    return archivo_final


# ---------------------------------------------------------------- utilidades
def _otros_admins_activos(excluir_id):
    return Usuario.query.filter(
        Usuario.rol == 'Administrador',
        Usuario.activo.is_(True),
        Usuario.id_usuario != excluir_id,
    ).count()


def _leer_formulario(form, usuario=None):
    nombre = (form.get('nombre') or '').strip()
    correo = (form.get('correo') or '').strip().lower()
    telefono = (form.get('telefono') or '').strip() or None
    rol = form.get('rol', '')
    password = form.get('password') or ''

    if not nombre:
        raise ValueError('El nombre es obligatorio.')
    if not CORREO_RE.match(correo):
        raise ValueError('El correo no es válido.')
    if rol not in ROLES:
        raise ValueError('Rol no válido.')

    repetido = Usuario.query.filter(db.func.lower(Usuario.correo) == correo)
    if usuario is not None:
        repetido = repetido.filter(Usuario.id_usuario != usuario.id_usuario)
    if repetido.first():
        raise ValueError('Ya existe un usuario con ese correo.')

    if usuario is None and not password:
        raise ValueError('La contraseña es obligatoria para un usuario nuevo.')
    if password and len(password) < MIN_PASSWORD:
        raise ValueError(f'La contraseña debe tener al menos {MIN_PASSWORD} caracteres.')

    if (
        usuario is not None and usuario.rol == 'Administrador' and rol != 'Administrador'
        and _otros_admins_activos(usuario.id_usuario) == 0
    ):
        raise ValueError('Debe quedar al menos un Administrador activo.')

    return {'nombre': nombre, 'correo': correo, 'telefono': telefono, 'rol': rol, 'password': password}


def _formulario(usuario):
    valores = request.form if request.method == 'POST' else {
        'nombre': usuario.nombre, 'correo': usuario.correo,
        'telefono': usuario.telefono or '', 'rol': usuario.rol,
    } if usuario else {}

    if request.method == 'POST':
        try:
            datos = _leer_formulario(request.form, usuario)
            if usuario is None:
                usuario = Usuario(
                    nombre=datos['nombre'], correo=datos['correo'], telefono=datos['telefono'],
                    rol=datos['rol'], password_hash=generate_password_hash(datos['password'], method='pbkdf2:sha256'),
                )
                db.session.add(usuario)
                db.session.flush()
                registrar('USUARIO_CREADO', 'usuario', usuario.id_usuario,
                          f"{usuario.correo} · rol {usuario.rol}")
            else:
                cambios = []
                if usuario.rol != datos['rol']:
                    cambios.append(f"rol {usuario.rol} → {datos['rol']}")
                if usuario.correo != datos['correo']:
                    cambios.append(f"correo {usuario.correo} → {datos['correo']}")
                if usuario.nombre != datos['nombre']:
                    cambios.append('nombre')
                if (usuario.telefono or None) != datos['telefono']:
                    cambios.append('teléfono')
                usuario.nombre, usuario.correo = datos['nombre'], datos['correo']
                usuario.telefono, usuario.rol = datos['telefono'], datos['rol']
                if datos['password']:
                    usuario.password_hash = generate_password_hash(datos['password'], method='pbkdf2:sha256')
                    cambios.append('contraseña restablecida')
                registrar('USUARIO_EDITADO', 'usuario', usuario.id_usuario,
                          f"{usuario.correo}: " + (', '.join(cambios) or 'sin cambios'))
            db.session.commit()
            invalidar_usuario(usuario.id_usuario)
            flash(f'Usuario {usuario.nombre} guardado.', 'success')
            return redirect(url_for('usuarios.lista'))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'warning')
        except IntegrityError:
            db.session.rollback()
            flash('Ya existe un usuario con ese correo.', 'warning')

    return render_template('usuarios/form.html', usuario=usuario, valores=valores, roles=ROLES)


# ------------------------------------------------------------------- usuarios
@usuarios_bp.route('/', methods=['GET'])
@permiso_requerido('usuarios.administrar')
def lista():
    usuarios = Usuario.query.order_by(Usuario.activo.desc(), Usuario.nombre.asc()).all()
    return render_template('usuarios/lista.html', usuarios=usuarios)


@usuarios_bp.route('/perfil', methods=['GET', 'POST'])
def perfil():
    """Permite a cada usuario modificar solo su teléfono, contraseña y foto."""
    usuario = db.session.get(Usuario, g.usuario.id_usuario)  # g.usuario es una copia ligera
    perfil_usuario = db.session.get(PerfilUsuario, usuario.id_usuario)
    if perfil_usuario is None:
        perfil_usuario = PerfilUsuario(id_usuario=usuario.id_usuario)
        db.session.add(perfil_usuario)
        db.session.commit()

    if request.method == 'POST':
        foto_nueva = None
        archivo_nuevo = None
        try:
            telefono = (request.form.get('telefono') or '').strip() or None
            password_actual = request.form.get('password_actual') or ''
            password_nueva = request.form.get('password_nueva') or ''
            confirmar_password = request.form.get('confirmar_password') or ''

            if telefono and len(telefono) > 20:
                raise ValueError('El número de teléfono no debe superar 20 caracteres.')
            if password_nueva:
                if not password_actual or not check_password_hash(usuario.password_hash, password_actual):
                    raise ValueError('La contraseña actual no es correcta.')
                if len(password_nueva) < MIN_PASSWORD:
                    raise ValueError(f'La nueva contraseña debe tener al menos {MIN_PASSWORD} caracteres.')
                if password_nueva != confirmar_password:
                    raise ValueError('La confirmación de contraseña no coincide.')

            archivo = request.files.get('foto_perfil')
            if archivo and archivo.filename:
                foto_nueva = _subir_foto_perfil(archivo)
                archivo_nuevo = Path(current_app.static_folder) / 'uploads' / 'perfiles' / foto_nueva

            foto_anterior = perfil_usuario.foto_archivo
            usuario.telefono = telefono
            if password_nueva:
                usuario.password_hash = generate_password_hash(password_nueva, method='pbkdf2:sha256')
            if foto_nueva:
                perfil_usuario.foto_archivo = foto_nueva

            registrar(
                'PERFIL_EDITADO', 'usuario', usuario.id_usuario,
                'Teléfono actualizado'
                + (' · contraseña cambiada' if password_nueva else '')
                + (' · foto actualizada' if foto_nueva else ''),
            )
            db.session.commit()
            invalidar_usuario(usuario.id_usuario)
            if foto_nueva and foto_anterior:
                nombre_anterior = secure_filename(foto_anterior)
                if nombre_anterior:
                    (Path(current_app.static_folder) / 'uploads' / 'perfiles' / nombre_anterior).unlink(missing_ok=True)
            flash('Tu perfil se actualizó correctamente.', 'success')
            return redirect(url_for('usuarios.perfil'))
        except ValueError as exc:
            db.session.rollback()
            if archivo_nuevo:
                archivo_nuevo.unlink(missing_ok=True)
            flash(str(exc), 'warning')
        except Exception:
            db.session.rollback()
            if archivo_nuevo:
                archivo_nuevo.unlink(missing_ok=True)
            current_app.logger.exception('Error al actualizar perfil')
            flash('No se pudo actualizar el perfil. Intenta de nuevo.', 'danger')

        usuario = db.session.get(Usuario, g.usuario.id_usuario)
        perfil_usuario = db.session.get(PerfilUsuario, usuario.id_usuario)

    return render_template('usuarios/perfil.html', usuario=usuario, perfil_usuario=perfil_usuario)


@usuarios_bp.route('/nuevo', methods=['GET', 'POST'])
@permiso_requerido('usuarios.administrar')
def nuevo():
    return _formulario(None)


@usuarios_bp.route('/<int:id_usuario>/editar', methods=['GET', 'POST'])
@permiso_requerido('usuarios.administrar')
def editar(id_usuario):
    return _formulario(Usuario.query.get_or_404(id_usuario))


@usuarios_bp.route('/<int:id_usuario>/estado', methods=['POST'])
@permiso_requerido('usuarios.administrar')
def cambiar_estado(id_usuario):
    usuario = Usuario.query.get_or_404(id_usuario)
    if usuario.id_usuario == g.usuario.id_usuario:
        flash('No puedes desactivar tu propia cuenta.', 'warning')
    elif usuario.activo and usuario.rol == 'Administrador' and _otros_admins_activos(usuario.id_usuario) == 0:
        flash('Debe quedar al menos un Administrador activo.', 'warning')
    else:
        usuario.activo = not usuario.activo
        registrar('USUARIO_ACTIVADO' if usuario.activo else 'USUARIO_DESACTIVADO',
                  'usuario', usuario.id_usuario, usuario.correo)
        db.session.commit()
        invalidar_usuario(usuario.id_usuario)
        flash(f"{usuario.nombre} {'activado' if usuario.activo else 'desactivado'}.", 'success')
    return redirect(url_for('usuarios.lista'))


# ------------------------------------------------------------------- bitácora
@bitacora_bp.route('/', methods=['GET'])
@permiso_requerido('bitacora.ver')
def index():
    filtros = {
        'usuario': request.args.get('usuario', type=int),
        'accion': request.args.get('accion') or None,
        'desde': request.args.get('desde') or None,
        'hasta': request.args.get('hasta') or None,
    }
    q = Bitacora.query
    if filtros['usuario']:
        q = q.filter(Bitacora.id_usuario == filtros['usuario'])
    if filtros['accion']:
        q = q.filter(Bitacora.accion == filtros['accion'])
    try:
        if filtros['desde']:
            q = q.filter(Bitacora.fecha >= datetime.strptime(filtros['desde'], '%Y-%m-%d'))
        if filtros['hasta']:
            q = q.filter(Bitacora.fecha < datetime.strptime(filtros['hasta'], '%Y-%m-%d') + timedelta(days=1))
    except ValueError:
        flash('Fecha de filtro no válida.', 'warning')

    pagina = request.args.get('pagina', 1, type=int)
    paginacion = q.order_by(Bitacora.fecha.desc(), Bitacora.id_bitacora.desc()).paginate(
        page=pagina, per_page=50, error_out=False,
    )
    acciones = [a for (a,) in db.session.query(Bitacora.accion).distinct().order_by(Bitacora.accion)]
    return render_template(
        'bitacora.html', paginacion=paginacion, acciones=acciones,
        usuarios=Usuario.query.order_by(Usuario.nombre).all(),
        filtros={k: v for k, v in filtros.items() if v},
    )