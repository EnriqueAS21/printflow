import os
from datetime import timedelta
from dotenv import load_dotenv

from flask import Flask, redirect, url_for
from sqlalchemy import text

from config import db
from modelos import bitacora, caja, cliente, configuracion_nota, envios, notificacion, pedido, perfil_usuario, producto, produccion, usuario  # noqa: F401  # registran las tablas
from modelos.pedido import (
    REGIMENES_FISCALES, TASA_COMISION_TARJETA, TASA_IVA, USOS_CFDI,
)
from rutas.autenticacion import autenticacion_bp
from rutas.caja import caja_bp
from rutas.clientes import clientes_bp
from rutas.configuracion import configuracion_bp
from rutas.dashboard import dashboard_bp
from rutas.notificaciones import notificaciones_bp
from rutas.pedidos import pedidos_bp
from rutas.productos import productos_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios.compresion import configurar_compresion
from servicios.permisos import registrar_seguridad
from rutas.reportes import reportes_bp
from rutas.stickers import stickers_bp
from servicios.textos import enlazar

load_dotenv()



app = Flask(__name__)

# Hilos de Waitress (servidor.py los usa) y conexiones del pool: una por hilo más un margen.
try:
    HILOS_WAITRESS = int(os.environ.get('WAITRESS_HILOS', '16'))
except ValueError as exc:
    raise RuntimeError('WAITRESS_HILOS debe ser un número entero.') from exc
if HILOS_WAITRESS <= 0:
    raise RuntimeError('WAITRESS_HILOS debe ser mayor que cero.')

secret_key = os.environ.get('SECRET_KEY')
database_url = os.environ.get('DATABASE_URL')
if not secret_key:
    raise RuntimeError('Define la variable de entorno SECRET_KEY antes de iniciar la aplicación.')
if not database_url:
    raise RuntimeError('Define la variable de entorno DATABASE_URL antes de iniciar la aplicación.')

try:
    max_content_length = int(os.environ.get('MAX_CONTENT_LENGTH', str(4 * 1024 * 1024)))
except ValueError as exc:
    raise RuntimeError('MAX_CONTENT_LENGTH debe ser un número entero de bytes.') from exc
if max_content_length <= 0:
    raise RuntimeError('MAX_CONTENT_LENGTH debe ser mayor que cero.')

app.config.update(
    SECRET_KEY=secret_key,
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    # Activa los contadores de consola del tablero (base.html); en producción queda apagado.
    JS_DEBUG=os.environ.get('PRINTFLOW_ENTORNO') == 'desarrollo',
    SQLALCHEMY_ENGINE_OPTIONS={
        'pool_size': HILOS_WAITRESS,
        'max_overflow': 4,
        'pool_pre_ping': True,
        'pool_recycle': 1800,
    },
    MAX_CONTENT_LENGTH=max_content_length,
    # Estáticos con caché de un año; url_defaults añade ?v=<mtime> para invalidarla al cambiar un archivo.
    SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=365),
    IMPRESORA_ETIQUETAS=os.environ.get('IMPRESORA_ETIQUETAS', 'Phomemo M110'),
    # Vacío: Etiquetas usa el host de la petición. Define URL_BASE para un host/IP fijo.
    URL_BASE=os.environ.get('URL_BASE', '').rstrip('/'),
    # Carpeta que vigila SignCut; vacía desactiva el envío directo.
    CARPETA_SIGNCUT=os.environ.get('CARPETA_SIGNCUT', ''),
    PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get('SESION_DIAS', '30'))),
)

load_dotenv()

_VERSIONES_ESTATICOS = {}


@app.url_defaults
def versionar_estaticos(endpoint, values):
    """Agrega ?v=<mtime> a url_for('static', ...) para que cada cambio invalide la caché."""
    if endpoint != 'static' or 'v' in values:
        return
    nombre = values.get('filename')
    if not nombre:
        return
    ruta = os.path.join(app.static_folder, nombre)
    try:
        mtime = int(os.stat(ruta).st_mtime)
    except OSError:
        return
    values['v'] = mtime


app.jinja_env.filters['enlazar'] = enlazar
app.jinja_env.globals.update(
    TASA_IVA=TASA_IVA,
    TASA_COMISION_TARJETA=TASA_COMISION_TARJETA,
    REGIMENES_FISCALES=REGIMENES_FISCALES,
    USOS_CFDI=USOS_CFDI,
)

db.init_app(app)
configurar_compresion(app)

# Exige sesión en todas las rutas y registra el comando `flask crear-admin`
registrar_seguridad(app)

# Registrar Blueprints
app.register_blueprint(usuarios_bp, url_prefix='/usuarios')
app.register_blueprint(bitacora_bp, url_prefix='/bitacora')
app.register_blueprint(pedidos_bp, url_prefix='/pedidos')
app.register_blueprint(autenticacion_bp, url_prefix='/auth')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
app.register_blueprint(caja_bp, url_prefix='/caja')
app.register_blueprint(configuracion_bp, url_prefix='/configuracion')
app.register_blueprint(reportes_bp, url_prefix='/reportes')
app.register_blueprint(stickers_bp, url_prefix='/stickers')
app.register_blueprint(notificaciones_bp, url_prefix='/notificaciones')
app.register_blueprint(clientes_bp, url_prefix='/clientes')
app.register_blueprint(productos_bp, url_prefix='/productos')

with app.app_context():
    try:
        with db.engine.connect():
            print("¡Conexión con PostgreSQL establecida exitosamente!")
        db.create_all()
        db.session.execute(text("CREATE SEQUENCE IF NOT EXISTS folio_pedido_seq START 1"))
        db.session.commit()
        print("Tablas y secuencia de folios verificadas.")
    except Exception as e:
        print(f"Error al conectar con la base de datos: {e}")


@app.route('/')
def index():
    # Si no hay sesión, la guardia de seguridad lo manda al login.
    return redirect(url_for('dashboard.index'))


if __name__ == '__main__':
    app.run(debug=True)
