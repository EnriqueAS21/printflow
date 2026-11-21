"""Administración de la información impresa en notas de pedido."""
from io import BytesIO
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename

from config import db
from modelos.bitacora import registrar
from modelos.configuracion_nota import (
    ConfiguracionNota,
    normalizar_apartados,
    obtener_configuracion_nota,
)
from modelos.producto import Producto
from modelos.produccion import (
    LineaPaso, ProductoPaso, Proceso, ReglaClasificacion, ReglaPaso,
)
from modelos.envios import MENSAJE_DUPLICADO, reclamar_envio
from servicios.rutas_produccion import invalidar
from servicios.permisos import permiso_requerido
from servicios.textos import normalizar_busqueda, normalizar_nombre
from servicios.rutas_produccion import ruta_para

configuracion_bp = Blueprint("configuracion", __name__, template_folder="../templates")
EXTENSIONES_LOGO = {"png", "jpg", "jpeg"}
MAX_LOGO_BYTES = 3 * 1024 * 1024


def _directorio_logos():
    directorio = Path(current_app.static_folder) / "uploads" / "logos"
    directorio.mkdir(parents=True, exist_ok=True)
    return directorio


def _guardar_logo(archivo):
    nombre_original = secure_filename(archivo.filename or "")
    extension = nombre_original.rsplit(".", 1)[-1].lower() if "." in nombre_original else ""
    if extension not in EXTENSIONES_LOGO:
        raise ValueError("El logo debe ser una imagen PNG o JPG.")

    contenido = archivo.stream.read(MAX_LOGO_BYTES + 1)
    if len(contenido) > MAX_LOGO_BYTES:
        raise ValueError("El logo no debe superar 3 MB.")
    firma = contenido[:8]
    coincide_png = extension == "png" and firma == b"\x89PNG\r\n\x1a\n"
    coincide_jpg = extension in {"jpg", "jpeg"} and contenido.startswith(b"\xff\xd8\xff")
    if not (coincide_png or coincide_jpg):
        raise ValueError("La extensión del archivo no coincide con una imagen PNG o JPG válida.")
    try:
        ancho, alto = ImageReader(BytesIO(contenido)).getSize()
    except Exception as exc:
        raise ValueError("El archivo seleccionado no es una imagen válida.") from exc
    if ancho <= 0 or alto <= 0 or ancho * alto > 40_000_000:
        raise ValueError("La resolución del logo es demasiado grande.")

    nombre = f"logo-{uuid4().hex}.{extension}"
    ruta = _directorio_logos() / nombre
    ruta.write_bytes(contenido)
    return nombre


@configuracion_bp.route("/nota", methods=["GET", "POST"])
@permiso_requerido("configuracion.administrar")
def nota():
    configuracion = obtener_configuracion_nota()
    apartados = configuracion.apartados or []
    if request.method == "POST":
        anterior_logo = configuracion.logo_archivo
        logo_nuevo = None
        apartados = normalizar_apartados(request.form.get("apartados_json", "[]"))
        try:
            nombre = (request.form.get("nombre_negocio") or "").strip()
            lema = (request.form.get("lema") or "").strip()
            direccion = (request.form.get("direccion") or "").strip()
            telefono = (request.form.get("telefono") or "").strip()
            cuenta_fiscal_banco = (request.form.get("cuenta_fiscal_banco") or "").strip() or None
            cuenta_fiscal_titular = (request.form.get("cuenta_fiscal_titular") or "").strip() or None
            cuenta_fiscal_clabe = (request.form.get("cuenta_fiscal_clabe") or "").strip() or None
            politicas = "\n".join(
                linea.strip() for linea in (request.form.get("politicas") or "").splitlines()
                if linea.strip()
            )
            if not nombre or not direccion or not telefono:
                raise ValueError("Nombre del negocio, dirección y teléfono son obligatorios.")
            if len(nombre) > 120 or len(lema) > 160 or len(direccion) > 240 or len(telefono) > 40:
                raise ValueError("Uno o más campos exceden el límite de caracteres.")
            if (len(cuenta_fiscal_banco or "") > 60
                    or len(cuenta_fiscal_titular or "") > 150
                    or len(cuenta_fiscal_clabe or "") > 18):
                raise ValueError("Uno o más datos de la cuenta fiscal exceden el límite de caracteres.")
            if cuenta_fiscal_clabe and not re.fullmatch(r"\d{18}", cuenta_fiscal_clabe):
                raise ValueError("La CLABE fiscal debe contener exactamente 18 dígitos.")
            if len(politicas) > 5000:
                raise ValueError("Las políticas no deben superar 5,000 caracteres.")

            archivo = request.files.get("logo")
            if archivo and archivo.filename:
                logo_nuevo = _guardar_logo(archivo)
                configuracion.logo_archivo = logo_nuevo
            elif request.form.get("quitar_logo") == "on":
                configuracion.logo_archivo = None

            configuracion.nombre_negocio = nombre
            configuracion.lema = lema
            configuracion.direccion = direccion
            configuracion.telefono = telefono
            configuracion.politicas = politicas
            configuracion.cuenta_fiscal_banco = cuenta_fiscal_banco
            configuracion.cuenta_fiscal_titular = cuenta_fiscal_titular
            configuracion.cuenta_fiscal_clabe = cuenta_fiscal_clabe
            configuracion.apartados = apartados
            registrar("CONFIGURACION_NOTA_EDITADA", "configuracion", 1, "Datos de nota actualizados")
            db.session.commit()

            if anterior_logo and anterior_logo != configuracion.logo_archivo:
                nombre_anterior = secure_filename(anterior_logo)
                if nombre_anterior:
                    (_directorio_logos() / nombre_anterior).unlink(missing_ok=True)
            flash("Configuración de la nota guardada.", "success")
            return redirect(url_for("configuracion.nota"))
        except ValueError as exc:
            db.session.rollback()
            if logo_nuevo:
                (_directorio_logos() / logo_nuevo).unlink(missing_ok=True)
            flash(str(exc), "warning")
        except Exception:
            db.session.rollback()
            if logo_nuevo:
                (_directorio_logos() / logo_nuevo).unlink(missing_ok=True)
            current_app.logger.exception("Error al guardar configuración de nota")
            flash("No se pudo guardar la configuración.", "danger")
        configuracion = db.session.get(ConfiguracionNota, 1)

    return render_template("configuracion/nota.html", configuracion=configuracion, apartados=apartados)


def _es_sin_clasificar(proceso):
    return bool(proceso.es_sistema) or (proceso.nombre or "").strip().lower() == "sin clasificar"


def _proceso_en_uso(proceso):
    return bool(
        db.session.query(ProductoPaso.id_producto_paso)
        .filter(ProductoPaso.id_proceso == proceso.id_proceso)
        .first()
        or db.session.query(Producto.id_producto)
        .filter(Producto.id_proceso_principal == proceso.id_proceso)
        .first()
        or db.session.query(ReglaPaso.id_regla_paso)
        .filter(ReglaPaso.id_proceso == proceso.id_proceso)
        .first()
        or db.session.query(ReglaClasificacion.id_regla)
        .filter(ReglaClasificacion.id_proceso_principal == proceso.id_proceso)
        .first()
        or db.session.query(LineaPaso.id_linea_paso)
        .filter(LineaPaso.id_proceso == proceso.id_proceso)
        .first()
    )


@configuracion_bp.route("/procesos", methods=["GET", "POST"])
@permiso_requerido("productos.administrar", estricto=True)
def procesos():
    if request.method == "POST":
        datos_json = request.get_json(silent=True)
        datos = datos_json if isinstance(datos_json, dict) else request.form
        accion = (datos.get("accion") or "").strip()
        es_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if not reclamar_envio(datos.get("token_envio")):
                if es_ajax or accion == "reordenar":
                    return jsonify(ok=False, error=MENSAJE_DUPLICADO), 409
                raise ValueError(MENSAJE_DUPLICADO)

            if accion == "crear":
                nombre = normalizar_nombre(datos.get("nombre"))
                if not nombre:
                    raise ValueError("El nombre del proceso es obligatorio.")
                if len(nombre) > 80:
                    raise ValueError("El nombre no puede superar 80 caracteres.")
                if nombre.lower() == "sin clasificar":
                    raise ValueError('"Sin clasificar" está reservado.')
                existente = Proceso.query.filter(db.func.lower(Proceso.nombre) == nombre.lower()).first()
                if existente is not None:
                    raise ValueError("Ya existe un proceso con ese nombre.")
                ultimo = db.session.query(db.func.max(Proceso.orden)).scalar() or 0
                db.session.add(Proceso(nombre=nombre, orden=ultimo + 1, activo=True, es_sistema=False))
                db.session.commit()
                invalidar()
                flash("Proceso creado.", "success")
                if es_ajax or accion == "reordenar":
                    return jsonify(ok=True)
                return redirect(url_for("configuracion.procesos"))

            proceso = None
            if accion == "reordenar":
                proceso = SimpleNamespace(id_proceso=None, nombre="orden", activo=True)
            else:
                id_proceso = int(datos.get("id_proceso") or 0)
                proceso = db.session.get(Proceso, id_proceso)
                if proceso is None:
                    raise ValueError("El proceso no existe.")

            if accion == "editar":
                if _es_sin_clasificar(proceso):
                    raise ValueError('"Sin clasificar" no se puede editar.')
                nombre = normalizar_nombre(datos.get("nombre"))
                if not nombre:
                    raise ValueError("El nombre del proceso es obligatorio.")
                if len(nombre) > 80:
                    raise ValueError("El nombre no puede superar 80 caracteres.")
                if nombre.lower() == "sin clasificar":
                    raise ValueError('"Sin clasificar" está reservado.')
                duplicado = (
                    Proceso.query
                    .filter(db.func.lower(Proceso.nombre) == nombre.lower(), Proceso.id_proceso != proceso.id_proceso)
                    .first()
                )
                if duplicado is not None:
                    raise ValueError("Ya existe un proceso con ese nombre.")
                proceso.nombre = nombre

            elif accion == "activar":
                if _es_sin_clasificar(proceso):
                    raise ValueError('"Sin clasificar" no se puede desactivar.')
                proceso.activo = not bool(proceso.activo)

            elif accion == "reordenar":
                if isinstance(datos_json, dict):
                    ids = [str(valor) for valor in (datos_json.get("orden") or [])]
                else:
                    ids = datos.getlist("orden[]")
                if not ids:
                    raise ValueError("No hay procesos para ordenar.")
                lista_por_id = {
                    fila.id_proceso: fila
                    for fila in Proceso.query.filter(
                        Proceso.id_proceso.in_([int(valor) for valor in ids])
                    ).all()
                }
                for orden, valor in enumerate(ids, start=1):
                    fila = lista_por_id.get(int(valor))
                    if fila is not None and not _es_sin_clasificar(fila):
                        fila.orden = orden
                sistema = [
                    fila for fila in Proceso.query.all()
                    if _es_sin_clasificar(fila)
                ]
                ultimo = len(ids)
                for fila in sistema:
                    fila.orden = ultimo + 1

            elif accion in ("subir", "bajar"):
                lista = Proceso.query.order_by(Proceso.orden, Proceso.nombre, Proceso.id_proceso).all()
                indice = next((i for i, fila in enumerate(lista) if fila.id_proceso == proceso.id_proceso), None)
                vecino = indice - 1 if accion == "subir" else indice + 1
                if indice is not None and 0 <= vecino < len(lista):
                    otra = lista[vecino]
                    proceso.orden, otra.orden = otra.orden, proceso.orden

            elif accion == "eliminar":
                if _es_sin_clasificar(proceso):
                    raise ValueError('"Sin clasificar" no se puede borrar.')
                if proceso.activo and _proceso_en_uso(proceso):
                    proceso.activo = False
                    raise ValueError("El proceso está en uso y solo puede desactivarse.")
                if _proceso_en_uso(proceso):
                    raise ValueError("El proceso está en uso y solo puede desactivarse.")
                db.session.delete(proceso)

            else:
                raise ValueError("Acción no válida.")

            registrar("PROCESO_EDITADO", "proceso", proceso.id_proceso, f"{accion}: {proceso.nombre}")
            db.session.commit()
            invalidar()
            if not (es_ajax or accion == "reordenar"):
                flash("Procesos actualizados.", "success")
            if es_ajax or accion == "reordenar":
                respuesta = {
                    "ok": True,
                    "id_proceso": proceso.id_proceso,
                    "nombre": proceso.nombre,
                    "activo": bool(proceso.activo),
                }
                if accion == "reordenar":
                    if isinstance(datos_json, dict):
                        respuesta["orden"] = [int(valor) for valor in (datos_json.get("orden") or [])]
                    else:
                        respuesta["orden"] = [int(valor) for valor in datos.getlist("orden[]")]
                return jsonify(respuesta)
        except ValueError as exc:
            db.session.rollback()
            if es_ajax or accion == "reordenar":
                return jsonify(ok=False, error=str(exc)), 400
            flash(str(exc), "warning")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error al guardar procesos")
            if es_ajax or accion == "reordenar":
                return jsonify(ok=False, error="No se pudo guardar el proceso."), 500
            flash("No se pudo guardar el proceso.", "danger")
        return redirect(url_for("configuracion.procesos"))

    todos = Proceso.query.order_by(Proceso.orden, Proceso.nombre, Proceso.id_proceso).all()
    normales = [fila for fila in todos if not _es_sin_clasificar(fila)]
    procesos = (
        [fila for fila in normales if fila.activo]
        + [fila for fila in normales if not fila.activo]
        + [fila for fila in todos if _es_sin_clasificar(fila)]
    )
    return render_template(
        "configuracion/procesos.html",
        procesos=procesos,
        total_procesos=len(normales),
    )


def _validar_ruta_regla(pasos, id_principal):
    if len(pasos) > 8:
        raise ValueError("La ruta no puede tener más de 8 pasos.")
    ids = []
    for paso in pasos:
        id_proceso = int(paso.get("id_proceso") or 0)
        proceso = db.session.get(Proceso, id_proceso)
        if proceso is None or not bool(proceso.activo) or _es_sin_clasificar(proceso):
            raise ValueError("La ruta solo puede usar procesos activos.")
        if ids and ids[-1] == id_proceso:
            raise ValueError("No repitas el mismo proceso consecutivamente.")
        ids.append(id_proceso)
    if id_principal:
        proceso = db.session.get(Proceso, int(id_principal))
        if proceso is None or not bool(proceso.activo) or _es_sin_clasificar(proceso):
            raise ValueError("El proceso principal debe estar activo.")


@configuracion_bp.route("/reglas", methods=["GET", "POST"])
@permiso_requerido("productos.administrar", estricto=True)
def reglas():
    if request.method == "POST":
        datos_json = request.get_json(silent=True)
        datos = datos_json if isinstance(datos_json, dict) else request.form
        accion = (datos.get("accion") or "").strip()
        es_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if accion == "prueba":
                descripcion = str(datos.get("descripcion") or "").strip()
                ruta = ruta_para(None, descripcion)
                coincidencia = None
                texto = normalizar_busqueda(descripcion)
                if texto:
                    for regla in (
                        ReglaClasificacion.query
                        .filter(ReglaClasificacion.activo.is_(True))
                        .order_by(ReglaClasificacion.prioridad.desc(), ReglaClasificacion.id_regla)
                        .all()
                    ):
                        clave = normalizar_busqueda(regla.palabra_clave)
                        if clave and clave in texto:
                            coincidencia = {
                                "palabra_clave": regla.palabra_clave,
                                "categoria": regla.categoria,
                                "prioridad": regla.prioridad,
                            }
                            break
                nombres = {
                    proceso.id_proceso: proceso.nombre
                    for proceso in Proceso.query.filter(Proceso.id_proceso.in_([x[1] for x in ruta])).all()
                } if ruta and all(x[1] is not None for x in ruta) else {}
                return jsonify(
                    ok=True,
                    regla=coincidencia,
                    ruta=[
                        {"orden": orden, "id_proceso": id_proceso,
                         "nombre": nombres.get(id_proceso), "opcional": opcional}
                        for orden, id_proceso, opcional in ruta
                    ],
                )

            if not reclamar_envio(datos.get("token_envio")):
                return jsonify(ok=False, error=MENSAJE_DUPLICADO), 409

            if accion in ("crear", "editar"):
                id_regla = int(datos.get("id_regla") or 0)
                palabra_clave = normalizar_nombre(datos.get("palabra_clave"))
                if not palabra_clave:
                    raise ValueError("La palabra clave es obligatoria.")
                if len(palabra_clave) > 120:
                    raise ValueError("La palabra clave no puede superar 120 caracteres.")
                categoria = normalizar_nombre(datos.get("categoria"))
                if categoria and len(categoria) > 60:
                    raise ValueError("La categoría no puede superar 60 caracteres.")
                try:
                    prioridad = int(datos.get("prioridad") or 0)
                except (TypeError, ValueError):
                    raise ValueError("La prioridad debe ser un número entero.")

                duplicado = (
                    ReglaClasificacion.query
                    .filter(db.func.lower(ReglaClasificacion.palabra_clave) == palabra_clave.lower())
                )
                if accion == "editar":
                    duplicado = duplicado.filter(ReglaClasificacion.id_regla != id_regla)
                if duplicado.first() is not None:
                    raise ValueError("Ya existe una regla con esa palabra clave.")

                if accion == "crear":
                    regla = ReglaClasificacion(
                        palabra_clave=palabra_clave,
                        prioridad=prioridad,
                        categoria=categoria,
                        activo=True,
                    )
                    db.session.add(regla)
                    db.session.flush()
                else:
                    regla = db.session.get(ReglaClasificacion, id_regla)
                    if regla is None:
                        raise ValueError("La regla no existe.")

                ruta_datos = datos.get("ruta") if isinstance(datos, dict) else None
                if not isinstance(ruta_datos, dict):
                    raise ValueError("La ruta de la regla no es válida.")
                pasos = ruta_datos.get("pasos") or []
                id_principal = ruta_datos.get("id_proceso_principal")
                _validar_ruta_regla(pasos, id_principal)

                regla.palabra_clave = palabra_clave
                regla.prioridad = prioridad
                regla.categoria = categoria
                regla.id_proceso_principal = int(id_principal) if id_principal else None
                ReglaPaso.query.filter(ReglaPaso.id_regla == regla.id_regla).delete(
                    synchronize_session=False
                )
                db.session.flush()
                for orden, paso in enumerate(pasos, start=1):
                    db.session.add(ReglaPaso(
                        id_regla=regla.id_regla,
                        orden=orden,
                        id_proceso=int(paso["id_proceso"]),
                        opcional=bool(paso.get("opcional")),
                    ))

            elif accion == "activar":
                regla = db.session.get(ReglaClasificacion, int(datos.get("id_regla") or 0))
                if regla is None:
                    raise ValueError("La regla no existe.")
                regla.activo = not bool(regla.activo)

            elif accion == "reordenar":
                ids = [str(valor) for valor in (datos.get("orden") or [])] if isinstance(datos, dict) else datos.getlist("orden[]")
                if not ids:
                    raise ValueError("No hay reglas para ordenar.")
                lista_por_id = {
                    fila.id_regla: fila
                    for fila in ReglaClasificacion.query.filter(
                        ReglaClasificacion.id_regla.in_([int(valor) for valor in ids])
                    ).all()
                }
                for posicion, valor in enumerate(ids):
                    fila = lista_por_id.get(int(valor))
                    if fila is not None:
                        fila.prioridad = len(ids) - posicion

            else:
                raise ValueError("Acción no válida.")

            registrar("REGLA_CLASIFICACION_EDITADA", "regla_clasificacion",
                      getattr(regla if 'regla' in locals() else None, "id_regla", None),
                      f"{accion}: {getattr(locals().get('regla'), 'palabra_clave', '')}")
            db.session.commit()
            invalidar()
            if es_ajax:
                respuesta = {"ok": True}
                if accion == "reordenar":
                    respuesta["orden"] = [int(valor) for valor in ids]
                if accion in ("crear", "editar", "activar"):
                    respuesta.update(
                        id_regla=regla.id_regla,
                        palabra_clave=regla.palabra_clave,
                        categoria=regla.categoria,
                        prioridad=regla.prioridad,
                        activo=bool(regla.activo),
                    )
                return jsonify(respuesta)
            return redirect(url_for("configuracion.reglas"))
        except ValueError as exc:
            db.session.rollback()
            if es_ajax:
                return jsonify(ok=False, error=str(exc)), 400
            flash(str(exc), "warning")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error al guardar reglas de clasificación")
            if es_ajax:
                return jsonify(ok=False, error="No se pudo guardar la regla."), 500
            flash("No se pudo guardar la regla.", "danger")
        return redirect(url_for("configuracion.reglas"))

    procesos = (
        Proceso.query
        .filter(db.func.lower(Proceso.nombre) != "sin clasificar")
        .order_by(Proceso.orden, Proceso.nombre)
        .all()
    )
    reglas = (
        ReglaClasificacion.query
        .order_by(ReglaClasificacion.activo.desc(),
                  ReglaClasificacion.prioridad.desc(), ReglaClasificacion.palabra_clave)
        .all()
    )
    datos_reglas = []
    for regla in reglas:
        pasos = (
            ReglaPaso.query
            .filter(ReglaPaso.id_regla == regla.id_regla)
            .order_by(ReglaPaso.orden)
            .all()
        )
        datos_reglas.append({
            "id_regla": regla.id_regla,
            "palabra_clave": regla.palabra_clave,
            "categoria": regla.categoria,
            "prioridad": regla.prioridad,
            "activo": bool(regla.activo),
            "id_proceso_principal": regla.id_proceso_principal,
            "pasos": [
                {"id_proceso": paso.id_proceso, "opcional": bool(paso.opcional)}
                for paso in pasos
            ],
        })
    return render_template(
        "configuracion/reglas.html",
        reglas=datos_reglas,
        procesos=[
            {"id_proceso": proceso.id_proceso, "nombre": proceso.nombre,
             "activo": bool(proceso.activo)}
            for proceso in procesos
        ],
    )
