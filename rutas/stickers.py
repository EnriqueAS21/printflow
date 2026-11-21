"""Acomodo de stickers en pliegos: biblioteca de diseños, cálculo y exportación."""

import io
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, render_template, request, send_file, url_for

from config import db
from modelos.bitacora import registrar
from servicios.permisos import permiso_requerido
from servicios.stickers import (
    Hoja,
    acomodar,
    agrupar_pliegos_identicos,
    area_util,
    posiciones_marcas,
    sugerir_orientacion,
)
from servicios.stickers import biblioteca, solicitud
from servicios.stickers import plantillas
from servicios.stickers.corte import _sufijo_hojas, archivos_corte, generar_pdf_corte, pdf_corte_signmaster
from servicios.stickers.disenos import MM_POR_PULGADA, aviso_resolucion, colocar
from servicios.stickers.exportar import generar_pdf_impresion
from servicios.stickers.previsualizacion import svg_previsualizacion
from servicios.stickers.plantillas import combinar_con_plantilla, guardar_plantilla

stickers_bp = Blueprint('stickers', __name__)

DPI_SUGERIDO = 300.0
TIPOS_EXPORTACION = ('impresion', 'corte')


def _error(mensaje, codigo=400):
    return jsonify({'success': False, 'error': mensaje}), codigo


def _raiz():
    return Path(current_app.instance_path) / 'stickers_biblioteca'


def _carpeta_plantillas():
    return Path(current_app.instance_path) / 'stickers_plantillas'


def _id_usuario():
    return g.usuario.id_usuario


def _registrar(accion, detalle):
    """La bitácora no debe impedir la descarga si falla la base de datos."""
    try:
        registrar(accion, detalle=detalle)
        db.session.commit()
    except Exception:
        current_app.logger.exception('No se pudo registrar %s en la bitácora.', accion)
        try:
            db.session.rollback()
        except Exception:
            pass


def _json():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        raise ValueError('El cuerpo de la petición debe ser un objeto JSON.')
    return datos


def _mm(valor):
    return round(float(valor), 3)


class _Acomodo:
    """Solicitud validada y su resultado, compartido por cálculo y exportación."""

    def __init__(self, datos):
        self.tipo = solicitud.tipo_trabajo(datos)
        self.disenos = solicitud.resolver_disenos(datos, _raiz())
        slug = solicitud.plantilla_solicitada(datos)
        self.plantilla = plantillas.obtener_plantilla(_carpeta_plantillas(), slug) if slug is not None else None
        self.plantilla_slug = slug
        self.ruta_plantilla = _carpeta_plantillas() / f'{slug}.pdf' if slug is not None else None
        if self.ruta_plantilla is not None and not self.ruta_plantilla.is_file():
            raise LookupError(f'No existe el PDF de la plantilla {slug!r}.')
        (self.hoja, self.marcas, self.stickers, self.ajustes,
         self.separacion, self.rellenar, self.area, self.ocultar_gris) = solicitud.parsear(
             datos, self.disenos, self.plantilla
         )
        self.incluir_marcas = solicitud.incluir_marcas_corte(datos)
        self.resultado = acomodar(
            self.hoja, self.marcas, self.stickers, self.separacion, self.rellenar,
            area=self.area,
        )
        self.avisos = (
            list(self.resultado.avisos)
            + self._avisos_resolucion()
            + solicitud.avisos(self.ajustes, self.separacion, self.disenos, self.stickers)
        )

    def _avisos_resolucion(self):
        avisos = []
        for sticker in self.stickers:
            info = self.disenos[sticker.id_diseno]
            ajuste = self.ajustes[sticker.id_diseno]
            ubic = colocar(
                info, sticker.ancho_mm, sticker.alto_mm, sticker.forma,
                ajuste.modo, ajuste.sangrado_mm, ajuste.padding_mm, ajuste.borde_mm,
            )
            aviso = aviso_resolucion(info, ubic)
            if aviso:
                avisos.append(aviso)
        return avisos

    def orientacion_sugerida(self):
        if self.plantilla is not None:
            return None
        if len(self.stickers) == 1:
            s = self.stickers[0]
            return sugerir_orientacion(self.hoja, self.marcas, s.ancho_mm, s.alto_mm, self.separacion)
        # Con varios diseños se compara cuántas hojas salen en cada orientación.
        lado_corto = min(self.hoja.ancho_mm, self.hoja.alto_mm)
        lado_largo = max(self.hoja.ancho_mm, self.hoja.alto_mm)
        vertical = Hoja(lado_corto, lado_largo, self.hoja.margen_mm)
        hojas = {
            nombre: len(acomodar(
                h, self.marcas, self.stickers, self.separacion, self.rellenar, area=self.area
            ).pliegos)
            for nombre, h in (('vertical', vertical), ('horizontal', vertical.girada()))
        }
        if hojas['vertical'] == hojas['horizontal']:
            return None
        return min(hojas, key=hojas.get)

    def detalle(self, tipo):
        return f'{tipo}: {self.resultado.total} stickers, {len(self.resultado.pliegos)} hojas'


def _preparar_acomodo():
    """Devuelve (acomodo, None) o (None, respuesta de error)."""
    try:
        return _Acomodo(_json()), None
    except LookupError as exc:
        return None, _error(str(exc), 404)
    except ValueError as exc:
        return None, _error(str(exc), 400)


def _archivos_corte(acomodo):
    return archivos_corte(
        acomodo.hoja, acomodo.marcas, acomodo.resultado, acomodo.ajustes, acomodo.incluir_marcas,
        acomodo.disenos,
    )


@stickers_bp.route('/')
@permiso_requerido('stickers.usar')
def index():
    return render_template(
        'stickers/index.html',
        signcut_configurado=bool(current_app.config.get('CARPETA_SIGNCUT')),
    )


@stickers_bp.route('/api/plantillas', methods=['GET'])
@permiso_requerido('stickers.usar')
def listar_plantillas():
    return jsonify({'success': True, 'plantillas': plantillas.listar_plantillas(_carpeta_plantillas())})


@stickers_bp.route('/api/plantillas', methods=['POST'])
@permiso_requerido('stickers.usar')
def subir_plantilla():
    archivo = request.files.get('archivo')
    nombre = request.form.get('nombre', '').strip()
    hoja_fisica = request.form.get('hoja_fisica', 'carta')
    if archivo is None or not archivo.filename:
        return _error('Selecciona un archivo PDF de plantilla.')
    if not nombre:
        return _error('Escribe un nombre para la plantilla.')
    try:
        guardada = guardar_plantilla(
            _carpeta_plantillas(), archivo.read(), nombre, hoja_fisica=hoja_fisica
        )
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({'success': True, 'plantilla': guardada})


@stickers_bp.route('/api/plantillas/<slug>/area', methods=['POST'])
@permiso_requerido('stickers.usar')
def corregir_area_plantilla(slug):
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return _error('El cuerpo de la petición debe ser un objeto JSON.')
    try:
        area = [datos[nombre] for nombre in ('x0', 'y0', 'x1', 'y1')]
        plantilla = plantillas.actualizar_area(_carpeta_plantillas(), slug, area)
    except LookupError as exc:
        return _error(str(exc), 404)
    except (ValueError, KeyError) as exc:
        return _error(str(exc))
    return jsonify({'success': True, 'plantilla': plantilla})


@stickers_bp.route('/api/plantillas/<slug>', methods=['DELETE'])
@permiso_requerido('stickers.usar')
def eliminar_plantilla(slug):
    try:
        plantillas.borrar_plantilla(_carpeta_plantillas(), slug)
    except LookupError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({'success': True})


def _diseno_json(registro):
    dpi = registro.get('dpi') or DPI_SUGERIDO
    datos = {clave: valor for clave, valor in registro.items() if clave not in ('ruta', 'sha256')}
    datos['ancho_mm'] = round(min(1000.0, max(5.0, registro['ancho_px'] / dpi * MM_POR_PULGADA)), 1)
    datos['alto_mm'] = round(min(1000.0, max(5.0, registro['alto_px'] / dpi * MM_POR_PULGADA)), 1)
    datos['url_mini'] = url_for('stickers.miniatura_biblioteca', id_diseno=registro['id'])
    return datos


@stickers_bp.route('/api/biblioteca', methods=['GET'])
@permiso_requerido('stickers.usar')
def listar_biblioteca():
    try:
        registros = biblioteca.listar(
            _raiz(), request.args.get('buscar', ''), request.args.get('orden', 'reciente')
        )
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({'success': True, 'disenos': [_diseno_json(r) for r in registros]})


@stickers_bp.route('/api/biblioteca', methods=['POST'])
@permiso_requerido('stickers.usar')
def subir_biblioteca():
    archivos = [a for a in request.files.getlist('archivos') if a and a.filename]
    if not archivos:
        return _error('Selecciona al menos un archivo PNG, JPG o PDF.')
    agregados, errores = [], []
    for archivo in archivos:
        nombre = os.path.basename(archivo.filename.replace('\\', '/'))
        try:
            registro = biblioteca.agregar(
                _raiz(), archivo.read(), nombre, _id_usuario(), getattr(g.usuario, 'nombre', None)
            )
        except ValueError as exc:
            errores.append({'nombre': nombre, 'error': str(exc)})
            continue
        agregados.append(_diseno_json(registro))
    return jsonify({'success': True, 'agregados': agregados, 'errores': errores})


@stickers_bp.route('/api/biblioteca/<id_diseno>', methods=['PATCH'])
@permiso_requerido('stickers.usar')
def renombrar_biblioteca(id_diseno):
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return _error('El cuerpo de la petición debe ser un objeto JSON.')
    try:
        registro = biblioteca.renombrar(_raiz(), id_diseno, datos.get('nombre'))
    except LookupError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({'success': True, 'diseno': _diseno_json(registro)})


@stickers_bp.route('/api/biblioteca/<id_diseno>', methods=['DELETE'])
@permiso_requerido('stickers.usar')
def borrar_biblioteca(id_diseno):
    try:
        registro = biblioteca.leer(_raiz(), id_diseno)
    except LookupError as exc:
        return _error(str(exc), 404)
    if registro.get('subido_por') != _id_usuario() and g.usuario.rol != 'Administrador':
        return _error('Solo quien subió el diseño o un administrador puede borrarlo.', 403)
    try:
        biblioteca.borrar(_raiz(), id_diseno)
    except LookupError as exc:
        return _error(str(exc), 404)
    return jsonify({'success': True})


@stickers_bp.route('/api/biblioteca/<id_diseno>/miniatura')
@permiso_requerido('stickers.usar')
def miniatura_biblioteca(id_diseno):
    try:
        ruta = biblioteca.ruta_miniatura(_raiz(), id_diseno)
    except LookupError as exc:
        return _error(str(exc), 404)
    return send_file(ruta, mimetype='image/png', max_age=86400)


@stickers_bp.route('/api/calcular', methods=['POST'])
@permiso_requerido('stickers.usar')
def calcular():
    acomodo, error = _preparar_acomodo()
    if error:
        return error
    r = acomodo.resultado
    grupos = agrupar_pliegos_identicos(r)
    try:
        vistas = _vistas(acomodo, grupos)
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({
        'success': True,
        'hoja': {'ancho_mm': acomodo.hoja.ancho_mm, 'alto_mm': acomodo.hoja.alto_mm},
        'area_util': [_mm(v) for v in (acomodo.area or area_util(acomodo.hoja, acomodo.marcas))],
        'plantilla_area': [_mm(v) for v in acomodo.area] if acomodo.area is not None else None,
        'marcas': [[_mm(v) for v in rect] for rect in posiciones_marcas(acomodo.hoja, acomodo.marcas)],
        'pliegos': [
            {
                'numero': pliego.numero,
                'colocaciones': [
                    {
                        'id_diseno': col.id_diseno,
                        'x_mm': _mm(col.x_mm),
                        'y_mm': _mm(col.y_mm),
                        'ancho_mm': _mm(col.ancho_mm),
                        'alto_mm': _mm(col.alto_mm),
                        'forma': col.forma,
                    }
                    for col in pliego.colocaciones
                ],
            }
            for pliego in r.pliegos
        ],
        'total': r.total,
        'por_pliego_max': r.por_pliego_max,
        'grupos': [
            {'indice': indice, 'hojas': numeros, 'etiqueta': _etiqueta_grupo(numeros)}
            for indice, numeros in enumerate(grupos)
        ],
        'vistas': vistas,
        'tipo': acomodo.tipo,
        'orientacion_sugerida': acomodo.orientacion_sugerida(),
        'avisos': acomodo.avisos,
    })


def _etiqueta_grupo(numeros):
    if len(numeros) == 1:
        return f'hoja {numeros[0]}'
    if all(b - a == 1 for a, b in zip(numeros, numeros[1:])):
        return f'hojas {numeros[0]}–{numeros[-1]}'
    return 'hojas ' + ', '.join(str(n) for n in numeros)


def _vistas(acomodo, grupos):
    pliegos = {pliego.numero: pliego for pliego in acomodo.resultado.pliegos}

    def url_miniatura(id_diseno):
        return url_for('stickers.miniatura_biblioteca', id_diseno=id_diseno)

    return [
        {
            'hojas': numeros,
            'svg': svg_previsualizacion(
                acomodo.hoja, acomodo.marcas, pliegos[numeros[0]],
                acomodo.disenos, acomodo.ajustes, url_miniatura,
            ),
        }
        for numeros in grupos
    ]


@stickers_bp.route('/api/exportar/<tipo>', methods=['POST'])
@permiso_requerido('stickers.usar')
def exportar(tipo):
    if tipo not in TIPOS_EXPORTACION:
        return _error('Tipo de exportación no válido.', 404)
    acomodo, error = _preparar_acomodo()
    if error:
        return error
    if acomodo.tipo == 'sin_corte' and tipo != 'impresion':
        return _error('Este trabajo es solo de impresión.')
    if acomodo.resultado.total == 0:
        return _error('No hay stickers que exportar: ninguno cabe en la hoja.')
    if acomodo.plantilla is not None and not acomodo.plantilla.get('valida', False):
        return _error(acomodo.plantilla.get('error') or 'La plantilla SignMaster no es válida.')

    try:
        if tipo == 'corte':
            grupos = agrupar_pliegos_identicos(acomodo.resultado)
            try:
                indice = int(request.args.get('grupo', ''))
            except ValueError:
                indice = -1
            if not 0 <= indice < len(grupos):
                return _error('El grupo de hojas no existe.')
            numeros = grupos[indice]
            pliego = acomodo.resultado.pliegos[numeros[0] - 1]
            if acomodo.plantilla is not None:
                contenido = pdf_corte_signmaster(
                    acomodo.hoja, pliego, acomodo.ajustes, acomodo.disenos, acomodo.plantilla["gris"]
                )
            else:
                contenido = generar_pdf_corte(
                    acomodo.hoja, acomodo.marcas, pliego, acomodo.ajustes, acomodo.incluir_marcas,
                    disenos=acomodo.disenos,
                )
            nombre = f'corte_{_sufijo_hojas(numeros)}.pdf'
        else:
            contenido = generar_pdf_impresion(
                acomodo.hoja, acomodo.marcas, acomodo.resultado, acomodo.disenos, acomodo.ajustes
            )
            nombre = 'impresion.pdf'
            if acomodo.plantilla is not None:
                contenido = combinar_con_plantilla(
                    contenido, acomodo.ruta_plantilla, acomodo.area,
                    acomodo.ocultar_gris, meta=acomodo.plantilla,
                )
                nombre = f'impresion_{acomodo.plantilla_slug}.pdf'
    except ValueError as exc:
        return _error(str(exc))

    _marcar_uso(acomodo)
    _registrar('STICKERS_EXPORTADO', acomodo.detalle(tipo))
    return send_file(io.BytesIO(contenido), mimetype='application/pdf', as_attachment=True, download_name=nombre)


def _marcar_uso(acomodo):
    """El contador de usos no debe impedir la descarga."""
    try:
        biblioteca.marcar_uso(_raiz(), [s.id_diseno for s in acomodo.stickers])
    except Exception:
        current_app.logger.exception('No se pudo actualizar el uso de los diseños.')


@stickers_bp.route('/api/signcut', methods=['POST'])
@permiso_requerido('stickers.usar')
def enviar_signcut():
    carpeta = current_app.config.get('CARPETA_SIGNCUT', '')
    if not carpeta:
        return _error('No hay carpeta de SignCut configurada (variable CARPETA_SIGNCUT).')
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return _error(f'La carpeta de SignCut no existe: {carpeta}')

    acomodo, error = _preparar_acomodo()
    if error:
        return error
    if acomodo.tipo == 'sin_corte':
        return _error('Este trabajo es solo de impresión.')
    if acomodo.resultado.total == 0:
        return _error('No hay stickers que enviar: ninguno cabe en la hoja.')

    marca = datetime.now().strftime('%Y%m%d-%H%M%S')
    escritas = []
    try:
        for nombre, datos in _archivos_corte(acomodo):
            if not nombre.endswith('.pdf'):
                continue
            ruta = carpeta / f'{marca}_{nombre}'
            with open(ruta, 'wb') as destino:
                destino.write(datos)
            escritas.append(str(ruta))
    except OSError as exc:
        current_app.logger.exception('No se pudo escribir en la carpeta de SignCut.')
        return _error(f'No se pudo escribir en la carpeta de SignCut: {exc.strerror or exc}')

    _registrar('STICKERS_EXPORTADO', acomodo.detalle('signcut'))
    return jsonify({'success': True, 'archivos': escritas})
