"""Cola de impresión en memoria para la Phomemo M110.

- Un único trabajador por proceso de PrintFlow.
- La cola no se persiste: al reiniciar el servidor queda vacía.
- Ante un error, se pausa automáticamente y conserva los trabajos pendientes.
- El trabajo fallido NO se reintenta automáticamente para evitar duplicados:
  se debe llamar a reintentar() o descartar().
- Si la etiqueta se imprimió pero falla la bitácora, el trabajo queda como
  impreso y la cola sigue (solo se registra una advertencia).
"""
from collections import OrderedDict, deque
from datetime import datetime
from types import SimpleNamespace
import os
import threading
import time
import uuid

from flask import g, has_request_context

_MAX_HISTORIAL = 100

_condicion = threading.Condition()
_pendientes = deque()
_historial = OrderedDict()
_trabajador = None
_app = None
_pausada = False
_actual = None
_fallido = None
_ultimo_error = None
_ultimo_resultado = None
_ultimo_fin_monotonic = None


def _fecha():
    return datetime.now().isoformat(timespec="seconds")


def _guardar_historial(trabajo):
    """Guarda (o actualiza) el trabajo en el historial. Llamar con _condicion tomada."""
    _historial[trabajo["id"]] = trabajo
    _historial.move_to_end(trabajo["id"])
    while len(_historial) > _MAX_HISTORIAL:
        _historial.popitem(last=False)


def _resumen_solicitante(solicitante):
    """Copia simple y serializable del usuario que pidió la impresión."""
    if solicitante is None and has_request_context():
        solicitante = g.get('usuario')
    if solicitante is None:
        return None
    if isinstance(solicitante, dict):
        obtener = solicitante.get
    else:
        obtener = lambda clave: getattr(solicitante, clave, None)  # noqa: E731
    return {
        "id_usuario": obtener("id_usuario"),
        "nombre": obtener("nombre"),
        "rol": obtener("rol"),
    }


def _publico(trabajo):
    return {k: v for k, v in trabajo.items() if k != "solicitante"}


def _asegurar_trabajador(app):
    global _trabajador, _app
    with _condicion:
        _app = app
        if _trabajador is None or not _trabajador.is_alive():
            _trabajador = threading.Thread(
                target=_bucle_trabajador,
                name="printflow-cola-etiquetas",
                daemon=True,
            )
            _trabajador.start()


def agregar(id_pedido, folio, base_url, app, solicitante=None):
    """Encola un pedido sin bloquear la solicitud web hasta que imprima."""
    _asegurar_trabajador(app)
    trabajo = {
        "id": uuid.uuid4().hex[:12],
        "id_pedido": int(id_pedido),
        "folio": str(folio),
        "base_url": (base_url or "http://localhost/").rstrip("/") + "/",
        "estado": "En cola",
        "mensaje": None,
        "solicitado": _fecha(),
        "inicio": None,
        "fin": None,
        "solicitante": _resumen_solicitante(solicitante),
    }
    with _condicion:
        _pendientes.append(trabajo)
        _guardar_historial(trabajo)
        posicion = len(_pendientes)
        _condicion.notify_all()
    return {"id": trabajo["id"], "folio": trabajo["folio"], "estado": trabajo["estado"], "posicion": posicion}


def _imprimir_trabajo(app, trabajo):
    """Imprime la etiqueta. Cualquier excepción aquí cuenta como fallo de impresión."""
    with app.app_context():
        # Se crea contexto de petición para que _url_pedido() pueda construir
        # el QR sin modificar el generador de etiquetas.
        with app.test_request_context("/", base_url=trabajo["base_url"]):
            from modelos.pedido import Pedido
            from servicios import Etiquetas as etiquetas

            pedido = Pedido.query.filter_by(id_pedido=trabajo["id_pedido"]).first()
            if pedido is None:
                raise RuntimeError(f"El pedido {trabajo['folio']} ya no existe.")
            return etiquetas.imprimir(pedido)


def _registrar_bitacora(app, trabajo, modo):
    """Registra la impresión en bitácora. Si falla, solo advierte y hace rollback."""
    try:
        with app.app_context():
            with app.test_request_context("/", base_url=trabajo["base_url"]):
                from modelos.bitacora import registrar
                from config import db

                usuario = (
                    SimpleNamespace(**trabajo["solicitante"])
                    if trabajo.get("solicitante") else None
                )
                try:
                    registrar(
                        'ETIQUETA_IMPRESA', 'pedido', trabajo["id_pedido"],
                        f"{trabajo['folio']}: modo {modo}",
                        usuario=usuario,
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise
    except Exception:
        app.logger.warning(
            "La etiqueta %s se imprimió, pero no se pudo registrar en bitácora.",
            trabajo["folio"], exc_info=True,
        )


def _bucle_trabajador():
    global _actual, _pausada, _fallido, _ultimo_error, _ultimo_resultado, _ultimo_fin_monotonic
    while True:
        with _condicion:
            _condicion.wait_for(lambda: _app is not None and (not _pausada) and bool(_pendientes))
            try:
                pausa = max(0.0, float(os.environ.get('ETIQUETAS_PAUSA_SEG', '3')))
            except ValueError:
                pausa = 3.0
                _app.logger.warning(
                    'ETIQUETAS_PAUSA_SEG no es válido; se usará 3 segundos.'
                )
            while not _pausada and _pendientes and _ultimo_fin_monotonic is not None:
                restante = pausa - (time.monotonic() - _ultimo_fin_monotonic)
                if restante <= 0:
                    break
                _condicion.wait(timeout=restante)
            if _pausada or not _pendientes:
                continue
            trabajo = _pendientes.popleft()
            trabajo["estado"] = "Imprimiendo"
            trabajo["inicio"] = _fecha()
            trabajo["mensaje"] = None
            _guardar_historial(trabajo)
            _actual = trabajo
            app = _app

        try:
            try:
                resultado_impresion = _imprimir_trabajo(app, trabajo)
            except Exception as exc:
                app.logger.exception("Falló la impresión de la etiqueta %s", trabajo["folio"])
                with _condicion:
                    trabajo["estado"] = "Error"
                    trabajo["mensaje"] = str(exc)
                    trabajo["fin"] = _fecha()
                    _guardar_historial(trabajo)
                    _fallido = trabajo
                    _pausada = True
                    _ultimo_error = {
                        "id": trabajo["id"], "folio": trabajo["folio"],
                        "mensaje": str(exc), "fecha": trabajo["fin"],
                        "nota": "No se reintentó automáticamente. Verifica si la etiqueta salió físicamente.",
                    }
                    _ultimo_resultado = {
                        "id": trabajo["id"], "folio": trabajo["folio"],
                        "estado": "Error", "fin": trabajo["fin"],
                    }
                continue

            modo = resultado_impresion.get('modo') if isinstance(resultado_impresion, dict) else None
            with _condicion:
                trabajo["estado"] = "Impreso"
                trabajo["modo"] = modo
                trabajo["fin"] = _fecha()
                _guardar_historial(trabajo)
                _ultimo_resultado = {
                    "id": trabajo["id"], "folio": trabajo["folio"],
                    "modo": modo, "estado": "Impreso", "fin": trabajo["fin"],
                }
                _ultimo_error = None

            _registrar_bitacora(app, trabajo, modo)
        except Exception:
            # Protección final: el hilo trabajador nunca debe morir.
            app.logger.exception("Error inesperado en la cola de etiquetas.")
        finally:
            with _condicion:
                _actual = None
                _ultimo_fin_monotonic = time.monotonic()
                _condicion.notify_all()


def estado_trabajo(id_trabajo):
    """Devuelve el estado de un trabajo del historial, o None si no existe."""
    with _condicion:
        trabajo = _historial.get(id_trabajo)
        if trabajo is None:
            return None
        resultado = {
            "id": trabajo["id"],
            "folio": trabajo["folio"],
            "estado": trabajo["estado"],
            "mensaje": trabajo.get("mensaje"),
            "solicitado": trabajo.get("solicitado"),
            "inicio": trabajo.get("inicio"),
            "fin": trabajo.get("fin"),
            "modo": trabajo.get("modo"),
        }
        if trabajo["estado"] == "En cola":
            posicion = None
            for indice, pendiente in enumerate(_pendientes, start=1):
                if pendiente["id"] == id_trabajo:
                    posicion = indice
                    break
            resultado["posicion"] = posicion
            resultado["cola_pausada"] = _pausada
        return resultado


def estado():
    with _condicion:
        return {
            "pausada": _pausada,
            "actual": _publico(_actual) if _actual else None,
            "pendientes": [_publico(x) for x in _pendientes],
            "cantidad_pendiente": len(_pendientes),
            "fallido": _publico(_fallido) if _fallido else None,
            "trabajador_vivo": _trabajador is not None and _trabajador.is_alive(),
            "ultimo_error": dict(_ultimo_error) if _ultimo_error else None,
            "ultimo_resultado": dict(_ultimo_resultado) if _ultimo_resultado else None,
        }


def reintentar():
    """Vuelve a encolar el trabajo fallido al inicio y reanuda la cola."""
    global _pausada, _fallido, _ultimo_error
    with _condicion:
        trabajo = _fallido
        if trabajo is not None:
            trabajo["estado"] = "En cola"
            trabajo["inicio"] = None
            trabajo["fin"] = None
            _pendientes.appendleft(trabajo)
            _guardar_historial(trabajo)
            _fallido = None
            _ultimo_error = None
        _pausada = False
        _condicion.notify_all()
        return {
            "reanudada": True,
            "reintentado": trabajo["id"] if trabajo else None,
            "cantidad_pendiente": len(_pendientes),
        }


def descartar():
    """Descarta el trabajo fallido (sin reimprimirlo) y reanuda la cola."""
    global _pausada, _fallido
    with _condicion:
        trabajo = _fallido
        if trabajo is not None:
            trabajo["estado"] = "Descartado"
            trabajo["fin"] = _fecha()
            _guardar_historial(trabajo)
            _fallido = None
        _pausada = False
        _condicion.notify_all()
        return {
            "reanudada": True,
            "descartado": trabajo["id"] if trabajo else None,
            "cantidad_pendiente": len(_pendientes),
        }


# Compatibilidad: reanudar nunca reimprime el trabajo fallido.
reanudar = descartar
