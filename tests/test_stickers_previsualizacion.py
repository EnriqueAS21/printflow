import io
import re
from types import SimpleNamespace

import pytest
from flask import Flask, g
from PIL import Image

import rutas.stickers as modulo
from servicios.stickers import Hoja, Marcas, Sticker, acomodar
from servicios.stickers.corte import generar_svg_corte
from servicios.stickers.disenos import cargar_diseno
from servicios.stickers.exportar import Ajuste
from servicios.stickers.previsualizacion import svg_previsualizacion


def _png(ancho=600, alto=300, dpi=300):
    salida = io.BytesIO()
    imagen = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    imagen.paste((200, 30, 30, 255), (10, 10, ancho - 10, alto - 10))
    imagen.save(salida, 'PNG', dpi=(dpi, dpi))
    return salida.getvalue()


def _caja(datos_path):
    numeros = [float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', datos_path)]
    xs, ys = numeros[0::2], numeros[1::2]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.fixture
def acomodo(tmp_path):
    info = cargar_diseno(_png(), 'logo.png', tmp_path)
    hoja = Hoja(304.8, 457.2, 5)
    marcas = Marcas()
    stickers = [Sticker(info.id, 50, 25, 10, 'redondeado')]
    ajustes = {info.id: Ajuste(modo='cubrir', sangrado_mm=1.5, radio_esquina_mm=3, color_fondo='#FFEE00')}
    resultado = acomodar(hoja, marcas, stickers, 3, False)
    return SimpleNamespace(hoja=hoja, marcas=marcas, disenos={info.id: info},
                           ajustes=ajustes, pliego=resultado.pliegos[0])


def _vista(a):
    return svg_previsualizacion(
        a.hoja, a.marcas, a.pliego, a.disenos, a.ajustes, lambda i: f'/miniatura/{i}'
    )


def test_una_imagen_y_un_clip_por_colocacion(acomodo):
    svg = _vista(acomodo)
    n = len(acomodo.pliego.colocaciones)
    assert n == 10
    assert svg.count('<image ') == n
    assert svg.count('<clipPath ') == n
    assert 'preserveAspectRatio="none"' in svg
    assert 'viewBox="0 0 304.8 457.2"' in svg and 'width="100%"' in svg


def test_caja_de_corte_coincide_con_svg_de_corte(acomodo):
    vista = _vista(acomodo)
    corte = generar_svg_corte(acomodo.hoja, acomodo.marcas, acomodo.pliego, acomodo.ajustes)
    d_vista = re.search(r'<path class="corte" d="([^"]+)"', vista).group(1)
    d_corte = re.search(r'<g id="CORTE"[^>]*>\s*<path d="([^"]+)"', corte).group(1)
    for a, b in zip(_caja(d_vista), _caja(d_corte)):
        assert a == pytest.approx(b, abs=0.001)


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, 'registrar', lambda *a, **k: None)
    monkeypatch.setattr(modulo, 'db', SimpleNamespace(
        session=SimpleNamespace(commit=lambda: None, rollback=lambda: None)))
    app = Flask(__name__, instance_path=str(tmp_path / 'instance'))
    app.config.update(SECRET_KEY='prueba', TESTING=True, CARPETA_SIGNCUT='')
    app.register_blueprint(modulo.stickers_bp, url_prefix='/stickers')

    @app.before_request
    def _usuario_falso():
        g.usuario = SimpleNamespace(id_usuario=1, rol='Administrador', activo=True)

    return app.test_client()


def test_calcular_devuelve_una_vista_si_las_hojas_son_identicas(cliente):
    diseno = cliente.post(
        '/stickers/api/biblioteca',
        data={'archivos': [(io.BytesIO(_png()), 'logo.png')]},
        content_type='multipart/form-data',
    ).get_json()['agregados'][0]
    assert len(diseno['contenido_px']) == 4

    hoja = Hoja(215.9, 279.4, 5)
    por_hoja = acomodar(hoja, Marcas(), [Sticker(diseno['id'], 50, 25, 1, 'rectangulo')],
                        3, True).por_pliego_max
    respuesta = cliente.post('/stickers/api/calcular', json={
        'hoja': {'ancho_mm': 215.9, 'alto_mm': 279.4, 'margen_mm': 5},
        'separacion_mm': 3,
        'disenos': [{'id': diseno['id'], 'ancho_mm': 50, 'alto_mm': 25,
                     'cantidad': por_hoja * 3, 'forma': 'rectangulo'}],
    })
    assert respuesta.status_code == 200
    datos = respuesta.get_json()
    assert len(datos['pliegos']) == 3
    assert len(datos['vistas']) == 1
    assert datos['vistas'][0]['hojas'] == [1, 2, 3]
    assert datos['vistas'][0]['svg'].count('<image ') == por_hoja
    assert f'/stickers/api/biblioteca/{diseno["id"]}/miniatura' in datos['vistas'][0]['svg']
