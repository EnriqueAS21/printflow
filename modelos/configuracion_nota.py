"""Configuración editable del encabezado y políticas de las notas PDF."""
import json
from decimal import Decimal, InvalidOperation

from flask import g, has_app_context

from config import db


POLITICAS_DEFAULT = "\n".join((
    "Favor de revisar su producto al momento de recibir. No hay cambios ni devoluciones una vez que sale de sucursal.",
    "Si después de 15 días de su fecha de entrega el cliente no ha recogido su material, no nos hacemos responsables por daño o pérdida.",
    "No se hacen devoluciones por cancelación.",
    "Es responsabilidad del cliente revisar su material antes de dejarlo para impresión o proceso.",
))


class ConfiguracionNota(db.Model):
    __tablename__ = "configuracion_nota"

    id = db.Column(db.Integer, primary_key=True, default=1)
    nombre_negocio = db.Column(db.String(120), nullable=False, default="AC 628 IMPRENTA")
    lema = db.Column(db.String(160), nullable=False, default="imprimimos tus ideas")
    direccion = db.Column(db.String(240), nullable=False, default="Zaragoza 628 Sur, Centro de Santa Catarina")
    telefono = db.Column(db.String(40), nullable=False, default="81 1250 5911")
    logo_archivo = db.Column(db.String(100), nullable=True)
    politicas = db.Column(db.Text, nullable=False, default=POLITICAS_DEFAULT)
    cuenta_fiscal_banco = db.Column(db.String(60), nullable=True)
    cuenta_fiscal_titular = db.Column(db.String(150), nullable=True)
    cuenta_fiscal_clabe = db.Column(db.String(18), nullable=True)
    # Lista ordenada de apartados; ver normalizar_apartados para la estructura.
    apartados = db.Column(db.JSON, nullable=False, default=list)


def obtener_configuracion_nota():
    """Obtiene la única configuración, o la crea con valores iniciales.

    Dentro de una petición se lee una sola vez y se reutiliza desde flask.g.
    """
    en_contexto_app = has_app_context()
    if en_contexto_app and g.get("_configuracion_nota") is not None:
        return g._configuracion_nota
    configuracion = db.session.get(ConfiguracionNota, 1)
    if configuracion is None:
        configuracion = ConfiguracionNota(id=1)
        db.session.add(configuracion)
        db.session.commit()
    if en_contexto_app:
        g._configuracion_nota = configuracion
    return configuracion


DESTINOS_APARTADOS = ("nota", "ticket", "whatsapp")
MAX_APARTADOS = 10
MAX_BLOQUES = 10
MAX_TITULO = 80
MAX_SUBTITULO = 80
MAX_TEXTO = 1000
CONDICIONES_APARTADOS = ("siempre", "con_saldo")


def _texto_limpio(valor, maximo):
    if valor is None:
        return ""
    return str(valor).strip()[:maximo]


def _a_bool(valor):
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "on", "si", "sí", "yes")
    return bool(valor)


def normalizar_apartados(data):
    """Limpia y valida los apartados recibidos del formulario.

    Acepta una lista o un string JSON. Descarta apartados sin título y bloques
    sin subtítulo ni texto; recorta longitudes y cantidades a los máximos.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return []
    if not isinstance(data, list):
        return []

    apartados = []
    for crudo in data:
        if len(apartados) >= MAX_APARTADOS:
            break
        if not isinstance(crudo, dict):
            continue
        titulo = _texto_limpio(crudo.get("titulo"), MAX_TITULO)
        if not titulo:
            continue

        mostrar_crudo = crudo.get("mostrar_en")
        if not isinstance(mostrar_crudo, dict):
            mostrar_crudo = {}
        mostrar_en = {destino: _a_bool(mostrar_crudo.get(destino, False)) for destino in DESTINOS_APARTADOS}

        bloques = []
        bloques_crudos = crudo.get("bloques")
        for bloque in bloques_crudos if isinstance(bloques_crudos, list) else []:
            if len(bloques) >= MAX_BLOQUES:
                break
            if not isinstance(bloque, dict):
                continue
            subtitulo = _texto_limpio(bloque.get("subtitulo"), MAX_SUBTITULO)
            texto = _texto_limpio(bloque.get("texto"), MAX_TEXTO)
            if subtitulo or texto:
                bloques.append({"subtitulo": subtitulo, "texto": texto})

        condicion = crudo.get("condicion")
        if condicion not in CONDICIONES_APARTADOS:
            condicion = "siempre"

        apartados.append({
            "titulo": titulo,
            "activo": _a_bool(crudo.get("activo", False)),
            "mostrar_en": mostrar_en,
            "condicion": condicion,
            "bloques": bloques,
        })
    return apartados


def _formato_moneda(valor):
    try:
        cantidad = Decimal(str(valor if valor is not None else 0))
    except (InvalidOperation, ValueError):
        cantidad = Decimal("0")
    return f"${cantidad:,.2f}"


def _reemplazar_variables(texto, variables):
    for clave, valor in variables.items():
        texto = texto.replace("{" + clave + "}", valor)
    return texto


def apartados_para(destino, pedido=None):
    """Apartados activos visibles en ``destino`` ("nota", "ticket" o "whatsapp").

    Si se pasa ``pedido`` reemplaza {folio}, {cliente}, {total} y {saldo} en
    subtítulos y textos (cualquier otra variable se deja tal cual) y omite los
    apartados con condición "con_saldo" cuando el pedido ya está liquidado.
    """
    if destino not in DESTINOS_APARTADOS:
        raise ValueError(f"Destino de apartados inválido: {destino!r}")

    configuracion = obtener_configuracion_nota()
    variables = None
    if pedido is not None:
        variables = {
            "folio": str(pedido.folio or ""),
            "cliente": str(pedido.nombre_cliente or ""),
            "total": _formato_moneda(pedido.total),
            "saldo": _formato_moneda(pedido.saldo_restante),
        }

    resultado = []
    for apartado in normalizar_apartados(configuracion.apartados or []):
        if not apartado["activo"] or not apartado["mostrar_en"].get(destino):
            continue
        if pedido is not None and apartado["condicion"] == "con_saldo" and pedido.saldo_restante <= 0:
            continue
        if variables:
            apartado["bloques"] = [
                {
                    "subtitulo": _reemplazar_variables(bloque["subtitulo"], variables),
                    "texto": _reemplazar_variables(bloque["texto"], variables),
                }
                for bloque in apartado["bloques"]
            ]
        resultado.append(apartado)
    return resultado


def texto_apartados_whatsapp(pedido):
    """Apartados de WhatsApp como texto plano; cadena vacía si no hay o si falla.

    Cada apartado va como ``*TÍTULO*`` (negrita en WhatsApp) seguido de sus
    subtítulos y textos, con una línea en blanco entre apartados.
    """
    try:
        apartados = apartados_para("whatsapp", pedido)
    except Exception:
        db.session.rollback()
        return ""
    secciones = []
    for apartado in apartados:
        lineas = [f"*{apartado['titulo'].upper()}*"]
        for bloque in apartado["bloques"]:
            if bloque["subtitulo"]:
                lineas.append(bloque["subtitulo"])
            if bloque["texto"]:
                lineas.append(bloque["texto"])
        secciones.append("\n".join(lineas))
    return "\n\n".join(secciones)
