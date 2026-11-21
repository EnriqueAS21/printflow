"""Bloque 2: sesión vencida (401 JSON a código / 302 a navegación), denegar(), next seguro, caché y cookies."""
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401
from modelos.bitacora import Bitacora
from modelos.pedido import Pedido, TASA_COMISION_TARJETA, TASA_IVA
from modelos.usuario import Usuario
from rutas.autenticacion import _destino_seguro, autenticacion_bp
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
from servicios import permisos
from servicios.permisos import es_destino_interno, registrar_seguridad

AJAX = {"X-Requested-With": "XMLHttpRequest"}
FETCH_SIN_CABECERAS = {"Sec-Fetch-Mode": "cors"}     # lo que manda el navegador en un fetch() sin cabeceras propias
NAVEGACION = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html,application/xhtml+xml"}


class SesionVencidaTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="test-secret", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.jinja_env.globals.update(TASA_IVA=TASA_IVA, TASA_COMISION_TARJETA=TASA_COMISION_TARJETA)
        registrar_seguridad(self.app)
        for bp, prefijo in ((usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (pedidos_bp, "/pedidos"),
                            (autenticacion_bp, "/auth"), (dashboard_bp, "/dashboard"), (caja_bp, "/caja"),
                            (configuracion_bp, "/configuracion"), (reportes_bp, "/reportes"),
                            (stickers_bp, "/stickers"), (notificaciones_bp, "/notificaciones"),
                            (clientes_bp, "/clientes"), (productos_bp, "/productos")):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.contexto = self.app.app_context()
        self.contexto.push()
        db.create_all()
        for destino in ("rutas.caja.previsualizar_siguiente_folio", "rutas.pedidos.previsualizar_siguiente_folio"):
            parche = patch(destino, return_value="AC0001")
            parche.start()
            self.addCleanup(parche.stop)
        self.admin = self._usuario("Admin", "admin@x.test", "Administrador")
        self.prod = self._usuario("Prod", "prod@x.test", "Produccion")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.contexto.pop()

    def _usuario(self, nombre, correo, rol, activo=True):
        u = Usuario(nombre=nombre, correo=correo, rol=rol, activo=activo,
                    password_hash=generate_password_hash("clave-123", method="pbkdf2:sha256"))
        db.session.add(u)
        db.session.commit()
        return u

    def _con_sesion(self, usuario_):
        cliente = self.app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario_id"] = usuario_.id_usuario
        return cliente

    # --------------------------------------------------------- 401 JSON a código
    def _assert_401_json(self, respuesta):
        self.assertEqual(respuesta.status_code, 401)
        self.assertTrue(respuesta.is_json)
        datos = respuesta.get_json()
        self.assertIs(datos["ok"], False)
        self.assertEqual(datos["codigo"], "sesion_vencida")
        self.assertTrue(datos["login"].startswith("/auth/login"))
        return datos

    def test_ajax_sin_sesion_responde_401_json(self):
        self._assert_401_json(self.app.test_client().get("/dashboard/cambios?desde=0", headers=AJAX))

    def test_fetch_sin_cabeceras_propias_tambien_recibe_401_json(self):
        cliente = self.app.test_client()
        self._assert_401_json(cliente.get("/dashboard/cambios?desde=0", headers=FETCH_SIN_CABECERAS))
        self._assert_401_json(cliente.get("/dashboard/cambios?desde=0", headers={"Accept": "application/json"}))
        self._assert_401_json(cliente.post("/pedidos/vista-previa", json={}))

    def test_401_json_lleva_login_con_la_pagina_abierta_como_next(self):
        cliente = self.app.test_client()
        datos = self._assert_401_json(cliente.get(
            "/dashboard/cambios?desde=0", headers={**AJAX, "Referer": "http://localhost/dashboard?x=1"}))
        self.assertEqual(parse_qs(urlsplit(datos["login"]).query)["next"], ["/dashboard?x=1"])   # no la URL del fetch
        # Referer de otro sitio, o ausente: sin next.
        datos = self._assert_401_json(cliente.get(
            "/dashboard/cambios?desde=0", headers={**AJAX, "Referer": "https://evil.example/dashboard"}))
        self.assertNotIn("next", datos["login"])
        datos = self._assert_401_json(cliente.get("/dashboard/cambios?desde=0", headers=AJAX))
        self.assertNotIn("next", datos["login"])

    def test_usuario_desactivado_con_cookie_vieja_tambien_recibe_401(self):
        baja = self._usuario("Baja", "baja@x.test", "Produccion", activo=False)
        self._assert_401_json(self._con_sesion(baja).get("/dashboard/cambios?desde=0", headers=AJAX))

    # ------------------------------------------------------ 302 a navegación normal
    def test_navegacion_sin_sesion_redirige_al_login_con_next(self):
        cliente = self.app.test_client()
        respuesta = cliente.get("/dashboard", headers=NAVEGACION)
        self.assertEqual(respuesta.status_code, 302)
        destino = urlsplit(respuesta.headers["Location"])
        self.assertEqual(destino.path, "/auth/login")
        self.assertEqual(parse_qs(destino.query)["next"], ["/dashboard"])
        respuesta = cliente.get("/dashboard?x=1", headers=NAVEGACION)
        self.assertEqual(parse_qs(urlsplit(respuesta.headers["Location"]).query)["next"], ["/dashboard?x=1"])
        # Sin cabeceras de navegador (cliente de pruebas, curl): también navegación.
        self.assertEqual(cliente.get("/dashboard").status_code, 302)

    def test_post_de_formulario_sin_sesion_redirige_sin_next(self):
        respuesta = self.app.test_client().post("/pedidos/1/cancelar", headers=NAVEGACION)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta.headers["Location"], "/auth/login")

    # ------------------------------------------------------------------ denegar()
    def test_denegar_responde_403_json_a_fetch_con_o_sin_cabecera(self):
        cliente = self._con_sesion(self.prod)
        for cabeceras in (AJAX, FETCH_SIN_CABECERAS, {"Accept": "application/json"}):
            respuesta = cliente.post("/pedidos/vista-previa", headers=cabeceras, json={})
            self.assertEqual(respuesta.status_code, 403, cabeceras)
            self.assertTrue(respuesta.is_json)
            self.assertIs(respuesta.get_json()["ok"], False)
            self.assertFalse(respuesta.get_json()["success"])
        # Navegación: sigue siendo aviso y vuelta al tablero.
        respuesta = cliente.post("/pedidos/vista-previa", headers=NAVEGACION)
        self.assertEqual(respuesta.status_code, 302)

    def test_403_de_pagina_estricta_con_fetch_es_json(self):
        respuesta = self._con_sesion(self.prod).get("/configuracion/procesos", headers=FETCH_SIN_CABECERAS)
        self.assertEqual(respuesta.status_code, 403)
        self.assertTrue(respuesta.is_json)

    # --------------------------------------------------------------- next seguro
    def test_destino_seguro_rechaza_externos_y_barras_invertidas(self):
        malos = ["//evil.com", "/\\evil.com", "/\\/evil.com", "\\evil.com", "http://evil.com", "https://evil.com/x",
                 "evil.com", "javascript:alert(1)", "/ok\\malo", "/a\r\nSet-Cookie: x=1", "/\t/evil.com",
                 "/a\x00b", "", None, "dashboard", "///evil.com", "/%2F/evil.com/..\\"]
        with self.app.test_request_context():
            for destino in malos[:-1]:
                self.assertEqual(_destino_seguro(destino), "/dashboard", repr(destino))
        for bueno in ("/dashboard", "/dashboard?x=1&y=2", "/pedidos/ficha/AC-1", "/productos/?q=a%20b"):
            self.assertTrue(es_destino_interno(bueno), bueno)
            with self.app.test_request_context():
                self.assertEqual(_destino_seguro(bueno), bueno)

    def test_login_ignora_next_malicioso(self):
        for malo in ("//evil.com", "/\\evil.com", "http://evil.com", "/\\/evil.com"):
            cliente = self.app.test_client()
            respuesta = cliente.post("/auth/login", query_string={"next": malo},
                                     data={"correo": "admin@x.test", "password": "clave-123"})
            self.assertEqual(respuesta.status_code, 302, malo)
            self.assertEqual(urlsplit(respuesta.headers["Location"]).path, "/dashboard", malo)
            self.assertNotIn("evil", respuesta.headers["Location"])

    def test_login_respeta_next_interno(self):
        cliente = self.app.test_client()
        respuesta = cliente.post("/auth/login", query_string={"next": "/dashboard?x=1"},
                                 data={"correo": "admin@x.test", "password": "clave-123"})
        self.assertEqual(respuesta.headers["Location"], "/dashboard?x=1")

    # ---------------------------------------------------- polls sin Set-Cookie (B2)
    def test_poll_con_sesion_no_manda_set_cookie(self):
        db.session.add(Bitacora(accion="PEDIDO_EDITADO", entidad="pedido", id_entidad=1))
        db.session.commit()
        for usuario_ in (self.admin, self.prod):
            cliente = self._con_sesion(usuario_)
            for _ in range(2):
                respuesta = cliente.get("/dashboard/cambios?desde=0", headers=AJAX)
                self.assertEqual(respuesta.status_code, 200)
                self.assertNotIn("Set-Cookie", respuesta.headers, usuario_.rol)
            respuesta = cliente.get("/notificaciones/resumen", headers=AJAX)
            self.assertEqual(respuesta.status_code, 200)
            self.assertNotIn("Set-Cookie", respuesta.headers)

    def test_la_sesion_ya_no_guarda_usuario_rol(self):
        cliente = self.app.test_client()
        cliente.post("/auth/login", data={"correo": "admin@x.test", "password": "clave-123"})
        with cliente.session_transaction() as sesion:
            self.assertIn("usuario_id", sesion)
            self.assertNotIn("usuario_rol", sesion)

    # ------------------------------------------------ caché de usuario en login/logout (M5)
    def test_login_descarta_la_cache_del_usuario(self):
        reactivable = self._usuario("Vuelve", "vuelve@x.test", "Administrador", activo=False)
        cliente = self._con_sesion(reactivable)
        self.assertEqual(cliente.get("/dashboard/cambios?desde=0", headers=AJAX).status_code, 401)  # queda cacheado inactivo
        reactivable.activo = True            # lo reactiva otro proceso (p. ej. `flask crear-admin`): sin invalidar_usuario()
        db.session.commit()
        cliente = self.app.test_client()
        respuesta = cliente.post("/auth/login", data={"correo": "vuelve@x.test", "password": "clave-123"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(cliente.get("/dashboard/cambios?desde=0", headers=AJAX).status_code, 200)

    def test_logout_descarta_la_cache_del_usuario(self):
        cliente = self._con_sesion(self.admin)
        cliente.get("/dashboard/cambios?desde=0", headers=AJAX)
        self.assertIn(self.admin.id_usuario, permisos._cache_usuarios)
        cliente.post("/auth/logout")
        self.assertNotIn(self.admin.id_usuario, permisos._cache_usuarios)
        self._assert_401_json(cliente.get("/dashboard/cambios?desde=0", headers=AJAX))


    # ----------------------------------------------- cliente: un solo envoltorio de fetch
    # base.html trae UN envoltorio global y las páginas ya no navegan a respuesta.url.
    def test_base_trae_el_envoltorio_antes_de_cualquier_otro_script(self):
        html = self._con_sesion(self.admin).get("/dashboard", headers=NAVEGACION).get_data(as_text=True)
        self.assertIn("window.fetch = function (entrada, opciones)", html)
        self.assertIn("resp.status === 401 || haciaLogin", html)
        self.assertIn("encodeURIComponent(location.pathname + location.search)", html)   # next = página actual
        self.assertIn("resp.status === 403", html)
        self.assertLess(html.index("window.fetch = function"), html.index("window.pfModo"))
        self.assertLess(html.index("window.fetch = function"), html.index("tablero_sync.js"))

    def test_ninguna_pagina_navega_a_respuesta_url(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parent.parent
        for ruta in ("templates/dashboard.html", "templates/productos/listas.html", "static/js/tablero_sync.js"):
            texto = (raiz / ruta).read_text(encoding="utf-8")
            self.assertNotIn("respuesta.url", texto, ruta)

    def test_poll_deja_de_reintentar_ante_403_y_stickers_rechaza_html(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parent.parent
        sync = (raiz / "static/js/tablero_sync.js").read_text(encoding="utf-8")
        self.assertIn("if (respuesta.status === 403) {", sync)
        stickers = (raiz / "static/stickers/stickers.js").read_text(encoding="utf-8")
        self.assertIn("includes('text/html')", stickers)


if __name__ == "__main__":
    unittest.main()
