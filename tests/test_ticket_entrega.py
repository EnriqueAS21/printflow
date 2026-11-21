import json
import unittest
from datetime import datetime

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, cliente, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401  # registran las tablas
from modelos.bitacora import Bitacora
from modelos.configuracion_nota import obtener_configuracion_nota
from modelos.pedido import DetallePedido, Pago, Pedido, TASA_COMISION_TARJETA, TASA_IVA
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


def _apartado(titulo, texto, ticket):
    return {
        "titulo": titulo, "activo": True, "condicion": "siempre",
        "mostrar_en": {"nota": False, "ticket": ticket, "whatsapp": False},
        "bloques": [{"subtitulo": f"Sub {titulo}", "texto": texto}],
    }


class TicketEntregaTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        self.app.jinja_env.globals.update(TASA_IVA=TASA_IVA, TASA_COMISION_TARJETA=TASA_COMISION_TARJETA)
        db.init_app(self.app)
        registrar_seguridad(self.app)
        for bp, prefijo in (
            (usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (pedidos_bp, "/pedidos"),
            (autenticacion_bp, "/auth"), (dashboard_bp, "/dashboard"), (caja_bp, "/caja"),
            (configuracion_bp, "/configuracion"), (reportes_bp, "/reportes"),
            (stickers_bp, "/stickers"), (notificaciones_bp, "/notificaciones"),
            (clientes_bp, "/clientes"), (productos_bp, "/productos"),
        ):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.admin = Usuario(
            nombre="Admin", correo="admin@example.test", rol="Administrador", activo=True,
            password_hash=generate_password_hash("x", method="pbkdf2:sha256"),
        )
        db.session.add(self.admin)
        config = obtener_configuracion_nota()
        config.nombre_negocio = "Imprenta Demo"
        config.lema = "lema de prueba"
        config.politicas = "Política uno de prueba.\n\n  \nPolítica dos de prueba."
        config.apartados = [
            _apartado("Marcado", "texto del marcado", True),
            _apartado("Sin marcar", "texto sin marcar", False),
        ]
        db.session.commit()
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = self.admin.id_usuario

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pedido(self, estatus="Entregado", folio="E-001"):
        p = Pedido(
            folio=folio, nombre_cliente="Ana López", telefono_cliente="5512345678",
            fecha_creacion=datetime(2026, 11, 20, 9, 0), fecha_entrega=datetime(2026, 11, 23, 15, 0),
            fecha_entregado=datetime(2026, 11, 23, 16, 45), estatus=estatus,
            subtotal=1000, iva=0, total=1000,
        )
        p.detalles.append(DetallePedido(
            cantidad=2, descripcion="Lonas 1x1", precio_unitario=500, subtotal=1000))
        p.pagos.append(Pago(monto=1000, metodo_pago="Tarjeta", comision=40,
                            fecha_pago=datetime(2026, 11, 20, 9, 5)))
        db.session.add(p)
        db.session.commit()
        return p

    def _bitacora_estado(self, p, nombre, estado="Entregado", fecha=None):
        db.session.add(Bitacora(
            nombre_usuario=nombre, accion="PEDIDO_ESTADO", entidad="pedido",
            id_entidad=p.id_pedido, detalle=f"{p.folio}: {estado}",
            fecha=fecha or datetime.now(),
        ))
        db.session.commit()

    def _get(self, p):
        return self.client.get(f"/pedidos/{p.id_pedido}/ticket-entrega")

    def test_responde_200_con_folio_titulo_y_politica(self):
        p = self._pedido()
        r = self._get(p)
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("FOLIO E-001", html)
        self.assertIn("COMPROBANTE DE ENTREGA", html)
        self.assertIn("• Política uno de prueba.", html)
        self.assertIn("• Política dos de prueba.", html)
        self.assertIn("Imprenta Demo", html)
        self.assertIn("lema de prueba", html)
        self.assertIn("** PAGADO **", html)
        self.assertIn("Comisión tarjeta", html)
        self.assertIn("$1,000.00", html)
        self.assertIn("23/11/2026  04:45 PM", html)

    def test_registra_en_bitacora(self):
        p = self._pedido()
        self._get(p)
        fila = Bitacora.query.filter_by(accion="PEDIDO_TICKET_ENTREGA").one()
        self.assertEqual(fila.id_entidad, p.id_pedido)
        self.assertEqual(fila.detalle, "E-001: ticket de entrega")

    def test_404_si_no_existe(self):
        self.assertEqual(self.client.get("/pedidos/9999/ticket-entrega").status_code, 404)

    def test_redirige_si_no_esta_entregado(self):
        for estatus in ("En proceso", "Terminado"):
            p = self._pedido(estatus, folio=f"X-{estatus[:2]}")
            r = self._get(p)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/dashboard", r.headers["Location"])
        self.assertEqual(Bitacora.query.filter_by(accion="PEDIDO_TICKET_ENTREGA").count(), 0)

    def test_muestra_quien_entrego_segun_la_bitacora(self):
        p = self._pedido()
        self._bitacora_estado(p, "Pedro Viejo", fecha=datetime(2026, 11, 22, 10, 0))
        self._bitacora_estado(p, "Marta Reciente", fecha=datetime(2026, 11, 23, 16, 45))
        self._bitacora_estado(p, "Otro", estado="Terminado", fecha=datetime(2026, 11, 24, 8, 0))
        self.assertIn("Entregó: Marta Reciente", self._get(p).get_data(as_text=True))

    def test_entrego_sin_bitacora_muestra_guion(self):
        html = self._get(self._pedido()).get_data(as_text=True)
        self.assertIn("Entregó: —", html)

    def test_apartado_marcado_para_ticket_si_y_no_marcado_no(self):
        html = self._get(self._pedido()).get_data(as_text=True)
        self.assertIn("MARCADO", html)
        self.assertIn("texto del marcado", html)
        self.assertNotIn("texto sin marcar", html)
        self.assertNotIn("SIN MARCAR", html)

    def test_guardar_diseno_de_nota_con_casilla_ticket(self):
        apartados = [_apartado("Marcado", "t", True), _apartado("Otro", "t", False)]
        r = self.client.post("/configuracion/nota", data={
            "nombre_negocio": "Imprenta Demo", "lema": "x", "direccion": "Calle 1",
            "telefono": "555", "politicas": "Una", "apartados_json": json.dumps(apartados),
        })
        self.assertEqual(r.status_code, 302)
        db.session.expire_all()
        guardados = obtener_configuracion_nota().apartados
        self.assertTrue(guardados[0]["mostrar_en"]["ticket"])
        self.assertFalse(guardados[1]["mostrar_en"]["ticket"])

    def test_pagina_de_configuracion_ofrece_la_casilla(self):
        html = self.client.get("/configuracion/nota").get_data(as_text=True)
        self.assertIn('data-destino="ticket"', html)
        self.assertIn("Ticket de entrega", html)

    # ------------------------------------------------------------ conexión con el tablero
    def _boton_ticket(self, html):
        return html[html.index('data-ticket-url'):].split('"')[1]

    def test_tarjeta_entregado_apunta_al_ticket_de_entrega(self):
        p = self._pedido("Entregado", folio="T-ENT")
        html = self.client.get(f"/pedidos/{p.id_pedido}/tarjeta").get_json()["html_tarjeta"]
        self.assertEqual(self._boton_ticket(html), f"/pedidos/{p.id_pedido}/ticket-entrega")
        self.assertIn("Imprimir ticket de entrega", html)

    def test_tarjeta_terminado_apunta_a_la_orden_de_trabajo(self):
        p = self._pedido("Terminado", folio="T-TER")
        html = self.client.get(f"/pedidos/{p.id_pedido}/tarjeta").get_json()["html_tarjeta"]
        self.assertEqual(self._boton_ticket(html), f"/pedidos/{p.id_pedido}/ticket")
        self.assertNotIn("ticket-entrega", html)

    def test_panel_de_cobro_trae_tasa_y_bloque_de_comision_oculto(self):
        p = self._pedido("En proceso", folio="P-COM")
        p.pagos.clear()
        db.session.commit()
        html = self.client.get(f"/pedidos/{p.id_pedido}/tarjeta").get_json()["html_tarjeta"]
        self.assertIn(f'data-tasa-comision="{TASA_COMISION_TARJETA}"', html)
        self.assertRegex(html, r'data-pago-comision aria-live="polite" hidden>')
        self.assertIn("Comisión 4%:", html)
        self.assertIn("data-cobro-terminal", html)

    def test_cambiar_estado_ajax_devuelve_url_ticket_entrega(self):
        p = self._pedido("Terminado", folio="C-AJAX")
        r = self.client.post(f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado",
                             data={"nuevo_estado": "Entregado"},
                             headers={"X-Requested-With": "XMLHttpRequest"})
        datos = r.get_json()
        self.assertTrue(datos["success"])
        self.assertEqual(datos["url_ticket_entrega"], f"/pedidos/{p.id_pedido}/ticket-entrega")
        with self.client.session_transaction() as sesion:
            self.assertNotIn("imprimir_ticket_entrega", sesion)

    def test_cambiar_estado_ajax_a_terminado_no_trae_url(self):
        p = self._pedido("En proceso", folio="C-TER")
        datos = self.client.post(f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado",
                                 data={"nuevo_estado": "Terminado"},
                                 headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
        self.assertTrue(datos["success"])
        self.assertNotIn("url_ticket_entrega", datos)

    def test_cambiar_estado_sin_ajax_deja_bandera_en_sesion(self):
        p = self._pedido("Terminado", folio="C-FICHA")
        r = self.client.post(f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado",
                             data={"nuevo_estado": "Entregado", "volver": "ficha"})
        self.assertEqual(r.status_code, 302)
        with self.client.session_transaction() as sesion:
            self.assertEqual(sesion["imprimir_ticket_entrega"], p.id_pedido)
        # La página siguiente lanza la impresión una sola vez.
        html = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn(f'imprimirTicket("/pedidos/{p.id_pedido}/ticket-entrega")', html)
        with self.client.session_transaction() as sesion:
            self.assertNotIn("imprimir_ticket_entrega", sesion)

    def test_ficha_entregado_tiene_boton_de_ticket_de_entrega(self):
        p = self._pedido("Entregado", folio="F-ENT")
        html = self.client.get(f"/pedidos/ficha/{p.folio}").get_data(as_text=True)
        self.assertIn(f'data-ticket-url="/pedidos/{p.id_pedido}/ticket-entrega"', html)
        self.assertIn("Ticket de entrega", html)

    def test_imprimir_ticket_esta_definido_una_sola_vez_en_base(self):
        html = self.client.get("/configuracion/nota").get_data(as_text=True)
        self.assertEqual(html.count("function imprimirTicket(url)"), 1)

    def test_entrega_en_dos_renglones_y_logo_en_negro(self):
        html = self._get(self._pedido()).get_data(as_text=True)
        self.assertIn("<div>Entrega:</div>\n        <div>23/11/2026  04:45 PM</div>", html)
        self.assertIn("grayscale(1) contrast(200%) brightness(0.9)", html)


if __name__ == "__main__":
    unittest.main()
