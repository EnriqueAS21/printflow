import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, cliente, configuracion_nota, envios, notificacion, pedido, perfil_usuario, producto, usuario  # noqa: F401
from modelos.cliente import Cliente
from modelos.envios import nuevo_token
from modelos.pedido import (
    DetallePedido, Pedido, REGIMENES_FISCALES, TASA_COMISION_TARJETA, TASA_IVA, USOS_CFDI,
)
from modelos.producto import ListaPrecio, Presentacion, Producto
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
from servicios import permisos
from servicios.textos import enlazar

APAGADOS = {"clientes", "productos", "stickers"}
ROLES = ("Administrador", "Recepcion", "Produccion")
ENLACES = ('href="/clientes', 'href="/productos', 'href="/stickers')
RASTROS_FORMULARIO = (
    "data-url-buscar", "data-url-cotizar", "data-puede-usar-productos",
    "pf-producto-descripcion", 'name="guardar_cliente"', "buscador_productos.js",
)


class ModulosDesactivadosTestCase(unittest.TestCase):
    def setUp(self):
        # Independiente del .env; la secuencia de folios solo existe en PostgreSQL.
        for parche in (
            patch.object(permisos, "MODULOS_DESACTIVADOS", set()),
            patch("rutas.pedidos.previsualizar_siguiente_folio", return_value="F-2"),
            patch("rutas.pedidos.generar_siguiente_folio", return_value="F-2"),
            patch("rutas.caja.previsualizar_siguiente_folio", return_value="F-2"),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        self.app.jinja_env.filters["enlazar"] = enlazar
        self.app.jinja_env.globals.update(
            TASA_IVA=TASA_IVA, TASA_COMISION_TARJETA=TASA_COMISION_TARJETA,
            REGIMENES_FISCALES=REGIMENES_FISCALES, USOS_CFDI=USOS_CFDI,
        )
        db.init_app(self.app)
        permisos.registrar_seguridad(self.app)
        for bp, prefijo in (
            (usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (pedidos_bp, "/pedidos"),
            (autenticacion_bp, "/auth"), (dashboard_bp, "/dashboard"), (caja_bp, "/caja"),
            (configuracion_bp, "/configuracion"), (reportes_bp, "/reportes"),
            (stickers_bp, "/stickers"), (notificaciones_bp, "/notificaciones"),
            (clientes_bp, "/clientes"), (productos_bp, "/productos"),
        ):
            self.app.register_blueprint(bp, url_prefix=prefijo)
        self.contexto = self.app.app_context()
        self.contexto.push()
        db.create_all()
        self.client = self.app.test_client()

        self.usuarios = {}
        for rol in ROLES:
            u = Usuario(nombre=rol, correo=f"{rol}@example.test", rol=rol, activo=True,
                        password_hash=generate_password_hash("x", method="pbkdf2:sha256"))
            db.session.add(u)
            self.usuarios[rol] = u
        self.cliente_viejo = Cliente(nombre="CLIENTE VIEJO", telefono="8111111111")
        self.lista = ListaPrecio(nombre="Mayoreo")
        self.producto = Producto(nombre="Lona")
        db.session.add_all([self.cliente_viejo, self.lista, self.producto])
        db.session.flush()
        self.presentacion = Presentacion(id_producto=self.producto.id_producto, nombre="1x2", forma="fija")
        db.session.add(self.presentacion)
        db.session.flush()
        fecha = datetime(2026, 10, 15, 10, 0)
        self.pedido = Pedido(
            folio="F-1", nombre_cliente="CLIENTE VIEJO", telefono_cliente="8111111111",
            id_cliente=self.cliente_viejo.id_cliente, id_lista_precio=self.lista.id_lista,
            fecha_creacion=fecha, fecha_entrega=fecha, estatus="En proceso", total=10,
        )
        self.pedido.detalles.append(DetallePedido(
            cantidad=1, descripcion="LONA", precio_unitario=10, subtotal=10,
            id_producto=self.producto.id_producto, id_presentacion=self.presentacion.id_presentacion,
            configuracion={"ancho": 2},
        ))
        db.session.add(self.pedido)
        db.session.flush()
        self.pedido.responsables = [self.usuarios["Produccion"]]
        self.pedido.id_usuario_produccion = self.usuarios["Produccion"].id_usuario
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.contexto.pop()

    def _entrar(self, rol):
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = self.usuarios[rol].id_usuario

    def _apagados(self, modulos=APAGADOS):
        return patch.object(permisos, "MODULOS_DESACTIVADOS", set(modulos))

    def _html(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.get_data(as_text=True)

    def _datos_pedido(self, **cambios):
        datos = {
            "token_envio": nuevo_token(), "nombre_cliente": "Cliente Libre",
            "telefono_cliente": "8122222222", "fecha_entrega": "2026-12-01", "hora_entrega": "10:00",
            "cantidad[]": "1", "descripcion[]": "Fila libre", "precio_unitario[]": "50",
            "detalle_id[]": "", "producto_id[]": "", "presentacion_id[]": "", "configuracion[]": "",
            "responsable_producto[]": "", "anticipo": "0", "metodo_pago": "Efectivo",
            "id_cliente": "", "id_lista_precio": "",
        }
        datos.update(cambios)
        return datos

    # ------------------------------------------------------- variable y permisos
    def test_normaliza_la_variable(self):
        self.assertEqual(permisos.leer_modulos_desactivados(" Productos, CLIENTES ,,stickers "), APAGADOS)
        self.assertEqual(permisos.leer_modulos_desactivados(None), set())
        self.assertEqual(permisos.leer_modulos_desactivados(""), set())

    def test_modulo_apagado_quita_permiso_a_todos_los_roles(self):
        with self._apagados():
            for u in self.usuarios.values():
                for p in ("clientes.ver", "productos.usar", "stickers.usar"):
                    self.assertFalse(permisos.tiene_permiso(p, u))
            self.assertTrue(permisos.tiene_permiso("caja.corte", self.usuarios["Administrador"]))

    def test_rutas_directas_responden_404(self):
        urls = ("/clientes/", "/productos/", "/stickers/")
        with self._apagados():
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 404)  # sin sesión
            for rol in ROLES:
                self._entrar(rol)
                for url in urls:
                    self.assertEqual(self.client.get(url).status_code, 404, (rol, url))
                    r = self.client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
                    self.assertEqual((r.status_code, r.get_json()), (404, {"error": "Módulo no disponible"}))
                r = self.client.get(url, headers={"Accept": "application/json"})
                self.assertEqual(r.get_json(), {"error": "Módulo no disponible"})

    # -------------------------------------------------------------- pantallas
    def test_pantallas_sin_enlaces_con_modulos_apagados(self):
        id_p = self.pedido.id_pedido
        pantallas = {
            "Administrador": ["/dashboard/", "/caja/", "/pedidos/nuevo", f"/pedidos/nuevo?editar={id_p}",
                              f"/pedidos/{id_p}/formulario-editar", "/pedidos/ficha/F-1", f"/pedidos/{id_p}/ticket",
                              "/reportes/ventas", "/reportes/productividad", "/reportes/cartera"],
            "Recepcion": ["/dashboard/", "/caja/", "/pedidos/nuevo", f"/pedidos/nuevo?editar={id_p}",
                          "/pedidos/ficha/F-1", f"/pedidos/{id_p}/ticket"],
            "Produccion": ["/dashboard/", "/pedidos/ficha/F-1"],
        }
        with self._apagados():
            for rol, urls in pantallas.items():
                self._entrar(rol)
                for url in urls:
                    html = self._html(url)
                    for enlace in ENLACES:
                        self.assertNotIn(enlace, html, (rol, url))
                    for rastro in RASTROS_FORMULARIO:
                        self.assertNotIn(rastro, html, (rol, url))
                    self.assertNotIn("data-url-buscar-clientes", html, (rol, url))

    def test_edicion_vieja_no_muestra_cliente_vinculado(self):
        self._entrar("Recepcion")
        with self._apagados():
            html = self._html(f"/pedidos/nuevo?editar={self.pedido.id_pedido}")
        self.assertNotIn("Cliente CL-", html)
        self.assertNotIn(f'name="id_cliente" value="{self.cliente_viejo.id_cliente}"', html)
        self.assertIn("LONA", html)

    def test_sin_variable_todo_sigue_visible(self):
        self._entrar("Administrador")
        html = self._html("/pedidos/nuevo")
        for texto in ('href="/clientes/', 'href="/productos/', 'href="/stickers/',
                      "data-url-buscar-clientes", "data-puede-usar-productos", 'name="guardar_cliente"'):
            self.assertIn(texto, html)
        self.assertIn("data-url-buscar", self._html("/caja/"))
        self.assertEqual(self.client.get("/clientes/").status_code, 200)
        self.assertEqual(self.client.get("/productos/").status_code, 200)
        self.assertEqual(self.client.get("/stickers/").status_code, 200)

        self._entrar("Recepcion")
        html = self._html(f"/pedidos/nuevo?editar={self.pedido.id_pedido}")
        self.assertIn(f'name="id_cliente" value="{self.cliente_viejo.id_cliente}"', html)
        self._entrar("Produccion")
        html = self._html("/dashboard/")
        self.assertIn('href="/stickers/', html)
        self.assertNotIn('href="/clientes/', html)

    # -------------------------------------------------------------- guardado
    def test_alta_con_fila_libre_ignora_cliente(self):
        self._entrar("Recepcion")
        with self._apagados():
            r = self.client.post("/pedidos/nuevo", data=self._datos_pedido(
                guardar_cliente="on", id_cliente=str(self.cliente_viejo.id_cliente),
                id_lista_precio=str(self.lista.id_lista),
            ))
        self.assertEqual(r.status_code, 302)
        nuevo = Pedido.query.filter_by(telefono_cliente="8122222222").one()
        self.assertIsNone(nuevo.id_cliente)
        self.assertIsNone(nuevo.id_lista_precio)
        self.assertEqual(Cliente.query.count(), 1)
        with self.client.session_transaction() as sesion:
            self.assertNotIn("cliente_por_completar", sesion)

    def test_edicion_vieja_conserva_cliente_lista_y_catalogo(self):
        id_detalle = self.pedido.detalles[0].id_detalle
        self.lista.activa = False  # si el modelo la maneja, no debe estorbar
        db.session.commit()
        self._entrar("Recepcion")
        with self._apagados():
            r = self.client.post(f"/pedidos/nuevo?editar={self.pedido.id_pedido}", data=self._datos_pedido(**{
                "descripcion[]": "LONA EDITADA", "detalle_id[]": str(id_detalle),
                "producto_id[]": str(self.producto.id_producto),
                "presentacion_id[]": str(self.presentacion.id_presentacion),
                "configuracion[]": '{"ancho": 2}',
            }))
        self.assertEqual(r.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.pedido.nombre_cliente, "CLIENTE LIBRE")
        self.assertEqual(self.pedido.id_cliente, self.cliente_viejo.id_cliente)
        self.assertEqual(self.pedido.id_lista_precio, self.lista.id_lista)
        detalle = self.pedido.detalles[0]
        self.assertEqual(detalle.descripcion, "LONA EDITADA")
        self.assertEqual(detalle.id_producto, self.producto.id_producto)
        self.assertEqual(detalle.configuracion["ancho"], 2)

    def test_sin_variable_editar_puede_desvincular_como_hoy(self):
        self._entrar("Recepcion")
        r = self.client.post(f"/pedidos/nuevo?editar={self.pedido.id_pedido}", data=self._datos_pedido())
        self.assertEqual(r.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(self.pedido.id_cliente)
        self.assertIsNone(self.pedido.id_lista_precio)

    def test_ticket_de_mostrador(self):
        self._entrar("Recepcion")
        with self._apagados(), patch("rutas.caja.abrir_cajon_o_avisar", return_value=True):
            r = self.client.post("/caja/ticket", headers={"X-Requested-With": "XMLHttpRequest"}, data={
                "token_envio": nuevo_token(), "metodo_pago": "Efectivo",
                "cantidad[]": "2", "precio_unitario[]": "15", "descripcion[]": "Copias",
            })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
