"""Bloque 1: filtros del tablero por rol (sin nombres de otros operadores a Producción) y operadores activos."""
import re
import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, caja, configuracion_nota, envios, notificacion, pedido, perfil_usuario, usuario  # noqa: F401
from modelos.bitacora import Bitacora
from modelos.pedido import Pedido, TASA_COMISION_TARJETA, TASA_IVA
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

COLUMNAS_PRODUCCION = 4   # Vencidos · Para hoy · Próximos · Listos
COLUMNAS_ESTANDAR = 3     # En proceso · Terminado · Entregado


class TableroFiltrosTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
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
        self.client = self.app.test_client()
        for destino in ("rutas.caja.previsualizar_siguiente_folio", "rutas.pedidos.previsualizar_siguiente_folio"):
            parche = patch(destino, return_value="AC0001")
            parche.start()
            self.addCleanup(parche.stop)

        self.admin = self._usuario("Admin Ana", "admin@x.test", "Administrador")
        self.recepcion = self._usuario("Recepcion Rosa", "rec@x.test", "Recepcion")
        self.prod_a = self._usuario("Operador Alfa", "a@x.test", "Produccion")
        self.prod_b = self._usuario("Operador Bravo", "b@x.test", "Produccion")
        self.prod_baja = self._usuario("Operador Baja", "c@x.test", "Produccion", activo=False)

        # Pedido solo de Alfa: la tarjeta de Producción no debe mencionar a los demás.
        p = Pedido(folio="T-1", nombre_cliente="Cliente Uno", telefono_cliente="5550000000",
                   fecha_entrega=datetime.now(), total=0, estatus="En proceso")
        db.session.add(p)
        db.session.flush()
        p.reemplazar_detalles([{"cantidad": 1, "descripcion": "Lona", "precio_unitario": 10,
                                "responsable_id": self.prod_a.id_usuario}])
        p.responsables = [self.prod_a]
        db.session.commit()
        self.pedido = p

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.contexto.pop()

    def _usuario(self, nombre, correo, rol, activo=True):
        u = Usuario(nombre=nombre, correo=correo, rol=rol, activo=activo,
                    password_hash=generate_password_hash("x", method="pbkdf2:sha256"))
        db.session.add(u)
        db.session.commit()
        return u

    def _dashboard(self, usuario_):
        cliente = self.app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario_id"] = usuario_.id_usuario
        respuesta = cliente.get("/dashboard")
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.get_data(as_text=True)

    # ------------------------------------------------------------------ Producción
    def test_produccion_no_recibe_nombres_de_otros_operadores(self):
        html = self._dashboard(self.prod_a)
        self.assertIn("Operador Alfa", html)   # sí ve el suyo (barra de usuario / tarjeta)
        for ajeno in ("Operador Bravo", "Operador Baja", "Admin Ana", "Recepcion Rosa"):
            self.assertNotIn(ajeno, html, ajeno)

    def test_produccion_no_tiene_filtros_responsable_ni_fecha_entrega(self):
        html = self._dashboard(self.prod_a)
        for quitado in ("Responsable:", "Fecha entrega:", "Sin asignar", 'data-filtro="responsable"',
                        'data-filtro="fecha"', "filtro-responsable", "filtro-fecha"):
            self.assertNotIn(quitado, html, quitado)
        # Sin caja.ver tampoco hay filtro de pago: solo queda ordenar, una vez por columna.
        self.assertNotIn('data-filtro="pago"', html)
        self.assertEqual(html.count('class="form-select form-select-sm orden-columna"'), COLUMNAS_PRODUCCION)
        self.assertIn("Ordenar por:", html)

    def test_boton_de_produccion_dice_ordenar_y_no_filtrar(self):
        html = self._dashboard(self.prod_a)
        self.assertEqual(len(re.findall(r'<span class="filtro-texto">Ordenar</span>', html)), COLUMNAS_PRODUCCION)
        self.assertNotIn('<span class="filtro-texto">Filtrar</span>', html)

    def test_un_solo_menu_por_columna_sin_barra_duplicada(self):
        for usuario_, columnas in ((self.prod_a, COLUMNAS_PRODUCCION), (self.admin, COLUMNAS_ESTANDAR)):
            html = self._dashboard(usuario_)
            self.assertNotIn("tablero-barra-filtro", html)
            self.assertEqual(html.count("col-menu-filtros"), columnas)
            self.assertEqual(html.count('class="form-select form-select-sm orden-columna"'), columnas)

    def test_cambios_de_produccion_no_filtra_nombres_ajenos(self):
        db.session.add(Bitacora(accion="PEDIDO_EDITADO", entidad="pedido", id_entidad=self.pedido.id_pedido))
        db.session.commit()
        cliente = self.app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario_id"] = self.prod_a.id_usuario
        respuesta = cliente.get("/dashboard/cambios?desde=0")
        self.assertEqual(respuesta.status_code, 200)
        texto = respuesta.get_data(as_text=True)
        for ajeno in ("Operador Bravo", "Operador Baja", "Admin Ana", "Recepcion Rosa"):
            self.assertNotIn(ajeno, texto, ajeno)

    # ------------------------------------------------------------- Admin y Recepción
    def test_admin_y_recepcion_conservan_los_filtros_completos(self):
        for usuario_ in (self.admin, self.recepcion):
            html = self._dashboard(usuario_)
            for filtro in ("pago", "fecha", "responsable"):
                self.assertEqual(html.count(f'data-filtro="{filtro}"'), COLUMNAS_ESTANDAR, (usuario_.rol, filtro))
            self.assertEqual(len(re.findall(r'<span class="filtro-texto">Filtrar</span>', html)), COLUMNAS_ESTANDAR)

    def test_responsable_lista_solo_operadores_activos(self):
        html = self._dashboard(self.admin)
        self.assertIn('<option value="operador alfa">Operador Alfa</option>', html)
        self.assertIn('<option value="operador bravo">Operador Bravo</option>', html)
        self.assertNotIn("Operador Baja", html)   # ni en el filtro ni en el formulario de nota
        self.assertNotIn("Admin Ana</option>", html)

    def test_formulario_de_edicion_conserva_responsable_dado_de_baja(self):
        p = self.pedido
        p.responsables = [self.prod_a, self.prod_baja]
        db.session.commit()
        cliente = self.app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario_id"] = self.admin.id_usuario
        html = cliente.get(f"/pedidos/{p.id_pedido}/formulario-editar").get_data(as_text=True)
        self.assertIn("Operador Baja", html)      # ya asignado: no se pierde al editar
        self.assertIn("Operador Alfa", html)
        self.assertIn("Operador Bravo", html)
        # Un pedido sin la baja asignada no la ofrece.
        p.responsables = [self.prod_a]
        db.session.commit()
        html = cliente.get(f"/pedidos/{p.id_pedido}/formulario-editar").get_data(as_text=True)
        self.assertNotIn("Operador Baja", html)

    # -------------------------------------------------------------- ancho de columna
    def test_ancho_minimo_de_columna_280_solo_para_produccion(self):
        produccion = self._dashboard(self.prod_a)
        self.assertIn("window.PF_MIN_COLUMNA = 280", produccion)
        self.assertIn("minmax(280px, 1fr)", produccion)
        self.assertNotIn("minmax(284px, 1fr)", produccion)
        # Medidas reales de su cuadrícula: 4 × 280 + 80 = 1200 px de ancho útil (clientWidth, sin barra de scroll).
        self.assertIn("window.PF_GUTTER = 0; window.PF_MARGENES = 80;", produccion)
        self.assertIn("raiz.clientWidth", produccion)   # el modo se decide con el ancho útil, no con innerWidth
        self.assertNotIn("innerWidth", produccion.split("var raiz = document.documentElement")[1].split("</script>")[0])
        admin = self._dashboard(self.admin)
        self.assertNotIn("window.PF_MIN_COLUMNA = ", admin)   # base.html usa 310 por omisión
        self.assertIn("minmax(284px, 1fr)", admin)

    # ------------------------------------------------------- títulos y fecha de Vencidos
    def test_columna_terminados_hoy_se_llama_listos_en_todos_los_anchos(self):
        html = self._dashboard(self.prod_a)
        self.assertIn('<h2 class="col-title">Listos</h2>', html)
        self.assertNotIn("Terminados hoy", html)
        self.assertEqual(html.count('<span class="tab-largo">Listos</span>'), 1)

    def test_vencido_sin_hora_en_columnas_solo_en_produccion_y_hora_en_el_detalle(self):
        produccion = self._dashboard(self.prod_a)
        self.assertIn("const sinHora = tableroProduccion && enColumnas();", produccion)
        self.assertIn("window.addEventListener('pfmodo', () => actualizarFechasTarjetas());", produccion)
        # El detalle expandido de Producción lleva la entrega con hora; el de Admin no cambia.
        self.assertIn(self.pedido.fecha_entrega.strftime('%d/%m/%Y · %I:%M %p'), produccion)
        admin = self._dashboard(self.admin)
        self.assertEqual(admin.count('<div class="ip-label">Entrega</div>'), 0)

    # ------------------------------------------------------------------------- JS
    def test_js_filtra_desde_el_objeto_de_estado_no_desde_el_dom(self):
        html = self._dashboard(self.admin)
        self.assertIn("const filtrosDe = clave =>", html)
        self.assertIn("filtrosDe(columna.dataset.col)[select.dataset.filtro] = select.value", html)
        self.assertIn("const estado = filtrosDe(columna.dataset.col);", html)
        # Ya no se lee ningún <select> de filtro dentro de la columna (la copia oculta del modo pestañas).
        self.assertNotIn("columna.querySelector('.filtro-", html)


if __name__ == "__main__":
    unittest.main()
