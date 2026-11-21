import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash
from werkzeug.datastructures import MultiDict

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401  # registran las tablas
from modelos.caja import CorteCaja, MovimientoCaja
from modelos.pedido import Pago, Pedido, TASA_COMISION_TARJETA, TASA_IVA
from modelos.usuario import Usuario
from rutas.autenticacion import autenticacion_bp
from rutas.caja import caja_bp
from rutas.clientes import clientes_bp
from rutas.configuracion import configuracion_bp
from rutas.dashboard import dashboard_bp
from rutas.notificaciones import notificaciones_bp
from rutas.pedidos import (
    _actualizar_pedido, _leer_datos_formulario, _texto_cobro_terminal, pedidos_bp,
)
from rutas.productos import productos_bp
from rutas.reportes import reportes_bp
from rutas.stickers import stickers_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios.permisos import registrar_seguridad
from servicios.generador_reportes import reporte_cartera, reporte_ventas


class CajaRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.jinja_env.globals.update(
            TASA_IVA=TASA_IVA,
            TASA_COMISION_TARJETA=TASA_COMISION_TARJETA,
        )
        registrar_seguridad(self.app)
        self.app.register_blueprint(usuarios_bp, url_prefix="/usuarios")
        self.app.register_blueprint(bitacora_bp, url_prefix="/bitacora")
        self.app.register_blueprint(pedidos_bp, url_prefix="/pedidos")
        self.app.register_blueprint(autenticacion_bp, url_prefix="/auth")
        self.app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
        self.app.register_blueprint(caja_bp, url_prefix="/caja")
        self.app.register_blueprint(configuracion_bp, url_prefix="/configuracion")
        self.app.register_blueprint(reportes_bp, url_prefix="/reportes")
        self.app.register_blueprint(stickers_bp, url_prefix="/stickers")
        self.app.register_blueprint(notificaciones_bp, url_prefix="/notificaciones")
        self.app.register_blueprint(clientes_bp, url_prefix="/clientes")
        self.app.register_blueprint(productos_bp, url_prefix="/productos")
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # folio_pedido_seq es una secuencia de PostgreSQL; SQLite no la tiene.
        folio_patcher = patch("rutas.caja.previsualizar_siguiente_folio", return_value="AC0001")
        folio_patcher.start()
        self.addCleanup(folio_patcher.stop)
        siguiente_pedido_patcher = patch(
            "rutas.pedidos.previsualizar_siguiente_folio", return_value="AC0001"
        )
        siguiente_pedido_patcher.start()
        self.addCleanup(siguiente_pedido_patcher.stop)

        self.admin = self._crear_usuario("Admin de prueba", "admin@example.test", "Administrador")
        self._iniciar_sesion(self.admin)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _crear_usuario(self, nombre, correo, rol):
        usuario = Usuario(
            nombre=nombre,
            correo=correo,
            password_hash=generate_password_hash("test-password", method="pbkdf2:sha256"),
            rol=rol,
            activo=True,
        )
        db.session.add(usuario)
        db.session.commit()
        return usuario

    def _iniciar_sesion(self, usuario):
        with self.client.session_transaction() as session:
            session["usuario_id"] = usuario.id_usuario

    def _cerrar_sesion(self):
        with self.client.session_transaction() as session:
            session.clear()

    def _create_daily_transactions(self):
        now = datetime.now()
        pedido = Pedido(
            folio="TEST-001",
            nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000",
            fecha_entrega=now,
            total=500,
            estatus="En proceso",
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add_all([
            Pago(id_pedido=pedido.id_pedido, monto=100, metodo_pago="Efectivo", fecha_pago=now),
            Pago(id_pedido=pedido.id_pedido, monto=200, metodo_pago="Tarjeta", fecha_pago=now),
            MovimientoCaja(
                tipo="EGRESO",
                cantidad=1,
                descripcion="Compra de material",
                monto=20,
                metodo_pago="Efectivo",
                fecha=now,
            ),
        ])
        db.session.commit()

    def test_corte_recalcula_totales_y_guarda_fecha_modelo_correcta(self):
        self._create_daily_transactions()

        response = self.client.post(
            "/caja/corte",
            data={
                "retiro_efectivo": "30",
                "fondo_inicial": "99999",
                "total_ventas": "99999",
                "total_egresos": "0",
                "total_efectivo": "0",
                "total_transferencia": "0",
                "total_tarjeta": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        corte = CorteCaja.query.one()
        self.assertIsNotNone(corte.fecha_corte)
        self.assertEqual(float(corte.fondo_inicial), 0.0)
        self.assertEqual(float(corte.total_efectivo), 100.0)
        self.assertEqual(float(corte.total_tarjeta), 200.0)
        self.assertEqual(float(corte.total_egresos), 20.0)
        self.assertEqual(float(corte.retiro_efectivo), 30.0)
        self.assertEqual(float(corte.saldo_final), 50.0)

    def test_corte_rechaza_retiro_mayor_al_efectivo_disponible(self):
        self._create_daily_transactions()

        response = self.client.post("/caja/corte", data={"retiro_efectivo": "81"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CorteCaja.query.count(), 0)

    def test_corte_efectivo_disponible_incluye_iva_cobrado(self):
        ahora = datetime.now()
        pedido = Pedido(
            folio="TEST-IVA", nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000", fecha_entrega=ahora,
            requiere_factura=True, subtotal=100, iva=10, total=110,
            estatus="En proceso",
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add(Pago(
            id_pedido=pedido.id_pedido, monto=110, iva=10, comision=0,
            metodo_pago="Efectivo", fecha_pago=ahora,
        ))
        db.session.commit()

        response = self.client.post("/caja/corte", data={"retiro_efectivo": "110"})

        self.assertEqual(response.status_code, 302)
        corte = CorteCaja.query.one()
        self.assertEqual(float(corte.total_ventas), 100.0)
        self.assertEqual(float(corte.saldo_final), 0.0)

    def test_reporte_muestra_iva_y_comision_fuera_de_ventas(self):
        ahora = datetime.now()
        pedido = Pedido(
            folio="TEST-IMPUESTOS", nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000", fecha_entrega=ahora,
            total=110, estatus="En proceso",
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add(Pago(
            id_pedido=pedido.id_pedido, monto=110, iva=10, comision=4,
            metodo_pago="Tarjeta", fecha_pago=ahora,
        ))
        db.session.commit()

        response = self.client.get("/caja/reporte")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"IVA cobrado", response.data)
        self.assertIn(b"Comisi\xc3\xb3n tarjeta", response.data)
        self.assertIn(b"$10.00", response.data)
        self.assertIn(b"$4.00", response.data)

    def test_reporte_ventas_excluye_iva_y_cartera_conserva_abono_bruto(self):
        ahora = datetime.now()
        pedido = Pedido(
            folio="TEST-REPORTE-IVA", nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000", fecha_entrega=ahora,
            subtotal=100, iva=16, total=116, estatus="En proceso",
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add(Pago(
            id_pedido=pedido.id_pedido, monto=112, iva=12,
            metodo_pago="Efectivo", fecha_pago=ahora,
        ))
        db.session.commit()

        ventas = reporte_ventas(ahora.date(), ahora.date())
        cartera = reporte_cartera()
        fila_cartera = next(f for f in cartera['filas'] if f['folio'] == pedido.folio)

        self.assertEqual(ventas['total_ventas'], 100.0)
        self.assertEqual(fila_cartera['total'], 116.0)
        self.assertEqual(fila_cartera['pagado'], 112.0)
        self.assertEqual(fila_cartera['saldo'], 4.0)

    def test_formulario_pedido_calcula_subtotal_y_total_con_iva(self):
        form = MultiDict([
            ('nombre_cliente', 'Cliente'), ('telefono_cliente', '5550000000'),
            ('fecha_entrega', '2026-11-02'), ('hora_entrega', '12:00'),
            ('requiere_factura', 'on'), ('anticipo', '50'), ('metodo_pago', 'Efectivo'),
            ('rfc', 'XAXX010101000'), ('razon_social', 'PUBLICO EN GENERAL'),
            ('regimen_fiscal', '616'), ('cp_fiscal', '64000'), ('uso_cfdi', 'S01'),
            ('cantidad[]', '1'), ('descripcion[]', 'Producto'), ('precio_unitario[]', '100'),
        ])

        datos = _leer_datos_formulario(form)

        self.assertEqual(datos['subtotal'], 100.0)
        self.assertEqual(datos['total'], 116.0)

    def test_no_se_puede_facturar_pedido_con_pago_tarjeta(self):
        pedido = Pedido(
            folio='TEST-TARJETA-FACTURA', nombre_cliente='Cliente de prueba',
            telefono_cliente='5550000000', fecha_entrega=datetime.now(),
            total=100, estatus='En proceso',
        )
        pedido.pagos.append(Pago(monto=100, metodo_pago='Tarjeta'))

        with self.assertRaisesRegex(
            ValueError, r'Este pedido ya tiene pagos con tarjeta; no se puede facturar\.'
        ):
            _actualizar_pedido(pedido, {'requiere_factura': True})

    def test_mensaje_tarjeta_indica_total_a_cobrar_en_terminal(self):
        pago = Pago(monto=100, comision=4, metodo_pago='Tarjeta')

        self.assertEqual(_texto_cobro_terminal(pago), ' Cobrar en terminal: $104.00')

    def test_formulario_renderiza_pago_en_alta_y_registro_solo_en_edicion(self):
        alta = self.client.get('/pedidos/nuevo')
        self.assertEqual(alta.status_code, 200)
        self.assertIn(b'name="anticipo"', alta.data)
        self.assertNotIn(b'card-pagos-resumen', alta.data)

        pedido = Pedido(
            folio='TEST-FORM', nombre_cliente='Cliente de prueba',
            telefono_cliente='5550000000', fecha_entrega=datetime.now(),
            subtotal=100, iva=16, total=116, estatus='En proceso',
            requiere_factura=True,
        )
        db.session.add(pedido)
        db.session.commit()
        edicion = self.client.get(f'/pedidos/nuevo?editar={pedido.id_pedido}')

        self.assertEqual(edicion.status_code, 200)
        # El registro de pagos ya no es tarjeta aparte; sin pagos no se muestra el botón.
        self.assertNotIn(b'card-pagos-resumen', edicion.data)
        self.assertNotIn('Registro de pagos'.encode(), edicion.data)
        self.assertIn(b'data-pf-toggle-preview', edicion.data)
        self.assertIn('Editando'.encode(), edicion.data)
        self.assertIn(b'Transferencia (cuenta fiscal)', edicion.data)

    def test_pantalla_caja_renderiza_responsables_de_produccion(self):
        db.session.add(Usuario(
            nombre="Producción de prueba",
            correo="produccion@example.test",
            password_hash="test-hash",
            rol="Produccion",
        ))
        db.session.commit()

        response = self.client.get("/caja/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Producción de prueba".encode(), response.data)

    # ------------------------------------------------------------ comisión de mostrador
    ITEMS_REDONDEO = (("1", "10.01", "Cuaderno"), ("1", "20.03", "Lápices"), ("1", "33.33", "Tijeras"))

    def _post_ticket(self, metodo, items=None, token="a"):
        items = items or self.ITEMS_REDONDEO
        datos = MultiDict([("metodo_pago", metodo), ("token_envio", (token * 32)[:32])])
        for cantidad, precio, descripcion in items:
            datos.add("cantidad[]", cantidad)
            datos.add("precio_unitario[]", precio)
            datos.add("descripcion[]", descripcion)
        with patch("rutas.caja.abrir_cajon_o_avisar", return_value=True):
            return self.client.post("/caja/ticket", data=datos, headers={"X-Requested-With": "XMLHttpRequest"})

    def test_ticket_con_tarjeta_guarda_comision_y_cuadra_al_centavo(self):
        respuesta = self._post_ticket("Tarjeta").get_json()
        total = round(10.01 + 20.03 + 33.33, 2)
        esperada = round(total * TASA_COMISION_TARJETA, 2)
        self.assertTrue(respuesta["success"])
        self.assertEqual(respuesta["comision"], esperada)
        self.assertEqual(respuesta["total_cobrado"], round(total + esperada, 2))
        comisiones = [float(m.comision) for m in
                      MovimientoCaja.query.order_by(MovimientoCaja.id_movimiento).all()]
        self.assertEqual(len(comisiones), 3)
        self.assertEqual(round(sum(comisiones), 2), esperada)
        # Las dos primeras son proporcionales (redondeadas); la última absorbe los centavos.
        self.assertEqual(comisiones[0], round(esperada * 10.01 / total, 2))
        self.assertEqual(comisiones[1], round(esperada * 20.03 / total, 2))
        from modelos.bitacora import Bitacora
        self.assertIn(f"comisión ${esperada:.2f}", Bitacora.query.filter_by(accion="CAJA_TICKET").one().detalle)

    def test_ticket_con_efectivo_o_transferencia_no_lleva_comision(self):
        for i, metodo in enumerate(("Efectivo", "Transferencia")):
            respuesta = self._post_ticket(metodo, token=f"tok-{i}").get_json()
            self.assertEqual(respuesta["comision"], 0)
            self.assertEqual(respuesta["total_cobrado"], respuesta["total"])
        self.assertTrue(all(float(m.comision) == 0 for m in MovimientoCaja.query.all()))

    def test_resumen_periodo_incluye_comision_del_mostrador(self):
        from rutas.caja import _resumen_periodo
        self._post_ticket("Tarjeta")
        total = round(10.01 + 20.03 + 33.33, 2)
        esperada = round(total * TASA_COMISION_TARJETA, 2)
        resumen = _resumen_periodo(None)
        self.assertAlmostEqual(resumen["comision"], esperada, places=2)
        self.assertAlmostEqual(resumen["cobrado"]["Tarjeta"], total + esperada, places=2)
        self.assertAlmostEqual(resumen["ingresos"]["Tarjeta"], total, places=2)
        self.assertAlmostEqual(sum(f["comision"] for f in resumen["historial"]), esperada, places=2)

    def test_egresos_no_llevan_comision(self):
        from rutas.caja import _resumen_periodo
        db.session.add(MovimientoCaja(tipo="EGRESO", cantidad=1, descripcion="Gasto", monto=50,
                                      metodo_pago="Tarjeta", comision=9, fecha=datetime.now()))
        db.session.commit()
        resumen = _resumen_periodo(None)
        self.assertEqual(resumen["comision"], 0)
        self.assertEqual(resumen["cobrado"]["Tarjeta"], 0)

    def test_ticket_impreso_muestra_total_cobrado_solo_con_tarjeta(self):
        con_tarjeta = self._post_ticket("Tarjeta", token="a").get_json()
        con_efectivo = self._post_ticket("Efectivo", token="b").get_json()
        html = self.client.get(con_tarjeta["print_url"]).get_data(as_text=True)
        self.assertIn("TOTAL COBRADO", html)
        self.assertIn("Comisión tarjeta", html)
        self.assertIn(f"${con_tarjeta['total_cobrado']:.2f}", html)
        html = self.client.get(con_efectivo["print_url"]).get_data(as_text=True)
        self.assertNotIn("TOTAL COBRADO", html)
        self.assertNotIn("Comisión tarjeta", html)

    def test_modal_de_ticket_trae_bloque_de_comision_oculto(self):
        html = self.client.get("/caja/").get_data(as_text=True)
        self.assertIn(f'id="ticket-resumen-comision"', html)
        self.assertIn(f'data-tasa-comision="{TASA_COMISION_TARJETA}"', html)

    def test_sin_sesion_redirige_al_login(self):
        self._cerrar_sesion()

        response = self.client.get("/caja/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_reporte_solo_administrador(self):
        self.assertEqual(self.client.get("/caja/reporte").status_code, 200)

        recepcion = self._crear_usuario("Recepción", "recepcion@example.test", "Recepcion")
        self._iniciar_sesion(recepcion)

        response = self.client.get("/caja/reporte")

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/caja/reporte", response.headers["Location"])

    def test_produccion_no_puede_hacer_corte(self):
        self._create_daily_transactions()
        produccion = self._crear_usuario("Producción", "produccion2@example.test", "Produccion")
        self._iniciar_sesion(produccion)

        response = self.client.post("/caja/corte", data={"retiro_efectivo": "30"})

        self.assertIn(response.status_code, (302, 403))
        self.assertNotIn("/caja", response.headers.get("Location", ""))
        self.assertEqual(CorteCaja.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
