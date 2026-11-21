import json
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401
from modelos.bitacora import Bitacora
from modelos.envios import nuevo_token
from modelos.produccion import Proceso, ProductoPaso
from modelos.producto import ListaPrecio, Presentacion, PresentacionPrecio, Producto
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
from servicios import rutas_produccion
from servicios.permisos import registrar_seguridad


class RutaProductoTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        registrar_seguridad(self.app)
        for bp, prefijo in (
            (usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (pedidos_bp, "/pedidos"),
            (autenticacion_bp, "/auth"), (dashboard_bp, "/dashboard"), (caja_bp, "/caja"),
            (configuracion_bp, "/configuracion"), (reportes_bp, "/reportes"), (stickers_bp, "/stickers"),
            (notificaciones_bp, "/notificaciones"), (clientes_bp, "/clientes"), (productos_bp, "/productos"),
        ):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        for destino in ("rutas.caja.previsualizar_siguiente_folio", "rutas.pedidos.previsualizar_siguiente_folio"):
            parche = patch(destino, return_value="AC0001")
            parche.start()
            self.addCleanup(parche.stop)

        admin = Usuario(
            nombre="Admin", correo="admin@example.test", rol="Administrador", activo=True,
            password_hash=generate_password_hash("x", method="pbkdf2:sha256"),
        )
        db.session.add(admin)
        self.lista = ListaPrecio(nombre="Público", predeterminada=True, activo=True, orden=1)
        db.session.add(self.lista)
        self.sistema = Proceso(nombre="Sin clasificar", orden=99, activo=True, es_sistema=True)
        self.diseno = Proceso(nombre="Diseño", orden=1, activo=True)
        self.impresion = Proceso(nombre="Impresión", orden=2, activo=True)
        self.corte = Proceso(nombre="Corte", orden=3, activo=True)
        self.inactivo = Proceso(nombre="Viejo", orden=4, activo=False)
        db.session.add_all([self.sistema, self.diseno, self.impresion, self.corte, self.inactivo])
        db.session.commit()
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = admin.id_usuario
        rutas_produccion.invalidar()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
        rutas_produccion.invalidar()

    # ── utilidades ──
    def _producto(self, nombre, categoria="Lonas"):
        producto = Producto(nombre=nombre, categoria=categoria)
        db.session.add(producto)
        db.session.flush()
        presentacion = Presentacion(id_producto=producto.id_producto, nombre="Pieza", forma="fijo", orden=0, activo=True)
        db.session.add(presentacion)
        db.session.flush()
        db.session.add(PresentacionPrecio(
            id_presentacion=presentacion.id_presentacion, id_lista=self.lista.id_lista,
            cantidad_minima=0, precio=10,
        ))
        db.session.commit()
        return producto

    def _ruta(self, *pasos, principal=None):
        return {
            "pasos": [{"id_proceso": p.id_proceso, "opcional": o} for p, o in pasos],
            "id_proceso_principal": principal.id_proceso if principal else None,
        }

    def _datos(self, nombre="Lona", categoria="Lonas", ruta=None):
        datos = {
            "nombre": nombre, "categoria": categoria, "descripcion": "", "cobro_rollo": "m2_real",
            "presentaciones": [{
                "nombre": "Pieza", "forma": "fijo",
                "rangos": [{"desde": 1, "precios": {str(self.lista.id_lista): 10}}],
            }],
        }
        if ruta is not None:
            datos["ruta"] = ruta
        return datos

    def _guardar(self, datos, url="/productos/nuevo"):
        return self.client.post(url, data={"datos": json.dumps(datos), "token_envio": nuevo_token()})

    def _ruta_guardada(self, id_producto):
        producto = db.session.get(Producto, id_producto)
        db.session.refresh(producto)
        return [
            (p.id_proceso, p.opcional)
            for p in sorted(producto.rutas_produccion, key=lambda x: x.orden)
        ], producto.id_proceso_principal

    # ── guardar y leer ──
    def test_guardar_y_leer_ruta_en_alta_y_edicion(self):
        ruta = self._ruta((self.diseno, True), (self.impresion, False), principal=self.corte)
        respuesta = self._guardar(self._datos(ruta=ruta))
        self.assertEqual(respuesta.status_code, 302)
        producto = Producto.query.filter_by(nombre="LONA").one()
        self.assertEqual(
            self._ruta_guardada(producto.id_producto),
            ([(self.diseno.id_proceso, True), (self.impresion.id_proceso, False)], self.corte.id_proceso),
        )

        # Se lee en el formulario de edición y se reordena al editar.
        pagina = self.client.get(f"/productos/{producto.id_producto}/editar")
        self.assertEqual(pagina.status_code, 200)
        nueva = self._ruta((self.impresion, False), (self.diseno, False))
        self.assertEqual(self._guardar(self._datos(ruta=nueva), f"/productos/{producto.id_producto}/editar").status_code, 302)
        self.assertEqual(
            self._ruta_guardada(producto.id_producto),
            ([(self.impresion.id_proceso, False), (self.diseno.id_proceso, False)], None),
        )
        acciones = [b.detalle for b in Bitacora.query.filter_by(accion="PRODUCTO_EDITAR").all()]
        self.assertTrue(any("Ruta de producción" in (a or "") for a in acciones))

    def test_sin_clave_ruta_no_toca_la_ruta_existente(self):
        producto = self._producto("Lona")
        db.session.add(ProductoPaso(id_producto=producto.id_producto, orden=1, id_proceso=self.diseno.id_proceso))
        db.session.commit()
        datos = self._datos(nombre="Lona")
        datos["presentaciones"][0]["id_presentacion"] = producto.presentaciones[0].id_presentacion
        self.assertEqual(self._guardar(datos, f"/productos/{producto.id_producto}/editar").status_code, 302)
        self.assertEqual(self._ruta_guardada(producto.id_producto)[0], [(self.diseno.id_proceso, False)])

    # ── validaciones ──
    def test_validaciones_rechazan_y_no_guardan(self):
        casos = {
            "inactivo": self._ruta((self.inactivo, False)),
            "sistema": self._ruta((self.sistema, False)),
            "repetido": self._ruta((self.diseno, False), (self.diseno, True)),
            "principal_inactivo": self._ruta(principal=self.inactivo),
            "inexistente": {"pasos": [{"id_proceso": 9999}]},
        }
        for nombre, ruta in casos.items():
            with self.subTest(nombre):
                respuesta = self._guardar(self._datos(nombre=f"X {nombre}", ruta=ruta))
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(Producto.query.filter_by(nombre=f"X {nombre}".upper()).count(), 0)

    def test_maximo_ocho_pasos(self):
        extra = [Proceso(nombre=f"P{i}", orden=10 + i, activo=True) for i in range(8)]
        db.session.add_all(extra)
        db.session.commit()
        nueve = self._ruta(*[(p, False) for p in extra], (self.diseno, False))
        self.assertEqual(self._guardar(self._datos(ruta=nueve)).status_code, 400)
        ocho = self._ruta(*[(p, False) for p in extra])
        self.assertEqual(self._guardar(self._datos(ruta=ocho)).status_code, 302)

    # ── copiar ──
    def test_copiar_ruta_devuelve_solo_procesos_activos_sin_guardar(self):
        origen = self._producto("Origen")
        db.session.add_all([
            ProductoPaso(id_producto=origen.id_producto, orden=1, id_proceso=self.diseno.id_proceso, opcional=True),
            ProductoPaso(id_producto=origen.id_producto, orden=2, id_proceso=self.inactivo.id_proceso),
        ])
        origen.id_proceso_principal = self.corte.id_proceso
        db.session.commit()
        otro = self._producto("Otro")
        datos = self.client.get(f"/productos/{origen.id_producto}/ruta").get_json()
        self.assertEqual(datos["pasos"], [{"id_proceso": self.diseno.id_proceso, "opcional": True}])
        self.assertEqual(datos["id_proceso_principal"], self.corte.id_proceso)
        self.assertEqual(datos["omitidos"], 1)
        self.assertEqual(self._ruta_guardada(otro.id_producto), ([], None))  # copiar no guarda
        self.assertEqual(self.client.get("/productos/9999/ruta").status_code, 404)

    # ── aplicar a categoría ──
    def _escenario_categoria(self):
        fuente = self._producto("Fuente")
        db.session.add_all([
            ProductoPaso(id_producto=fuente.id_producto, orden=1, id_proceso=self.diseno.id_proceso, opcional=True),
            ProductoPaso(id_producto=fuente.id_producto, orden=2, id_proceso=self.impresion.id_proceso),
        ])
        sin_ruta = [self._producto(f"Libre {i}") for i in range(3)]
        con_pasos = self._producto("Con pasos")
        db.session.add(ProductoPaso(id_producto=con_pasos.id_producto, orden=1, id_proceso=self.corte.id_proceso))
        con_principal = self._producto("Con principal")
        con_principal.id_proceso_principal = self.corte.id_proceso
        otra_categoria = self._producto("Otra", categoria="Vinil")
        db.session.commit()
        return fuente, sin_ruta, con_pasos, con_principal, otra_categoria

    def test_conteos_de_categoria(self):
        fuente, *_ = self._escenario_categoria()
        datos = self.client.get(f"/productos/{fuente.id_producto}/aplicar-ruta").get_json()
        self.assertEqual((datos["total"], datos["sin_ruta"]), (5, 3))

    def test_aplicar_solo_sin_ruta(self):
        fuente, sin_ruta, con_pasos, con_principal, otra = self._escenario_categoria()
        respuesta = self.client.post(
            f"/productos/{fuente.id_producto}/aplicar-ruta", json={"modo": "sin_ruta"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()["aplicados"], 3)
        esperado = [(self.diseno.id_proceso, True), (self.impresion.id_proceso, False)]
        for producto in sin_ruta:
            self.assertEqual(self._ruta_guardada(producto.id_producto)[0], esperado)
        self.assertEqual(self._ruta_guardada(con_pasos.id_producto)[0], [(self.corte.id_proceso, False)])
        self.assertEqual(self._ruta_guardada(con_principal.id_producto), ([], self.corte.id_proceso))
        self.assertEqual(self._ruta_guardada(otra.id_producto), ([], None))
        registro = Bitacora.query.filter_by(accion="PRODUCTO_RUTA_CATEGORIA").one()
        self.assertIn("3 de 5", registro.detalle)

    def test_aplicar_sobrescribir_todas(self):
        fuente, sin_ruta, con_pasos, con_principal, otra = self._escenario_categoria()
        respuesta = self.client.post(f"/productos/{fuente.id_producto}/aplicar-ruta", json={"modo": "todas"})
        self.assertEqual(respuesta.get_json()["aplicados"], 5)
        esperado = [(self.diseno.id_proceso, True), (self.impresion.id_proceso, False)]
        for producto in (*sin_ruta, con_pasos, con_principal):
            self.assertEqual(self._ruta_guardada(producto.id_producto), (esperado, None))
        self.assertEqual(self._ruta_guardada(otra.id_producto), ([], None))
        # La fuente no cambia.
        self.assertEqual(self._ruta_guardada(fuente.id_producto)[0], esperado)

    def test_aplicar_errores(self):
        fuente, *_ = self._escenario_categoria()
        self.assertEqual(self.client.post(f"/productos/{fuente.id_producto}/aplicar-ruta", json={"modo": "x"}).status_code, 400)
        vacio = self._producto("Vacío", categoria="Otra cat")
        self.assertEqual(self.client.post(f"/productos/{vacio.id_producto}/aplicar-ruta", json={"modo": "todas"}).status_code, 400)

    def test_aplicar_requiere_permiso(self):
        fuente, *_ = self._escenario_categoria()
        recepcion = Usuario(
            nombre="Recep", correo="r@example.test", rol="Recepcion", activo=True,
            password_hash=generate_password_hash("x", method="pbkdf2:sha256"),
        )
        db.session.add(recepcion)
        db.session.commit()
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = recepcion.id_usuario
        respuesta = self.client.post(
            f"/productos/{fuente.id_producto}/aplicar-ruta", json={"modo": "todas"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(respuesta.status_code, 403)

    # ── filtro «Sin ruta» y lista ──
    def test_filtro_sin_ruta_y_resumen(self):
        fuente, sin_ruta, con_pasos, con_principal, _otra = self._escenario_categoria()
        db.session.commit()
        pagina = self.client.get("/productos/?ruta=sin").get_data(as_text=True)
        for producto in sin_ruta:
            self.assertIn(producto.nombre, pagina)
        for producto in (fuente, con_pasos, con_principal):
            self.assertNotIn(f">{producto.nombre}<", pagina)
        completa = self.client.get("/productos/").get_data(as_text=True)
        self.assertIn("DISEÑO*" if False else "Diseño* → Impresión", completa)
        self.assertIn("Principal: Corte", completa)

    def test_lista_sin_ruta_sin_n_mas_uno(self):
        self._escenario_categoria()
        from sqlalchemy import event
        consultas = []
        evento = lambda conn, cursor, statement, *a: consultas.append(statement)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", evento)
        try:
            self.client.get("/productos/")
        finally:
            event.remove(db.engine, "before_cursor_execute", evento)
        rutas = [c for c in consultas if "producto_pasos" in c]
        self.assertLessEqual(len(rutas), 2)

    # ── caché ──
    def test_guardar_ruta_invalida_cache(self):
        producto = self._producto("Lona cache")
        self.assertEqual(rutas_produccion.ruta_para(producto.id_producto, "")[0][1], self.sistema.id_proceso)
        ruta = self._ruta((self.impresion, False))
        datos = self._datos(nombre="Lona cache", ruta=ruta)
        datos["presentaciones"][0]["id_presentacion"] = producto.presentaciones[0].id_presentacion
        self._guardar(datos, f"/productos/{producto.id_producto}/editar")
        self.assertEqual(rutas_produccion.ruta_para(producto.id_producto, ""), [(1, self.impresion.id_proceso, False)])

    def test_aplicar_a_categoria_invalida_cache(self):
        fuente, sin_ruta, *_ = self._escenario_categoria()
        rutas_produccion.cargar_cache()
        self.assertEqual(rutas_produccion.ruta_para(sin_ruta[0].id_producto, "")[0][1], self.sistema.id_proceso)
        self.client.post(f"/productos/{fuente.id_producto}/aplicar-ruta", json={"modo": "sin_ruta"})
        self.assertEqual(rutas_produccion.ruta_para(sin_ruta[0].id_producto, "")[0][1], self.diseno.id_proceso)


if __name__ == "__main__":
    unittest.main()
