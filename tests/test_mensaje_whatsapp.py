import unittest
from datetime import datetime

from flask import Flask

from config import db
from modelos import configuracion_nota, pedido  # noqa: F401  # registran las tablas
from modelos.configuracion_nota import obtener_configuracion_nota
from modelos.pedido import DetallePedido, Pago, Pedido


class MensajeWhatsappTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        config = obtener_configuracion_nota()
        config.nombre_negocio = "Imprenta Demo"
        config.direccion = "Calle Falsa 123"
        config.telefono = "55 1234 5678"
        config.apartados = []
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pedido(self, estatus="En proceso", pagos=(), detalles=True,
                entrega=datetime(2026, 11, 23, 15, 30), entregado=None):
        p = Pedido(
            folio="F-001", nombre_cliente="Ana López", telefono_cliente="5512345678",
            fecha_entrega=entrega, estatus=estatus, fecha_entregado=entregado,
            subtotal=1350, iva=0, total=1350,
        )
        if detalles:
            p.detalles.append(DetallePedido(
                cantidad=2, descripcion="Lonas 1x1", precio_unitario=500, subtotal=1000))
            p.detalles.append(DetallePedido(
                cantidad=1.5, descripcion="Vinil", precio_unitario=233.33, subtotal=350))
        for monto in pagos:
            p.pagos.append(Pago(monto=monto, metodo_pago="Efectivo"))
        db.session.add(p)
        db.session.commit()
        return p

    def test_en_proceso_sin_anticipo(self):
        msg = self._pedido().mensaje_whatsapp
        self.assertEqual(msg, (
            "Hola *ANA LÓPEZ* 👋\n"
            "Te escribimos de *Imprenta Demo*. Recibimos tu pedido:\n\n"
            "*Folio:* F-001\n"
            "*Entrega:* lunes 23/11/2026, 3:30 PM\n\n"
            "*Detalles:*\n"
            "• 2x Lonas 1x1\n"
            "• 1.5x Vinil\n\n"
            "*Total:* $1,350.00\n"
            "*Resta:* $1,350.00\n\n"
            "Te avisamos por aquí cuando esté listo."
        ))
        self.assertNotIn("Anticipo", msg)

    def test_en_proceso_con_anticipo(self):
        msg = self._pedido(pagos=[350]).mensaje_whatsapp
        self.assertIn("*Total:* $1,350.00\n*Anticipo:* $350.00\n*Resta:* $1,000.00", msg)
        self.assertNotIn("Pagado", msg)

    def test_en_proceso_pagado(self):
        msg = self._pedido(pagos=[1350]).mensaje_whatsapp
        self.assertIn("*Total:* $1,350.00\n*Pagado:* ✔ No tienes saldo pendiente.", msg)
        self.assertNotIn("Anticipo", msg)
        self.assertNotIn("Resta", msg)

    def test_en_proceso_sin_fecha_de_entrega(self):
        p = self._pedido()
        p.detalles, p.pagos  # noqa: B018  # carga las relaciones antes de vaciar la fecha
        with db.session.no_autoflush:  # la columna es NOT NULL; solo se prueba el mensaje
            p.fecha_entrega = None
            msg = p.mensaje_whatsapp
        db.session.rollback()
        self.assertNotIn("Entrega", msg)
        self.assertIn("*Folio:* F-001\n\n*Detalles:*", msg)

    def test_sin_detalles(self):
        msg = self._pedido(detalles=False).mensaje_whatsapp
        self.assertIn("*Detalles:*\n• Detalles registrados en la nota", msg)

    def test_terminado_con_saldo(self):
        msg = self._pedido("Terminado", pagos=[350]).mensaje_whatsapp
        self.assertEqual(msg, (
            "Hola *ANA LÓPEZ* 👋\n"
            "¡Tu pedido *F-001* ya está listo para recoger! ✅\n\n"
            "*Detalles:*\n"
            "• 2x Lonas 1x1\n"
            "• 1.5x Vinil\n\n"
            "*Por liquidar al recoger:* $1,000.00\n\n"
            "📍 Calle Falsa 123\n"
            "📞 55 1234 5678"
        ))

    def test_terminado_pagado(self):
        msg = self._pedido("Terminado", pagos=[1350]).mensaje_whatsapp
        self.assertIn("*Pagado:* ✔ No tienes saldo pendiente.", msg)
        self.assertNotIn("Por liquidar", msg)

    def test_terminado_omite_direccion_y_telefono_vacios(self):
        config = obtener_configuracion_nota()
        config.direccion = ""
        config.telefono = ""
        db.session.commit()
        msg = self._pedido("Terminado").mensaje_whatsapp
        self.assertNotIn("📍", msg)
        self.assertNotIn("📞", msg)

    def test_entregado(self):
        msg = self._pedido("Entregado", entregado=datetime(2026, 11, 24, 10, 0)).mensaje_whatsapp
        self.assertEqual(msg, (
            "Hola *ANA LÓPEZ*, gracias por tu compra 🙌\n"
            "Tu pedido *F-001* fue entregado el 24/11/2026.\n\n"
            "Cualquier duda, aquí estamos.\n"
            "*Imprenta Demo* · 55 1234 5678"
        ))

    def test_entregado_sin_fecha_ni_telefono(self):
        config = obtener_configuracion_nota()
        config.telefono = ""
        db.session.commit()
        msg = self._pedido("Entregado").mensaje_whatsapp
        self.assertIn("Tu pedido *F-001* fue entregado.", msg)
        self.assertTrue(msg.endswith("*Imprenta Demo*"))

    def test_mensaje_simple(self):
        self.assertEqual(
            self._pedido().mensaje_whatsapp_simple,
            "Hola *ANA LÓPEZ*, te escribimos de *Imprenta Demo* sobre tu pedido *F-001*.",
        )

    def test_apartados_se_agregan_al_final(self):
        config = obtener_configuracion_nota()
        config.apartados = [{
            "titulo": "Cuentas", "activo": True,
            "mostrar_en": {"whatsapp": True}, "condicion": "siempre",
            "bloques": [{"subtitulo": "", "texto": "Banco X"}],
        }]
        db.session.commit()
        msg = self._pedido().mensaje_whatsapp
        self.assertTrue(msg.endswith("cuando esté listo.\n\n*CUENTAS*\nBanco X"))

    def test_datos_del_negocio_salen_de_configuracion_nota(self):
        p = self._pedido("Terminado")
        self.assertIn("Calle Falsa 123", p.mensaje_whatsapp)
        config = obtener_configuracion_nota()
        config.nombre_negocio = "Otro Negocio"
        config.direccion = "Av. Nueva 9"
        db.session.commit()
        p.estatus = "En proceso"
        self.assertIn("*Otro Negocio*", p.mensaje_whatsapp)
        self.assertNotIn("Imprenta Demo", p.mensaje_whatsapp)
        p.estatus = "Terminado"
        self.assertIn("📍 Av. Nueva 9", p.mensaje_whatsapp)
        self.assertNotIn("Calle Falsa 123", p.mensaje_whatsapp)
        self.assertIn("*Otro Negocio*", p.mensaje_whatsapp_simple)


if __name__ == "__main__":
    unittest.main()
