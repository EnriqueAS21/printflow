import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401  # registran las tablas
from modelos.pedido import DetalleAsignacion, Pedido, TASA_COMISION_TARJETA, TASA_IVA
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


class EstadoLineasTestCase(unittest.TestCase):
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
        for destino in ("rutas.caja.previsualizar_siguiente_folio",
                        "rutas.pedidos.previsualizar_siguiente_folio"):
            patcher = patch(destino, return_value="AC0001")
            patcher.start()
            self.addCleanup(patcher.stop)

        self.admin = self._crear_usuario("Admin", "admin@example.test", "Administrador")
        self.prod_a = self._crear_usuario("Prod A", "a@example.test", "Produccion")
        self.prod_b = self._crear_usuario("Prod B", "b@example.test", "Produccion")
        self._iniciar_sesion(self.admin)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # ---------------------------------------------------------- utilidades
    def _crear_usuario(self, nombre, correo, rol):
        u = Usuario(
            nombre=nombre, correo=correo, rol=rol, activo=True,
            password_hash=generate_password_hash("test-password", method="pbkdf2:sha256"),
        )
        db.session.add(u)
        db.session.commit()
        return u

    def _iniciar_sesion(self, u):
        with self.client.session_transaction() as session:
            session["usuario_id"] = u.id_usuario

    def _items(self, cambios=None):
        """Dos líneas, cada una con su responsable (pedido con asignaciones por producto)."""
        base = [
            {"cantidad": 2, "descripcion": "Lona", "precio_unitario": 50,
             "responsable_id": self.prod_a.id_usuario},
            {"cantidad": 1, "descripcion": "Vinil", "precio_unitario": 30,
             "responsable_id": self.prod_b.id_usuario},
        ]
        for indice, campos in (cambios or {}).items():
            base[indice] = {**base[indice], **campos}
        return base

    def _crear_pedido(self, estatus="En proceso", pagado=False):
        p = Pedido(
            folio="TEST-LINEAS", nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000", fecha_entrega=datetime.now(),
            total=0, estatus=estatus,
        )
        db.session.add(p)
        db.session.flush()
        p.reemplazar_detalles(self._items())
        p.responsables = [self.prod_a, self.prod_b]
        if pagado:
            p.agregar_pago(p.total_float, "Efectivo")
        db.session.commit()
        return p

    def _reemplazar(self, p, items):
        p.reemplazar_detalles(items)
        db.session.commit()
        db.session.refresh(p)
        return p

    def _items_con_id(self, p, cambios=None):
        items = self._items(cambios)
        for item, detalle in zip(items, p.detalles):
            item["id_detalle"] = detalle.id_detalle
        return items

    def _terminar(self, p, indice):
        d = p.detalles[indice]
        return self.client.post(f"/pedidos/{p.id_pedido}/productos/{d.id_detalle}/terminar")

    def _cambiar(self, p, **form):
        return self.client.post(f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado", data=form)

    # ---------------------------------------------------- terminar_producto
    def test_terminar_producto_marca_linea_sin_cerrar_pedido_si_faltan_otras(self):
        p = self._crear_pedido()

        response = self._terminar(p, 0)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertTrue(p.detalles[0].asignacion.completado)
        self.assertFalse(p.detalles[1].asignacion.completado)
        self.assertEqual(p.estatus, "En proceso")
        self.assertIsNone(p.fecha_terminado)

    def test_terminar_ultima_linea_pasa_pedido_a_terminado(self):
        p = self._crear_pedido()

        self._terminar(p, 0)
        self._terminar(p, 1)

        db.session.refresh(p)
        self.assertTrue(p.productos_completos)
        self.assertEqual(p.estatus, "Terminado")
        self.assertIsNotNone(p.fecha_terminado)

    def test_terminar_producto_repetido_no_cambia_nada(self):
        p = self._crear_pedido()
        self._terminar(p, 0)

        response = self._terminar(p, 0)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertTrue(p.detalles[0].asignacion.completado)
        self.assertEqual(p.estatus, "En proceso")

    def test_terminar_producto_con_pedido_fuera_de_proceso_no_marca(self):
        p = self._crear_pedido(estatus="Terminado")

        self._terminar(p, 0)

        db.session.refresh(p)
        self.assertFalse(p.detalles[0].asignacion.completado)

    def test_terminar_producto_inexistente_o_ajeno_devuelve_404(self):
        p = self._crear_pedido()

        response = self.client.post(f"/pedidos/{p.id_pedido}/productos/99999/terminar")

        self.assertEqual(response.status_code, 404)

    def test_produccion_solo_termina_su_linea(self):
        p = self._crear_pedido()
        self._iniciar_sesion(self.prod_a)

        self._terminar(p, 1)  # asignada a Prod B
        db.session.refresh(p)
        self.assertFalse(p.detalles[1].asignacion.completado)

        self._terminar(p, 0)  # asignada a Prod A
        db.session.refresh(p)
        self.assertTrue(p.detalles[0].asignacion.completado)

    # ---------------------------------------------------- cambiar_estado
    def test_cambiar_estado_en_proceso_a_terminado_sin_asignaciones_por_producto(self):
        p = Pedido(
            folio="TEST-SIMPLE", nombre_cliente="Cliente de prueba",
            telefono_cliente="5550000000", fecha_entrega=datetime.now(),
            total=10, estatus="En proceso",
        )
        db.session.add(p)
        db.session.commit()

        response = self._cambiar(p)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "Terminado")
        self.assertIsNotNone(p.fecha_terminado)

    def test_cambiar_estado_a_terminado_exige_todas_las_lineas_completas(self):
        p = self._crear_pedido()
        self._terminar(p, 0)

        response = self._cambiar(p, nuevo_estado="Terminado")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "En proceso")

    def test_cambiar_estado_ajax_rechaza_con_400_si_faltan_lineas(self):
        p = self._crear_pedido()

        response = self.client.post(
            f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado",
            data={"nuevo_estado": "Terminado"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    def test_cambiar_estado_rechaza_transicion_invalida(self):
        p = self._crear_pedido()

        response = self._cambiar(p, nuevo_estado="Entregado")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "En proceso")

    def test_cambiar_estado_entregar_exige_pedido_pagado(self):
        p = self._crear_pedido(estatus="Terminado", pagado=False)

        self._cambiar(p)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "Terminado")

        p.agregar_pago(p.saldo_restante, "Efectivo")
        db.session.commit()
        self._cambiar(p)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "Entregado")
        self.assertIsNotNone(p.fecha_entregado)

    def test_cambiar_estado_produccion_no_puede_entregar(self):
        p = self._crear_pedido(estatus="Terminado", pagado=True)
        self._iniciar_sesion(self.prod_a)

        response = self.client.post(
            f"/dashboard/pedidos/{p.id_pedido}/cambiar-estado",
            data={"nuevo_estado": "Entregado"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 403)
        db.session.refresh(p)
        self.assertEqual(p.estatus, "Terminado")

    def test_cambiar_estado_tras_terminar_todas_las_lineas(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        # Estado manual forzado a En proceso con todas completas: el cambio sí procede.
        p.detalles[1].asignacion.completado = True
        db.session.commit()

        self._cambiar(p, nuevo_estado="Terminado")

        db.session.refresh(p)
        self.assertEqual(p.estatus, "Terminado")

    # ------------------------------------------------- reemplazar_detalles
    def test_reemplazar_detalles_crea_asignaciones_no_completadas(self):
        p = self._crear_pedido()

        self.assertEqual(len(p.detalles), 2)
        self.assertEqual(float(p.subtotal), 130.0)
        self.assertEqual(p.total_float, 130.0)
        self.assertTrue(all(d.asignacion and not d.asignacion.completado for d in p.detalles))

    def test_reemplazar_detalles_conserva_completado_si_linea_no_cambio(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p))

        self.assertTrue(p.detalles[0].asignacion.completado)
        self.assertFalse(p.detalles[1].asignacion.completado)

    def test_reemplazar_detalles_reinicia_completado_si_cambia_cantidad(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p, {0: {"cantidad": 3}}))

        self.assertFalse(p.detalles[0].asignacion.completado)

    def test_reemplazar_detalles_reinicia_completado_si_cambia_descripcion(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p, {0: {"descripcion": "Lona mate"}}))

        self.assertFalse(p.detalles[0].asignacion.completado)

    def test_reemplazar_detalles_reinicia_completado_si_cambia_precio(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p, {0: {"precio_unitario": 55}}))

        self.assertFalse(p.detalles[0].asignacion.completado)

    def test_reemplazar_detalles_reinicia_completado_si_cambia_responsable(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p, {0: {"responsable_id": self.prod_b.id_usuario}}))

        self.assertFalse(p.detalles[0].asignacion.completado)
        self.assertEqual(p.detalles[0].asignacion.id_usuario, self.prod_b.id_usuario)

    def test_reemplazar_detalles_sin_id_detalle_reinicia_completado(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        db.session.refresh(p)

        self._reemplazar(p, self._items())  # sin id_detalle: se trata como línea nueva

        self.assertFalse(p.detalles[0].asignacion.completado)

    def test_reemplazar_detalles_solo_reinicia_la_linea_modificada(self):
        p = self._crear_pedido()
        self._terminar(p, 0)
        self._terminar(p, 1)
        db.session.refresh(p)

        self._reemplazar(p, self._items_con_id(p, {1: {"cantidad": 5}}))

        self.assertTrue(p.detalles[0].asignacion.completado)
        self.assertFalse(p.detalles[1].asignacion.completado)

    def test_reemplazar_detalles_sin_responsable_no_crea_asignacion(self):
        p = self._crear_pedido()

        self._reemplazar(p, [{"cantidad": 1, "descripcion": "Taza", "precio_unitario": 10}])

        self.assertEqual(len(p.detalles), 1)
        self.assertIsNone(p.detalles[0].asignacion)
        self.assertEqual(DetalleAsignacion.query.count(), 0)
        self.assertEqual(p.total_float, 10.0)


if __name__ == "__main__":
    unittest.main()
