"""Clasificación de líneas y selección de rutas de producción.

Este módulo solo lee datos y mantiene caché en memoria.  No modifica pedidos ni
líneas, por lo que puede usarse antes y durante el guardado de pedidos.
"""

import threading

from servicios.textos import normalizar_busqueda


_cache_lock = threading.Lock()
_cache = None


def invalidar():
    """Vacía toda la caché. Debe llamarse después de guardar rutas o procesos."""
    global _cache
    with _cache_lock:
        _cache = None


def cargar_cache():
    """Carga una sola vez todos los datos necesarios para clasificar rutas."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache

    from modelos.producto import Producto
    from modelos.produccion import (
        Proceso,
        ProductoPaso,
        ReglaClasificacion,
        ReglaPaso,
    )

    procesos = {}
    for fila in (
        Proceso.query
        .filter(Proceso.activo.is_(True))
        .order_by(Proceso.orden, Proceso.nombre, Proceso.id_proceso)
        .all()
    ):
        procesos[fila.id_proceso] = {
            "nombre": fila.nombre,
            "orden": fila.orden,
            "es_sistema": bool(fila.es_sistema),
        }

    rutas_producto = {}
    for fila in (
        ProductoPaso.query
        .order_by(ProductoPaso.id_producto, ProductoPaso.orden, ProductoPaso.id_producto_paso)
        .all()
    ):
        rutas_producto.setdefault(fila.id_producto, []).append(
            (fila.orden, fila.id_proceso, bool(fila.opcional))
        )

    procesos_principales_producto = {}
    for fila in (
        Producto.query
        .filter(Producto.id_proceso_principal.is_not(None))
        .all()
    ):
        procesos_principales_producto[fila.id_producto] = fila.id_proceso_principal

    rutas_regla = {}
    for fila in (
        ReglaPaso.query
        .order_by(ReglaPaso.id_regla, ReglaPaso.orden, ReglaPaso.id_regla_paso)
        .all()
    ):
        rutas_regla.setdefault(fila.id_regla, []).append(
            (fila.orden, fila.id_proceso, bool(fila.opcional))
        )

    reglas = []
    for fila in (
        ReglaClasificacion.query
        .filter(ReglaClasificacion.activo.is_(True))
        .order_by(ReglaClasificacion.prioridad.desc(), ReglaClasificacion.id_regla)
        .all()
    ):
        reglas.append({
            "palabra_clave": fila.palabra_clave,
            "prioridad": fila.prioridad,
            "id_proceso_principal": fila.id_proceso_principal,
            "pasos": rutas_regla.get(fila.id_regla, []),
        })

    sin_clasificar = None
    for id_proceso, datos in procesos.items():
        if datos["es_sistema"] and datos["nombre"].strip().lower() == "sin clasificar":
            sin_clasificar = id_proceso
            break
    if sin_clasificar is None:
        for id_proceso, datos in procesos.items():
            if datos["nombre"].strip().lower() == "sin clasificar":
                sin_clasificar = id_proceso
                break

    cache = {
        "procesos": procesos,
        "rutas_producto": rutas_producto,
        "procesos_principales_producto": procesos_principales_producto,
        "reglas": reglas,
        "sin_clasificar": sin_clasificar,
    }
    with _cache_lock:
        if _cache is None:
            _cache = cache
        return _cache


def _ruta_activa(ruta, procesos):
    """Conserva el orden y descarta pasos cuyo proceso esté inactivo."""
    activa = []
    for orden, id_proceso, opcional in ruta:
        if id_proceso in procesos:
            activa.append((orden, id_proceso, opcional))
    return activa


def _paso_proceso_principal(id_proceso, procesos):
    if id_proceso in procesos:
        return [(1, id_proceso, False)]
    return []


def _sin_clasificar(id_sin_clasificar):
    if id_sin_clasificar is not None:
        return [(1, id_sin_clasificar, False)]
    return [(1, None, False)]


def _ruta_para_datos(cache, id_producto, descripcion):
    """Decisión pura: solo lee los diccionarios recibidos."""
    procesos = cache["procesos"]

    if id_producto is not None:
        ruta = _ruta_activa(cache["rutas_producto"].get(id_producto, []), procesos)
        if ruta:
            return ruta

        id_principal = cache["procesos_principales_producto"].get(id_producto)
        ruta = _paso_proceso_principal(id_principal, procesos)
        if ruta:
            return ruta

    texto = normalizar_busqueda(descripcion)
    if texto:
        reglas = sorted(
            cache["reglas"],
            key=lambda regla: int(regla.get("prioridad") or 0),
            reverse=True,
        )
        for regla in reglas:
            clave = normalizar_busqueda(regla["palabra_clave"])
            if clave and clave in texto:
                ruta = _ruta_activa(regla["pasos"], procesos)
                if ruta:
                    return ruta
                ruta = _paso_proceso_principal(regla["id_proceso_principal"], procesos)
                if ruta:
                    return ruta

    return _sin_clasificar(cache["sin_clasificar"])


def ruta_para(id_producto, descripcion):
    """Obtiene la caché y delega toda la lógica a la función pura."""
    return _ruta_para_datos(cargar_cache(), id_producto, descripcion)
