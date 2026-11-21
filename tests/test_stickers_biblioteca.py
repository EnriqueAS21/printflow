import io
from types import SimpleNamespace

import pytest
from flask import Flask, g
from PIL import Image

import rutas.stickers as modulo
from servicios.stickers import biblioteca


def _png(ancho=600, alto=300, color=(200, 30, 30, 255)):
    salida = io.BytesIO()
    imagen = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    imagen.paste(color, (10, 10, ancho - 10, alto - 10))
    imagen.save(salida, 'PNG', dpi=(300, 300))
    return salida.getvalue()


@pytest.fixture
def usuario():
    return SimpleNamespace(id_usuario=1, nombre='Ana', rol='Administrador', activo=True)


@pytest.fixture
def app(tmp_path, monkeypatch, usuario):
    monkeypatch.setattr(modulo, 'registrar', lambda *a, **k: None)
    monkeypatch.setattr(modulo, 'db', SimpleNamespace(
        session=SimpleNamespace(commit=lambda: None, rollback=lambda: None)))
    app = Flask(__name__, instance_path=str(tmp_path / 'instance'))
    app.config.update(SECRET_KEY='prueba', TESTING=True, CARPETA_SIGNCUT='')
    app.register_blueprint(modulo.stickers_bp, url_prefix='/stickers')

    @app.before_request
    def _usuario_falso():
        g.usuario = usuario

    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


@pytest.fixture
def raiz(app):
    return modulo.Path(app.instance_path) / 'stickers_biblioteca'


def _subir(cliente, datos=None, nombre='logo.png'):
    return cliente.post(
        '/stickers/api/biblioteca',
        data={'archivos': [(io.BytesIO(datos or _png()), nombre)]},
        content_type='multipart/form-data',
    )


def _solicitud(id_diseno, **cambios):
    datos = {
        'hoja': {'ancho_mm': 304.8, 'alto_mm': 457.2, 'margen_mm': 5},
        'separacion_mm': 3,
        'disenos': [{'id': id_diseno, 'ancho_mm': 50, 'alto_mm': 25, 'cantidad': 4,
                     'forma': 'rectangulo'}],
    }
    datos.update(cambios)
    return datos


def test_mismo_archivo_dos_veces_es_duplicado(tmp_path):
    datos = _png()
    primero = biblioteca.agregar(tmp_path, datos, 'logo.png', 1, 'Ana')
    segundo = biblioteca.agregar(tmp_path, datos, 'otro.png', 2, 'Luis')
    assert primero['duplicado'] is False
    assert segundo['duplicado'] is True and segundo['id'] == primero['id']
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert len(list(tmp_path.glob('*.png'))) == 2  # diseño + miniatura


def test_listar_busca_sin_acentos(tmp_path):
    biblioteca.agregar(tmp_path, _png(), 'Café escolar.png', 1, 'Ana')
    biblioteca.agregar(tmp_path, _png(color=(0, 0, 200, 255)), 'Perro.png', 1, 'Ana')
    encontrados = biblioteca.listar(tmp_path, buscar='cafe')
    assert [d['nombre'] for d in encontrados] == ['Café escolar.png']
    assert [d['nombre'] for d in biblioteca.listar(tmp_path, orden='nombre')] == [
        'Café escolar.png', 'Perro.png']


def test_obtener_inexistente_es_lookup(tmp_path):
    with pytest.raises(LookupError):
        biblioteca.obtener(tmp_path, '../x')
    with pytest.raises(LookupError):
        biblioteca.obtener(tmp_path, 'a' * 32)


def test_borrar_de_otro_usuario_es_403_y_del_dueno_200(cliente, usuario, raiz):
    diseno = _subir(cliente).get_json()['agregados'][0]
    assert len(list(raiz.glob(f'{diseno["id"]}*'))) == 3

    usuario.id_usuario, usuario.rol = 2, 'Recepcion'
    assert cliente.delete(f'/stickers/api/biblioteca/{diseno["id"]}').status_code == 403
    assert len(list(raiz.glob(f'{diseno["id"]}*'))) == 3

    usuario.id_usuario = 1
    assert cliente.delete(f'/stickers/api/biblioteca/{diseno["id"]}').status_code == 200
    assert list(raiz.glob(f'{diseno["id"]}*')) == []


@pytest.mark.parametrize('id_invalido', ['..%2Fx', 'abc', 'a' * 32])
def test_id_invalido_es_404(cliente, id_invalido):
    assert cliente.get(f'/stickers/api/biblioteca/{id_invalido}/miniatura').status_code == 404
    assert cliente.patch(f'/stickers/api/biblioteca/{id_invalido}',
                         json={'nombre': 'x'}).status_code == 404
    assert cliente.delete(f'/stickers/api/biblioteca/{id_invalido}').status_code == 404


def test_id_con_ruta_relativa_es_404(raiz):
    with pytest.raises(LookupError):
        biblioteca.leer(raiz, '../x')


def test_exportar_impresion_cuenta_uso(cliente, raiz):
    diseno = _subir(cliente).get_json()['agregados'][0]
    respuesta = cliente.post('/stickers/api/exportar/impresion', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 200
    registro = biblioteca.leer(raiz, diseno['id'])
    assert registro['usos'] == 1 and registro['ultimo_uso']


def test_sin_corte_no_exporta_corte(cliente, raiz):
    diseno = _subir(cliente).get_json()['agregados'][0]
    solicitud = _solicitud(diseno['id'], tipo='sin_corte', plantilla='no-existe')
    calculo = cliente.post('/stickers/api/calcular', json=solicitud).get_json()
    assert calculo['tipo'] == 'sin_corte' and calculo['marcas'] == []

    respuesta = cliente.post('/stickers/api/exportar/corte', json=solicitud)
    assert respuesta.status_code == 400
    assert respuesta.get_json()['error'] == 'Este trabajo es solo de impresión.'
    assert biblioteca.leer(raiz, diseno['id'])['usos'] == 0

    assert cliente.post('/stickers/api/exportar/impresion', json=solicitud).status_code == 200
