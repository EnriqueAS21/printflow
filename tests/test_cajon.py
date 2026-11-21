from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401
from modelos.bitacora import Bitacora
from modelos.caja import CorteCaja, FondoCaja, MovimientoCaja
from modelos.pedido import EstadoPedido, Pago, Pedido, TASA_COMISION_TARJETA, TASA_IVA
from modelos.usuario import Usuario
from rutas.autenticacion import autenticacion_bp
from rutas.caja import caja_bp
from rutas.clientes import clientes_bp
from rutas.configuracion import configuracion_bp
from rutas.dashboard import dashboard_bp
from rutas.notificaciones import notificaciones_bp
from rutas.pedidos import pedidos_bp
from rutas.productos import productos_bp
from rutas.reportes import reportes_bp
from rutas.stickers import stickers_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios.permisos import registrar_seguridad


@pytest.fixture
def app_and_client(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY='test-secret',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.jinja_env.globals.update(
        TASA_IVA=TASA_IVA,
        TASA_COMISION_TARJETA=TASA_COMISION_TARJETA,
    )
    registrar_seguridad(app)
    for blueprint, prefix in (
        (usuarios_bp, '/usuarios'),
        (bitacora_bp, '/bitacora'),
        (pedidos_bp, '/pedidos'),
        (autenticacion_bp, '/auth'),
        (dashboard_bp, '/dashboard'),
        (caja_bp, '/caja'),
        (configuracion_bp, '/configuracion'),
        (reportes_bp, '/reportes'),
        (stickers_bp, '/stickers'),
        (notificaciones_bp, '/notificaciones'),
        (clientes_bp, '/clientes'),
        (productos_bp, '/productos'),
    ):
        app.register_blueprint(blueprint, url_prefix=prefix)

    context = app.app_context()
    context.push()
    db.create_all()
    client = app.test_client()

    monkeypatch.setattr('rutas.caja.previsualizar_siguiente_folio', lambda: 'AC0001')
    monkeypatch.setattr('rutas.pedidos.previsualizar_siguiente_folio', lambda: 'AC0001')
    monkeypatch.setattr('rutas.pedidos.generar_siguiente_folio', lambda: 'AC0001')
    monkeypatch.setattr('rutas.caja.reclamar_envio', lambda _token: True)
    monkeypatch.setattr('rutas.pedidos.reclamar_envio', lambda _token: True)

    llamadas_cajon = []
    import modelos.cajon as modulo_cajon
    monkeypatch.setattr(modulo_cajon, 'abrir_cajon', lambda: llamadas_cajon.append(True) or True)

    def crear_usuario(nombre, correo, rol):
        nuevo = Usuario(
            nombre=nombre,
            correo=correo,
            password_hash=generate_password_hash('test-password', method='pbkdf2:sha256'),
            rol=rol,
            activo=True,
        )
        db.session.add(nuevo)
        db.session.commit()
        return nuevo

    admin = crear_usuario('Admin de prueba', 'admin@example.test', 'Administrador')

    def iniciar_sesion(user):
        with client.session_transaction() as session:
            session['usuario_id'] = user.id_usuario

    iniciar_sesion(admin)
    yield {
        'app': app,
        'client': client,
        'admin': admin,
        'crear_usuario': crear_usuario,
        'iniciar_sesion': iniciar_sesion,
        'llamadas_cajon': llamadas_cajon,
        'modulo_cajon': modulo_cajon,
    }
    db.session.remove()
    db.drop_all()
    context.pop()


def datos_pedido(**extra):
    datos = {
        'nombre_cliente': 'Cliente de prueba',
        'telefono_cliente': '5550000000',
        'fecha_entrega': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'),
        'hora_entrega': '12:00',
        'cantidad[]': '1',
        'descripcion[]': 'Producto de prueba',
        'precio_unitario[]': '200',
        'anticipo': '0',
        'metodo_pago': 'Efectivo',
    }
    datos.update(extra)
    return datos


def crear_pedido(total=200, pagado=0, estatus=EstadoPedido.EN_PROCESO.value):
    nuevo = Pedido(
        folio=f'TEST-{Pedido.query.count() + 1:03d}',
        nombre_cliente='Cliente de prueba',
        telefono_cliente='5550000000',
        fecha_entrega=datetime.now() + timedelta(days=2),
        subtotal=total,
        total=total,
        estatus=estatus,
    )
    db.session.add(nuevo)
    db.session.flush()
    if pagado:
        db.session.add(Pago(
            id_pedido=nuevo.id_pedido,
            monto=pagado,
            metodo_pago='Efectivo',
            fecha_pago=datetime.now(),
        ))
    db.session.commit()
    return nuevo


def ticket_data(metodo='Efectivo'):
    return {
        'cantidad[]': '1',
        'precio_unitario[]': '25',
        'descripcion[]': 'Producto mostrador',
        'metodo_pago': metodo,
    }


@pytest.mark.parametrize(('metodo', 'esperadas'), [
    ('Efectivo', 1), ('Tarjeta', 0), ('Transferencia', 0),
])
def test_ticket_abre_cajon_solo_en_efectivo(app_and_client, metodo, esperadas):
    respuesta = app_and_client['client'].post(
        '/caja/ticket', data=ticket_data(metodo),
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    assert respuesta.status_code == 200
    assert len(app_and_client['llamadas_cajon']) == esperadas
    assert MovimientoCaja.query.filter_by(tipo='VENTA_TICKET').count() == 1


@pytest.mark.parametrize(('anticipo', 'metodo', 'esperadas'), [
    ('25', 'Efectivo', 1), ('0', 'Efectivo', 0), ('25', 'Tarjeta', 0),
])
def test_crear_pedido_abre_cajon_solo_con_anticipo_efectivo(
    app_and_client, anticipo, metodo, esperadas,
):
    respuesta = app_and_client['client'].post(
        '/pedidos/nuevo',
        data=datos_pedido(anticipo=anticipo, metodo_pago=metodo),
    )

    assert respuesta.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == esperadas
    assert Pago.query.count() == (0 if anticipo == '0' else 1)


@pytest.mark.parametrize(('anticipo_nuevo', 'esperadas'), [(20, 1), (0, 0)])
def test_editar_pedido_abre_cajon_solo_al_agregar_pago_efectivo(
    app_and_client, anticipo_nuevo, esperadas,
):
    pedido = crear_pedido()
    datos = datos_pedido(anticipo=str(anticipo_nuevo))
    datos['pedido_id'] = str(pedido.id_pedido)

    respuesta = app_and_client['client'].post('/pedidos/nuevo', data=datos)

    assert respuesta.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == esperadas
    assert Pago.query.filter_by(id_pedido=pedido.id_pedido).count() == (anticipo_nuevo > 0)


@pytest.mark.parametrize(('monto', 'esperadas', 'pagos_finales'), [(30, 1, 1), (101, 0, 0)])
def test_abono_efectivo_abre_cajon_solo_si_no_excede_saldo(
    app_and_client, monto, esperadas, pagos_finales,
):
    pedido = crear_pedido(total=100)

    respuesta = app_and_client['client'].post(
        f'/pedidos/{pedido.id_pedido}/abonar',
        data={'monto_abono': str(monto), 'metodo_pago_abono': 'Efectivo'},
    )

    assert respuesta.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == esperadas
    assert Pago.query.filter_by(id_pedido=pedido.id_pedido).count() == pagos_finales


@pytest.mark.parametrize(('metodo', 'descripcion', 'esperadas'), [
    ('Efectivo', 'Compra de material', 1),
    ('Tarjeta', 'Compra de material', 0),
    ('Efectivo', '', 0),
])
def test_egreso_abre_cajon_solo_si_es_valido_y_en_efectivo(
    app_and_client, metodo, descripcion, esperadas,
):
    respuesta = app_and_client['client'].post(
        '/caja/egreso',
        data={'costo': '15', 'descripcion': descripcion, 'metodo_pago': metodo},
    )

    assert respuesta.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == esperadas
    assert MovimientoCaja.query.filter_by(tipo='EGRESO').count() == (1 if descripcion else 0)


def test_corte_abre_cajon_al_abrir_modal_y_no_al_guardar(app_and_client):
    client = app_and_client['client']
    respuesta_abrir = client.post('/caja/corte/abrir-cajon')
    assert respuesta_abrir.status_code == 200
    assert respuesta_abrir.get_json() == {'cajon_abierto': True}
    assert len(app_and_client['llamadas_cajon']) == 1
    assert Bitacora.query.filter_by(detalle='Apertura de cajón para corte').count() == 1

    respuesta_guardar = client.post('/caja/corte', data={'retiro_efectivo': '0'})
    assert respuesta_guardar.status_code == 302
    assert CorteCaja.query.count() == 1
    assert len(app_and_client['llamadas_cajon']) == 1


def test_corte_abrir_cajon_reporta_fallo(app_and_client, monkeypatch):
    monkeypatch.setattr(app_and_client['modulo_cajon'], 'abrir_cajon', lambda: False)
    respuesta = app_and_client['client'].post('/caja/corte/abrir-cajon')
    assert respuesta.get_json() == {'cajon_abierto': False}


def test_recepcion_no_puede_abrir_cajon_para_corte(app_and_client):
    client = app_and_client['client']
    recepcion = app_and_client['crear_usuario'](
        'Recepción de prueba', 'recepcion@example.test', 'Recepcion',
    )
    app_and_client['iniciar_sesion'](recepcion)

    respuesta = client.post('/caja/corte/abrir-cajon')
    assert respuesta.status_code == 302
    assert '/dashboard' in respuesta.headers['Location']
    assert len(app_and_client['llamadas_cajon']) == 0

    respuesta_corte = client.post('/caja/corte', data={'retiro_efectivo': '0'})
    assert respuesta_corte.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == 0
    assert CorteCaja.query.count() == 0


def test_cambio_estado_y_definir_fondo_no_abren_cajon(app_and_client):
    pedido = crear_pedido()
    client = app_and_client['client']

    respuesta_estado = client.post(
        f'/dashboard/pedidos/{pedido.id_pedido}/cambiar-estado',
        data={'nuevo_estado': EstadoPedido.TERMINADO.value},
    )
    respuesta_fondo = client.post('/caja/fondo', data={'fondo_inicial': '50'})

    assert respuesta_estado.status_code == 302
    assert respuesta_fondo.status_code == 302
    assert len(app_and_client['llamadas_cajon']) == 0
    assert FondoCaja.query.count() == 1


def test_fallo_al_abrir_no_revierte_ticket_pago_ni_egreso(app_and_client, monkeypatch):
    monkeypatch.setattr(app_and_client['modulo_cajon'], 'abrir_cajon', lambda: False)
    client = app_and_client['client']

    respuesta_ticket = client.post(
        '/caja/ticket', data=ticket_data(),
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    datos_ticket = respuesta_ticket.get_json()
    assert datos_ticket['cajon_abierto'] is False
    assert MovimientoCaja.query.filter_by(tipo='VENTA_TICKET').count() == 1

    respuesta_pago = client.post(
        '/pedidos/nuevo', data=datos_pedido(anticipo='25'),
    )
    assert respuesta_pago.status_code == 302
    assert Pago.query.count() == 1

    respuesta_egreso = client.post(
        '/caja/egreso',
        data={'costo': '15', 'descripcion': 'Compra de material', 'metodo_pago': 'Efectivo'},
    )
    assert respuesta_egreso.status_code == 302
    assert MovimientoCaja.query.filter_by(tipo='EGRESO').count() == 1


def test_recepcion_no_ve_boton_de_corte_en_caja(app_and_client):
    recepcion = app_and_client['crear_usuario'](
        'Recepción de prueba', 'recepcion@example.test', 'Recepcion',
    )
    app_and_client['iniciar_sesion'](recepcion)

    respuesta = app_and_client['client'].get('/caja/')

    assert respuesta.status_code == 200
    assert b'data-bs-target="#modalCorte"' not in respuesta.data
    assert b'id="modalCorte"' not in respuesta.data


def test_abrir_cajon_sin_impresora_no_importa_win32print(app_and_client, monkeypatch):
    import builtins

    modulo_cajon = app_and_client['modulo_cajon']
    monkeypatch.delenv('CAJON_IMPRESORA', raising=False)
    monkeypatch.setattr(modulo_cajon.sys, 'platform', 'win32')
    importaciones = []
    importar_original = builtins.__import__

    def observar_importacion(nombre, *args, **kwargs):
        if nombre == 'win32print':
            importaciones.append(nombre)
        return importar_original(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', observar_importacion)
    with app_and_client['app'].app_context():
        resultado = modulo_cajon.abrir_cajon()

    assert resultado is True
    assert importaciones == []
