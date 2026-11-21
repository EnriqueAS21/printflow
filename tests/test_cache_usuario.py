import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from config import db
from modelos import bitacora, notificacion, perfil_usuario, usuario  # noqa: F401
from modelos.perfil_usuario import PerfilUsuario
from modelos.usuario import Usuario
from rutas.autenticacion import autenticacion_bp
from rutas.usuarios import bitacora_bp, usuarios_bp
from servicios import permisos


class CacheUsuarioTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="t", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        permisos.registrar_seguridad(self.app)
        for bp, p in ((usuarios_bp, "/usuarios"), (bitacora_bp, "/bitacora"), (autenticacion_bp, "/auth")):
            self.app.register_blueprint(bp, url_prefix=p)
        self.app.add_url_rule("/ping", "ping", lambda: "ok")  # ruta protegida sin plantillas
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        h = generate_password_hash("x", method="pbkdf2:sha256")
        self.admin = Usuario(nombre="Ana", correo="a@x.t", rol="Administrador", activo=True, password_hash=h)
        self.otro = Usuario(nombre="Beto", correo="b@x.t", rol="Recepcion", activo=True, password_hash=h)
        db.session.add_all([self.admin, self.otro])
        db.session.flush()
        db.session.add(PerfilUsuario(id_usuario=self.otro.id_usuario, foto_archivo="f.jpg"))
        db.session.commit()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.session.remove)

    def test_carga_una_consulta_y_luego_usa_cache(self):
        permisos.invalidar_usuario()
        u = permisos.cargar_usuario_actual(self.otro.id_usuario)
        self.assertEqual((u.nombre, u.rol, u.activo, u.foto), ("Beto", "Recepcion", True, "f.jpg"))
        with patch.object(db.session, "query", side_effect=AssertionError("no debía consultar")):
            self.assertIs(permisos.cargar_usuario_actual(self.otro.id_usuario), u)

    def test_ttl_vencido_recarga(self):
        permisos.cargar_usuario_actual(self.otro.id_usuario)
        with patch.object(permisos.time, "monotonic", return_value=permisos.time.monotonic() + permisos.USUARIO_TTL_SEG + 1):
            self.otro.rol = "Produccion"
            db.session.commit()
            self.assertEqual(permisos.cargar_usuario_actual(self.otro.id_usuario).rol, "Produccion")

    def test_desactivar_corta_acceso_de_inmediato(self):
        cliente_otro = self.app.test_client()
        cliente_admin = self.app.test_client()
        with cliente_otro.session_transaction() as s:
            s["usuario_id"] = self.otro.id_usuario
        with cliente_admin.session_transaction() as s:
            s["usuario_id"] = self.admin.id_usuario
        self.assertEqual(cliente_otro.get("/ping").status_code, 200)  # queda en caché
        self.assertEqual(cliente_admin.post(f"/usuarios/{self.otro.id_usuario}/estado").status_code, 302)
        r = cliente_otro.get("/ping")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login", r.headers["Location"])

    def test_lectura_vieja_en_vuelo_no_se_guarda(self):
        permisos.invalidar_usuario()
        original = db.session.query

        def consulta_con_invalidacion(*a, **k):
            q = original(*a, **k)
            permisos.invalidar_usuario(self.otro.id_usuario)  # alguien invalida mientras se consulta
            return q

        with patch.object(db.session, "query", side_effect=consulta_con_invalidacion):
            permisos.cargar_usuario_actual(self.otro.id_usuario)
        self.assertNotIn(self.otro.id_usuario, permisos._cache_usuarios)


if __name__ == "__main__":
    unittest.main()
