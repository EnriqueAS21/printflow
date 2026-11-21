"""Normaliza los datos ya guardados de clientes y pedidos.

Uso (desde la raíz del proyecto):  python scripts/normalizar_datos.py

Aplica en Python las mismas funciones que los @validates de los modelos:
  - Cliente: nombre, razón social, RFC, correo, teléfono y notas.
  - Pedido: solo nombre_cliente.
Recorre en lotes de 500 y hace commit por lote. Es idempotente: una segunda
ejecución no modifica nada.

Si al normalizar el teléfono dos o más clientes quedarían con el mismo número,
NO se fusionan ni se cambia su teléfono: se imprimen para revisión manual
(el resto de sus campos sí se normaliza).
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.cliente import Cliente  # noqa: E402
from modelos.pedido import Pedido  # noqa: E402
from servicios.telefonos import normalizar_telefono  # noqa: E402
from servicios.textos import normalizar_correo, normalizar_nombre, normalizar_rfc  # noqa: E402

TAMANO_LOTE = 500


def _recortar(texto):
    return texto.strip() if texto is not None else None


CAMPOS_CLIENTE = {
    'nombre': normalizar_nombre,
    'razon_social': normalizar_nombre,
    'rfc': normalizar_rfc,
    'correo': normalizar_correo,
    'telefono': normalizar_telefono,
    'notas': _recortar,
}
# Columnas NOT NULL: si normalizar las dejaría vacías, se conservan como están.
OBLIGATORIOS = {'nombre', 'telefono', 'nombre_cliente'}


def _en_lotes(modelo, llave):
    """Itera por lotes ordenados por la llave primaria (paginación por llave)."""
    ultimo = 0
    while True:
        lote = (modelo.query.filter(llave > ultimo)
                .order_by(llave).limit(TAMANO_LOTE).all())
        if not lote:
            return
        yield lote
        ultimo = getattr(lote[-1], llave.key)


def _telefonos_en_conflicto():
    """{teléfono normalizado: [(id, nombre, teléfono actual), ...]} con 2+ clientes."""
    grupos = defaultdict(list)
    filas = db.session.query(Cliente.id_cliente, Cliente.nombre, Cliente.telefono)
    for id_cliente, nombre, telefono in filas:
        grupos[normalizar_telefono(telefono) or telefono].append((id_cliente, nombre, telefono))
    return {tel: clientes for tel, clientes in grupos.items() if len(clientes) > 1}


def _aplicar(registro, campos, omitir=()):
    """Asigna los valores normalizados que cambien. Devuelve True si hubo cambios."""
    cambio = False
    for campo, normalizar in campos.items():
        if campo in omitir:
            continue
        actual = getattr(registro, campo)
        nuevo = normalizar(actual)
        if nuevo is None and campo in OBLIGATORIOS:
            continue
        if nuevo != actual:
            setattr(registro, campo, nuevo)   # el @validates vuelve a normalizar (idempotente)
            cambio = True
    return cambio


def normalizar_clientes():
    conflictos = _telefonos_en_conflicto()
    ids_en_conflicto = {c[0] for clientes in conflictos.values() for c in clientes}

    modificados = 0
    for lote in _en_lotes(Cliente, Cliente.id_cliente):
        for cliente in lote:
            omitir = {'telefono'} if cliente.id_cliente in ids_en_conflicto else ()
            if _aplicar(cliente, CAMPOS_CLIENTE, omitir):
                modificados += 1
        db.session.commit()

    if conflictos:
        print(f"\n{len(conflictos)} teléfono(s) quedarían duplicados; revísalos a mano "
              "(no se cambió su teléfono):")
        for telefono, clientes in sorted(conflictos.items()):
            print(f"  {telefono}:")
            for id_cliente, nombre, actual in clientes:
                print(f"    C-{id_cliente:04d}  {nombre!r}  teléfono guardado: {actual!r}")
    return modificados


def normalizar_pedidos():
    campos = {'nombre_cliente': normalizar_nombre}
    modificados = 0
    for lote in _en_lotes(Pedido, Pedido.id_pedido):
        for pedido in lote:
            if _aplicar(pedido, campos):
                modificados += 1
        db.session.commit()
    return modificados


def main():
    with app.app_context():
        clientes = normalizar_clientes()
        pedidos = normalizar_pedidos()
    print(f"\nClientes modificados: {clientes}")
    print(f"Pedidos modificados:  {pedidos}")
    print(f"Total de registros modificados: {clientes + pedidos}")


if __name__ == "__main__":
    main()
