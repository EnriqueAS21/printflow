"""Administración de la información impresa en notas de pedido."""
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename

from config import db
from modelos.bitacora import registrar
from modelos.configuracion_nota import (
    ConfiguracionNota,
    normalizar_apartados,
    obtener_configuracion_nota,
)
from servicios.permisos import permiso_requerido

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
