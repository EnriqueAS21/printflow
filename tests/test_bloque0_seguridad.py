"""Bloque 0 de la auditoría: renombrado de procesos (A7), caja de prueba de reglas (M12) y SECRET_KEY.

Los scripts de Procesos y Reglas viven en las plantillas y no hay framework de pruebas de JS, así que
el JS se verifica sobre la página renderizada (que contenga la corrección y no el patrón inseguro) y el
contrato del servidor en que se apoya cada corrección se prueba por HTTP.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario  # noqa: F401
from modelos.produccion import Proceso, ReglaClasificacion
from modelos.usuario import Usuario
from rutas.configuracion import configuracion_bp
from rutas.autenticacion import autenticacion_bp
from rutas.caja import caja_bp
from rutas.clientes import clientes_bp
from rutas.dashboard import dashboard_bp
from rutas.notificaciones import notificaciones_bp
from rutas.pedidos import pedidos_bp
from rutas.productos import productos_bp
from rutas.reportes import reportes_bp
from rutas.stickers import stickers_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios import permisos

RAIZ = Path(__file__).resolve().parent.parent
AJAX = {"X-Requested-With": "XMLHttpRequest"}
XSS = '<img src=x onerror=alert(1)>'


class Bloque0TestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(RAIZ / "templates"),
                         static_folder=str(RAIZ / "static"))
        self.app.config.update(
            SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        permisos.registrar_seguridad(self.app)
        # base.html enlaza a todos los módulos: se registran igual que en app.py.
        for bp, prefijo in ((usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (pedidos_bp, "/pedidos"),
                            (autenticacion_bp, "/auth"), (dashboard_bp, "/dashboard"), (caja_bp, "/caja"),
                            (configuracion_bp, "/configuracion"), (reportes_bp, "/reportes"),
                            (stickers_bp, "/stickers"), (notificaciones_bp, "/notificaciones"),
                            (clientes_bp, "/clientes"), (productos_bp, "/productos")):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.contexto = self.app.app_context()
        self.contexto.push()
        db.create_all()
        admin = Usuario(nombre="ADMIN", correo="admin@example.test", rol="Administrador",
                        activo=True, password_hash="x")
        db.session.add(admin)
        self.proceso = Proceso(nombre="CORTE", orden=1, activo=True, es_sistema=False)
        db.session.add(self.proceso)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = admin.id_usuario
        parche = patch("rutas.configuracion.reclamar_envio", return_value=True)
        parche.start()
        self.addCleanup(parche.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.contexto.pop()

    def post(self, ruta, datos):
        return self.client.post(ruta, json=datos, headers=AJAX)

    # --- A7: renombrar un proceso ---
    def test_a7_script_actualiza_el_nombre_original_tras_guardar(self):
        html = self.client.get("/configuracion/procesos").get_data(as_text=True)
        self.assertIn("input.dataset.original = resultado.nombre", html)
        # La actualización debe ir antes de cancelarEdicion, que restaura `original` en el input.
        posicion_actualiza = html.index("input.dataset.original = resultado.nombre")
        posicion_cancela = html.index("cancelarEdicion(input);", posicion_actualiza)
        self.assertLess(posicion_actualiza, posicion_cancela)

    def test_a7_servidor_devuelve_nombre_normalizado_y_es_estable(self):
        respuesta = self.post("/configuracion/procesos", {
            "accion": "editar", "id_proceso": self.proceso.id_proceso, "nombre": " laminado ",
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()["nombre"], "LAMINADO")
        self.assertEqual(db.session.get(Proceso, self.proceso.id_proceso).nombre, "LAMINADO")
        # Un segundo Enter con el valor devuelto (lo que ahora queda en el input) no lo cambia.
        repetida = self.post("/configuracion/procesos", {
            "accion": "editar", "id_proceso": self.proceso.id_proceso, "nombre": "LAMINADO",
        })
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(db.session.get(Proceso, self.proceso.id_proceso).nombre, "LAMINADO")

    # --- M12: caja de prueba de reglas ---
    def test_m12_script_no_usa_innerhtml_en_el_resultado(self):
        html = self.client.get("/configuracion/reglas").get_data(as_text=True)
        self.assertNotIn("resultado.innerHTML", html)
        self.assertIn("resultado.replaceChildren", html)

    def test_m12_texto_libre_viaja_como_dato_y_la_pagina_lo_escapa(self):
        db.session.add(ReglaClasificacion(palabra_clave=XSS, prioridad=1, activo=True))
        db.session.commit()
        # Insertada directo (sin pasar por el guardado que la pone en mayúsculas): se compara tal cual.
        datos = self.post("/configuracion/reglas", {
            "accion": "prueba", "descripcion": f"pedido {XSS}",
        }).get_json()
        self.assertEqual(datos["regla"]["palabra_clave"], XSS)
        html = self.client.get("/configuracion/reglas").get_data(as_text=True)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)  # la lista de reglas la muestra escapada
        self.assertNotIn(XSS, html)


class SecretKeyTestCase(unittest.TestCase):
    def _arrancar(self, secret_key):
        entorno = {**os.environ, "SECRET_KEY": secret_key, "DATABASE_URL": "sqlite:///:memory:"}
        return subprocess.run(
            [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(RAIZ)!r}); import app"],
            cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=120,
        )

    def test_secret_key_corta_impide_arrancar(self):
        resultado = self._arrancar("corta")
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("al menos 32 caracteres", resultado.stderr)


if __name__ == "__main__":
    unittest.main()
