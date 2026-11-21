import unittest
from datetime import date, datetime
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, cliente, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401  # registran las tablas
from modelos.bitacora import Bitacora
from modelos.cliente import Cliente
from modelos.pedido import DetallePedido, Pago, Pedido
from modelos.usuario import Usuario
from rutas.autenticacion import autenticacion_bp
from rutas.caja import caja_bp
from rutas.clientes import clientes_bp, estadisticas_cliente
from rutas.configuracion import configuracion_bp
from rutas.dashboard import dashboard_bp
from rutas.notificaciones import notificaciones_bp
from rutas.pedidos import pedidos_bp
from rutas.productos import productos_bp
from rutas.reportes import reportes_bp
from rutas.stickers import stickers_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios.permisos import registrar_seguridad
from servicios.textos import enlazar

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _date_trunc_sqlite(campo, valor):
    """date_trunc('month', ...) de PostgreSQL para SQLite."""
    if valor is None:
        return None
    assert campo == "month"
    return f"{valor[:7]}-01 00:00:00.000000"


def _translate_sqlite(texto, origen, destino):
    """translate() de PostgreSQL para SQLite."""
    return texto.translate(str.maketrans(origen, destino)) if texto is not None else None


def _funciones_sqlite(conexion, _):
    conexion.create_function("date_trunc", 2, _date_trunc_sqlite)
    conexion.create_function("translate", 3, _translate_sqlite)


class ClientesFichaTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        self.app.jinja_env.filters["enlazar"] = enlazar
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
        self.app_context = self.app.app_context()
        self.app_context.push()
        event.listen(db.engine, "connect", _funciones_sqlite)
        db.create_all()
        self.client = self.app.test_client()

        self.admin = self._crear_usuario("Admin", "admin@example.test", "Administrador")
        self.recepcion = self._crear_usuario("Recepción", "recepcion@example.test", "Recepcion")
        self.cliente = Cliente(nombre="Escuela Benito", telefono="8111111111", tipo="Escuela")
        self.otro = Cliente(nombre="Otra Persona", telefono="8122222222")
        db.session.add_all([self.cliente, self.otro])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # ------------------------------------------------------------ utilidades
    def _crear_usuario(self, nombre, correo, rol):
        u = Usuario(nombre=nombre, correo=correo, rol=rol, activo=True,
                    password_hash=generate_password_hash("x", method="pbkdf2:sha256"))
        db.session.add(u)
        db.session.commit()
        return u

    def _iniciar_sesion(self, usuario):
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = usuario.id_usuario

    def _pedido(self, folio, estatus, total, fecha=None, productos=(), pagos=(), cliente=None):
        fecha = fecha or datetime(2026, 10, 15, 10, 0)
        p = Pedido(
            folio=folio, nombre_cliente="x", telefono_cliente="8111111111",
            id_cliente=(cliente or self.cliente).id_cliente,
            fecha_creacion=fecha, fecha_entrega=fecha, estatus=estatus, total=total,
        )
        for cantidad, descripcion in productos:
            p.detalles.append(DetallePedido(cantidad=cantidad, descripcion=descripcion,
                                            precio_unitario=1, subtotal=cantidad))
        for monto in pagos:
            p.pagos.append(Pago(monto=monto, metodo_pago="Efectivo", fecha_pago=fecha))
        db.session.add(p)
        db.session.commit()
        return p

    def _url(self, sufijo=""):
        return f"/clientes/{self.cliente.id_cliente}{sufijo}"

    def _datos_edicion(self, **cambios):
        datos = {"nombre": "Escuela Benito Juárez", "telefono": "81 1111-1111", "tipo": "Escuela",
                 "correo": "escuela@example.test", "rfc": "ebj010101abc", "razon_social": "",
                 "notas": "", "activo": "1"}
        datos.update(cambios)
        return datos

    @staticmethod
    def _seccion(html, inicio, fin=None):
        resto = html[html.index(inicio):]
        return resto[:resto.index(fin)] if fin and fin in resto else resto

    # ------------------------------------------------------------ permisos
    def test_recepcion_no_recibe_estadisticas_ni_consulta(self):
        self._iniciar_sesion(self.recepcion)
        with patch("rutas.clientes.estadisticas_cliente") as stats:
            respuesta = self.client.get(self._url())
        html = respuesta.get_data(as_text=True)

        self.assertEqual(respuesta.status_code, 200)
        stats.assert_not_called()
        self.assertNotIn("panel-estadisticas", html)
        self.assertNotIn("Estadísticas", html)
        self.assertNotIn("Total comprado", html)
        self.assertNotIn("editarCliente-", html)
        self.assertIn("Completar datos", html)
        self.assertIn("Nota de pedidos activos", html)

    def test_recepcion_no_puede_editar(self):
        self._iniciar_sesion(self.recepcion)
        respuesta = self.client.post(self._url("/editar"), data=self._datos_edicion(), headers=AJAX)
        self.assertEqual(respuesta.status_code, 403)

        # Sin AJAX el decorador redirige, pero tampoco guarda nada.
        respuesta = self.client.post(self._url("/editar"), data=self._datos_edicion())
        self.assertEqual(respuesta.status_code, 302)

        db.session.expire_all()
        self.assertEqual(db.session.get(Cliente, self.cliente.id_cliente).nombre, "ESCUELA BENITO")
        self.assertEqual(Bitacora.query.filter_by(accion="CLIENTE_EDITAR").count(), 0)
        self.assertEqual(Bitacora.query.filter_by(accion="ACCESO_DENEGADO").count(), 2)

    def test_administrador_ve_estadisticas_y_boton_editar(self):
        self._iniciar_sesion(self.admin)
        respuesta = self.client.get(self._url())
        html = respuesta.get_data(as_text=True)

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('id="panel-estadisticas"', html)
        self.assertIn("Total comprado", html)
        self.assertIn('id="editarCliente-', html)

    def test_administrador_edita_y_registra_bitacora(self):
        self._iniciar_sesion(self.admin)
        respuesta = self.client.post(self._url("/editar"), data=self._datos_edicion(), headers=AJAX)

        self.assertEqual(respuesta.status_code, 200, respuesta.get_data(as_text=True))
        db.session.expire_all()
        c = db.session.get(Cliente, self.cliente.id_cliente)
        self.assertEqual(c.nombre, "ESCUELA BENITO JUÁREZ")
        self.assertEqual(c.telefono, "8111111111")
        self.assertEqual(c.rfc, "EBJ010101ABC")
        self.assertEqual(c.correo, "escuela@example.test")
        registro = Bitacora.query.filter_by(accion="CLIENTE_EDITAR").one()
        self.assertIn("nombre: ESCUELA BENITO → ESCUELA BENITO JUÁREZ", registro.detalle)
        self.assertIn("rfc", registro.detalle)
        self.assertNotIn("telefono", registro.detalle)   # mismo número, solo con separadores
        self.assertNotIn("tipo", registro.detalle)

    def test_editar_rechaza_telefono_de_otro_cliente_y_limites(self):
        self._iniciar_sesion(self.admin)
        duplicado = self.client.post(self._url("/editar"),
                                     data=self._datos_edicion(telefono="8122222222"), headers=AJAX)
        self.assertEqual(duplicado.status_code, 409)

        largo = self.client.post(self._url("/editar"),
                                 data=self._datos_edicion(nombre="x" * 121), headers=AJAX)
        self.assertEqual(largo.status_code, 400)

        corto = self.client.post(self._url("/editar"),
                                 data=self._datos_edicion(telefono="12345"), headers=AJAX)
        self.assertEqual(corto.status_code, 400)

        db.session.expire_all()
        self.assertEqual(db.session.get(Cliente, self.cliente.id_cliente).telefono, "8111111111")
        self.assertEqual(Bitacora.query.filter_by(accion="CLIENTE_EDITAR").count(), 0)

    # ------------------------------------------------------------ normalización
    def test_modelo_normaliza_campos(self):
        c = Cliente(nombre="  josé   pérez ", telefono="81 3333-3333", correo=" Jose@Example.TEST ",
                    rfc="pepj-800101-ab1", razon_social=" papelería  pérez ", notas="  hola  ")
        db.session.add(c)
        db.session.commit()
        self.assertEqual(c.nombre, "JOSÉ PÉREZ")
        self.assertEqual(c.telefono, "8133333333")
        self.assertEqual(c.correo, "jose@example.test")
        self.assertEqual(c.rfc, "PEPJ800101AB1")
        self.assertEqual(c.razon_social, "PAPELERÍA PÉREZ")
        self.assertEqual(c.notas, "hola")

    def test_pedido_normaliza_nombre_cliente(self):
        p = self._pedido("NORM-1", "En proceso", 10)
        p.nombre_cliente = "  maría   peña "
        db.session.commit()
        self.assertEqual(p.nombre_cliente, "MARÍA PEÑA")

    def test_editar_acepta_rfc_con_guiones_y_sin_cambios_falsos(self):
        self._iniciar_sesion(self.admin)
        datos = self._datos_edicion(rfc="ebj-010101-abc", correo="ESCUELA@EXAMPLE.TEST")
        self.assertEqual(self.client.post(self._url("/editar"), data=datos, headers=AJAX).status_code, 200)
        db.session.expire_all()
        c = db.session.get(Cliente, self.cliente.id_cliente)
        self.assertEqual(c.rfc, "EBJ010101ABC")
        self.assertEqual(c.correo, "escuela@example.test")

        # Mismos datos con otra capitalización: no cuenta como cambio.
        datos = self._datos_edicion(nombre="escuela  benito juárez", rfc="EBJ 010101 ABC")
        respuesta = self.client.post(self._url("/editar"), data=datos, headers=AJAX)
        self.assertEqual(respuesta.get_json()["cambios"], [])

    def test_completar_trata_espacios_como_vacio(self):
        self.cliente.correo = "   "   # el validador lo deja en None
        db.session.commit()
        self.assertIsNone(self.cliente.correo)
        self._iniciar_sesion(self.recepcion)
        respuesta = self.client.post(self._url("/completar"),
                                     data={"correo": " Nuevo@Example.TEST ", "rfc": "  "},
                                     headers=AJAX)
        self.assertEqual(respuesta.get_json()["actualizados"], ["correo"])
        db.session.expire_all()
        self.assertEqual(db.session.get(Cliente, self.cliente.id_cliente).correo, "nuevo@example.test")

    def test_buscar_sin_acentos_encuentra_nombre_en_mayusculas(self):
        db.session.add(Cliente(nombre="josé pérez", telefono="8144444444"))
        db.session.commit()
        self._iniciar_sesion(self.recepcion)
        for q in ("jose", "JOSÉ", "perez"):
            nombres = [c["nombre"] for c in self.client.get(f"/clientes/buscar?q={q}").get_json()]
            self.assertIn("JOSÉ PÉREZ", nombres, q)

    # ------------------------------------------------------------ pestañas
    def test_historial_excluye_activos(self):
        self._pedido("ACT-PROC", "En proceso", 100, pagos=[30])
        self._pedido("ACT-TERM", "Terminado", 50)
        self._pedido("HIS-ENTR", "Entregado", 80, fecha=datetime(2026, 9, 1))
        self._pedido("HIS-CANC", "Cancelado", 40, fecha=datetime(2026, 10, 1))
        self._iniciar_sesion(self.recepcion)

        html = self.client.get(self._url()).get_data(as_text=True)
        activos = self._seccion(html, 'id="panel-activos"', 'id="panel-historial"')
        historial = self._seccion(html, 'id="panel-historial"')

        self.assertIn("ACT-PROC", activos)
        self.assertIn("ACT-TERM", activos)
        self.assertIn("$150.00", activos)   # total
        self.assertIn("$30.00", activos)    # pagado
        self.assertIn("$120.00", activos)   # saldo por liquidar
        self.assertNotIn("HIS-", activos)
        self.assertNotIn("ACT-", historial)
        # Más reciente primero.
        self.assertLess(historial.index("HIS-CANC"), historial.index("HIS-ENTR"))

    def test_historial_pagina_de_20(self):
        for i in range(25):
            self._pedido(f"H{i:03d}", "Entregado", 10, fecha=datetime(2026, 1, 1, 0, i))
        self._iniciar_sesion(self.recepcion)

        pagina1 = self._seccion(self.client.get(self._url()).get_data(as_text=True), 'id="panel-historial"')
        pagina2 = self._seccion(self.client.get(self._url("?pagina=2")).get_data(as_text=True),
                                'id="panel-historial"')

        self.assertEqual(pagina1.count('<td class="fw-bold text-nowrap">H'), 20)
        self.assertIn("H024", pagina1)
        self.assertEqual(pagina2.count('<td class="fw-bold text-nowrap">H'), 5)
        self.assertIn("H000", pagina2)

    def test_estadisticas_excluyen_cancelados(self):
        self._pedido("E1", "Entregado", 100, fecha=datetime(2026, 10, 3),
                     productos=[(3, "Playera"), (1, "Taza")])
        self._pedido("E2", "En proceso", 50, fecha=datetime(2026, 8, 20),
                     productos=[(2, "Playera")], pagos=[20])
        self._pedido("E3", "Cancelado", 1000, fecha=datetime(2026, 10, 25),
                     productos=[(99, "Gorra")])
        self._pedido("E4", "Cancelado", 70, fecha=datetime(2026, 9, 5), pagos=[10])
        self._pedido("OTRO", "Entregado", 500, cliente=self.otro, productos=[(50, "Lona")])

        stats = estadisticas_cliente(self.cliente.id_cliente, hoy=date(2026, 10, 31))

        self.assertEqual(stats["total_comprado"], 150.0)
        self.assertEqual(stats["num_pedidos"], 2)
        self.assertEqual(stats["ticket_promedio"], 75.0)
        self.assertEqual(stats["saldo_activos"], 30.0)
        self.assertEqual(stats["ultimo_pedido"], datetime(2026, 10, 3))
        self.assertEqual([p["descripcion"] for p in stats["top_productos"]], ["Playera", "Taza"])
        self.assertEqual(stats["top_productos"][0]["cantidad"], 5.0)

        mensual = stats["mensual"]
        self.assertEqual(len(mensual), 12)
        self.assertEqual(mensual[0]["etiqueta"], "Nov 2025")
        self.assertEqual(mensual[-1]["etiqueta"], "Oct 2026")
        self.assertEqual((mensual[-1]["pedidos"], mensual[-1]["monto"]), (1, 100.0))
        self.assertEqual((mensual[-2]["pedidos"], mensual[-2]["monto"]), (0, 0.0))   # sep: solo cancelado
        self.assertEqual((mensual[-3]["pedidos"], mensual[-3]["monto"]), (1, 50.0))
        self.assertEqual(sum(m["pedidos"] for m in mensual), 2)
        self.assertEqual(mensual[-1]["porcentaje"], 100.0)

    def test_estadisticas_cliente_sin_pedidos(self):
        stats = estadisticas_cliente(self.cliente.id_cliente, hoy=date(2026, 1, 15))
        self.assertEqual(stats["num_pedidos"], 0)
        self.assertEqual(stats["ticket_promedio"], 0.0)
        self.assertIsNone(stats["ultimo_pedido"])
        self.assertEqual(stats["mensual"][0]["etiqueta"], "Feb 2025")
        self.assertTrue(all(m["monto"] == 0 for m in stats["mensual"]))
        self.assertEqual(stats["top_productos"], [])

    # ------------------------------------------------------------ nota de activos
    def _configurar_apartados(self):
        conf = configuracion_nota.obtener_configuracion_nota()
        visible = {"nota": True, "ticket": False, "whatsapp": False}
        conf.apartados = [
            {"titulo": "Horario", "activo": True, "mostrar_en": visible, "condicion": "siempre",
             "bloques": [{"subtitulo": "Lunes a viernes", "texto": "9 a 7\nSábado 9 a 2"}]},
            {"titulo": "Métodos de pago", "activo": True, "mostrar_en": visible, "condicion": "con_saldo",
             "bloques": [{"subtitulo": "Transferencia",
                          "texto": "{cliente} ({folio}) debe {saldo} de {total} <b>x</b>"}]},
        ]
        db.session.commit()

    def test_nota_activos_con_saldo_muestra_apartados_con_totales(self):
        self._configurar_apartados()
        self._pedido("A-1", "En proceso", 300, pagos=(100,))
        self._pedido("A-2", "Terminado", 1000, pagos=(450,))
        self._iniciar_sesion(self.admin)
        html = self.client.get(self._url("/nota-activos")).get_data(as_text=True)

        apartados = self._seccion(html, '<section class="apartados">', "</section>")
        self.assertIn("HORARIO", apartados)
        self.assertIn("MÉTODOS DE PAGO", apartados)
        self.assertIn(f"ESCUELA BENITO ({self.cliente.clave}) debe $750.00 de $1,300.00", apartados)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", apartados)   # Jinja escapa el texto
        self.assertLess(html.index('class="totales"'), html.index('class="apartados"'))
        self.assertLess(html.index('class="apartados"'), html.index('class="politicas"'))

    def test_nota_activos_sin_saldo_solo_muestra_horario(self):
        self._configurar_apartados()
        self._pedido("A-1", "Terminado", 300, pagos=(300,))
        self._iniciar_sesion(self.admin)
        html = self.client.get(self._url("/nota-activos")).get_data(as_text=True)

        apartados = self._seccion(html, '<section class="apartados">', "</section>")
        self.assertIn("HORARIO", apartados)
        self.assertNotIn("MÉTODOS DE PAGO", apartados)

    def test_nota_activos_si_fallan_apartados_no_se_rompe(self):
        self._iniciar_sesion(self.admin)
        with patch("rutas.clientes.apartados_para", side_effect=RuntimeError("x")):
            respuesta = self.client.get(self._url("/nota-activos"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('class="apartados"', respuesta.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
