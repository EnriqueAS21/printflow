import io
from math import hypot
from pathlib import Path

import pytest
from PIL import Image

from servicios.stickers.disenos import (
    InfoDiseno,
    Ubicacion,
    cargar_diseno,
    colocar,
    dpi_efectivo,
)


def _png_cuadro() -> bytes:
    imagen = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    imagen.paste((255, 0, 0, 255), (100, 100, 300, 300))
    salida = io.BytesIO()
    imagen.save(salida, "PNG")
    return salida.getvalue()


def _jpg(ancho: int = 320, alto: int = 240) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), (10, 120, 200)).save(salida, "JPEG")
    return salida.getvalue()


def _transformar(ubic: Ubicacion, info: InfoDiseno, x: float, y: float) -> tuple[float, float]:
    sx = ubic.ancho / info.ancho_px
    sy = ubic.alto / info.alto_px
    return ubic.x + x * sx, ubic.y + y * sy


@pytest.fixture
def info_cuadro(tmp_path: Path) -> InfoDiseno:
    return cargar_diseno(_png_cuadro(), "cuadro", tmp_path)


def test_png_contenido_px(info_cuadro: InfoDiseno, tmp_path: Path):
    assert info_cuadro.tiene_alfa is True
    assert (info_cuadro.ancho_px, info_cuadro.alto_px) == (400, 400)
    assert info_cuadro.contenido_px == (100, 100, 300, 300)
    assert info_cuadro.radio_px == pytest.approx(hypot(100, 100))
    assert Path(info_cuadro.ruta).suffix == ".png"
    assert Path(info_cuadro.ruta).exists()
    miniatura = Image.open(tmp_path / f"{info_cuadro.id}_mini.png")
    assert max(miniatura.size) <= 300


def test_circulo_ajustar_queda_dentro(info_cuadro: InfoDiseno):
    w = 50.0
    ubic = colocar(info_cuadro, w, w, "circulo", "ajustar", padding_mm=2.0)
    assert ubic.recorte == "forma"
    x0, y0, x1, y1 = info_cuadro.contenido_px
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        tx, ty = _transformar(ubic, info_cuadro, x, y)
        assert hypot(tx - w / 2, ty - w / 2) <= 23.0 + 1e-9


def test_cubrir_cubre_sangrado(tmp_path: Path):
    imagen = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    imagen.paste((0, 0, 0, 255), (50, 20, 350, 180))
    salida = io.BytesIO()
    imagen.save(salida, "PNG")
    info = cargar_diseno(salida.getvalue(), "ancho", tmp_path)

    w, h, b = 40.0, 40.0, 1.5
    ubic = colocar(info, w, h, "rectangulo", "cubrir", sangrado_mm=b)
    assert ubic.recorte == "sangrado"
    x0, y0, x1, y1 = info.contenido_px
    ix0, iy0 = _transformar(ubic, info, x0, y0)
    ix1, iy1 = _transformar(ubic, info, x1, y1)
    assert ix0 <= -b + 1e-9 and iy0 <= -b + 1e-9
    assert ix1 >= w + b - 1e-9 and iy1 >= h + b - 1e-9


def test_estirar_mide_exacto(info_cuadro: InfoDiseno):
    w, h, b = 60.0, 30.0, 1.5
    ubic = colocar(info_cuadro, w, h, "redondeado", "estirar", sangrado_mm=b)
    assert ubic.recorte == "sangrado"
    x0, y0, x1, y1 = info_cuadro.contenido_px
    ix0, iy0 = _transformar(ubic, info_cuadro, x0, y0)
    ix1, iy1 = _transformar(ubic, info_cuadro, x1, y1)
    assert ix1 - ix0 == pytest.approx(w + 2 * b)
    assert iy1 - iy0 == pytest.approx(h + 2 * b)
    assert ix0 == pytest.approx(-b)
    assert iy0 == pytest.approx(-b)


def test_jpg_sin_alfa(tmp_path: Path):
    info = cargar_diseno(_jpg(320, 240), "foto", tmp_path)
    assert info.tiene_alfa is False
    assert info.contenido_px == (0, 0, 320, 240)
    assert info.radio_px == pytest.approx(200.0)
    assert Path(info.ruta).suffix == ".jpg"


def test_firma_invalida(tmp_path: Path):
    with pytest.raises(ValueError):
        cargar_diseno(b"GIF89a" + b"\x00" * 100, "malo", tmp_path)


def test_padding_sin_espacio(info_cuadro: InfoDiseno):
    with pytest.raises(ValueError):
        colocar(info_cuadro, 4.0, 4.0, "rectangulo", "ajustar", padding_mm=2.0)


def test_dpi_efectivo():
    info = InfoDiseno("x", "x", "x.png", 600, 600, False, None, (0, 0, 600, 600), 0.0)
    ubic = Ubicacion(0.0, 0.0, 50.8, 50.8, None)
    assert dpi_efectivo(info, ubic) == pytest.approx(300.0)
