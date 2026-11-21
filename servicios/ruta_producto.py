"""Ruta de producción de un producto: validar, guardar y copiar a una categoría.

No hace commit ni invalida la caché: lo hace quien llama, después de confirmar
la transacción (así la ruta se guarda junto con el producto o no se guarda).
"""

from sqlalchemy import or_

from config import db
from modelos.produccion import Proceso, ProductoPaso
from modelos.producto import Producto

MAX_PASOS = 8
MODOS_APLICAR = ("sin_ruta", "todas")
_LOTE = 500


def ruta_de(producto):
    """Ruta guardada: {'pasos': [(id_proceso, opcional)], 'id_proceso_principal': id|None}."""
    filas = sorted(producto.rutas_produccion, key=lambda p: (p.orden, p.id_producto_paso or 0))
    return {
        "pasos": [(f.id_proceso, bool(f.opcional)) for f in filas],
        "id_proceso_principal": producto.id_proceso_principal,
    }


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def validar_ruta(ruta):
    """Devuelve (ruta_normalizada, errores). La ruta normalizada tiene la forma de ruta_de()."""
    if not isinstance(ruta, dict):
        return None, ["La ruta de producción no es válida."]
    pasos_crudos = ruta.get("pasos", [])
    if pasos_crudos is None:
        pasos_crudos = []
    if not isinstance(pasos_crudos, list):
        return None, ["Los pasos de la ruta no son válidos."]

    errores = []
    if len(pasos_crudos) > MAX_PASOS:
        errores.append(f"La ruta no puede tener más de {MAX_PASOS} pasos.")

    pasos = []
    for paso in pasos_crudos:
        id_proceso = _entero(paso.get("id_proceso")) if isinstance(paso, dict) else None
        if id_proceso is None:
            errores.append("Hay un paso de la ruta con un proceso no válido.")
            continue
        pasos.append((id_proceso, bool(paso.get("opcional") is True)))

    id_principal = ruta.get("id_proceso_principal")
    if id_principal in (None, ""):
        id_principal = None
    else:
        id_principal = _entero(id_principal)
        if id_principal is None:
            errores.append("El proceso principal no es válido.")

    ids = {i for i, _ in pasos}
    if id_principal is not None:
        ids.add(id_principal)
    procesos = {}
    if ids:
        procesos = {
            p.id_proceso: p
            for p in Proceso.query.filter(Proceso.id_proceso.in_(ids)).all()
        }

    def comprobar(id_proceso, etiqueta):
        proceso = procesos.get(id_proceso)
        if proceso is None:
            errores.append(f"{etiqueta}: el proceso no existe.")
        elif proceso.es_sistema:
            errores.append(f"{etiqueta}: «{proceso.nombre}» no se puede usar en una ruta.")
        elif not proceso.activo:
            errores.append(f"{etiqueta}: el proceso «{proceso.nombre}» está inactivo.")

    vistos = set()
    for indice, (id_proceso, _opcional) in enumerate(pasos, start=1):
        comprobar(id_proceso, f"Paso {indice}")
        if id_proceso in vistos:
            nombre = procesos[id_proceso].nombre if id_proceso in procesos else id_proceso
            errores.append(f"El proceso «{nombre}» está repetido en la ruta.")
        vistos.add(id_proceso)
    if id_principal is not None:
        comprobar(id_principal, "Proceso principal")

    if errores:
        return None, list(dict.fromkeys(errores))
    return {"pasos": pasos, "id_proceso_principal": id_principal}, []


def guardar_ruta(producto, ruta):
    """Reemplaza la ruta del producto (ya validada). True si hubo cambios."""
    if ruta_de(producto) == ruta:
        return False
    id_producto = producto.id_producto
    ProductoPaso.query.filter_by(id_producto=id_producto).delete(synchronize_session=False)
    db.session.flush()
    db.session.expire(producto, ["rutas_produccion"])
    for orden, (id_proceso, opcional) in enumerate(ruta["pasos"], start=1):
        db.session.add(ProductoPaso(
            id_producto=id_producto, orden=orden, id_proceso=id_proceso, opcional=opcional,
        ))
    producto.id_proceso_principal = ruta["id_proceso_principal"]
    return True


def _tiene_ruta():
    con_pasos = ProductoPaso.query.filter(ProductoPaso.id_producto == Producto.id_producto).exists()
    return or_(con_pasos, Producto.id_proceso_principal.isnot(None))


def condicion_sin_ruta():
    """Condición SQL «sin pasos ni proceso principal» (NOT EXISTS, sin N+1)."""
    return ~_tiene_ruta()


def _destinos(producto):
    return db.session.query(Producto.id_producto).filter(
        Producto.categoria == producto.categoria,
        Producto.id_producto != producto.id_producto,
    )


def contar_categoria(producto):
    """{'categoria', 'total', 'sin_ruta'} de los OTROS productos de la categoría."""
    if not producto.categoria:
        return {"categoria": None, "total": 0, "sin_ruta": 0}
    consulta = _destinos(producto)
    return {
        "categoria": producto.categoria,
        "total": consulta.count(),
        "sin_ruta": consulta.filter(condicion_sin_ruta()).count(),
    }


def aplicar_a_categoria(producto, modo):
    """Copia la ruta guardada de `producto` a su categoría. Devuelve
    {'categoria', 'modo', 'aplicados', 'total', 'sin_ruta'}. ValueError si no procede."""
    if modo not in MODOS_APLICAR:
        raise ValueError("El modo de aplicación no es válido.")
    if not producto.categoria:
        raise ValueError("El producto no tiene categoría.")
    fuente = ruta_de(producto)
    if not fuente["pasos"] and fuente["id_proceso_principal"] is None:
        raise ValueError("El producto no tiene una ruta guardada que copiar.")
    ruta, errores = validar_ruta({
        "pasos": [{"id_proceso": i, "opcional": o} for i, o in fuente["pasos"]],
        "id_proceso_principal": fuente["id_proceso_principal"],
    })
    if errores:
        raise ValueError(
            "La ruta guardada tiene procesos que ya no se pueden usar; "
            "edítala y guárdala antes de aplicarla. " + " ".join(errores)
        )

    conteo = contar_categoria(producto)
    consulta = _destinos(producto)
    if modo == "sin_ruta":
        consulta = consulta.filter(condicion_sin_ruta())
    ids = [fila[0] for fila in consulta.all()]

    for inicio in range(0, len(ids), _LOTE):
        lote = ids[inicio:inicio + _LOTE]
        ProductoPaso.query.filter(ProductoPaso.id_producto.in_(lote)).delete(synchronize_session=False)
        Producto.query.filter(Producto.id_producto.in_(lote)).update(
            {Producto.id_proceso_principal: ruta["id_proceso_principal"]},
            synchronize_session=False,
        )
        db.session.flush()
        if ruta["pasos"]:
            db.session.execute(ProductoPaso.__table__.insert(), [
                {"id_producto": id_producto, "orden": orden, "id_proceso": id_proceso, "opcional": opcional}
                for id_producto in lote
                for orden, (id_proceso, opcional) in enumerate(ruta["pasos"], start=1)
            ])
    db.session.expire_all()
    return {
        "categoria": producto.categoria,
        "modo": modo,
        "aplicados": len(ids),
        "total": conteo["total"],
        "sin_ruta": conteo["sin_ruta"],
    }
