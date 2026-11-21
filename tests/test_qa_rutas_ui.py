"""QA del despliegue 2: permisos estrictos (403) y respuestas AJAX sin avisos huérfanos."""
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401
from modelos.envios import nuevo_token
from modelos.produccion import Proceso, ReglaClasificacion
from modelos.usuario import Usuario
from rutas.autenticacion import autenticacion_bp
from rutas.configuracion import configuracion_bp
from rutas.productos import productos_bp
from servicios import rutas_produccion
from servicios.permisos import registrar_seguridad

AJAX = {"X-Requested-With": "XMLHttpRequest"}


class QaRutasUiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder="../templates")
        self.app.config.update(SECRET_KEY="t", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        registrar_seguridad(self.app)
        for bp, prefijo in ((autenticacion_bp, "/auth"), (configuracion_bp, "/configuracion"),
                            (productos_bp, "/productos")):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.session.remove)
        parche = patch.object(rutas_produccion, "invalidar")
        parche.start()
        self.addCleanup(parche.stop)

        h = generate_password_hash("x", method="pbkdf2:sha256")
        self.usuarios = {}
        for rol in ("Administrador", "Recepcion", "Produccion"):
            u = Usuario(nombre=rol, correo=f"{rol}@x.t", rol=rol, activo=True, password_hash=h)
            db.session.add(u)
            self.usuarios[rol] = u
        db.session.add(Proceso(nombre="DISEÑO", orden=1, activo=True, es_sistema=False))
        db.session.commit()

    def _cliente(self, rol):
        cliente = self.app.test_client()
        with cliente.session_transaction() as s:
            s["usuario_id"] = self.usuarios[rol].id_usuario
        return cliente

    def test_sin_permiso_responde_403_en_pagina_y_ajax(self):
        rutas = [
            ("get", "/configuracion/procesos"), ("post", "/configuracion/procesos"),
            ("get", "/configuracion/reglas"), ("post", "/configuracion/reglas"),
            ("get", "/productos/nuevo"), ("get", "/productos/1/editar"), ("post", "/productos/1/editar"),
            ("get", "/productos/1/ruta"), ("get", "/productos/1/aplicar-ruta"), ("post", "/productos/1/aplicar-ruta"),
        ]
        for rol in ("Recepcion", "Produccion"):
            cliente = self._cliente(rol)
            for metodo, url in rutas:
                with self.subTest(rol=rol, ruta=f"{metodo} {url}"):
                    self.assertEqual(getattr(cliente, metodo)(url).status_code, 403)
                    r = getattr(cliente, metodo)(url, headers=AJAX, json={})
                    self.assertEqual(r.status_code, 403)
                    self.assertTrue(r.is_json)

    def test_ajax_de_procesos_no_deja_avisos_pendientes(self):
        cliente = self._cliente("Administrador")
        proceso = Proceso.query.filter_by(nombre="DISEÑO").one()
        casos = [
            {"accion": "editar", "id_proceso": proceso.id_proceso, "nombre": "diseño 2"},
            {"accion": "activar", "id_proceso": proceso.id_proceso},
            {"accion": "reordenar", "orden": [proceso.id_proceso]},
            {"accion": "editar", "id_proceso": proceso.id_proceso, "nombre": ""},   # error 400
        ]
        for datos in casos:
            with self.subTest(datos=datos):
                r = cliente.post("/configuracion/procesos", headers=AJAX, json={**datos, "token_envio": nuevo_token()})
                self.assertTrue(r.is_json)
                self.assertIn(r.status_code, (200, 400), r.get_data(as_text=True))
                with cliente.session_transaction() as s:
                    self.assertFalse(s.get("_flashes"), "la petición AJAX dejó un aviso para la página siguiente")

    def test_ajax_de_reglas_responde_siempre_json(self):
        cliente = self._cliente("Administrador")
        r = cliente.post("/configuracion/reglas", headers=AJAX, json={"accion": "crear", "palabra_clave": "", "token_envio": nuevo_token()})
        self.assertEqual((r.status_code, r.is_json), (400, True))
        r = cliente.post("/configuracion/reglas", headers=AJAX, json={"accion": "nada", "token_envio": nuevo_token()})
        self.assertEqual((r.status_code, r.is_json), (400, True))
        r = cliente.post("/configuracion/reglas", headers=AJAX, json={
            "accion": "crear", "palabra_clave": "lona", "prioridad": 1,
            "ruta": {"pasos": [], "id_proceso_principal": None}, "token_envio": nuevo_token()})
        self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True))
        self.assertEqual(ReglaClasificacion.query.count(), 1)

    def test_producto_inexistente_es_404_html(self):
        r = self._cliente("Administrador").get("/productos/999/editar")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.is_json)


if __name__ == "__main__":
    unittest.main()
