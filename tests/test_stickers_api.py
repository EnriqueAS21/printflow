import io
import re
from types import SimpleNamespace

import pytest
from flask import Flask, g
from PIL import Image

import rutas.stickers as modulo


class _SesionFalsa:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def bitacora(monkeypatch):
    registros = []
    sesion = _SesionFalsa()
    monkeypatch.setattr(
        modulo, 'registrar',
        lambda accion, entidad=None, id_entidad=None, detalle=None: registros.append((accion, detalle)),
    )
    monkeypatch.setattr(modulo, 'db', SimpleNamespace(session=sesion))
    return SimpleNamespace(registros=registros, sesion=sesion)


@pytest.fixture
def app(tmp_path, bitacora):
    app = Flask(__name__, instance_path=str(tmp_path / 'instance'))
    app.config.update(SECRET_KEY='prueba', TESTING=True, CARPETA_SIGNCUT='')
    app.register_blueprint(modulo.stickers_bp, url_prefix='/stickers')

    @app.before_request
    def _usuario_falso():
        g.usuario = SimpleNamespace(id_usuario=1, rol='Administrador', activo=True)

    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


def _png(ancho=600, alto=300, dpi=None):
    salida = io.BytesIO()
    imagen = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    imagen.paste((200, 30, 30, 255), (10, 10, ancho - 10, alto - 10))
    imagen.save(salida, 'PNG', **({'dpi': (dpi, dpi)} if dpi else {}))
    return salida.getvalue()


def _subir(cliente, *archivos):
    archivos = archivos or ((_png(), 'logo.png'),)
    return cliente.post(
        '/stickers/api/biblioteca',
        data={'archivos': [(io.BytesIO(datos), nombre) for datos, nombre in archivos]},
        content_type='multipart/form-data',
    )


def _solicitud(id_diseno, **cambios):
    diseno = {
        'id': id_diseno, 'ancho_mm': 50, 'alto_mm': 25, 'cantidad': 10,
        'forma': 'redondeado', 'modo': 'cubrir', 'sangrado_mm': 1.5,
        'padding_mm': 2, 'radio_esquina_mm': 3, 'color_fondo': None,
    }
    diseno.update(cambios.pop('diseno', {}))
    datos = {
        'hoja': {'ancho_mm': 304.8, 'alto_mm': 457.2, 'margen_mm': 5},
        'marcas': {'activas': True, 'tipo': 'L', 'largo_mm': 10, 'grosor_mm': 0.5,
                   'distancia_borde_mm': 10, 'holgura_mm': 3},
        'separacion_mm': 3, 'rellenar': False, 'incluir_marcas_corte': True,
        'disenos': [diseno],
    }
    datos.update(cambios)
    return datos


@pytest.fixture
def con_diseno(cliente):
    return _subir(cliente, (_png(600, 300, dpi=300), 'logo.png')).get_json()['agregados'][0]


# --- Biblioteca ------------------------------------------------------------------

def test_subir_diseno_sugiere_medida_por_dpi(con_diseno):
    diseno = con_diseno
    assert re.fullmatch(r'[0-9a-f]{32}', diseno['id'])
    assert diseno['ancho_px'] == 600 and diseno['alto_px'] == 300
    assert diseno['ancho_mm'] == pytest.approx(50.8, abs=0.1)
    assert diseno['alto_mm'] == pytest.approx(25.4, abs=0.1)
    assert diseno['subido_por'] == 1 and diseno['usos'] == 0
    assert diseno['url_mini'] == f'/stickers/api/biblioteca/{diseno["id"]}/miniatura'
    assert 'ruta' not in diseno


def test_subir_diseno_sin_dpi_usa_300(cliente):
    diseno = _subir(cliente, (_png(600, 300), 'a.png')).get_json()['agregados'][0]
    assert diseno['dpi'] is None
    assert diseno['ancho_mm'] == pytest.approx(50.8, abs=0.1)


def test_subir_varios_con_errores(cliente):
    respuesta = _subir(cliente, (_png(), 'a.png'), (b'no es imagen', 'a.txt'))
    datos = respuesta.get_json()
    assert respuesta.status_code == 200
    assert len(datos['agregados']) == 1
    assert datos['errores'][0]['nombre'] == 'a.txt' and datos['errores'][0]['error']


def test_subir_sin_archivos_es_400(cliente):
    assert cliente.post('/stickers/api/biblioteca', data={},
                        content_type='multipart/form-data').status_code == 400


def test_listar_y_renombrar(cliente, con_diseno):
    id_diseno = con_diseno['id']
    respuesta = cliente.patch(f'/stickers/api/biblioteca/{id_diseno}', json={'nombre': 'Gato'})
    assert respuesta.status_code == 200
    assert cliente.patch(f'/stickers/api/biblioteca/{id_diseno}', json={'nombre': ''}).status_code == 400
    lista = cliente.get('/stickers/api/biblioteca?buscar=gat&orden=nombre').get_json()['disenos']
    assert [d['id'] for d in lista] == [id_diseno]
    assert lista[0]['url_mini']
    assert cliente.get('/stickers/api/biblioteca?orden=otro').status_code == 400


def test_miniatura(cliente, con_diseno):
    respuesta = cliente.get(con_diseno['url_mini'])
    assert respuesta.status_code == 200 and respuesta.mimetype == 'image/png'
    assert respuesta.cache_control.max_age == 86400


# --- Cálculo y validación ------------------------------------------------------

def test_calcular(cliente, con_diseno):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/calcular', json=_solicitud(diseno['id']))
    datos = respuesta.get_json()
    assert respuesta.status_code == 200, datos
    assert datos['total'] == 10
    assert len(datos['pliegos']) == 1
    assert datos['grupos'] == [{'indice': 0, 'hojas': [1], 'etiqueta': 'hoja 1'}]
    col = datos['pliegos'][0]['colocaciones'][0]
    assert col['id_diseno'] == diseno['id'] and col['ancho_mm'] == 50
    assert 'orientacion_sugerida' in datos
    assert datos['avisos'] == []
    assert datos['tipo'] == 'con_corte'


def test_calcular_aviso_separacion_menor_que_sangrado(cliente, con_diseno):
    diseno = con_diseno
    datos = cliente.post(
        '/stickers/api/calcular', json=_solicitud(diseno['id'], separacion_mm=1)
    ).get_json()
    assert any('sangrado' in a for a in datos['avisos'])


def test_calcular_aviso_resolucion(cliente, con_diseno):
    diseno = con_diseno
    datos = cliente.post(
        '/stickers/api/calcular',
        json=_solicitud(diseno['id'], diseno={'ancho_mm': 200, 'alto_mm': 100, 'cantidad': 1}),
    ).get_json()
    assert any('dpi' in a for a in datos['avisos'])


@pytest.mark.parametrize('cambios, campo', [
    ({'hoja': {'ancho_mm': 10, 'alto_mm': 300}}, 'hoja.ancho_mm'),
    ({'separacion_mm': 60}, 'separacion_mm'),
    ({'diseno': {'cantidad': 0}}, 'disenos[0].cantidad'),
    ({'diseno': {'ancho_mm': 'x'}}, 'disenos[0].ancho_mm'),
    ({'diseno': {'forma': 'estrella'}}, 'disenos[0].forma'),
    ({'diseno': {'modo': 'otro'}}, 'disenos[0].modo'),
    ({'diseno': {'sangrado_mm': 6}}, 'disenos[0].sangrado_mm'),
    ({'diseno': {'color_fondo': 'rojo'}}, 'disenos[0].color_fondo'),
    ({'diseno': {'id': 'no-existe'}}, 'disenos[0].id'),
    ({'marcas': {'tipo': 'X'}}, 'marcas.tipo'),
])
def test_calcular_solicitud_invalida(cliente, con_diseno, cambios, campo):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/calcular', json=_solicitud(diseno['id'], **cambios))
    assert respuesta.status_code == 400
    datos = respuesta.get_json()
    assert datos['success'] is False and campo in datos['error']


def test_calcular_sin_json_es_400(cliente):
    respuesta = cliente.post('/stickers/api/calcular', data='x', content_type='text/plain')
    assert respuesta.status_code == 400


# --- Exportación ---------------------------------------------------------------

def test_exportar_impresion_registra_bitacora(cliente, con_diseno, bitacora):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/exportar/impresion', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 200
    assert respuesta.mimetype == 'application/pdf'
    assert respuesta.data.startswith(b'%PDF-')
    assert bitacora.registros == [('STICKERS_EXPORTADO', 'impresion: 10 stickers, 1 hojas')]
    assert bitacora.sesion.commits == 1


def test_exportar_corte_grupo(cliente, con_diseno):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/exportar/corte?grupo=0', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 200
    assert respuesta.mimetype == 'application/pdf'
    assert respuesta.data.startswith(b'%PDF-')
    assert 'corte_hoja_1.pdf' in respuesta.headers['Content-Disposition']


def test_exportar_corte_grupo_inexistente(cliente, con_diseno):
    diseno = con_diseno
    for grupo in ('9', '-1', 'x', ''):
        respuesta = cliente.post(f'/stickers/api/exportar/corte?grupo={grupo}', json=_solicitud(diseno['id']))
        assert respuesta.status_code == 400


def test_exportar_paquete_ya_no_existe(cliente, con_diseno):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/exportar/paquete', json=_solicitud(diseno['id']))
    assert respuesta.status_code in (400, 404)


def test_exportar_tipo_invalido(cliente, con_diseno):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/exportar/otro', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 404


def test_fallo_de_bitacora_no_impide_descarga(cliente, con_diseno, bitacora, monkeypatch):
    def falla(*args, **kwargs):
        raise RuntimeError('sin base de datos')

    monkeypatch.setattr(modulo, 'registrar', falla)
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/exportar/impresion', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 200
    assert bitacora.sesion.rollbacks == 1


# --- SignCut -------------------------------------------------------------------

def test_signcut_sin_carpeta_configurada(cliente, con_diseno):
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/signcut', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 400
    assert 'CARPETA_SIGNCUT' in respuesta.get_json()['error']


def test_signcut_carpeta_inexistente(cliente, con_diseno, app, tmp_path):
    app.config['CARPETA_SIGNCUT'] = str(tmp_path / 'no-existe')
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/signcut', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 400
    assert 'no existe' in respuesta.get_json()['error']


def test_signcut_escribe_pdfs(cliente, con_diseno, app, tmp_path, bitacora):
    destino = tmp_path / 'signcut'
    destino.mkdir()
    app.config['CARPETA_SIGNCUT'] = str(destino)
    diseno = con_diseno
    respuesta = cliente.post('/stickers/api/signcut', json=_solicitud(diseno['id']))
    assert respuesta.status_code == 200
    archivos = respuesta.get_json()['archivos']
    assert len(archivos) == 1
    nombre = archivos[0].rsplit('/', 1)[-1]
    assert re.fullmatch(r'\d{8}-\d{6}_corte_hoja_1\.pdf', nombre)
    assert (destino / nombre).read_bytes().startswith(b'%PDF-')
    assert bitacora.registros[-1][0] == 'STICKERS_EXPORTADO'
