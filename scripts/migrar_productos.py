"""Crea las tablas de productos y agrega sus referencias a clientes y pedidos.

Uso (desde la raíz del proyecto):  python scripts/migrar_productos.py

Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.producto import (  # noqa: E402
    ListaPrecio,
    Presentacion,
    PresentacionPrecio,
    Producto,
)

TABLAS_PRODUCTOS = [
    ListaPrecio.__table__,
    Producto.__table__,
    Presentacion.__table__,
    PresentacionPrecio.__table__,
]

SENTENCIAS = [
    "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS id_lista_precio INTEGER "
    "REFERENCES listas_precio(id_lista) ON DELETE SET NULL",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS id_lista_precio INTEGER "
    "REFERENCES listas_precio(id_lista) ON DELETE SET NULL",
    "ALTER TABLE detalle_pedido ADD COLUMN IF NOT EXISTS id_producto INTEGER "
    "REFERENCES productos(id_producto) ON DELETE SET NULL",
    "ALTER TABLE detalle_pedido ADD COLUMN IF NOT EXISTS id_presentacion INTEGER "
    "REFERENCES presentaciones(id_presentacion) ON DELETE SET NULL",
    "ALTER TABLE detalle_pedido ADD COLUMN IF NOT EXISTS configuracion JSONB",
    "CREATE INDEX IF NOT EXISTS ix_clientes_id_lista_precio "
    "ON clientes (id_lista_precio)",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_id_lista_precio "
    "ON pedidos (id_lista_precio)",
    "CREATE INDEX IF NOT EXISTS ix_detalle_pedido_id_producto "
    "ON detalle_pedido (id_producto)",
    "CREATE INDEX IF NOT EXISTS ix_detalle_pedido_id_presentacion "
    "ON detalle_pedido (id_presentacion)",
    "UPDATE listas_precio SET activo = FALSE WHERE nombre = 'Mayoreo' "
    "AND NOT EXISTS (SELECT 1 FROM presentacion_precios "
    "WHERE presentacion_precios.id_lista = listas_precio.id_lista)",
]

LISTAS_PRECIO = [
    {"nombre": "Público", "predeterminada": True, "orden": 1, "activo": True},
    {"nombre": "Maquila", "predeterminada": False, "orden": 2, "activo": True},
]


def main():
    with app.app_context():
        # Crea únicamente las tablas del catálogo de productos y precios.
        db.metadata.create_all(db.engine, tables=TABLAS_PRODUCTOS, checkfirst=True)
        print("OK: tablas listas_precio, productos, presentaciones y presentacion_precios verificadas.")

        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")

            for lista in LISTAS_PRECIO:
                resultado = conn.execute(
                    text(
                        "INSERT INTO listas_precio (nombre, predeterminada, orden, activo) "
                        "VALUES (:nombre, :predeterminada, :orden, :activo) "
                        "ON CONFLICT (nombre) DO UPDATE SET activo = TRUE"
                    ),
                    lista,
                )
                accion = "insertada" if resultado.rowcount else "ya existía"
                print(f"OK: lista de precio {lista['nombre']} {accion}.")

    print("Migración de productos completada.")


if __name__ == "__main__":
    main()