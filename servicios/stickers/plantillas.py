"""Gestión de plantillas PDF para acomodar diseños sobre áreas imprimibles."""

import json
import re
from datetime import datetime
from pathlib import Path

import fitz

PT_MM = 25.4 / 72
HOJAS_FISICAS = {"carta": (215.9, 279.4), "tabloide": (304.8, 457.2)}


def _slug(nombre):
    return re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")[:40] or "plantilla"


def _detectar_area(page):
    mejor = None
    total = page.rect.width * page.rect.height
    for d in page.get_drawings():
        f = d.get("fill")
        if not f or len(f) != 3 or not all(0.70 <= c <= 0.98 for c in f):
            continue
        r = d["rect"]
        a = r.width * r.height
        if a > 0.2 * total and (mejor is None or a > mejor[0]):
            mejor = (a, r)
    if mejor is None:
        raise ValueError("No se encontró el área gris de la plantilla; captúrala manualmente.")
    r = mejor[1]
    return [round(v * PT_MM, 2) for v in (r.x0, r.y0, r.x1, r.y1)]


def _es_negro(dibujo):
    relleno = dibujo.get("fill")
    return bool(relleno) and len(relleno) == 3 and all(c <= 0.05 for c in relleno)


def _detectar_marcas(page):
    marcas = []
    for dibujo in page.get_drawings():
        if not _es_negro(dibujo):
            continue
        rectangulo = dibujo["rect"]
        ancho = rectangulo.width * PT_MM
        alto = rectangulo.height * PT_MM
        lados = (ancho, alto)
        if any(18 <= lado_largo <= 22 and 0.5 <= lado_corto <= 1.5
               for lado_largo, lado_corto in (lados, lados[::-1])):
            marcas.append(rectangulo)
    if len(marcas) != 8:
        raise ValueError("La plantilla no tiene las 8 marcas en L de SignMaster.")
    x0 = min(rect.x0 for rect in marcas)
    y0 = min(rect.y0 for rect in marcas)
    x1 = max(rect.x1 for rect in marcas)
    y1 = max(rect.y1 for rect in marcas)
    return [round(v * PT_MM, 2) for v in (x0, y0, x1, y1)]


def _detectar_id_rect(page, area):
    area_rect = fitz.Rect(*(v / PT_MM for v in area))
    for dibujo in page.get_drawings():
        if not _es_negro(dibujo) or len(dibujo.get("items", ())) <= 20:
            continue
        rectangulo = dibujo["rect"]
        if area_rect.contains(rectangulo):
            return rectangulo
    return None


def posicion_en_hoja(meta, margen_impresora=5.0, libre_inferior=40.0):
    """Calcula el desplazamiento de la página de plantilla sobre la hoja física."""
    try:
        ancho_fisico, alto_fisico = HOJAS_FISICAS[meta["hoja_fisica"]]
        mx0, my0, mx1, my1 = meta["marcas_bbox"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Los datos de posicionamiento de la plantilla no son válidos.") from exc

    dx = (ancho_fisico - (mx0 + mx1)) / 2
    dy = margen_impresora - my0
    faltante_izquierdo = max(0.0, margen_impresora - (dx + mx0))
    faltante_derecho = max(0.0, margen_impresora - (ancho_fisico - (dx + mx1)))
    if faltante_izquierdo > 0 or faltante_derecho > 0:
        raise ValueError(
            "Las marcas exceden el ancho imprimible; faltan "
            f"{faltante_izquierdo:.1f} mm a la izquierda y "
            f"{faltante_derecho:.1f} mm a la derecha."
        )

    libre_abajo = alto_fisico - (dy + my1)
    if libre_abajo < libre_inferior:
        maximo = alto_fisico - margen_impresora - libre_inferior
        exceso = (my1 - my0) - maximo
        raise ValueError(
            f"La plantilla mide {my1-my0:.1f} mm de marca a marca; para dejar "
            f"{libre_inferior:.0f} mm abajo debe medir máximo {maximo:.1f} mm. "
            f"Reduce el alto de página en SignMaster al menos {exceso:.1f} mm."
        )
    return round(dx, 2), round(dy, 2), []


def _calcular_posicion(plantilla):
    """Actualiza dx, dy, libre_abajo, valida y error según las reglas vigentes."""
    marcas_bbox = plantilla["marcas_bbox"]
    ancho_fisico, alto_fisico = HOJAS_FISICAS[plantilla["hoja_fisica"]]
    dx = (ancho_fisico - (marcas_bbox[0] + marcas_bbox[2])) / 2
    dy = 5.0 - marcas_bbox[1]
    try:
        posicion_en_hoja(plantilla)
        valida, error = True, None
    except ValueError as exc:
        valida, error = False, str(exc)
    plantilla.update({
        "dx": round(dx, 2),
        "dy": round(dy, 2),
        "libre_abajo": round(alto_fisico - (dy + marcas_bbox[3]), 2),
        "valida": valida,
        "error": error,
    })
    return plantilla


def _validar_slug(slug):
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]+", slug) is None:
        raise ValueError("El identificador de plantilla no es válido.")


def _ruta_json(carpeta, slug):
    _validar_slug(slug)
    return Path(carpeta) / f"{slug}.json"


def guardar_plantilla(carpeta: Path, datos: bytes, nombre: str, hoja_fisica="carta") -> dict:
    """Valida un PDF de una página, detecta el área y guarda plantilla y metadatos."""
    if not isinstance(datos, bytes) or datos[:4] != b"%PDF":
        raise ValueError("El archivo proporcionado no es un PDF válido.")
    if len(datos) > 5 * 1024 * 1024:
        raise ValueError("El PDF no puede superar 5 MB.")

    try:
        documento = fitz.open(stream=datos, filetype="pdf")
    except Exception as exc:
        raise ValueError("No se pudo abrir el archivo PDF.") from exc
    try:
        if len(documento) != 1:
            raise ValueError("La plantilla PDF debe contener exactamente una página.")
        pagina = documento[0]
        ancho_mm = round(pagina.rect.width * PT_MM, 2)
        alto_mm = round(pagina.rect.height * PT_MM, 2)
        area = _detectar_area(pagina)
        gris = list(area)
        marcas_bbox = _detectar_marcas(pagina)
        id_rect = _detectar_id_rect(pagina, area)
        if id_rect is not None:
            area[1] = round(id_rect.y1 * PT_MM + 2, 2)
    finally:
        documento.close()

    if hoja_fisica not in ("carta", "tabloide"):
        raise ValueError('hoja_fisica debe ser "carta" o "tabloide".')
    slug = _slug(nombre)
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{slug}.pdf").write_bytes(datos)
    plantilla = {
        "nombre": nombre,
        "ancho_mm": ancho_mm,
        "alto_mm": alto_mm,
        "area": area,
        "gris": gris,
        "marcas_bbox": marcas_bbox,
        "id_rect": [round(v * PT_MM, 2) for v in (id_rect.x0, id_rect.y0, id_rect.x1, id_rect.y1)]
        if id_rect is not None else None,
        "hoja_fisica": hoja_fisica,
        "fecha": datetime.now().isoformat(),
    }
    _calcular_posicion(plantilla)
    (_ruta_json(carpeta, slug)).write_text(
        json.dumps(plantilla, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"slug": slug, **plantilla}


def _completar_gris(carpeta, slug, datos):
    """Recalcula el rect gris (si falta) y la posición en hoja; guarda si cambió."""
    original = dict(datos)
    if not datos.get("gris"):
        try:
            documento = fitz.open(Path(carpeta) / f"{slug}.pdf")
        except Exception:
            documento = None
        if documento is not None:
            try:
                datos["gris"] = _detectar_area(documento[0])
            except ValueError:
                pass
            finally:
                documento.close()
    try:
        _calcular_posicion(datos)
    except (KeyError, TypeError, ValueError, IndexError):
        pass
    if datos != original:
        _ruta_json(carpeta, slug).write_text(
            json.dumps({k: v for k, v in datos.items() if k != "slug"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return datos


def listar_plantillas(carpeta) -> list[dict]:
    """Lee y devuelve los metadatos JSON de las plantillas existentes."""
    carpeta = Path(carpeta)
    if not carpeta.exists():
        return []
    plantillas = []
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(datos, dict):
            plantillas.append({"slug": ruta.stem, **_completar_gris(carpeta, ruta.stem, datos)})
    return plantillas


def obtener_plantilla(carpeta, slug) -> dict:
    """Busca una plantilla por slug; lanza LookupError si no existe."""
    ruta = _ruta_json(carpeta, slug)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LookupError(f"No existe la plantilla {slug!r}.") from exc
    if not isinstance(datos, dict):
        raise LookupError(f"No existe la plantilla {slug!r}.")
    return {"slug": slug, **_completar_gris(carpeta, slug, datos)}


def actualizar_area(carpeta, slug, area) -> dict:
    """Actualiza el área en milímetros validando límites y orden de coordenadas."""
    plantilla = obtener_plantilla(carpeta, slug)
    if not isinstance(area, (list, tuple)) or len(area) != 4:
        raise ValueError("El área debe tener cuatro coordenadas: x0, y0, x1, y1.")
    try:
        x0, y0, x1, y1 = (float(valor) for valor in area)
    except (TypeError, ValueError) as exc:
        raise ValueError("Las coordenadas del área deben ser numéricas.") from exc
    if not (x0 < x1 and y0 < y1):
        raise ValueError("El área debe tener ancho y alto mayores que cero.")
    if x0 < 0 or y0 < 0 or x1 > plantilla["ancho_mm"] or y1 > plantilla["alto_mm"]:
        raise ValueError("El área debe estar dentro de los límites de la hoja.")

    plantilla["area"] = [round(valor, 2) for valor in (x0, y0, x1, y1)]
    _ruta_json(carpeta, slug).write_text(
        json.dumps({k: v for k, v in plantilla.items() if k != "slug"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return plantilla


def borrar_plantilla(carpeta, slug) -> None:
    """Elimina el PDF y los metadatos de una plantilla."""
    ruta_json = _ruta_json(carpeta, slug)
    plantilla = obtener_plantilla(carpeta, slug)
    ruta_json.unlink(missing_ok=True)
    (Path(carpeta) / f"{plantilla['slug']}.pdf").unlink(missing_ok=True)


def combinar_con_plantilla(pdf_bytes: bytes, ruta_plantilla, area_mm, ocultar_gris=True, meta=None) -> bytes:
    tpl = fitz.open(ruta_plantilla)
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    combinado = fitz.open()
    out = fitz.open()
    try:
        for i in range(len(src)):
            combinado.insert_pdf(tpl, from_page=0, to_page=0)
            pag = combinado[-1]
            if ocultar_gris:
                pag.draw_rect(fitz.Rect(*[v / PT_MM for v in area_mm]), color=None, fill=(1, 1, 1), overlay=True)
            pag.show_pdf_page(pag.rect, src, i)
        plantilla_rect = tpl[0].rect
        if meta is None:
            dx = dy = 0.0
            ancho_fisico, alto_fisico = plantilla_rect.width * PT_MM, plantilla_rect.height * PT_MM
        else:
            dx, dy = meta["dx"], meta["dy"]
            ancho_fisico, alto_fisico = HOJAS_FISICAS[meta["hoja_fisica"]]
        for i in range(len(combinado)):
            pagina_salida = out.new_page(width=ancho_fisico / PT_MM, height=alto_fisico / PT_MM)
            destino = fitz.Rect(
                dx / PT_MM,
                dy / PT_MM,
                dx / PT_MM + plantilla_rect.width,
                dy / PT_MM + plantilla_rect.height,
            )
            pagina_salida.show_pdf_page(destino, combinado, i)
        return out.tobytes(deflate=True)
    finally:
        out.close()
        combinado.close()
        src.close()
        tpl.close()
