import unittest
from unittest.mock import patch

from flask import Flask

from config import db
from modelos import produccion
from modelos.produccion import Proceso, ReglaClasificacion, ReglaPaso
from modelos.usuario import Usuario
from rutas.configuracion import configuracion_bp
from servicios import permisos


class ReglasClasificacionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        permisos.registrar_seguridad(self.app)
        self.app.register_blueprint(configuracion_bp, url_prefix="/configuracion")
        self.contexto = self.app.app_context()
        self.contexto.push()
        db.create_all()
        self.client = self.app.test_client()
        usuario = Usuario(
            nombre="ADMIN",
            correo="admin@example.test",
            rol="Administrador",
            activo=True,
            password_hash="x",
        )
        db.session.add(usuario)
        self.proceso = Proceso(nombre="DISEÑO", orden=1, activo=True)
        db.session.add(self.proceso)
        db.session.commit()
        with self.client.session_transaction() as sesion:
            sesion["usuario_id"] = usuario.id_usuario
        parche = patch("rutas.configuracion.reclamar_envio", return_value=True)
        parche.start()
        self.addCleanup(parche.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.contexto.pop()

    def post(self, payload):
        return self.client.post(
            "/configuracion/reglas",
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def test_crear_editar_regla_con_ruta(self):
        creada = self.post({
            "accion": "crear",
            "palabra_clave": " sticker ",
            "categoria": " stickers ",
            "prioridad": 20,
            "ruta": {
                "pasos": [{"id_proceso": self.proceso.id_proceso, "opcional": True}],
                "id_proceso_principal": self.proceso.id_proceso,
            },
        })
        self.assertEqual(creada.status_code, 200)
        regla = ReglaClasificacion.query.one()
        self.assertEqual(regla.palabra_clave, "STICKER")
        self.assertEqual(regla.categoria, "STICKERS")
        paso = ReglaPaso.query.one()
        self.assertTrue(paso.opcional)

        editada = self.post({
            "accion": "editar",
            "id_regla": regla.id_regla,
            "palabra_clave": "vinil",
            "categoria": "impresión",
            "prioridad": 30,
            "ruta": {
                "pasos": [{"id_proceso": self.proceso.id_proceso, "opcional": False}],
                "id_proceso_principal": None,
            },
        })
        self.assertEqual(editada.status_code, 200)
        self.assertEqual(regla.palabra_clave, "VINIL")
        self.assertEqual(regla.prioridad, 30)
        self.assertEqual(ReglaPaso.query.one().opcional, False)

    def test_duplicado_rechazado(self):
        db.session.add(ReglaClasificacion(palabra_clave="STICKER", prioridad=10, activo=True))
        db.session.commit()
        respuesta = self.post({
            "accion": "crear",
            "palabra_clave": " sticker ",
            "categoria": "",
            "prioridad": 20,
            "ruta": {"pasos": [], "id_proceso_principal": None},
        })
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("Ya existe", respuesta.get_json()["error"])

    def test_prioridad_y_activar(self):
        primera = ReglaClasificacion(palabra_clave="LONA", prioridad=10, activo=True)
        segunda = ReglaClasificacion(palabra_clave="STICKER", prioridad=20, activo=True)
        db.session.add_all([primera, segunda])
        db.session.commit()
        respuesta = self.post({
            "accion": "reordenar",
            "orden": [primera.id_regla, segunda.id_regla],
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()["orden"], [primera.id_regla, segunda.id_regla])

        activa = self.post({"accion": "activar", "id_regla": primera.id_regla})
        self.assertEqual(activa.status_code, 200)
        self.assertFalse(primera.activo)

    def test_caja_de_prueba(self):
        db.session.add(ReglaClasificacion(
            palabra_clave="STICKERS",
            categoria="GRAFICOS",
            prioridad=50,
            activo=True,
        ))
        db.session.commit()
        respuesta = self.post({
            "accion": "prueba",
            "descripcion": "50 stickers vinil",
        })
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.get_json()
        self.assertTrue(datos["ok"])
        self.assertEqual(datos["regla"]["palabra_clave"], "STICKERS")


if __name__ == "__main__":
    unittest.main()
